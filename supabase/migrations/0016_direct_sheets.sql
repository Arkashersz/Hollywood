-- TikTok Studio · sem a etapa "Imagens dos personagens" (corpo inteiro).
-- Projetos novos (link, cópia fiel, ideia de história e continuação) vão direto da etapa de personagens (texto) para as
-- folhas 16:9, que o ChatGPT gera a partir da descrição fixa. Assim cada personagem consome 1 imagem em vez de 2.
-- Projetos antigos mantêm as 9 etapas; "Já tenho os prompts" (create_project_from_plan) também não muda.
-- O número de ordem das etapas fica com um "buraco" no 4, o que o fluxo de aprovação já aceita (usa stage_order > atual).


-- create_project
create or replace function public.create_project(p_title text, p_source text, p_mode text default 'inspired', p_model text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), ''); v_owner uuid; v_mode text := coalesce(nullif(btrim(p_mode), ''), 'inspired'); v_model text := nullif(btrim(p_model), '');
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
 if v_mode not in ('inspired','clone') then raise exception 'Modo inválido: %', v_mode; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if v_source is null then raise exception 'Informe o link do TikTok ou o caminho do MP4'; end if;
 insert into public.projects(owner_id, title, source_url, status, current_stage, mode, model)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Novo projeto'), v_source, 'active', 'analysis', v_mode, v_model)
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'analysis',         1, 'Análise da referência', 'queued'),
  (p.id, 'script',           2, case when v_mode='clone' then 'Roteiro fiel ao original' else 'Roteiro em alemão' end, 'locked'),
  (p.id, 'character_bible',  3, 'Personagens e vozes (texto)', 'locked'),
  (p.id, 'character_sheets', 5, 'Folhas dos personagens 16:9', 'locked'),
  (p.id, 'visual_plan',      6, 'Ambientes e cenas (texto)', 'locked'),
  (p.id, 'scene_images',     7, 'Imagens de ambientes e cenas', 'locked'),
  (p.id, 'video_prompts',    8, 'Prompts de vídeo, edição e publicação', 'locked'),
  (p.id, 'production',       9, 'Vídeos no Grok', 'locked');

 select id into s_id from public.project_stages where project_id=p.id and stage_key='analysis';
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p.id, s_id, 'run_stage', jsonb_build_object('stage_key', 'analysis'));
 insert into public.events(project_id, stage_key, message)
 values (p.id, 'analysis', case when v_mode='clone' then 'Projeto criado (cópia fiel); análise enfileirada.' else 'Projeto criado; análise enfileirada.' end);
 return p;
end; $$;
revoke all on function public.create_project(text,text,text,text) from public, anon;
grant execute on function public.create_project(text,text,text,text) to authenticated;

-- create_sequel
create or replace function public.create_sequel(p_parent uuid, p_source text default null, p_title text default null, p_notes text default null, p_model text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare par public.projects; p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), ''); v_notes text := nullif(btrim(p_notes), ''); v_owner uuid; v_part int; v_model text := nullif(btrim(p_model), '');
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
 select user_id into v_owner from public.studio_owner;
 if v_owner is null or v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta.'; end if;
 select * into par from public.projects where id = p_parent and owner_id = auth.uid();
 if par.id is null then raise exception 'Projeto de origem não encontrado'; end if;
 if par.status <> 'completed' then raise exception 'A parte 2 só pode ser criada depois que o projeto estiver concluído.'; end if;
 if v_notes is not null and length(v_notes) > 4000 then raise exception 'As ideias para a continuação passam de 4000 caracteres'; end if;
 v_part := par.part + 1;
 insert into public.projects(owner_id, title, source_url, status, current_stage, mode, parent_id, part, sequel_notes, model)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), regexp_replace(par.title, '\s*·?\s*Parte \d+$', '') || ' · Parte ' || v_part),
         v_source, 'active', 'analysis', case when v_source is null then 'inspired' else par.mode end, par.id, v_part, v_notes, coalesce(v_model, par.model))
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'analysis',         1, case when v_source is null then 'Resumo da parte anterior' else 'Análise da parte 2 da referência' end, 'queued'),
  (p.id, 'script',           2, case when v_source is not null and par.mode='clone' then 'Roteiro da continuação (fiel)' else 'Roteiro da continuação' end, 'locked'),
  (p.id, 'character_bible',  3, 'Personagens e vozes (texto)', 'locked'),
  (p.id, 'character_sheets', 5, 'Folhas dos personagens 16:9', 'locked'),
  (p.id, 'visual_plan',      6, 'Ambientes e cenas (texto)', 'locked'),
  (p.id, 'scene_images',     7, 'Imagens de ambientes e cenas', 'locked'),
  (p.id, 'video_prompts',    8, 'Prompts de vídeo, edição e publicação', 'locked'),
  (p.id, 'production',       9, 'Vídeos no Grok', 'locked');

 select id into s_id from public.project_stages where project_id=p.id and stage_key='analysis';
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p.id, s_id, 'run_stage', jsonb_build_object('stage_key', 'analysis'));
 insert into public.events(project_id, stage_key, message)
 values (p.id, 'analysis', 'Parte ' || v_part || ' criada a partir de “' || par.title || '”; ' || case when v_source is null then 'resumo da parte anterior enfileirado.' else 'análise da parte 2 enfileirada.' end);
 return p;
end; $$;
revoke all on function public.create_sequel(uuid,text,text,text,text) from public, anon;
grant execute on function public.create_sequel(uuid,text,text,text,text) to authenticated;

-- create_story_project
create or replace function public.create_story_project(p_title text, p_idea text, p_seconds int, p_min_scenes int, p_model text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_idea text := nullif(btrim(p_idea), ''); v_owner uuid; v_model text := nullif(btrim(p_model), '');
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if v_idea is null then raise exception 'Conte a ideia da história'; end if;
 if length(v_idea) > 6000 then raise exception 'A ideia passa de 6000 caracteres'; end if;
 if p_seconds is null or p_seconds not between 15 and 300 then raise exception 'A duração precisa ficar entre 15 e 300 segundos'; end if;
 if p_min_scenes is null or p_min_scenes not between 1 and 60 then raise exception 'A quantidade mínima de cenas precisa ficar entre 1 e 60'; end if;
 if p_min_scenes * 6 > p_seconds + 10 then raise exception 'Com % cenas de no mínimo 6 s cada, o vídeo passa de % s. Aumente a duração ou reduza as cenas.', p_min_scenes, p_seconds; end if;

 insert into public.projects(owner_id, title, source_url, status, current_stage, mode, model, story_idea, target_seconds, min_scenes)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Nova história'), null, 'active', 'analysis', 'story', v_model, v_idea, p_seconds, p_min_scenes)
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'analysis',         1, 'Briefing da história', 'queued'),
  (p.id, 'script',           2, 'Roteiro em alemão', 'locked'),
  (p.id, 'character_bible',  3, 'Personagens e vozes (texto)', 'locked'),
  (p.id, 'character_sheets', 5, 'Folhas dos personagens 16:9', 'locked'),
  (p.id, 'visual_plan',      6, 'Ambientes e cenas (texto)', 'locked'),
  (p.id, 'scene_images',     7, 'Imagens de ambientes e cenas', 'locked'),
  (p.id, 'video_prompts',    8, 'Prompts de vídeo, edição e publicação', 'locked'),
  (p.id, 'production',       9, 'Vídeos no Grok', 'locked');

 select id into s_id from public.project_stages where project_id=p.id and stage_key='analysis';
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p.id, s_id, 'run_stage', jsonb_build_object('stage_key', 'analysis'));
 insert into public.events(project_id, stage_key, message)
 values (p.id, 'analysis', format('Projeto criado a partir de uma ideia de história (%s s, no mínimo %s cenas); briefing enfileirado.', p_seconds, p_min_scenes));
 return p;
end; $$;
revoke all on function public.create_story_project(text,text,int,int,text) from public, anon;
grant execute on function public.create_story_project(text,text,int,int,text) to authenticated;
