"""Contexto de execução de um projeto, leitura/validação de JSON e destino dos resultados."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..errors import Blocked
from ..media import slugify, snap_duration
from . import prompts

DOCS = {
    "01": ("01-ficha-da-referencia.md", "Documento 01 · Ficha da referência"),
    "02": ("02-edicao-da-referencia.md", "Documento 02 · Edição da referência"),
    "03": ("03-roteiro-de.md", "Documento 03 · Roteiro em alemão"),
    "04": ("04-personagens.md", "Documento 04 · Personagens"),
    "05": ("05-folhas-dos-personagens.md", "Documento 05 · Folhas dos personagens"),
    "06": ("06-vozes.md", "Documento 06 · Vozes"),
    "07": ("07-ambientes-e-cenas.md", "Documento 07 · Ambientes e cenas"),
    "08": ("08-prompts-grok.md", "Documento 08 · Prompts dos vídeos (Grok)"),
    "09": ("09-plano-edicao-capcut.md", "Documento 09 · Plano de edição no CapCut"),
    "10": ("10-kit-publicacao.md", "Documento 10 · Kit de publicação"),
}


class LocalSink:
    """Destino usado em testes e na execução local: só registra o que seria publicado."""

    def __init__(self, echo: bool = False):
        self.events: list[tuple[str, str, str]] = []
        self.documents: dict[str, str] = {}
        self.media: dict[str, dict] = {}
        self.echo = echo

    def event(self, stage_key: str, level: str, message: str, details: dict | None = None) -> None:
        self.events.append((stage_key, level, message))
        if self.echo:
            print(f"[{stage_key}] {message}")

    def document(self, stage_key: str, item_key: str, name: str, path: Path) -> None:
        self.documents[item_key] = path.read_text(encoding="utf-8")

    def file(self, stage_key: str, item_key: str, name: str, path: Path, kind: str, meta: dict | None = None) -> None:
        self.media[item_key] = {"name": name, "path": str(path), "kind": kind, "meta": meta or {}}

    def progress(self, stage_key: str, done: int, total: int, label: str) -> None:
        if self.echo:
            print(f"[{stage_key}] {done}/{total} {label}")


@dataclass
class Ctx:
    project_id: str
    source: str
    dir: Path
    sink: object
    brain: object          # Claude: ask(prompt, files, new_chat=) -> análise, roteiro e prompts
    images: object         # ChatGPT: generate_image(prompt, refs, out)
    video: object          # Grok: generate_video(prompt, start_image, out)
    stage_key: str = ""
    version: int = 1
    feedback: str | None = None
    max_clip: int = 6
    whisper_model: str = "small"
    language: str | None = None
    pause: Callable | None = None   # espera entre gerações (injetável nos testes)
    image_slots: object = None      # abas do ChatGPT compartilhadas entre os projetos em andamento (asyncio.Queue de números)
    video_slots: object = None
    image_concurrency: int = 1      # imagens geradas ao mesmo tempo (uma aba por item)
    video_concurrency: int = 1
    pilot_approved: bool = False    # o clipe-piloto já foi aprovado: a etapa de vídeos gera o restante
    overrides: dict = field(default_factory=dict)   # prompts editados pelo usuário: chave do item -> texto
    force: set = field(default_factory=set)          # itens a refazer mesmo já prontos (ajuste pedido numa etapa parada)
    clone: bool = False             # modo cópia fiel: roteiro, cortes e câmera idênticos ao original
    plan: dict | None = None        # prompts trazidos prontos pelo usuário (projeto iniciado numa etapa avançada)
    _generated: dict = field(default_factory=dict)

    # --- registro -----------------------------------------------------------
    def log(self, message: str, level: str = "info", **details) -> None:
        self.sink.event(self.stage_key, level, message, details or None)

    # --- arquivos do projeto -----------------------------------------------
    def data_path(self, name: str) -> Path:
        return self.dir / "dados" / f"{name}.json"

    def save_data(self, name: str, value: dict) -> None:
        path = self.data_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_data(self, name: str) -> dict:
        path = self.data_path(name)
        if not path.is_file():
            raise Blocked("MISSING_INPUT", f"Dados da etapa anterior não encontrados: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def doc_path(self, number: str) -> Path:
        return self.dir / DOCS[number][0]

    def read_doc(self, number: str, required: bool = True) -> str | None:
        path = self.doc_path(number)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        if required:
            raise Blocked("MISSING_INPUT", f"Documento {number} não encontrado no PC do worker: {path}")
        return None

    def write_doc(self, number: str, body: str) -> Path:
        """Grava o documento com título padronizado e publica no painel."""
        filename, title = DOCS[number]
        path = self.dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{strip_leading_title(body).strip()}\n", encoding="utf-8")
        self.sink.document(self.stage_key, f"doc-{number}", title, path)
        return path

    def previous(self, number: str) -> str | None:
        """Versão anterior de um documento, só quando esta execução é uma revisão."""
        return self.read_doc(number, required=False) if self.feedback else None

    # --- controle de versões dos itens gerados ----------------------------------
    def _generated_path(self) -> Path:
        return self.dir / "dados" / "gerados.json"

    def generated_version(self, item_key: str) -> int | None:
        if not self._generated and self._generated_path().is_file():
            self._generated.update(json.loads(self._generated_path().read_text(encoding="utf-8")))
        return self._generated.get(item_key)

    def mark_generated(self, item_key: str) -> None:
        self.generated_version(item_key)
        self._generated[item_key] = self.version
        path = self._generated_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._generated, indent=1), encoding="utf-8")


def strip_leading_title(markdown: str) -> str:
    """Remove um título H1 que o modelo tenha colocado, pois o título oficial é fixo."""
    lines = markdown.strip().splitlines()
    while lines and (not lines[0].strip() or re.match(r"^#\s", lines[0])):
        lines.pop(0)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON vindo do modelo
# ---------------------------------------------------------------------------
def extract_json(text: str) -> dict:
    """Lê o objeto JSON da resposta: primeiro os blocos de código (do último para o primeiro), depois o recorte entre chaves."""
    fenced = [m.group(1) for m in re.finditer(r"`{3,}[a-zA-Z]*[ \t]*\n(.*?)\n`{3,}", text, re.S)]
    candidates = list(reversed(fenced))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    last_error = "nenhum bloco JSON encontrado"
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"JSON inválido ({exc.msg}, linha {exc.lineno})"
            continue
        if isinstance(value, dict):
            return value
        last_error = "o JSON não é um objeto"
    raise ValueError(last_error)


async def ask_json(llm, prompt: str, validate: Callable[[dict], dict], *, files: list[Path] | None = None, new_chat: bool = True) -> dict:
    """Pede um objeto JSON e confere o formato; uma única nova tentativa, na mesma conversa."""
    text = await llm.ask(prompt, files or [], new_chat=new_chat)
    try:
        return validate(extract_json(text))
    except ValueError as first:
        text = await llm.ask(prompts.RETRY_JSON.format(error=first), [], new_chat=False)
        try:
            return validate(extract_json(text))
        except ValueError as second:
            raise Blocked("BAD_OUTPUT", f"Resposta fora do formato: {second}") from None


def _text(obj: dict, key: str, where: str, *, required: bool = True) -> str:
    value = obj.get(key)
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str) or (required and not value.strip()):
        if required:
            raise ValueError(f'campo "{key}" ausente ou vazio em {where}')
        return ""
    return value.strip()


def _strings(obj: dict, key: str) -> list[str]:
    value = obj.get(key) or []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def _unique_slug(name: str, used: set[str]) -> str:
    base = slugify(name, 40)
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def validate_bible(raw: dict) -> dict:
    style = _text(raw, "style_block", "raiz")
    chars = raw.get("characters")
    if not isinstance(chars, list) or not chars:
        raise ValueError('"characters" precisa ser uma lista com pelo menos um personagem')
    used: set[str] = set()
    out = []
    for i, c in enumerate(chars, 1):
        if not isinstance(c, dict):
            raise ValueError(f"personagem {i} não é um objeto")
        where = f"characters[{i}]"
        name = _text(c, "name", where)
        voice = c.get("voice") if isinstance(c.get("voice"), dict) else {}
        fixed = _text(c, "fixed_description", where)
        out.append({
            "name": name, "slug": _unique_slug(name, used), "role_pt": _text(c, "role_pt", where, required=False),
            "fixed_description": fixed, "voice_only": fixed.strip().lower().rstrip(".") == "voice only",
            "never_change_pt": _strings(c, "never_change_pt"), "expressions": _strings(c, "expressions"), "details": _strings(c, "details"),
            "voice": {
                "profile_pt": _text(voice, "profile_pt", where + ".voice", required=False),
                "descriptor_en": _text(voice, "descriptor_en", where + ".voice"),
                "descriptor_de": _text(voice, "descriptor_de", where + ".voice", required=False),
                "rules_pt": _strings(voice, "rules_pt"),
            },
        })
    if all(c["voice_only"] for c in out):
        raise ValueError("nenhum personagem com aparência definida")
    return {"style_block": style, "style_explanation_pt": _text(raw, "style_explanation_pt", "raiz", required=False), "characters": out}


def validate_plan(raw: dict, bible: dict) -> dict:
    names = {c["name"].casefold(): c for c in bible["characters"]}
    envs_raw, scenes_raw = raw.get("environments"), raw.get("scenes")
    if not isinstance(envs_raw, list) or not envs_raw:
        raise ValueError('"environments" precisa ter pelo menos um ambiente')
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError('"scenes" precisa ter pelo menos uma cena')
    used: set[str] = set()
    envs = []
    for i, e in enumerate(envs_raw, 1):
        where = f"environments[{i}]"
        name = _text(e if isinstance(e, dict) else {}, "name", where)
        envs.append({"name": name, "slug": _unique_slug(name, used), "fixed_description": _text(e, "fixed_description", where),
                     "explanation_pt": _text(e, "explanation_pt", where, required=False)})
    by_env = {e["name"].casefold(): e for e in envs}
    used_ids: set[str] = set()
    scenes = []
    for i, s in enumerate(scenes_raw, 1):
        where = f"scenes[{i}]"
        if not isinstance(s, dict):
            raise ValueError(f"{where} não é um objeto")
        sid = slugify(_text(s, "id", where), 12)
        if sid in used_ids:
            raise ValueError(f'id de cena repetido: "{sid}"')
        used_ids.add(sid)
        env = by_env.get(_text(s, "environment", where).casefold())
        if env is None:
            raise ValueError(f'{where}: ambiente "{s.get("environment")}" não está em "environments"')
        cast = []
        for n in _strings(s, "characters"):
            c = names.get(n.casefold())
            if c is None:
                raise ValueError(f'{where}: personagem "{n}" não existe (use {[c["name"] for c in bible["characters"]]})')
            if not c["voice_only"]:
                cast.append(c["name"])
        scenes.append({"id": sid, "environment": env["name"], "characters": cast, "composition": _text(s, "composition", where),
                       "shot_pt": _text(s, "shot_pt", where, required=False), "explanation_pt": _text(s, "explanation_pt", where, required=False)})
    areas_raw = raw.get("areas") if isinstance(raw.get("areas"), dict) else {}
    areas = {k: _text(areas_raw, k, "areas", required=False) or "não aplicável (não informado pelo modelo)"
             for k in ("lighting_mood_style_pt", "area_flow_plan_pt", "camera_work_pt", "shooting_technique_pt", "notes_pt")}
    return {"areas": areas, "environments": envs, "scenes": scenes}


def validate_clips(raw: dict, bible: dict, plan: dict, max_clip: int, catalog: set[str] | None = None, min_total: int = 70) -> dict:
    scene_ids = {s["id"] for s in plan["scenes"]}
    names = {c["name"].casefold(): c["name"] for c in bible["characters"]}
    clips_raw = raw.get("clips")
    if not isinstance(clips_raw, list) or not clips_raw:
        raise ValueError('"clips" precisa ter pelo menos um clipe')
    clips = []
    for i, c in enumerate(clips_raw, 1):
        where = f"clips[{i}]"
        if not isinstance(c, dict):
            raise ValueError(f"{where} não é um objeto")
        scene = slugify(_text(c, "scene", where), 12)
        if scene not in scene_ids:
            raise ValueError(f'{where}: cena "{c.get("scene")}" não tem imagem-base (use {sorted(scene_ids)})')
        try:
            duration = int(round(float(c.get("duration_s"))))
        except (TypeError, ValueError):
            raise ValueError(f'{where}: "duration_s" precisa ser um número') from None
        if not 1 <= duration <= max_clip:
            raise ValueError(f'{where}: "duration_s" = {duration} está fora de 1–{max_clip} s')
        duration = snap_duration(duration, max_clip)   # o Grok só faz 6, 10 ou 15 s
        start = "previous_last_frame" if "previous" in str(c.get("start", "")).lower() and i > 1 else "scene_image"
        speakers = []
        for n in _strings(c, "speakers"):
            if n.casefold() not in names:
                raise ValueError(f'{where}: quem fala ("{n}") não é um personagem conhecido')
            speakers.append(names[n.casefold()])
        refs = _strings(c, "reference_images")
        if catalog is not None:
            unknown = [r for r in refs if r not in catalog]
            if unknown:
                raise ValueError(f'{where}: "reference_images" tem ids que não existem: {unknown} (use só {sorted(catalog)})')
            if len(set(refs)) != len(refs) or len(refs) > 3:
                raise ValueError(f'{where}: "reference_images" precisa ter de 1 a 3 ids diferentes')
            if not refs and start != "previous_last_frame":
                raise ValueError(f'{where}: informe em "reference_images" a(s) imagem(ns) deste clipe (a primeira é o quadro inicial)')
        clips.append({"n": i, "scene": scene, "duration_s": duration, "start": start, "reference_images": refs, "prompt": _text(c, "prompt", where),
                      "speakers": speakers, "dialogue_de": _text(c, "dialogue_de", where, required=False),
                      "dialogue_pt": _text(c, "dialogue_pt", where, required=False),
                      "continuity_pt": _text(c, "continuity_pt", where, required=False), "check_pt": _strings(c, "check_pt")})
    total = sum(c["duration_s"] for c in clips)
    if min_total and total <= min_total:
        raise ValueError(f"a soma das durações é {total} s; precisa ficar acima de {min_total} s (acrescente ou alongue clipes, respeitando o máximo por clipe)")
    return {"order_note_pt": _text(raw, "order_note_pt", "raiz", required=False), "clips": clips, "total_s": total}
