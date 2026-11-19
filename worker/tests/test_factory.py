"""Fábrica de roteiros + workflow de animação (modo "factory"), com Claude, ChatGPT e Grok simulados."""
from __future__ import annotations

import asyncio
import copy

import pytest

from app import config, main
from app.errors import Blocked
from app.pipeline import factory, render
from test_pipeline import FakeLLM, FakeVideo, as_json, project, run  # noqa: F401 - `project` é uma fixture
from test_worker import FakeDb

FRUIT_BODY = ("Fruit-headed character: the head is a literal {fruit} with real peel texture and an expressive cartoon face; human-proportioned body with cartoon arms and legs; "
              "body skin tone matches the {fruit} color. Outfit: {outfit}.")


def proposals_payload():
    archs = ["mistério progressivo", "início pelo clímax", "progressão cronológica", "perspectivas paralelas", "dilema moral"]
    return {"proposals": [
        {"title": f"História {i}", "hook": f"Gancho {i}", "synopsis": f"Sinopse {i}", "protagonist": "Banana", "supporting": ["Morango"], "conflict": f"Conflito {i}",
         "motivation": f"Motivação {i}", "obstacles": [f"Obstáculo {i}"], "twist": f"Reviravolta {i}", "ending": f"Final {i}", "dominant_emotion": "tensão",
         "architecture": archs[i - 1], "setting": "Cozinha", "continuation": "Parte 2"} for i in range(1, 6)],
        "comparison_pt": "Comparei as cinco entre si e reescrevi a 4.", "recommended": 2, "recommended_why_pt": "Mais forte."}


def script_payload(n=12, duration=8):
    scenes = []
    for i in range(1, n + 1):
        scenes.append({"n": i, "title": f"Cena {i}", "duration_s": duration, "location": "Cozinha" if i < 7 else "Jardim", "characters": ["Banana", "Morango"],
                       "event": f"Acontece {i}", "action": f"Banana age {i}", "emotion": "surpresa", "lines": [{"speaker": "Banana" if i % 2 else "Morango", "text": f"Eu não acredito que isso aconteceu {i}!"}],
                       "framing": "Plano médio", "function": "Escalada", "transition": "Corte seco"})
    return {"title": "A Banana e o Segredo", "logline_pt": "Uma banana descobre um segredo.", "tone_pt": "tenso", "audio_pt": "diálogos", "hook_pt": "Começa no meio da briga.",
            "climax_pt": "O segredo vem à tona.", "ending_pt": "Uma porta se abre.", "originality_pt": "Acontecimentos novos.", "scenes": scenes}


def bible_payload(n=12):
    def char(name, fruit, gender, colors):
        return {"name": name, "role_pt": "personagem", "gender_pt": gender, "kind_pt": fruit, "apparent_age_pt": "adulta", "personality_pt": "curiosa", "relations_pt": "amigos",
                "anatomy_pt": f"cabeça de {fruit}", "outfit": [{"piece": "avental", "fit": "solto", "material": "algodão", "colors": colors}],
                "accessories_pt": ["laço"], "allowed_changes_pt": [], "never_change_pt": [f"sempre de avental {colors}"],
                "fixed_description": FRUIT_BODY.format(fruit=fruit, outfit=f"{colors} apron"), "expressions": ["happy", "shocked"], "details": ["apron"],
                "voice": {"profile_pt": "média", "descriptor_en": f"{name} has a warm mid voice.", "rules_pt": ["mesma voz"]}}
    env = lambda name: {"name": name, "function_pt": "cenário", "architecture_pt": "pequeno", "furniture_pt": "mesa", "objects_pt": "panelas", "colors_pt": "quentes", "materials_pt": "madeira",
                        "lighting_pt": "luz suave", "time_of_day_pt": "manhã", "atmosphere_pt": "aconchego", "fixed_elements_pt": "fogão", "angles_pt": "frontal",
                        "fixed_description": f"A cozy {name} with warm light."}
    return {"style_extra_en": "warm palette", "global_rules_pt": ["Tudo em 9:16"],
            "characters": [char("Banana", "banana", "feminino", "yellow"), char("Morango", "strawberry", "masculino", "red"),
                           {"name": "Narrador", "role_pt": "voz", "fixed_description": "voice only", "voice": {"descriptor_en": "Calm narrator voice."}}],
            "environments": [env("Cozinha"), env("Jardim")], "props": [],
            "continuity": [{"scene": i, "environment": "Cozinha" if i < 7 else "Jardim", "characters": ["Banana", "Morango"], "outfit_pt": "avental", "accessories_pt": "laço",
                            "objects_pt": "nenhum", "emotion_pt": "surpresa"} for i in range(1, n + 1)], "audit_pt": ["Tudo coerente."]}


def workflow_payload(script, split=None):
    scenes = []
    for s in script["scenes"]:
        scenes.append({"id": f"{s['n']:02d}", "script_scene": s["n"], "title": s["title"], "environment": "Cozinha" if s["n"] < 7 else "Jardim", "characters": ["Banana", "Morango"],
                       "image_prompt_en": f"Medium shot, Banana holds a whisk, Morango stands beside the table, scene {s['n']}.", "animation_camera_en": f"Slow push-in; Banana lifts the whisk, scene {s['n']}.",
                       "shot_pt": "plano médio, 35 mm, leve zoom", "dialogues": [dict(l) for l in s["lines"]], "duration_s": s["duration_s"], "note_pt": ""})
    return {"scenes": scenes, "order_note_pt": "Em ordem."}


class FactoryLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.script = script_payload()
        self.bible = bible_payload()
        self.proposals = proposals_payload()
        self.workflow = workflow_payload(self.script)

    async def ask(self, prompt, files=None, *, new_chat=True):
        for marker, queue in self.json_script.items():
            if marker in prompt and queue:
                self.asks.append({"prompt": prompt, "files": [], "new_chat": new_chat})
                return queue.pop(0)
        if "TAREFA: você é o ANALISTA DE REFERÊNCIAS" in prompt:
            self.asks.append({"prompt": prompt, "files": [str(f) for f in files or []], "new_chat": new_chat})
            n = prompt.split("referência ")[1].split(" ")[0]
            return f"## Referência {n}\n\n### Origem e disponibilidade\n\nAnalisada."
        for marker, payload in (("TAREFA: você é o ROTEIRISTA DE PROPOSTAS", self.proposals), ("TAREFA: você é o ROTEIRISTA. Desenvolva", self.script),
                                ("TAREFA: você é o RESPONSÁVEL PELA CONTINUIDADE VISUAL", self.bible), ("TAREFA: você é o DIRETOR DE ANIMAÇÃO", self.workflow)):
            if marker in prompt:
                self.asks.append({"prompt": prompt, "files": [], "new_chat": new_chat})
                return as_json(payload)
        if "TAREFA: você é o ANALISTA COMPARATIVO" in prompt:
            self.asks.append({"prompt": prompt, "files": [], "new_chat": new_chat})
            return "## 1. Matriz de aprendizados\n\n| Elemento | Referências |\n| --- | --- |\n| gancho | 1 |"
        return await super().ask(prompt, files, new_chat=new_chat)


@pytest.fixture
def factory_project(project):
    ctx, _, video, sink = project
    llm = FactoryLLM()
    ctx.brain = ctx.images = llm
    ctx.profile = factory.normalize_profile({"references": []})
    ctx.max_clip = factory.MAX_CLIP_S
    ctx.direct_sheets = True
    return ctx, llm, video, sink


def run_factory(ctx, stage_key, **changes):
    return run(ctx, stage_key, **changes)


def up_to_workflow(ctx):
    for key in ("f_refs", "f_proposals", "f_script", "f_bible", "f_workflow"):
        run_factory(ctx, key)


# ---------------------------------------------------------------------------
def test_profile_uses_the_factory_defaults_and_rejects_the_impossible():
    p = factory.normalize_profile({})
    assert (p["language"], p["min_s"], p["max_s"], p["min_scenes"], p["scene_min_s"], p["scene_max_s"], p["audio"]) == ("pt-BR", 90, 120, 12, 6, 10, "dialogos")
    assert p["fruit"] and p["language_en"] == "Brazilian Portuguese" and "Pixar" in p["style_en"]
    assert factory.normalize_profile({"niche": "robôs", "style": "clay", "scene_max_s": 99})["scene_max_s"] == 10, "o limite absoluto é 10 s por clipe"
    assert not factory.normalize_profile({"niche": "robôs"})["fruit"]
    assert factory.normalize_profile({"style": "um estilo meu", "style_custom": "neon noir"})["style_en"] == "neon noir"
    with pytest.raises(ValueError, match="somam"):   # 12 × 6 = 72 já passa de 60
        factory.normalize_profile({"min_s": 30, "max_s": 60, "min_scenes": 12})
    assert len(factory.normalize_profile({"references": [f"r{i}" for i in range(9)]})["references"]) == 5


def test_full_factory_flow_from_proposals_to_9x16_clips(factory_project):
    ctx, llm, video, sink = factory_project
    assert run_factory(ctx, "f_refs")["warnings"], "sem referências o painel avisa"
    result = run_factory(ctx, "f_proposals")
    assert result["recommended"] == 2 and [p["n"] for p in result["proposals"]] == [1, 2, 3, 4, 5]
    assert "## História 3 · História 3" in sink.documents["doc-F3"]

    ctx.chosen = 3
    run_factory(ctx, "f_script")
    script_prompt = next(a["prompt"] for a in llm.asks if "ROTEIRISTA. Desenvolva" in a["prompt"])
    assert '"title": "História 3"' in script_prompt and '"title": "História 2"' not in script_prompt, "só a proposta escolhida é desenvolvida"
    assert "entre 90 e 120 s" in script_prompt and "12 × 6 s = 72 s" in script_prompt
    assert "Soma das durações" not in sink.documents["doc-F4"] and "96 s" in sink.documents["doc-F4"]

    result = run_factory(ctx, "f_bible")
    pack = sink.documents["doc-F5"]
    for part in ("[00_METADADOS]", "[01_REGRAS_GLOBAIS]", "[02_PERSONAGENS]", "[03_AMBIENTES]", "[06_MATRIZ_DE_CONTINUIDADE]", "CHAR_01", "LOC_02"):
        assert part in pack
    assert "[05_ROTEIRO_CENA_A_CENA]" not in pack, "a Fábrica termina nos metadados, personagens e ambientes"
    assert "vertical 9:16" in pack and "APROVADO" in pack
    bible = ctx.load_data("bible")
    assert bible["style_block"].startswith("stylized cinematic 3D") and [c["name"] for c in bible["characters"]] == ["Banana", "Morango", "Narrador"]

    result = run_factory(ctx, "f_workflow")
    assert result["next_generation"] == {"service": "ChatGPT", "count": 2, "unit": "folhas 9:16 de personagem"}
    doc = sink.documents["doc-F6"]
    assert "#### CENA 01 - Cena 1" in doc and "Animation & Camera Action:" in doc and "**Banana (feminino):**" in doc
    plan, clips = ctx.load_data("plan"), ctx.load_data("clips")
    assert all(s["pre_action"] for s in plan["scenes"]) and [c["duration_s"] for c in clips["clips"]] == [10] * 12, "8 s viram o clipe de 10 s do Grok"

    for key in ("character_sheets", "scene_images", "production"):
        run_factory(ctx, key)
    assert llm.images and all("9:16" in i["prompt"] and "16:9" not in i["prompt"] and "2:3" not in i["prompt"] for i in llm.images), "toda imagem sai em 9:16"
    sheets = [i for i in llm.images if "Character reference sheet" in i["prompt"]]
    assert len(sheets) == 2 and all("vertical 9:16 portrait" in s["prompt"] for s in sheets)
    scene = next(i for i in llm.images if i["out"] == "cena-01.png")
    assert "zero motion, pre-action pose, no particles" in scene["prompt"] and "Static pre-action image: Medium shot, Banana holds a whisk" in scene["prompt"]
    assert scene["refs"] == ["folha-banana.png", "folha-morango.png", "ambiente-cozinha.png"]
    first = video.calls[0]
    assert first["start"] == "cena-01.png" and first["duration_s"] == 10 and len(video.calls) == 12
    assert first["prompt"].startswith("Animation & Camera Action: Slow push-in")
    assert 'Dialogue (speak in Brazilian Portuguese, each line exactly as written, in this order):\n- Banana (female): "Eu não acredito que isso aconteceu 1!"' in first["prompt"]
    assert first["prompt"].endswith("Duration: 10 seconds. Vertical 9:16.") and "Banana has a warm mid voice." in first["prompt"]


def test_without_a_choice_the_recommended_proposal_is_developed(factory_project):
    ctx, llm, _, _ = factory_project
    for key in ("f_refs", "f_proposals", "f_script"):
        run_factory(ctx, key)
    prompt = next(a["prompt"] for a in llm.asks if "ROTEIRISTA. Desenvolva" in a["prompt"])
    assert '"title": "História 2"' in prompt and ctx.load_data("script")["proposal"] == {"n": 2, "title": "História 2"}
    ctx.chosen = 9
    with pytest.raises(Blocked, match="não existe"):
        run_factory(ctx, "f_script")


def test_references_are_analyzed_one_by_one_then_compared(factory_project, sample_video):
    ctx, llm, _, sink = factory_project
    ctx.profile = factory.normalize_profile({"references": [str(sample_video), "C:/nao/existe.mp4"]})
    result = run_factory(ctx, "f_refs")
    individual = [a for a in llm.asks if "ANALISTA DE REFERÊNCIAS" in a["prompt"]]
    assert len(individual) == 1 and individual[0]["files"], "cada referência é analisada com os quadros dela anexados"
    assert "Referência 1" in sink.documents["doc-F1"] and "Referência 2 (C:/nao/existe.mp4)" in sink.documents["doc-F1"] and "Não analisadas" in sink.documents["doc-F1"]
    assert any("não pôde" in w or "Referência 2" in w for w in result["warnings"]) and any("1 de 5" in w for w in result["warnings"])
    assert "ev-r1-gancho-01" in sink.media and "Matriz de aprendizados" in sink.documents["doc-F2"]
    matrix = next(a["prompt"] for a in llm.asks if "ANALISTA COMPARATIVO" in a["prompt"])
    assert "NÃO puderam ser analisadas" in matrix and "não finja tê-las visto" in matrix

    calls = len(llm.asks)
    run_factory(ctx, "f_refs", feedback="destaque o gancho")   # ajuste: refaz só a matriz, sem reanalisar os vídeos
    assert len(llm.asks) == calls + 1 and "destaque o gancho" in llm.asks[-1]["prompt"]

    ctx.profile = factory.normalize_profile({"references": ["C:/nao/existe.mp4"]})
    ctx.dir = ctx.dir.parent / "outro"
    with pytest.raises(Blocked) as err:
        run_factory(ctx, "f_refs")
    assert err.value.code == "SOURCE_UNAVAILABLE"


def test_proposals_must_be_five_and_really_different():
    good = proposals_payload()
    assert factory.validate_proposals(good)["recommended"] == 2
    few = copy.deepcopy(good)
    few["proposals"].pop()
    with pytest.raises(ValueError, match="exatamente 5"):
        factory.validate_proposals(few)
    same_arch = copy.deepcopy(good)
    for p in same_arch["proposals"]:
        p["architecture"] = "progressão cronológica"
    with pytest.raises(ValueError, match="arquitetura"):
        factory.validate_proposals(same_arch)
    same_conflict = copy.deepcopy(good)
    for p in same_conflict["proposals"]:
        p["conflict"] = "o mesmo conflito"
    with pytest.raises(ValueError, match="mesmo conflito"):
        factory.validate_proposals(same_conflict)


def test_script_is_checked_against_scenes_total_time_and_speech():
    profile = factory.normalize_profile({})
    assert factory.validate_script(script_payload(), profile)["total_s"] == 96
    with pytest.raises(ValueError, match="pelo menos 12"):
        factory.validate_script(script_payload(n=11), profile)
    with pytest.raises(ValueError, match="soma das durações é 72"):   # 12 × 6 s = 72, e o alvo é 90–120
        factory.validate_script(script_payload(duration=6), profile)
    too_long = script_payload()
    too_long["scenes"][3]["lines"][0]["text"] = " ".join(["palavra"] * 40)
    with pytest.raises(ValueError, match="a fala leva cerca de"):
        factory.validate_script(too_long, profile)
    wrong = script_payload()
    wrong["scenes"][0]["duration_s"] = 12
    with pytest.raises(ValueError, match="fora de 6–10"):
        factory.validate_script(wrong, profile)


def test_workflow_keeps_every_line_covers_every_scene_and_respects_ten_seconds(factory_project):
    ctx, llm, _, _ = factory_project
    for key in ("f_refs", "f_proposals", "f_script", "f_bible"):
        run_factory(ctx, key)
    script, data = ctx.load_data("script"), ctx.load_data("factory")
    profile = ctx.profile
    ok = factory.validate_workflow(workflow_payload(script), profile, script, data)
    assert len(ok["scenes"]) == 12

    altered = workflow_payload(script)
    altered["scenes"][2]["dialogues"][0]["text"] = "Outra fala qualquer"
    with pytest.raises(ValueError, match="não aparece exatamente"):
        factory.validate_workflow(altered, profile, script, data)
    missing = workflow_payload(script)
    missing["scenes"].pop()
    with pytest.raises(ValueError, match="não cobre as cenas do roteiro \\[12\\]"):
        factory.validate_workflow(missing, profile, script, data)
    long_clip = workflow_payload(script)
    long_clip["scenes"][0]["duration_s"] = 12
    with pytest.raises(ValueError, match="limite absoluto é 10 s"):
        factory.validate_workflow(long_clip, profile, script, data)
    stranger = workflow_payload(script)
    stranger["scenes"][0]["characters"] = ["Abacaxi"]
    with pytest.raises(ValueError, match="não existe"):
        factory.validate_workflow(stranger, profile, script, data)
    split = workflow_payload(script)   # cena dividida em duas: as falas continuam todas lá
    first = split["scenes"][0]
    second = {**copy.deepcopy(first), "id": "01b", "dialogues": []}
    first["id"] = "01a"
    split["scenes"].insert(1, second)
    assert len(factory.validate_workflow(split, profile, script, data)["scenes"]) == 13


def test_bible_needs_a_sheet_for_everyone_and_a_continuity_row_per_scene():
    profile = factory.normalize_profile({})
    script = factory.validate_script(script_payload(), profile)
    assert factory.validate_factory(bible_payload(), profile, script)["bible"]["characters"][0]["slug"] == "banana"
    no_one = bible_payload()
    no_one["characters"].pop(1)
    with pytest.raises(ValueError, match="sem ficha"):
        factory.validate_factory(no_one, profile, script)
    short = bible_payload(n=10)
    with pytest.raises(ValueError, match="cenas \\[11, 12\\]"):
        factory.validate_factory(short, profile, script)
    bad_env = bible_payload()
    bad_env["continuity"][0]["environment"] = "Praia"
    with pytest.raises(ValueError, match="não está em"):
        factory.validate_factory(bad_env, profile, script)
    naked = bible_payload()
    naked["characters"][0]["outfit"] = []
    with pytest.raises(ValueError, match="outfit"):
        factory.validate_factory(naked, profile, script)


def test_revision_of_the_workflow_sends_the_previous_scenes_and_the_adjustment(factory_project):
    ctx, llm, _, _ = factory_project
    up_to_workflow(ctx)
    run_factory(ctx, "f_workflow", feedback="deixe a câmera mais lenta")
    prompt = llm.asks[-1]["prompt"]
    assert "deixe a câmera mais lenta" in prompt and "Slow push-in" in prompt
    assert "Limite absoluto de **10 segundos por clipe**" in prompt, "o workflow-base editável entra no pedido"
    assert 'nome="correcoes-acumuladas"' in prompt and "Personagem trocou de roupa" in prompt, "a camada 4 (regras aprendidas) entra em toda geração de cenas"


def test_worker_wires_profile_choice_and_clears_the_choice_when_proposals_are_redone(tmp_path, sample_video, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    db, llm = FakeDb(str(sample_video)), FactoryLLM()
    db.projects["p1"].update({"mode": "factory", "profile": {"references": []}})
    db.payloads = {"f_proposals": {"chosen": 4}}
    db.stage_payload = lambda project_id, key: db.payloads.get(key) or {}

    def go(stage, **inp):
        if stage not in db.stages:
            db.add_stage(stage)
        job = db.job("run_stage", stage, **inp)
        asyncio.run(main.process(db, job, lambda project_dir, model=None: llm, llm, FakeVideo(sample_video)))
        return db.jobs[job["id"]]

    assert go("f_proposals")["status"] == "succeeded"
    payload = db.stages["f_proposals"]["payload"]
    assert payload["recommended"] == 2 and len(payload["proposals"]) == 5 and "chosen" not in payload
    assert db.artifacts["doc-F3"]["artifact_type"] == "document"
    go("f_script")
    prompt = next(a["prompt"] for a in llm.asks if "ROTEIRISTA. Desenvolva" in a["prompt"])
    assert '"title": "História 4"' in prompt, "o worker lê a escolha da etapa de propostas"
    db.projects["p1"]["profile"] = {"min_s": 30, "max_s": 60, "min_scenes": 12}
    outcome = go("f_bible")
    assert outcome["status"] == "blocked" and "Perfil de produção inválido" in outcome["error_message"]
    assert main.factory_context(db, {"mode": "inspired"}) == (None, None)


def test_legacy_modes_keep_their_prompts_but_now_ask_for_9x16():
    bible = {"style_block": "Style.", "characters": []}
    ch = {"fixed_description": "A fox.", "expressions": [], "details": []}
    assert "vertical 9:16 portrait" in render.sheet_prompt(bible, ch) and "16:9" not in render.sheet_prompt(bible, ch)
    assert "Vertical 9:16" in render.full_body_prompt(bible, ch)
    plain = {"environment": "Küche", "characters": [], "composition": "Wide shot."}
    plan = {"environments": [{"name": "Küche", "fixed_description": "A kitchen."}]}
    legacy = render.scene_prompt(bible, plan, plain)
    assert "Composition: Wide shot." in legacy and "pre-action" not in legacy
    assert render.clip_prompt({"characters": []}, {"prompt": "Wide shot.", "speakers": [], "duration_s": 6}) == "Wide shot.\n\nDuration: 6 seconds. Vertical 9:16."


def test_speech_estimate_and_narrator_detection():
    assert factory.speech_seconds("") == 0
    assert 2.5 < factory.speech_seconds("uma duas três quatro cinco seis sete") < 2.7
    assert factory.is_narrator(" Narrador ") and not factory.is_narrator("Banana")
