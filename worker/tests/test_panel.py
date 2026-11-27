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
        self.media = {"worker_id": "home-pc-01", "local_url": "http://127.0.0.1:8765", "public_url": "https://rapid-fox.trycloudflare.com", "token": "SENHA-DO-ESTUDIO"}
        self.heartbeat_age = 0
        self.jobs: list[dict] = []
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
            if table == "worker_media":
                return await reply([self.media] if self.media else [])
            rows = {"jobs": self.jobs, "projects": [self.project], "project_stages": self.stages, "artifacts": self.artifacts, "events": self.events,
                    "worker_heartbeats": [{"worker_id": "home-pc-01", "hostname": "CASA", "status": "online", "last_seen_at": datetime.fromtimestamp(time.time() - self.heartbeat_age, timezone.utc).isoformat(),
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
        await page.locator(".mediacard").last.scroll_into_view_if_needed()   # as prévias são "lazy": só carregam quando entram na tela
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
        assert await page.locator(".stagepanel").get_by_role("button", name="Aprovar").count() == 0, "etapa já aprovada não oferece aprovar de novo"
        assert await page.get_by_role("button", name="Voltar para esta etapa").count() == 1, "mas oferece voltar a ela"
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
        await page.get_by_role("tab", name="A partir de um vídeo do TikTok").click()
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
        ("create_project", {"p_title": "Segundo", "p_source": "https://www.tiktok.com/@y/video/2", "p_mode": "clone", "p_model": None, "p_effort": None}),
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


def test_copy_stage_puts_everything_of_the_stage_on_the_clipboard(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', { configurable: true,
            value: { writeText: async (t) => { window.__copied = [...(window.__copied || []), t]; } } })""")
        await page.get_by_role("button", name="Copiar etapa").click()
        await page.get_by_role("button", name="Copiado").wait_for()
        await shot(page, "copiar-etapa")
        await page.locator(".stage").nth(2).click()
        await page.locator(".stagepanel summary", has_text="Documentos").click()
        await page.locator(".doc > summary", has_text="Documento 04 · Personagens").click()
        await page.get_by_role("button", name="Copiar documento").click()
        return await page.evaluate("window.__copied")
    stage, doc = drive(panel, scenario)
    assert stage.startswith("# Anna und der Hund · Etapa 04 · Etapa character_images")
    for part in ("## Resumo", "2 imagem(ns) de corpo inteiro.", "- Aviso de teste.", "## Prompts (2)", "### Anna · corpo inteiro · imagens/personagens/personagem-anna-corpo.png", "Stylized 3D cartoon render."):
        assert part in stage, part
    assert doc == DOC


def test_other_items_can_be_adjusted_while_one_is_still_running(panel):
    async def scenario(page, fake):
        fake.stages[3].update(status="in_progress", payload={"progress": {"done": 0, "total": 1, "label": "Ajustando Anna · corpo inteiro…"}})
        fake.jobs = [{"id": "j1", "stage_id": "stage-4", "job_type": "regen_item", "status": "running", "input": {"item_key": "img-personagem-anna-corpo"}}]
        await page.reload()
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.get_by_text("1 item em ajuste").wait_for()
        anna, hund = (page.locator(".mediacard", has_text=f"{n} · corpo inteiro") for n in ("Anna", "Hund"))
        await anna.get_by_text("Em ajuste").wait_for()
        assert await anna.get_by_role("button", name="Ajustar").count() == 0, "o item em ajuste não aceita outro pedido"
        await hund.get_by_role("button", name="Ajustar").click()
        await hund.get_by_placeholder("O que ajustar (opcional)").fill("pelo mais claro")
        await shot(page, "ajuste-simultaneo")
        await hund.get_by_role("button", name="Gerar de novo").click()
        await page.get_by_text("Ajuste enfileirado: Hund · corpo inteiro.").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert calls == [("adjust_item", {"p_stage_id": "stage-4", "p_item_key": "img-personagem-hund-corpo", "p_prompt": None, "p_comment": "pelo mais claro"})]


def test_completed_project_offers_part_two_with_or_without_a_link(panel):
    async def scenario(page, fake):
        # Projeto em andamento: nada de "Criar parte 2"
        await page.get_by_text("Anna und der Hund").first.click()
        assert await page.get_by_role("button", name="Criar parte 2").count() == 0
        # Concluído: o botão aparece e abre o formulário
        fake.project["status"] = "completed"
        await page.reload()
        await page.get_by_text("Anna und der Hund").first.click()
        await page.get_by_role("button", name="Criar parte 2").click()
        await page.get_by_label("Ideias para a continuação").fill("um dragão aparece")
        assert await page.get_by_text("Sem link:").count() == 1
        await page.get_by_label("Link da parte seguinte do original").fill("https://www.tiktok.com/@x/video/2")
        assert await page.get_by_text("Com link:").count() == 1
        await page.get_by_role("button", name="Criar parte 2").last.click()
        await page.get_by_text("Continuação criada").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert ("create_sequel", {"p_parent": "11111111-aaaa-bbbb-cccc-000000000001", "p_source": "https://www.tiktok.com/@x/video/2",
                              "p_title": None, "p_notes": "um dragão aparece"}) in calls


def test_next_part_accepts_several_links_in_order(panel):
    async def scenario(page, fake):
        fake.project["status"] = "completed"
        await page.reload()
        await page.get_by_text("Anna und der Hund").first.click()
        await page.get_by_role("button", name="Criar parte 2").click()
        await page.get_by_label("Link da parte seguinte do original").fill("https://www.tiktok.com/@x/video/4")
        await page.get_by_role("button", name="Adicionar outro vídeo").click()
        await page.get_by_label("Link do vídeo 2").fill("https://www.tiktok.com/@x/video/descartado")
        await page.get_by_role("button", name="Adicionar outro vídeo").click()
        await page.get_by_label("Link do vídeo 3").fill("https://www.tiktok.com/@x/video/5")
        await page.get_by_role("button", name="Remover o vídeo 2").click()
        await page.get_by_text("Com 2 links:").wait_for()
        await shot(page, "varios-links")
        await page.get_by_role("button", name="Criar parte 2").last.click()
        await page.get_by_text("analisa os 2 vídeos").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert calls[0][0] == "create_sequel" and calls[0][1]["p_source"] == "https://www.tiktok.com/@x/video/4\nhttps://www.tiktok.com/@x/video/5"


def test_factory_form_sends_the_production_profile_and_blocks_impossible_ones(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        create = page.get_by_role("button", name="Criar na Fábrica")   # a Fábrica é a aba padrão
        assert await create.is_enabled()
        await page.get_by_label("Referências (até 5)").fill("https://www.tiktok.com/@a/video/1\n\nC:/v/2.mp4")
        await page.get_by_text("2 de 5 referência(s)").wait_for()
        await page.get_by_label("Mínimo de cenas").fill("30")   # 30 × 6 s = 180 s, mais que os 120 s do máximo
        assert await create.is_disabled()
        await page.get_by_text("somam 180 s").wait_for()
        await page.get_by_label("Mínimo de cenas").fill("12")
        await page.get_by_label("Estilo visual").select_option("clay")
        await page.get_by_placeholder("Título (opcional)").fill("Novela")
        await create.click()
        await page.get_by_text("Projeto criado na Fábrica de roteiros.").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert [c[0] for c in calls] == ["create_factory_project"]
    args = calls[0][1]
    assert args["p_title"] == "Novela" and args["p_model"] is None and args["p_effort"] is None
    profile = args["p_profile"]
    assert profile["references"] == ["https://www.tiktok.com/@a/video/1", "C:/v/2.mp4"]
    assert (profile["language"], profile["style"], profile["min_s"], profile["max_s"], profile["min_scenes"], profile["scene_min_s"], profile["scene_max_s"], profile["audio"]) == \
        ("pt-BR", "clay", 90, 120, 12, 6, 10, "dialogos")


def test_choosing_one_of_the_five_proposals_calls_choose_proposal(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        fake.stages[3].update(stage_key="f_proposals", title="Cinco propostas originais", payload={
            "summary": "Cinco propostas prontas.", "recommended": 2, "proposals": [{"n": n, "title": f"Título {n}", "hook": f"Gancho {n}"} for n in range(1, 6)]})
        await page.reload()
        await page.get_by_text("Qual história desenvolver?").wait_for()
        assert await page.locator(".proposalbtns button").count() == 5
        assert await page.get_by_role("button", name=re.compile("Título 2")).get_attribute("aria-pressed") == "true", "sem escolha vale a recomendada"
        await page.get_by_role("button", name=re.compile("Título 4")).click()
        await page.get_by_text("História 4 escolhida").wait_for()
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert ("choose_proposal", {"p_stage_id": "stage-4", "p_number": 4}) in calls


def _with_local_clip(fake):
    fake.artifacts.append({"id": "vid-1", "project_id": fake.project["id"], "stage_id": "stage-4", "artifact_type": "video", "name": "Clipe 01",
                           "item_key": "vid-clipe-01", "storage_path": None, "local_relative_path": "videos/clipe 01.mp4", "version": 1,
                           "content": {"local": True, "duration": 10}, "updated_at": NOW})


async def _watch(page):
    """Anota os endereços de vídeo que o player pede (e responde vazio: só importa de onde ele busca)."""
    seen: list[str] = []

    async def clip(route):
        seen.append(route.request.url)
        await route.fulfill(status=200, headers={"content-type": "video/mp4", "access-control-allow-origin": "*"}, body=b"")
    await page.route(re.compile(r".*/v/11111111-aaaa-bbbb-cccc-000000000001/.*"), clip)
    return seen


async def _video_state(page, seen):
    """(endereço pedido pelo player, aviso mostrado no lugar do player)."""
    await page.get_by_text("Clipe 01").first.wait_for()
    for _ in range(40):
        note = await page.evaluate("document.querySelector('.videoerr span')?.textContent ?? null")
        if seen or note:
            await page.wait_for_timeout(300)
            return (seen[0] if seen else None), await page.evaluate("document.querySelector('.videoerr span')?.textContent ?? null")
        await page.wait_for_timeout(100)
    return None, None


def test_local_video_plays_from_the_pc_address_when_the_panel_is_open_on_that_pc(panel):
    async def scenario(page, fake):
        _with_local_clip(fake)
        seen = await _watch(page)
        await page.reload()
        await page.get_by_text("Anna und der Hund").first.click()
        return await _video_state(page, seen)
    src, note = drive(panel, scenario)
    assert src == "http://127.0.0.1:8765/v/11111111-aaaa-bbbb-cccc-000000000001/videos/clipe%2001.mp4?k=SENHA-DO-ESTUDIO", note


def test_away_from_home_the_video_comes_from_the_tunnel_and_never_from_supabase_storage(panel):
    async def scenario(page, fake):
        _with_local_clip(fake)
        # o mesmo painel aberto em outro endereço (celular/site): não é o PC do worker, então tem de usar o túnel
        async def serve(route):
            response = await route.fetch(url=route.request.url.replace("http://panel.test", panel))
            await route.fulfill(response=response)
        await page.route("http://panel.test/**", serve)
        seen = await _watch(page)
        await page.goto("http://panel.test/")
        await page.get_by_text("Anna und der Hund").first.click()
        src, note = await _video_state(page, seen)
        storage = [r for r in fake.rpc_calls if "storage" in r[0]]
        return src, note, storage
    src, note, storage = drive(panel, scenario)
    assert src == "https://rapid-fox.trycloudflare.com/v/11111111-aaaa-bbbb-cccc-000000000001/videos/clipe%2001.mp4?k=SENHA-DO-ESTUDIO", note
    assert storage == []


def test_video_explains_why_it_cannot_play_when_the_pc_is_off_or_remote_access_is_not_set_up(panel):
    async def off(page, fake):
        _with_local_clip(fake)
        fake.heartbeat_age = 600
        seen = await _watch(page)
        await page.reload()
        await page.get_by_text("Anna und der Hund").first.click()
        return await _video_state(page, seen)
    src, note = drive(panel, off)
    assert src is None and "desligado" in note and "só nele" in note

    async def no_tunnel(page, fake):
        _with_local_clip(fake)
        fake.media["public_url"] = None
        async def serve(route):
            await route.fulfill(response=await route.fetch(url=route.request.url.replace("http://panel.test", panel)))
        await page.route("http://panel.test/**", serve)
        seen = await _watch(page)
        await page.goto("http://panel.test/")
        await page.get_by_text("Anna und der Hund").first.click()
        return await _video_state(page, seen)
    src, note = drive(panel, no_tunnel)
    assert src is None and "cloudflared" in note


def test_model_and_effort_are_chosen_on_creation_and_changed_inside_the_project(panel):
    async def scenario(page, fake):
        await page.get_by_role("heading", name="Anna und der Hund").wait_for()
        await page.get_by_label("Modelo do Claude", exact=True).select_option("claude-opus-5-5")
        await page.get_by_label("Nível de esforço", exact=True).select_option("high")
        await page.get_by_role("button", name="Criar na Fábrica").click()
        await page.get_by_text("Projeto criado na Fábrica de roteiros.").wait_for()
        await page.get_by_text("Anna und der Hund").first.click()
        await page.get_by_label("Nível de esforço do projeto").select_option("max")
        await page.get_by_text("Esforço do projeto: Máximo").wait_for()
        await shot(page, "esforco")
        return fake.rpc_calls
    calls = drive(panel, scenario)
    assert calls[0][0] == "create_factory_project" and calls[0][1]["p_model"] == "claude-opus-5-5" and calls[0][1]["p_effort"] == "high"
    assert ("set_project_effort", {"p_project_id": "11111111-aaaa-bbbb-cccc-000000000001", "p_effort": "max"}) in calls
