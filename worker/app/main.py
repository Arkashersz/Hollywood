"""Worker local do TikTok Studio.

Fica em execução no PC de casa, busca tarefas no Supabase (conexão só de saída), conduz as
etapas e publica os resultados para aprovação no painel. Uso: `python -m app.main`.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import time
import traceback

from . import config, folders, media_server, tunnel
from .brain import ClaudeBrain
from .browser import selectors
from .browser.chatgpt import ChatGPT
from .browser.grok import Grok
from .browser.session import Browser
from .claude_bridge.batches import create_batch
from .db import Db, SupabaseSink, now
from .errors import Blocked
from .pipeline import factory, stages
from .pipeline.core import Ctx, sequel_context

HOST = socket.gethostname()


def has_no_body_stage(db, project: dict) -> bool:
    """Projetos novos não têm a etapa de imagens de corpo inteiro: a folha 16:9 sai direto da descrição."""
    keys = getattr(db, "stage_keys", None)
    return bool(keys) and "character_images" not in keys(project["id"])


def story_of(project: dict) -> dict | None:
    """Projeto criado a partir de uma ideia de história: a ideia, a duração total e a quantidade mínima de cenas."""
    if project.get("mode") != "story" or (project.get("parent_id") and not project.get("story_idea")):
        return None   # a continuação de uma história não herda ideia nem duração: segue as regras da continuação
    return {"idea": project.get("story_idea") or "", "target_s": int(project.get("target_seconds") or 60), "min_scenes": int(project.get("min_scenes") or 1)}


def factory_context(db, project: dict) -> tuple[dict | None, int | None]:
    """Fábrica de roteiros: o perfil de produção (Fase 0) e a proposta que o usuário escolheu desenvolver. Fora desse modo, (None, None)."""
    if project.get("mode") != "factory":
        return None, None
    try:
        profile = factory.normalize_profile(project.get("profile"))
    except ValueError as exc:
        raise Blocked("MISSING_INPUT", f"Perfil de produção inválido: {exc}", "Crie o projeto de novo com um perfil possível.") from None
    reader = getattr(db, "stage_payload", None)
    chosen = ((reader(project["id"], "f_proposals") if reader else None) or {}).get("chosen")
    return profile, int(chosen) if chosen else None


def describe_failure(ctx: Ctx, exc: Exception) -> tuple[str, dict]:
    """Desfecho de uma falha: o status da tarefa e o erro mostrado no painel (também registrado na atividade)."""
    if isinstance(exc, Blocked):
        ctx.log(f"Parado ({exc.code}): {exc.message} {exc.action}", "warn")
        return "blocked", exc.as_dict()
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    error = {"code": "HANDLER_ERROR", "message": f"{type(exc).__name__}: {exc}"[:600], "blocked": False,
             "action": "Veja o terminal do worker para o detalhe e clique em Tentar novamente."}
    if getattr(exc, "item", None):
        error["item"] = exc.item
    ctx.log(f"Falha na etapa: {error['message']}", "error")
    return "failed", error


RETRY_WAIT_S = 2.0


async def persist(action, attempts: int = 5) -> None:
    """Grava um desfecho insistindo: se a rede piscar, a tarefa não pode ficar presa em andamento (o item não aceitaria novo ajuste)."""
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts:
                print(f"Não consegui gravar no Supabase: {exc}. Reinicie o worker se um item ficar preso em ajuste.", flush=True)
                return
            await asyncio.sleep(RETRY_WAIT_S * attempt)


def adjust_targets(job: dict) -> dict[str, str | None]:
    inp = job.get("input") or {}
    if inp.get("items"):
        return {str(i.get("item_key", "")): i.get("comment") for i in inp["items"]}
    return {str(inp.get("item_key", "")): inp.get("comment")}


async def adjust_session(db: Db, first: dict, stage: dict, ctx: Ctx) -> None:
    """Ajustes de uma etapa. Enquanto roda, os pedidos novos do painel entram junto e são gerados ao mesmo tempo, dividindo as
    mesmas abas. Um único contexto para todos (os registros em disco não se atropelam); cada pedido tem o seu desfecho, e a
    falha de um não derruba os outros. A etapa volta para revisão quando não sobra nenhum."""
    for attr, width in (("image_slots", ctx.image_concurrency), ("video_slots", ctx.video_concurrency)):
        if getattr(ctx, attr) is None:   # sem abas compartilhadas entre projetos: os pedidos desta etapa dividem estas
            slots: asyncio.Queue[int] = asyncio.Queue()
            for n in range(max(1, width)):
                slots.put_nowait(n)
            setattr(ctx, attr, slots)
    claim_more = getattr(db, "claim_regen_jobs", None)
    waiting: list[dict] = [first]
    running: dict[asyncio.Task, tuple[dict, set[str]]] = {}
    total: set[str] = set()
    done: set[str] = set()
    failures: list[dict] = []
    warnings: list[str] = []

    def report(item, finished: bool) -> None:
        if finished:
            done.add(item.key)
        label = f"Ajustando · pronto: {item.name}" if finished else f"Ajustando {item.name}…"
        ctx.sink.progress(ctx.stage_key, len(done), len(total), label)

    ctx.on_progress = report

    def absorb() -> None:
        if claim_more is None:
            return
        try:
            fresh = claim_more(stage["id"])
        except Exception as exc:  # noqa: BLE001 - rede instável: tenta de novo na próxima volta
            print(f"Não consegui buscar novos ajustes: {exc}", flush=True)
            return
        waiting.extend(fresh)

    def accept(job: dict) -> None:
        """Prompt editado no painel para este pedido (vem na própria tarefa)."""
        inp = job.get("input") or {}
        if inp.get("prompt") and inp.get("item_key"):
            ctx.overrides[inp["item_key"]] = inp["prompt"]

    def start_ready() -> None:
        busy = {k for _, keys in running.values() for k in keys}
        for job in list(waiting):
            keys = set(adjust_targets(job))
            try:
                accept(job)
                blocked = bool(busy) and stages.related_items(ctx, keys, busy)
            except Exception:  # noqa: BLE001 - dados ausentes: o próprio ajuste explica o erro
                blocked = False
            busy |= keys   # quem veio depois e depende deste também espera
            if blocked:
                continue
            waiting.remove(job)
            total.update(keys)
            running[asyncio.create_task(stages.regen_items(ctx, adjust_targets(job)))] = (job, keys)

    def collect(task: asyncio.Task, job: dict) -> tuple[str, dict]:
        try:
            warnings.extend(task.result().get("warnings") or [])
            return "succeeded", {}
        except Exception as exc:  # noqa: BLE001
            status, error = describe_failure(ctx, exc)
            failures.append(error)
            return status, error

    def finish(job: dict, status: str, error: dict) -> None:
        if status == "succeeded":
            db.finish_job(job["id"], status, output={"stage_key": stage["stage_key"]})
        else:
            db.finish_job(job["id"], status, error_code=error["code"], error_message=error["message"])

    def to_review() -> None:
        """A etapa volta para revisão: um ajuste que falhou não invalida o restante, só aparece como aviso."""
        payload = db.stage(stage["id"]).get("payload") or {}
        # Avisos de "desatualizado" são refeitos a cada ajuste; os demais avisos da etapa ficam como estavam.
        kept = [w for w in (stage.get("payload") or {}).get("warnings") or [] if not str(w).startswith(stages.STALE_PREFIX)]
        notes = list(dict.fromkeys(kept + warnings))
        patch: dict = {}
        if notes:
            patch["warnings"] = notes
        if failures:
            error = dict(failures[0])
            others = [f.get("item", {}).get("name") or f["code"] for f in failures[1:]]
            if others:
                error["message"] = f"{error['message']} Também falharam: {', '.join(others)}."
            patch["error"] = error
        if ctx.overrides:   # o progresso gravado durante a execução não pode apagar um prompt editado no meio dela
            patch["prompt_overrides"] = {**(payload.get("prompt_overrides") or {}), **ctx.overrides}
        db.update_stage(stage["id"], status="pending_review", remove=("progress",) + (() if notes else ("warnings",)), patch=patch or None)

    finished: list[tuple[dict, str, dict]] = []
    while True:
        absorb()
        start_ready()
        for job, status, error in finished:
            await persist(lambda: finish(job, status, error))
        finished = []
        if running:
            ready, _ = await asyncio.wait(list(running), timeout=min(config.POLL_SECONDS, 3), return_when=asyncio.FIRST_COMPLETED)
            for task in ready:
                job, _ = running.pop(task)
                status, error = collect(task, job)
                finished.append((job, status, error))
            if running or waiting:
                continue
        # Nada rodando: devolve a etapa antes de encerrar as últimas tarefas e confere de novo, para não perder um pedido
        # que chegou neste instante (ele reabre a etapa e entra nesta mesma execução).
        absorb()
        if not waiting:
            await persist(to_review)
            absorb()
        for job, status, error in finished:
            await persist(lambda: finish(job, status, error))
        finished = []
        if not waiting:
            return
        await persist(lambda: db.update_stage(stage["id"], status="in_progress"))


async def process(db: Db, job: dict, brain_for, images, video, pools: dict | None = None) -> None:
    """Executa uma tarefa e grava o desfecho. Nunca aprova nada: a etapa termina em revisão."""
    kind, inp = job.get("job_type"), job.get("input") or {}
    if kind == "prepare_claude_batch":  # rota manual opcional: pacote para o Claude Code operar o navegador
        folder = create_batch(project_id=str(job["project_id"]), service=inp["service"], purpose=inp["purpose"], items=inp["items"], approved=False)
        db.finish_job(job["id"], "succeeded", output={"batch_dir": str(folder), "status": "awaiting_user_authorization"})
        return
    if kind == "open_folder":   # o painel só pede; quem abre a pasta é este PC
        folder = folders.find_project_dir(config.PROJECTS_DIR, str(job["project_id"]))
        if folder is None:
            db.finish_job(job["id"], "failed", error_code="NO_FOLDER", error_message="A pasta deste projeto não existe neste PC.")
            return
        target = folder / "videos" if (folder / "videos").is_dir() else folder
        try:
            folders.open_in_explorer(target)
        except Exception as exc:  # noqa: BLE001
            db.finish_job(job["id"], "failed", error_code="OPEN_FAILED", error_message=f"Não consegui abrir {target}: {exc}")
            return
        db.finish_job(job["id"], "succeeded", output={"path": str(target)})
        return
    if kind not in ("run_stage", "regen_item") or not job.get("stage_id"):
        db.finish_job(job["id"], "failed", error_code="UNKNOWN_JOB", error_message=f"Tipo de tarefa não suportado: {kind}")
        return

    project, stage = db.project(job["project_id"]), db.stage(job["stage_id"])
    project_dir = folders.resolve_project_dir(config.PROJECTS_DIR, project)
    sink = SupabaseSink(db, project, stage, project_dir)
    try:
        sequel = sequel_context(project, config.PROJECTS_DIR)
        profile, chosen = factory_context(db, project)
    except Blocked as exc:   # sem o roteiro da parte anterior neste PC (ou com um perfil impossível) não há como continuar
        db.update_stage(stage["id"], status="failed", remove=("progress",), patch={"error": exc.as_dict()})
        db.finish_job(job["id"], "blocked", error_code=exc.code, error_message=exc.message)
        return
    ctx = Ctx(sequel=sequel, project_id=project["id"], source=project.get("source_url") or project.get("source_video_path") or "", dir=project_dir,
              sink=sink, brain=brain_for(project_dir, project.get("model"), project.get("effort")), images=images, video=video, stage_key=stage["stage_key"], version=stage["current_version"],
              feedback=inp.get("feedback"), max_clip=factory.MAX_CLIP_S if profile else config.GROK_MAX_CLIP_SECONDS, profile=profile, chosen=chosen, whisper_model=config.WHISPER_MODEL,
              language=config.REFERENCE_LANGUAGE, plan=project.get("import_plan"), clone=project.get("mode") == "clone", story=story_of(project), direct_sheets=has_no_body_stage(db, project), image_concurrency=config.IMAGE_CONCURRENCY, video_concurrency=config.VIDEO_CONCURRENCY,
              image_slots=(pools or {}).get("image"), video_slots=(pools or {}).get("video"), pilot_approved=bool((stage.get("payload") or {}).get("pilot_approved")),
              overrides=dict((stage.get("payload") or {}).get("prompt_overrides") or {}),
              story_cut=(stage.get("payload") or {}).get("story_cut") if stage["stage_key"] == "video_prompts" else None, force=set(inp.get("force_items") or []), pause=lambda: asyncio.sleep(config.PAUSE_BETWEEN_ITEMS))
    db.update_stage(stage["id"], status="in_progress", remove=("error", "progress"))
    if kind == "regen_item":
        await adjust_session(db, job, stage, ctx)
        return
    try:
        result = await stages.run_stage(ctx)
        db.update_stage(stage["id"], status="pending_review", submitted_at=now(), remove=("progress", "next_generation", "warnings", "pilot", "pilot_remaining", "chosen", "proposals", "recommended"),
                        patch={"local_dir": str(project_dir), **result})
        ctx.log(f"Etapa pronta para revisão. {result.get('summary', '')}")
        db.finish_job(job["id"], "succeeded", output={"stage_key": stage["stage_key"]})
    except Exception as exc:  # noqa: BLE001 - qualquer falha fica registrada com etapa e ação recomendada
        status, error = describe_failure(ctx, exc)
        db.update_stage(stage["id"], status="failed", remove=("progress",), patch={"error": error})
        db.finish_job(job["id"], status, error_code=error["code"], error_message=error["message"])


async def heartbeat(db: Db, state: dict, media: tunnel.RemoteMedia | None = None) -> None:
    published, warned = None, False
    while True:
        try:
            db.heartbeat(HOST, "online", state)
        except Exception as exc:  # noqa: BLE001
            print(f"Heartbeat falhou: {exc}", flush=True)
        if media is not None:
            snapshot = media.snapshot()
            if snapshot != published:   # o endereço do túnel muda a cada abertura; o painel precisa do atual
                try:
                    db.publish_media(snapshot)
                    published, warned = snapshot, False
                except Exception as exc:  # noqa: BLE001
                    if not warned:
                        print(f"Não consegui publicar o endereço dos vídeos (aplique a migração 0018_worker_media.sql): {exc}", flush=True)
                        warned = True
        await asyncio.sleep(15)


def start_media() -> tunnel.RemoteMedia:
    """Servidor local de vídeos (com senha) e, se possível, o túnel que o torna alcançável fora de casa."""
    token = media_server.load_token(config.RUNTIME_DIR)
    server = media_server.start(config.PROJECTS_DIR, config.LOCAL_MEDIA_PORT, token)
    if server is None:
        return tunnel.RemoteMedia(token, None)
    port = server.server_address[1]
    media = tunnel.RemoteMedia(token, f"http://127.0.0.1:{port}", config.MEDIA_PUBLIC_URL or None)
    if config.MEDIA_PUBLIC_URL:
        print(f"Vídeos acessíveis de fora de casa em {config.MEDIA_PUBLIC_URL} (endereço informado em MEDIA_PUBLIC_URL).", flush=True)
    elif config.MEDIA_TUNNEL != "off":
        exe = tunnel.find_cloudflared(config.CLOUDFLARED_PATH)
        if exe:
            media.tunnel = tunnel.Tunnel(tunnel.command(exe, port)).start()
        else:
            print("Vídeos só neste PC: para vê-los de fora de casa instale o cloudflared (winget install Cloudflare.cloudflared) e reinicie o worker.", flush=True)
    return media


async def main() -> None:
    if not config.SUPABASE_URL or not config.SUPABASE_SECRET_KEY or "YOUR_PROJECT_REF" in config.SUPABASE_URL:
        raise SystemExit("Configure SUPABASE_URL e SUPABASE_SECRET_KEY em worker/.env (veja worker/.env.example).")
    db = Db()
    media = start_media()
    browser = Browser()
    sel = selectors.load(config.SELECTORS_FILE)
    images, video = ChatGPT(browser, sel), Grok(browser, sel)
    # Cérebro: Claude (padrão), uma conversa por tarefa na pasta do projeto. BRAIN=chatgpt usa o ChatGPT do navegador.
    brain_for = ((lambda project_dir, model=None, effort=None: images) if config.BRAIN == "chatgpt"
                 else (lambda project_dir, model=None, effort=None: ClaudeBrain(project_dir, model=model, effort=effort)))
    print(f"Cérebro: {'ChatGPT (navegador)' if config.BRAIN == 'chatgpt' else 'Claude (Claude Code local)'} · imagens: ChatGPT · vídeos: Grok", flush=True)
    parallel = 1 if config.BRAIN == "chatgpt" else config.MAX_PARALLEL_JOBS   # com o ChatGPT como cérebro, a aba de texto é a mesma das imagens
    pools = {}
    for name, size in (("image", config.IMAGE_CONCURRENCY), ("video", config.VIDEO_CONCURRENCY)):
        pools[name] = asyncio.Queue()
        for n in range(size):
            pools[name].put_nowait(n)
    state = {"job": None, "jobs": [], "version": "0.4.0", "brain": config.BRAIN, "parallel": parallel}
    print(f"Projetos ao mesmo tempo: {parallel} · abas de imagem: {config.IMAGE_CONCURRENCY} · abas de vídeo: {config.VIDEO_CONCURRENCY}", flush=True)
    print(f"TikTok Studio Worker {config.WORKER_ID} iniciado em {HOST}. Projetos em {config.PROJECTS_DIR}", flush=True)
    stale = db.requeue_stale()
    if stale:
        print(f"{stale} tarefa(s) interrompida(s) voltaram para a fila.", flush=True)
    beat = asyncio.create_task(heartbeat(db, state, media))
    idle_since = time.monotonic()
    active: dict[str, tuple[asyncio.Task, str, str]] = {}   # id da tarefa -> (execução, id do projeto, etapa)

    async def run_one(job: dict) -> None:
        try:
            await process(db, job, brain_for, images, video, pools)
        except Exception as exc:  # noqa: BLE001 - falha ao gravar o desfecho
            traceback.print_exc()
            try:
                db.finish_job(job["id"], "failed", error_code="WORKER_ERROR", error_message=str(exc))
            except Exception:  # noqa: BLE001
                pass

    def publish() -> None:
        state["jobs"] = [{"id": jid, "project_id": pid, "stage_key": stage} for jid, (_, pid, stage) in active.items()]
        state["job"] = state["jobs"][0] if state["jobs"] else None

    try:
        while True:
            for jid in [j for j, (task, _, _) in active.items() if task.done()]:
                del active[jid]
                idle_since = time.monotonic()
            publish()
            try:
                while len(active) < parallel:
                    job = db.claim()
                    if job is None:
                        break
                    if any(pid == job["project_id"] for _, pid, _ in active.values()):
                        db.release_job(job["id"])   # um projeto por vez: esta tarefa espera a atual do mesmo projeto
                        break
                    active[job["id"]] = (asyncio.create_task(run_one(job)), job["project_id"], (job.get("input") or {}).get("stage_key") or job["job_type"])
                    print(f"Iniciada tarefa de {(job.get('input') or {}).get('stage_key') or job['job_type']} ({len(active)}/{parallel} em andamento).", flush=True)
                    publish()
            except Exception as exc:  # noqa: BLE001 - rede instável não derruba o worker
                print(f"Sem acesso ao Supabase: {exc}", flush=True)
                await asyncio.sleep(max(config.POLL_SECONDS, 10))
                continue
            if not active:
                # Nada em andamento (tudo foi para a sua aprovação): libera o Chrome e só consulta a fila.
                if browser.is_open and time.monotonic() - idle_since > config.IDLE_CLOSE_SECONDS:
                    await browser.close()
                    print("Ocioso: Chrome do worker fechado. Reabre quando houver nova tarefa (após sua aprovação).", flush=True)
                await asyncio.sleep(config.POLL_SECONDS)
            else:
                await asyncio.wait([task for task, _, _ in active.values()], timeout=config.POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
    finally:
        beat.cancel()
        media.stop()
        try:
            db.heartbeat(HOST, "offline", {})
            db.publish_media({**media.snapshot(), "public_url": None})   # PC desligado: o painel avisa em vez de tentar um túnel morto
        except Exception:  # noqa: BLE001
            pass
        await browser.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Worker encerrado.")
