"""Resgate: projetos cujas pastas existem no PC voltam ao banco novo com o mesmo id, etapas deduzidas e arquivos publicados."""
from __future__ import annotations

import shutil

import pytest

from app import recover
from app.pipeline import stages
from tests.test_pipeline import project, run  # noqa: F401 - fixture

PID = "8a2f3c10-5b7e-4d21-9c3a-0e1f2a3b4c5d"


class FakeDb:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {"projects": [], "project_stages": []}
        self.artifacts: dict[tuple, dict] = {}
        self.uploads: dict[str, tuple] = {}
        self.events: list[tuple] = []

    def exists(self, table, row_id):
        return any(r["id"] == row_id for r in self.rows[table])

    def insert_rows(self, table, rows):
        out = []
        for row in rows:
            row = dict(row)
            row.setdefault("id", f"s-{len(self.rows[table])}")
            self.rows[table].append(row)
            out.append(row)
        return out

    def delete_project(self, project_id):
        self.rows["projects"] = [r for r in self.rows["projects"] if r["id"] != project_id]
        self.rows["project_stages"] = [r for r in self.rows["project_stages"] if r["project_id"] != project_id]
        self.artifacts = {k: v for k, v in self.artifacts.items() if k[0] != project_id}

    def upsert_artifact(self, row):
        self.artifacts[(row["project_id"], row["item_key"])] = row

    def upload(self, path, data, content_type):
        self.uploads[path] = (data, content_type)

    def event(self, project_id, stage_key, level, message, details=None):
        self.events.append((project_id, stage_key, message))


@pytest.fixture
def saved(project, tmp_path):  # noqa: F811
    """Um projeto completo (todas as etapas) copiado para uma pasta com o nome do projeto e o marcador do id antigo."""
    ctx, llm, video, sink = project
    for stage in stages.ORDER:
        run(ctx, stage)
    projects = tmp_path / "projects"
    folder = projects / "Minha história"
    shutil.copytree(ctx.dir, folder)
    (folder / ".projeto-id").write_text(PID, encoding="utf-8")
    return projects, folder, sink


def by_key(plan):
    return {s.key: s for s in plan.stages}


def test_a_finished_folder_comes_back_with_the_same_id_and_every_stage_inferred(saved):
    projects, folder, sink = saved
    plans, skipped = recover.scan(projects)
    assert skipped == [] and [p.id for p in plans] == [PID]
    plan = plans[0]
    assert plan.title == "Minha história" and plan.mode == "inspired" and plan.old_layout
    stg = by_key(plan)
    assert [s.status for s in plan.stages] == ["approved"] * 8 + ["pending_review"], "tudo antes do fim é aprovado; a última etapa completa espera você"
    assert plan.current_stage == "production"
    assert len(stg["production"].pieces) == stg["production"].expected == 12
    assert stg["character_images"].order == 4 and stg["character_sheets"].order == 5
    assert stg["analysis"].docs == ["01", "02"] and stg["video_prompts"].docs == ["08", "09", "10"]
    published = {p.key for s in plan.stages for p in s.pieces}
    assert published == set(sink.media), "as chaves dos itens são as mesmas que o worker publica"
    assert stg["production"].payload["pilot_approved"] is True


def test_partial_generation_stops_with_retry_and_a_single_clip_is_the_pilot(saved):
    projects, folder, _ = saved
    for n in range(6, 13):
        (folder / "videos" / f"clipe-{n:02d}.mp4").unlink()
    production = by_key(recover.scan(projects)[0][0])["production"]
    assert production.status == "failed" and production.payload["error"]["code"] == "RECOVERED_PARTIAL"
    assert "5 de 12" in production.payload["error"]["message"] and production.payload["pilot_approved"] is True

    for n in range(2, 6):
        (folder / "videos" / f"clipe-{n:02d}.mp4").unlink()
    production = by_key(recover.scan(projects)[0][0])["production"]
    assert production.status == "pending_review" and production.payload["pilot"] is True and production.payload["pilot_remaining"] == 11


def test_stages_without_results_stay_locked_and_the_last_one_with_results_is_the_current_stage(saved):
    projects, folder, _ = saved
    shutil.rmtree(folder / "videos")
    for name in ("08-prompts-grok.md", "09-plano-edicao-capcut.md", "10-kit-publicacao.md"):
        (folder / name).unlink()
    plan = recover.scan(projects)[0][0]
    assert [s.status for s in plan.stages][-3:] == ["pending_review", "locked", "locked"] or plan.current_stage == "scene_images"
    assert by_key(plan)["scene_images"].status == "pending_review" and by_key(plan)["production"].status == "locked"


def test_apply_writes_project_stages_documents_and_previews_then_skips_what_exists(saved):
    projects, folder, sink = saved
    (folder / "dados" / "publicados.json").write_text('{"img-cena-01": "1:1"}', encoding="utf-8")   # cache do banco antigo
    plan = recover.scan(projects)[0][0]
    db = FakeDb()
    result = recover.apply_project(db, plan, "owner-1", log=lambda *_: None)
    assert result == {"skipped": False, "failed": []}

    project_row = db.rows["projects"][0]
    assert project_row["id"] == PID and project_row["owner_id"] == "owner-1" and project_row["mode"] == "inspired"
    assert project_row["current_stage"] == "production" and project_row["status"] == "active"
    assert [r["stage_key"] for r in db.rows["project_stages"]] == [s.key for s in plan.stages]
    assert [r["status"] for r in db.rows["project_stages"]][-1] == "pending_review"
    keys = {k[1] for k in db.artifacts}
    assert {f"doc-{n}" for n in ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")} <= keys
    assert set(sink.media) <= keys, "cada imagem, vídeo e quadro de referência foi publicado"
    assert all(row["project_id"] == PID for row in db.artifacts.values())
    assert db.artifacts[(PID, "doc-03")]["content"]["markdown"].startswith("# Documento 03")
    video = db.artifacts[(PID, "vid-clipe-01")]
    assert video["artifact_type"] == "video" and video["content"]["local"] is True and video["storage_path"] is None
    image = db.artifacts[(PID, "img-cena-01")]
    assert image["storage_path"] == f"{PID}/imagens/cenas/cena-01.jpg" and image["storage_path"] in db.uploads, "a prévia foi enviada ao storage"
    assert (folder / "dados" / "publicados.antigo.json").is_file(), "o cache do banco antigo não vale no banco novo, mas é guardado"
    assert (folder / ".projeto-id").read_text(encoding="utf-8") == PID
    assert db.events and "resgatado" in db.events[0][2]

    assert recover.apply_project(db, plan, "owner-1", log=lambda *_: None) == {"skipped": True}
    assert len(db.rows["projects"]) == 1
    again = recover.apply_project(db, plan, "owner-1", replace=True, log=lambda *_: None)
    assert again["skipped"] is False and len(db.rows["projects"]) == 1 and len(db.rows["project_stages"]) == 9


def test_legacy_id_folders_empty_folders_and_repeated_ids(saved, tmp_path):
    projects, folder, _ = saved
    legacy = projects / "11111111-2222-4333-8444-555555555555"
    shutil.copytree(folder, legacy)
    (legacy / ".projeto-id").unlink()                  # pasta antiga: o id é o próprio nome
    (projects / "Vazia").mkdir()
    (projects / "Copia").mkdir()
    shutil.copytree(folder, projects / "Copia", dirs_exist_ok=True)   # outra pasta com o mesmo marcador
    plans, skipped = recover.scan(projects)
    assert sorted(p.id for p in plans) == sorted([PID, "11111111-2222-4333-8444-555555555555"])
    assert any("Vazia" in s for s in skipped) and any("repetido" in s for s in skipped)
    assert next(p for p in plans if p.id.startswith("11111111")).title.startswith("Recuperado")


def test_sequel_is_linked_to_its_parent_by_title_and_story_folders_get_a_briefing(saved):
    projects, folder, _ = saved
    child = projects / "Minha história · Parte 2"
    shutil.copytree(folder, child)
    (child / ".projeto-id").write_text("22222222-3333-4444-8555-666666666666", encoding="utf-8")
    (child / "historia-anterior.md").write_text("# PARTE 1\n\ntexto", encoding="utf-8")
    plans, _ = recover.scan(projects)
    parent, sequel = plans
    assert parent.part == 1 and sequel.part == 2 and sequel.parent_id == parent.id, "a parte anterior é inserida antes (ordem por parte)"

    db = FakeDb()
    recover.apply_project(db, parent, "o", log=lambda *_: None)
    recover.apply_project(db, sequel, "o", log=lambda *_: None)
    assert db.rows["projects"][1]["parent_id"] == parent.id and db.rows["projects"][1]["part"] == 2

    only_child = FakeDb()   # com --only o anterior pode não estar no banco: o vínculo é largado em vez de quebrar
    recover.apply_project(only_child, sequel, "o", log=lambda *_: None)
    assert only_child.rows["projects"][0]["parent_id"] is None

    ref = folder / "referencia"
    shutil.rmtree(ref)
    (folder / "03-roteiro-de.md").unlink()
    for name in ("04-personagens.md", "05-folhas-dos-personagens.md", "06-vozes.md", "07-ambientes-e-cenas.md", "08-prompts-grok.md", "09-plano-edicao-capcut.md", "10-kit-publicacao.md"):
        (folder / name).unlink()
    for sub in ("imagens", "videos", "dados"):
        shutil.rmtree(folder / sub)
    story = next(p for p in recover.scan(projects)[0] if p.id == PID)
    assert story.mode == "story" and story.story_idea and 15 <= story.target_seconds <= 300 and 1 <= story.min_scenes <= 60
    assert [s.status for s in story.stages][:3] == ["pending_review", "locked", "locked"] and story.current_stage == "analysis"
