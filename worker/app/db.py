"""Acesso ao Supabase com a chave secreta (somente no PC do worker) e publicação de resultados."""
from __future__ import annotations

import io
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from . import config

MAX_UPLOAD_BYTES = 45 * 1024 * 1024
PREVIEW_SIDE = 1600


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Db:
    def __init__(self, url: str | None = None, key: str | None = None):
        from supabase import create_client
        self.client = create_client(url or config.SUPABASE_URL, key or config.SUPABASE_SECRET_KEY)

    # --- worker ---------------------------------------------------------------
    def heartbeat(self, hostname: str, status: str, details: dict) -> None:
        self.client.table("worker_heartbeats").upsert({"worker_id": config.WORKER_ID, "hostname": hostname, "status": status,
                                                       "last_seen_at": now(), "details": details}).execute()

    def requeue_stale(self) -> int:
        """Tarefas que ficaram 'running' quando o worker foi interrompido voltam para a fila."""
        res = (self.client.table("jobs").update({"status": "queued", "worker_id": None, "started_at": None})
               .eq("status", "running").eq("worker_id", config.WORKER_ID).execute())
        return len(res.data or [])

    def claim(self) -> dict | None:
        rows = self.client.rpc("claim_next_job", {"p_worker_id": config.WORKER_ID}).execute().data or []
        return rows[0] if rows else None

    def finish_job(self, job_id: str, status: str, *, output: dict | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
        self.client.table("jobs").update({"status": status, "output": output or {}, "error_code": error_code,
                                          "error_message": (error_message or "")[:2000] or None, "finished_at": now()}).eq("id", job_id).execute()

    # --- projeto e etapas -----------------------------------------------------
    def project(self, project_id: str) -> dict:
        return self.client.table("projects").select("*").eq("id", project_id).single().execute().data

    def stage(self, stage_id: str) -> dict:
        return self.client.table("project_stages").select("*").eq("id", stage_id).single().execute().data

    def update_stage(self, stage_id: str, *, patch: dict | None = None, remove: tuple[str, ...] = (), **fields) -> None:
        """Atualiza colunas e mescla `patch` no payload da etapa."""
        if patch is not None or remove:
            payload = dict(self.stage(stage_id).get("payload") or {})
            for key in remove:
                payload.pop(key, None)
            payload.update(patch or {})
            fields["payload"] = payload
        if fields:
            self.client.table("project_stages").update(fields).eq("id", stage_id).execute()

    def event(self, project_id: str, stage_key: str, level: str, message: str, details: dict | None = None) -> None:
        self.client.table("events").insert({"project_id": project_id, "stage_key": stage_key or None, "level": level,
                                            "message": message[:1000], "details": details or {}}).execute()

    # --- artefatos --------------------------------------------------------------
    def upsert_artifact(self, row: dict) -> None:
        self.client.table("artifacts").upsert({**row, "updated_at": now()}, on_conflict="project_id,item_key").execute()

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.client.storage.from_(config.STORAGE_BUCKET).upload(path, data, {"content-type": content_type, "upsert": "true"})


class SupabaseSink:
    """Publica documentos, prévias e eventos de uma etapa. Os originais ficam no PC do worker."""

    def __init__(self, db: Db, project: dict, stage: dict, project_dir: Path):
        self.db, self.project, self.stage, self.dir = db, project, stage, project_dir
        self._cache_path = project_dir / "dados" / "publicados.json"
        self._cache: dict[str, str] = json.loads(self._cache_path.read_text(encoding="utf-8")) if self._cache_path.is_file() else {}

    def event(self, stage_key: str, level: str, message: str, details: dict | None = None) -> None:
        print(f"[{self.project['id'][:8]} {stage_key}] {message}", flush=True)
        try:
            self.db.event(self.project["id"], stage_key, level, message, details)
        except Exception as exc:  # noqa: BLE001 - registro não pode derrubar a etapa
            print(f"  (evento não gravado: {exc})", flush=True)

    def progress(self, stage_key: str, done: int, total: int, label: str) -> None:
        try:
            self.db.update_stage(self.stage["id"], patch={"progress": {"done": done, "total": total, "label": label}})
        except Exception as exc:  # noqa: BLE001
            print(f"  (progresso não gravado: {exc})", flush=True)

    def _row(self, item_key: str, name: str, path: Path, kind: str) -> dict:
        return {"project_id": self.project["id"], "stage_id": self.stage["id"], "item_key": item_key, "artifact_type": kind,
                "name": name, "version": self.stage["current_version"],
                "local_relative_path": path.relative_to(self.dir).as_posix() if path.is_relative_to(self.dir) else path.name}

    def document(self, stage_key: str, item_key: str, name: str, path: Path) -> None:
        self.db.upsert_artifact({**self._row(item_key, name, path, "document"), "content": {"markdown": path.read_text(encoding="utf-8")}})

    def file(self, stage_key: str, item_key: str, name: str, path: Path, kind: str, meta: dict | None = None) -> None:
        stat = path.stat()
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        if self._cache.get(item_key) == fingerprint:
            return
        row = self._row(item_key, name, path, kind)
        content = {**(meta or {}), "bytes": stat.st_size}
        rel = row["local_relative_path"]
        storage_path = None
        if kind == "video":
            if stat.st_size <= MAX_UPLOAD_BYTES:
                storage_path = f"{self.project['id']}/{rel}"
                self.db.upload(storage_path, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "video/mp4")
            else:
                content["note"] = "Arquivo grande demais para prévia remota; disponível no PC do worker."
        else:
            data, size = _preview(path)
            content.setdefault("width", size[0])
            content.setdefault("height", size[1])
            storage_path = f"{self.project['id']}/{Path(rel).with_suffix('.jpg').as_posix()}"
            self.db.upload(storage_path, data, "image/jpeg")
        self.db.upsert_artifact({**row, "storage_path": storage_path, "content": content})
        self._cache[item_key] = fingerprint
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, indent=1), encoding="utf-8")


def _preview(path: Path) -> tuple[bytes, tuple[int, int]]:
    """Prévia JPEG leve para o painel; devolve também o tamanho do original."""
    from PIL import Image
    with Image.open(path) as im:
        original = im.size
        im = im.convert("RGB")
        im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
        buffer = io.BytesIO()
        im.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), original
