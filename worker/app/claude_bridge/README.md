# Claude Code batch bridge

O Python cria pacotes de trabalho; não inicia Claude Code, não dirige o Chrome e não manipula cookies.

- `create_batch(...)`: cria `manifest.json`, `task.md` e `result.template.json`.
- `python -m app.claude_bridge.cli authorize <batch_id>`: usado pelo backend após aprovação explícita do lote.
- `python -m app.claude_bridge.cli next`: lista o próximo pacote autorizado.

O usuário executa `claude --chrome` no workspace e entrega o caminho `task.md` à skill `holywood`. Isso preserva a sessão do Chrome existente e torna visível quando a cota do Claude Code será utilizada.
