-- TikTok Studio · modelo do Claude por projeto (escolhido ao criar; vazio = padrão do PC do worker).
alter table public.projects add column if not exists model text check (model is null or model ~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$');

drop function if exists public.create_project(text, text, text);
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
  (p.id, 'character_images', 4, 'Imagens dos personagens', 'locked'),
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


drop function if exists public.create_project_from_plan(text, text, jsonb);
create or replace function public.create_project_from_plan(p_title text, p_source text, p_plan jsonb, p_model text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare
 v_model text := nullif(btrim(p_model), ''); p public.projects; v_owner uuid; v_first text; v_total int := 0; v_count int; k text; it jsonb;
 v_keys constant text[] := array['character_images','character_sheets','scene_images','production'];
 v_stage record; v_job_stage uuid; v_status public.stage_status;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
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

 insert into public.projects(owner_id, title, source_url, status, current_stage, import_plan, model)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Projeto com prompts prontos'),
         coalesce(nullif(btrim(p_source), ''), 'Prompts prontos (sem vídeo de referência)'), 'active', v_first, p_plan, v_model)
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
revoke all on function public.create_project_from_plan(text,text,jsonb,text) from public, anon;
grant execute on function public.create_project_from_plan(text,text,jsonb,text) to authenticated;


-- A continuação herda o modelo da parte anterior, a menos que outro seja escolhido.
drop function if exists public.create_sequel(uuid, text, text, text);
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
  (p.id, 'character_images', 4, 'Imagens dos personagens', 'locked'),
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


-- Trocar o modelo de um projeto já criado (vale a partir da próxima tarefa).
create or replace function public.set_project_model(p_project_id uuid, p_model text)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_model text := nullif(btrim(p_model), '');
begin
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
 update public.projects set model=v_model where id=p_project_id and owner_id=auth.uid() returning * into p;
 if p.id is null then raise exception 'Project not found or access denied'; end if;
 return p;
end; $$;
revoke all on function public.set_project_model(uuid,text) from public, anon;
grant execute on function public.set_project_model(uuid,text) to authenticated;
