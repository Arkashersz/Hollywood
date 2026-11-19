"""Servidor local de vídeos: o painel toca direto os arquivos da pasta do projeto, sem enviá-los ao Supabase.

Escuta só em 127.0.0.1; fora de casa o acesso passa pelo túnel do worker (tunnel.py), por isso todo pedido precisa
da senha (?k=...), que só o dono lê no Supabase. Serve apenas vídeos dentro da pasta de um projeto e aceita pedidos
parciais (Range), que o player usa para avançar e voltar no vídeo.
URL: http://127.0.0.1:<porta>/v/<id do projeto>/<caminho relativo do vídeo>?k=<senha>
"""
from __future__ import annotations

import hmac
import mimetypes
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import folders

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}
UUID = re.compile(r"^[0-9a-fA-F-]{8,40}$")
CHUNK = 1024 * 256


def resolve_video(projects_dir: Path, url_path: str) -> Path | None:
    """Caminho do vídeo pedido, ou None se não for um vídeo dentro da pasta de um projeto."""
    parts = unquote(urlsplit(url_path).path).split("/")
    if len(parts) < 4 or parts[0] != "" or parts[1] != "v" or not UUID.match(parts[2]):
        return None
    root = folders.find_project_dir(projects_dir, parts[2])
    if root is None:
        return None
    root = root.resolve()
    target = (root.joinpath(*[p for p in parts[3:] if p])).resolve()
    if not target.is_relative_to(root) or target.suffix.lower() not in VIDEO_SUFFIXES or not target.is_file():
        return None
    return target


def load_token(runtime_dir: Path) -> str:
    """Senha do servidor de vídeos: criada uma vez e guardada na pasta runtime (a mesma entre uma abertura e outra do worker)."""
    file = runtime_dir / "media-token.txt"
    try:
        token = file.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        file.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


def _authorized(url_path: str, token: str | None) -> bool:
    if not token:
        return True
    given = parse_qs(urlsplit(url_path).query).get("k", [""])[0]
    return hmac.compare_digest(given.encode(), token.encode())


def _range(header: str | None, size: int) -> tuple[int, int] | None:
    m = re.match(r"bytes=(\d*)-(\d*)$", header or "")
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        start, end = max(size - int(m.group(2)), 0), size - 1
    end = min(end, size - 1)
    return (start, end) if start <= end else None


def make_handler(projects_dir: Path, token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # silencioso: o terminal do worker já é cheio
            pass

        def _send(self, head_only: bool) -> None:
            if not _authorized(self.path, token):
                self.send_error(403)
                return
            path = resolve_video(projects_dir, self.path)
            if path is None:
                self.send_error(404)
                return
            size = path.stat().st_size
            wanted = self.headers.get("Range")
            span = _range(wanted, size)
            if wanted and span is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            start, end = span if span else (0, size - 1)
            self.send_response(206 if span else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if span:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if head_only:
                return
            try:
                with path.open("rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(CHUNK, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):   # o player fecha a conexão ao pular no vídeo
                pass

        def do_GET(self) -> None:  # noqa: N802
            self._send(False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._send(True)

    return Handler


def start(projects_dir: Path, port: int, token: str | None = None) -> ThreadingHTTPServer | None:
    """Sobe o servidor em segundo plano; devolve None se a porta estiver ocupada (os vídeos seguem na pasta)."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(projects_dir, token))
    except OSError as exc:
        print(f"Servidor local de vídeos não iniciou na porta {port}: {exc}. Mude LOCAL_MEDIA_PORT em worker/.env.", flush=True)
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="media-server").start()
    return server
