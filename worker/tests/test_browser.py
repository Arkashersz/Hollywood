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


def grok(browser, site, query="", verify=False):
    return Grok(browser, SEL, f"{site}/grok.html{query}", poll=0.2, settle=1, verify_files=verify)


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
    assert sent == {"prompt": 'She says "Hallo"', "files": ["cena-01.png"], "settings": {"res": "720p", "dur": "6s", "asp": "2:3"}}
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


def test_grok_picks_duration_from_the_clip_and_locks_720p(site, tmp_path):
    out = tmp_path / "c.mp4"

    async def scenario(browser):
        await grok(browser, site).generate_video("[[hd]] clip", None, out, duration_s=8)   # 8 s pede o próximo: 10 s
        page = await browser.page("grok")
        return json.loads(await page.locator("#log").inner_text())["settings"]
    assert with_browser(scenario) == {"res": "720p", "dur": "10s", "asp": "9:16"}


def test_grok_downloads_only_the_video_that_matches_the_request(site, tmp_path):
    out = tmp_path / "c.mp4"

    async def scenario(browser):
        return await grok(browser, site, verify=True).generate_video("[[decoy]] clip", None, out, duration_s=6)
    info = with_browser(scenario)
    assert (info["width"], info["height"]) == (720, 1280) and 5.5 < info["duration"] < 6.5, "o vídeo errado (4 s, 270x480) foi ignorado"


def test_grok_refuses_a_video_with_the_wrong_resolution_or_duration(site, tmp_path):
    out = tmp_path / "c.mp4"

    async def scenario(browser):
        await grok(browser, site, verify=True).generate_video("[[low]] clip", None, out, duration_s=6, timeout=3)
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == "TIMEOUT" and "duração 4.0 s (pedido: 6 s)" in err.value.message
    assert not out.exists()


def test_snap_duration_picks_the_smallest_grok_option():
    assert [media.snap_duration(x) for x in (None, 3, 6, 6.2, 7, 10, 11, 15)] == [6, 6, 6, 10, 10, 10, 15, 15]
    with pytest.raises(ValueError):
        media.snap_duration(16)
    with pytest.raises(ValueError):
        media.snap_duration(10, max_seconds=6)


def test_grok_mismatch_rules():
    check = Grok._mismatch
    assert check({"duration": 6.04, "w": 720, "h": 1280}, 6, 698) is None
    assert check({"duration": 5.0, "w": 720, "h": 1280}, 6, 698) is None, "tolerância de 1,5 s"
    assert "duração" in check({"duration": 10.0, "w": 720, "h": 1280}, 6, 698)
    assert "resolução 480x854" in check({"duration": 6.0, "w": 480, "h": 854}, 6, 698)
    assert check({"duration": 6.0, "w": 1080, "h": 1920}, 6, 698) is None, "acima de 720p serve"


def test_grok_does_not_send_without_the_start_image_thumbnail(site, tmp_path, monkeypatch):
    start = tmp_path / "cena.png"
    Image.new("RGB", (64, 64)).save(start)
    from app.browser import common

    async def silent_attach(page, sel, files, service):   # simula anexar no campo errado: nada aparece na caixa
        return None
    monkeypatch.setattr(common, "attach", silent_attach)

    async def scenario(browser):
        g = grok(browser, site)
        await g.generate_video("x", start, tmp_path / "c.mp4")
    with pytest.raises(Blocked) as err:
        with_browser(scenario)
    assert err.value.code == "UI_CHANGED" and "0 de 1 imagem(ns) apareceu anexada" in err.value.message


def test_grok_attaches_all_listed_images_in_order(site, tmp_path):
    a, b = tmp_path / "primeira.png", tmp_path / "segunda.png"
    for f in (a, b):
        Image.new("RGB", (64, 64)).save(f)
    out = tmp_path / "c.mp4"

    async def scenario(browser):
        await grok(browser, site).generate_video("clip", [a, b], out, duration_s=6)
        page = await browser.page("grok")
        return json.loads(await page.locator("#log").inner_text())
    sent = with_browser(scenario)
    assert sent["files"] == ["primeira.png", "segunda.png"]


def test_attach_skips_the_camera_input_and_accepts_several_files(site, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for f in (a, b):
        Image.new("RGB", (64, 64)).save(f)

    async def scenario(browser):
        reply = await chat(browser, site).ask("ver anexos", [a, b])
        return reply
    assert "a.png" in with_browser(scenario) and "b.png" in with_browser(scenario)
