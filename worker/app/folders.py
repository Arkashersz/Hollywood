"""Pasta de cada projeto no PC: leva o nome que você deu ao projeto.

Um arquivo `.projeto-id` dentro da pasta liga ela ao projeto, então renomear o projeto renomeia a pasta
e duas produções com o mesmo nome ficam em "Nome" e "Nome (2)". Pastas antigas, nomeadas pelo id, são
reconhecidas e passam ao nome do projeto no próximo uso.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

MARKER = ".projeto-id"
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def folder_name(title: str | None, limit: int = 80) -> str:
    """Nome de pasta válido no Windows a partir do título (acentos e espaços são mantidos)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(title or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")[:limit].strip(" .")
    if name.casefold() in _RESERVED:
        name += "_"
    return name


def find_project_dir(projects_dir: Path, project_id: str) -> Path | None:
    pid = str(project_id)
    if projects_dir.is_dir():
        for d in projects_dir.iterdir():
            marker = d / MARKER
            try:
                if d.is_dir() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == pid:
                    return d
            except OSError:
                continue
    legacy = projects_dir / pid
    return legacy if legacy.is_dir() else None


def resolve_project_dir(projects_dir: Path, project: dict) -> Path:
    """Pasta do projeto (criada se faltar), com o nome atual dele."""
    pid = str(project["id"])
    wanted = folder_name(project.get("title")) or pid
    projects_dir.mkdir(parents=True, exist_ok=True)
    current = find_project_dir(projects_dir, pid)

    def free(name: str) -> str:
        candidate, n = name, 2
        while (projects_dir / candidate).exists():
            candidate, n = f"{name} ({n})", n + 1
        return candidate

    if current is None:
        current = projects_dir / free(wanted)
        current.mkdir(parents=True)
    elif current.name.casefold() != wanted.casefold() and not re.fullmatch(re.escape(wanted) + r" \(\d+\)", current.name, re.I):
        target = projects_dir / free(wanted)
        try:
            current.rename(target)
            current = target
        except OSError:   # algo (um programa de edição, por exemplo) está usando a pasta: mantém o nome atual
            pass
    try:
        (current / MARKER).write_text(pid, encoding="utf-8")
    except OSError:
        pass
    return current


def open_in_explorer(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - abre a pasta no Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
