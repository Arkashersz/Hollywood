> **Nota:** registro histórico da primeira entrega. O estado atual está no `README.md` (seção "Estado da validação").

# Status da primeira implementação

## Criado
- Monorepo e documentação arquitetural.
- Painel React responsivo com fluxo demonstrativo e trava visual de aprovação.
- Casca Tauri v2 para Windows.
- Migração SQL inicial do Supabase com tabelas, RLS, RPC de aprovação e RPC de fila.
- Worker Python com heartbeat e mecanismo de reivindicação de jobs.
- Adaptadores ChatGPT/Grok declarados, ainda sem automação real.
- Skill HOLYWOOD original preservada e plano de migração Flow → Grok.

## Não concluído / depende de ambiente externo
- Criar e configurar projeto Supabase.
- Aplicar migração no projeto real e testar RLS/RPCs.
- Login real e persistência do painel.
- Implementar os seletores/fluxos atuais do ChatGPT e Grok no navegador.
- Instalar e testar no Windows do usuário, configurar inicialização automática e empacotar instalador.
- Testar acesso pela rede corporativa.

## Segurança
Nenhuma senha, cookie, chave Supabase ou credencial foi solicitada ou embutida. O worker espera que a chave service_role seja adicionada localmente ao arquivo `worker/.env`, que está ignorado pelo Git.
