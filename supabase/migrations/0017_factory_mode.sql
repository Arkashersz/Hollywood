-- TikTok Studio · Fábrica de roteiros + workflow de animação (mode = 'factory'), tudo em 9:16.
-- Fluxo: referências → cinco propostas (você escolhe) → roteiro → personagens e ambientes (fim da Fábrica)
--        → workflow de animação (cenas) → folhas 9:16 → imagens de ambiente e cena 9:16 → vídeos 9:16 no Grok.
-- profile guarda o perfil de produção (Fase 0): nicho, idioma, público, estilo, duração, cenas, áudio, restrições e referências.
alter table public.projects add column if not exists profile jsonb;

alter table public.projects drop constraint if exists projects_mode_check;
alter table public.projects add constraint projects_mode_check check (mode in ('inspired','clone','story','factory'));

-- Tudo é 9:16: títulos de etapas antigas e novas que citem 16:9 passam a dizer 9:16 (as imagens já saem em 9:16).
create or replace function public.stage_title_ratio() returns trigger language plpgsql as $$
begin
 new.title := replace(new.title, '16:9', '9:16');
 return new;
end; $$;
drop trigger if exists project_stages_title_ratio on public.project_stages;
create trigger project_stages_title_ratio before insert or update of title on public.project_stages
 for each row execute function public.stage_title_ratio();
update public.project_stages set title = replace(title, '16:9', '9:16') where title like '%16:9%';

create or replace function public.create_factory_project(p_title text, p_profile jsonb, p_model text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_owner uuid; v_model text := nullif(btrim(p_model), ''); v_profile jsonb := coalesce(p_profile, '{}'::jsonb);
 v_min int; v_max int; v_scenes int; v_smin int; v_smax int;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_model is not null and v_model !~ '^[A-Za-z0-9._-]{1,80}(\[[A-Za-z0-9]{1,8}\])?$' then raise exception 'Modelo inválido'; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if jsonb_typeof(v_profile) <> 'object' then raise exception 'Perfil de produção inválido'; end if;
 if length(v_profile::text) > 16000 then raise exception 'O perfil de produção é grande demais'; end if;
 if v_profile ? 'references' and (jsonb_typeof(v_profile->'references') <> 'array' or jsonb_array_length(v_profile->'references') > 5) then
   raise exception 'Informe no máximo 5 referências'; end if;
 if coalesce(v_profile->>'audio', 'dialogos') not in ('dialogos','narracao','ambos') then raise exception 'Tipo de áudio inválido'; end if;

 v_min    := case when (v_profile->>'min_s')       ~ '^\d{1,4}$' then (v_profile->>'min_s')::int       else 90 end;
 v_max    := case when (v_profile->>'max_s')       ~ '^\d{1,4}$' then (v_profile->>'max_s')::int       else 120 end;
 v_scenes := case when (v_profile->>'min_scenes')  ~ '^\d{1,3}$' then (v_profile->>'min_scenes')::int  else 12 end;
 v_smin   := case when (v_profile->>'scene_min_s') ~ '^\d{1,3}$' then (v_profile->>'scene_min_s')::int else 6 end;
 v_smax   := case when (v_profile->>'scene_max_s') ~ '^\d{1,3}$' then (v_profile->>'scene_max_s')::int else 10 end;
 if v_min not between 15 and 240 then raise exception 'A duração mínima precisa ficar entre 15 e 240 segundos'; end if;
 if v_max < v_min or v_max > 300 then raise exception 'A duração máxima precisa ficar entre a mínima e 300 segundos'; end if;
 if v_scenes not between 1 and 60 then raise exception 'A quantidade mínima de cenas precisa ficar entre 1 e 60'; end if;
 if v_smin not between 1 and 10 or v_smax not between v_smin and 10 then raise exception 'A duração por cena precisa ficar entre 1 e 10 segundos (limite de 10 s por clipe)'; end if;
 if v_scenes * v_smin > v_max then raise exception '% cenas de no mínimo % s somam % s, mais do que o máximo de % s. Reduza as cenas ou aumente a duração.', v_scenes, v_smin, v_scenes * v_smin, v_max; end if;

 insert into public.projects(owner_id, title, source_url, status, current_stage, mode, model, profile)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Nova história'), null, 'active', 'f_refs', 'factory', v_model, v_profile)
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'f_refs',           1, 'Referências e aprendizados', 'queued'),
  (p.id, 'f_proposals',      2, 'Cinco propostas originais', 'locked'),
  (p.id, 'f_script',         3, 'Roteiro completo', 'locked'),
  (p.id, 'f_bible',          4, 'Personagens e ambientes (fim da Fábrica)', 'locked'),
  (p.id, 'f_workflow',       5, 'Workflow de animação · cenas', 'locked'),
  (p.id, 'character_sheets', 6, 'Folhas dos personagens 9:16', 'locked'),
  (p.id, 'scene_images',     7, 'Imagens de ambientes e cenas 9:16', 'locked'),
  (p.id, 'production',       8, 'Vídeos no Grok 9:16', 'locked');

 select id into s_id from public.project_stages where project_id=p.id and stage_key='f_refs';
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (p.id, s_id, 'run_stage', jsonb_build_object('stage_key', 'f_refs'));
 insert into public.events(project_id, stage_key, message)
 values (p.id, 'f_refs', 'Projeto criado na Fábrica de roteiros; análise das referências enfileirada.');
 return p;
end; $$;
revoke all on function public.create_factory_project(text,jsonb,text) from public, anon;
grant execute on function public.create_factory_project(text,jsonb,text) to authenticated;

-- Escolha da história a desenvolver (entre as cinco propostas). Vale enquanto a etapa estiver em revisão; sem escolha, o roteiro segue a recomendada.
create or replace function public.choose_proposal(p_stage_id uuid, p_number int)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.stage_key <> 'f_proposals' then raise exception 'Esta etapa não tem propostas para escolher'; end if;
 if s.status <> 'pending_review' then raise exception 'Escolha a história enquanto a etapa estiver em revisão'; end if;
 if p_number is null or p_number not between 1 and 5 then raise exception 'Escolha uma história de 1 a 5'; end if;
 update public.project_stages set payload = payload || jsonb_build_object('chosen', p_number) where id=s.id returning * into s;
 insert into public.events(project_id, stage_key, message)
 values (s.project_id, s.stage_key, format('História %s escolhida para virar roteiro.', p_number));
 return s;
end; $$;
revoke all on function public.choose_proposal(uuid,int) from public, anon;
grant execute on function public.choose_proposal(uuid,int) to authenticated;
