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
        asyncio.run(main.process(db, job, lambda project_dir, model=None: llm, llm, video))
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
    original = config.PROJECTS_DIR / "Teste" / image["local_relative_path"]
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


def test_worker_runs_up_to_three_projects_at_once_and_never_two_jobs_of_the_same_project(monkeypatch, tmp_path):
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
    monkeypatch.setattr(config, "MEDIA_TUNNEL", "off")   # o teste não abre túnel de verdade
    monkeypatch.setattr(config, "LOCAL_MEDIA_PORT", 0)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)

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


def test_local_media_server_serves_only_project_videos_with_range(tmp_path):
    import threading, urllib.request, urllib.error
    from app import media_server
    pid = "11111111-2222-3333-4444-555555555555"
    root = tmp_path / pid
    (root / "videos").mkdir(parents=True)
    (root / "videos" / "clip.mp4").write_bytes(bytes(range(100)))
    (root / "dados.json").write_text("{}")
    server = media_server.start(tmp_path, 0)
    port = server.server_address[1]
    try:
        base = f"http://127.0.0.1:{port}/v/{pid}"
        assert urllib.request.urlopen(f"{base}/videos/clip.mp4").read() == bytes(range(100))
        part = urllib.request.urlopen(urllib.request.Request(f"{base}/videos/clip.mp4", headers={"Range": "bytes=10-19"}))
        assert part.status == 206 and part.read() == bytes(range(10, 20))
        for bad in (f"{base}/dados.json", f"{base}/../../etc/passwd", f"{base}/%2e%2e/x.mp4", f"http://127.0.0.1:{port}/v/99999999-0000/videos/clip.mp4"):
            try:
                urllib.request.urlopen(bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
    finally:
        server.shutdown()


def test_media_server_asks_for_the_studio_password_when_it_has_one(tmp_path):
    import urllib.request, urllib.error
    from app import media_server
    pid = "11111111-2222-3333-4444-555555555555"
    (tmp_path / pid / "videos").mkdir(parents=True)
    (tmp_path / pid / "videos" / "clip.mp4").write_bytes(bytes(range(50)))
    token = media_server.load_token(tmp_path / "runtime")
    assert len(token) >= 32 and media_server.load_token(tmp_path / "runtime") == token, "a senha é criada uma vez e reaproveitada"
    server = media_server.start(tmp_path, 0, token)
    base = f"http://127.0.0.1:{server.server_address[1]}/v/{pid}/videos/clip.mp4"
    try:
        for bad in (base, f"{base}?k=errada", f"{base}?k="):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(bad)
            assert exc.value.code == 403
        assert urllib.request.urlopen(f"{base}?k={token}").read() == bytes(range(50))
        part = urllib.request.urlopen(urllib.request.Request(f"{base}?k={token}", headers={"Range": "bytes=5-9"}))
        assert part.status == 206 and part.read() == bytes(range(5, 10))
    finally:
        server.shutdown()


def test_tunnel_reads_the_public_address_and_comes_back_after_a_crash(tmp_path):
    import sys, time
    from app import tunnel
    assert tunnel.parse_url("2026 INF |  https://Rapid-Fox-12.trycloudflare.com  |") == "https://rapid-fox-12.trycloudflare.com"
    assert tunnel.parse_url("Requesting new quick Tunnel on trycloudflare.com...") is None
    assert tunnel.command("cf.exe", 8765)[-2:] == ["--url", "http://127.0.0.1:8765"]

    runs = tmp_path / "runs.txt"
    script = (f"import pathlib,time; p=pathlib.Path(r'{runs}'); n=len(p.read_text().split()) if p.exists() else 0; "
              "p.write_text(p.read_text()+'x ' if p.exists() else 'x '); "
              "print(f'| https://run{n}.trycloudflare.com |', flush=True); time.sleep(0.4 if n == 0 else 30)")
    tun = tunnel.Tunnel([sys.executable, "-c", script], retry_seconds=0.2).start()
    try:
        deadline = time.time() + 15
        while time.time() < deadline and tun.url != "https://run0.trycloudflare.com":
            time.sleep(0.05)
        assert tun.url == "https://run0.trycloudflare.com"
        while time.time() < deadline and tun.url != "https://run1.trycloudflare.com":   # o processo morreu e foi reaberto
            time.sleep(0.05)
        assert tun.url == "https://run1.trycloudflare.com"
        media = tunnel.RemoteMedia("senha", "http://127.0.0.1:8765", tunnel=tun)
        assert media.snapshot() == {"local_url": "http://127.0.0.1:8765", "public_url": "https://run1.trycloudflare.com", "token": "senha"}
        assert tunnel.RemoteMedia("senha", None, "https://fixo.example.com").public_url == "https://fixo.example.com"
    finally:
        tun.stop()
    assert tun.url is None


def test_find_cloudflared_uses_the_configured_path_first(tmp_path, monkeypatch):
    from app import tunnel
    exe = tmp_path / "cloudflared.exe"
    exe.write_bytes(b"")
    assert tunnel.find_cloudflared(str(exe)) == str(exe)
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "vazio"))
    assert tunnel.find_cloudflared("") is None
