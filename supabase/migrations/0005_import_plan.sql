-- TikTok Studio · começar de uma etapa avançada.
-- O dono já tem os prompts de imagem e/ou de vídeo: o projeto nasce com as etapas de texto
-- (análise, roteiro, personagens, ambientes, prompts de vídeo) marcadas como puladas e a fila
-- começa na primeira etapa de geração que tiver itens. Cada etapa de geração continua parando
-- para aprovação no painel.
alter table public.projects add column if not exists import_plan jsonb;

-- Aprovar uma etapa libera a PRÓXIMA etapa bloqueada (as puladas já estão aprovadas).
create or replace function public.decide_stage(p_stage_id uuid, p_decision text, p_comment text default null)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; nxt public.project_stages; v_comment text := nullif(btrim(p_comment), '');
begin
 if p_decision not in ('approve','request_changes') then raise exception 'Invalid decision'; end if;
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.status <> 'pending_review' then raise exception 'Stage is not awaiting review'; end if;
 if p_decision='request_changes' and v_comment is null then raise exception 'Descreva o ajuste desejado'; end if;

 insert into public.approvals(project_id, stage_id, user_id, decision, version, comment)
 values (s.project_id, s.id, auth.uid(), p_decision, s.current_version, v_comment);

 if p_decision='approve' then
   update public.project_stages set status='approved', approved_at=now() where id=s.id returning * into s;
   select * into nxt from public.project_stages
    where project_id=s.project_id and stage_order>s.stage_order and status='locked'
    order by stage_order limit 1 for update;
   if found then
     update public.project_stages set status='queued' where id=nxt.id;
     insert into public.jobs(project_id, stage_id, job_type, input)
     values (s.project_id, nxt.id, 'run_stage', jsonb_build_object('stage_key', nxt.stage_key));
     update public.projects set current_stage=nxt.stage_key where id=s.project_id;
     insert into public.events(project_id, stage_key, message)
     values (s.project_id, s.stage_key, format('Etapa "%s" aprovada; "%s" enfileirada.', s.title, nxt.title));
   else
     update public.projects set status='completed' where id=s.project_id
      and not exists(select 1 from public.project_stages x where x.project_id=s.project_id and x.status<>'approved');
     insert into public.events(project_id, stage_key, message)
     values (s.project_id, s.stage_key, format('Etapa "%s" aprovada.', s.title));
   end if;
 else
   update public.project_stages
    set status='changes_requested', current_version=current_version+1,
        payload=payload||jsonb_build_object('review_comment', v_comment)
    where id=s.id returning * into s;
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key, 'feedback', v_comment));
   insert into public.events(project_id, stage_key, message, details)
   values (s.project_id, s.stage_key, format('Ajustes pedidos em "%s".', s.title), jsonb_build_object('comment', v_comment));
 end if;
 return s;
end; $$;
revoke all on function public.decide_stage(uuid,text,text) from public, anon;
grant execute on function public.decide_stage(uuid,text,text) to authenticated;

-- p_plan = {"character_images":[{"name","prompt","refs":[nomes]}], "character_sheets":[…],
--           "scene_images":[…], "production":[{"name","prompt","start":"nome da imagem"|"anterior"}]}
create or replace function public.create_project_from_plan(p_title text, p_source text, p_plan jsonb)
returns public.projects language plpgsql security definer set search_path=public as $$
declare
 p public.projects; v_owner uuid; v_first text; v_total int := 0; v_count int; k text; it jsonb;
 v_keys constant text[] := array['character_images','character_sheets','scene_images','production'];
 v_stage record; v_job_stage uuid; v_status public.stage_status;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if p_plan is null or jsonb_typeof(p_plan) <> 'object' then raise exception 'Plano inválido'; end if;

 foreach k in array v_keys loop
   if p_plan ? k then
     if jsonb_typeof(p_plan->k) <> 'array' then raise exception 'Lista inválida em "%"', k; end if;
     v_count := jsonb_array_length(p_plan->k);
     if v_count > 200 then raise exception 'Itens demais em "%" (máximo 200)', k; end if;
     for it in select * from jsonb_array_elements(p_plan->k) loop
       if jsonb_typeof(it) <> 'object' or nullif(btrim(it->>'name'), '') is null or nullif(btrim(it->>'prompt'), '') is null then
         raise exception 'Todo item precisa de nome e prompt (lista "%")', k;
       end if;
       if length(it->>'prompt') > 20000 then raise exception 'Prompt grande demais: %', left(it->>'name', 60); end if;
     end loop;
     v_total := v_total + v_count;
     if v_count > 0 and v_first is null then v_first := k; end if;
   end if;
 end loop;
 if v_total = 0 then raise exception 'Envie pelo menos um prompt de imagem ou de vídeo'; end if;

 insert into public.projects(owner_id, title, source_url, status, current_stage, import_plan)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Projeto com prompts prontos'),
         coalesce(nullif(btrim(p_source), ''), 'Prompts prontos (sem vídeo de referência)'), 'active', v_first, p_plan)
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'analysis',         1, 'Análise da referência', 'locked'),
  (p.id, 'script',           2, 'Roteiro em alemão', 'locked'),
  (p.id, 'character_bible',  3, 'Personagens e vozes (texto)', 'locked'),
  (p.id, 'character_images', 4, 'Imagens dos personagens', 'locked'),
  (p.id, 'character_sheets', 5, 'Folhas dos personagens 16:9', 'locked'),
  (p.id, 'visual_plan',      6, 'Ambientes e cenas (texto)', 'locked'),
  (p.id, 'scene_images',     7, 'Imagens de ambientes e cenas', 'locked'),
  (p.id, 'video_prompts',    8, 'Prompts de vídeo, edição e publicação', 'locked'),
  (p.id, 'production',       9, 'Vídeos no Grok', 'locked');

 -- Etapas de texto: puladas (você trouxe os prompts). Etapas de geração sem itens: puladas também.
 update public.project_stages set status='approved', approved_at=now(),
   payload=jsonb_build_object('skipped', true, 'summary', 'Etapa pulada: você trouxe os prompts prontos.')
  where project_id=p.id and stage_key in ('analysis','script','character_bible','visual_plan','video_prompts');
 update public.project_stages set status='approved', approved_at=now(),
   payload=jsonb_build_object('skipped', true, 'summary', 'Etapa pulada: nenhum prompt enviado para ela.')
  where project_id=p.id and stage_key = any(v_keys)
    and coalesce(jsonb_array_length(case when jsonb_typeof(p_plan->stage_key)='array' then p_plan->stage_key end), 0) = 0;

 select id into v_job_stage from public.project_stages where project_id=p.id and stage_key=v_first;
 update public.project_stages set status='queued' where id=v_job_stage;
 update public.project_stages set payload=jsonb_build_object('imported', true,
   'summary', format('%s prompt(s) importado(s) para esta etapa.', jsonb_array_length(p_plan->v_first)))
  where id=v_job_stage;
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p.id, v_job_stage, 'run_stage', jsonb_build_object('stage_key', v_first));
 insert into public.events(project_id, stage_key, message)
 values (p.id, v_first, format('Projeto criado a partir de prompts prontos (%s item(ns)); começa em "%s".', v_total, v_first));
 return p;
end; $$;
revoke all on function public.create_project_from_plan(text,text,jsonb) from public, anon;
grant execute on function public.create_project_from_plan(text,text,jsonb) to authenticated;
