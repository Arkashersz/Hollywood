"""Acesso ao Supabase com a chave secreta (somente no PC do worker) e publicação de resultados."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

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

    def publish_media(self, media: dict) -> None:
        """Endereço e senha do servidor de vídeos do PC (só o dono do estúdio lê; os vídeos em si não saem do PC)."""
        self.client.table("worker_media").upsert({"worker_id": config.WORKER_ID, **media, "updated_at": now()}).execute()

    def requeue_stale(self) -> int:
        """Tarefas que ficaram 'running' quando o worker foi interrompido voltam para a fila."""
        res = (self.client.table("jobs").update({"status": "queued", "worker_id": None, "started_at": None})
               .eq("status", "running").eq("worker_id", config.WORKER_ID).execute())
        return len(res.data or [])

    def claim(self) -> dict | None:
        rows = self.client.rpc("claim_next_job", {"p_worker_id": config.WORKER_ID}).execute().data or []
        return rows[0] if rows else None

    def claim_regen_jobs(self, stage_id: str) -> list[dict]:
        """Ajustes novos pedidos enquanto a etapa já está sendo ajustada: entram na mesma execução."""
        res = (self.client.table("jobs").update({"status": "running", "worker_id": config.WORKER_ID, "started_at": now()})
               .eq("stage_id", stage_id).eq("job_type", "regen_item").eq("status", "queued").execute())
        return sorted(res.data or [], key=lambda job: str(job.get("created_at") or ""))

    def release_job(self, job_id: str) -> None:
        """Devolve uma tarefa reivindicada à fila (ex.: o mesmo projeto já está sendo processado)."""
        self.client.table("jobs").update({"status": "queued", "worker_id": None, "started_at": None}).eq("id", job_id).execute()

    def finish_job(self, job_id: str, status: str, *, output: dict | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
        self.client.table("jobs").update({"status": status, "output": output or {}, "error_code": error_code,
                                          "error_message": (error_message or "")[:2000] or None, "finished_at": now()}).eq("id", job_id).execute()

    # --- projeto e etapas -----------------------------------------------------
    def project(self, project_id: str) -> dict:
        return self.client.table("projects").select("*").eq("id", project_id).single().execute().data

    def stage_keys(self, project_id: str) -> set[str]:
        rows = self.client.table("project_stages").select("stage_key").eq("project_id", project_id).execute().data or []
        return {r["stage_key"] for r in rows}

    def stage(self, stage_id: str) -> dict:
        return self.client.table("project_stages").select("*").eq("id", stage_id).single().execute().data

    def stage_payload(self, project_id: str, stage_key: str) -> dict:
        rows = self.client.table("project_stages").select("payload").eq("project_id", project_id).eq("stage_key", stage_key).limit(1).execute().data or []
        return (rows[0].get("payload") if rows else None) or {}

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

    def prune_artifacts(self, stage_id: str, keep: set[str]) -> list[str]:
        """Remove do painel as imagens/vídeos da etapa que não existem mais no plano atual (documentos nunca são removidos)."""
        rows = self.client.table("artifacts").select("item_key,artifact_type").eq("stage_id", stage_id).in_("artifact_type", ["image", "video"]).execute().data or []
        gone = [r["item_key"] for r in rows if r["item_key"] not in keep]
        for key in gone:
            self.client.table("artifacts").delete().eq("stage_id", stage_id).eq("item_key", key).execute()
        return gone

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.client.storage.from_(config.STORAGE_BUCKET).upload(path, data, {"content-type": content_type, "upsert": "true"})

    # --- resgate de projetos (app.recover) ----------------------------------------
    def exists(self, table: str, row_id: str) -> bool:
        return bool(self.client.table(table).select("id").eq("id", row_id).limit(1).execute().data)

    def insert_rows(self, table: str, rows: list[dict]) -> list[dict]:
        return self.client.table(table).insert(rows).execute().data or []

    def delete_project(self, project_id: str) -> None:
        self.client.table("projects").delete().eq("id", project_id).execute()

    def studio_owner(self, who: str | None = None) -> str:
        """Dono do estúdio. Sem dono ainda (banco novo), usa a única conta do painel, ou a indicada em `who` (e-mail ou id)."""
        rows = self.client.table("studio_owner").select("user_id").execute().data or []
        if rows:
            return rows[0]["user_id"]
        users = self.client.auth.admin.list_users()
        users = users if isinstance(users, list) else getattr(users, "users", [])
        if who:
            users = [u for u in users if who.casefold() in (str(u.id).casefold(), str(getattr(u, "email", "") or "").casefold())]
        if len(users) != 1:
            raise SystemExit(f"Encontrei {len(users)} contas no painel; indique a sua com --owner <e-mail ou id>." if users else "Nenhuma conta no painel: entre nele uma vez e rode de novo.")
        self.client.table("studio_owner").insert({"user_id": str(users[0].id)}).execute()
        return str(users[0].id)


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

    def prune(self, stage_key: str, keep: set[str]) -> list[str]:
        gone = self.db.prune_artifacts(self.stage["id"], keep)
        for key in gone:
            self._cache.pop(key, None)
        if gone:
            self._cache_path.write_text(json.dumps(self._cache, indent=1), encoding="utf-8")
        return gone

    def file(self, stage_key: str, item_key: str, name: str, path: Path, kind: str, meta: dict | None = None) -> None:
        stat = path.stat()
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}" + (":local" if kind == "video" else "")
        if self._cache.get(item_key) == fingerprint:
            return
        row = self._row(item_key, name, path, kind)
        content = {**(meta or {}), "bytes": stat.st_size}
        rel = row["local_relative_path"]
        storage_path = None
        if kind == "video":
            content["local"] = True   # o vídeo fica só na pasta do projeto; o painel o toca pelo servidor local do worker
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
