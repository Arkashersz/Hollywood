"""Infraestrutura dos testes: servidor local com páginas de simulação e um Chrome headless.

Nenhum teste acessa ChatGPT, Grok, TikTok ou Supabase.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
FIXTURES = Path(__file__).parent / "fixtures"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silencia o log do servidor
        pass


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=270x480:rate=24:duration=2",
                    "-f", "lavfi", "-i", "smptebars=size=270x480:rate=24:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@pytest.fixture(scope="session")
def site(tmp_path_factory, sample_video) -> str:
    """Serve as páginas de simulação e os arquivos 'gerados'. Devolve a URL base."""
    from PIL import Image
    root = tmp_path_factory.mktemp("site")
    for page in FIXTURES.glob("*.html"):
        shutil.copy(page, root / page.name)
    Image.new("RGB", (1024, 1536), (120, 60, 200)).save(root / "gen.png")
    Image.new("RGB", (32, 32), (0, 0, 0)).save(root / "icon.png")
    shutil.copy(sample_video, root / "clip.mp4")
    shutil.copy(sample_video, root / "old.mp4")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class FakeBrowser:
    """Mesma interface de app.browser.session.Browser, sobre um contexto headless."""

    def __init__(self, context):
        self.context = context
        self.pages = {}

    async def page(self, name: str):
        if name not in self.pages or self.pages[name].is_closed():
            self.pages[name] = await self.context.new_page()
        return self.pages[name]


def with_browser(scenario):
    """Executa `scenario(browser)` com um Chrome headless descartável."""
    async def runner():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            try:
                chrome = await pw.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001 - sem Chrome instalado, usa o Chromium do Playwright
                chrome = await pw.chromium.launch(headless=True)
            context = await chrome.new_context()
            try:
                return await scenario(FakeBrowser(context))
            finally:
                await chrome.close()
    return asyncio.run(runner())


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    from app import config
    from app.browser import common
    monkeypatch.setattr(common, "SETTLE_SECONDS", 0.2)
    monkeypatch.setattr(config, "STALL_SECONDS", 4)
    monkeypatch.setattr(config, "PAUSE_BETWEEN_ITEMS", 0)
