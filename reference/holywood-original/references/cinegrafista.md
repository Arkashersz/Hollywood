# Módulo 3 · CINEGRAFISTA (documentos 04 a 07)

Objetivo: criar a identidade visual e sonora dos personagens, os ambientes e as imagens-base das cenas, gerando as imagens no ChatGPT (veja `<SKILL_DIR>/references/chatgpt-navegador.md`) e levando cada resultado à aprovação do usuário.

Entradas: documento 03 aprovado, documentos 01 e 02 (estilo visual do original) e `holywood/config.md`.

## Princípios de consistência (valem para tudo)

- **Bloco de estilo global:** um texto fixo que descreve o estilo visual (por exemplo, render 3D de desenho animado, fotorrealista, pintura), proporção, paleta, iluminação, câmera e qualidade. Derive do vídeo de referência (docs 01 e 02) e do tom do roteiro. Cole o bloco, idêntico, em **todo** prompt.
- **Descrição fixa do personagem:** texto imutável, colado inteiro em todo prompt em que o personagem aparece.
- Prompts em inglês (padrão da `config.md`), com texto que apareça dentro da imagem em alemão, entre aspas. Cada prompt traz, no documento, uma explicação curta em português.
- Sem pessoas reais, marcas nem propriedade intelectual de terceiros.
- Nomes de arquivo padronizados (veja `<SKILL_DIR>/references/estrutura-e-estado.md`).

## 3A · Personagens de corpo inteiro → documento 04

1. Defina o bloco de estilo global.
2. Para cada personagem do roteiro registre: papel; **descrição fixa** (idade aparente, porte, rosto, cabelo, olhos, pele, roupa com cores exatas, acessórios, marcas distintivas, postura típica); o que nunca pode mudar; e o prompt de imagem de **corpo inteiro** (pose neutra, vista frontal, fundo liso, dos pés à cabeça, vertical).
3. Gere no ChatGPT (um chat por personagem), inspecione e mostre ao usuário. Itere até a aprovação.
4. Documento 04: bloco de estilo, ficha de cada personagem, prompt final usado e imagem aprovada.
5. **Pare para aprovação** (por personagem). Avance para 3B quando todos estiverem aprovados.

## 3B · Folhas standalone 16:9 → documento 05 · Vozes → documento 06

**Folhas** (depois de aprovada a imagem de corpo inteiro; gere no **mesmo chat**, a partir da imagem aprovada):
- Uma imagem **16:9** por personagem com: corpo inteiro (frente e, se possível, perfil), rosto em close, **expressões faciais** relevantes à história (6 a 8) e detalhes (roupa, acessórios, marcas distintivas). Rótulos curtos em alemão, ou nenhum texto.
- Documento 05: prompt usado, imagem aprovada e lista do que a folha cobre.

**Vozes** (documento 06), uma por personagem:
- timbre, idade aparente, registro (grave, médio, agudo), ritmo, sotaque ou dialeto do alemão, emoção típica e faixa de variação, jeito de falar;
- **linha-descritor de voz** (uma frase em inglês e a versão em alemão) para colar em **todo** prompt de vídeo, mantendo a mesma voz entre clipes;
- regras de consistência (mesma frase-descritor, sem mudar idade ou sotaque);
- se a geração de vídeo não mantiver a voz, indique a alternativa (gerar a fala numa ferramenta de voz com uma voz fixa e sincronizar na edição) e deixe a decisão ao usuário.
- Não descreva voz como imitação de pessoa real.

Aprovação do usuário para 05 e 06 juntos. **Pare.**

## 3C · Ambientes e imagens-base das cenas → documento 07

**Ambientes:** para cada local do roteiro, a descrição fixa (layout, materiais, cores, luz, hora do dia, objetos permanentes), o prompt (com o bloco de estilo) e a imagem aprovada. Ambientes recorrentes são gerados uma vez e reutilizados.

**Imagens-base das cenas** (uma por cena ou âncora do roteiro; a quantidade vem do documento 03):
- Proporção vertical **9:16**.
- Cada prompt traz: composição inicial estável (ângulo, distância, posição dos personagens, direção do olhar, objetos em cena), personagens (cole a descrição fixa), ambiente (cole a descrição fixa), luz e o estado inicial dos objetos. Anexe como referência as folhas dos personagens e a imagem do ambiente (upload pela extensão do navegador).
- Descreva só a imagem **inicial**. O que acontece depois é movimento e vai para o documento 08.
- Documento 07: tabela Cena · Ambiente · Personagens presentes · Prompt · Arquivo aprovado.
- Gere em lotes, para respeitar a cota do plano do ChatGPT, e registre no `estado.md` o que está aprovado e o que falta.

**Pare para aprovação** (por imagem). Avance ao EDITOR quando todas estiverem aprovadas.

## Entregas do módulo

Documentos 04 a 07 (`.md` e `.html` com as imagens), imagens aprovadas em `imagens/` e o `estado.md` atualizado.
