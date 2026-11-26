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
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import media
from ..media import slugify
from ..errors import Blocked
from . import factory_stages, prompts, render
from .core import Ctx, adopt_bible, adopt_plan, ask_json, carry_changes, strip_leading_title, validate_bible, validate_clips, validate_plan

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
async def _sequel_analysis(ctx: Ctx) -> dict:
    """Parte 2 sem vídeo de referência: o Claude resume a história até aqui e propõe direções para a continuação."""
    ctx.log(f"Pedindo o resumo e as direções da parte {ctx.sequel['part']} ao Claude…")
    ctx.write_doc("01", await ctx.brain.ask(prompts.sequel_brief(ctx.sequel, ctx.previous("01"), ctx.feedback), []))
    ctx.write_doc("02", (ctx.sequel.get("doc02") or "## Edição\n\nA parte anterior não tem documento de edição neste PC; mantenha o ritmo e o estilo do roteiro da parte anterior.")
                  + "\n\n> Herdado da parte anterior: esta continuação não tem vídeo de referência próprio.")
    return {"summary": f"Resumo da história até aqui e direções para a parte {ctx.sequel['part']}. Leia a seção 5 (direções); "
                       "para escolher uma, peça revisão dizendo qual, ou aprove para o Claude seguir a recomendada.", "warnings": []}


async def _story_analysis(ctx: Ctx) -> dict:
    """História criada a partir de uma ideia: o Claude escreve o briefing (premissa, personagens, beats, ganhos e finais) e o plano de edição base."""
    story = ctx.story
    ctx.log(f"Pedindo o briefing da história ({story['target_s']} s, no mínimo {story['min_scenes']} cenas) ao Claude…")
    ctx.write_doc("01", await ctx.brain.ask(prompts.story_brief(story, ctx.max_clip, ctx.previous("01"), ctx.feedback), []))
    ctx.log("Pedindo o plano de edição base ao Claude…")
    ctx.write_doc("02", await ctx.brain.ask(prompts.story_edit(ctx.previous("02"), ctx.feedback), [], new_chat=False))
    return {"summary": f"Briefing da sua ideia pronto: {story['target_s']} s, no mínimo {story['min_scenes']} cenas, gancho de 5 s e final aberto para a parte 2. "
                       "Leia os três ganchos e os três finais (seções 6 e 7); para escolher, peça revisão dizendo qual, ou aprove para seguir o recomendado.", "warnings": []}


async def analysis(ctx: Ctx) -> dict:
    if ctx.story and not ctx.sequel:
        return await _story_analysis(ctx)
    if ctx.sequel and not ctx.sequel.get("has_ref"):
        return await _sequel_analysis(ctx)
    sources = media.split_sources(ctx.source)
    if len(sources) > 1:
        return await _merged_analysis(ctx, sources)
    found = await _analyze_reference(ctx, ctx.source, ctx.dir / "referencia", ctx.previous("01"), ctx.previous("02"))
    evidence, transcript = found["evidence"], found["evidence"].get("transcript")
    ctx.write_doc("01", found["doc01"])
    ctx.write_doc("02", found["doc02"])
    p = evidence["probe"]
    return {"summary": f"Referência de {p['duration']} s ({p['width']}x{p['height']}) analisada: {len(evidence['cut_candidates'])} candidatos a corte, "
                       f"transcrição {'em ' + transcript['language'] if transcript else 'indisponível'}.", "warnings": found["warnings"]}


async def _merged_analysis(ctx: Ctx, sources: list[str]) -> dict:
    """Continuação inspirada em vários vídeos: analisa cada um por inteiro e depois planeja a história única que junta todos."""
    total = len(sources)
    prev_head, prev01 = prompts.split_by_video(ctx.previous("01"))
    prev02 = prompts.split_by_video(ctx.previous("02"))[1]
    found = []
    for n, source in enumerate(sources, 1):
        ctx.log(f"Analisando o vídeo {n} de {total}…")
        try:
            found.append(await _analyze_reference(ctx, source, ctx.dir / "referencia" / f"ref-{n}", prev01.get(n), prev02.get(n), video=(n, total)))
        except Blocked as exc:
            raise Blocked(exc.code, f"Vídeo {n} de {total}: {exc.message}", exc.action) from None
    # Índice geral da referência: usado pelo kit de publicação e pela recuperação do projeto a partir da pasta.
    index = {"source": "\n".join(sources), "metadata": found[0]["evidence"].get("metadata") or {}, "videos": [f["evidence"] for f in found]}
    (ctx.dir / "referencia" / "evidencias.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.log(f"Pedindo ao Claude o plano para unir os {total} vídeos em uma história só…")
    plan = await ctx.brain.ask(prompts.merge_refs([(f["doc01"], f["doc02"]) for f in found], ctx.sequel, ctx.clone, prev_head or None, ctx.feedback), [])

    def by_video(key: str) -> str:
        return "\n\n".join(f"# Vídeo {n} de {total}\n\n{f[key]}" for n, f in enumerate(found, 1))

    ctx.write_doc("01", strip_leading_title(plan).strip() + "\n\n" + by_video("doc01"))
    ctx.write_doc("02", f"## Edição de cada referência\n\nA direção de edição do vídeo único está na seção U5 do Documento 01.\n\n{by_video('doc02')}")
    seconds = round(sum(f["evidence"]["probe"]["duration"] for f in found), 1)
    return {"summary": f"{total} vídeos de referência analisados ({seconds} s somados) e planejados como uma única história, em ordem. Leia o plano de união no começo do Documento 01.",
            "warnings": [f"Vídeo {n}: {w}" for n, f in enumerate(found, 1) for w in f["warnings"]]}


async def _analyze_reference(ctx: Ctx, source: str, ref: Path, prev01: str | None, prev02: str | None, video: tuple[int, int] | None = None) -> dict:
    """Extrai as evidências de um vídeo e pede a ficha (01) e a análise de edição (02) dele."""
    key = f"ev-v{video[0]}-" if video else "ev-"
    label = f"Vídeo {video[0]} · " if video else ""
    evidence = await asyncio.to_thread(media.analyze, source, ref, whisper_model=ctx.whisper_model,
                                       language=ctx.language, log=ctx.log)
    sheets = [ref / n for n in evidence["sheets_1s"]]
    cuts = [ref / n for n in evidence["sheets_cuts"]]
    hook = [ref / n for n in evidence.get("sheets_hook", [])]
    end = [ref / n for n in evidence.get("sheets_end", [])]
    for i, path in enumerate(hook, 1):
        ctx.sink.file(ctx.stage_key, f"{key}folha-gancho-{i:02d}", f"{label}Gancho quadro a quadro (primeiros 4 s)", path, "evidence")
    for i, path in enumerate(end, 1):
        ctx.sink.file(ctx.stage_key, f"{key}folha-final-{i:02d}", f"{label}Fecho / CTA quadro a quadro (últimos 4 s)", path, "evidence")
    for i, path in enumerate(sheets, 1):
        ctx.sink.file(ctx.stage_key, f"{key}folha-1s-{i:02d}", f"{label}Quadros a cada 1 s ({i}/{len(sheets)})", path, "evidence")
    for i, path in enumerate(cuts, 1):
        ctx.sink.file(ctx.stage_key, f"{key}folha-cortes-{i:02d}", f"{label}Candidatos a corte ({i}/{len(cuts)})", path, "evidence")

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
    body01 = await ctx.brain.ask(prompts.doc01(evidence, prev01, ctx.feedback, ctx.clone, video), attachments)
    ctx.log("Pedindo a análise de edição ao Claude…")
    body02 = await ctx.brain.ask(prompts.doc02(prev02, ctx.feedback), [], new_chat=False)
    return {"evidence": evidence, "warnings": warnings, "doc01": render.identification(evidence) + "\n\n" + strip_leading_title(body01),
            "doc02": strip_leading_title(body02).strip()}


# ---------------------------------------------------------------------------
# 2 · Roteiro
# ---------------------------------------------------------------------------
async def script(ctx: Ctx) -> dict:
    prompt = prompts.doc03(ctx.read_doc("01"), ctx.read_doc("02"), ctx.max_clip, ctx.previous("03"), ctx.feedback, ctx.clone, ctx.sequel, ctx.story)
    ctx.log("Pedindo o roteiro " + ("fiel ao original, em alemão, " if ctx.clone else "em alemão ") + "ao Claude…")
    ctx.write_doc("03", await ctx.brain.ask(prompt, []))
    if ctx.clone:
        return {"summary": "Roteiro fiel ao original escrito (mesmos cortes, cenas e câmera). Leia a seção 7: há uma proposta de troca de blocos de cena. Aprovar mantém 100% fiel; para aplicar a troca, peça revisão escrevendo “aplicar a troca”."}
    if ctx.story:
        return {"summary": f"Roteiro em alemão da sua história, para {ctx.story['target_s']} s e no mínimo {ctx.story['min_scenes']} cenas, com gancho de 5 s e final aberto."}
    return {"summary": "Roteiro em alemão escrito, com tradução e conta de duração."}


# ---------------------------------------------------------------------------
# 3 · Personagens e vozes (texto)
# ---------------------------------------------------------------------------
def _previous_data(ctx: Ctx, name: str) -> str | None:
    if ctx.feedback and ctx.data_path(name).is_file():
        return ctx.data_path(name).read_text(encoding="utf-8")
    return None


async def character_bible(ctx: Ctx) -> dict:
    prompt = prompts.bible(ctx.read_doc("01"), ctx.read_doc("03"), _previous_data(ctx, "bible"), ctx.feedback, ctx.clone, ctx.sequel)
    ctx.log("Pedindo personagens, estilo e vozes ao Claude…")
    bible = await ask_json(ctx.brain, prompt, validate_bible)
    reused = adopt_bible(bible, ctx.sequel)
    if reused:
        ctx.log(f"Já existem e voltam idênticos (sem gerar de novo): {', '.join(reused)}.")
    if ctx.data_path("bible").is_file():   # refazer o texto não apaga o que o usuário já ajustou nas imagens
        carry_changes(bible, ctx.load_data("bible"), "characters")
    ctx.save_data("bible", bible)
    ctx.write_doc("04", render.doc04(bible, ctx.direct_sheets))
    ctx.write_doc("05", render.doc05(bible, ctx.direct_sheets))
    ctx.write_doc("06", render.doc06(bible))
    count = len([c for c in render.visible(bible) if not c.get("reused")])
    unit = "folhas 9:16 de personagem" if ctx.direct_sheets else "imagens de corpo inteiro"
    new = [c["name"] for c in bible["characters"] if not c.get("reused")]
    note = f" Reaproveitados da parte anterior: {', '.join(reused)}. Novos: {', '.join(new) or 'nenhum'}." if reused else ""
    return {"summary": f"{len(bible['characters'])} personagem(ns) definidos, com estilo global e vozes.{note}",
            "next_generation": {"service": "ChatGPT", "count": count, "unit": unit + (" novas" if reused else "")}}


# ---------------------------------------------------------------------------
# Geração item a item (imagens no ChatGPT, vídeos no Grok)
# ---------------------------------------------------------------------------
async def generate(ctx: Ctx, items: list[Item], *, only: str | None = None, note: str | None = None,
                   targets: dict[str, str | None] | None = None) -> dict:
    """Gera os itens pendentes, um por vez. Itens já prontos nesta versão são mantidos.
    `only` refaz um item; `targets` refaz vários, cada um com a sua observação."""
    if only is not None:
        targets = {only: note}
    if targets is not None:
        unknown = [k for k in targets if k not in {i.key for i in items}]
        if unknown:
            raise Blocked("MISSING_INPUT", f"Item desconhecido nesta etapa: {', '.join(unknown)}")
    _prune_gone(ctx, items)
    kept = made = 0
    todo: list[Item] = []
    for item in items:
        if targets is not None and item.key not in targets:
            continue
        recorded = ctx.generated_version(item.key)
        if targets is None and item.key not in ctx.force and item.path.is_file() and not ctx.is_stale(item.key) and (recorded is None or recorded >= ctx.version):
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
            item_note = (targets.get(item.key) if targets is not None else note) or None
            # Ajuste = editar a imagem que está aprovada hoje (ela vai como 1ª referência) e manter todos os ajustes anteriores.
            # Item desatualizado (a imagem da qual ele depende mudou) é refeito a partir das referências novas.
            base = item.path if (item_note and item.kind == "image" and item.path.is_file() and not ctx.is_stale(item.key)) else None
            prompt = adjusted_prompt(item.prompt, ctx.adjustments(item.key), item_note, base is not None)
            refs = item.refs()
            if base is not None:
                refs = [base, *[r for r in refs if Path(r) != base]]
            if item.kind == "image" and item.path.is_file():
                _keep_previous(ctx, item.path)   # a versão de hoje nunca se perde, mesmo que a nova saia pior
            if item.kind == "video":
                info = await ctx.video.generate_video(prompt, refs, item.path, duration_s=item.duration, slot=slot)
            else:
                info = await ctx.images.generate_image(prompt, refs, item.path, slot=slot)
            ctx.mark_generated(item.key)
            if item_note:
                ctx.add_adjustment(item.key, item_note)
                _remember_look(ctx, item, item_note)
            await asyncio.to_thread(ctx.sink.file, ctx.stage_key, item.key, item.name, item.path, item.kind, {**_meta(item), **(info or {})})
            ctx.log(f"Gerado: {item.name}.")
            made += 1
            finished += 1
        except Exception as exc:  # noqa: BLE001 - guarda o primeiro erro; os itens já em andamento terminam sozinhos
            exc = _explain_browser_error(exc)
            if state["error"] is None:
                exc.item = {"key": item.key, "name": item.name, "prompt": item.prompt[:6000], "kind": item.kind}
                state["error"] = exc
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


def adjusted_prompt(prompt: str, earlier: list[str], new: str | None, with_base: bool) -> str:
    """Prompt do item com os ajustes já aprovados e o novo. Com `with_base`, a 1ª imagem anexada é a versão atual e só o novo ajuste muda."""
    earlier = [n for n in earlier if n != new]
    out = prompt
    if with_base:
        out += ("\n\nEDIT MODE: the FIRST attached image is the CURRENT approved version of this image. Keep it EXACTLY as it is "
                "(same character(s), face, hair, outfit, colors, accessories, composition, framing, lighting and style) and change ONLY what the "
                "latest adjustment below asks for. Any other attached image is only a reference.")
    if earlier:
        out += ("\n\nAdjustments the user already approved (they are part of the current look; never undo them):\n"
                + "\n".join(f"{n}. {note}" for n, note in enumerate(earlier, 1)))
    if new:
        out += f"\n\nRequested adjustment (from the user): {new}"
    return out


def _explain_browser_error(exc: Exception) -> Exception:
    """Aba ou janela do Chrome fechada no meio da geração: vira uma parada com explicação, não um erro técnico."""
    if type(exc).__name__ == "TargetClosedError" or "has been closed" in str(exc):
        return Blocked("TAB_CLOSED", "A aba ou a janela do Chrome do worker foi fechada enquanto este item era gerado.")
    return exc


def _prune_gone(ctx: Ctx, items: list[Item]) -> None:
    """Tira do painel as imagens/vídeos que ficaram de um plano anterior (ex.: ambiente que o plano refeito não tem mais),
    para não sobrar item que parece ajustável mas já não existe nesta etapa."""
    prune = getattr(ctx.sink, "prune", None)
    if prune is None:
        return
    try:
        gone = prune(ctx.stage_key, {i.key for i in items})
    except Exception as exc:  # noqa: BLE001 - limpeza do painel nunca derruba a geração
        ctx.log(f"Não consegui limpar itens antigos do painel: {exc}", "warn")
        return
    if gone:
        ctx.log(f"Removidos do painel {len(gone)} item(ns) que não fazem mais parte desta etapa: {', '.join(gone)}.")


def _keep_previous(ctx: Ctx, path: Path) -> None:
    """Guarda a versão atual da imagem em imagens/_anteriores/ antes de ela ser substituída."""
    try:
        folder = ctx.dir / "imagens" / "_anteriores"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = int(path.stat().st_mtime)
        target = folder / f"{path.stem}-{stamp}{path.suffix}"
        if not target.exists():
            shutil.copyfile(path, target)
    except OSError:
        pass


def _remember_look(ctx: Ctx, item: Item, note: str) -> None:
    """Um ajuste na imagem de um personagem ou de um ambiente vira parte da descrição dele, para os prompts de cena e de vídeo
    (que repetem essa descrição) não voltarem à aparência anterior."""
    key = item.key
    source, slug = None, ""
    if key.startswith("img-personagem-") and key.endswith("-corpo"):
        source, slug = "bible", key[len("img-personagem-"):-len("-corpo")]
    elif key.startswith("img-folha-"):
        source, slug = "bible", key[len("img-folha-"):]
    elif key.startswith("img-ambiente-"):
        source, slug = "plan", key[len("img-ambiente-"):]
    if source is None or not ctx.data_path(source).is_file():
        return
    data = ctx.load_data(source)
    entries = data.get("characters" if source == "bible" else "environments") or []
    entry = next((e for e in entries if e.get("slug") == slug), None)
    if entry is None:
        return
    changes = [c for c in entry.get("approved_changes") or [] if c != note] + [note]
    entry["approved_changes"] = changes[-ctx.MAX_ADJUSTMENTS:]
    ctx.save_data(source, data)


def _stale_dependents(ctx: Ctx, changed: list[Item]) -> list[Item]:
    """Itens JÁ GERADOS (de qualquer etapa) que usam, direta ou indiretamente, uma das imagens que acabou de mudar.
    Eles foram feitos com a versão anterior: ficam marcados como desatualizados e são refeitos a partir da nova."""
    everything: list[Item] = []
    for stage_key in ITEM_BUILDERS:
        try:
            everything += items_for(ctx, stage_key)
        except Exception:  # noqa: BLE001 - etapa ainda sem dados (não rodou): não há nada gerado nela
            continue
    refs_of: dict[str, set[Path]] = {}
    for it in everything:
        try:
            refs_of[it.key] = {Path(r).resolve() for r in it.refs()}
        except Exception:  # noqa: BLE001 - referência ainda inexistente
            refs_of[it.key] = set()
    changed_keys = {i.key for i in changed}
    reached = {i.path.resolve() for i in changed}
    stale: list[Item] = []
    grew = True
    while grew:
        grew = False
        for it in everything:
            if it.key in changed_keys or it.path.resolve() in reached:
                continue
            if refs_of[it.key] & reached:
                reached.add(it.path.resolve())
                grew = True
                if it.path.is_file():
                    stale.append(it)
    for it in stale:
        ctx.mark_stale(it.key, ", ".join(i.name for i in changed))
    return stale


def _meta(item: Item) -> dict:
    meta = {"prompt": item.prompt}
    if item.kind == "video":
        try:
            meta["references"] = [p.name for p in item.refs()]
        except Blocked:
            pass
    return meta


STALE_PREFIX = "Desatualizado: "
REUSED = " (da parte anterior)"


def _adopt(ctx: Ctx, key: str, rel: Path, reused: bool) -> str:
    """Continuação: copia da biblioteca da série a imagem que já existe, para ela não ser gerada de novo. Devolve o sufixo do nome."""
    if not reused or not ctx.sequel:
        return ""
    src, dest = Path(ctx.sequel["library"]) / rel, ctx.dir / rel
    if src.is_file() and not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        ctx.mark_generated(key)
    return REUSED if src.is_file() or dest.is_file() else ""


def _reused_count(items: list[Item]) -> str:
    n = sum(1 for i in items if i.name.endswith(REUSED))
    return f" {n} reaproveitada(s) da parte anterior (não geradas de novo)." if n else ""


def _character_items(ctx: Ctx) -> list[Item]:
    bible = ctx.load_data("bible")
    return [Item(f"img-personagem-{c['slug']}-corpo", f"{c['name']} · corpo inteiro" + _adopt(ctx, f"img-personagem-{c['slug']}-corpo", render.body_path(c), c.get("reused", False)),
                 ctx.dir / render.body_path(c), render.full_body_prompt(bible, c), lambda: []) for c in render.visible(bible)]


def _sheet_items(ctx: Ctx) -> list[Item]:
    bible = ctx.load_data("bible")
    return [Item(f"img-folha-{c['slug']}", f"{c['name']} · folha 9:16" + _adopt(ctx, f"img-folha-{c['slug']}", render.sheet_path(c), c.get("reused", False)),
                 ctx.dir / render.sheet_path(c), render.sheet_prompt(bible, c, not ctx.direct_sheets),
                 (lambda: []) if ctx.direct_sheets else (lambda c=c: [ctx.dir / render.body_path(c)])) for c in render.visible(bible)]


def _scene_items(ctx: Ctx) -> list[Item]:
    bible, plan = ctx.load_data("bible"), ctx.load_data("plan")
    chars = {c["name"]: c for c in bible["characters"]}
    envs = {e["name"]: e for e in plan["environments"]}
    items = [Item(f"img-ambiente-{e['slug']}", f"Ambiente · {e['name']}" + _adopt(ctx, f"img-ambiente-{e['slug']}", render.env_path(e), e.get("reused", False)), ctx.dir / render.env_path(e),
                  render.environment_prompt(bible, e), lambda: []) for e in plan["environments"]]   # onda 0: as cenas usam estes ambientes
    for s in plan["scenes"]:
        refs = [ctx.dir / render.sheet_path(chars[n]) for n in s["characters"]] + [ctx.dir / render.env_path(envs[s["environment"]])]
        items.append(Item(f"img-cena-{s['id']}", f"Cena {s['id']} · imagem-base", ctx.dir / render.scene_path(s["id"]),
                          render.scene_prompt(bible, plan, s), lambda refs=refs: refs, wave=1))
    return items


def _clip_items(ctx: Ctx) -> list[Item]:
    bible, clips = ctx.load_data("bible"), ctx.load_data("clips")
    catalog = render.image_catalog(bible, ctx.load_data("plan"), ctx.direct_sheets)

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
    items = _imported_items(ctx, stage_key) if ctx.plan and ctx.plan.get(stage_key) else _BUILDERS[stage_key](ctx)
    for item in items:   # o usuário pode ter editado o prompt de um item depois de ele falhar ou de ser aprovado
        edited = (ctx.overrides or {}).get(item.key)
        if isinstance(edited, str) and edited.strip():
            item.prompt = edited.strip()
    return items


ITEM_BUILDERS = _BUILDERS


def _imported_next(ctx: Ctx) -> dict:
    """Próximo lote autorizado pela aprovação, num projeto com prompts prontos (as etapas sem itens foram puladas)."""
    units = {"character_images": ("ChatGPT", "imagens de personagens"), "character_sheets": ("ChatGPT", "folhas 9:16"),
             "scene_images": ("ChatGPT", "imagens de ambiente e cena"), "production": ("Grok", "vídeos")}
    for key in ORDER[ORDER.index(ctx.stage_key) + 1:]:
        if key in units and ctx.plan.get(key):
            return {"next_generation": {"service": units[key][0], "count": len(ctx.plan[key]), "unit": units[key][1]}}
    return {}


async def character_images(ctx: Ctx) -> dict:
    items = items_for(ctx, "character_images")
    stats = await generate(ctx, items, note=ctx.feedback)
    nxt = _imported_next(ctx) if ctx.plan else {"next_generation": {"service": "ChatGPT", "count": len(items), "unit": "folhas 9:16"}}
    return {"summary": f"{len(items)} imagem(ns) de corpo inteiro ({stats['made']} gerada(s) agora).{_reused_count(items)}", **nxt}


async def character_sheets(ctx: Ctx) -> dict:
    items = items_for(ctx, "character_sheets")
    stats = await generate(ctx, items, note=ctx.feedback)
    return {"summary": f"{len(items)} folha(s) 9:16 ({stats['made']} gerada(s) agora).{_reused_count(items)}", **(_imported_next(ctx) if ctx.plan else {})}


# ---------------------------------------------------------------------------
# 6 · Ambientes e cenas (texto)  ·  7 · imagens
# ---------------------------------------------------------------------------
async def visual_plan(ctx: Ctx) -> dict:
    bible = ctx.load_data("bible")
    prompt = prompts.visual_plan(ctx.read_doc("03"), bible, _previous_data(ctx, "plan"), ctx.feedback, ctx.clone, ctx.sequel, ctx.story, ctx.read_doc("01") if ctx.clone else None)
    ctx.log("Pedindo ambientes e imagens-base ao Claude…")
    plan = await ask_json(ctx.brain, prompt, lambda raw: validate_plan(raw, bible, int(ctx.story["min_scenes"]) if ctx.story else 0))
    reused = adopt_plan(plan, ctx.sequel)
    if ctx.data_path("plan").is_file():
        carry_changes(plan, ctx.load_data("plan"), "environments")
    ctx.save_data("plan", plan)
    ctx.write_doc("07", render.doc07(bible, plan))
    count = len([e for e in plan["environments"] if not e.get("reused")]) + len(plan["scenes"])
    note = f" Ambientes reaproveitados da parte anterior: {', '.join(reused)}." if reused else ""
    return {"summary": f"{len(plan['environments'])} ambiente(s) e {len(plan['scenes'])} cena(s) planejados nas oito áreas.{note}",
            "next_generation": {"service": "ChatGPT", "count": count, "unit": "imagens de ambiente e cena"}}


async def scene_images(ctx: Ctx) -> dict:
    items = items_for(ctx, "scene_images")
    stats = await generate(ctx, items, note=ctx.feedback)
    return {"summary": f"{len(items)} imagem(ns) de ambiente e cena ({stats['made']} gerada(s) agora).{_reused_count(items)}", **(_imported_next(ctx) if ctx.plan else {})}


# ---------------------------------------------------------------------------
# 8 · Prompts de vídeo, edição e publicação (texto)  ·  9 · vídeos no Grok
# ---------------------------------------------------------------------------
async def video_prompts(ctx: Ctx) -> dict:
    bible, plan = ctx.load_data("bible"), ctx.load_data("plan")
    prev09, prev10 = ctx.previous("09"), ctx.previous("10")
    catalog = render.image_catalog(bible, plan, ctx.direct_sheets)
    prompt = prompts.clips(ctx.read_doc("02"), ctx.read_doc("03"), bible, plan, ctx.max_clip, _previous_data(ctx, "clips"), ctx.feedback, catalog, ctx.clone, ctx.story, ctx.read_doc("01") if ctx.clone else None)
    ctx.log("Pedindo os prompts de vídeo ao Claude…")
    if ctx.story:   # duração e quantidade de cenas escolhidas pelo usuário (os clipes só têm 6, 10 ou 15 s, então há uma folga)
        target, scenes = int(ctx.story["target_s"]), int(ctx.story["min_scenes"])
        check = lambda raw: validate_clips(raw, bible, plan, ctx.max_clip, set(catalog), target - 4, target + 10, scenes)  # noqa: E731
    else:
        check = lambda raw: validate_clips(raw, bible, plan, ctx.max_clip, set(catalog), 0 if ctx.clone else 70)  # noqa: E731
    clips = await ask_json(ctx.brain, prompt, check)
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
    if ctx.profile:   # Fábrica de roteiros: o alvo é o do perfil (os clipes são de 6 ou 10 s; o excesso é cortado na edição)
        warnings = [] if total >= ctx.profile["min_s"] else [f"A soma real dos clipes é {total} s, abaixo da meta mínima de {ctx.profile['min_s']} s do perfil. Regenere clipes ou ajuste cenas."]
    elif ctx.story:
        target = int(ctx.story["target_s"])
        warnings = [] if abs(total - target) <= max(8, target * 0.2) else [f"A soma real dos clipes é {total} s, longe da meta de {target} s. Regenere ou ajuste clipes antes de editar."]
    else:
        warnings = [] if (total > 70 or ctx.plan or ctx.clone) else [f"A soma real dos clipes é {total} s, abaixo da meta de 70 s. Regenere clipes mais longos ou acrescente cenas."]
    return {"summary": f"{len(items)} clipe(s), {total} s no total ({stats['made']} gerado(s) agora). " + ("Próximo passo: editar (cortar o excesso de cada clipe, legendas e música)." if ctx.profile else "Próximo passo: editar no CapCut conforme o Documento 09."),
            "warnings": warnings}


HANDLERS: dict[str, Callable] = {
    "analysis": analysis, "script": script, "character_bible": character_bible, "character_images": character_images,
    "character_sheets": character_sheets, "visual_plan": visual_plan, "scene_images": scene_images,
    "video_prompts": video_prompts, "production": production,
}


HANDLERS.update(factory_stages.HANDLERS)   # Fábrica de roteiros + workflow de animação (modo "factory")


async def run_stage(ctx: Ctx) -> dict:
    handler = HANDLERS.get(ctx.stage_key)
    if handler is None:
        raise ValueError(f"Etapa desconhecida: {ctx.stage_key}")
    return await handler(ctx)


async def regen_item(ctx: Ctx, item_key: str, comment: str | None) -> dict:
    return await regen_items(ctx, {item_key: comment})


async def regen_items(ctx: Ctx, targets: dict[str, str | None]) -> dict:
    """Refaz só os itens indicados, cada um com a sua observação."""
    if ctx.stage_key not in ITEM_BUILDERS:
        raise Blocked("MISSING_INPUT", "Esta etapa não tem itens regeneráveis.")
    items = items_for(ctx, ctx.stage_key)
    valid = {i.key for i in items}
    skipped = [k for k in targets if k not in valid]
    if skipped and len(skipped) == len(targets):
        skipped = []   # nada válido: o generate() explica o erro
    else:   # itens de um plano anterior não derrubam o pedido dos que ainda existem
        targets = {k: v for k, v in targets.items() if k in valid}
    await generate(ctx, items, targets=targets)
    names = ", ".join(targets)
    result = {"summary": f"Item regenerado: {names}." if len(targets) == 1 else f"{len(targets)} itens regenerados: {names}."}
    if skipped:
        note = f"Ignorados {len(skipped)} item(ns) que não existem mais nesta etapa (de um plano anterior): {', '.join(skipped)}."
        ctx.log(note, "warn")
        result["warnings"] = [note]
    stale = _stale_dependents(ctx, [i for i in items if i.key in targets])
    if stale:
        listed = ", ".join(i.name for i in stale[:12]) + (f" e mais {len(stale) - 12}" if len(stale) > 12 else "")
        warning = (f"{STALE_PREFIX}{len(stale)} item(ns) de etapas seguintes foram feitos com a versão anterior desta imagem: {listed}. "
                   "Eles serão refeitos com a nova versão ao rodar a etapa deles (ou use Ajustar neles).")
        ctx.log(warning, "warn")
        result["warnings"] = [*result.get("warnings", []), warning]
    return result
