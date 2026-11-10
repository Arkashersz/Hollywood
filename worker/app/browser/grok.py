"""Grok pelo navegador: vídeo a partir de uma imagem inicial e de um prompt.

A interface do Grok muda com frequência e o fluxo abaixo (anexar imagem, escrever, enviar,
esperar um <video> novo) precisa ser conferido com `python -m app.cli check` na conta do usuário.
Quando algo não bate, o item fica aguardando geração/baixa manual em vez de insistir.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import config, media
from ..errors import Blocked
from . import common, dom


class Grok:
    service = "Grok"

    def __init__(self, browser, selectors: dict, url: str | None = None, *, poll: float = 3.0, settle: int = 2):
        self.browser = browser
        self.sel = selectors["grok"]
        self.url = url or config.GROK_URL
        self.poll = poll
        self.settle = settle

    async def check_session(self) -> tuple[bool, str]:
        try:
            page = await self.browser.page("grok")
            await common.open_service(page, self.url, self.sel, self.service, wait_seconds=20)
            return True, "Sessão do Grok ativa."
        except Blocked as exc:
            return False, f"{exc.message} {exc.action}"

    async def generate_video(self, prompt: str, start_image: Path | None, out_path: Path, *, timeout: int | None = None) -> dict:
        timeout = timeout or config.VIDEO_TIMEOUT
        manual = f"Gere o clipe manualmente no Grok e salve em {out_path}."
        page = await self.browser.page("grok")
        await page.bring_to_front()
        await common.open_service(page, self.url, self.sel, self.service)
        if self.sel.get("video_mode"):
            mode = page.locator(self.sel["video_mode"]).first
            if await mode.count() and await mode.is_visible():
                await mode.click()
                await asyncio.sleep(1)
        before = {v["src"] for v in (await page.evaluate(dom.VIDEOS, self.sel))["videos"]}
        await common.attach(page, self.sel, [start_image] if start_image else [], self.service)
        await common.write(page, self.sel, prompt, self.service)
        await common.send(page, self.sel, self.service)

        loop = asyncio.get_event_loop()
        start = loop.time()
        candidate, stable = None, 0
        while True:
            await asyncio.sleep(self.poll)
            state = await page.evaluate(dom.VIDEOS, self.sel)
            for alert in state["alerts"]:
                if common.looks_like_limit(alert):
                    raise Blocked("LIMIT_REACHED", f"Grok: {alert[:300]}")
            fresh = [v for v in state["videos"] if v["src"] not in before and v["ready"] >= 1 and v["duration"] > 0.5]
            if fresh:
                newest = fresh[-1]["src"]
                stable = stable + 1 if newest == candidate else 0
                candidate = newest
                if stable >= self.settle:
                    break
            if loop.time() - start > timeout:
                raise Blocked("TIMEOUT", f"O Grok não entregou um vídeo em {timeout} s.", manual)

        if candidate.startswith("blob:"):
            tmp = await self._download_by_button(page, out_path, manual)
        else:
            tmp = await common.download(page, candidate, out_path)
        try:
            info = media.probe(tmp)
            if info["duration"] <= 0:
                raise ValueError("duração zero")
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            raise Blocked("DOWNLOAD_FAILED", f"O arquivo baixado do Grok não abriu como vídeo: {exc}", manual) from None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(out_path)
        return {"duration": info["duration"], "width": info["width"], "height": info["height"], "has_audio": info["has_audio"]}

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
