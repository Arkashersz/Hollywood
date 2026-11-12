"""ChatGPT pelo navegador: texto (análises, roteiros, prompts) e imagens."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import config
from ..errors import Blocked
from . import common, dom


class ChatGPT:
    service = "ChatGPT"
    # Linha fixa antes do prompt aprovado, para o ChatGPT gerar a imagem em vez de responder com texto.
    IMAGE_PREFIX = "Generate an image from this description:\n\n"

    def __init__(self, browser, selectors: dict, url: str | None = None, *, poll: float = 1.5, settle: int = 3):
        self.browser = browser
        self.sel = selectors["chatgpt"]
        self.url = url or config.CHATGPT_URL
        self.poll = poll
        self.settle = settle

    async def check_session(self) -> tuple[bool, str]:
        try:
            page = await self.browser.page("chatgpt")
            await common.open_service(page, self.url, self.sel, self.service, wait_seconds=20)
            return True, "Sessão do ChatGPT ativa."
        except Blocked as exc:
            return False, f"{exc.message} {exc.action}"

    async def ask(self, prompt: str, files: list[Path] | None = None, *, new_chat: bool = True, timeout: int | None = None) -> str:
        """Envia um pedido de texto e devolve a resposta em Markdown."""
        snap, _ = await self._exchange(prompt, files or [], new_chat, timeout or config.TEXT_TIMEOUT, want_image=False)
        text = snap["text"]
        if common.looks_like_limit(text):
            raise Blocked("LIMIT_REACHED", f"ChatGPT: {text[:300]}")
        if not text:
            raise Blocked("UI_CHANGED", "O ChatGPT terminou sem texto legível na resposta.")
        return text

    async def generate_image(self, prompt: str, refs: list[Path] | None, out_path: Path, *, timeout: int | None = None, slot: int = 0) -> dict:
        """Gera uma imagem em uma conversa nova (aba `slot`) e salva o arquivo original em `out_path` (PNG)."""
        snap, page = await self._exchange(self.IMAGE_PREFIX + prompt, refs or [], True, timeout or config.IMAGE_TIMEOUT, want_image=True, slot=slot)
        if not snap["images"]:
            text = snap["text"]
            if common.looks_like_limit(text):
                raise Blocked("LIMIT_REACHED", f"ChatGPT: {text[:300]}")
            raise Blocked("REFUSED", f"O ChatGPT respondeu sem imagem: {text[:400] or 'sem texto'}")
        best = max(snap["images"], key=lambda im: im["w"] * im["h"])
        tmp = await common.download(page, best["src"], out_path)
        try:
            from PIL import Image
            with Image.open(tmp) as im:
                im.load()
                width, height = im.size
                if max(width, height) < 512:
                    raise ValueError(f"imagem pequena demais ({width}x{height})")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                im.convert("RGB").save(out_path, format="PNG")
        except Blocked:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Blocked("DOWNLOAD_FAILED", f"O arquivo baixado não abriu como imagem: {exc}", f"Baixe manualmente e salve em {out_path}.") from None
        finally:
            tmp.unlink(missing_ok=True)
        return {"width": width, "height": height}

    # ------------------------------------------------------------------
    async def _exchange(self, prompt: str, files: list[Path], new_chat: bool, timeout: int, *, want_image: bool, slot: int = 0):
        page = await self.browser.page("chatgpt" if slot == 0 else f"chatgpt-{slot}")
        await page.bring_to_front()
        if new_chat or not page.url.startswith(self.url.rstrip("/")):
            await common.open_service(page, self.url, self.sel, self.service)
        before = (await page.evaluate(dom.SNAPSHOT, self.sel))["count"]
        await common.attach(page, self.sel, files, self.service)
        await common.write(page, self.sel, prompt, self.service)
        await common.send(page, self.sel, self.service)

        loop = asyncio.get_event_loop()
        start = last_change = loop.time()
        started = False
        signature = None
        stable = 0
        while True:
            await asyncio.sleep(self.poll)
            snap = await page.evaluate(dom.SNAPSHOT, self.sel)
            now = loop.time()
            for alert in snap["alerts"]:
                if common.looks_like_limit(alert):
                    raise Blocked("LIMIT_REACHED", f"ChatGPT: {alert[:300]}")
            new_reply = snap["count"] > before
            if not started:
                if new_reply or snap["busy"]:
                    started, last_change = True, now
                elif now - start > 45:
                    raise Blocked("UI_CHANGED", "O ChatGPT não começou a responder depois do envio.")
                continue
            current = (snap["count"], len(snap["text"]), tuple(im["src"] for im in snap["images"]), snap["busy"])
            if current != signature:
                signature, last_change, stable = current, now, 0
            else:
                stable += 1
            done = new_reply and not snap["busy"] and stable >= self.settle and bool(snap["text"] or snap["images"])
            if done and want_image and not snap["images"]:
                # a imagem pode aparecer logo depois do texto; um aviso de limite não precisa esperar
                done = common.looks_like_limit(snap["text"]) or now - last_change > min(20, config.STALL_SECONDS / 2)
            if done:
                return snap, page
            if now - last_change > config.STALL_SECONDS:
                raise Blocked("TIMEOUT", f"O ChatGPT ficou {config.STALL_SECONDS} s sem progresso.")
            if now - start > timeout:
                raise Blocked("TIMEOUT", f"O ChatGPT não terminou em {timeout} s.")
