"""O cérebro do pipeline: Claude, chamado pelo Claude Code em modo não interativo (`claude -p`).

É o Claude quem olha os quadros do vídeo, identifica gancho, CTA e estratégia, escreve o roteiro
e cria os prompts de imagem e de vídeo. O Python só entrega o pedido, confere o formato da
resposta e despacha os prompts aprovados para o ChatGPT (imagens) e o Grok (vídeos).

- Usa o login do Claude Code deste computador (a sua assinatura); nenhuma chave de API é usada.
- O Claude roda só com a ferramenta de leitura de arquivos, dentro da pasta do projeto, e sem
  carregar personalizações (skills, hooks, MCP): conteúdo vindo do vídeo não consegue agir.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config
from .errors import Blocked

SYSTEM_PROMPT = (
    "Você é o analista, roteirista e diretor de arte do TikTok Studio. Responda exatamente no formato pedido, "
    "sem comentários sobre o processo. Quando o pedido listar arquivos de imagem, abra TODOS com a ferramenta Read "
    "antes de responder e baseie-se no que realmente aparece neles. Você só lê arquivos; nunca tente executar comandos."
)
LIMIT_RE = re.compile(r"usage limit|rate limit|limit reached|hit your limit|limite|overloaded|quota", re.I)
LOGIN_RE = re.compile(r"/login|not logged in|invalid api key|authentication|unauthorized|credentials", re.I)
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")


class ClaudeBrain:
    service = "Claude"

    def __init__(self, cwd: Path, *, executable: str | None = None, runner=None):
        self.cwd = Path(cwd)
        self.executable = executable or config.CLAUDE_PATH or shutil.which("claude") or ""
        self.session_id: str | None = None
        self._run = runner or _run_cli

    async def ask(self, prompt: str, files: list[Path] | None = None, *, new_chat: bool = True, timeout: int | None = None) -> str:
        """Faz um pedido e devolve o texto da resposta (Markdown). `new_chat=False` continua a conversa anterior."""
        if not self.executable:
            raise Blocked("CLAUDE_NOT_FOUND", "O Claude Code (comando `claude`) não foi encontrado no PC do worker.",
                          "Instale o Claude Code, faça login com `claude` e clique em Tentar novamente (ou defina CLAUDE_PATH em worker/.env).")
        missing = [str(f) for f in files or [] if not Path(f).is_file()]
        if missing:
            raise Blocked("MISSING_INPUT", f"Arquivo de referência não encontrado: {', '.join(missing)}")
        if files:
            listing = "\n".join(f"- {Path(f).resolve()}" for f in files)
            prompt = f"{prompt}\n\nArquivos de imagem para abrir com a ferramenta Read antes de responder (em ordem):\n{listing}"
        command = [self.executable, "-p", "--output-format", "json", "--tools", "Read", "--allowedTools", "Read",
                   "--safe-mode", "--strict-mcp-config", "--system-prompt", SYSTEM_PROMPT]
        if config.CLAUDE_MODEL:
            command += ["--model", config.CLAUDE_MODEL]
        if config.CLAUDE_EFFORT:
            command += ["--effort", config.CLAUDE_EFFORT]
        if not new_chat and self.session_id:
            command += ["--resume", self.session_id]
        self.cwd.mkdir(parents=True, exist_ok=True)
        code, out, err = await self._run(command, prompt, self.cwd, timeout or config.CLAUDE_TIMEOUT)
        return self._parse(code, out, err)

    def _parse(self, code: int, out: str, err: str) -> str:
        data = None
        try:
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
        except (json.JSONDecodeError, IndexError):
            data = None
        if isinstance(data, dict):
            self.session_id = data.get("session_id") or self.session_id
            text = str(data.get("result") or "").strip()
            if not data.get("is_error") and code == 0 and text:
                return text
            detail = text or str(data.get("subtype") or "")
        else:
            detail = (err or out).strip()[-600:]
        if LIMIT_RE.search(detail):
            raise Blocked("LIMIT_REACHED", f"Claude: {detail[:300]}",
                          "A cota do seu plano Claude foi atingida. Aguarde a renovação e clique em Tentar novamente.")
        if LOGIN_RE.search(detail):
            raise Blocked("SESSION_REQUIRED", f"Claude: {detail[:300]}",
                          "No PC do worker, abra um terminal, rode `claude`, faça login e clique em Tentar novamente.")
        raise Blocked("BRAIN_ERROR", f"O Claude não devolveu uma resposta utilizável (código {code}): {detail[:400] or 'sem detalhe'}",
                      "Veja o terminal do worker e clique em Tentar novamente.")


async def _run_cli(command: list[str], prompt: str, cwd: Path, timeout: int) -> tuple[int, str, str]:
    """Executa o Claude Code com o pedido na entrada padrão. Sem chave de API: vale o login do Claude Code."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV and not k.startswith("CLAUDE_CODE_")}

    def run() -> tuple[int, str, str]:
        try:
            proc = subprocess.run(command, input=prompt, cwd=str(cwd), env=env, text=True, encoding="utf-8", errors="replace",
                                  capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Blocked("TIMEOUT", f"O Claude não respondeu em {timeout} s.") from None
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    return await asyncio.to_thread(run)
