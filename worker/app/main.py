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

from . import config
from .brain import ClaudeBrain
from .browser import selectors
from .browser.chatgpt import ChatGPT
from .browser.grok import Grok
from .browser.session import Browser
from .claude_bridge.batches import create_batch
from .db import Db, SupabaseSink, now
from .errors import Blocked
from .pipeline import stages
from .pipeline.core import Ctx

HOST = socket.gethostname()


async def process(db: Db, job: dict, brain_for, images, video, pools: dict | None = None) -> None:
    """Executa uma tarefa e grava o desfecho. Nunca aprova nada: a etapa termina em revisão."""
    kind, inp = job.get("job_type"), job.get("input") or {}
    if kind == "prepare_claude_batch":  # rota manual opcional: pacote para o Claude Code operar o navegador
        folder = create_batch(project_id=str(job["project_id"]), service=inp["service"], purpose=inp["purpose"], items=inp["items"], approved=False)
        db.finish_job(job["id"], "succeeded", output={"batch_dir": str(folder), "status": "awaiting_user_authorization"})
        return
    if kind not in ("run_stage", "regen_item") or not job.get("stage_id"):
        db.finish_job(job["id"], "failed", error_code="UNKNOWN_JOB", error_message=f"Tipo de tarefa não suportado: {kind}")
        return

    project, stage = db.project(job["project_id"]), db.stage(job["stage_id"])
    project_dir = config.PROJECTS_DIR / project["id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    sink = SupabaseSink(db, project, stage, project_dir)
    ctx = Ctx(project_id=project["id"], source=project.get("source_url") or project.get("source_video_path") or "", dir=project_dir,
              sink=sink, brain=brain_for(project_dir), images=images, video=video, stage_key=stage["stage_key"], version=stage["current_version"],
              feedback=inp.get("feedback"), max_clip=config.GROK_MAX_CLIP_SECONDS, whisper_model=config.WHISPER_MODEL,
              language=config.REFERENCE_LANGUAGE, plan=project.get("import_plan"), clone=project.get("mode") == "clone", image_concurrency=config.IMAGE_CONCURRENCY, video_concurrency=config.VIDEO_CONCURRENCY,
              image_slots=(pools or {}).get("image"), video_slots=(pools or {}).get("video"), pilot_approved=bool((stage.get("payload") or {}).get("pilot_approved")), pause=lambda: asyncio.sleep(config.PAUSE_BETWEEN_ITEMS))
    regen = kind == "regen_item"
    db.update_stage(stage["id"], status="in_progress", remove=("error", "progress"))
    try:
        if regen:
            await stages.regen_item(ctx, inp.get("item_key", ""), inp.get("comment"))
            db.update_stage(stage["id"], status="pending_review", remove=("progress",))
        else:
            result = await stages.run_stage(ctx)
            db.update_stage(stage["id"], status="pending_review", submitted_at=now(), remove=("progress", "next_generation", "warnings", "pilot", "pilot_remaining"),
                            patch={"local_dir": str(project_dir), **result})
            ctx.log(f"Etapa pronta para revisão. {result.get('summary', '')}")
        db.finish_job(job["id"], "succeeded", output={"stage_key": stage["stage_key"]})
    except Blocked as exc:
        # Regenerar um item não invalida o restante: a etapa volta para revisão com o aviso.
        db.update_stage(stage["id"], status="pending_review" if regen else "failed", remove=("progress",), patch={"error": exc.as_dict()})
        db.finish_job(job["id"], "blocked", error_code=exc.code, error_message=exc.message)
        ctx.log(f"Parado ({exc.code}): {exc.message} {exc.action}", "warn")
    except Exception as exc:  # noqa: BLE001 - qualquer falha fica registrada com etapa e ação recomendada
        traceback.print_exc()
        error = {"code": "HANDLER_ERROR", "message": f"{type(exc).__name__}: {exc}"[:600], "blocked": False,
                 "action": "Veja o terminal do worker para o detalhe e clique em Tentar novamente."}
        db.update_stage(stage["id"], status="pending_review" if regen else "failed", remove=("progress",), patch={"error": error})
        db.finish_job(job["id"], "failed", error_code="HANDLER_ERROR", error_message=error["message"])
        ctx.log(f"Falha na etapa: {error['message']}", "error")


async def heartbeat(db: Db, state: dict) -> None:
    while True:
        try:
            db.heartbeat(HOST, "online", state)
        except Exception as exc:  # noqa: BLE001
            print(f"Heartbeat falhou: {exc}", flush=True)
        await asyncio.sleep(15)


async def main() -> None:
    if not config.SUPABASE_URL or not config.SUPABASE_SECRET_KEY or "YOUR_PROJECT_REF" in config.SUPABASE_URL:
        raise SystemExit("Configure SUPABASE_URL e SUPABASE_SECRET_KEY em worker/.env (veja worker/.env.example).")
    db = Db()
    browser = Browser()
    sel = selectors.load(config.SELECTORS_FILE)
    images, video = ChatGPT(browser, sel), Grok(browser, sel)
    # Cérebro: Claude (padrão), uma conversa por tarefa na pasta do projeto. BRAIN=chatgpt usa o ChatGPT do navegador.
    brain_for = (lambda project_dir: images) if config.BRAIN == "chatgpt" else (lambda project_dir: ClaudeBrain(project_dir))
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
    beat = asyncio.create_task(heartbeat(db, state))
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
        try:
            db.heartbeat(HOST, "offline", {})
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
