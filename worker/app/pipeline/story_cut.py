"""Encurtar ou mudar a história na etapa de prompts de vídeo, sem gerar nenhuma imagem nova.

O Claude escolhe quais das cenas que já têm imagem aprovada entram no vídeo. Os clipes já escritos dessas cenas são
reaproveitados; só as cenas que precisam mudar para a história continuar fazendo sentido são reescritas.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from .. import config
from ..media import slugify
from . import prompts
from .core import Ctx, ask_json, validate_clips


def signature(ctx: Ctx, feedback: str = "") -> str:
    """Muda quando muda algo que entra nos prompts: aí os clipes já escritos não servem mais."""
    parts = [ctx.read_doc("02"), ctx.read_doc("03"), ctx.read_doc("01") if ctx.clone else "", ctx.load_data("bible"),
             ctx.load_data("plan"), feedback, ctx.max_clip]
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read(ctx: Ctx, name: str) -> dict:
    return ctx.load_data(name) if ctx.data_path(name).is_file() else {}


def save_full(ctx: Ctx, clips: list[dict]) -> None:
    """Guarda os clipes da história inteira: é deles que os cortes partem e é para eles que se volta."""
    ctx.save_data("clips-completo", {"signature": signature(ctx), "clips": clips})


def full_story(ctx: Ctx, plan: dict) -> list[dict]:
    """Os clipes da história inteira já escritos neste PC que ainda valem para o roteiro e as imagens atuais
    (podem cobrir só parte das cenas, quando a escrita parou no meio)."""
    current = signature(ctx)
    saved = _read(ctx, "clips-completo")
    if saved.get("signature") == current:
        return saved["clips"]
    partial = _read(ctx, "clips-parcial")
    if partial.get("signature") == current:
        batches = partial.get("batches") or {}
        return [c for i in sorted(batches, key=int) for c in batches[i]["clips"]]
    previous = _read(ctx, "clips")
    if not saved and not previous.get("cut"):   # projeto escrito antes de existir a cópia da história inteira
        ids = {s["id"] for s in plan["scenes"]}
        return [c for c in previous.get("clips") or [] if c.get("scene") in ids]
    return []


def by_scene(clips: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for clip in clips:
        out.setdefault(clip["scene"], []).append(clip)
    return out


def assemble(groups: list[tuple[str, str, list[dict]]]) -> list[dict]:
    """Junta os clipes na ordem do vídeo novo e renumera. `groups`: (cena, origem, clipes). "Continuar do clipe anterior" só vale
    se o anterior continuar sendo o mesmo; senão o clipe começa na imagem-base da própria cena."""
    out: list[dict] = []
    before: tuple[str, str, int] | None = None
    for scene, origin, clips in groups:
        for index, original in enumerate(clips):
            clip = dict(original)
            follows = before is not None and before[:2] == (scene, origin) and (origin == "new" or before[2] == original.get("n", 0) - 1)
            if clip["start"] == "previous_last_frame" and not follows:
                clip["start"] = "scene_image"
                base = f"cena-{scene}"
                clip["reference_images"] = [base, *[r for r in clip.get("reference_images") or [] if r != base]][:3]
            before = (scene, origin, original.get("n", index))
            clip["n"] = len(out) + 1
            out.append(clip)
    return out


def _selection_check(ids: list[str], seconds: dict[str, int], target: int | None) -> Callable:
    def check(raw: dict, lenient: bool = False) -> dict:
        entries = raw.get("scenes")
        if not isinstance(entries, list) or not entries:
            raise ValueError('"scenes" precisa ser a lista das cenas que ficam, na ordem do vídeo')
        kept: list[str] = []
        changes: dict[str, str] = {}
        for entry in entries:
            entry = entry if isinstance(entry, dict) else {"id": entry}
            scene = slugify(str(entry.get("id") or ""), 12)
            if scene not in ids:
                raise ValueError(f'cena "{entry.get("id")}" não existe; use só os ids da lista (não há cenas novas)')
            if scene in kept:
                raise ValueError(f'cena "{scene}" repetida')
            kept.append(scene)
            change = str(entry.get("change_pt") or "").strip()
            if change:
                changes[scene] = change
        total = sum(seconds[s] for s in kept)
        slack = max(12, round((target or 0) * 0.15))
        if target and abs(total - target) > slack and not lenient:
            raise ValueError(f"as cenas escolhidas somam {total} s; precisam somar entre {target - slack} e {target + slack} s. "
                             + ("Tire cenas." if total > target else "Mantenha mais cenas."))
        return {"summary_pt": str(raw.get("summary_pt") or "").strip(), "scenes": kept, "changes": changes}
    return check


async def cut(ctx: Ctx, bible: dict, plan: dict, catalog: dict, forgiving: Callable) -> dict:
    """Devolve os clipes do vídeo com a história ajustada. Cada passo pronto fica em dados/corte.json: uma nova tentativa continua dali."""
    request = ctx.story_cut or {}
    base = full_story(ctx, plan)
    if base:
        save_full(ctx, base)
    written_before = by_scene(base)
    scenes = {s["id"]: s for s in plan["scenes"]}
    seconds = {sid: sum(c["duration_s"] for c in written_before.get(sid, [])) or 6 for sid in scenes}
    key = hashlib.sha1((signature(ctx) + json.dumps(request, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
    state = _read(ctx, "corte")
    if state.get("key") != key:
        state = {"key": key, "previous": state.get("selection")}
    if "selection" not in state:
        rows = []
        for sid, scene in scenes.items():
            said = " / ".join(c["dialogue_de"].replace("\n", " / ") for c in written_before.get(sid, []) if c.get("dialogue_de"))
            rows.append(f"{sid} | {seconds[sid]} s | {scene['environment']} · {', '.join(scene['characters']) or 'sem personagens'} | "
                        f"{scene.get('shot_pt', '')}" + (f" | falas: {said}" if said else ""))
        ctx.log(f"Pedindo ao Claude quais das {len(scenes)} cenas ficam na história…")
        state["selection"] = await ask_json(ctx.brain, prompts.story_selection(ctx.read_doc("03"), "\n".join(rows), request.get("target_s"),
                                                                              request.get("instruction"), state.get("previous"), ctx.clone),
                                            forgiving(_selection_check(list(scenes), seconds, request.get("target_s"))))
        ctx.save_data("corte", state)
    selection = state["selection"]
    kept, changes = selection["scenes"], selection["changes"]

    round_ = [ctx.feedback or "", ctx.version if ctx.feedback else 0]   # um ajuste novo reescreve; "tentar novamente" aproveita
    if state.get("round") != round_:
        state["round"], state["written"] = round_, {}
    written: dict = state["written"]
    need = [sid for sid in kept if ctx.feedback or sid in changes or sid not in written_before]
    pending = [sid for sid in need if sid not in written]
    size = config.CLIP_BATCH_SCENES
    batches = [pending[i:i + size] for i in range(0, len(pending), size)]
    for number, wanted in enumerate(batches, 1):
        ctx.sink.progress(ctx.stage_key, number - 1, len(batches), f"Reescrevendo as cenas {', '.join(wanted[:6])}…")
        ctx.log(f"Reescrevendo os prompts das cenas que mudaram · lote {number} de {len(batches)} ({len(wanted)} cena(s))…")
        group = [scenes[s] for s in wanted]
        usable = {k: v for k, v in catalog.items() if not k.startswith("cena-") or k[5:] in wanted}
        around: dict[str, str] = {}
        for sid in wanted:
            at = kept.index(sid)
            for near in kept[max(0, at - 1):at + 2]:
                clips = written.get(near) or written_before.get(near) or []
                around[near] = clips[0]["prompt"] if clips else scenes[near].get("shot_pt", "")
        old = [c for s in wanted for c in written_before.get(s, [])]
        prompt = prompts.clips(ctx.read_doc("02"), ctx.read_doc("03"), bible, {**plan, "scenes": group}, ctx.max_clip,
                               json.dumps({"clips": old}, ensure_ascii=False) if ctx.feedback and old else None, ctx.feedback, usable,
                               ctx.clone, None, ctx.read_doc("01") if ctx.clone else None)
        prompt += prompts.cut_clips(selection["summary_pt"], request.get("instruction"), wanted, changes,
                                    [(s, around[s]) for s in kept if s in around], kept[0], kept[-1])

        def check(raw, lenient, group=group, wanted=wanted, usable=usable):
            mine = [c for c in raw.get("clips") or [] if isinstance(c, dict) and slugify(str(c.get("scene") or ""), 12) in wanted]
            out = validate_clips({**raw, "clips": mine}, bible, {**plan, "scenes": group}, ctx.max_clip, set(usable), 0, lenient=lenient)
            missing = [s for s in wanted if s not in {c["scene"] for c in out["clips"]}]
            if missing:
                raise ValueError(f"faltam clipes para as cenas {', '.join(missing)}; cada cena precisa de pelo menos um")
            return out

        result = await ask_json(ctx.brain, prompt, forgiving(check))
        written.update(by_scene(result["clips"]))
        ctx.save_data("corte", state)

    clips = assemble([(sid, "new" if sid in need else "base", written[sid] if sid in need else written_before[sid]) for sid in kept])
    info = {"summary_pt": selection["summary_pt"], "instruction": request.get("instruction"), "target_s": request.get("target_s"),
            "scenes": kept, "total_scenes": len(scenes), "rewritten": len(need)}
    return {"order_note_pt": "Na ordem numérica dos clipes.", "clips": clips, "total_s": sum(c["duration_s"] for c in clips), "cut": info}
