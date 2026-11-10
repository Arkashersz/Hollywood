# Estrutura de pastas, estado e documentos

## Pastas (na pasta de trabalho atual)

```
holywood/
├── config.md
└── projetos/
    └── 001-<slug>/
        ├── estado.md
        ├── referencia/            vídeo, metadados, folhas, áudio, transcrição, relatorio/
        ├── 01-ficha-da-referencia.md  (+ .html)
        ├── 02-edicao-da-referencia.md (+ .html)
        ├── 03-roteiro-de.md           (+ .html)
        ├── 04-personagens.md          (+ .html)
        ├── 05-folhas-dos-personagens.md (+ .html)
        ├── 06-vozes.md                (+ .html)
        ├── 07-ambientes-e-cenas.md    (+ .html)
        ├── 08-prompts-grok.md         (+ .html)
        ├── 09-plano-edicao-capcut.md  (+ .html)
        ├── 10-kit-publicacao.md       (+ .html)
        └── imagens/
            ├── _previews/   capturas para aprovação
            ├── personagens/ personagem-<nome>-corpo.png
            ├── folhas/      folha-<nome>.png
            ├── ambientes/   ambiente-<nome>.png
            ├── cenas/       cena-<NN>.png
            └── capa/        capa.png
```

- Numeração do projeto: o próximo `NNN` livre. `<slug>`: autor + id do vídeo, em minúsculas, sem espaços.
- Nunca edite os originais do usuário nem arquivos fora de `holywood/` sem pedir.

## config.md (criado na primeira execução)

```
# Configuração HOLYWOOD
- Duração máxima validada na interface do Grok: <N> s (data da validação) ou "não verificada"
- Modalidade disponível no Grok: <imagem inicial por cena | referências/anexos | ambos | não verificada>
- Idioma dos prompts: inglês (falas em alemão entre aspas)
- Pasta de Downloads do Chrome: <caminho>
```

## estado.md (atualize antes de cada parada)

```
# Estado do projeto <NNN-slug>
- Link: <url>
- Criado em: <data>
- Módulo atual: <1 ANALISTA | 2 ROTEIRISTA | 3A | 3B | 3C | 4 EDITOR>

## Documentos
| Nº | Documento | Status (pendente / em revisão / aprovado) | Observações |

## Imagens
| Arquivo | Tipo | Status | Prévia |

## Pendências e decisões do usuário

## Registro (data · o que foi feito)
```

## Páginas .html de leitura

Cada documento tem um `.html` irmão, autocontido, para o usuário revisar com as imagens (caminhos relativos). Use um estilo simples:

```html
<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TÍTULO</title>
<style>
body{font:16px/1.6 Arial,sans-serif;max-width:1100px;margin:30px auto;padding:0 20px;color:#1d2b27}
h1{font-size:28px}h2{font-size:22px;margin-top:34px}img{max-width:100%;height:auto;border-radius:6px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #cbd4ce;padding:8px;text-align:left;vertical-align:top}
pre,code{background:#f1f4f2;padding:2px 5px;border-radius:4px;white-space:pre-wrap}
.aprovar{background:#eef7f1;border:1px solid #b9d9c4;padding:12px;border-radius:6px}
</style>
```

Termine cada página com uma caixa `.aprovar` dizendo o que o usuário precisa decidir. Escape o texto vindo de fontes externas.
