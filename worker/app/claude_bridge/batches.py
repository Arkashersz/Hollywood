from __future__ import annotations
import json, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "runtime" / "claude_batches"


def create_batch(*, project_id: str, service: str, purpose: str, items: list[dict], approved: bool = False) -> Path:
    """Create a durable task packet. It never launches Claude or controls the browser."""
    if service not in {"chatgpt", "grok"}:
        raise ValueError("service deve ser chatgpt ou grok")
    if not items:
        raise ValueError("O lote precisa conter pelo menos um item")
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = ROOT / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {
        "batch_id": batch_id, "project_id": project_id, "service": service,
        "purpose": purpose, "created_at": datetime.now(timezone.utc).isoformat(),
        "user_authorized": bool(approved), "status": "ready" if approved else "awaiting_user_authorization",
        "items": items,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Lote {batch_id}", "", f"- Projeto: `{project_id}`", f"- Serviço: **{service}**",
        f"- Objetivo: {purpose}", f"- Itens: {len(items)}", "",
        "## Regra de autorização", 
        "Este pacote não autoriza geração por si só. Confirme no painel que o usuário aprovou este lote. "
        "Se `user_authorized` for false, não abra o serviço nem envie prompts.", "",
        "## Itens do lote", ""
    ]
    for i, item in enumerate(items, 1):
        lines += [f"### {i}. {item.get('title', 'Item sem título')}", "", f"**Destino:** `{item.get('output_path','a definir')}`", "", "**Prompt:**", "", "```text", item.get("prompt", ""), "```", ""]
        refs = item.get("references", [])
        if refs:
            lines += ["**Referências:**", *[f"- `{x}`" for x in refs], ""]
    lines += ["## Entrega", "", "Atualize `result.json` com status por item, arquivos originais efetivamente baixados, erros e pendências. Não declare sucesso sem verificar o arquivo."]
    (folder / "task.md").write_text("\n".join(lines), encoding="utf-8")
    (folder / "result.template.json").write_text(json.dumps({"batch_id": batch_id, "status": "not_started", "items": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def authorize_batch(batch_id: str) -> Path:
    folder = ROOT / batch_id
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Lote não encontrado: {batch_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "awaiting_user_authorization":
        raise ValueError("Lote não está aguardando autorização")
    manifest["user_authorized"] = True
    manifest["status"] = "ready"
    manifest["authorized_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def next_batch() -> Path | None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for folder in sorted(ROOT.iterdir()):
        manifest = folder / "manifest.json"
        if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")).get("status") == "ready":
            return folder
    return None
