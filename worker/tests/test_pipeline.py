"""Pipeline completo com ChatGPT/Grok simulados em memória (sem navegador, sem rede)."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app import media
from app.errors import Blocked
from app.pipeline import core, render, stages
from app.pipeline.core import Ctx, LocalSink

STYLE = "Stylized 3D cartoon render, warm palette, soft key light."
BIBLE = {
    "style_block": STYLE, "style_explanation_pt": "Segue a referência.",
    "characters": [
        {"name": "Anna", "role_pt": "Protagonista", "fixed_description": "A 30-year-old woman, red coat.", "never_change_pt": ["casaco vermelho"],
         "expressions": ["happy", "shocked"], "details": ["red coat"],
         "voice": {"profile_pt": "média", "descriptor_en": "Warm mid-range female voice.", "descriptor_de": "Warme Frauenstimme.", "rules_pt": ["mesma frase"]}},
        {"name": "../../Böser Hund", "role_pt": "Antagonista", "fixed_description": "A small grumpy dog.", "expressions": [], "details": [],
         "voice": {"descriptor_en": "Raspy low voice."}},
        {"name": "Erzähler", "role_pt": "Narrador", "fixed_description": "voice only", "voice": {"descriptor_en": "Calm narrator voice."}},
    ],
}
PLAN = {
    "areas": {"lighting_mood_style_pt": "Luz quente.", "area_flow_plan_pt": "Anna à esquerda.", "camera_work_pt": "Planos médios.", "shooting_technique_pt": "Estável."},
    "environments": [{"name": "Küche", "fixed_description": "A small bright kitchen.", "explanation_pt": "Cozinha."}],
    "scenes": [
        {"id": "01", "environment": "küche", "characters": ["Anna", "Erzähler"], "composition": "Medium shot, Anna at the table.", "shot_pt": "Plano médio"},
        {"id": "02", "environment": "Küche", "characters": ["anna", "../../Böser Hund"], "composition": "Wide shot, dog at the door.", "shot_pt": "Plano aberto"},
    ],
}


def clips_payload(n=12, duration=6):
    return {"order_note_pt": "Em ordem.", "clips": [
        {"scene": "01" if i % 2 == 0 else "02", "duration_s": duration, "start": "previous_last_frame" if i == 1 else "scene_image",
         "reference_images": ["folha-anna"] if i == 1 else ["cena-01" if i % 2 == 0 else "cena-02", "folha-anna"],
         "prompt": f'Clip {i}: Anna says "Hallo".', "speakers": ["Anna"] if i % 2 == 0 else [], "dialogue_de": "Hallo", "dialogue_pt": "Olá",
         "continuity_pt": "Segue.", "check_pt": ["rosto"]} for i in range(n)]}


def as_json(obj) -> str:
    return "Claro!\n\n```json\n" + json.dumps(obj, ensure_ascii=False, indent=1) + "\n```"


class FakeLLM:
    def __init__(self, sample_image_size=(1024, 1536)):
        self.asks: list[dict] = []
        self.images: list[dict] = []
        self.json_script: dict[str, list[str]] = {}   # respostas forçadas por tarefa
        self.fail_image_at: int | None = None
        self.size = sample_image_size

    async def ask(self, prompt, files=None, *, new_chat=True):
        self.asks.append({"prompt": prompt, "files": [Path(f).name for f in files or []], "new_chat": new_chat})
        for marker, queue in self.json_script.items():
            if (marker in prompt or prompt.startswith("Não consegui usar")) and queue:
                return queue.pop(0)
        if "TAREFA: você é o ANALISTA" in prompt:
            return "# Título do modelo\n\n## 2. Resumo e ontologia\n\nResumo."
        if "TAREFA: você é o ROTEIRISTA" in prompt:
            return "## 1. Cabeçalho\n\nAnna und der Hund."
        if "TAREFA: você é o CINEGRAFISTA" in prompt:
            return as_json(BIBLE)
        if "TAREFA: você é o DESIGNER" in prompt:
            return as_json(PLAN)
        if "TAREFA: você é o EDITOR" in prompt:
            return as_json(clips_payload())
        if prompt.startswith("Agora, com as mesmas imagens"):
            return "## 1. Técnico\n\n540x960."
        if prompt.startswith("Agora escreva o Documento 09"):
            return "## 1. Parâmetros do projeto\n\n9:16."
        if prompt.startswith("Por fim, escreva o Documento 10"):
            return "## 1. Título\n\nHallo!"
        raise AssertionError("pedido inesperado: " + prompt[:80])

    async def generate_image(self, prompt, refs, out_path, *, slot=0):
        if self.fail_image_at is not None and len(self.images) == self.fail_image_at:
            self.fail_image_at = None
            raise Blocked("LIMIT_REACHED", "limite")
        for ref in refs or []:
            assert Path(ref).is_file(), f"referência ausente: {ref}"
        self.images.append({"prompt": prompt, "refs": [Path(r).name for r in refs or []], "out": out_path.name})
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", self.size, (len(self.images) * 20 % 255, 80, 120)).save(out_path)
        return {"width": self.size[0], "height": self.size[1]}


class FakeVideo:
    def __init__(self, sample: Path):
        self.sample = sample
        self.calls: list[dict] = []

    async def generate_video(self, prompt, images, out_path, *, duration_s=None, slot=0):
        images = [images] if isinstance(images, (str, Path)) else list(images or [])
        assert all(Path(i).is_file() for i in images)
        self.calls.append({"prompt": prompt, "start": Path(images[0]).name if images else None, "images": [Path(i).name for i in images],
                           "out": out_path.name, "duration_s": duration_s})
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.sample, out_path)
        return {"duration": 4.0}


@pytest.fixture
def project(tmp_path, sample_video, monkeypatch):
    monkeypatch.setattr(media, "transcribe", lambda audio, model, language: {
        "language": "de", "language_probability": 0.97, "segments": [{"start": 0.0, "end": 2.0, "text": "Hallo zusammen."}]})
    llm, video, sink = FakeLLM(), FakeVideo(sample_video), LocalSink()
    ctx = Ctx(project_id="p1", source=str(sample_video), dir=tmp_path / "p1", sink=sink, brain=llm, images=llm, video=video, max_clip=6)
    return ctx, llm, video, sink


def run(ctx: Ctx, stage: str, auto_pilot: bool = True, **changes) -> dict:
    ctx.stage_key = stage
    for key, value in {"feedback": None, **changes}.items():
        setattr(ctx, key, value)
    result = asyncio.run(stages.run_stage(ctx))
    if auto_pilot and result.get("pilot"):   # os testes comuns aprovam o clipe de teste e seguem
        ctx.pilot_approved = True
        result = asyncio.run(stages.run_stage(ctx))
    return result


def run_until(ctx: Ctx, last: str) -> None:
    for stage in stages.ORDER[: stages.ORDER.index(last) + 1]:
        run(ctx, stage)


def test_full_pipeline_produces_all_documents_images_and_clips(project):
    ctx, llm, video, sink = project
    payloads = {stage: run(ctx, stage) for stage in stages.ORDER}

    assert sorted(sink.documents) == [f"doc-{n:02d}" for n in range(1, 11)]
    doc01 = sink.documents["doc-01"]
    assert doc01.startswith("# Documento 01 · Ficha da referência\n\n## 1. Identificação")
    assert "Título do modelo" not in doc01 and "## 2. Resumo e ontologia" in doc01
    assert "| Autor | não obtido |" in doc01, "metadado ausente não pode ser inventado"
    first = llm.asks[0]
    assert first["files"][0] == "folha-gancho-01.jpg" and "folha-final-01.jpg" in first["files"] and all(f.startswith("folha-") for f in first["files"])
    assert "## 5. CTA" in first["prompt"] and "## 6. Estratégia do criador" in first["prompt"]
    assert "[0.00–2.00] Hallo zusammen." in first["prompt"]
    assert llm.asks[1]["new_chat"] is False, "o doc 02 continua a conversa que já tem as imagens"

    assert [i["out"] for i in llm.images] == [
        "personagem-anna-corpo.png", "personagem-b-ser-hund-corpo.png", "folha-anna.png", "folha-b-ser-hund.png",
        "ambiente-k-che.png", "cena-01.png", "cena-02.png"]
    assert all(i["prompt"].startswith(STYLE) for i in llm.images), "bloco de estilo idêntico em todos os prompts"
    by_out = {i["out"]: i for i in llm.images}
    assert by_out["folha-anna.png"]["refs"] == ["personagem-anna-corpo.png"]
    assert by_out["cena-02.png"]["refs"] == ["folha-anna.png", "folha-b-ser-hund.png", "ambiente-k-che.png"]
    assert by_out["cena-01.png"]["refs"] == ["folha-anna.png", "ambiente-k-che.png"], "narrador só de voz não vira referência"
    assert "A 30-year-old woman, red coat." in by_out["cena-01.png"]["prompt"]

    assert len(video.calls) == 12
    assert video.calls[0]["start"] == "cena-01.png"
    assert video.calls[1]["start"] == "inicio-clipe-02.png", "clipe encadeado parte do último quadro do anterior"
    assert "Warm mid-range female voice." in video.calls[0]["prompt"]
    assert "Voices" not in video.calls[1]["prompt"]

    for image in llm.images:
        assert ctx.dir in (ctx.dir / "imagens").rglob(image["out"]).__next__().parents
    assert not list(ctx.dir.parent.glob("*.png")), "nomes vindos do modelo não escapam da pasta do projeto"

    doc08 = sink.documents["doc-08"]
    assert "**Total:** 12 clipes · **Soma das durações:** 72 s" in doc08
    assert video.calls[0]["prompt"] in doc08, "o prompt do documento é o mesmo enviado ao gerador"
    assert by_out["cena-01.png"]["prompt"] in sink.documents["doc-07"]
    for area in ("## 1. Character Reference", "## 2. Environment", "## 3. Lighting", "## 4. Area Flow", "## 5. Camera Work",
                 "## 6. Shooting Technique", "## 7. Storyboard", "## 8. Shooting Technique & Notes"):
        assert area in sink.documents["doc-07"]
    assert "não aplicável" in sink.documents["doc-07"], "área não informada fica registrada como não aplicável"

    assert payloads["character_bible"]["next_generation"] == {"service": "ChatGPT", "count": 2, "unit": "imagens de corpo inteiro"}
    assert payloads["video_prompts"]["next_generation"]["count"] == 12
    assert payloads["production"]["warnings"], "12 clipes de 4 s reais ficam abaixo de 70 s e isso é avisado"
    assert {m["kind"] for m in sink.media.values()} == {"evidence", "image", "video"}


def test_limit_mid_batch_keeps_finished_items_and_resumes(project):
    ctx, llm, _, _ = project
    run_until(ctx, "character_bible")
    llm.fail_image_at = 1
    with pytest.raises(Blocked) as err:
        run(ctx, "character_images")
    assert err.value.code == "LIMIT_REACHED"
    assert [i["out"] for i in llm.images] == ["personagem-anna-corpo.png"]
    payload = run(ctx, "character_images")
    assert [i["out"] for i in llm.images] == ["personagem-anna-corpo.png", "personagem-b-ser-hund-corpo.png"]
    assert "1 gerada(s) agora" in payload["summary"]


def test_manually_saved_file_is_accepted_instead_of_regenerated(project):
    ctx, llm, _, sink = project
    run_until(ctx, "character_bible")
    manual = ctx.dir / "imagens" / "personagens" / "personagem-anna-corpo.png"
    manual.parent.mkdir(parents=True)
    Image.new("RGB", (800, 1200)).save(manual)
    run(ctx, "character_images")
    assert [i["out"] for i in llm.images] == ["personagem-b-ser-hund-corpo.png"]
    assert "img-personagem-anna-corpo" in sink.media


def test_stage_feedback_regenerates_with_the_adjustment(project):
    ctx, llm, _, _ = project
    run_until(ctx, "character_images")
    before = len(llm.images)
    run(ctx, "character_images", feedback="casaco mais escuro", version=2)
    again = llm.images[before:]
    assert len(again) == 2 and all("Requested adjustment (from the user): casaco mais escuro" in i["prompt"] for i in again)
    run(ctx, "character_images", feedback="casaco mais escuro", version=2)
    assert len(llm.images) == before + 2, "retomar a mesma versão não gera de novo"


def test_regen_item_touches_only_that_item(project):
    ctx, llm, _, _ = project
    run_until(ctx, "character_sheets")
    before = len(llm.images)
    ctx.stage_key = "character_sheets"
    asyncio.run(stages.regen_item(ctx, "img-folha-anna", "sem texto"))
    assert [i["out"] for i in llm.images[before:]] == ["folha-anna.png"]
    with pytest.raises(Blocked):
        asyncio.run(stages.regen_item(ctx, "img-inexistente", None))
    ctx.stage_key = "script"
    with pytest.raises(Blocked):
        asyncio.run(stages.regen_item(ctx, "doc-03", None))


def test_text_revision_sends_previous_version_and_feedback(project):
    ctx, llm, _, sink = project
    run_until(ctx, "script")
    run(ctx, "script", feedback="Troque o cachorro por um gato", version=2)
    prompt = llm.asks[-1]["prompt"]
    assert "ESTA É UMA REVISÃO" in prompt and "Troque o cachorro por um gato" in prompt and "Anna und der Hund." in prompt
    run(ctx, "character_bible", feedback="Anna mais velha", version=2)
    assert '"style_block"' in llm.asks[-1]["prompt"] and "Anna mais velha" in llm.asks[-1]["prompt"]


def test_bad_json_is_retried_once_in_the_same_chat_then_blocks(project):
    ctx, llm, _, _ = project
    run_until(ctx, "script")
    llm.json_script["você é o CINEGRAFISTA"] = ["Aqui está: {quebrado: sim,}", as_json(BIBLE)]
    run(ctx, "character_bible")
    assert llm.asks[-1]["new_chat"] is False and "JSON inválido" in llm.asks[-1]["prompt"]

    llm.json_script["você é o CINEGRAFISTA"] = ["nada", as_json({"style_block": "x", "characters": []})]
    with pytest.raises(Blocked) as err:
        run(ctx, "character_bible")
    assert err.value.code == "BAD_OUTPUT"


def test_missing_previous_stage_is_reported(project):
    ctx, _, _, _ = project
    with pytest.raises(Blocked) as err:
        run(ctx, "script")
    assert err.value.code == "MISSING_INPUT"


def test_validators_reject_inconsistent_plans():
    bible = core.validate_bible(json.loads(json.dumps(BIBLE)))
    assert [c["slug"] for c in bible["characters"]] == ["anna", "b-ser-hund", "erz-hler"]
    plan = core.validate_plan(json.loads(json.dumps(PLAN)), bible)
    assert plan["scenes"][0]["characters"] == ["Anna"]

    bad = json.loads(json.dumps(PLAN)); bad["scenes"][0]["environment"] = "Garten"
    with pytest.raises(ValueError, match="ambiente"):
        core.validate_plan(bad, bible)
    bad = json.loads(json.dumps(PLAN)); bad["scenes"][1]["characters"] = ["Fremder"]
    with pytest.raises(ValueError, match="personagem"):
        core.validate_plan(bad, bible)
    bad = json.loads(json.dumps(PLAN)); bad["scenes"][1]["id"] = "01"
    with pytest.raises(ValueError, match="repetido"):
        core.validate_plan(bad, bible)

    with pytest.raises(ValueError, match="acima de 70"):
        core.validate_clips(clips_payload(n=11), bible, plan, 6)
    with pytest.raises(ValueError, match="fora de 1–6"):
        core.validate_clips(clips_payload(duration=8), bible, plan, 6)
    wrong = clips_payload(); wrong["clips"][0]["scene"] = "99"
    with pytest.raises(ValueError, match="imagem-base"):
        core.validate_clips(wrong, bible, plan, 6)
    ok = core.validate_clips(clips_payload(), bible, plan, 6)
    assert ok["total_s"] == 72 and ok["clips"][0]["start"] == "scene_image" and ok["clips"][1]["start"] == "previous_last_frame"


def test_extract_json_variants():
    assert core.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert core.extract_json('texto {"a": {"b": 2}} fim') == {"a": {"b": 2}}
    assert core.extract_json('```text\nnada\n```\n\n````json\n{"a": "```"}\n````') == {"a": "```"}
    with pytest.raises(ValueError):
        core.extract_json("sem json")
    with pytest.raises(ValueError):
        core.extract_json("```json\n[1, 2]\n```")


def test_identification_table_uses_only_downloaded_metadata(tmp_path):
    meta = tmp_path / "referencia_tikwm.json"
    meta.write_text(json.dumps({"data": {"title": "Wow #fyp #lustig", "author": {"unique_id": "anna"}, "create_time": 1700000000,
                                         "play_count": 10, "music_info": {"title": "Song", "author": "Band"}}}), encoding="utf-8")
    m = media.read_metadata(meta)
    assert m["hashtags"] == ["#fyp", "#lustig"] and m["author"] == "anna" and "likes" not in m
    assert m["posted_berlin"] == "14/11/2023 23:13" and m["posted_brasilia"] == "14/11/2023 19:13"
    table = render.identification({"source": "https://x", "metadata": m, "probe": {"duration": 80.0, "width": 720, "height": 1280, "fps": 30.0,
                                                                                    "video_codec": "h264", "audio_codec": "aac"}})
    assert "| Hashtags originais | #fyp #lustig |" in table and "visualizações: 10" in table and "curtidas" not in table
    assert "| Som (metadados) | Song — Band |" in table


IMPORTED = {
    "character_images": [{"name": "Anna", "prompt": "Full body of Anna, red coat."}, {"name": "Böser Hund", "prompt": "Full body of a grumpy dog."}],
    "scene_images": [{"name": "Küche", "prompt": "Empty bright kitchen."},
                     {"name": "Cena 1", "prompt": "Anna at the table.", "refs": ["anna", "Küche"]}],
    "production": [{"name": "Abertura", "prompt": "Anna says Hallo.", "start": "Cena 1"},
                   {"name": "Continuação", "prompt": "Anna smiles.", "start": "anterior"}],
}


def test_imported_prompts_run_the_generation_stages_without_text_stages(project):
    ctx, llm, video, sink = project
    ctx.plan = IMPORTED
    first = run(ctx, "character_images")
    assert [i["prompt"] for i in llm.images] == ["Full body of Anna, red coat.", "Full body of a grumpy dog."]
    assert first["next_generation"] == {"service": "ChatGPT", "count": 2, "unit": "imagens de ambiente e cena"}, "pula folhas (sem itens)"
    scenes = run(ctx, "scene_images")
    assert llm.images[3]["refs"] == ["anna.png", "k-che.png"], "referências resolvidas por nome, sem diferenciar maiúsculas"
    assert scenes["next_generation"]["service"] == "Grok" and scenes["next_generation"]["count"] == 2
    done = run(ctx, "production")
    assert [c["start"] for c in video.calls] == ["cena-1.png", "inicio-02.png"]
    assert done["warnings"] == [], "sem exigência de 70 s quando os prompts são do usuário"
    assert not llm.asks, "nenhuma chamada ao Claude: os prompts já são do usuário"


def test_imported_reference_to_unknown_or_missing_image_is_reported(project):
    ctx, llm, video, sink = project
    ctx.plan = {"scene_images": [{"name": "Cena", "prompt": "x", "refs": ["Fantasma"]}]}
    with pytest.raises(Blocked, match="Fantasma"):
        run(ctx, "scene_images")
    ctx.plan = {"character_images": [{"name": "Anna", "prompt": "a"}], "scene_images": [{"name": "Cena", "prompt": "x", "refs": ["Anna"]}]}
    with pytest.raises(Blocked, match="ainda não foi gerada"):
        run(ctx, "scene_images")


def test_imported_item_can_be_regenerated_alone(project):
    ctx, llm, video, sink = project
    ctx.plan = IMPORTED
    run(ctx, "character_images")
    ctx.stage_key = "character_images"
    before = len(llm.images)
    asyncio.run(stages.regen_item(ctx, "imp-character_images-anna", "mais escuro"))
    assert len(llm.images) == before + 1 and "mais escuro" in llm.images[-1]["prompt"]


def test_imported_clip_duration_comes_from_the_field_or_the_prompt(project):
    ctx, llm, video, sink = project
    ctx.max_clip = 15
    ctx.plan = {"production": [{"name": "A", "prompt": "Anna waves.", "duration_s": 10}, {"name": "B", "prompt": "Anna talks for 12 seconds."},
                               {"name": "C", "prompt": "Anna smiles."}]}
    run(ctx, "production")
    assert [c["duration_s"] for c in video.calls] == [10, 15, 6]


def test_each_clip_attaches_exactly_the_images_claude_listed(project):
    ctx, llm, video, sink = project
    for stage in stages.ORDER:
        run(ctx, stage)
    clips = ctx.load_data("clips")["clips"]
    assert len(video.calls) == len(clips) == 12
    for clip, call in zip(clips, video.calls):
        expected = [Path(p).name for p in [render.image_catalog(ctx.load_data("bible"), ctx.load_data("plan"))[r]["path"] for r in clip["reference_images"]]]
        if clip["start"] == "previous_last_frame":
            expected.insert(0, "inicio-clipe-%02d.png" % clip["n"])
        assert call["images"] == expected, f"clipe {clip['n']}"
        assert call["duration_s"] == clip["duration_s"]
        assert call["prompt"].startswith(clip["prompt"])
    assert [c["out"] for c in video.calls] == [f"clipe-{n:02d}.mp4" for n in range(1, 13)]
    assert "folha-anna.png" in video.calls[2]["images"] and len(video.calls[2]["images"]) == 2


def test_clip_reference_ids_must_exist_and_be_listed():
    bible = {"characters": [{"name": "Anna", "slug": "anna", "voice_only": False}]}
    plan = {"scenes": [{"id": "01"}]}
    base = {"scene": "01", "duration_s": 6, "start": "scene_image", "prompt": "p", "speakers": []}
    catalog = {"cena-01", "folha-anna"}
    def run_validate(refs, n=13):
        return core.validate_clips({"clips": [{**base, "reference_images": refs} for _ in range(n)]}, bible, plan, 6, catalog)
    assert run_validate(["cena-01"])["clips"][0]["reference_images"] == ["cena-01"]
    with pytest.raises(ValueError, match="não existem"):
        run_validate(["cena-99"])
    with pytest.raises(ValueError, match="informe em"):
        run_validate([])


def test_video_stage_generates_only_the_pilot_clip_until_approved(project):
    ctx, llm, video, sink = project
    for stage in stages.ORDER[:-1]:
        run(ctx, stage)
    first = run(ctx, "production", auto_pilot=False)
    assert [c["out"] for c in video.calls] == ["clipe-01.mp4"], "só o clipe de teste"
    assert first["pilot"] is True and first["pilot_remaining"] == 11
    # pedir ajustes no piloto refaz só o primeiro clipe, com o comentário
    again = run(ctx, "production", auto_pilot=False, feedback="rosto mais claro", version=2)
    assert [c["out"] for c in video.calls] == ["clipe-01.mp4", "clipe-01.mp4"] and again["pilot"] is True
    # aprovado o piloto: gera os 11 restantes e mantém o primeiro
    rest = run(ctx, "production", feedback=None, version=2, pilot_approved=True)
    assert [c["out"] for c in video.calls][2:] == [f"clipe-{n:02d}.mp4" for n in range(2, 13)]
    assert "pilot" not in rest and "11 gerado(s) agora" in rest["summary"]


class SlowLLM(FakeLLM):
    """Mede quantas imagens estão em andamento ao mesmo tempo e em que aba (slot) cada uma rodou."""
    def __init__(self):
        super().__init__()
        self.active = self.peak = 0
        self.slots: list[int] = []
        self.started: list[str] = []
        self.finished: list[str] = []

    async def generate_image(self, prompt, refs, out_path, *, slot=0):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.slots.append(slot)
        self.started.append(out_path.name)
        await asyncio.sleep(0.15)
        try:
            return await super().generate_image(prompt, refs, out_path, slot=slot)
        finally:
            self.active -= 1
            self.finished.append(out_path.name)


def test_images_of_a_batch_run_three_at_a_time_in_separate_tabs(project):
    ctx, llm, video, sink = project
    slow = SlowLLM()
    ctx.images = slow
    for stage in ("analysis", "script", "character_bible"):
        run(ctx, stage)
    ctx.image_concurrency = 3
    run(ctx, "character_images")
    assert 2 <= slow.peak <= 3 and set(slow.slots) <= {0, 1, 2}


def test_dependent_items_wait_for_their_wave_even_with_three_tabs(project):
    ctx, llm, video, sink = project
    slow = SlowLLM()
    ctx.images = slow
    for stage in ("analysis", "script", "character_bible", "character_images", "character_sheets", "visual_plan"):
        run(ctx, stage)
    ctx.image_concurrency = 3
    run(ctx, "scene_images")
    names = slow.started
    env_done = slow.finished.index("ambiente-k-che.png")
    first_scene_start = min(names.index(n) for n in names if n.startswith("cena-"))
    assert first_scene_start >= 1 and env_done < len(slow.finished), "ambientes terminam antes de as cenas dependentes começarem"
    assert slow.started.index("ambiente-k-che.png") < first_scene_start


def test_one_failure_stops_new_items_but_keeps_what_finished(project):
    ctx, llm, video, sink = project
    for stage in ("analysis", "script", "character_bible"):
        run(ctx, stage)
    ctx.image_concurrency = 3
    llm.fail_image_at = 1
    with pytest.raises(Blocked) as err:
        run(ctx, "character_images")
    assert err.value.code == "LIMIT_REACHED"
    done = len(llm.images)
    run(ctx, "character_images")   # retoma: o que já saiu é mantido, só falta o que não saiu
    assert len({i["out"] for i in llm.images}) == 2 and len(llm.images) <= done + 2


def test_clone_mode_asks_for_a_faithful_copy_with_shot_map_swap_proposal_and_no_70s_rule(project):
    ctx, llm, video, sink = project
    ctx.clone = True
    run(ctx, "analysis")
    assert "REMAKE FIEL" in llm.asks[0]["prompt"] and "Mapa plano a plano" in llm.asks[0]["prompt"]
    run(ctx, "script")
    script = next(a["prompt"] for a in llm.asks if "ROTEIRISTA do REMAKE FIEL" in a["prompt"])
    assert "Proposta de troca de blocos" in script and "manter 100% fiel" in script and "Câmera idêntica ao original" in script
    assert "Teste de originalidade" not in script
    result = run(ctx, "script", feedback="aplicar a troca", version=2)
    revised = llm.asks[-1]["prompt"]
    assert "aplicar a troca" in revised and "APLICADO" in revised and "troca de blocos" in result["summary"] or "troca" in result["summary"]
    run(ctx, "character_bible")
    assert "cópia fiel" in llm.asks[-1]["prompt"]
    run(ctx, "character_images"); run(ctx, "character_sheets"); run(ctx, "visual_plan")
    assert "Uma cena por plano do original" in llm.asks[-1]["prompt"]
    run(ctx, "scene_images")
    out = run(ctx, "video_prompts")   # clipes curtos: a regra de 70 s não vale na cópia fiel
    assert any("copie no prompt de cada clipe o movimento de câmera" in a["prompt"] for a in llm.asks)
    assert out is not None


def test_inspired_mode_is_unchanged(project):
    ctx, llm, _, _ = project
    run(ctx, "analysis"); run(ctx, "script")
    assert not any("REMAKE FIEL" in a["prompt"] for a in llm.asks)
    assert any("Teste de originalidade" in a["prompt"] for a in llm.asks)


def test_failed_item_is_reported_and_an_edited_prompt_is_used_on_retry(project):
    ctx, llm, _, _ = project
    run_until(ctx, "character_bible")
    llm.fail_image_at = 1
    with pytest.raises(Blocked) as err:
        run(ctx, "character_images")
    item = err.value.as_dict()["item"]
    assert item["key"] == "img-personagem-b-ser-hund-corpo" and item["prompt"] and item["name"]
    ctx.overrides = {item["key"]: "Um cachorro simpático, versão suavizada."}
    run(ctx, "character_images")
    assert llm.images[-1]["prompt"].endswith("Um cachorro simpático, versão suavizada.") or "versão suavizada" in llm.images[-1]["prompt"]
    assert [i["out"] for i in llm.images].count("personagem-anna-corpo.png") == 1   # o que já estava pronto não é refeito


def test_force_remakes_an_item_that_already_exists_even_when_the_stage_is_resumed(project):
    ctx, llm, _, _ = project
    run_until(ctx, "character_images")
    before = len(llm.images)
    run(ctx, "character_images")
    assert len(llm.images) == before   # nada a refazer
    ctx.force = {"img-personagem-anna-corpo"}
    ctx.overrides = {"img-personagem-anna-corpo": "Anna com casaco azul."}
    run(ctx, "character_images")
    assert len(llm.images) == before + 1 and "casaco azul" in llm.images[-1]["prompt"]


def _finished_parent(project, tmp_path):
    """Produz o roteiro e os personagens da parte 1 e devolve (pasta dos projetos, id do pai)."""
    ctx, _, _, _ = project
    run(ctx, "analysis"); run(ctx, "script"); run(ctx, "character_bible")
    return ctx.dir.parent, ctx.project_id


def test_sequel_context_copies_the_story_and_characters_and_survives_deleting_the_parent(project, tmp_path):
    projects_dir, parent_id = _finished_parent(project, tmp_path)
    child = {"id": "p2", "parent_id": parent_id, "part": 2, "sequel_notes": "Anna encontra um dragão", "source_url": None}
    sq = core.sequel_context(child, projects_dir)
    assert sq["part"] == 2 and not sq["has_ref"] and "Anna und der Hund" in sq["story"] and sq["notes"] == "Anna encontra um dragão"
    assert [c["name"] for c in sq["characters"]] and sq["doc02"]
    shutil.rmtree(projects_dir / parent_id)
    assert core.sequel_context(child, projects_dir)["story"] == sq["story"]   # a cópia local basta


def test_sequel_without_the_parent_script_on_this_pc_is_blocked_with_a_clear_message(tmp_path):
    with pytest.raises(Blocked) as err:
        core.sequel_context({"id": "p2", "parent_id": "nao-existe", "part": 2}, tmp_path)
    assert err.value.code == "MISSING_INPUT" and "parte anterior" in err.value.message


def test_sequel_without_a_link_writes_a_continuation_brief_and_inherits_editing_style(project, tmp_path):
    projects_dir, parent_id = _finished_parent(project, tmp_path)
    ctx, llm, _, _ = project
    ctx.project_id, ctx.dir = "p2", projects_dir / "p2"
    ctx.sequel = core.sequel_context({"id": "p2", "parent_id": parent_id, "part": 2, "sequel_notes": "um dragão aparece", "source_url": None}, projects_dir)
    ctx.source = ""
    out = run(ctx, "analysis")
    brief = llm.asks[-1]["prompt"]
    assert "PARTE 2" in brief and "um dragão aparece" in brief and "Anna und der Hund" in brief and "Três direções" in brief
    assert ctx.read_doc("01") and "Herdado da parte anterior" in ctx.read_doc("02") and "parte 2" in out["summary"]
    run(ctx, "script")
    script = llm.asks[-1]["prompt"]
    assert "CONTINUAÇÃO · PARTE 2" in script and "Não há vídeo de referência" in script and "um dragão aparece" in script
    run(ctx, "character_bible")
    assert "Personagens da parte anterior" in llm.asks[-1]["prompt"] and "fixed_description" in llm.asks[-1]["prompt"]


def test_sequel_with_a_link_analyzes_the_new_video_and_continues_the_story(project, tmp_path):
    projects_dir, parent_id = _finished_parent(project, tmp_path)
    ctx, llm, _, _ = project
    ctx.project_id, ctx.dir = "p2", projects_dir / "p2"
    ctx.sequel = core.sequel_context({"id": "p2", "parent_id": parent_id, "part": 2, "source_url": "https://tiktok.com/x"}, projects_dir)
    run(ctx, "analysis")   # analisa o vídeo (aqui o MP4 de teste), sem o resumo sem referência
    assert not any("Não há vídeo de referência" in a["prompt"] for a in llm.asks[-2:]) and llm.asks[-2]["files"]
    run(ctx, "script")
    script = llm.asks[-1]["prompt"]
    assert "CONTINUAÇÃO · PARTE 2" in script and "PARTE 2 do original" in script and "Anna und der Hund" in script
