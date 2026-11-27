-- TikTok Studio · encurtar ou mudar a história na etapa de prompts de vídeo, sem refazer imagens.
-- O pedido fica em payload.story_cut: o worker escolhe, entre as cenas que já têm imagem aprovada, quais entram.
-- p_restore volta à história completa.
create or replace function public.adjust_story(p_stage_id uuid, p_instruction text default null, p_target_s int default null, p_restore boolean default false)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; p public.projects; v_text text := nullif(btrim(p_instruction), ''); v_cut jsonb;
begin
 select st.* into s from public.project_stages st join public.projects pr on pr.id=st.project_id
  where st.id=p_stage_id and pr.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 select * into p from public.projects where id=s.project_id;
 if s.stage_key <> 'video_prompts' or p.mode='factory' or p.import_plan is not null then
   raise exception 'A história só pode ser ajustada na etapa de prompts de vídeo';
 end if;
 if s.status not in ('pending_review','failed') then
   raise exception 'Esta etapa não está pronta para ajustar a história (se já foi aprovada, volte para ela primeiro)';
 end if;
 if exists(select 1 from public.jobs j where j.stage_id=s.id and j.status in ('queued','running')) then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;

 if coalesce(p_restore, false) then
   if s.payload->'story_cut' is null or coalesce((s.payload->'story_cut'->>'restore')::boolean, false) then
     raise exception 'A história já está completa';
   end if;
   v_cut := jsonb_build_object('restore', true, 'request', gen_random_uuid());
 else
   if v_text is null and p_target_s is null then raise exception 'Diga a duração desejada ou o que mudar na história'; end if;
   if p_target_s is not null and p_target_s not between 30 and 1200 then
     raise exception 'A duração precisa ficar entre 30 segundos e 20 minutos';
   end if;
   if length(v_text) > 4000 then raise exception 'Pedido grande demais (máximo de 4000 caracteres)'; end if;
   v_cut := jsonb_strip_nulls(jsonb_build_object('instruction', v_text, 'target_s', p_target_s, 'request', gen_random_uuid()));
 end if;

 update public.project_stages
    set status='queued', current_version=current_version+1,
        payload=(payload - 'error' - 'progress' - 'review_comment' - 'warnings') || jsonb_build_object('story_cut', v_cut)
  where id=s.id returning * into s;
 -- Prompts editados à mão nos vídeos valiam para a numeração antiga dos clipes.
 update public.project_stages set payload = payload - 'prompt_overrides' where project_id=s.project_id and stage_key='production';
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key));
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, case when coalesce(p_restore, false) then 'Voltando à história completa.' else 'Ajuste da história pedido.' end, v_cut);
 return s;
end; $$;
revoke all on function public.adjust_story(uuid,text,int,boolean) from public, anon;
grant execute on function public.adjust_story(uuid,text,int,boolean) to authenticated;
