-- TikTok Studio · "é o dono do estúdio?" como função.
-- studio_owner não tem política de leitura (ninguém a lê pelo painel), então uma política que a consulta diretamente
-- nunca enxerga o dono. A função roda com os direitos do criador e só devolve sim/não para quem a chama.
create or replace function public.is_studio_owner() returns boolean
language sql stable security definer set search_path = public as $$
 select exists (select 1 from public.studio_owner o where o.user_id = auth.uid())
$$;
revoke all on function public.is_studio_owner() from public, anon;
grant execute on function public.is_studio_owner() to authenticated;

drop policy if exists "media owner read" on public.worker_media;
create policy "media owner read" on public.worker_media for select to authenticated using (public.is_studio_owner());
