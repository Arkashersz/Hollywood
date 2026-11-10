> **Nota (v0.2):** esta rota por lotes passou a ser opcional. O fluxo padrão é o worker Python controlando o Chrome diretamente; veja o `README.md`.

# Claude Code por lotes — política operacional

## Decisão
O Python é o orquestrador e executa download/análise/transcrição/relatórios/arquivos/estado. Claude Code não roda como daemon e não é invocado automaticamente pelo worker.

## Handoff
1. Python cria `runtime/claude_batches/<id>/manifest.json` e `task.md`.
2. O painel apresenta a quantidade de itens e solicita aprovação explícita do lote.
3. Após aprovação, o lote fica `ready`.
4. O usuário inicia/retoma Claude Code no workspace: `claude --chrome`.
5. O usuário pede a execução do arquivo `task.md` com a skill `holywood`.
6. Claude Code usa a extensão no Chrome existente, com login já feito pelo usuário.
7. Resultado e arquivos são registrados; Python importa/valida os arquivos e atualiza o projeto.

## Por que não disparar Claude Code silenciosamente pelo Python?
A integração da extensão de navegador depende de uma sessão ativa do Claude Code e de autorização/interação do usuário. Não há garantia de que um processo Python consiga assumir essa sessão com segurança ou sem consumo adicional. O handoff explícito evita processos invisíveis, reduz chamadas e mantém controle sobre a cota semanal.

## Otimização de cota
- Agrupar prompts por etapa/personagem/ambiente em lotes coerentes.
- Não pedir ao Claude para reanalisar o vídeo se o relatório Python já existe.
- Fornecer no pacote somente roteiro aprovado, Character Bible, referências e prompts pendentes.
- Reutilizar arquivos de prompt aprovados; não regenerar sem alteração solicitada.
- Separar geração ChatGPT e Grok para autorizar cada lote.
