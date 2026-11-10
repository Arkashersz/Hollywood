# Plano de implementação por entregas

## Entrega 1 — fundação (este scaffold)
- Monorepo inicial.
- Painel web responsivo inicial.
- SQL do Supabase com RLS e RPCs para aprovar etapa e reivindicar job.
- Worker local com polling, heartbeat e estados.
- Contratos dos adaptadores ChatGPT/Grok.

## Entrega 2 — Supabase real
- Criar projeto e aplicar migração.
- Login e isolamento por usuário.
- Realtime para estados e aprovações.
- Storage privado para prévias e artefatos leves.

## Entrega 3 — pipeline Analista
- Entrada de URL/MP4.
- Download apenas por meios permitidos e quando disponível; alternativa upload MP4.
- FFmpeg/ffprobe, transcrição e storyboard.
- Relatório 01/02 e aprovação.

## Entrega 4 — roteiros
- Remodelar e Roteirista como agentes distintos.
- Roteiros em alemão com tradução PT.
- Comparação de versões e aprovação.

## Entrega 5 — Designer
- Character Bible e oito seções da referência visual.
- Prompts de personagens, folhas, ambientes, cenas e storyboard.
- ChatGPT browser adapter e revisão de imagens.

## Entrega 6 — Grok e finalização
- Adaptador Grok browser-first.
- Prompts de vídeo por clipe, controle de duração e continuidade.
- Documentos 08–10 e organização dos arquivos.

## Critérios de aceite
- Nenhuma etapa avança sem aprovação persistida.
- Retomar produção após reinício do PC.
- Painel funciona em celular e desktop.
- Worker nunca expõe porta pública nem credenciais.
- Toda falha de integração fica registrada com etapa, categoria e ação recomendada.
