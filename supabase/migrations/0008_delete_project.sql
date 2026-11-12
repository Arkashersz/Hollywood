-- TikTok Studio · excluir projeto (e tudo dele: etapas, tarefas, eventos e artefatos).
-- Os arquivos de prévia no Storage são removidos pelo painel pela API do Storage (o Supabase proíbe DELETE direto em storage.objects).
create or replace function public.delete_project(p_project_id uuid)
returns void language plpgsql security definer set search_path=public as $$
declare v_owner uuid;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 select owner_id into v_owner from public.projects where id = p_project_id;
 if v_owner is null or v_owner <> auth.uid() then raise exception 'Projeto não encontrado'; end if;
 if exists (select 1 from public.jobs where project_id = p_project_id and status = 'running') then
  raise exception 'O worker está trabalhando neste projeto agora. Espere terminar (ou parar o worker) e tente de novo.';
 end if;
 delete from public.projects where id = p_project_id;   -- o resto some em cascata
end; $$;
revoke all on function public.delete_project(uuid) from public, anon;
grant execute on function public.delete_project(uuid) to authenticated;

-- O dono do Studio pode apagar arquivos do bucket (usado só ao excluir um projeto).
drop policy if exists "artifacts bucket owner delete" on storage.objects;
create policy "artifacts bucket owner delete" on storage.objects for delete to authenticated
 using (bucket_id = 'artifacts' and exists (select 1 from public.studio_owner o where o.user_id = auth.uid()));
