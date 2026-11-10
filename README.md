# TikTok Studio

Produção de vídeos originais para o TikTok em alemão, a partir de um vídeo de referência. Três peças:

- **Supabase** — login, projetos, fila de tarefas, aprovações, eventos e prévias.
- **Worker Python** (no seu PC) — conduz as nove etapas e faz a ponte entre as ferramentas.
- **Painel web** (`apps/web`, também empacotável com Tauri) — onde você revisa, aprova, pede ajustes e regenera itens, do computador ou do celular.

Quem faz o quê:

- **Claude** (Claude Code deste PC, em modo não interativo, com a sua assinatura) é o cérebro: vê os quadros do vídeo, identifica gancho, CTA e estratégia do criador, escreve o roteiro e cria os prompts de imagem e de vídeo.
- **ChatGPT** (navegador) gera as imagens a partir dos prompts aprovados.
- **Grok** (navegador) gera os vídeos a partir dos prompts aprovados.
- **Python** baixa e prepara o vídeo (quadros, cortes, transcrição), entrega os pedidos ao Claude, confere o formato das respostas, despacha os prompts e guarda tudo. Nada avança sem a sua aprovação.

## As nove etapas

| # | Etapa | Quem faz | Entrega |
| --- | --- | --- | --- |
| 1 | Análise da referência | Python (download, quadros, cortes, transcrição) + Claude | Documentos 01 e 02 |
| 2 | Roteiro em alemão | Claude | Documento 03 |
| 3 | Personagens e vozes | Claude | Documentos 04, 05 e 06 |
| 4 | Imagens dos personagens | ChatGPT | Corpo inteiro de cada personagem |
| 5 | Folhas 16:9 | ChatGPT | Uma folha por personagem |
| 6 | Ambientes e cenas | Claude | Documento 07 (oito áreas) |
| 7 | Imagens de ambientes e cenas | ChatGPT | Ambientes e imagem-base de cada cena |
| 8 | Prompts de vídeo, edição e publicação | Claude | Documentos 08, 09 e 10 |
| 9 | Vídeos | Grok | Um clipe por prompt |

Aprovar uma etapa de texto é o que autoriza o lote de geração seguinte; o painel mostra quantos itens serão gerados antes de você aprovar.

## Preparação (uma vez)

1. **Supabase:** as migrações `0001` a `0004` de `supabase/migrations/` já foram aplicadas no projeto configurado. Para aplicar migrações futuras (ou em outro projeto): `npm install --no-save pg` e `node supabase/apply.mjs`, com `SUPABASE_DB_PASSWORD` no ambiente; ou cole os arquivos, em ordem, no SQL Editor.
2. **Worker:** `worker/.env` (ignorado pelo Git) guarda `SUPABASE_URL` e `SUPABASE_SECRET_KEY` (`sb_secret_…`). Essa chave fica só neste PC.
3. **Painel:** `apps/web/.env` guarda `VITE_SUPABASE_URL` e `VITE_SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…`, que é pública).
4. **Dependências:** Python 3.12, Node.js, FFmpeg, Google Chrome e Claude Code. Na raiz: `npm install`. O `worker\start-worker.ps1` cria o ambiente Python sozinho.
5. **Login no Claude:** rode `claude` uma vez neste PC e entre na sua conta. O worker usa esse login; nenhuma chave de API é usada.
6. **Login no ChatGPT e no Grok:** em `worker/`, rode `.\.venv\Scripts\python.exe -m app.cli login`, entre nas duas contas e feche todas as janelas desse Chrome. Depois rode `... -m app.cli check`.

## Uso diário

**Um clique (recomendado):** rode uma vez `.\criar-atalho.ps1` (na raiz). Ele cria o atalho **TikTok Studio** na Área de Trabalho; ao abrir, o atalho inicia o worker (se não estiver rodando), o painel local e abre o painel numa janela própria de aplicativo. Para o worker iniciar sozinho quando você entrar no Windows: `.\criar-atalho.ps1 -InicioAutomatico`. Para encerrar tudo: `.\iniciar-tiktok-studio.ps1 -Parar`.

Na primeira vez, crie a conta no painel, cole o link do TikTok (ou o caminho de um `.mp4` no PC do worker) e acompanhe as etapas.

## Painel: PC, celular e web

- **PC:** o atalho acima. Também dá para abrir `http://127.0.0.1:4173` em qualquer navegador e usar *Instalar app* (o painel é um PWA instalável).
- **Celular / computador do trabalho (Vercel):** o painel é um site estático e a configuração já está em `vercel.json`. Passo a passo:
  1. Suba esta pasta para um repositório no GitHub (o `.gitignore` já exclui `.env`, `runtime/` e o perfil do Chrome) e importe o repositório na Vercel. Não mude o *Root Directory*; o build usa os comandos do `vercel.json`.
  2. Em *Settings → Environment Variables*, crie `VITE_SUPABASE_URL` e `VITE_SUPABASE_PUBLISHABLE_KEY` (os mesmos valores de `apps/web/.env`; a chave `sb_publishable_…` é pública) e faça o deploy.
  3. **Primeiro acesso:** abra o site, crie a sua conta e **crie o primeiro projeto você mesmo**. Quem cria o primeiro projeto vira o único dono: outras contas conseguem entrar, mas não criam projetos nem acionam o worker (travado no banco, migração `0004`). Depois, no Supabase em *Authentication → Sign In / Providers*, você pode desligar *Allow new signups*.
  4. No Supabase, em *Authentication → URL Configuration*, ponha o endereço da Vercel em *Site URL* (senão o link do e-mail de confirmação da conta aponta para o lugar errado).
  5. No celular, abra o site e use *Adicionar à tela inicial*.

  O worker continua no PC de casa, ligado; o site e o celular só leem e aprovam pelo Supabase. Cada etapa continua parando para a sua aprovação, de qualquer lugar.
- **Aplicativo Tauri (opcional):** a casca está em `apps/desktop` (`npm run desktop:dev` / `desktop:build`), mas exige instalar o Rust e as Build Tools do Visual Studio, que não estão neste PC. O atalho e o PWA entregam o mesmo painel sem isso.

Os arquivos originais ficam em `runtime/projects/<id do projeto>/` (documentos `.md`, `imagens/`, `videos/`). O Supabase guarda os textos e prévias leves.

## Quando uma etapa para

O worker nunca digita senha, resolve CAPTCHA, contorna limite ou insiste às cegas. Ele para, explica no painel e espera você:

- **Login ou verificação de segurança:** resolva na janela do Chrome do worker e clique em *Tentar novamente*.
- **Limite de uso** (Claude, ChatGPT ou Grok): aguarde a cota; ao tentar de novo, o que já foi gerado é mantido. Cada etapa de texto consome a cota do seu plano Claude.
- **Interface do site mudou:** `python -m app.cli check` mostra quais seletores deixaram de bater; corrija em `worker/selectors.json` (mesmo formato de `worker/app/browser/selectors.py`). Como alternativa, gere o item à mão e salve no caminho indicado no erro: o worker aceita o arquivo existente.

## Estado da validação

Verificado:

- SQL: `node supabase/tests/run.mjs` (Postgres local) — etapas, aprovações, isolamento entre usuários, fila e bucket.
- Worker e painel: `cd worker; .\.venv\Scripts\python.exe -m pytest` — análise, pipeline, chamada do Claude, automação do navegador e painel contra simulações locais.
- **Supabase real:** migrações aplicadas; o worker conecta e registra presença; sem login, a chave pública não enxerga nenhuma linha nem consegue reivindicar tarefas.
- **Supabase + worker + Claude, ponta a ponta:** com um usuário de teste (apagado depois), criar projeto pelo painel, a análise rodar no worker, as imagens de evidência chegarem ao bucket privado e outro usuário não ver nada: tudo confirmado. A aprovação seguida do roteiro foi confirmada no Claude isolado, mas o roteiro **demora**: com esforço alto passou de 15 min duas vezes. Por isso o padrão é `CLAUDE_EFFORT=low` (≈3 min por texto); aumente em `worker/.env` se quiser textos mais elaborados e aceitar a espera.
- Sites reais **sem login**: conexão com o Chrome, caixa de mensagem, botão de envio, campo de anexo e detecção de "sem sessão" no ChatGPT e no Grok.

- **ChatGPT real (imagens):** sessão ativa; uma imagem de personagem (1024×1536) e uma folha 16:9 (1672×941) geradas com a imagem anterior como referência, baixadas em PNG e com identidade consistente.

**Ainda não verificado**: todo o fluxo de vídeo no Grok logado (não testado de propósito) e a leitura de respostas de *texto* do ChatGPT (só usada com `BRAIN=chatgpt`). Faça o primeiro projeto acompanhando a janela do Chrome; espere ajustar seletores. Também falta um projeto completo passando pelo painel com um vídeo real do TikTok.

Automatizar a interface desses serviços pode contrariar os termos de uso deles e levar a restrições na conta; as APIs oficiais são o caminho suportado. A decisão de usar o navegador é sua.

## Outros comandos

- `python -m app.cli analyze <link ou .mp4>` — só a análise local, sem navegador nem Supabase.
- `python -m app.cli local <link ou .mp4>` — pipeline completo sem Supabase, com aprovação no terminal.

A skill HOLYWOOD original está em `reference/holywood-original/`; a versão em `skills/holywood/` e a ponte `worker/app/claude_bridge/` são uma rota manual opcional (Claude Code operando o navegador por lotes) e não fazem parte do fluxo automático.
