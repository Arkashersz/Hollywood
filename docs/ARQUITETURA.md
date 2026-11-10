# Arquitetura técnica — TikTok Studio

## Decisões aprovadas
- Integrações IA: browser-first, priorizando assinaturas atuais.
- Banco/backend: Supabase (Postgres, Auth, Realtime, Storage quando necessário).
- Interfaces: aplicação Windows com Tauri + painel web responsivo.
- Execução: worker Python local em segundo plano, ativo enquanto o PC estiver ligado.
- APIs pagas: não usar inicialmente; manter adaptadores futuros sem acoplar o produto a eles.

## Componentes
1. **Web (React + Vite):** painel de projetos, visualização de artefatos, revisão e aprovação.
2. **Desktop (Tauri):** invólucro Windows para a mesma experiência web; no futuro controla inicialização/estado do worker.
3. **Worker local (Python):** recebe jobs autorizados, executa análise local e automações de navegador, publica resultados e heartbeat.
4. **Supabase:** autenticação, dados, estados, fila e sincronização em tempo real.
5. **Adaptadores browser-first:** perfis persistentes separados para ChatGPT e Grok. O usuário faz login manualmente. O sistema não digita senha, não resolve CAPTCHA, não burla limites e para diante de desafios de segurança.

## Fluxo de aprovação
- Cada artefato/etapa começa em `pending_review`.
- Somente uma ação explícita `approve` grava aprovação com usuário, timestamp e versão.
- A etapa seguinte só pode ser enfileirada quando a etapa anterior estiver `approved`.
- `request_changes` devolve a etapa para `in_progress` e registra comentário.
- Mudanças em artefatos aprovados criam nova versão e invalidam dependências posteriores afetadas.

## Segurança
- RLS habilitado em todas as tabelas expostas.
- `service_role` somente no worker local, em arquivo `.env` ignorado pelo Git.
- O browser local mantém perfil persistente no computador do usuário; não sincronizar cookies para Supabase.
- Não expor worker diretamente à internet. Worker faz polling de saída para Supabase.
- Rede corporativa pode bloquear Supabase, autenticação ou WebSockets; testar sem contornar controles corporativos.

## Limitações browser-first
A assinatura do ChatGPT Go e do SuperGrok não garante que suas interfaces permitam automação estável, download automatizado ou uso ilimitado. A automação será implementada após teste manual com login feito pelo usuário. Mudanças de UI, limites, CAPTCHA e políticas podem exigir pausa e intervenção humana.

## Atualização v0.3 — Claude é o cérebro
- **Claude** (Claude Code local em modo não interativo, `claude -p`, com a assinatura do usuário) vê os quadros, identifica gancho, CTA e estratégia, escreve o roteiro e cria os prompts de imagem e de vídeo. Roda só com leitura de arquivos, na pasta do projeto.
- **Python** prepara as evidências, entrega os pedidos ao Claude, confere os formatos e envia os prompts aprovados: imagens ao **ChatGPT**, vídeos ao **Grok**.
- `BRAIN=chatgpt` em `worker/.env` troca o cérebro pelo ChatGPT do navegador, como reserva.

## Atualização v0.2 — Python conduz o navegador diretamente
- O worker Python controla um Chrome com perfil dedicado (conexão local em 127.0.0.1) e usa ChatGPT e Grok sem depender do Claude Code.
- O login continua manual (`python -m app.cli login`); cookies e senhas não saem do PC.
- São nove etapas, cada uma com aprovação; aprovar uma etapa de texto autoriza o lote de geração seguinte.
- Diante de login, verificação, limite ou mudança de interface, a etapa para com código e ação recomendada.
- Detalhes e passo a passo: `README.md`. A seção abaixo descreve a rota manual por lotes, hoje opcional.

## Claude Code por lotes (rota manual opcional)
- Navegador: Chrome existente do usuário, com extensão Claude in Chrome.
- Claude Code: não é daemon e não é chamado automaticamente pelo worker; recebe pacotes de trabalho agrupados e autorizados.
- Python: cria pacotes, executa tarefas determinísticas e valida/organiza resultados.
- A execução browser-first é iniciada pelo usuário em sessão Claude Code ativa (`claude --chrome`). O worker não manipula cookies, credenciais ou abas.
- Se Claude Code atingir cota, o lote fica pendente; Python/Supabase preservam estado e arquivos.
