"""Painel web de ponta a ponta, com o Supabase simulado por interceptação de rede.

Compila o painel apontando para um endereço falso e responde às chamadas REST/RPC/Storage
no próprio teste. Verifica o que o usuário vê e o que cada botão envia ao banco.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import io
import json
import re
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
FAKE = "http://localhost:9"
SHOTS = os.environ.get("PANEL_SHOTS")  # pasta opcional para capturas de tela
NOW = datetime.now(timezone.utc).isoformat()
STAGE_KEYS = ["analysis", "script", "character_bible", "character_images", "character_sheets", "visual_plan", "scene_images", "video_prompts", "production"]
DOC = """# Documento 04 · Personagens

## Bloco de estilo global

| Campo | Valor |
| --- | --- |
| Estilo | 3D |

<script>window.hacked = true</script>
<img src="https://example.invalid/x.png" onerror="window.hacked = true">
[link](javascript:window.hacked=true)
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def panel(tmp_path_factory) -> str:
    npx = shutil.which("npx")
    if npx is None or not (REPO / "node_modules").is_dir():
        pytest.skip("dependências do painel não instaladas (rode npm install na raiz)")
    out = tmp_path_factory.mktemp("panel")
    env = {**os.environ, "VITE_SUPABASE_URL": FAKE, "VITE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test"}
    subprocess.run([npx, "vite", "build", "--outDir", str(out), "--emptyOutDir", "--logLevel", "error"], cwd=REPO / "apps" / "web", env=env, check=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class FakeSupabase:
    def __init__(self):
        self.rpc_calls: list[tuple[str, dict]] = []
        self.removed: list[str] = []
        self.project = {"id": "11111111-aaaa-bbbb-cccc-000000000001", "title": "Anna und der Hund", "source_url": "https://www.tiktok.com/@x/video/1",
                        "status": "active", "current_stage": "character_images", "updated_at": NOW, "created_at": NOW}
        self.stages = [{"id": f"stage-{i}", "project_id": self.project["id"], "stage_key": key, "stage_order": i, "title": f"Etapa {key}",
                        "status": "approved" if i < 4 else "pending_review" if i == 4 else "locked", "current_version": 1, "payload": {}}
                       for i, key in enumerate(STAGE_KEYS, 1)]
        self.stages[3]["payload"] = {"summary": "2 imagem(ns) de corpo inteiro.", "warnings": ["Aviso de teste."],
                                     "next_generation": {"service": "ChatGPT", "count": 2, "unit": "folhas 16:9"}}
        self.artifacts = [
            {"id": "doc-4", "project_id": self.project["id"], "stage_id": "stage-3", "artifact_type": "document", "name": "Documento 04 · Personagens",
             "item_key": "doc-04", "storage_path": None, "local_relative_path": "04-personagens.md", "version": 1, "content": {"markdown": DOC}, "updated_at": NOW},
            *[{"id": f"img-{n}", "project_id": self.project["id"], "stage_id": "stage-4", "artifact_type": "image", "name": f"{n} · corpo inteiro",
               "item_key": f"img-personagem-{n.lower()}-corpo", "storage_path": f"{self.project['id']}/imagens/personagens/personagem-{n.lower()}-corpo.jpg",
               "local_relative_path": f"imagens/personagens/personagem-{n.lower()}-corpo.png", "version": 1,
               "content": {"prompt": "Stylized 3D cartoon render.", "width": 1024, "height": 1536}, "updated_at": NOW} for n in ("Anna", "Hund")],
        ]
        self.events = [{"id": 1, "project_id": self.project["id"], "stage_key": "character_images", "level": "info", "message": "Gerado: Anna · corpo inteiro.", "created_at": NOW},
                       {"id": 2, "project_id": self.project["id"], "stage_key": "character_images", "level": "warn", "message": "Parado (LIMIT_REACHED): limite.", "created_at": NOW}]
        buffer = io.BytesIO()
        Image.new("RGB", (400, 600), (120, 60, 200)).save(buffer, format="JPEG")
        self.jpeg = buffer.getvalue()

    def apply(self, fn: str, args: dict) -> None:
        if fn == "decide_stage" and args["p_decision"] == "approve":
            self.stages[3]["status"] = "approved"
            self.stages[4].update(status="failed", payload={"error": {"code": "LIMIT_REACHED", "message": "ChatGPT: limite de imagens.",
                                                                       "action": "Aguarde a renovação da cota e clique em Tentar novamente.", "blocked": True}})
        if fn == "retry_stage":
            self.stages[4].update(status="queued", payload={})

    async def handle(self, route) -> None:
        request = route.request
        url = urlparse(request.url)
        headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
        if request.method == "OPTIONS":
            return await route.fulfill(status=204, headers=headers)

        def reply(data, status=200):
            return route.fulfill(status=status, headers={**headers, "content-type": "application/json"}, body=json.dumps(data))
        path = url.path
        if path.startswith("/rest/v1/rpc/"):
            fn, args = path.rsplit("/", 1)[1], json.loads(request.post_data or "{}")
            self.rpc_calls.append((fn, args))
            self.apply(fn, args)
            return await reply({})
        if path.startswith("/rest/v1/"):
            table = path.rsplit("/", 1)[1]
            query = parse_qs(url.query)
            rows = {"projects": [self.project], "project_stages": self.stages, "artifacts": self.artifacts, "events": self.events,
                    "worker_heartbeats": [{"worker_id": "home-pc-01", "hostname": "CASA", "status": "online", "last_seen_at": datetime.now(timezone.utc).isoformat(),
                                           "details": {"job": {"stage_key": "character_images"}}}]}.get(table, [])
            if "project_id" in query:
                assert query["project_id"] == [f"eq.{self.project['id']}"]
            return await reply(rows)
        if path == "/storage/v1/object/sign/artifacts":
            paths = json.loads(request.post_data)["paths"]
            return await reply([{"path": p, "signedURL": f"/object/sign/artifacts/{p}?token=t", "error": None} for p in paths])
        if request.method == "DELETE" and path == "/storage/v1/object/artifacts":
            self.removed = json.loads(request.post_data or "{}").get("prefixes", [])
            return await reply([])
        if path.startswith("/storage/v1/object/sign/artifacts/"):
            return await route.fulfill(status=200, headers={**headers, "content-type": "image/jpeg"}, body=self.jpeg)
        if path.startswith("/auth/v1/"):
            return await reply({})
        return await route.fulfill(status=404, headers=headers, body="")


def session_script() -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    exp = int(time.time()) + 3600
    token = f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': 'user-1', 'exp': exp, 'role': 'authenticated'})}.sig"
    session = {"access_token": token, "refresh_token": "r", "token_type": "bearer", "expires_in": 3600, "expires_at": exp,
               "user": {"id": "user-1", "email": "dono@example.com", "aud": "authenticated", "role": "authenticated", "app_metadata": {}, "user_metadata": {}, "created_at": NOW}}
    return f"localStorage.setItem('sb-localhost-auth-token', {json.dumps(json.dumps(session))});"


def drive(panel: str, scenario, *, logged_in: bool = True, viewport=(1280, 900)):
    async def runner():
        from playwright.async_api import async_playwright
        fake = FakeSupabase()
        problems: list[str] = []
        async with async_playwright() as pw:
            try:
                chrome = await pw.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                chrome = await pw.chromium.launch(headless=True)
            context = await chrome.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            if logged_in:
                await context.add_init_script(session_script())
            page = await context.new_page()
            page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
            page.on("console", lambda msg: problems.append(f"console: {msg.text}") if msg.type == "error" and "WebSocket" not in msg.text and "ERR_" not in msg.text else None)
            await page.route(f"{FAKE}/**", fake.handle)
            await page.goto(panel)
            try:
                result = await scenario(page, fake)
            finally:
                await chrome.close()
        assert not problems, problems
        return result
    return asyncio.run(runner())


async def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def test_logged_out_shows_login_and_no_data(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Entrar").wait_for()
        await shot(page, "login")
        return await page.locator("input[type=password]").count(), await page.locator(".project").count()
    assert drive(panel, scenario, logged_in=False) == (1, 0)


def test_review_flow_sends_the_right_decisions(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        assert await page.locator(".stage").count() == 9
        assert await page.get_by_text("3 de 9 etapas aprovadas").is_visible()
        assert await page.locator(".localcard b").inner_text() == "Online"

        # a etapa em revisão abre sozinha, com as prévias carregadas do bucket privado
        panel_box = page.locator(".stagepanel")
        await panel_box.get_by_text("2 imagem(ns) de corpo inteiro.").wait_for()
        await page.wait_for_function("document.querySelectorAll('.mediacard img').length === 2 && [...document.querySelectorAll('.mediacard img')].every(i => i.naturalWidth > 0)")
        assert await panel_box.get_by_text("Aviso de teste.").is_visible()
        assert "gerar 2 folhas 16:9 no ChatGPT" in await panel_box.locator(".approval small").inner_text()
        await shot(page, "revisao")

        # regenerar um item
        await page.locator(".mediacard").first.get_by_role("button", name="Ajustar").click()
        await page.get_by_placeholder("O que ajustar (opcional)").fill("casaco mais escuro")
        await page.get_by_role("button", name="Gerar de novo").click()
        await page.get_by_text("Ajuste enfileirado").wait_for()

        # pedir ajustes exige comentário
        await page.get_by_role("button", name="Pedir ajustes").click()
        send = page.get_by_role("button", name="Enviar ajustes")
        assert await send.is_disabled()
        await page.get_by_placeholder("Descreva o que deve mudar").fill("Anna mais velha")
        await send.click()
        await page.get_by_text("Ajustes enviados").wait_for()

        # documento de etapa anterior: renderizado e higienizado
        await page.locator(".stage").nth(2).click()
        assert not await page.locator(".md table").is_visible(), "etapa aprovada começa com os documentos recolhidos"
        await page.get_by_text("Documentos", exact=True).click()
        await page.locator(".doc > summary").first.click()
        await page.locator(".md table").wait_for()
        unsafe = await page.evaluate("[document.querySelectorAll('.md script, .md img').length, [...document.querySelectorAll('.md a')].filter(a => (a.getAttribute('href') || '').startsWith('javascript')).length, window.hacked === true]")
        assert unsafe == [0, 0, False]
        assert await page.locator(".stagepanel .approval").count() == 0, "etapa já aprovada não oferece decisão"
        await shot(page, "documento")

        # aprovar: a etapa seguinte para por limite e oferece nova tentativa
        await page.locator(".stage").nth(3).click()
        await page.get_by_role("button", name="Aprovar e autorizar geração").click()
        await page.get_by_text("Etapa aprovada.").wait_for()
        await page.get_by_text("4 de 9 etapas aprovadas").wait_for()
        await page.locator(".stage").nth(4).click()
        await page.get_by_text("Precisa de você · LIMIT_REACHED").wait_for()
        assert await page.get_by_text("Aguarde a renovação da cota").is_visible()
        await shot(page, "parada")
        await page.get_by_role("button", name="Tentar novamente").click()
        await page.get_by_text("Nova tentativa enfileirada.").wait_for()

        # demais telas
        await page.get_by_role("button", name="Atividade").click()
        await page.get_by_text("Parado (LIMIT_REACHED): limite.").wait_for()
        await page.get_by_role("button", name="Integrações").click()
        await page.get_by_text("executando Imagens dos personagens").wait_for()
        await shot(page, "integracoes")

        # novo projeto
        await page.get_by_role("button", name="Projetos").click()
        create = page.get_by_role("button", name="Criar projeto")
        assert await create.is_disabled()
        await page.get_by_placeholder("https://www.tiktok.com/").fill("https://www.tiktok.com/@y/video/2")
        await page.get_by_placeholder("Título (opcional)").fill("Segundo")
        await page.get_by_role("radio", name="Cópia fiel").click()
        await create.click()
        await page.get_by_text("Projeto criado (cópia fiel).").wait_for()
        await page.get_by_role("button", name="Excluir").first.click()
        await page.get_by_role("button", name="Cancelar").click()
        await page.get_by_role("button", name="Excluir").first.click()
        await page.get_by_role("button", name="Sim, excluir").click()
        await page.get_by_text("Projeto excluído.").wait_for()
        assert fake.removed, "os arquivos do projeto devem ser removidos do Storage"
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert calls == [
        ("adjust_item", {"p_stage_id": "stage-4", "p_item_key": "img-personagem-anna-corpo", "p_prompt": None, "p_comment": "casaco mais escuro"}),
        ("decide_stage", {"p_stage_id": "stage-4", "p_decision": "request_changes", "p_comment": "Anna mais velha"}),
        ("decide_stage", {"p_stage_id": "stage-4", "p_decision": "approve"}),
        ("retry_stage", {"p_stage_id": "stage-5"}),
        ("create_project", {"p_title": "Segundo", "p_source": "https://www.tiktok.com/@y/video/2", "p_mode": "clone"}),
        ("delete_project", {"p_project_id": "11111111-aaaa-bbbb-cccc-000000000001"}),
    ]


def test_phone_layout_has_no_sideways_scroll(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.wait_for_function("document.querySelectorAll('.mediacard img').length === 2")
        await shot(page, "celular")
        approve = page.get_by_role("button", name="Aprovar e autorizar geração")
        await approve.scroll_into_view_if_needed()
        box = await approve.bounding_box()
        return await page.evaluate("[document.scrollingElement.scrollWidth, window.innerWidth]"), box
    (scroll_width, inner_width), box = drive(panel, scenario, viewport=(390, 844))
    assert scroll_width <= inner_width + 1, f"conteúdo com {scroll_width}px em tela de {inner_width}px"
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= inner_width and box["height"] >= 32


def test_start_from_ready_prompts_sends_a_structured_plan(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.get_by_role("tab", name="Já tenho os prompts").click()
        create = page.get_by_role("button", name=re.compile("Criar com"))
        assert await create.is_disabled()
        await page.get_by_label(re.compile("Imagens dos personagens")).fill("## Anna\nFull body of Anna.\n\n## Hund\nFull body of a dog.")
        await page.get_by_label(re.compile("Ambientes e cenas")).fill("## Cena 1\nReferências: Anna, Hund\nAnna and the dog in the kitchen.")
        await page.get_by_label(re.compile("^Vídeos")).fill("Clip A says Hallo\n---\nImagem inicial: Cena 1\nClip B")
        await page.get_by_text("2 item(ns)").first.wait_for()
        await shot(page, "importar-prompts")
        assert "Criar com 5 prompt(s)" in await create.inner_text()
        await create.click()
        await page.get_by_text("Projeto criado com 5 prompt(s).").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert [c[0] for c in calls] == ["create_project_from_plan"]
    args = calls[0][1]
    assert args["p_plan"] == {
        "character_images": [{"name": "Anna", "prompt": "Full body of Anna."}, {"name": "Hund", "prompt": "Full body of a dog."}],
        "scene_images": [{"name": "Cena 1", "prompt": "Anna and the dog in the kitchen.", "refs": ["Anna", "Hund"]}],
        "production": [{"name": "Clipe 1", "prompt": "Clip A says Hallo"}, {"name": "Clipe 2", "prompt": "Clip B", "start": "Cena 1"}],
    }


def test_gallery_lightbox_navigates_and_shows_overview(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.get_by_text("Precisa de você agora").wait_for()
        await page.get_by_text("Etapas aprovadas", exact=True).wait_for()
        await page.get_by_role("button", name=re.compile("Ampliar Anna")).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_text("1 de 2").wait_for()
        await page.keyboard.press("ArrowRight")
        await dialog.get_by_text("2 de 2").wait_for()
        await shot(page, "galeria")
        await page.keyboard.press("Escape")
        assert await dialog.count() == 0
        return await page.title()
    assert drive(panel, scenario).startswith("(1)")
