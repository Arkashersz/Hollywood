"""As nove etapas do pipeline. O Python conduz; o Claude analisa e cria os textos e prompts;
o ChatGPT gera as imagens e o Grok os vídeos.

Cada etapa termina em `pending_review`: quem aprova é sempre o usuário, no painel.
Etapas de texto planejam (documentos e prompts); etapas de geração só rodam depois que o
usuário aprovou os prompts da etapa anterior, o que equivale a autorizar aquele lote.
"""
from __future__ import annotations

import asyncio
import json
import re
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
    duration: int | None = None   # só vídeo: segundos pedidos (o Python escolhe 6, 10 ou 15 no Grok)
    wave: int = 0                 # itens da mesma onda podem rodar juntos; uma onda só começa quando a anterior termina


# ---------------------------------------------------------------------------
# Projeto iniciado com prompts prontos (sem as etapas de texto)
# ---------------------------------------------------------------------------
IMPORT_DIRS = {"character_images": "personagens", "character_sheets": "folhas", "scene_images": "cenas", "production": "clipes"}


def _imported_duration(entry: dict, prompt: str, max_clip: int) -> int:
    """Duração do clipe importado: campo "Duração" informado; senão a que o prompt cita ("10 seconds"); senão 6 s."""
    raw = entry.get("duration_s")
    if raw in (None, ""):
        found = re.search(r"\b(\d{1,2})\s*(?:-|\s)?\s*(?:s|sec|secs|second|seconds|segundos?)\b", prompt, re.I)
        raw = found.group(1) if found else None
    try:
        return media.snap_duration(float(raw) if raw is not None else None, max_clip)
    except (TypeError, ValueError) as exc:
        raise Blocked("MISSING_INPUT", f"Duração inválida num clipe importado: {exc}") from None


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
    waves: dict[str, int] = {}   # nome -> onda: quem usa outra imagem DESTA etapa como referência espera por ela
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
                if start.casefold() in ("anterior", "previous", "ultimo quadro", "último quadro"):  # só sozinho
                    if previous is None or not previous.is_file():
                        raise Blocked("MISSING_INPUT", f'"{name}" continua do clipe anterior, que ainda não existe.')
                    frame = ctx.dir / "videos" / "_quadros" / f"inicio-{index:02d}.png"
                    frame.parent.mkdir(parents=True, exist_ok=True)
                    return [media.last_frame(previous, frame)]
                return resolve([x.strip() for x in start.split(",") if x.strip()], name)

            items.append(Item(f"imp-video-{index:02d}-{slug}", f"{index:02d} · {name}", ctx.dir / "videos" / "importados" / f"{index:02d}-{slug}.mp4",
                              prompt, start_image, "video", _imported_duration(entry, prompt, ctx.max_clip),
                              wave=(items[-1].wave + 1) if start.casefold() in ("anterior", "previous", "ultimo quadro", "último quadro") and items else 0))
        else:
            refs = [str(r) for r in entry.get("refs") or []]
            wave = 1 + max([waves.get(r.strip().casefold(), -1) for r in refs], default=-1)
            waves[name.casefold()] = wave
            items.append(Item(f"imp-{stage_key}-{slug}", name, ctx.dir / "imagens" / IMPORT_DIRS[stage_key] / f"{slug}.png",
                              prompt, lambda refs=refs, name=name: resolve(refs, name), wave=wave))
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
    body01 = await ctx.brain.ask(prompts.doc01(evidence, ctx.previous("01"), ctx.feedback, ctx.clone), attachments)
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
    prompt = prompts.doc03(ctx.read_doc("01"), ctx.read_doc("02"), ctx.max_clip, ctx.previous("03"), ctx.feedback, ctx.clone)
    ctx.log("Pedindo o roteiro " + ("fiel ao original, em alemão, " if ctx.clone else "em alemão ") + "ao Claude…")
    ctx.write_doc("03", await ctx.brain.ask(prompt, []))
    if ctx.clone:
        return {"summary": "Roteiro fiel ao original escrito (mesmos cortes, cenas e câmera). Leia a seção 7: há uma proposta de troca de blocos de cena. Aprovar mantém 100% fiel; para aplicar a troca, peça revisão escrevendo “aplicar a troca”."}
    return {"summary": "Roteiro em alemão escrito, com tradução e conta de duração."}


# ---------------------------------------------------------------------------
# 3 · Personagens e vozes (texto)
# ---------------------------------------------------------------------------
def _previous_data(ctx: Ctx, name: str) -> str | None:
    if ctx.feedback and ctx.data_path(name).is_file():
        return ctx.data_path(name).read_text(encoding="utf-8")
    return None


async def character_bible(ctx: Ctx) -> dict:
    prompt = prompts.bible(ctx.read_doc("01"), ctx.read_doc("03"), _previous_data(ctx, "bible"), ctx.feedback, ctx.clone)
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
    kept = made = 0
    todo: list[Item] = []
    for item in items:
        if only is not None and item.key != only:
            continue
        recorded = ctx.generated_version(item.key)
        if only is None and item.path.is_file() and (recorded is None or recorded >= ctx.version):
            ctx.sink.file(ctx.stage_key, item.key, item.name, item.path, item.kind, _meta(item))
            kept += 1
            continue
        todo.append(item)

    total, finished = len(items), kept
    video = bool(todo) and todo[0].kind == "video"
    width = max(1, ctx.video_concurrency if video else ctx.image_concurrency)
    shared = ctx.video_slots if video else ctx.image_slots   # com vários projetos, as abas são divididas entre eles
    slots: asyncio.Queue[int] = shared if shared is not None else asyncio.Queue()
    if shared is None:
        for n in range(width):
            slots.put_nowait(n)
    state = {"error": None, "last_start": 0.0}
    loop = asyncio.get_event_loop()

    async def one(item: Item) -> None:
        nonlocal made, finished
        slot = await slots.get()
        try:
            if state["error"] is not None:   # outro item já parou o lote: não começa mais nada
                return
            if ctx.pause and state["last_start"]:   # intervalo entre inícios, para não disparar tudo de uma vez
                await ctx.pause()
            state["last_start"] = loop.time()
            ctx.sink.progress(ctx.stage_key, finished, total, f"Gerando {item.name}…")
            prompt = item.prompt + (f"\n\nRequested adjustment (from the user): {note}" if note else "")
            refs = item.refs()
            if item.kind == "video":
                info = await ctx.video.generate_video(prompt, refs, item.path, duration_s=item.duration, slot=slot)
            else:
                info = await ctx.images.generate_image(prompt, refs, item.path, slot=slot)
            ctx.mark_generated(item.key)
            await asyncio.to_thread(ctx.sink.file, ctx.stage_key, item.key, item.name, item.path, item.kind, {**_meta(item), **(info or {})})
            ctx.log(f"Gerado: {item.name}.")
            made += 1
            finished += 1
        except Exception as exc:  # noqa: BLE001 - guarda o primeiro erro; os itens já em andamento terminam sozinhos
            state["error"] = state["error"] or exc
        finally:
            slots.put_nowait(slot)

    # Com um por vez vale a ordem da lista (quem depende de outro item vem depois dele). Com vários ao mesmo tempo,
    # os itens se dividem em ondas: uma onda só começa quando a anterior termina.
    for wave in (sorted({i.wave for i in todo}) if width > 1 else [None]):
        group = [i for i in todo if wave is None or i.wave == wave]
        await asyncio.gather(*(one(i) for i in group))
        if state["error"] is not None:
            raise state["error"]
    ctx.sink.progress(ctx.stage_key, total, total, "Concluído")
    return {"made": made, "kept": kept}


def _meta(item: Item) -> dict:
    meta = {"prompt": item.prompt}
    if item.kind == "video":
        try:
            meta["references"] = [p.name for p in item.refs()]
        except Blocked:
            pass
    return meta


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
                  render.environment_prompt(bible, e), lambda: []) for e in plan["environments"]]   # onda 0: as cenas usam estes ambientes
    for s in plan["scenes"]:
        refs = [ctx.dir / render.sheet_path(chars[n]) for n in s["characters"]] + [ctx.dir / render.env_path(envs[s["environment"]])]
        items.append(Item(f"img-cena-{s['id']}", f"Cena {s['id']} · imagem-base", ctx.dir / render.scene_path(s["id"]),
                          render.scene_prompt(bible, plan, s), lambda refs=refs: refs, wave=1))
    return items


def _clip_items(ctx: Ctx) -> list[Item]:
    bible, clips = ctx.load_data("bible"), ctx.load_data("clips")
    catalog = render.image_catalog(bible, ctx.load_data("plan"))

    def start_image(clip: dict) -> list[Path]:
        """Exatamente as imagens que o Claude indicou para este clipe, na ordem (a primeira é o quadro inicial)."""
        listed = clip.get("reference_images")
        paths = [ctx.dir / catalog[r]["path"] for r in listed] if listed else ([] if clip["start"] == "previous_last_frame" else [ctx.dir / render.scene_path(clip["scene"])])
        missing = [p for p in paths if not p.is_file()]
        if missing:
            raise Blocked("MISSING_INPUT", f"O clipe {clip['n']:02d} usa imagens que ainda não existem: {', '.join(p.name for p in missing)}")
        if clip["start"] != "previous_last_frame":
            return paths
        previous = ctx.dir / render.clip_path(clip["n"] - 1)
        if not previous.is_file():
            raise Blocked("MISSING_INPUT", f"O clipe {clip['n']:02d} continua do clipe anterior, que ainda não existe: {previous}")
        frame = ctx.dir / "videos" / "_quadros" / f"inicio-clipe-{clip['n']:02d}.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        return [media.last_frame(previous, frame), *paths]

    depth: dict[int, int] = {}
    for c in clips["clips"]:
        depth[c["n"]] = depth.get(c["n"] - 1, 0) + 1 if c["start"] == "previous_last_frame" else 0
    return [Item(f"vid-clipe-{c['n']:02d}", f"Clipe {c['n']:02d} · cena {c['scene']} · {c['duration_s']} s", ctx.dir / render.clip_path(c["n"]),
                 render.clip_prompt(bible, c), lambda c=c: start_image(c), "video", c["duration_s"], wave=depth[c["n"]]) for c in clips["clips"]]


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
    prompt = prompts.visual_plan(ctx.read_doc("03"), bible, _previous_data(ctx, "plan"), ctx.feedback, ctx.clone)
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
    catalog = render.image_catalog(bible, plan)
    prompt = prompts.clips(ctx.read_doc("02"), ctx.read_doc("03"), bible, plan, ctx.max_clip, _previous_data(ctx, "clips"), ctx.feedback, catalog, ctx.clone)
    ctx.log("Pedindo os prompts de vídeo ao Claude…")
    clips = await ask_json(ctx.brain, prompt, lambda raw: validate_clips(raw, bible, plan, ctx.max_clip, set(catalog), 0 if ctx.clone else 70))
    ctx.save_data("clips", clips)
    ctx.write_doc("08", render.doc08(bible, clips, ctx.max_clip, catalog))
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
    if len(items) > 1 and not ctx.pilot_approved:
        # Segurança: só o primeiro clipe é gerado; o restante depende da sua aprovação deste teste.
        await generate(ctx, items[:1], note=ctx.feedback)
        ctx.log("Clipe de teste pronto. Os demais só serão gerados depois que você aprovar este primeiro.")
        return {"summary": f"Clipe de teste gerado ({items[0].name}). Confira duração, imagens usadas, rosto, voz e movimento; aprovar libera os outros {len(items) - 1} clipes no Grok.",
                "pilot": True, "pilot_remaining": len(items) - 1}
    stats = await generate(ctx, items, note=ctx.feedback)
    total = round(sum(media.probe(i.path)["duration"] for i in items), 1)
    warnings = [] if (total > 70 or ctx.plan or ctx.clone) else [f"A soma real dos clipes é {total} s, abaixo da meta de 70 s. Regenere clipes mais longos ou acrescente cenas."]
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
