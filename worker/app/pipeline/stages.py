"""As nove etapas do pipeline. O Python conduz; o Claude analisa e cria os textos e prompts;
o ChatGPT gera as imagens e o Grok os vídeos.

Cada etapa termina em `pending_review`: quem aprova é sempre o usuário, no painel.
Etapas de texto planejam (documentos e prompts); etapas de geração só rodam depois que o
usuário aprovou os prompts da etapa anterior, o que equivale a autorizar aquele lote.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import media
from ..media import slugify
from ..errors import Blocked
from . import prompts, render
from .core import Ctx, ask_json, strip_leading_title, validate_bible, validate_clips, validate_plan

ORDER = ["analysis", "script", "character_bible", "character_images", "character_sheets",
         "visual_plan", "scene_images", "video_prompts", "production"]
MAX_ATTACHMENTS = 16


@dataclass
class Item:
    key: str
    name: str
    path: Path
    prompt: str
    refs: Callable[[], list[Path]]
    kind: str = "image"   # image | video


# ---------------------------------------------------------------------------
# Projeto iniciado com prompts prontos (sem as etapas de texto)
# ---------------------------------------------------------------------------
IMPORT_DIRS = {"character_images": "personagens", "character_sheets": "folhas", "scene_images": "cenas", "production": "clipes"}


def _imported_items(ctx: Ctx, stage_key: str) -> list[Item]:
    """Itens da etapa a partir dos prompts do usuário. Referências são resolvidas pelo NOME de outra imagem do plano."""
    plan = ctx.plan or {}
    paths: dict[str, Path] = {}      # nome (minúsculo) -> arquivo da imagem
    for key in ("character_images", "character_sheets", "scene_images"):
        used: set[str] = set()
        for entry in plan.get(key) or []:
            slug = slugify(entry["name"], 50)
            base, n = slug, 2
            while slug in used:
                slug, n = f"{base}-{n}", n + 1
            used.add(slug)
            paths.setdefault(str(entry["name"]).strip().casefold(), ctx.dir / "imagens" / IMPORT_DIRS[key] / f"{slug}.png")

    def resolve(names: list[str], owner: str) -> list[Path]:
        out = []
        for raw in names:
            path = paths.get(str(raw).strip().casefold())
            if path is None:
                raise Blocked("MISSING_INPUT", f'"{owner}" usa a referência "{raw}", que não está entre as imagens que você enviou.')
            if not path.is_file():
                raise Blocked("MISSING_INPUT", f'"{owner}" precisa da imagem "{raw}", que ainda não foi gerada ({path.name}). '
                                               "Envie esse prompt numa etapa anterior ou salve a imagem nesse caminho.")
            out.append(path)
        return out

    items: list[Item] = []
    used: set[str] = set()
    for index, entry in enumerate(plan.get(stage_key) or [], 1):
        name, prompt = str(entry["name"]).strip(), str(entry["prompt"]).strip()
        slug = slugify(name, 50)
        base, n = slug, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        if stage_key == "production":
            start = str(entry.get("start") or "").strip()
            previous = items[-1].path if items else None

            def start_image(start=start, name=name, previous=previous, index=index) -> list[Path]:
                if not start:
                    return []
                if start.casefold() in ("anterior", "previous", "ultimo quadro", "último quadro"):
                    if previous is None or not previous.is_file():
                        raise Blocked("MISSING_INPUT", f'"{name}" continua do clipe anterior, que ainda não existe.')
                    frame = ctx.dir / "videos" / "_quadros" / f"inicio-{index:02d}.png"
                    frame.parent.mkdir(parents=True, exist_ok=True)
                    return [media.last_frame(previous, frame)]
                return resolve([start], name)

            items.append(Item(f"imp-video-{index:02d}-{slug}", f"{index:02d} · {name}", ctx.dir / "videos" / "importados" / f"{index:02d}-{slug}.mp4",
                              prompt, start_image, "video"))
        else:
            refs = [str(r) for r in entry.get("refs") or []]
            items.append(Item(f"imp-{stage_key}-{slug}", name, ctx.dir / "imagens" / IMPORT_DIRS[stage_key] / f"{slug}.png",
                              prompt, lambda refs=refs, name=name: resolve(refs, name)))
    return items


# ---------------------------------------------------------------------------
# 1 · Análise (Python extrai as evidências; Claude interpreta)
# ---------------------------------------------------------------------------
async def analysis(ctx: Ctx) -> dict:
    ref = ctx.dir / "referencia"
    evidence = await asyncio.to_thread(media.analyze, ctx.source, ref, whisper_model=ctx.whisper_model,
                                       language=ctx.language, log=ctx.log)
    sheets = [ref / n for n in evidence["sheets_1s"]]
    cuts = [ref / n for n in evidence["sheets_cuts"]]
    hook = [ref / n for n in evidence.get("sheets_hook", [])]
    end = [ref / n for n in evidence.get("sheets_end", [])]
    for i, path in enumerate(hook, 1):
        ctx.sink.file(ctx.stage_key, f"ev-folha-gancho-{i:02d}", "Gancho quadro a quadro (primeiros 4 s)", path, "evidence")
    for i, path in enumerate(end, 1):
        ctx.sink.file(ctx.stage_key, f"ev-folha-final-{i:02d}", "Fecho / CTA quadro a quadro (últimos 4 s)", path, "evidence")
    for i, path in enumerate(sheets, 1):
        ctx.sink.file(ctx.stage_key, f"ev-folha-1s-{i:02d}", f"Quadros a cada 1 s ({i}/{len(sheets)})", path, "evidence")
    for i, path in enumerate(cuts, 1):
        ctx.sink.file(ctx.stage_key, f"ev-folha-cortes-{i:02d}", f"Candidatos a corte ({i}/{len(cuts)})", path, "evidence")

    warnings = []
    everything = hook + sheets + end + cuts
    attachments = everything[:MAX_ATTACHMENTS]
    if len(everything) > MAX_ATTACHMENTS:
        warnings.append(f"Vídeo longo: só {MAX_ATTACHMENTS} das {len(everything)} folhas de quadros foram enviadas para análise.")
    transcript = evidence.get("transcript")
    if not transcript:
        warnings.append("Sem transcrição: " + (evidence.get("transcript_error") or "vídeo sem áudio ou sem fala detectada") + ".")
    elif transcript["language_probability"] < 0.6:
        warnings.append(f"Idioma da fala incerto ({transcript['language']}, {transcript['language_probability']:.0%}); defina REFERENCE_LANGUAGE no worker se a transcrição estiver errada.")
    if not evidence.get("metadata"):
        warnings.append("Metadados do post (autor, legenda, data, métricas) não foram obtidos.")

    ctx.log("Pedindo a ficha da referência ao Claude…")
    body01 = await ctx.brain.ask(prompts.doc01(evidence, ctx.previous("01"), ctx.feedback), attachments)
    prev02 = ctx.previous("02")
    ctx.write_doc("01", render.identification(evidence) + "\n\n" + strip_leading_title(body01))
    ctx.log("Pedindo a análise de edição ao Claude…")
    body02 = await ctx.brain.ask(prompts.doc02(prev02, ctx.feedback), [], new_chat=False)
    ctx.write_doc("02", body02)
    p = evidence["probe"]
    return {"summary": f"Referência de {p['duration']} s ({p['width']}x{p['height']}) analisada: {len(evidence['cut_candidates'])} candidatos a corte, "
                       f"transcrição {'em ' + transcript['language'] if transcript else 'indisponível'}.", "warnings": warnings}


# ---------------------------------------------------------------------------
# 2 · Roteiro
# ---------------------------------------------------------------------------
async def script(ctx: Ctx) -> dict:
    prompt = prompts.doc03(ctx.read_doc("01"), ctx.read_doc("02"), ctx.max_clip, ctx.previous("03"), ctx.feedback)
    ctx.log("Pedindo o roteiro em alemão ao Claude…")
    ctx.write_doc("03", await ctx.brain.ask(prompt, []))
    return {"summary": "Roteiro em alemão escrito, com tradução e conta de duração."}


# ---------------------------------------------------------------------------
# 3 · Personagens e vozes (texto)
# ---------------------------------------------------------------------------
def _previous_data(ctx: Ctx, name: str) -> str | None:
    if ctx.feedback and ctx.data_path(name).is_file():
        return ctx.data_path(name).read_text(encoding="utf-8")
    return None


async def character_bible(ctx: Ctx) -> dict:
    prompt = prompts.bible(ctx.read_doc("01"), ctx.read_doc("03"), _previous_data(ctx, "bible"), ctx.feedback)
    ctx.log("Pedindo personagens, estilo e vozes ao Claude…")
    bible = await ask_json(ctx.brain, prompt, validate_bible)
    ctx.save_data("bible", bible)
    ctx.write_doc("04", render.doc04(bible))
    ctx.write_doc("05", render.doc05(bible))
    ctx.write_doc("06", render.doc06(bible))
    count = len(render.visible(bible))
    return {"summary": f"{len(bible['characters'])} personagem(ns) definidos, com estilo global e vozes.",
            "next_generation": {"service": "ChatGPT", "count": count, "unit": "imagens de corpo inteiro"}}


# ---------------------------------------------------------------------------
# Geração item a item (imagens no ChatGPT, vídeos no Grok)
# ---------------------------------------------------------------------------
async def generate(ctx: Ctx, items: list[Item], *, only: str | None = None, note: str | None = None) -> dict:
    """Gera os itens pendentes, um por vez. Itens já prontos nesta versão são mantidos."""
    if only is not None and only not in {i.key for i in items}:
        raise Blocked("MISSING_INPUT", f"Item desconhecido nesta etapa: {only}")
    made = kept = 0
    for index, item in enumerate(items, 1):
        if only is not None and item.key != only:
            continue
        recorded = ctx.generated_version(item.key)
        if only is None and item.path.is_file() and (recorded is None or recorded >= ctx.version):
            ctx.sink.file(ctx.stage_key, item.key, item.name, item.path, item.kind, _meta(item))
            kept += 1
            continue
        if made and ctx.pause:
            await ctx.pause()
        ctx.sink.progress(ctx.stage_key, index - 1, len(items), f"Gerando {item.name}…")
        prompt = item.prompt + (f"\n\nRequested adjustment (from the user): {note}" if note else "")
        refs = item.refs()
        if item.kind == "video":
            info = await ctx.video.generate_video(prompt, refs[0] if refs else None, item.path)
        else:
            info = await ctx.images.generate_image(prompt, refs, item.path)
        ctx.mark_generated(item.key)
        ctx.sink.file(ctx.stage_key, item.key, item.name, item.path, item.kind, {**_meta(item), **(info or {})})
        ctx.log(f"Gerado: {item.name}.")
        made += 1
    ctx.sink.progress(ctx.stage_key, len(items), len(items), "Concluído")
    return {"made": made, "kept": kept}


def _meta(item: Item) -> dict:
    return {"prompt": item.prompt}


def _character_items(ctx: Ctx) -> list[Item]:
    bible = ctx.load_data("bible")
    return [Item(f"img-personagem-{c['slug']}-corpo", f"{c['name']} · corpo inteiro", ctx.dir / render.body_path(c),
                 render.full_body_prompt(bible, c), lambda: []) for c in render.visible(bible)]


def _sheet_items(ctx: Ctx) -> list[Item]:
    bible = ctx.load_data("bible")
    return [Item(f"img-folha-{c['slug']}", f"{c['name']} · folha 16:9", ctx.dir / render.sheet_path(c),
                 render.sheet_prompt(bible, c), lambda c=c: [ctx.dir / render.body_path(c)]) for c in render.visible(bible)]


def _scene_items(ctx: Ctx) -> list[Item]:
    bible, plan = ctx.load_data("bible"), ctx.load_data("plan")
    chars = {c["name"]: c for c in bible["characters"]}
    envs = {e["name"]: e for e in plan["environments"]}
    items = [Item(f"img-ambiente-{e['slug']}", f"Ambiente · {e['name']}", ctx.dir / render.env_path(e),
                  render.environment_prompt(bible, e), lambda: []) for e in plan["environments"]]
    for s in plan["scenes"]:
        refs = [ctx.dir / render.sheet_path(chars[n]) for n in s["characters"]] + [ctx.dir / render.env_path(envs[s["environment"]])]
        items.append(Item(f"img-cena-{s['id']}", f"Cena {s['id']} · imagem-base", ctx.dir / render.scene_path(s["id"]),
                          render.scene_prompt(bible, plan, s), lambda refs=refs: refs))
    return items


def _clip_items(ctx: Ctx) -> list[Item]:
    bible, clips = ctx.load_data("bible"), ctx.load_data("clips")

    def start_image(clip: dict) -> list[Path]:
        if clip["start"] != "previous_last_frame":
            return [ctx.dir / render.scene_path(clip["scene"])]
        previous = ctx.dir / render.clip_path(clip["n"] - 1)
        if not previous.is_file():
            raise Blocked("MISSING_INPUT", f"O clipe {clip['n']:02d} continua do clipe anterior, que ainda não existe: {previous}")
        frame = ctx.dir / "videos" / "_quadros" / f"inicio-clipe-{clip['n']:02d}.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        return [media.last_frame(previous, frame)]

    return [Item(f"vid-clipe-{c['n']:02d}", f"Clipe {c['n']:02d} · cena {c['scene']} · {c['duration_s']} s", ctx.dir / render.clip_path(c["n"]),
                 render.clip_prompt(bible, c), lambda c=c: start_image(c), "video") for c in clips["clips"]]


_BUILDERS: dict[str, Callable[[Ctx], list[Item]]] = {
    "character_images": _character_items, "character_sheets": _sheet_items,
    "scene_images": _scene_items, "production": _clip_items,
}


def items_for(ctx: Ctx, stage_key: str) -> list[Item]:
    if ctx.plan and ctx.plan.get(stage_key):
        return _imported_items(ctx, stage_key)
    return _BUILDERS[stage_key](ctx)


ITEM_BUILDERS = _BUILDERS


def _imported_next(ctx: Ctx) -> dict:
    """Próximo lote autorizado pela aprovação, num projeto com prompts prontos (as etapas sem itens foram puladas)."""
    units = {"character_images": ("ChatGPT", "imagens de personagens"), "character_sheets": ("ChatGPT", "folhas 16:9"),
             "scene_images": ("ChatGPT", "imagens de ambiente e cena"), "production": ("Grok", "vídeos")}
    for key in ORDER[ORDER.index(ctx.stage_key) + 1:]:
        if key in units and ctx.plan.get(key):
            return {"next_generation": {"service": units[key][0], "count": len(ctx.plan[key]), "unit": units[key][1]}}
    return {}


async def character_images(ctx: Ctx) -> dict:
    items = items_for(ctx, "character_images")
    stats = await generate(ctx, items, note=ctx.feedback)
    nxt = _imported_next(ctx) if ctx.plan else {"next_generation": {"service": "ChatGPT", "count": len(items), "unit": "folhas 16:9"}}
    return {"summary": f"{len(items)} imagem(ns) de corpo inteiro ({stats['made']} gerada(s) agora).", **nxt}


async def character_sheets(ctx: Ctx) -> dict:
    items = items_for(ctx, "character_sheets")
    stats = await generate(ctx, items, note=ctx.feedback)
    return {"summary": f"{len(items)} folha(s) 16:9 ({stats['made']} gerada(s) agora).", **(_imported_next(ctx) if ctx.plan else {})}


# ---------------------------------------------------------------------------
# 6 · Ambientes e cenas (texto)  ·  7 · imagens
# ---------------------------------------------------------------------------
async def visual_plan(ctx: Ctx) -> dict:
    bible = ctx.load_data("bible")
    prompt = prompts.visual_plan(ctx.read_doc("03"), bible, _previous_data(ctx, "plan"), ctx.feedback)
    ctx.log("Pedindo ambientes e imagens-base ao Claude…")
    plan = await ask_json(ctx.brain, prompt, lambda raw: validate_plan(raw, bible))
    ctx.save_data("plan", plan)
    ctx.write_doc("07", render.doc07(bible, plan))
    count = len(plan["environments"]) + len(plan["scenes"])
    return {"summary": f"{len(plan['environments'])} ambiente(s) e {len(plan['scenes'])} cena(s) planejados nas oito áreas.",
            "next_generation": {"service": "ChatGPT", "count": count, "unit": "imagens de ambiente e cena"}}


async def scene_images(ctx: Ctx) -> dict:
    items = items_for(ctx, "scene_images")
    stats = await generate(ctx, items, note=ctx.feedback)
    return {"summary": f"{len(items)} imagem(ns) de ambiente e cena ({stats['made']} gerada(s) agora).", **(_imported_next(ctx) if ctx.plan else {})}


# ---------------------------------------------------------------------------
# 8 · Prompts de vídeo, edição e publicação (texto)  ·  9 · vídeos no Grok
# ---------------------------------------------------------------------------
async def video_prompts(ctx: Ctx) -> dict:
    bible, plan = ctx.load_data("bible"), ctx.load_data("plan")
    prev09, prev10 = ctx.previous("09"), ctx.previous("10")
    prompt = prompts.clips(ctx.read_doc("02"), ctx.read_doc("03"), bible, plan, ctx.max_clip, _previous_data(ctx, "clips"), ctx.feedback)
    ctx.log("Pedindo os prompts de vídeo ao Claude…")
    clips = await ask_json(ctx.brain, prompt, lambda raw: validate_clips(raw, bible, plan, ctx.max_clip))
    ctx.save_data("clips", clips)
    ctx.write_doc("08", render.doc08(bible, clips, ctx.max_clip))
    ctx.log("Pedindo o plano de edição ao Claude…")
    ctx.write_doc("09", await ctx.brain.ask(prompts.doc09(prev09, ctx.feedback), [], new_chat=False))
    metadata = {}
    evidence_file = ctx.dir / "referencia" / "evidencias.json"
    if evidence_file.is_file():
        metadata = json.loads(evidence_file.read_text(encoding="utf-8")).get("metadata") or {}
    ctx.log("Pedindo o kit de publicação ao Claude…")
    ctx.write_doc("10", await ctx.brain.ask(prompts.doc10(metadata, prev10, ctx.feedback), [], new_chat=False))
    return {"summary": f"{len(clips['clips'])} clipes planejados, somando {clips['total_s']} s; plano de edição e kit de publicação prontos.",
            "next_generation": {"service": "Grok", "count": len(clips["clips"]), "unit": "vídeos"}}


async def production(ctx: Ctx) -> dict:
    items = items_for(ctx, "production")
    stats = await generate(ctx, items, note=ctx.feedback)
    total = round(sum(media.probe(i.path)["duration"] for i in items), 1)
    warnings = [] if (total > 70 or ctx.plan) else [f"A soma real dos clipes é {total} s, abaixo da meta de 70 s. Regenere clipes mais longos ou acrescente cenas."]
    return {"summary": f"{len(items)} clipe(s), {total} s no total ({stats['made']} gerado(s) agora). Próximo passo: editar no CapCut conforme o Documento 09.",
            "warnings": warnings}


HANDLERS: dict[str, Callable] = {
    "analysis": analysis, "script": script, "character_bible": character_bible, "character_images": character_images,
    "character_sheets": character_sheets, "visual_plan": visual_plan, "scene_images": scene_images,
    "video_prompts": video_prompts, "production": production,
}


async def run_stage(ctx: Ctx) -> dict:
    handler = HANDLERS.get(ctx.stage_key)
    if handler is None:
        raise ValueError(f"Etapa desconhecida: {ctx.stage_key}")
    return await handler(ctx)


async def regen_item(ctx: Ctx, item_key: str, comment: str | None) -> dict:
    if ctx.stage_key not in ITEM_BUILDERS:
        raise Blocked("MISSING_INPUT", "Esta etapa não tem itens regeneráveis.")
    await generate(ctx, items_for(ctx, ctx.stage_key), only=item_key, note=comment)
    return {"summary": f"Item regenerado: {item_key}."}
