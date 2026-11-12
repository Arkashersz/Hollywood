"""Desfecho das tarefas no worker, com um banco em memória no lugar do Supabase."""
from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from app import config, main, media
from app.errors import Blocked
from test_pipeline import FakeLLM, FakeVideo


class FakeDb:
    def __init__(self, source: str):
        self.projects = {"p1": {"id": "p1", "source_url": source, "title": "Teste"}}
        self.stages = {}
        self.jobs = {}
        self.events = []
        self.artifacts = {}
        self.uploads = {}

    def add_stage(self, key: str, version: int = 1) -> str:
        self.stages[key] = {"id": key, "project_id": "p1", "stage_key": key, "status": "queued", "current_version": version, "payload": {}}
        return key

    def job(self, kind: str, stage: str, **inp) -> dict:
        job = {"id": f"j{len(self.jobs) + 1}", "project_id": "p1", "stage_id": stage, "job_type": kind, "input": {"stage_key": stage, **inp}}
        self.jobs[job["id"]] = {"status": "running"}
        return job

    def project(self, project_id):
        return self.projects[project_id]

    def stage(self, stage_id):
        return dict(self.stages[stage_id])

    def update_stage(self, stage_id, *, patch=None, remove=(), **fields):
        stage = self.stages[stage_id]
        payload = dict(stage["payload"])
        for key in remove:
            payload.pop(key, None)
        payload.update(patch or {})
        stage.update(fields)
        stage["payload"] = payload

    def finish_job(self, job_id, status, *, output=None, error_code=None, error_message=None):
        self.jobs[job_id] = {"status": status, "output": output, "error_code": error_code, "error_message": error_message}

    def event(self, project_id, stage_key, level, message, details=None):
        self.events.append((stage_key, level, message))

    def upsert_artifact(self, row):
        self.artifacts[row["item_key"]] = row

    def upload(self, path, data, content_type):
        self.uploads[path] = (data, content_type)


@pytest.fixture
def world(tmp_path, sample_video, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(media, "transcribe", lambda audio, model, language: {
        "language": "de", "language_probability": 0.97, "segments": [{"start": 0.0, "end": 2.0, "text": "Hallo."}]})
    db, llm, video = FakeDb(str(sample_video)), FakeLLM(sample_image_size=(2048, 3072)), FakeVideo(sample_video)

    def run(kind: str, stage: str, **inp) -> dict:
        if stage not in db.stages:
            db.add_stage(stage)
        job = db.job(kind, stage, **inp)
        asyncio.run(main.process(db, job, lambda project_dir: llm, llm, video))
        return db.jobs[job["id"]]
    return db, llm, video, run


def test_stage_success_goes_to_review_and_publishes(world):
    db, llm, _, run = world
    assert run("run_stage", "analysis")["status"] == "succeeded"
    stage = db.stages["analysis"]
    assert stage["status"] == "pending_review", "o worker nunca aprova: a etapa para em revisão"
    assert stage["payload"]["summary"].startswith("Referência de 4.0 s") and "progress" not in stage["payload"]
    assert db.artifacts["doc-01"]["artifact_type"] == "document" and "## 1. Identificação" in db.artifacts["doc-01"]["content"]["markdown"]
    assert db.artifacts["doc-01"]["local_relative_path"] == "01-ficha-da-referencia.md"
    sheet = db.artifacts["ev-folha-1s-01"]
    assert sheet["artifact_type"] == "evidence" and sheet["storage_path"] == "p1/referencia/folha-1s-01.jpg"
    assert db.uploads[sheet["storage_path"]][1] == "image/jpeg"

    for stage_key in ("script", "character_bible", "character_images"):
        assert run("run_stage", stage_key)["status"] == "succeeded"
    assert db.stages["character_bible"]["payload"]["next_generation"]["count"] == 2
    image = db.artifacts["img-personagem-anna-corpo"]
    assert image["content"]["width"] == 2048 and image["content"]["prompt"].startswith("Stylized 3D")
    data, _ = db.uploads[image["storage_path"]]
    with Image.open(io.BytesIO(data)) as preview:
        assert max(preview.size) == 1600 and preview.format == "JPEG", "o painel recebe prévia leve; o original fica no PC"
    original = config.PROJECTS_DIR / "p1" / image["local_relative_path"]
    with Image.open(original) as im:
        assert im.size == (2048, 3072)


def test_blocked_stage_fails_with_action_and_resumes_without_reupload(world):
    db, llm, _, run = world
    for stage_key in ("analysis", "script", "character_bible"):
        run("run_stage", stage_key)
    llm.fail_image_at = len(llm.images) + 1
    outcome = run("run_stage", "character_images")
    stage = db.stages["character_images"]
    assert outcome["status"] == "blocked" and outcome["error_code"] == "LIMIT_REACHED"
    assert stage["status"] == "failed" and stage["payload"]["error"]["blocked"] is True
    assert "Tentar novamente" in stage["payload"]["error"]["action"]
    assert any(level == "warn" and "LIMIT_REACHED" in msg for _, level, msg in db.events)

    uploads_before = len(db.uploads)
    assert run("run_stage", "character_images", resume=True)["status"] == "succeeded"
    assert db.stages["character_images"]["status"] == "pending_review"
    assert "error" not in db.stages["character_images"]["payload"]
    assert len(db.uploads) == uploads_before + 1, "a imagem já publicada não é enviada de novo"


def test_regen_failure_keeps_stage_in_review(world):
    db, llm, _, run = world
    for stage_key in ("analysis", "script", "character_bible", "character_images"):
        run("run_stage", stage_key)
    summary = db.stages["character_images"]["payload"]["summary"]

    assert run("regen_item", "character_images", item_key="img-personagem-anna-corpo", comment="mais jovem")["status"] == "succeeded"
    assert "Requested adjustment (from the user): mais jovem" in llm.images[-1]["prompt"]
    assert db.stages["character_images"]["payload"]["summary"] == summary

    llm.fail_image_at = len(llm.images)
    outcome = run("regen_item", "character_images", item_key="img-personagem-anna-corpo")
    assert outcome["status"] == "blocked"
    stage = db.stages["character_images"]
    assert stage["status"] == "pending_review" and stage["payload"]["error"]["code"] == "LIMIT_REACHED"


def test_unexpected_error_is_recorded_not_swallowed(world, monkeypatch):
    db, llm, _, run = world

    async def boom(prompt, files=None, *, new_chat=True):
        raise RuntimeError("conexão caiu")
    monkeypatch.setattr(llm, "ask", boom)
    outcome = run("run_stage", "analysis")
    assert outcome["status"] == "failed" and outcome["error_code"] == "HANDLER_ERROR"
    assert db.stages["analysis"]["status"] == "failed"
    assert "conexão caiu" in db.stages["analysis"]["payload"]["error"]["message"]


def test_unknown_job_and_bad_source(world):
    db, _, _, run = world
    assert run("mystery", "analysis")["error_code"] == "UNKNOWN_JOB"
    db.projects["p1"]["source_url"] = "C:/nao/existe.mp4"
    outcome = run("run_stage", "analysis")
    assert outcome["status"] == "blocked" and outcome["error_code"] == "SOURCE_UNAVAILABLE"
    with pytest.raises(Blocked):
        media.fetch_source("ftp://x/y.mp4", config.PROJECTS_DIR / "z")


def test_worker_runs_up_to_three_projects_at_once_and_never_two_jobs_of_the_same_project(monkeypatch):
    from app import config, main

    class QueueDb:
        def __init__(self):
            self.queue = [{"id": f"j{i}", "project_id": p, "job_type": "run_stage", "input": {"stage_key": "analysis"}}
                          for i, p in enumerate(["p1", "p2", "p1", "p3", "p4"])]
            self.finished, self.released = [], []

        def requeue_stale(self): return 0
        def heartbeat(self, *a, **k): pass
        def claim(self): return self.queue.pop(0) if self.queue else None
        def release_job(self, job_id):
            self.released.append(job_id)
            self.queue.insert(0, next(j for j in self.all if j["id"] == job_id))

    class DummyBrowser:
        is_open = False
        async def close(self): pass

    db = QueueDb()
    db.all = list(db.queue)
    running, peak, per_project, order = set(), [0], {}, []

    async def fake_process(db_, job, brain_for, images, video, pools=None):
        running.add(job["id"])
        peak[0] = max(peak[0], len(running))
        per_project[job["project_id"]] = per_project.get(job["project_id"], 0) + 1
        assert per_project[job["project_id"]] == 1, "dois jobs do mesmo projeto ao mesmo tempo"
        order.append(("start", job["id"]))
        await asyncio.sleep(0.4)
        per_project[job["project_id"]] -= 1
        running.discard(job["id"])
        order.append(("end", job["id"]))

    monkeypatch.setattr(main, "Db", lambda: db)
    monkeypatch.setattr(main, "Browser", DummyBrowser)
    monkeypatch.setattr(main, "ChatGPT", lambda *a, **k: object())
    monkeypatch.setattr(main, "Grok", lambda *a, **k: object())
    monkeypatch.setattr(main, "process", fake_process)
    monkeypatch.setattr(config, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(config, "SUPABASE_SECRET_KEY", "sb_secret_x")
    monkeypatch.setattr(config, "MAX_PARALLEL_JOBS", 3)
    monkeypatch.setattr(config, "BRAIN", "claude")
    monkeypatch.setattr(config, "POLL_SECONDS", 0.05)

    async def scenario():
        task = asyncio.create_task(main.main())
        await asyncio.sleep(2.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())
    assert peak[0] == 3, f"pico de {peak[0]} tarefas ao mesmo tempo"
    assert [e for e in order if e[0] == "start"].__len__() == 5, "as 5 tarefas rodaram"
    assert "j2" in db.released or order.index(("start", "j2")) > order.index(("end", "j0")), "o 2º job do p1 esperou o 1º"
