-- TikTok Studio · modo de produção.
-- 'inspired' = roteiro novo inspirado na referência (como era até aqui).
-- 'clone'    = cópia fiel: mesma estrutura, mesmos cortes, mesma câmera; só mudam personagem/elemento e, se você aprovar, alguns ambientes.
alter table public.projects add column if not exists mode text not null default 'inspired' check (mode in ('inspired','clone'));

drop function if exists public.create_project(text, text);
create or replace function public.create_project(p_title text, p_source text, p_mode text default 'inspired')
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), ''); v_owner uuid; v_mode text := coalesce(nullif(btrim(p_mode), ''), 'inspired');
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 if v_mode not in ('inspired','clone') then raise exception 'Modo inválido: %', v_mode; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if v_source is null then raise exception 'Informe o link do TikTok ou o caminho do MP4'; end if;
 insert into public.projects(owner_id, title, source_url, status, current_stage, mode)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Novo projeto'), v_source, 'active', 'analysis', v_mode)
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
revoke all on function public.create_project(text,text,text) from public, anon;
grant execute on function public.create_project(text,text,text) to authenticated;
