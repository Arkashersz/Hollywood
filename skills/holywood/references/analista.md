# Módulo 1 · ANALISTA (documentos 01 e 02)

Objetivo: entender o vídeo de referência (gancho, tema, estrutura, áudio, imagens-âncora, movimento e edição) e produzir evidências e documentos que alimentam os módulos seguintes. Escopo: **somente análise**. Não escreva roteiro novo, não proponha adaptação, não pontue potencial viral.

A análise de edição (documento 02) está em `<SKILL_DIR>/references/edicao-referencia.md`.

## 1. Obter o vídeo

- Pasta de trabalho: `referencia/` dentro do projeto.
- Arquivo local informado: use direto.
- Link do TikTok:
  1. `python -m yt_dlp` (o `analyze_tiktok.py` já faz isso quando recebe a URL).
  2. Se falhar (403, 429, `Unexpected response`, login obrigatório) **ou** baixar só vídeo sem áudio: rode `python "<SKILL_DIR>/scripts/fetch_tiktok_fallback.py" "<URL>" --out "<projeto>/referencia" --name "<slug>"`. Ele usa o stream `play`, que traz áudio. **Não use `--hd`**, que pode vir sem áudio. Se o yt-dlp estiver desatualizado, tente `python -m pip install -U yt-dlp` antes do fallback.
  3. Só se as duas rotas falharem: registre os dois erros e peça ao usuário o .mp4 (e o JSON de metadados, se ele tiver).
- Falha HTTP do TikTok não é falta de FFmpeg. Classifique: `python` ausente; `No module named`; `ffmpeg`/`ffprobe` ausente; 403/429 (recusa do TikTok ou do serviço); timeout/DNS/proxy; TikWM sem URL; vídeo sem áudio.
- Confirme com `ffprobe` que há stream de vídeo e, quando esperado, de áudio.
- Metadados (`*_tikwm.json` ou `*.info.json`): legenda/título, **hashtags**, autor, duração, **data e hora de postagem**, métricas, **nome e autor do som** (se existirem no arquivo; confirme no primeiro vídeo quais campos vieram). Se não houver metadados, diga que não foram obtidos; nunca invente.

## 2. Gerar as evidências

```
python "<SKILL_DIR>/scripts/analyze_tiktok.py" "<arquivo.mp4>" --out "<projeto>/referencia" --name "<slug>" --language <pt|de|en>
```

Opções úteis: `--model small` (melhor para alemão ou áudio ruim; baixa um modelo maior na primeira vez), `--no-transcribe`, `--initial-prompt "nomes evidenciados"`, `--boundary-times 4.2 9.8` (tiras locais em cortes disputados), `--scene-threshold 26` (sensibilidade fixa, nunca ajustada para obter uma contagem), `--install-deps` (só se faltarem pacotes).

Artefatos: `*_probe.json`, `*_1s_sheet.jpg`, `*_detected_scenes.jpg`, `*_scene_starts.txt`, `*_audio.wav`, `*_transcript.json`, `*_boundary_strips.jpg`.

- Os candidatos a corte (rótulos C) **não são âncoras**. A transcrição automática (ASR) **não é verificada** até ser conferida com o áudio e com legendas na tela.
- **Olhe você mesmo as imagens** (leia os JPG). O script não sabe se dois clipes vêm de uma ou duas imagens geradas.

## 3. Validar a "ontologia" antes de interpretar o enredo

- Identifique o que cenário, personagens, cores, objetos, transformações e nomes representam, literal ou metaforicamente.
- Cruze imagem, fala, legendas na tela, título/legenda do post e a continuidade de causa e efeito.
- Não deduza o cenário só pelo estilo visual (um ambiente vermelho orgânico pode ser anatomia, não inferno).
- Se ficar ambíguo, apresente as interpretações concorrentes e a evidência de cada uma. Nunca escolha uma em silêncio.
- Correções do usuário sobre o que algo representa valem como verdade, salvo contradição direta da fonte.
- Separe o que é visível, o que a fala ou as legendas afirmam e a sua interpretação. Não invente fatos médicos ou causais. Não deduza gravidez, doença etc. de formas do corpo ou de falas ambíguas. Atribua alegações ficcionais ao vídeo.

## 4. Reconstruir as prováveis imagens-âncora

Três níveis:
- **Âncora:** a imagem-fonte provável usada para iniciar uma geração image-to-video (I2V).
- **Plano (shot):** uma visão contínua editada, que pode reaproveitar uma âncora.
- **Estado de movimento/beat:** algo que acontece dentro do I2V; **não** é outra âncora numerada.

Regras:
- Não divida só por mudança de estado de objeto, ação, transformação, expressão, movimento de personagem, clímax, punch-in, pan, zoom, dissolve ou outra edição. Um clipe I2V cria tudo isso a partir de uma imagem, inclusive mudanças grandes.
- Não una só porque o local, os personagens, a função narrativa ou a paleta coincidem. Dois planos na mesma sala podem exigir imagens diferentes quando a **composição estável inicial** difere (ângulo, distância, posicionamento, elenco visível, arranjo de objetos, ação focal). Um ator que entra num plano contínuo, por si só, não é nova âncora.
- Em cada corte aparente, compare o último quadro estável antes com o primeiro depois. Una apenas com **evidência positiva de continuidade** (movimento ou câmera contínuos, geometria espacial e recorte coincidentes, reuso do mesmo cenário-fonte). "O I2V poderia gerar o estado seguinte" não basta. Trate um novo cenário estável após corte seco como divisão provisória e teste se um recorte editorial ou uma animação contínua o explica.
- Audite todo candidato a corte e toda mudança de bloco de fala, sobretudo pares fáceis de unir demais: plano de grupo vs. reação de um personagem; antes/depois de outro personagem entrar; plano aberto vs. confronto fechado no mesmo cômodo. Registre cada divisão disputada individualmente.
- A detecção de cenas só fornece candidatos. Nunca ajuste limiar para chegar a uma contagem desejada nem use a contagem de candidatos como resposta. Uma folha de 1 s pode perder continuidade.
- Em fronteiras disputadas, olhe uma tira local (−0,6; −0,3; 0; +0,3; +0,6 s), ampliando ou reproduzindo o trecho quando preciso. Registre a evidência de unir/dividir; mantenha a incerteza quando a evidência não decidir.
- Agrupe cenários reaproveitados sob IDs estáveis (A01, A02…), listando todas as ocorrências em ordem cronológica. Distinga âncoras únicas de totais de planos/ocorrências.
- A contagem exata de gerações é **incognoscível** só pelo vídeo final. Dê a melhor estimativa com confiança e faixa justificada, dizendo quais fronteiras mudam o total. Se o usuário corrigir plano a plano, reconcilie com o vídeo e atualize; não defenda a contagem antiga fundindo composições distintas.
- Mantenha **reconstrução da fonte** separada de **plano de produção mínima** (só quando pedido).

## 5. Descrever e montar o storyboard

- Comece com: "Minha melhor estimativa é N âncoras únicas (confiança X, faixa Y–Z)", rotulado como reconstrução da fonte.
- Para cada âncora, extraia **uma** imagem representativa perto da composição inicial estável. Indique o instante real; se a imagem-fonte não está visível, chame de quadro-proxy. Quadros das folhas de candidatos não são representantes aprovados.
- Para cada âncora: ID, tempos de ocorrência, imagem representativa, composição inicial, personagens/objetos, fala ou narração, ação image-to-video, ligação com o contexto, função narrativa e confiança de unir/dividir quando disputado.
- Bloco **separado** de movimento I2V por âncora: movimento de personagens, câmera, objetos, boca/expressões, efeitos, transições e continuidade de causa e efeito, em ordem. Preserve todos os beats (transformações, payoffs) no campo ordenado da âncora, sem criar âncoras extras.
- Conecte planos a ações anteriores só quando a fonte sustentar; marque o resto como interpretação.
- **Transcrição completa**, com tempos, de cada vídeo: ASR com tempos, ouça trechos incertos e confira tudo com legendas na tela. Mantenha o texto extra que só aparece em legenda separado e aponte divergências entre áudio e legenda; não troque a fala pela legenda em silêncio. Marque apenas as palavras e trechos realmente incertos. Não afirme verificação completa sem ter checado, não omita falas finais, não troque transcrição por resumo. Para gírias, nomes, legendas de uma palavra, vozes sobrepostas ou sintéticas, compare o ASR com a sequência de palavras visível e corrija só o que a evidência sustenta. Se a fala não for português, inclua tradução para o português ao lado.
- Se a transcrição precisa for exigida, tente um modelo ASR local e a conferência com legendas antes de responder; relate a limitação técnica real se não der.

## 6. Manifesto e relatórios

Crie o manifesto revisado conforme `<SKILL_DIR>/references/manifest-schema.md` e rode:

```
python "<SKILL_DIR>/scripts/render_storyboard_report.py" "<projeto>/referencia/<slug>_anchors.json" --video "<projeto>/referencia/<slug>.mp4" --metadata "<projeto>/referencia/<slug>_tikwm.json" --out "<projeto>/referencia/relatorio"
```

Gera `storyboard_<slug>.jpg`, `analise_<slug>.html` e `analise_<slug>.md`. Não coloque candidatos do detector direto no manifesto: toda âncora é revisada manualmente. Um quadro do storyboard é um proxy do vídeo final, nunca prova de ser a imagem-fonte original. (Sem `--metadata`, o renderizador omite métricas; isso é aceitável.)

## 7. Documento 01 · Ficha da referência (conteúdo obrigatório)

1. **Identificação:** link, autor, título/legenda, **hashtags originais**, **data e hora de postagem** (Brasília e Berlim; se o dado não existir, diga), duração, formato (resolução, fps) e métricas com a data da consulta.
2. **Resumo em 3 linhas** e a ontologia (o que cada elemento representa).
3. **Gancho (0–3 s):** o que acontece e por que prende.
4. **Estrutura em beats com tempos:** gancho → conflito → escalada → clímax/payoff → fecho, loop ou chamada final.
5. **Tema, emoção principal e público provável** (rotule o que é inferência).
6. **Áudio:** tipo (narração, diálogo, música, só texto), idioma, voz (humana ou sintética, quando evidente), tom.
7. **Âncoras:** estimativa, confiança, faixa, divisões disputadas, storyboard e tabela.
8. **Image-to-video:** bloco de movimento de cada âncora.
9. **Transcrição completa** com tempos, tradução e incertezas.
10. **Resumo para o ROTEIRISTA:** o que é essencial preservar (mecânica do gancho, tema, conflito, ritmo, tipo de áudio, duração mínima) e o que é específico do original e portanto deve mudar (nomes, personagens, falas, marcas). Sem escrever a adaptação.
11. **Incertezas e perguntas** para o usuário.

Inclua no `.html` o storyboard visual e links para os relatórios gerados.

## 8. Fechamento

Atualize o `estado.md`, resuma (âncoras, gancho, tema, tipo de áudio, principais incertezas), liste os arquivos 01 e 02 e **pare para aprovação** antes do ROTEIRISTA.
