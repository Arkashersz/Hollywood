"""Automação do navegador contra páginas de simulação locais."""
from __future__ import annotations

import json
import re

import pytest
from PIL import Image

from app import media
from app.browser import selectors
from app.browser.chatgpt import ChatGPT
from app.browser.grok import Grok
from app.errors import Blocked
from conftest import with_browser

SEL = selectors.load(None)
PROMPT = 'Linha um com "aspas" e acentuação: ação, über, ß.\nLinha dois.\n\nLinha quatro {json: [1, 2]} <tag> & fim.'


def chat(browser, site, query=""):
    return ChatGPT(browser, SEL, f"{site}/chatgpt.html{query}", poll=0.2, settle=2)


def grok(browser, site, query=""):
    return Grok(browser, SEL, f"{site}/grok.html{query}", poll=0.2, settle=1)


def echoed(markdown: str) -> dict:
    return json.loads(re.search(r"```json\n(.*?)\n```", markdown, re.S).group(1))


def test_ask_returns_markdown_and_delivers_prompt_intact(site, tmp_path):
    ref = tmp_path / "folha.png"
    Image.new("RGB", (64, 64)).save(ref)

    async def scenario(browser):
        return await chat(browser, site).ask(PROMPT, [ref])
    text = with_browser(scenario)

    assert text.startswith("# Documento 03 · Roteiro")
    assert "**negrito**" in text and "*itálico*" in text and "`codigo`" in text
    assert "- Item um\n  - Sub item\n- Item dois" in text
    assert "1. Primeiro\n2. Segundo" in text
    assert "| Cena | Fala (DE) |\n| --- | --- |\n| 01 | Hallo \\| Welt |" in text
    assert "> Citação" in text and "---" in text
    assert "Copy" not in text, "rótulos de botão não podem vazar para o documento"
    sent = echoed(text)
    assert sent["files"] == ["folha.png"]
    assert [l for l in sent["prompt"].splitlines() if l.strip()] == [l for l in PROMPT.splitlines() if l.strip()]


def test_follow_up_stays_in_same_conversation(site):
    async def scenario(browser):
        client = chat(browser, site)
        await client.ask("primeira")
        second = await client.ask("segunda pergunta", new_chat=False)
        page = await browser.page("chatgpt")
        return second, await page.locator("article").count()
    second, turns = with_browser(scenario)
    assert echoed(second)["prompt"].strip() == "segunda pergunta"
    assert turns == 4


def test_reads_only_the_new_answer_when_history_exists(site):
    async def scenario(browser):
        return await chat(browser, site, "?history=1").ask("nova")
    assert "old answer" not in with_browser(scenario)


def test_generate_image_saves_original_file(site, tmp_path):
    out = tmp_path / "imagens" / "personagem-anna-corpo.png"

    async def scenario(browser):
        return await chat(browser, site).generate_image("[[image]] full body", None, out)
    info = with_browser(scenario)
    assert info == {"width": 1024, "height": 1536}
    with Image.open(out) as im:
        assert im.size == (1024, 1536) and im.format == "PNG"
    assert not list(out.parent.glob("*.part"))


@pytest.mark.parametrize("marker,code", [("[[limit]]", "LIMIT_REACHED"), ("[[refuse]]", "REFUSED"), ("[[toast-limit]]", "LIMIT_REACHED")])
def test_image_blockers_stop_instead_of_retrying(site, tmp_path, marker, code):
    out = tmp_path / "x.png"

    async def scenario(browser):
        await chat(browser, site).generate_image(f"{marker} prompt", None, out)
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == code
    assert not out.exists()


def test_text_limit_is_not_saved_as_document(site):
    async def scenario(browser):
        await chat(browser, site).ask("[[limit]]")
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == "LIMIT_REACHED"


def test_login_and_challenge_are_reported_not_bypassed(site):
    async def login(browser):
        await chat(browser, site, "?login=1").ask("oi")

    async def challenge(browser):
        client = chat(browser, site, "?challenge=1")
        page = await browser.page("chatgpt")
        from app.browser import common
        await common.open_service(page, client.url, client.sel, "ChatGPT", wait_seconds=2)
    with pytest.raises(Blocked) as err:
        with_browser(login)
    assert err.value.code == "SESSION_REQUIRED"
    with pytest.raises(Blocked) as err:
        with_browser(challenge)
    assert err.value.code == "CHALLENGE"


def test_stalled_generation_times_out(site):
    async def scenario(browser):
        await chat(browser, site).ask("[[stall]]")
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == "TIMEOUT"


def test_missing_reference_fails_before_sending(site, tmp_path):
    async def scenario(browser):
        await chat(browser, site).ask("oi", [tmp_path / "nao-existe.png"])
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == "MISSING_INPUT"


def test_grok_generates_and_downloads_new_video_only(site, tmp_path):
    start = tmp_path / "cena-01.png"
    Image.new("RGB", (64, 64)).save(start)
    out = tmp_path / "videos" / "clipe-01.mp4"

    async def scenario(browser):
        info = await grok(browser, site).generate_video('She says "Hallo"', start, out)
        page = await browser.page("grok")
        return info, json.loads(await page.locator("#log").inner_text())
    info, sent = with_browser(scenario)
    assert sent == {"prompt": 'She says "Hallo"', "files": ["cena-01.png"]}
    assert info["duration"] > 3 and info["width"] == 270
    assert media.probe(out)["duration"] > 3
    assert not list(out.parent.glob("*.part"))


def test_grok_blockers(site, tmp_path):
    out = tmp_path / "c.mp4"

    async def limit(browser):
        await grok(browser, site).generate_video("[[limit]]", None, out)

    async def login(browser):
        await grok(browser, site, "?login=1").generate_video("x", None, out)

    async def never(browser):
        await grok(browser, site).generate_video("[[never]]", None, out, timeout=2)
    for scenario, code in ((limit, "LIMIT_REACHED"), (login, "SESSION_REQUIRED"), (never, "TIMEOUT")):
        with pytest.raises(Blocked) as err:
            with_browser(scenario)
        assert err.value.code == code
    assert not out.exists()
