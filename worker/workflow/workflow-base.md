# Workflow de animação — base

Esta é a camada 2 do sistema (bíblia visual → **workflow** → prompt da cena → correções acumuladas).
O worker lê este arquivo a cada vez que monta as cenas, então pode editá-lo à vontade: é um ponto de
partida para treinar, não uma versão final. Quando a IA errar duas ou três vezes do mesmo jeito, a
correção entra em `regras-aprendidas.md` (camada 4), não aqui.

Tudo é vertical **9:16**: imagens de personagem, de ambiente, de cena e vídeos.

## 1. Estilo gráfico e identidade visual

- O estilo vem do perfil de produção aprovado e vira um **bloco de identidade visual fixo**, escrito uma
  vez e colado idêntico em todo prompt de imagem. Nunca reescreva o bloco cena a cena.
- Estilo padrão: filme de animação 3D de longa-metragem (nível Disney/Pixar/DreamWorks), iluminação
  volumétrica cinematográfica, 4k. Para outro estilo basta trocar este bloco; o resto do workflow não muda.

## 2. Anatomia dos personagens

- Nicho de frutas: o personagem é uma fruta viva. A **cabeça é a própria fruta**, com a textura real da
  casca (poros da pera, brilho da uva, textura fosca da ameixa) e um rosto expressivo. O corpo é de
  proporção humana, com braços e pernas de desenho animado, e a **cor do corpo deriva da cor da fruta**.
- Idade aparente e papel seguem o contexto da história e das referências.
- Descreva a anatomia de forma literal e repetida em toda ficha (cabeça de fruta, corpo, cor do corpo),
  para a fruta não virar humano.
- Outros nichos: a bíblia define uma anatomia igualmente literal para o tipo de personagem escolhido.

## 3. Sincronia de tempo realista (sem barriga)

- A duração de cada cena sai da **duração real da fala** no idioma do perfil, mais o tempo da ação.
  Nada de cena esticada para completar tempo e nada de fala que não caiba.
- Limite absoluto de **10 segundos por clipe** (image-to-video). Fala longa demais: divida a cena em
  dois clipes (ex.: 7a e 7b), cada um com a sua imagem de segundo zero.
- Cena que só tem ação, sem fala, dura o que a ação precisa, dentro do mínimo e do máximo do perfil.

## 4. Imagem estática no segundo zero (pre-action)

- O prompt de imagem descreve a **foto do segundo exato zero, antes de a ação começar**: cenário limpo,
  personagens na pose de preparação, objetos e materiais já nas mãos de quem os usa.
- Nenhuma transformação, movimento, arremesso, partícula, explosão, efeito ou aparição de elementos entra
  na imagem. Tudo isso acontece **somente** no comando de animação e câmera.
- Liste só o que existe naquele quadro e declare as ausências importantes (ex.: "no other characters").

## 5. Animation & Camera Action

- Descreve, em ordem, o que acontece durante o clipe: movimento dos personagens, expressões, objetos,
  efeitos e som ambiente, mais a câmera (lente, distância, ângulo e movimento explícitos).
- O detalhe vai numa linha do tempo com horários (ex.: 0.0–0.6 s, 0.6–2.4 s), cada trecho dizendo o
  que cada personagem visível faz com o corpo, qual mão, o olhar e a boca. Quem não fala fica de boca
  fechada. O worker transforma isso nas travas do prompt (animação, câmera, fala, voz, visual e continuidade).
- Não repita a aparência dos personagens (ela vem da imagem) e não invente objetos ou personagens novos.
- As falas entram no clipe com o nome do personagem, na ordem em que são ditas.

## 6. Consistência

- Roupa, acessórios, cor do corpo e anatomia vêm da bíblia e não mudam entre cenas, a menos que o
  roteiro mostre a troca (e diga em que cena e a partir de quando).
- Cenário recorrente tem nome e descrição fixos; use sempre o mesmo nome.

## 7. Padrão de entrega (por cena)

```
#### CENA [número] - [nome curto]
* Imagem (9:16): prompt em inglês, ultra detalhado, do segundo zero / pre-action
* Animation & Camera Action: movimento e câmera do clipe
* Áudio e Diálogos: Nome da personagem (gênero): "fala"
* Duração: segundos (limite de 10 por clipe)
```

Descrições estruturais e comandos em português; prompts de imagem e de animação em inglês; diálogos
no idioma do perfil, organizados pelo nome de cada personagem.
