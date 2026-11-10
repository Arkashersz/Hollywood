"""Worker local do TikTok Studio.

Fica em execução no PC de casa, busca tarefas no Supabase (conexão só de saída), conduz as
etapas e publica os resultados para aprovação no painel. Uso: `python -m app.main`.
"""
from __future__ import annotations

import asyncio
import socket
import sys
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


async def process(db: Db, job: dict, brain_for, images, video) -> None:
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
              language=config.REFERENCE_LANGUAGE, pause=lambda: asyncio.sleep(config.PAUSE_BETWEEN_ITEMS))
    regen = kind == "regen_item"
    db.update_stage(stage["id"], status="in_progress", remove=("error", "progress"))
    try:
        if regen:
            await stages.regen_item(ctx, inp.get("item_key", ""), inp.get("comment"))
            db.update_stage(stage["id"], status="pending_review", remove=("progress",))
        else:
            result = await stages.run_stage(ctx)
            db.update_stage(stage["id"], status="pending_review", submitted_at=now(), remove=("progress", "next_generation", "warnings"),
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
    state = {"job": None, "version": "0.3.0", "brain": config.BRAIN}
    print(f"TikTok Studio Worker {config.WORKER_ID} iniciado em {HOST}. Projetos em {config.PROJECTS_DIR}", flush=True)
    stale = db.requeue_stale()
    if stale:
        print(f"{stale} tarefa(s) interrompida(s) voltaram para a fila.", flush=True)
    beat = asyncio.create_task(heartbeat(db, state))
    try:
        while True:
            try:
                job = db.claim()
            except Exception as exc:  # noqa: BLE001 - rede instável não derruba o worker
                print(f"Sem acesso ao Supabase: {exc}", flush=True)
                await asyncio.sleep(max(config.POLL_SECONDS, 10))
                continue
            if job is None:
                await asyncio.sleep(config.POLL_SECONDS)
                continue
            state["job"] = {"id": job["id"], "type": job["job_type"], "stage_key": (job.get("input") or {}).get("stage_key")}
            try:
                await process(db, job, brain_for, images, video)
            except Exception as exc:  # noqa: BLE001 - falha ao gravar o desfecho
                traceback.print_exc()
                try:
                    db.finish_job(job["id"], "failed", error_code="WORKER_ERROR", error_message=str(exc))
                except Exception:  # noqa: BLE001
                    pass
            state["job"] = None
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
