"""Chamada do Claude (Claude Code não interativo) com o executável simulado."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import brain as brain_module
from app.brain import ClaudeBrain
from app.errors import Blocked


class FakeCli:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def __call__(self, command, prompt, cwd, timeout):
        self.calls.append({"command": command, "prompt": prompt, "cwd": cwd})
        return self.replies.pop(0)


def ok(text: str, session: str = "s-1"):
    return 0, json.dumps({"type": "result", "is_error": False, "result": text, "session_id": session}), ""


def test_reads_listed_images_with_read_only_tools_and_resumes(tmp_path):
    sheet = tmp_path / "referencia" / "folha-gancho-01.jpg"
    sheet.parent.mkdir()
    sheet.write_bytes(b"x")
    cli = FakeCli(ok("## 2. Resumo"), ok("## 1. Técnico"))
    brain = ClaudeBrain(tmp_path, executable="claude", runner=cli)

    assert asyncio.run(brain.ask("Analise.", [sheet])) == "## 2. Resumo"
    first = cli.calls[0]
    assert first["cwd"] == tmp_path and str(sheet.resolve()) in first["prompt"] and first["prompt"].startswith("Analise.")
    command = first["command"]
    assert command[:3] == ["claude", "-p", "--output-format"] and "--resume" not in command
    assert command[command.index("--tools") + 1] == "Read", "o Claude só pode ler arquivos"
    assert "--safe-mode" in command and "--strict-mcp-config" in command
    assert not any("dangerously" in part or "bypass" in part for part in command)

    assert asyncio.run(brain.ask("Agora o documento 02.", new_chat=False)) == "## 1. Técnico"
    second = cli.calls[1]["command"]
    assert second[second.index("--resume") + 1] == "s-1"

    cli.replies.append(ok("novo"))
    asyncio.run(brain.ask("Outra conversa."))
    assert "--resume" not in cli.calls[2]["command"]


@pytest.mark.parametrize("reply,code", [
    ((1, json.dumps({"is_error": True, "result": "Claude usage limit reached. Your limit will reset at 5pm."}), ""), "LIMIT_REACHED"),
    ((1, "", "Invalid API key · Please run /login"), "SESSION_REQUIRED"),
    ((0, json.dumps({"is_error": False, "result": ""}), ""), "BRAIN_ERROR"),
    ((3, "não é json", "falha interna"), "BRAIN_ERROR"),
])
def test_failures_become_actionable_blocks(tmp_path, reply, code):
    brain = ClaudeBrain(tmp_path, executable="claude", runner=FakeCli(reply))
    with pytest.raises(Blocked) as err:
        asyncio.run(brain.ask("x"))
    assert err.value.code == code and err.value.action


def test_missing_cli_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_module.config, "CLAUDE_PATH", "")
    monkeypatch.setattr(brain_module.shutil, "which", lambda name: None)
    with pytest.raises(Blocked) as err:
        asyncio.run(ClaudeBrain(tmp_path).ask("x"))
    assert err.value.code == "CLAUDE_NOT_FOUND"
    cli = FakeCli()
    with pytest.raises(Blocked) as err:
        asyncio.run(ClaudeBrain(tmp_path, executable="claude", runner=cli).ask("x", [tmp_path / "nao-existe.jpg"]))
    assert err.value.code == "MISSING_INPUT" and not cli.calls


def test_child_process_never_gets_an_api_key(tmp_path, monkeypatch):
    """Sem chave de API no processo filho, vale o login do Claude Code (a assinatura), não cobrança por uso."""
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)

        class Done:
            returncode, stdout, stderr = 0, json.dumps({"result": "ok", "session_id": "s"}), ""
        return Done()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "pai")
    monkeypatch.setattr(brain_module.subprocess, "run", fake_run)
    assert asyncio.run(ClaudeBrain(tmp_path, executable="claude").ask("oi")) == "ok"
    assert "ANTHROPIC_API_KEY" not in captured["env"] and "CLAUDE_CODE_SESSION_ID" not in captured["env"]
    assert captured["input"] == "oi" and captured["cwd"] == str(tmp_path)


def test_project_effort_overrides_the_pc_default_and_extends_the_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_module.config, "CLAUDE_EFFORT", "low")
    monkeypatch.setattr(brain_module.config, "CLAUDE_TIMEOUT", 100)
    seen = {}

    async def runner(command, prompt, cwd, timeout):
        seen.update(command=command, timeout=timeout)
        return ok("pronto")
    asyncio.run(ClaudeBrain(tmp_path, executable="claude", runner=runner, model="claude-opus-5-5", effort="high").ask("x"))
    command = seen["command"]
    assert command[command.index("--model") + 1] == "claude-opus-5-5" and command[command.index("--effort") + 1] == "high" and seen["timeout"] == 200
    asyncio.run(ClaudeBrain(tmp_path, executable="claude", runner=runner).ask("x"))
    assert seen["command"][seen["command"].index("--effort") + 1] == "low" and seen["timeout"] == 100, "sem escolha no projeto vale o padrão do PC"
    asyncio.run(ClaudeBrain(tmp_path, executable="claude", runner=runner, effort="turbo").ask("x"))
    assert seen["command"][seen["command"].index("--effort") + 1] == "low", "valor desconhecido não chega ao Claude Code"
