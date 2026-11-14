-- TikTok Studio · abrir a pasta do projeto no PC do worker (o painel só pede; o worker abre o Explorer).
create or replace function public.open_project_folder(p_project_id uuid)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
 if not exists(select 1 from public.projects p where p.id=p_project_id and p.owner_id=auth.uid()) then
   raise exception 'Project not found or access denied';
 end if;
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p_project_id, null, 'open_folder', '{}'::jsonb) returning id into v_id;
 return v_id;
end; $$;
revoke all on function public.open_project_folder(uuid) from public, anon;
grant execute on function public.open_project_folder(uuid) to authenticated;
