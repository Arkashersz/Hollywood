"""Configuração do worker, lida de worker/.env (nunca versionado)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

WORKER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = WORKER_DIR.parent
load_dotenv(WORKER_DIR / ".env")


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (WORKER_DIR / p).resolve()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "artifacts")
WORKER_ID = os.getenv("WORKER_ID", "home-pc-01")
POLL_SECONDS = _int("POLL_SECONDS", 5)

RUNTIME_DIR = _path("RUNTIME_DIR", REPO_DIR / "runtime")
PROJECTS_DIR = RUNTIME_DIR / "projects"

# Navegador: um Chrome comum, com perfil dedicado, onde o usuário faz login manualmente.
BROWSER_PROFILE_DIR = _path("BROWSER_PROFILE_DIR", WORKER_DIR / "browser-profiles" / "studio")
CHROME_PATH = os.getenv("CHROME_PATH", "")
CDP_PORT = _int("CDP_PORT", 9333)
CHATGPT_URL = os.getenv("CHATGPT_URL", "https://chatgpt.com/")
GROK_URL = os.getenv("GROK_URL", "https://grok.com/imagine")
SELECTORS_FILE = _path("SELECTORS_FILE", WORKER_DIR / "selectors.json")

# Cérebro (análise, roteiro e prompts): Claude pelo Claude Code local. "chatgpt" usa o ChatGPT do navegador.
BRAIN = (os.getenv("BRAIN", "claude") or "claude").lower()
CLAUDE_PATH = os.getenv("CLAUDE_PATH", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")  # low | medium | high | xhigh | max — mais alto = mais lento e mais cota
CLAUDE_TIMEOUT = _int("CLAUDE_TIMEOUT", 1200)

# Análise local.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
REFERENCE_LANGUAGE = os.getenv("REFERENCE_LANGUAGE", "") or None  # ex.: de, pt, en; vazio = detectar

# Produção. O limite real do Grok deve ser conferido na interface; este valor guia o roteiro.
GROK_MAX_CLIP_SECONDS = _int("GROK_MAX_CLIP_SECONDS", 15)   # o Grok oferece clipes de 6, 10 ou 15 s
GROK_RESOLUTION = os.getenv("GROK_RESOLUTION", "720p")        # sempre 720p; vazio = não mexer
GROK_ASPECT = os.getenv("GROK_ASPECT", "9:16")                # vertical para TikTok; vazio = não mexer

# Tempos de espera (segundos).
TEXT_TIMEOUT = _int("TEXT_TIMEOUT", 600)
IMAGE_TIMEOUT = _int("IMAGE_TIMEOUT", 600)
VIDEO_TIMEOUT = _int("VIDEO_TIMEOUT", 900)
STALL_SECONDS = _int("STALL_SECONDS", 180)
# Sem tarefas por este tempo (ex.: esperando a sua aprovação), o Chrome do worker é fechado; reabre sozinho na próxima tarefa.
IDLE_CLOSE_SECONDS = _int("IDLE_CLOSE_SECONDS", 60)
# Quantos itens do MESMO lote o worker gera ao mesmo tempo (uma aba do navegador por item). 1 = um por vez.
# Quantos projetos (links) o worker conduz ao mesmo tempo. Os demais esperam na fila. 1 = um projeto por vez.
MAX_PARALLEL_JOBS = max(1, min(4, _int("MAX_PARALLEL_JOBS", 3)))
IMAGE_CONCURRENCY = max(1, min(10, _int("IMAGE_CONCURRENCY", 3)))
VIDEO_CONCURRENCY = max(1, min(4, _int("VIDEO_CONCURRENCY", 1)))
PAUSE_BETWEEN_ITEMS = _int("PAUSE_BETWEEN_ITEMS", 8)
