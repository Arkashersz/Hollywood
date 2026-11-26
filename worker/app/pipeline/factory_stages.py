"""Etapas de texto da Fábrica de roteiros e do workflow de animação (modo "factory").

Fábrica (Fases 1 a 6): referências → cinco propostas → roteiro → bíblia de personagens e ambientes (metadados incluídos).
Workflow de animação: transforma o roteiro e a bíblia em cenas (imagem de segundo zero, animação e câmera, diálogos).

Depois disso, as etapas de geração são as de sempre (folhas, imagens de ambiente e cena, vídeos), todas em 9:16:
estas etapas gravam os mesmos arquivos de dados (`bible`, `plan`, `clips`) que o restante do pipeline lê.
"""
from __future__ import annotations

import asyncio

from .. import media
from ..errors import Blocked
from . import factory, render
from .core import Ctx, ask_json, carry_changes, strip_leading_title

MAX_ATTACHMENTS = 16
FACTORY_ORDER = ["f_refs", "f_proposals", "f_script", "f_bible", "f_workflow", "character_sheets", "scene_images", "production"]


def _profile(ctx: Ctx) -> dict:
    if ctx.profile and "style_en" in ctx.profile:
        return ctx.profile
    try:
        return factory.normalize_profile(ctx.profile)
    except ValueError as exc:
        raise Blocked("MISSING_INPUT", f"Perfil de produção inválido: {exc}", "Crie o projeto de novo com um perfil possível.") from None


def _previous_data(ctx: Ctx, name: str) -> str | None:
    if ctx.feedback and ctx.data_path(name).is_file():
        return ctx.data_path(name).read_text(encoding="utf-8")
    return None


def _body(ctx: Ctx, number: str) -> str:
    return strip_leading_title(ctx.read_doc(number))


# ---------------------------------------------------------------------------
# Fases 1 e 2 · referências e matriz de aprendizados
# ---------------------------------------------------------------------------
def _publish_evidence(ctx: Ctx, n: int, ref_dir, evidence: dict) -> list:
    """Publica no painel o gancho, o fecho e as folhas de 1 s de cada referência; devolve os arquivos a anexar para o Claude."""
    hook = [ref_dir / x for x in evidence.get("sheets_hook", [])]
    end = [ref_dir / x for x in evidence.get("sheets_end", [])]
    sheets = [ref_dir / x for x in evidence["sheets_1s"]]
    cuts = [ref_dir / x for x in evidence["sheets_cuts"]]
    for kind, label, paths in (("gancho", "gancho (4 s iniciais)", hook), ("final", "fecho (4 s finais)", end), ("1s", "quadros a cada 1 s", sheets)):
        for i, path in enumerate(paths, 1):
            ctx.sink.file(ctx.stage_key, f"ev-r{n}-{kind}-{i:02d}", f"Referência {n} · {label} ({i}/{len(paths)})", path, "evidence")
    return (hook + sheets + end + cuts)[:MAX_ATTACHMENTS]


async def factory_refs(ctx: Ctx) -> dict:
    profile = _profile(ctx)
    refs = profile["references"]
    if not refs:
        ctx.write_doc("F1", factory.doc_refs([], []))
        ctx.write_doc("F2", "## Sem referências\n\nNenhuma referência foi enviada, então não há aprendizados para comparar. As cinco propostas partem de princípios gerais do nicho e do perfil de produção.")
        return {"summary": "Nenhuma referência enviada: as propostas partem do perfil e de princípios gerais do nicho.",
                "warnings": ["Sem referências. O ideal são cinco vídeos; aprove para seguir assim ou crie o projeto de novo com os links."]}

    if ctx.feedback and ctx.data_path("refs").is_file():   # ajuste pedido: reaproveita as análises e refaz só a matriz
        saved = ctx.load_data("refs")
        sections, unavailable = saved["sections"], saved["unavailable"]
        ctx.log("Reaproveitando as análises individuais; refazendo a matriz de aprendizados com o seu ajuste…")
    else:
        sections, unavailable = [], []
        for n, ref in enumerate(refs, 1):
            ref_dir = ctx.dir / "referencias" / f"ref-{n}"
            ctx.sink.progress(ctx.stage_key, n - 1, len(refs), f"Analisando a referência {n} de {len(refs)}…")
            try:
                evidence = await asyncio.to_thread(media.analyze, ref, ref_dir, whisper_model=ctx.whisper_model, language=ctx.language, log=ctx.log)
            except Blocked as exc:
                if exc.code != "SOURCE_UNAVAILABLE":
                    raise
                unavailable.append(f"Referência {n} ({ref}): {exc.message}")
                ctx.log(f"Referência {n} não pôde ser analisada: {exc.message}", "warn")
                continue
            attachments = _publish_evidence(ctx, n, ref_dir, evidence)
            ctx.log(f"Pedindo a análise individual da referência {n} ao Claude…")
            identification = render.identification(evidence).replace("## 1. Identificação", "### Identificação automática", 1)
            body = await ctx.brain.ask(factory.refs_individual(profile, n, len(refs), evidence, identification), attachments)
            sections.append(strip_leading_title(body).strip() + "\n\n" + identification)
        if not sections:
            raise Blocked("SOURCE_UNAVAILABLE", "Nenhuma das referências pôde ser baixada. " + " | ".join(unavailable),
                          "Salve os vídeos como .mp4 no PC do worker e crie o projeto informando os caminhos dos arquivos.")
        ctx.save_data("refs", {"sections": sections, "unavailable": unavailable})
    ctx.write_doc("F1", factory.doc_refs(sections, unavailable))
    ctx.log("Pedindo a matriz de aprendizados ao Claude…")
    matrix = await ctx.brain.ask(factory.refs_matrix(profile, "\n\n".join(sections), unavailable, ctx.previous("F2"), ctx.feedback), [])
    ctx.write_doc("F2", matrix)
    warnings = list(unavailable)
    if len(sections) < 5:
        warnings.append(f"Foram analisadas {len(sections)} de 5 referências. O ideal são cinco; aprove para seguir com o que há ou crie o projeto de novo com mais links.")
    return {"summary": f"{len(sections)} referência(s) analisada(s) individualmente e comparadas numa matriz de aprendizados (o que aproveitar e o que não copiar).", "warnings": warnings}


# ---------------------------------------------------------------------------
# Fase 3 · cinco propostas originais
# ---------------------------------------------------------------------------
async def factory_proposals(ctx: Ctx) -> dict:
    profile = _profile(ctx)
    matrix = _body(ctx, "F2") if profile["references"] and ctx.doc_path("F2").is_file() else None
    ctx.log("Pedindo as cinco propostas originais ao Claude…")
    data = await ask_json(ctx.brain, factory.proposals(profile, matrix, _previous_data(ctx, "proposals"), ctx.feedback), factory.validate_proposals)
    ctx.save_data("proposals", data)
    ctx.write_doc("F3", factory.doc_proposals(data))
    return {"summary": f"Cinco propostas originais prontas. O Claude recomenda a história {data['recommended']}; escolha qual desenvolver nos botões da etapa e aprove (sem escolha, vale a recomendada).",
            "proposals": [{"n": p["n"], "title": p["title"], "hook": p["hook"]} for p in data["proposals"]], "recommended": data["recommended"]}


# ---------------------------------------------------------------------------
# Fase 4 · roteiro completo
# ---------------------------------------------------------------------------
async def factory_script(ctx: Ctx) -> dict:
    profile = _profile(ctx)
    proposals = ctx.load_data("proposals")
    n = ctx.chosen or proposals["recommended"]
    if not 1 <= n <= len(proposals["proposals"]):
        raise Blocked("MISSING_INPUT", f"A história escolhida ({n}) não existe entre as propostas.")
    proposal = proposals["proposals"][n - 1]
    matrix = _body(ctx, "F2") if profile["references"] and ctx.doc_path("F2").is_file() else None
    ctx.log(f"Desenvolvendo a história {n}: {proposal['title']}. Pedindo o roteiro completo ao Claude…")
    script = await ask_json(ctx.brain, factory.script(profile, proposal, matrix, _previous_data(ctx, "script"), ctx.feedback), lambda raw: factory.validate_script(raw, profile))
    script["proposal"] = {"n": n, "title": proposal["title"]}
    ctx.save_data("script", script)
    ctx.write_doc("F4", factory.doc_script(script, profile))
    return {"summary": f"Roteiro de “{script['title']}” (história {n}): {len(script['scenes'])} cenas, {script['total_s']} s no total, falas conferidas contra o tempo de cada cena."}


# ---------------------------------------------------------------------------
# Fases 5 e 6 · bíblia de personagens e ambientes, metadados e continuidade (fim da Fábrica)
# ---------------------------------------------------------------------------
async def factory_bible(ctx: Ctx) -> dict:
    profile = _profile(ctx)
    script = ctx.load_data("script")
    ctx.log("Pedindo a bíblia de personagens e ambientes ao Claude…")
    data = await ask_json(ctx.brain, factory.bible(profile, script, _previous_data(ctx, "factory"), ctx.feedback), lambda raw: factory.validate_factory(raw, profile, script))
    bible = data.pop("bible")
    ctx.save_data("factory", data)
    if ctx.data_path("bible").is_file():   # refazer o texto não apaga o que o usuário já ajustou nas imagens
        carry_changes(bible, ctx.load_data("bible"), "characters")
    ctx.save_data("bible", bible)   # mesmo formato que as etapas de imagem e vídeo leem
    ctx.write_doc("F5", factory.doc_factory(profile, script, data))
    visible = len(render.visible(bible))
    return {"summary": f"Pacote da Fábrica pronto: metadados, {len(data['characters'])} personagem(ns), {len(data['environments'])} ambiente(s) e matriz de continuidade de {len(data['continuity'])} cenas. "
                       "A Fábrica termina aqui; o próximo passo é o workflow de animação.", "visible_characters": visible}


# ---------------------------------------------------------------------------
# Workflow de animação · cenas
# ---------------------------------------------------------------------------
async def factory_workflow(ctx: Ctx) -> dict:
    profile = _profile(ctx)
    script, data, bible = ctx.load_data("script"), ctx.load_data("factory"), ctx.load_data("bible")
    ctx.log("Pedindo as cenas do workflow de animação (imagem de segundo zero, animação e câmera, diálogos) ao Claude…")
    wf = await ask_json(ctx.brain, factory.workflow(profile, script, data, _previous_data(ctx, "workflow"), ctx.feedback),
                        lambda raw: factory.validate_workflow(raw, profile, script, data))
    plan, clips = factory.plan_and_clips(profile, data, wf, media.snap_duration)
    ctx.save_data("workflow", wf)
    if ctx.data_path("plan").is_file():
        carry_changes(plan, ctx.load_data("plan"), "environments")
    ctx.save_data("plan", plan)
    ctx.save_data("clips", clips)
    ctx.write_doc("F6", factory.doc_workflow(profile, plan, clips, wf, data, lambda s: render.scene_prompt(bible, plan, s), lambda c: render.clip_prompt(bible, c)))
    sheets = len(render.visible(bible))
    split = len(wf["scenes"]) - len(script["scenes"])
    extra = f" ({split} cena(s) dividida(s) por causa do limite de {factory.MAX_CLIP_S} s)" if split > 0 else ""
    warnings = []
    if clips["total_s"] > profile["max_s"]:
        warnings.append(f"Os clipes gerados somam {clips['total_s']} s (o Grok só gera 6, 10 ou 15 s), acima do alvo de {profile['max_s']} s. Corte o excesso na edição ou peça ajustes.")
    return {"summary": f"{len(wf['scenes'])} cenas prontas{extra}: imagem de segundo zero, Animation & Camera Action e diálogos em cada uma. Tudo em {factory.ASPECT}; clipes de no máximo {factory.MAX_CLIP_S} s.",
            "warnings": warnings, "next_generation": {"service": "ChatGPT", "count": sheets, "unit": f"folhas {factory.ASPECT} de personagem"}}


HANDLERS = {"f_refs": factory_refs, "f_proposals": factory_proposals, "f_script": factory_script, "f_bible": factory_bible, "f_workflow": factory_workflow}