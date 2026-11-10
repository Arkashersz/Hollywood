# Migração da skill HOLYWOOD para TikTok Studio

A pasta `reference/holywood-original/` preserva a skill recebida sem alterações. Este documento define a adaptação que será aplicada na implementação; não sobrescreve nem reescreve a fonte original.

## Mudanças obrigatórias
- Todo texto, referência, nome de arquivo e instrução de produção que diga **Flow** como gerador de vídeo deve ser atualizado para **Grok**.
- O agente envia prompts e referências ao Grok via adaptador browser-first; recebe o vídeo quando a interface permitir e salva o arquivo no projeto.
- Imagens continuam sendo geradas no **ChatGPT**, também pelo adaptador browser-first.
- O orquestrador passa a ser o worker Python local, não o Claude Code.
- O Claude Code deixa de ser requisito de execução diária; pode ser usado apenas para desenvolvimento/manutenção.
- Aprovações e estado deixam de depender de `estado.md` como fonte primária: Supabase é a fonte de verdade. `estado.md` e documentos Markdown/HTML podem ser exportados como artefatos de projeto.

## Conteúdo da skill a preservar
- Idioma do roteiro/falas/textos do vídeo: alemão; documentação e interface: português do Brasil; tradução PT ao lado.
- Duração-alvo original: acima de 70 s, mirando 75–95 s, até que o usuário altere essa regra.
- Inspiração sem cópia; separação entre confirmado, inferido e a confirmar.
- Documentos numerados 01–10, módulos Analista, Roteirista, Cinegrafista e Editor.
- Aprovação entre cada módulo e subetapa.
- Consistência de estilo global, descrição fixa de personagens e continuidade visual.

## Designer: oito áreas obrigatórias da referência visual
1. Character Reference.
2. Environment / Set Design.
3. Lighting / Mood / Style.
4. Area Flow Plan / Camera Movement.
5. Camera Work / Shot Flow.
6. Shooting Technique.
7. Storyboard / Sequence Flow (por parte/clipe).
8. Shooting Technique & Notes.

Cada entrega de design deve conter prompts de imagem de personagens, ambientes e cenas; prompts de vídeo para Grok; proporção, lente/enquadramento, movimento de câmera, iluminação, mood, continuidade, ações, duração e notas técnicas. A imagem enviada pelo usuário é referência estrutural; detalhes ilegíveis nela não serão inventados.

## Ajustes no documento 08
Renomear `08-prompts-flow` para `08-prompts-grok` (ou manter alias de migração), substituir instruções de geração no Flow por Grok e manter duração de cada clipe compatível com os limites verificados na interface do Grok no momento da produção. Não assumir limites fixos sem validar.

## Regras do navegador
- Login sempre feito manualmente pelo usuário.
- Não preencher credenciais, não resolver CAPTCHA, não contornar controles, não abrir conversas privadas alheias ao projeto.
- Pedir aprovação do usuário antes de cada lote de geração, além das aprovações formais de cada etapa.
- Em caso de limite, bloqueio ou mudança de interface, pausar e registrar o motivo.
