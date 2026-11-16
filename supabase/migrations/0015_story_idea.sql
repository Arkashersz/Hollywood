-- TikTok Studio · projeto criado a partir de uma IDEIA DE HISTÓRIA (sem link de vídeo de referência).
-- mode = 'story': o Claude escreve o roteiro do zero (gancho de 5 s, final com brecha para a parte 2), respeitando
-- a duração total e a quantidade mínima de cenas escolhidas aqui.
alter table public.projects add column if not exists story_idea text;
alter table public.projects add column if not exists target_seconds int check (target_seconds is null or target_seconds between 15 and 300);
alter table public.projects add column if not exists min_scenes int check (min_scenes is null or min_scenes between 1 and 60);

alter table public.projects drop constraint if exists projects_mode_check;
alter table public.projects add constraint projects_mode_check check (mode in ('inspired','clone','story'));

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
 values (p.id, 'analysis', format('Projeto criado a partir de uma ideia de história (%s s, no mínimo %s cenas); briefing enfileirado.', p_seconds, p_min_scenes));
 return p;
end; $$;
revoke all on function public.create_story_project(text,text,int,int,text) from public, anon;
grant execute on function public.create_story_project(text,text,int,int,text) to authenticated;
