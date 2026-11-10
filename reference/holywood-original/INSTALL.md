# Instalar a skill HOLYWOOD

Requisitos: Claude Code (plano Pro, Max, Team ou Enterprise), extensão Claude in Chrome (versão 1.0.36 ou mais nova) logada na mesma conta, Python 3.11 ou 3.12 e FFmpeg.

## Instalação

1. Copie a pasta `holywood` (a que contém `SKILL.md`) para `%USERPROFILE%\.claude\skills\`, de modo que exista `%USERPROFILE%\.claude\skills\holywood\SKILL.md`.
   - Alternativa: envie o ZIP em Customize > Skills no claude.ai e ative a skill.
2. Reinicie o Claude Code ou abra uma nova sessão.
3. Crie uma pasta só para os projetos (por exemplo, `Documentos\HOLYWOOD`), abra o terminal nela e rode `claude --chrome`.
4. Use: `/holywood <link do TikTok>`.

O Claude Code instala o que faltar (Python, FFmpeg, pacotes) com a sua autorização. Há um prompt pronto para isso em `PROMPT_INSTALAR_HOLYWOOD.txt`.
