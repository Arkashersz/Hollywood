# Correções acumuladas (camada 4)

Regra de ouro: se a IA errou **uma** vez, corrija a cena (aba Ajustar ou pedir ajustes). Se errou **duas ou
três** vezes do mesmo jeito, escreva a correção aqui. O worker entrega este arquivo ao Claude em toda
geração de cenas, então cada regra nova vale para todos os projetos seguintes.

Formato: uma linha por regra, começando com `- `. Apague as que não fizerem mais sentido.

- A imagem de segundo zero começou com a ação em andamento: reforce `zero motion, pre-action pose, no particles, no transformation yet` e mova qualquer efeito para a animação.
- Personagem trocou de roupa entre cenas: repita a ficha fixa (peça por peça, com cores) em toda cena que ele aparece.
- Cor do corpo mudou: declare a cor do corpo de forma literal e repetida na descrição do personagem.
- A fala ficou longa para o clipe: divida a cena em dois clipes antes de gerar.
- O cenário mudou sozinho: nomeie o ambiente e use sempre o mesmo nome e a mesma descrição fixa.
- A câmera inventou movimento: defina lente, distância, ângulo e ação de câmera de forma explícita.
- A fruta virou humano: repita cabeça literal de fruta + corpo antropomórfico + cor do corpo baseada na fruta.
- A IA inventou objeto ou personagem: liste só o que deve existir no quadro e declare as ausências importantes.
