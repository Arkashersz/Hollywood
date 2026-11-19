"""Acesso remoto aos vídeos: um túnel HTTPS até o servidor local de vídeos do worker.

Os vídeos nunca saem do PC. Quando você abre o painel longe de casa (celular, site, aplicativo), o player busca o vídeo
no PC por este túnel, em tempo real, e só enquanto você assiste. Usa o `cloudflared` (túnel rápido da Cloudflare,
sem conta): ele devolve um endereço https://<nome>.trycloudflare.com que muda a cada abertura; o worker publica o
endereço atual no Supabase (tabela worker_media) e o painel o lê. Sem a senha (?k=) o servidor de vídeos recusa tudo.
Alternativa: defina MEDIA_PUBLIC_URL com o endereço fixo de outro túnel (Tailscale Funnel, Cloudflare Tunnel nomeado…).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)


def find_cloudflared(configured: str = "") -> str | None:
    """Caminho do cloudflared: o configurado, o do PATH ou os lugares onde o winget o instala."""
    candidates = [configured, shutil.which("cloudflared") or ""]
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.getenv(var):
            candidates.append(str(Path(os.environ[var]) / "cloudflared" / "cloudflared.exe"))
    if os.getenv("LOCALAPPDATA"):
        candidates.append(str(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"))
    return next((c for c in candidates if c and Path(c).is_file()), None)


def command(exe: str, port: int) -> list[str]:
    return [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]


def parse_url(line: str) -> str | None:
    m = URL.search(line)
    return m.group(0).lower() if m else None


class Tunnel:
    """Mantém o cloudflared vivo (reabre se cair) e guarda o endereço público atual em `url`."""

    def __init__(self, cmd: list[str], retry_seconds: float = 5.0):
        self.cmd, self.retry_seconds = cmd, retry_seconds
        self.url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Tunnel":
        self._thread = threading.Thread(target=self._run, daemon=True, name="media-tunnel")
        self._thread.start()
        return self

    def _run(self) -> None:
        delay = self.retry_seconds
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
            except OSError as exc:
                print(f"Túnel de vídeos não iniciou: {exc}", flush=True)
                self._stop.wait(60)
                continue
            for line in self._proc.stdout:   # type: ignore[union-attr]
                found = parse_url(line)
                if found and found != self.url:
                    self.url = found
                    delay = self.retry_seconds
                    print(f"Vídeos acessíveis de fora de casa em {found} (com a senha do estúdio).", flush=True)
            self._proc.wait()
            self.url = None
            if not self._stop.is_set():
                print(f"Túnel de vídeos caiu; nova tentativa em {delay:.0f}s.", flush=True)
                self._stop.wait(delay)
                delay = min(delay * 2, 120)

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
        self.url = None


class RemoteMedia:
    """O que o painel precisa para tocar os vídeos do PC: endereço local, endereço público e a senha."""

    def __init__(self, token: str, local_url: str | None, public_url: str | None = None, tunnel: Tunnel | None = None):
        self.token, self.local_url, self._public, self.tunnel = token, local_url, public_url, tunnel

    @property
    def public_url(self) -> str | None:
        return self._public or (self.tunnel.url if self.tunnel else None)

    def snapshot(self) -> dict:
        return {"local_url": self.local_url, "public_url": self.public_url, "token": self.token}

    def stop(self) -> None:
        if self.tunnel:
            self.tunnel.stop()
