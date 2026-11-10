-- TikTok Studio · bucket privado para prévias (imagens/vídeos) exibidas no painel.
-- O worker grava com a service role em <project_id>/<caminho>; o dono do projeto só lê.
insert into storage.buckets (id, name, public)
values ('artifacts', 'artifacts', false)
on conflict (id) do nothing;

drop policy if exists "artifacts bucket owner read" on storage.objects;
create policy "artifacts bucket owner read" on storage.objects for select to authenticated
 using (
  bucket_id = 'artifacts'
  and exists (
   select 1 from public.projects p
   where p.owner_id = auth.uid() and p.id::text = (storage.foldername(name))[1]
  )
 );
