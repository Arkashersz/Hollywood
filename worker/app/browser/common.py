"""Rotinas compartilhadas pelos adaptadores: abrir o serviço, conferir sessão, escrever e baixar."""
from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

from ..errors import Blocked
from . import dom

LIMIT_RE = re.compile(
    r"you'?ve (reached|hit)|reached (your|the|our)\b.{0,40}\blimit|limit reached|rate limit|too many requests|"
    r"atingiu (o|seu) limite|limite (de uso|diário|atingido)|limit erreicht|"
    r"try again (later|after|in \d)|tente novamente (mais tarde|após|em \d)|upgrade to (continue|keep)|come back later",
    re.I,
)
LOGIN_URL_RE = re.compile(r"auth\.openai\.com|/auth/login|/log-in|accounts\.x\.ai|/sign-in|accounts\.google\.com", re.I)
SETTLE_SECONDS = 2.0
CHALLENGE_RE = re.compile(r"just a moment|um momento|verify you are human|verifique se você é humano|attention required", re.I)


def looks_like_limit(text: str) -> bool:
    return bool(text) and len(text) < 700 and bool(LIMIT_RE.search(text))


async def open_service(page, url: str, sel: dict, service: str, *, wait_seconds: int = 30) -> None:
    """Abre o serviço e só continua com a caixa de mensagem disponível e a conta autenticada."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    composer = page.locator(sel["composer"]).first
    try:
        await composer.wait_for(state="visible", timeout=wait_seconds * 1000)
    except Exception:  # noqa: BLE001 - classificamos abaixo o motivo
        title = await page.title()
        body = ""
        try:
            body = (await page.locator("body").inner_text(timeout=3000))[:600]
        except Exception:  # noqa: BLE001
            pass
        if LOGIN_URL_RE.search(page.url) or await _any_visible(page, sel.get("login", "")):
            raise Blocked("SESSION_REQUIRED", f"O {service} pediu login.") from None
        if CHALLENGE_RE.search(title) or CHALLENGE_RE.search(body):
            raise Blocked("CHALLENGE", f"O {service} exibiu uma verificação de segurança.") from None
        raise Blocked("UI_CHANGED", f"A caixa de mensagem do {service} não foi encontrada em {page.url}.") from None
    await asyncio.sleep(SETTLE_SECONDS)  # os botões de login surgem um instante depois da caixa de mensagem
    if LOGIN_URL_RE.search(page.url) or await _any_visible(page, sel.get("login", "")):
        raise Blocked("SESSION_REQUIRED", f"O {service} está sem conta autenticada no Chrome do worker.")


async def _any_visible(page, selector: str) -> bool:
    if not selector:
        return False
    try:
        # Os sites mantêm vários links de login ocultos no HTML; só os visíveis contam.
        loc = page.locator(selector)
        for i in range(min(await loc.count(), 40)):
            if await loc.nth(i).is_visible():
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


async def attach(page, sel: dict, files: list[Path], service: str) -> None:
    if not files:
        return
    missing = [str(f) for f in files if not Path(f).is_file()]
    if missing:
        raise Blocked("MISSING_INPUT", f"Referência não encontrada: {', '.join(missing)}")
    inputs = page.locator(sel["file_input"])
    if await inputs.count() == 0:
        raise Blocked("UI_CHANGED", f"O {service} não ofereceu campo de anexo.")
    target = await _pick_file_input(inputs, len(files))
    if target is None:
        raise Blocked("UI_CHANGED", f"O {service} só ofereceu campos de anexo de um arquivo (ex.: câmera) e a etapa precisa de {len(files)}.",
                      "Rode `python -m app.cli check` e ajuste `worker/selectors.json` (file_input).")
    await target.set_input_files([str(f) for f in files])
    await asyncio.sleep(1.5)


async def _pick_file_input(inputs, wanted: int):
    """Escolhe o campo de anexo certo: ignora o de câmera/foto e, com vários arquivos, exige um campo `multiple`."""
    best = None
    for i in range(min(await inputs.count(), 12)):
        candidate = inputs.nth(i)
        info = await candidate.evaluate("e => ({multiple: e.multiple, capture: e.hasAttribute('capture'), label: e.getAttribute('aria-label') || ''})")
        if info["capture"] or re.search(r"foto|photo|camera|câmera", info["label"], re.I):
            continue
        if wanted > 1 and not info["multiple"]:
            continue
        if info["multiple"]:
            return candidate
        best = best or candidate
    return best


async def write(page, sel: dict, prompt: str, service: str) -> None:
    """Escreve o prompt exatamente como recebido, sem pressionar teclas que possam enviá-lo antes da hora."""
    composer = page.locator(sel["composer"]).first
    await composer.click()
    try:
        await composer.fill(prompt)
    except Exception:  # noqa: BLE001 - alguns editores não aceitam fill; inserir texto dá o mesmo resultado
        await page.keyboard.insert_text(prompt)
    typed = await composer.evaluate("el => ('value' in el && el.value) ? el.value : el.innerText")
    if len((typed or "").strip()) < min(len(prompt.strip()), 20):
        await composer.click()
        await page.keyboard.insert_text(prompt)
        typed = await composer.evaluate("el => ('value' in el && el.value) ? el.value : el.innerText")
        if len((typed or "").strip()) < min(len(prompt.strip()), 20):
            raise Blocked("UI_CHANGED", f"Não foi possível escrever na caixa de mensagem do {service}.")


async def send(page, sel: dict, service: str, *, wait_seconds: int = 180) -> None:
    """Clica em enviar; espera anexos terminarem de subir (botão habilitado)."""
    button = page.locator(sel["send"])
    deadline = asyncio.get_event_loop().time() + wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        for i in range(min(await button.count(), 4)):
            candidate = button.nth(i)
            if await candidate.is_visible() and await candidate.is_enabled():
                await candidate.click()
                return
        await asyncio.sleep(1)
    raise Blocked("UI_CHANGED", f"O botão de enviar do {service} não ficou disponível (anexo ainda subindo ou interface diferente).")


async def download(page, src: str, out: Path) -> Path:
    """Baixa o arquivo original apontado pela página, com a sessão do próprio navegador."""
    out.parent.mkdir(parents=True, exist_ok=True)
    data: bytes | None = None
    if src.startswith("http"):
        try:
            response = await page.context.request.get(src, timeout=180000)
            if response.ok:
                data = await response.body()
        except Exception:  # noqa: BLE001 - tenta pela página
            data = None
    if not data:
        try:
            data = base64.b64decode(await page.evaluate(dom.FETCH_BASE64, src))
        except Exception as exc:  # noqa: BLE001
            raise Blocked("DOWNLOAD_FAILED", f"Falha ao baixar o arquivo gerado: {exc}", f"Baixe manualmente e salve em {out}.") from None
    if len(data) < 2048:
        raise Blocked("DOWNLOAD_FAILED", "O arquivo baixado veio vazio ou incompleto.", f"Baixe manualmente e salve em {out}.")
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(data)
    return tmp
