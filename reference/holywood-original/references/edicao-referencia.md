# Documento 02 · Edição da referência

Objetivo: registrar **como o vídeo original foi editado**, para o EDITOR poder reproduzir o efeito geral no CapCut com o conteúdo novo. É parte do Módulo 1 (ANALISTA) e só descreve, não adapta.

Limite importante: você **não ouve** áudio. Música e efeitos sonoros só entram como "confirmado" quando vierem dos metadados; o resto é "inferido" (pela transcrição, pelos silêncios e pelo ritmo dos cortes) ou "a confirmar pelo usuário".

## Como coletar evidências

- Folha de 1 s, candidatos a corte e tiras de fronteira (`analyze_tiktok.py`, com `--boundary-times` nos cortes em que a transição importa).
- Para ver uma transição de perto, extraia quadros densos ao redor do corte: `ffmpeg -ss <t-0.5> -i "<video>" -t 1 -vf fps=15 "<pasta>/trans_%02d.jpg"` e leia as imagens.
- Dados técnicos: `ffprobe -v error -show_entries stream=codec_name,width,height,avg_frame_rate,bit_rate:format=duration -of json "<video>"`.
- Pausas de fala: `ffmpeg -i "<audio.wav>" -af silencedetect=noise=-35dB:d=0.4 -f null -` (lista silêncios; ajuda a separar voz de música e a achar os respiros).
- Metadados do TikTok: nome e autor do som, se existirem no JSON.

## Estrutura do documento

1. **Técnico:** resolução, proporção, fps, duração, codec/bitrate (ffprobe).
2. **Ritmo:** número de planos revisados, duração média e mediana dos planos, se o ritmo acelera ou desacelera, e onde.
3. **Linha do tempo de edição** (tabela): tempo · tipo (corte seco, transição, zoom, shake, speed ramp…) · descrição · evidência (arquivo/quadro) · confiança (confirmado/inferido).
4. **Transições:** catálogo de tipos usados e quantas vezes cada um aparece.
5. **Movimentos de câmera e zooms:** separe o que **parece gerado dentro do vídeo de IA** (movimento do I2V) do que **parece aplicado na edição** (punch-in, zoom digital, tremor). Essa distinção decide o que o CapCut precisa fazer.
6. **Efeitos visuais:** filtros, correção de cor, vinheta, brilho, glitch, bordas, tela dividida, stickers, emojis, sobreposições.
7. **Texto na tela:** idioma, estilo aparente da fonte, tamanho relativo, cor, contorno/sombra, posição, animação de entrada/saída, momento e duração, se é palavra a palavra, e se é legenda de fala ou texto de destaque.
8. **Áudio e mixagem:**
   - música: nome e autor (só se vierem dos metadados), estilo, em que momento entra e sai, volume em relação à voz;
   - efeitos sonoros: quais são prováveis (impacto, whoosh, risers…) e onde, marcados como inferidos;
   - voz: humana ou sintética (se evidente), tratamento aparente (eco, filtro), silêncios;
   - sincronia entre cortes, texto e batida (se perceptível pelo ritmo dos cortes).
9. **Gancho e retenção na edição:** o que aparece nos primeiros 2 s, com que frequência algo muda na tela, se há loop ou chamada final.
10. **Como replicar no CapCut:** para cada técnica, o equivalente mais próximo (marque "a confirmar na biblioteca do app", pois não há como checar nomes exatos daqui) e o que **não** replicar (por exemplo, música com direitos de terceiros; sugira uma alternativa de estilo e andamento semelhantes).
11. **Resumo para o EDITOR:** as 5–8 decisões de edição que mais definem o "jeito" desse vídeo.

Registre incertezas em uma seção própria e peça ao usuário para confirmar a música, se ele a conhecer.
