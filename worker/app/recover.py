"""Resgate de projetos: recria no Supabase os projetos cujas pastas ainda existem neste PC.

Quando o banco é trocado, os projetos somem do painel, mas tudo o que o worker produziu continua em
runtime/projects: documentos .md, JSONs de dados, imagens e vídeos. Este comando lê cada pasta e recria
o projeto, as etapas e os arquivos publicados no painel, com o MESMO id que a pasta já guarda
(`.projeto-id`), então nada precisa ser movido ou renomeado.

    python -m app.recover                 # só mostra o que seria recriado (não grava nada)
    python -m app.recover --apply         # recria no Supabase
    python -m app.recover --apply --only "Pai vagabundo"      # só os projetos cujo título contém o texto
    python -m app.recover --apply --replace                   # recria de novo os que já existem no banco

O status das etapas não ficou guardado na pasta, então é deduzido do que existe nela:
  · etapa anterior a outra que já tem resultado  -> aprovada;
  · última etapa com resultado completo           -> em revisão (você aprova de novo, e só então a seguinte começa);
  · última etapa com itens faltando               -> parada, com "Tentar novamente" para gerar só o que falta;
  · as demais                                     -> bloqueadas.
Nada é enfileirado: o resgate nunca chama Claude, ChatGPT nem Grok.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, folders
from .db import SupabaseSink
from .errors import Blocked
from .pipeline import stages
from .pipeline.core import DOCS, Ctx, LocalSink

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Etapas de um projeto novo (as imagens de corpo inteiro só existem nos projetos mais antigos).
LAYOUT = [("analysis", 1), ("script", 2), ("character_bible", 3), ("character_sheets", 5), ("visual_plan", 6),
          ("scene_images", 7), ("video_prompts", 8), ("production", 9)]
LAYOUT_OLD = [*LAYOUT[:3], ("character_images", 4), *LAYOUT[3:]]

DOC_STAGES = {"analysis": ["01", "02"], "script": ["03"], "character_bible": ["04", "05", "06"], "visual_plan": ["07"], "video_prompts": ["08", "09", "10"]}
DATA_FILES = {"character_bible": "bible", "visual_plan": "plan", "video_prompts": "clips"}
ITEM_STAGES = ("character_images", "character_sheets", "scene_images", "production")
IMPORT_DIRS = stages.IMPORT_DIRS


@dataclass
class Piece:
    key: str
    name: str
    path: Path
    kind: str            # image | video | evidence
    meta: dict = field(default_factory=dict)


@dataclass
class StagePlan:
    key: str
    order: int
    title: str
    status: str
    version: int
    payload: dict
    docs: list[str]
    pieces: list[Piece]
    expected: int = 0   # itens que a etapa deveria ter (só etapas de geração)


@dataclass
class ProjectPlan:
    folder: Path
    id: str
    title: str
    mode: str
    source: str | None
    part: int
    parent_title: str | None
    story_idea: str | None
    target_seconds: int | None
    min_scenes: int | None
    clone: bool
    old_layout: bool
    stages: list[StagePlan]
    current_stage: str
    created_at: str
    updated_at: str
    notes: list[str] = field(default_factory=list)
    parent_id: str | None = None

    @property
    def files(self) -> int:
        return sum(len(s.pieces) for s in self.stages)


# ---------------------------------------------------------------------------
# Leitura da pasta
# ---------------------------------------------------------------------------
def folder_id(folder: Path) -> str | None:
    marker = folder / folders.MARKER
    try:
        text = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    except OSError:
        text = ""
    if UUID_RE.match(text):
        return text.lower()
    return folder.name.lower() if UUID_RE.match(folder.name) else None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _mtimes(folder: Path) -> tuple[float, float]:
    oldest = newest = None
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                t = (Path(root) / name).stat().st_mtime
            except OSError:
                continue
            oldest = t if oldest is None else min(oldest, t)
            newest = t if newest is None else max(newest, t)
    now = datetime.now(timezone.utc).timestamp()
    return oldest or now, newest or now


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _title(folder: Path, pid: str) -> str:
    if not UUID_RE.match(folder.name):
        return re.sub(r" \(\d+\)$", "", folder.name).strip() or folder.name   # "Nome (2)" é só a pasta de um nome repetido
    caption = ""
    data = _read_json(folder / "referencia" / "referencia_tikwm.json").get("data")
    if isinstance(data, dict):
        caption = re.sub(r"#\S+", "", str(data.get("title") or "")).strip()
    return f"Recuperado · {caption[:50].strip() or pid[:8]}"


def _has_stage_files(folder: Path) -> bool:
    return any((folder / n).is_file() for n, _ in DOCS.values()) or any(
        (folder / sub).is_dir() and any(p.is_file() for p in (folder / sub).rglob("*")) for sub in ("imagens", "videos"))


def _evidence(folder: Path) -> list[Piece]:
    ref = folder / "referencia"
    data = _read_json(ref / "evidencias.json")
    out: list[Piece] = []
    groups = [("sheets_hook", "ev-folha-gancho-{i:02d}", "Gancho quadro a quadro (primeiros 4 s)"),
              ("sheets_end", "ev-folha-final-{i:02d}", "Fecho / CTA quadro a quadro (últimos 4 s)"),
              ("sheets_1s", "ev-folha-1s-{i:02d}", "Quadros a cada 1 s ({i}/{n})"),
              ("sheets_cuts", "ev-folha-cortes-{i:02d}", "Candidatos a corte ({i}/{n})")]
    if not data:   # sem o índice: usa o que está na pasta
        names = sorted(p.name for p in ref.glob("folha-*.jpg")) if ref.is_dir() else []
        data = {"sheets_hook": [n for n in names if n.startswith("folha-gancho")], "sheets_end": [n for n in names if n.startswith("folha-final")],
                "sheets_1s": [n for n in names if n.startswith("folha-1s")], "sheets_cuts": [n for n in names if n.startswith("folha-cortes")]}
    videos = data.get("videos")   # vários vídeos de referência: cada um na sua pasta ref-N, com chaves ev-vN-…
    sets = [(ref / f"ref-{v}", f"ev-v{v}-", f"Vídeo {v} · ", entry) for v, entry in enumerate(videos, 1)] if videos else [(ref, "ev-", "", data)]
    for folder_of, prefix, title, entry in sets:
        for field_name, key, label in groups:
            names = [n for n in entry.get(field_name) or [] if isinstance(n, str)]
            for i, name in enumerate(names, 1):
                path = folder_of / name
                if path.is_file():
                    out.append(Piece(key.replace("ev-", prefix, 1).format(i=i), title + label.format(i=i, n=len(names)), path, "evidence"))
    return out


def _item_pieces(folder: Path, stage_key: str, old_layout: bool, clone: bool, generated: dict) -> tuple[list[Piece], int]:
    """Itens da etapa que existem na pasta e o total esperado. Usa os mesmos construtores do worker, então chaves e nomes são os de sempre."""
    ctx = Ctx(project_id="recover", source="", dir=folder, sink=LocalSink(), brain=None, images=None, video=None, stage_key=stage_key,
              direct_sheets=not old_layout, clone=clone)
    try:
        items = stages.items_for(ctx, stage_key)
    except (Blocked, KeyError, TypeError, ValueError):
        items = []
    pieces: list[Piece] = []
    if items:
        for item in items:
            if not item.path.is_file():
                continue
            try:
                meta = stages._meta(item)
            except Exception:  # noqa: BLE001 - as referências do clipe são um extra
                meta = {"prompt": item.prompt}
            pieces.append(Piece(item.key, item.name, item.path, item.kind, meta))
        return pieces, len(items)
    # Projeto iniciado com prompts prontos: o plano importado não ficou na pasta, mas as imagens e as chaves sim.
    prefix = f"imp-{stage_key}-"
    sub = IMPORT_DIRS.get(stage_key)
    if sub and stage_key != "production":
        for key in generated:
            if key.startswith(prefix):
                path = folder / "imagens" / sub / f"{key[len(prefix):]}.png"
                if path.is_file():
                    pieces.append(Piece(key, path.stem, path, "image", {}))
    return pieces, len(pieces)


# ---------------------------------------------------------------------------
# Plano de um projeto
# ---------------------------------------------------------------------------
def _stage_title(key: str, *, story: bool, clone: bool, sequel: bool, has_ref: bool) -> str:
    if key == "analysis":
        if story and not sequel:
            return "Briefing da história"
        if sequel:
            return "Análise da parte 2 da referência" if has_ref else "Resumo da parte anterior"
        return "Análise da referência"
    if key == "script":
        if sequel:
            return "Roteiro da continuação (fiel)" if clone and has_ref else "Roteiro da continuação"
        return "Roteiro fiel ao original" if clone else "Roteiro em alemão"
    return {"character_bible": "Personagens e vozes (texto)", "character_images": "Imagens dos personagens",
            "character_sheets": "Folhas dos personagens 9:16", "visual_plan": "Ambientes e cenas (texto)",
            "scene_images": "Imagens de ambientes e cenas", "video_prompts": "Prompts de vídeo, edição e publicação",
            "production": "Vídeos no Grok"}[key]


def plan_project(folder: Path) -> ProjectPlan | None:
    pid = folder_id(folder)
    if pid is None or not folder.is_dir() or not _has_stage_files(folder):
        return None
    notes: list[str] = []
    generated = _read_json(folder / "dados" / "gerados.json")
    evidence = _read_json(folder / "referencia" / "evidencias.json")
    has_ref = (folder / "referencia" / "referencia.mp4").is_file() or bool(evidence)
    doc3 = _read_text(folder / DOCS["03"][0])
    clone = has_ref and bool(re.search(r"Mapa original|^## 7\.\s*Proposta de troca", doc3, re.M))
    previous_story = _read_text(folder / "historia-anterior.md")
    part = max(2, len(re.findall(r"^# PARTE", previous_story, re.M)) + 1) if previous_story.strip() else 1
    title = _title(folder, pid)
    sequel = part > 1
    imported = not has_ref and not sequel and not any((folder / DOCS[n][0]).is_file() for n in ("01", "03"))   # só imagens: veio de "já tenho os prompts"
    story = not has_ref and not sequel and not imported
    parent_title = None
    if sequel:
        base = re.sub(r"\s*·?\s*Parte \d+$", "", title, flags=re.I).strip()
        parent_title = base if base and base.casefold() != title.casefold() else None

    source = None
    if has_ref:
        source = str(evidence.get("source") or "").strip() or str(folder / "referencia" / "referencia.mp4")
    if story:
        mode = "story"
    elif clone:
        mode = "clone"
    else:
        mode = "inspired"

    body_dir = folder / "imagens" / "personagens"   # projetos mais antigos geravam o corpo inteiro antes da folha
    old_layout = any(k.startswith("img-personagem-") for k in generated) or (body_dir.is_dir() and any(body_dir.iterdir()))
    layout = LAYOUT_OLD if old_layout else LAYOUT

    plan_data, clips_data = _read_json(folder / "dados" / "plan.json"), _read_json(folder / "dados" / "clips.json")
    story_idea = target = scenes = None
    if story:
        story_idea = (_read_text(folder / DOCS["01"][0]).strip() or "História recuperada da pasta do projeto.")[:6000]
        total = clips_data.get("total_s")
        target = int(min(300, max(15, round(total)))) if isinstance(total, (int, float)) and total else 60
        n = len(plan_data.get("scenes") or [])
        scenes = int(min(60, max(1, n))) if n else 1

    built: list[StagePlan] = []
    states: list[tuple] = []
    for key, order in layout:
        docs = [n for n in DOC_STAGES.get(key, []) if (folder / DOCS[n][0]).is_file()]
        pieces: list[Piece] = []
        expected = 0
        if key == "analysis":
            pieces = _evidence(folder)
        elif key in ITEM_STAGES:
            pieces, expected = _item_pieces(folder, key, old_layout, clone, generated)
        required = DOC_STAGES.get(key, [])
        missing_docs = [n for n in required if n not in docs]
        data_ok = key not in DATA_FILES or (folder / "dados" / f"{DATA_FILES[key]}.json").is_file()
        has_output = bool(docs) or (key in ITEM_STAGES and bool(pieces))
        complete = has_output and not missing_docs and data_ok and (key not in ITEM_STAGES or len(pieces) == expected)
        version = max([1, *[int(generated[p.key]) for p in pieces if isinstance(generated.get(p.key), int)]])
        built.append(StagePlan(key, order, _stage_title(key, story=story, clone=clone, sequel=sequel, has_ref=has_ref), "locked", version,
                               {"local_dir": str(folder), "recovered": True}, docs, pieces, expected))
        states.append((has_output, complete, expected, missing_docs, data_ok))

    last = max((i for i, state in enumerate(states) if state[0]), default=0)
    for i, (s, state) in enumerate(zip(built, states)):
        if i < last:
            s.status = "approved"
            s.payload["summary"] = "Recuperada da pasta do projeto (já estava concluída antes da troca do banco)."
        elif i == last:
            _finish_last(s, *state)
        else:
            s.status = "locked"

    if imported:
        notes.append("Projeto iniciado com prompts prontos: os arquivos voltam, mas o plano de prompts não ficou na pasta (regerar itens precisa que você envie os prompts de novo).")
    if story:
        notes.append("Modo história: a ideia original não ficou na pasta; o Documento 01 foi usado como ideia, e a duração/as cenas foram deduzidas dos dados.")
    if has_ref and not evidence.get("source"):
        notes.append("O link original não ficou salvo; a referência aponta para o MP4 desta pasta.")
    oldest, newest = _mtimes(folder)
    return ProjectPlan(folder=folder, id=pid, title=title, mode=mode, source=source, part=part, parent_title=parent_title, story_idea=story_idea,
                       target_seconds=target, min_scenes=scenes, clone=clone, old_layout=old_layout, stages=built, current_stage=built[last].key,
                       created_at=_iso(oldest), updated_at=_iso(newest), notes=notes)


def _finish_last(s: StagePlan, has_output: bool, complete: bool, expected: int, missing_docs: list[str], data_ok: bool) -> None:
    """Status da última etapa que tem resultado: o que existe na pasta diz até onde o projeto foi."""
    have = len(s.pieces)
    wait = "O status que você tinha dado não ficou guardado: confira e aprove se estiver tudo certo (a próxima etapa só começa depois disso)."
    if not has_output:   # projeto sem nada: fica na primeira etapa, parada
        s.status, s.payload["summary"] = "failed", "Nada desta etapa foi encontrado na pasta."
        s.payload["error"] = {"code": "RECOVERED_EMPTY", "message": "Projeto recuperado sem resultados desta etapa.", "action": "Clique em Tentar novamente.", "blocked": False}
        return
    if s.key == "production":
        if expected > 1:
            s.payload["pilot_approved"] = have > 1
        if have == 1 and expected > 1:
            s.status = "pending_review"
            s.payload.update(pilot=True, pilot_remaining=expected - 1, summary=f"Clipe de teste recuperado. Aprovar libera os outros {expected - 1} clipes no Grok.", warnings=[wait])
            return
    if complete:
        s.status = "pending_review"
        s.payload["summary"] = f"Recuperada da pasta do projeto: {len(s.docs)} documento(s), {have} arquivo(s)."
        s.payload["warnings"] = [wait]
        return
    s.status = "failed"
    if missing_docs or not data_ok:
        what = ("documento(s) " + ", ".join(missing_docs)) if missing_docs else "os dados da etapa"
        message = f"Recuperada da pasta, mas faltam {what}."
        action = "Clique em Tentar novamente para refazer esta etapa."
    else:
        message = f"Recuperada da pasta: {have} de {expected} itens estavam prontos."
        action = "Clique em Tentar novamente: só os itens que faltam serão gerados (os que existem são mantidos)."
    s.payload["summary"] = message
    s.payload["error"] = {"code": "RECOVERED_PARTIAL", "message": message, "action": action, "blocked": False}


def scan(projects_dir: Path) -> tuple[list[ProjectPlan], list[str]]:
    plans: list[ProjectPlan] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for folder in sorted(p for p in projects_dir.iterdir() if p.is_dir()) if projects_dir.is_dir() else []:
        plan = plan_project(folder)
        if plan is None:
            skipped.append(f"{folder.name}: {'vazia, nada para recuperar' if folder_id(folder) else 'sem o id do projeto'}")
        elif plan.id in seen:
            skipped.append(f"{folder.name}: id repetido ({plan.id[:8]}) em outra pasta")
        else:
            seen.add(plan.id)
            plans.append(plan)
    by_title = {p.title.casefold(): p for p in plans}
    for p in plans:   # liga a parte 2 ao projeto anterior quando o título deixa claro qual é
        parent = by_title.get(p.parent_title.casefold()) if p.parent_title else None
        if parent is not None and parent.part < p.part:
            p.parent_id = parent.id
        elif p.part > 1:
            p.notes.append("Sem projeto anterior para ligar: a continuação mantém a história guardada na pasta, mas regenerar textos não terá o contexto da série.")
    plans.sort(key=lambda p: (p.part, p.created_at))
    return plans, skipped


# ---------------------------------------------------------------------------
# Gravação no Supabase
# ---------------------------------------------------------------------------
def apply_project(db, plan: ProjectPlan, owner_id: str, *, replace: bool = False, log=print) -> dict:
    if db.exists("projects", plan.id):
        if not replace:
            log("  já existe no banco; pulando (use --replace para recriar)")
            return {"skipped": True}
        db.delete_project(plan.id)
    parent = plan.parent_id if plan.parent_id and db.exists("projects", plan.parent_id) else None   # com --only o anterior pode não estar no banco
    db.insert_rows("projects", [{
        "id": plan.id, "owner_id": owner_id, "title": plan.title, "source_url": plan.source, "status": "active", "current_stage": plan.current_stage,
        "mode": plan.mode, "story_idea": plan.story_idea, "target_seconds": plan.target_seconds, "min_scenes": plan.min_scenes,
        "parent_id": parent, "part": plan.part, "created_at": plan.created_at, "updated_at": plan.updated_at}])
    rows = db.insert_rows("project_stages", [{
        "project_id": plan.id, "stage_key": s.key, "stage_order": s.order, "title": s.title, "status": s.status, "current_version": s.version,
        "payload": s.payload, **({"submitted_at": plan.updated_at} if s.status in ("pending_review", "approved", "failed") else {}),
        **({"approved_at": plan.updated_at} if s.status == "approved" else {})} for s in plan.stages])
    by_key = {r["stage_key"]: r for r in rows}
    project = {"id": plan.id}
    cache = plan.folder / "dados" / "publicados.json"
    if cache.is_file():   # o cache diz "já publicado", mas foi no banco antigo: guarda como está e publica tudo de novo
        backup = cache.with_name("publicados.antigo.json")
        if not backup.exists():
            cache.rename(backup)
        else:
            cache.unlink()
    failed: list[str] = []
    for s in plan.stages:
        if not s.docs and not s.pieces:
            continue
        sink = SupabaseSink(db, project, by_key[s.key], plan.folder)
        for number in s.docs:
            sink.document(s.key, f"doc-{number}", DOCS[number][1], plan.folder / DOCS[number][0])
        for piece in s.pieces:
            try:
                sink.file(s.key, piece.key, piece.name, piece.path, piece.kind, piece.meta)
            except Exception as exc:  # noqa: BLE001 - um arquivo ruim não pára o resgate do projeto
                failed.append(f"{piece.path.name}: {exc}")
        log(f"  {s.key}: {len(s.docs)} documento(s), {len(s.pieces)} arquivo(s) · {s.status}")
    try:
        (plan.folder / folders.MARKER).write_text(plan.id, encoding="utf-8")
    except OSError:
        pass
    db.event(plan.id, plan.current_stage, "info", f"Projeto resgatado da pasta “{plan.folder.name}” depois da troca do banco: {sum(len(s.docs) for s in plan.stages)} documento(s) e {plan.files} arquivo(s).")
    return {"skipped": False, "failed": failed}


# ---------------------------------------------------------------------------
def describe(plan: ProjectPlan) -> str:
    stage_line = " ".join(f"{s.key}={s.status[:4]}" + (f"({len(s.pieces)}/{s.expected})" if s.expected else "") for s in plan.stages if s.status != "locked")
    kinds = {k: sum(1 for s in plan.stages for p in s.pieces if p.kind == k) for k in ("image", "video", "evidence")}
    part = f" · parte {plan.part}" if plan.part > 1 else ""
    return (f"• {plan.title}{part}  [{plan.mode}{', camada antiga' if plan.old_layout else ''}]  pasta: {plan.folder.name}\n"
            f"    {sum(len(s.docs) for s in plan.stages)} docs, {kinds['image']} imagens, {kinds['video']} vídeos, {kinds['evidence']} quadros da referência · {stage_line}"
            + "".join(f"\n    aviso: {n}" for n in plan.notes))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m app.recover", description="Recria no Supabase os projetos das pastas de runtime/projects.")
    ap.add_argument("--apply", action="store_true", help="grava no Supabase (sem isto, só mostra o que seria feito)")
    ap.add_argument("--only", help="só projetos cujo título (ou pasta) contém este texto")
    ap.add_argument("--replace", action="store_true", help="recria de novo os projetos que já existem no banco")
    ap.add_argument("--owner", help="e-mail ou id do usuário dono, se o painel tiver mais de uma conta")
    ap.add_argument("--dir", help="pasta dos projetos (padrão: runtime/projects)")
    args = ap.parse_args(argv)

    plans, skipped = scan(Path(args.dir) if args.dir else config.PROJECTS_DIR)
    if args.only:
        needle = args.only.casefold()
        plans = [p for p in plans if needle in p.title.casefold() or needle in p.folder.name.casefold()]
    print(f"{len(plans)} projeto(s) encontrados:\n")
    for plan in plans:
        print(describe(plan))
    for line in skipped:
        print(f"  (ignorada) {line}")
    if not args.apply:
        print("\nNada foi gravado. Rode de novo com --apply para recriar estes projetos no Supabase.")
        return 0

    from .db import Db
    db = Db()
    owner = db.studio_owner(args.owner)
    done = 0
    problems: list[str] = []
    for plan in plans:
        print(f"\n▶ {plan.title}")
        try:
            result = apply_project(db, plan, owner, replace=args.replace)
        except Exception as exc:  # noqa: BLE001 - segue para o próximo projeto
            problems.append(f"{plan.title}: {type(exc).__name__}: {exc}")
            print(f"  FALHOU: {exc}")
            continue
        done += 0 if result.get("skipped") else 1
        problems += [f"{plan.title} · {f}" for f in result.get("failed", [])]
    print(f"\nPronto: {done} projeto(s) recriados.")
    for p in problems:
        print(f"  problema: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
