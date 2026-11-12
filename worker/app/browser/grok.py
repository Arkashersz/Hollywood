"""Grok pelo navegador: vídeo a partir de uma imagem inicial e de um prompt.

Tudo aqui é decidido e executado pelo Python, sem o Claude: ele escolhe no Grok a resolução
(sempre 720p), a duração (6, 10 ou 15 s, a menor que comporta o clipe pedido) e a proporção,
confere na tela que as opções ficaram aplicadas, envia o prompt e baixa SOMENTE o vídeo que
nasceu desse envio (novo na página, com a duração e a resolução pedidas).

A interface do Grok muda com frequência: `python -m app.cli check` mostra o que deixou de bater.
Quando algo não bate, a etapa para com a explicação em vez de insistir ou baixar o vídeo errado.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .. import config, media
from ..errors import Blocked
from . import common, dom


class Grok:
    service = "Grok"

    def __init__(self, browser, selectors: dict, url: str | None = None, *, poll: float = 3.0, settle: int = 2,
                 resolution: str | None = None, aspect: str | None = None, verify_files: bool = True):
        self.browser = browser
        self.sel = selectors["grok"]
        self.url = url or config.GROK_URL
        self.poll = poll
        self.settle = settle
        self.resolution = config.GROK_RESOLUTION if resolution is None else resolution   # "" = não mexer
        self.aspect = config.GROK_ASPECT if aspect is None else aspect                    # "" = não mexer
        self.verify_files = verify_files
        self._submit_lock = asyncio.Lock()   # configurar opções + enviar é feito por uma aba de cada vez

    async def check_session(self) -> tuple[bool, str]:
        try:
            page = await self.browser.page("grok")
            await common.open_service(page, self.url, self.sel, self.service, wait_seconds=20)
            return True, "Sessão do Grok ativa."
        except Blocked as exc:
            return False, f"{exc.message} {exc.action}"

    # --- opções do vídeo (resolução, duração, proporção) ------------------------------
    async def _read(self, page, key: str) -> str | None:
        button = page.locator(self.sel[key]).first
        if await button.count() == 0 or not await button.is_visible():
            return None
        text = (await button.inner_text()).strip()
        return text.splitlines()[0].strip() if text else ""

    async def _choose(self, page, key: str, wanted: str, label: str) -> None:
        current = await self._read(page, key)
        if current is None:
            raise Blocked("UI_CHANGED", f"O Grok não mostrou o controle de {label}. Confira o modo Vídeo e rode `python -m app.cli check`.")
        if current == wanted:
            return
        await page.locator(self.sel[key]).first.click()
        option = page.locator(self.sel["menu_option"]).filter(has_text=re.compile(rf"^\s*{re.escape(wanted)}")).first
        try:
            await option.wait_for(state="visible", timeout=5000)
        except Exception:  # noqa: BLE001
            await page.keyboard.press("Escape")
            raise Blocked("UI_CHANGED", f'O Grok não ofereceu a opção "{wanted}" em {label}.') from None
        await option.click()
        await asyncio.sleep(0.6)
        now = await self._read(page, key)
        if now != wanted:
            raise Blocked("UI_CHANGED", f'Não consegui definir {label} = {wanted} no Grok (ficou em "{now}").')

    async def _apply_settings(self, page, seconds: int, with_image: bool = False) -> None:
        if self.resolution:
            await self._choose(page, "resolution_button", self.resolution, "resolução")
        await self._choose(page, "duration_button", f"{seconds}s", "duração")
        if self.aspect and not with_image:   # com imagem inicial o Grok some com este controle: vale a proporção da imagem
            await self._choose(page, "aspect_button", self.aspect, "proporção")

    async def _assert_settings(self, page, seconds: int, with_image: bool = False) -> None:
        """Última conferência antes de enviar: nada pode ter voltado ao padrão (ex.: ao anexar a imagem)."""
        wanted = {"duration_button": f"{seconds}s"}
        if self.resolution:
            wanted["resolution_button"] = self.resolution
        if self.aspect and not with_image:
            wanted["aspect_button"] = self.aspect
        wrong = {key: await self._read(page, key) for key, value in wanted.items() if await self._read(page, key) != value}
        if wrong:
            await self._apply_settings(page, seconds, with_image)

    async def _wait_attachment(self, page, images: list[Path]) -> None:
        """Só envia depois de ver na caixa uma miniatura por imagem; sem elas o Grok geraria sem a referência."""
        loop = asyncio.get_event_loop()
        end = loop.time() + 20
        shown = 0
        while loop.time() < end:
            shown = await page.locator(self.sel["attachment"]).count()
            if shown >= len(images):
                return
            await asyncio.sleep(0.5)
        raise Blocked("UI_CHANGED", f"Só {shown} de {len(images)} imagem(ns) apareceu anexada na caixa do Grok ({', '.join(i.name for i in images)}); não enviei para não gerar sem elas.",
                      "Rode `python -m app.cli check` e ajuste `worker/selectors.json` (grok.file_input / grok.attachment).")

    # --- geração ---------------------------------------------------------------------
    async def generate_video(self, prompt: str, images: Path | list[Path] | None, out_path: Path, *, duration_s: int | None = None,
                             timeout: int | None = None, slot: int = 0) -> dict:
        """`images`: exatamente as imagens deste clipe, na ordem (a primeira é o quadro inicial). Nenhuma outra é anexada."""
        images = [Path(i) for i in ([images] if isinstance(images, (str, Path)) else images or [])]
        timeout = timeout or config.VIDEO_TIMEOUT
        seconds = media.snap_duration(duration_s)
        manual = f"Gere o clipe manualmente no Grok ({self.resolution or 'resolução padrão'}, {seconds} s) e salve em {out_path}."
        page = await self.browser.page("grok" if slot == 0 else f"grok-{slot}")
        await page.bring_to_front()
        async with self._submit_lock:   # as opções do Grok não podem ser trocadas por outra aba entre conferir e enviar
            await common.open_service(page, self.url, self.sel, self.service)
            if self.sel.get("video_mode"):
                mode = page.locator(self.sel["video_mode"]).first
                if await mode.count() and await mode.is_visible():
                    await mode.click()
                    await asyncio.sleep(1)
            await common.attach(page, self.sel, images, self.service)
            if images:
                await self._wait_attachment(page, images)
            await self._apply_settings(page, seconds, bool(images))
            await common.write(page, self.sel, prompt, self.service)
            await self._assert_settings(page, seconds, bool(images))
            before = {v["src"] for v in (await page.evaluate(dom.VIDEOS, self.sel))["videos"]}
            await common.send(page, self.sel, self.service)

        # Só interessa o vídeo NOVO (não estava na página antes do envio) com a duração e a resolução pedidas.
        min_side = int(re.sub(r"\D", "", self.resolution) or 0) * 0.97 if self.resolution else 0
        loop = asyncio.get_event_loop()
        start = loop.time()
        candidate, stable, ignored = None, 0, {}
        while True:
            await asyncio.sleep(self.poll)
            state = await page.evaluate(dom.VIDEOS, self.sel)
            for alert in state["alerts"]:
                if common.looks_like_limit(alert):
                    raise Blocked("LIMIT_REACHED", f"Grok: {alert[:300]}")
            fresh = [v for v in state["videos"] if v["src"] not in before and v["ready"] >= 1 and v["duration"] > 0.5]
            good = []
            for v in fresh:
                reason = self._mismatch(v, seconds, min_side) if self.verify_files else None
                if reason:
                    ignored[v["src"]] = reason
                else:
                    good.append(v)
            if good:
                newest = good[-1]["src"]
                stable = stable + 1 if newest == candidate else 0
                candidate = newest
                if stable >= self.settle:
                    break
            if loop.time() - start > timeout:
                extra = f" Vídeos novos ignorados: {'; '.join(sorted(set(ignored.values())))}." if ignored else ""
                raise Blocked("TIMEOUT", f"O Grok não entregou o vídeo esperado ({self.resolution or 'padrão'}, {seconds} s) em {timeout} s.{extra}", manual)

        # Baixa exatamente o arquivo deste <video> (blob ou URL), com a sessão do navegador; o botão é só reserva.
        try:
            tmp = await common.download(page, candidate, out_path)
        except Blocked:
            if not candidate.startswith("blob:"):
                raise
            tmp = await self._download_by_button(page, out_path, manual)
        try:
            info = media.probe(tmp)
            if info["duration"] <= 0:
                raise ValueError("duração zero")
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            raise Blocked("DOWNLOAD_FAILED", f"O arquivo baixado do Grok não abriu como vídeo: {exc}", manual) from None
        if self.verify_files:
            reason = self._mismatch({"duration": info["duration"], "w": info["width"], "h": info["height"]}, seconds, min_side)
            if reason:
                tmp.unlink(missing_ok=True)
                raise Blocked("DOWNLOAD_FAILED", f"O vídeo baixado não é o pedido: {reason}.", manual)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(out_path)
        return {"duration": info["duration"], "width": info["width"], "height": info["height"], "has_audio": info["has_audio"],
                "requested_s": seconds, "resolution": self.resolution or None}

    @staticmethod
    def _mismatch(video: dict, seconds: int, min_side: float) -> str | None:
        """Motivo de um vídeo não ser o pedido (duração fora de ±1,5 s ou resolução abaixo da escolhida); None se serve."""
        duration = float(video.get("duration") or 0)
        if duration and abs(duration - seconds) > 1.5:
            return f"duração {duration:.1f} s (pedido: {seconds} s)"
        w, h = int(video.get("w") or 0), int(video.get("h") or 0)
        if min_side and w and h and min(w, h) < min_side:
            return f"resolução {w}x{h} (pedido: {int(min_side / 0.97)}p)"
        return None

    async def _download_by_button(self, page, out_path: Path, manual: str) -> Path:
        button = page.locator(self.sel["download"]).last
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        try:
            async with page.expect_download(timeout=120000) as pending:
                await button.click()
            download = await pending.value
            tmp.parent.mkdir(parents=True, exist_ok=True)
            await download.save_as(str(tmp))
        except Exception as exc:  # noqa: BLE001
            raise Blocked("DOWNLOAD_FAILED", f"O Grok não liberou o download automático: {exc}", manual) from None
        return tmp
