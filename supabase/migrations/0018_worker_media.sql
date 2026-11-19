-- TikTok Studio · acesso remoto aos vídeos.
-- Os vídeos continuam só no PC (nunca vão ao Supabase). O worker abre um servidor local de vídeos e, fora de casa,
-- um túnel HTTPS até ele; aqui ele publica o endereço e a senha de acesso. Só o dono do estúdio lê esta tabela
-- (a senha do servidor de vídeos não pode ficar visível a outras contas). Sem política de escrita: só o worker escreve.
create table if not exists public.worker_media (
 worker_id text primary key,
 local_url text,
 public_url text,
 token text not null,
 updated_at timestamptz not null default now()
);
alter table public.worker_media enable row level security;
drop policy if exists "media owner read" on public.worker_media;
create policy "media owner read" on public.worker_media for select to authenticated
 using (exists (select 1 from public.studio_owner o where o.user_id = auth.uid()));

do $$
begin
 alter publication supabase_realtime add table public.worker_media;
exception when others then null;
end $$;
