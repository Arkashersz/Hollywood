"""Chrome do worker: um Chrome comum, com perfil dedicado, ao qual o Python se conecta localmente.

- O login é sempre feito pelo usuário, em uma janela normal (`python -m app.cli login`).
- O worker só se conecta a esse Chrome pela porta de depuração em 127.0.0.1; não lê nem copia cookies.
- Diante de login, verificação de segurança ou limite, os adaptadores param e pedem intervenção.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path

from .. import config
from ..errors import Blocked

_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]


def find_chrome() -> str:
    if config.CHROME_PATH and Path(config.CHROME_PATH).is_file():
        return config.CHROME_PATH
    for candidate in _CANDIDATES:
        path = os.path.expandvars(candidate)
        if Path(path).is_file():
            return path
    for name in ("chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise Blocked("CHROME_NOT_FOUND", "Google Chrome não encontrado no PC do worker.")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_login_window(urls: list[str]) -> subprocess.Popen:
    """Abre o Chrome do worker sem nenhuma automação, para o usuário entrar nas contas."""
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([find_chrome(), f"--user-data-dir={config.BROWSER_PROFILE_DIR}", "--no-first-run",
                             "--no-default-browser-check", *urls])


class Browser:
    """Mantém a conexão com o Chrome do worker e uma aba por serviço."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._proc: subprocess.Popen | None = None
        self._pages: dict[str, object] = {}
        self._leftover: list = []
        self._lock = asyncio.Lock()   # várias abas pedem página ao mesmo tempo; só uma abre o Chrome

    @property
    def is_open(self) -> bool:
        return self._browser is not None or (self._proc is not None and self._proc.poll() is None)

    async def _drop_playwright(self) -> None:
        """Desfaz a conexão atual (e o processo auxiliar do Playwright) sem encerrar o Chrome."""
        try:
            if self._browser is not None:
                await asyncio.wait_for(self._browser.close(), 10)   # em conexões CDP isto apenas desconecta
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                await asyncio.wait_for(self._pw.stop(), 10)
        except Exception:  # noqa: BLE001
            pass
        self._browser = self._pw = self._context = None
        self._pages = {}

    async def start(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        await self._drop_playwright()   # conexão antiga (Chrome fechado ou travado): não deixa o processo auxiliar acumulando
        from playwright.async_api import async_playwright
        port = config.CDP_PORT
        launched = not _port_open(port)
        if launched:
            config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen([find_chrome(), f"--remote-debugging-port={port}",
                                           f"--user-data-dir={config.BROWSER_PROFILE_DIR}", "--no-first-run",
                                           "--no-default-browser-check", "--disable-session-crashed-bubble", "--hide-crash-restore-bubble", "about:blank"])
            for _ in range(60):
                if _port_open(port):
                    break
                await asyncio.sleep(0.5)
            else:
                raise Blocked("BROWSER_BUSY", "O Chrome do worker não abriu a conexão local.",
                              "Feche todas as janelas do Chrome do worker (inclusive a de login) e clique em Tentar novamente.")
        # A conexão com um Chrome que acabou de abrir pode ficar esperando para sempre: limita o tempo e tenta de novo.
        for attempt in (1, 2):
            try:
                self._pw = await asyncio.wait_for(async_playwright().start(), 30)
                self._browser = await asyncio.wait_for(self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}"), 45)
                break
            except Exception as exc:  # noqa: BLE001
                await self._drop_playwright()
                if attempt == 2:
                    raise Blocked("BROWSER_BUSY", f"Não consegui conectar ao Chrome do worker ({type(exc).__name__}).",
                                  "Feche as janelas do Chrome do worker e clique em Tentar novamente.") from None
                await asyncio.sleep(2)
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._pages = {}
        self._leftover = list(self._context.pages) if launched else []   # Chrome recém-aberto: abas que o Chrome restaurou de uma sessão anterior (ex.: um vídeo antigo do Grok)

    async def page(self, name: str):
        async with self._lock:
            await self.start()
            page = self._pages.get(name)
            if page is None or page.is_closed():
                try:
                    page = await asyncio.wait_for(self._context.new_page(), 30)
                except Exception as exc:  # noqa: BLE001
                    await self._drop_playwright()
                    raise Blocked("BROWSER_BUSY", f"O Chrome do worker não abriu uma aba nova ({type(exc).__name__}).",
                                  "Feche as janelas do Chrome do worker e clique em Tentar novamente.") from None
                self._pages[name] = page
                await self._close_leftover()
            return page

    async def _close_leftover(self) -> None:
        """Fecha as abas antigas restauradas pelo Chrome: sobrava na tela o vídeo anterior, parecendo que o worker voltou para ele."""
        leftover, self._leftover = self._leftover, []
        for old in leftover:
            try:
                if not old.is_closed() and old not in self._pages.values():
                    await asyncio.wait_for(old.close(), 5)
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        self._leftover = []
        for page in self._pages.values():
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:  # noqa: BLE001
                pass
        self._pages = {}
        try:
            if self._browser is not None:
                await self._browser.close()  # em conexões CDP isto apenas desconecta
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = self._pw = self._context = None
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        self._proc = None
