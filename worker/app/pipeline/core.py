"""Contexto de execução de um projeto, leitura/validação de JSON e destino dos resultados."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..errors import Blocked
from ..folders import find_project_dir
from ..media import slugify, snap_duration, split_sources
from . import prompts, render

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
    # Fábrica de roteiros + workflow de animação (modo "factory")
    "F1": ("f1-analise-das-referencias.md", "Documento F1 · Análise individual das referências"),
    "F2": ("f2-matriz-de-aprendizados.md", "Documento F2 · Matriz de aprendizados"),
    "F3": ("f3-cinco-propostas.md", "Documento F3 · Cinco propostas originais"),
    "F4": ("f4-roteiro-completo.md", "Documento F4 · Roteiro completo"),
    "F5": ("f5-pacote-da-fabrica.md", "Documento F5 · Pacote da Fábrica (metadados, personagens e ambientes)"),
    "F6": ("f6-workflow-de-animacao.md", "Documento F6 · Workflow de animação (cenas 9:16)"),
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
        self.media[item_key] = {"name": name, "path": str(path), "kind": kind, "meta": meta or {}, "stage": stage_key}

    def prune(self, stage_key: str, keep: set[str]) -> list[str]:
        gone = [k for k, m in self.media.items() if k not in keep and m["kind"] in ("image", "video") and m.get("stage") == stage_key]
        for key in gone:
            self.media.pop(key)
        return gone

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
    last_start: float = 0.0         # quando começou a última geração (vale para todos os pedidos que usam este contexto)
    on_progress: Callable | None = None   # (item, terminou) · vários ajustes ao mesmo tempo somam um progresso só
    image_slots: object = None      # abas do ChatGPT compartilhadas entre os projetos em andamento (asyncio.Queue de números)
    video_slots: object = None
    image_concurrency: int = 1      # imagens geradas ao mesmo tempo (uma aba por item)
    video_concurrency: int = 1
    pilot_approved: bool = False    # o clipe-piloto já foi aprovado: a etapa de vídeos gera o restante
    overrides: dict = field(default_factory=dict)   # prompts editados pelo usuário: chave do item -> texto
    force: set = field(default_factory=set)          # itens a refazer mesmo já prontos (ajuste pedido numa etapa parada)
    sequel: dict | None = None      # continuação (parte 2+): {part, notes, story, characters, has_ref, doc02}
    clone: bool = False             # modo cópia fiel: roteiro, cortes e câmera idênticos ao original
    direct_sheets: bool = False     # sem a etapa de imagens de corpo inteiro: a folha 9:16 sai direto da descrição
    story: dict | None = None       # história criada a partir de uma ideia: {idea, target_s, min_scenes}
    plan: dict | None = None        # prompts trazidos prontos pelo usuário (projeto iniciado numa etapa avançada)
    profile: dict | None = None     # Fábrica de roteiros: perfil de produção (Fase 0) já normalizado
    chosen: int | None = None       # Fábrica: número da proposta que o usuário escolheu desenvolver (None = a recomendada)
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
        self.clear_stale(item_key)   # acabou de ser feito com as referências atuais

    # --- ajustes pedidos pelo usuário (acumulam: cada novo ajuste parte dos anteriores) -----------------
    MAX_ADJUSTMENTS = 20

    def _small_store(self, name: str) -> dict:
        path = self.dir / "dados" / f"{name}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError:
            return {}

    def _save_small_store(self, name: str, value: dict) -> None:
        path = self.dir / "dados" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")

    def adjustments(self, item_key: str) -> list[str]:
        """Ajustes já aprovados deste item, do mais antigo ao mais novo."""
        return [str(n) for n in self._small_store("ajustes").get(item_key, [])]

    def add_adjustment(self, item_key: str, note: str) -> None:
        store = self._small_store("ajustes")
        notes = [n for n in store.get(item_key, []) if n != note] + [note]
        store[item_key] = notes[-self.MAX_ADJUSTMENTS:]
        self._save_small_store("ajustes", store)

    # --- itens desatualizados: dependem de uma imagem que o usuário ajustou depois que eles foram feitos ---
    def is_stale(self, item_key: str) -> bool:
        return item_key in self._small_store("desatualizados")

    def mark_stale(self, item_key: str, reason: str) -> None:
        store = self._small_store("desatualizados")
        store[item_key] = reason
        self._save_small_store("desatualizados", store)

    def clear_stale(self, item_key: str) -> None:
        store = self._small_store("desatualizados")
        if store.pop(item_key, None) is not None:
            self._save_small_store("desatualizados", store)


def carry_changes(new: dict, old: dict | None, key: str) -> None:
    """Ao refazer o texto (personagens ou ambientes), os ajustes que o usuário já aprovou nas imagens não se perdem."""
    if not old:
        return
    before = {str(x["name"]).casefold(): x.get("approved_changes") for x in old.get(key) or [] if x.get("approved_changes")}
    for entry in new.get(key) or []:
        if entry.get("new_look"):   # os ajustes eram do visual antigo
            continue
        kept = before.get(str(entry["name"]).casefold())
        if kept:
            entry["approved_changes"] = list(kept)


def _merge_by_name(base: list, newer: list) -> list:
    out = {str(x["name"]).casefold(): x for x in base}
    out.update({str(x["name"]).casefold(): x for x in newer})
    return list(out.values())


def _build_library(parent: Path, mine: Path) -> None:
    """Biblioteca da série: personagens, folhas e ambientes que já existem (imagens e textos), acumulados de todas as partes anteriores.
    Os arquivos usam os mesmos nomes de sempre (personagem-<nome>-corpo, folha-<nome>, ambiente-<nome>)."""
    lib = mine / "biblioteca"
    index = {"style_block": "", "characters": [], "environments": []}
    older = parent / "dados" / "biblioteca.json"
    if older.is_file():
        index = json.loads(older.read_text(encoding="utf-8"))
        if (parent / "biblioteca" / "imagens").is_dir():
            shutil.copytree(parent / "biblioteca" / "imagens", lib / "imagens", dirs_exist_ok=True)
    bible, plan = parent / "dados" / "bible.json", parent / "dados" / "plan.json"
    if bible.is_file():
        b = json.loads(bible.read_text(encoding="utf-8"))
        index["style_block"] = b.get("style_block") or index["style_block"]
        index["characters"] = _merge_by_name(index["characters"], b.get("characters") or [])
    if plan.is_file():
        index["environments"] = _merge_by_name(index["environments"], json.loads(plan.read_text(encoding="utf-8")).get("environments") or [])
    for sub in ("personagens", "folhas", "ambientes"):
        if (parent / "imagens" / sub).is_dir():
            shutil.copytree(parent / "imagens" / sub, lib / "imagens" / sub, dirs_exist_ok=True)
    (mine / "dados").mkdir(parents=True, exist_ok=True)
    (mine / "dados" / "biblioteca.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def sequel_context(project: dict, projects_dir: Path) -> dict | None:
    """Contexto de uma continuação. A história e a biblioteca da série (personagens e ambientes já criados) são copiadas para a
    pasta do projeto na primeira vez, então apagar o projeto anterior depois não quebra a parte 2."""
    parent_id = project.get("parent_id")
    if not parent_id:
        return None
    mine = find_project_dir(projects_dir, str(project["id"])) or projects_dir / str(project["id"])
    story_file, doc02_file, index_file = mine / "historia-anterior.md", mine / "dados" / "edicao-anterior.md", mine / "dados" / "biblioteca.json"
    part = int(project.get("part") or 2)
    if not story_file.is_file():
        parent = find_project_dir(projects_dir, str(parent_id)) or projects_dir / str(parent_id)
        script = parent / DOCS["03"][0]
        if not script.is_file():
            raise Blocked("MISSING_INPUT", f"O roteiro da parte anterior não está neste PC ({script}). A parte 2 precisa dele; "
                                           "gere a continuação no computador onde o projeto anterior foi produzido.")
        earlier = (parent / "historia-anterior.md").read_text(encoding="utf-8") if (parent / "historia-anterior.md").is_file() else ""
        story = (earlier.strip() + f"\n\n# PARTE {part - 1}\n\n" + script.read_text(encoding="utf-8")[:9000]).strip()
        mine.mkdir(parents=True, exist_ok=True)
        _build_library(parent, mine)
        story_file.write_text(story, encoding="utf-8")
        edit = parent / DOCS["02"][0]
        if edit.is_file():
            doc02_file.write_text(edit.read_text(encoding="utf-8"), encoding="utf-8")
    library = json.loads(index_file.read_text(encoding="utf-8")) if index_file.is_file() else {"style_block": "", "characters": [], "environments": []}
    return {"part": part, "notes": project.get("sequel_notes"), "story": story_file.read_text(encoding="utf-8"),
            "characters": library["characters"], "environments": library["environments"], "style_block": library.get("style_block") or "",
            "library": str(mine / "biblioteca"),
            "has_ref": bool(project.get("source_url") or project.get("source_video_path")),
            "refs": len(split_sources(project.get("source_url") or project.get("source_video_path"))),
            "doc02": doc02_file.read_text(encoding="utf-8") if doc02_file.is_file() else None}


def adopt_bible(bible: dict, sequel: dict | None) -> list[str]:
    """Numa continuação, personagem que já existe volta IDÊNTICO (descrição, acessórios, voz, estilo) e a imagem dele é reaproveitada.
    Devolve os nomes reaproveitados. Quem não existia segue o fluxo normal e é criado."""
    if not sequel:
        return []
    known = {str(c["name"]).casefold(): c for c in sequel.get("characters") or []}
    current = {c["name"].casefold(): c for c in bible["characters"]}
    if sequel.get("style_block"):
        bible["style_block"] = sequel["style_block"]
    names = []
    for c in bible["characters"]:
        old = known.get(c["name"].casefold())
        c["reused"] = False
        base = c.get("variant_of")
        if base:   # figurino novo de alguém que já existe: mesma pessoa e mesma voz, com folha própria
            origin = known.get(base.casefold()) or current.get(base.casefold())
            if origin is None or origin is c:
                c["variant_of"] = ""
            else:
                c["variant_of"] = origin["name"]
                c["voice"] = origin["voice"]
                continue
        if old is None:
            continue
        if c.get("new_look"):   # pedido explícito de visual novo: a descrição nova vale e a folha é gerada de novo
            c["slug"], c["voice"] = old["slug"], old["voice"]
            continue
        for key in ("slug", "fixed_description", "voice_only", "never_change_pt", "expressions", "details", "voice"):
            c[key] = old[key]
        library = Path(sequel["library"])
        c["reused"] = bool(old["voice_only"]) or (library / render.body_path(old)).is_file() or (library / render.sheet_path(old)).is_file()
        if c["reused"]:
            names.append(c["name"])
    return names


def adopt_plan(plan: dict, sequel: dict | None) -> list[str]:
    """Idem para os ambientes: o que já existe volta igual e a imagem é reaproveitada."""
    if not sequel:
        return []
    known = {str(e["name"]).casefold(): e for e in sequel.get("environments") or []}
    names = []
    for e in plan["environments"]:
        old = known.get(e["name"].casefold())
        e["reused"] = False
        if old is None:
            continue
        e["slug"], e["fixed_description"] = old["slug"], old["fixed_description"]
        e["reused"] = (Path(sequel["library"]) / render.env_path(old)).is_file()
        if e["reused"]:
            names.append(e["name"])
    return names


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
            "variant_of": _text(c, "variant_of", where, required=False), "new_look": c.get("new_look") is True,
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


def validate_plan(raw: dict, bible: dict, min_scenes: int = 0) -> dict:
    names = {c["name"].casefold(): c for c in bible["characters"]}
    envs_raw, scenes_raw = raw.get("environments"), raw.get("scenes")
    if not isinstance(envs_raw, list) or not envs_raw:
        raise ValueError('"environments" precisa ter pelo menos um ambiente')
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError('"scenes" precisa ter pelo menos uma cena')
    if len(scenes_raw) < min_scenes:
        raise ValueError(f'"scenes" tem {len(scenes_raw)} cena(s); o usuário pediu no mínimo {min_scenes}. Acrescente cenas')
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


# ---------------------------------------------------------------------------
# Travas do vídeo: linha do tempo da animação e falas com horário
# ---------------------------------------------------------------------------
MAX_BEAT_S = 3.0          # nenhum trecho da linha do tempo passa disso: força a descrição movimento a movimento
WORDS_PER_SECOND_DE = 2.5


def count_words(text: str) -> int:
    return len(re.findall(r"[\wÀ-ÿ]+(?:['’-][\wÀ-ÿ]+)*", text or ""))


def _seconds(obj: dict, key: str, where: str) -> float:
    try:
        value = round(float(obj.get(key)), 2)
    except (TypeError, ValueError):
        raise ValueError(f'{where}: "{key}" precisa ser um número de segundos (ex.: 0.6)') from None
    if value < 0:
        raise ValueError(f'{where}: "{key}" não pode ser negativo')
    return value


def validate_timeline(raw, duration: float, where: str) -> list[dict]:
    """Animação do clipe em trechos contínuos, de 0 até a duração, sem buracos, cada um com no máximo MAX_BEAT_S."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(f'{where}: "timeline" precisa descrever a animação em trechos com tempo (start_s, end_s, action), de 0.0 a {duration:g} s')
    out, cursor = [], 0.0
    for j, beat in enumerate(raw, 1):
        w = f"{where}.timeline[{j}]"
        if not isinstance(beat, dict):
            raise ValueError(f"{w} não é um objeto")
        start, end = _seconds(beat, "start_s", w), _seconds(beat, "end_s", w)
        if abs(start - cursor) > 0.15:
            raise ValueError(f"{w}: começa em {start:g} s, mas precisa começar em {cursor:g} s"
                             + (" (onde o trecho anterior termina; a linha do tempo não pode ter buracos nem sobreposições)" if j > 1 else ""))
        if end <= cursor:
            raise ValueError(f"{w}: termina em {end:g} s, antes de começar")
        if end - cursor > MAX_BEAT_S + 0.05:
            raise ValueError(f"{w}: trecho de {end - cursor:.1f} s; divida em trechos de no máximo {MAX_BEAT_S:g} s, descrevendo cada movimento")
        action = _text(beat, "action", w)
        if len(action) < 20:
            raise ValueError(f'{w}: "action" curta demais; descreva corpo, mãos, olhar, expressão e boca de cada personagem visível')
        out.append({"start_s": cursor, "end_s": end, "action": action})
        cursor = end
    if abs(cursor - duration) > 0.3:
        raise ValueError(f"{where}: a linha do tempo termina em {cursor:g} s; precisa terminar em {duration:g} s (a duração do clipe)")
    out[-1]["end_s"] = float(duration)
    return out


def validate_dialogue(raw, names: dict[str, str], duration: float, where: str, words_per_second: float = WORDS_PER_SECOND_DE) -> list[dict]:
    """Falas com quem fala, o texto, o início e o fim. Uma pessoa por vez e tempo suficiente para dizer cada fala."""
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise ValueError(f'{where}: "dialogue" precisa ser uma lista')
    out, cursor = [], 0.0
    for j, line in enumerate(raw, 1):
        w = f"{where}.dialogue[{j}]"
        if not isinstance(line, dict):
            raise ValueError(f"{w} não é um objeto")
        said = _text(line, "speaker", w)
        speaker = names.get(said.casefold())
        if speaker is None:
            raise ValueError(f'{w}: quem fala ("{said}") não é um personagem conhecido (use {sorted(names.values())})')
        text = _text(line, "text", w)
        start, end = _seconds(line, "start_s", w), _seconds(line, "end_s", w)
        if end <= start or end > duration + 0.05:
            raise ValueError(f"{w}: a fala vai de {start:g} a {end:g} s, fora do clipe de {duration:g} s")
        if start < cursor - 0.05:
            raise ValueError(f"{w}: começa em {start:g} s, antes de a fala anterior terminar ({cursor:g} s); só uma pessoa fala por vez")
        words = count_words(text)
        if (end - start) + 0.3 < words / (words_per_second * 1.3):
            raise ValueError(f"{w}: {words} palavras em {end - start:.1f} s é rápido demais; reserve cerca de {words / words_per_second:.1f} s ou divida a fala entre clipes")
        out.append({"speaker": speaker, "start_s": start, "end_s": min(end, float(duration)), "text": text,
                    "delivery": _text(line, "delivery", w, required=False) or _text(line, "delivery_en", w, required=False)})
        cursor = end
    return out


def validate_clips(raw: dict, bible: dict, plan: dict, max_clip: int, catalog: set[str] | None = None, min_total: int = 70,
                   max_total: int = 0, min_clips: int = 0) -> dict:
    scene_ids = {s["id"] for s in plan["scenes"]}
    names = {c["name"].casefold(): c["name"] for c in bible["characters"]}
    cast_of = {s["id"]: list(s.get("characters") or []) for s in plan["scenes"]}
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
        timeline = validate_timeline(c.get("timeline"), duration, where)
        lines = validate_dialogue(c.get("dialogue"), names, duration, where)
        planned, duration = duration, snap_duration(duration, max_clip)   # o Grok só faz 6, 10 ou 15 s
        start = "previous_last_frame" if "previous" in str(c.get("start", "")).lower() and i > 1 else "scene_image"
        speakers = list(dict.fromkeys(line["speaker"] for line in lines))
        refs = _strings(c, "reference_images")
        if catalog is not None:
            unknown = [r for r in refs if r not in catalog]
            if unknown:
                raise ValueError(f'{where}: "reference_images" tem ids que não existem: {unknown} (use só {sorted(catalog)})')
            if len(set(refs)) != len(refs) or len(refs) > 3:
                raise ValueError(f'{where}: "reference_images" precisa ter de 1 a 3 ids diferentes')
            if not refs and start != "previous_last_frame":
                raise ValueError(f'{where}: informe em "reference_images" a(s) imagem(ns) deste clipe (a primeira é o quadro inicial)')
        clips.append({"n": i, "scene": scene, "cast": cast_of.get(scene, []), "duration_s": duration, "planned_s": planned, "start": start, "reference_images": refs,
                      "prompt": _text(c, "prompt", where), "timeline": timeline, "camera": _text(c, "camera", where), "keep": _strings(c, "keep"),
                      "ambient": _text(c, "ambient", where, required=False), "lines": lines, "language_en": "German",
                      "speakers": speakers, "dialogue_de": "\n".join(f"{line['speaker']}: {line['text']}" for line in lines),
                      "dialogue_pt": _text(c, "dialogue_pt", where, required=False),
                      "continuity_pt": _text(c, "continuity_pt", where, required=False), "check_pt": _strings(c, "check_pt")})
    total = sum(c["duration_s"] for c in clips)
    if len(clips) < min_clips:
        raise ValueError(f"há {len(clips)} clipe(s); o usuário pediu no mínimo {min_clips} cenas, então são necessários pelo menos {min_clips} clipes")
    if max_total and total > max_total:
        raise ValueError(f"a soma das durações é {total} s; precisa ficar em no máximo {max_total} s (a duração pedida pelo usuário). Encurte ou remova clipes")
    if min_total and total <= min_total:
        raise ValueError(f"a soma das durações é {total} s; precisa ficar acima de {min_total} s (acrescente ou alongue clipes, respeitando o máximo por clipe)")
    return {"order_note_pt": _text(raw, "order_note_pt", "raiz", required=False), "clips": clips, "total_s": total}
