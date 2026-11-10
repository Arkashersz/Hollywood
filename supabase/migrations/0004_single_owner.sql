-- TikTok Studio · um único dono.
-- O painel fica público na internet (Vercel) e o worker gasta a cota das assinaturas do dono.
-- Por isso só quem criou o primeiro projeto pode criar projetos; qualquer outra conta criada
-- no painel consegue entrar, mas não cria nada nem aciona o worker.
create table if not exists public.studio_owner (
 singleton boolean primary key default true check (singleton),
 user_id uuid not null references auth.users(id) on delete cascade
);
alter table public.studio_owner enable row level security;  -- sem políticas: ninguém lê pelo painel

create or replace function public.create_project(p_title text, p_source text)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), ''); v_owner uuid;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 insert into public.studio_owner(user_id) values (auth.uid()) on conflict (singleton) do nothing;
 select user_id into v_owner from public.studio_owner;
 if v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta; novos projetos não são permitidos aqui.'; end if;
 if v_source is null then raise exception 'Informe o link do TikTok ou o caminho do MP4'; end if;
 insert into public.projects(owner_id, title, source_url, status, current_stage)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), 'Novo projeto'), v_source, 'active', 'analysis')
 returning * into p;

 insert into public.project_stages(project_id, stage_key, stage_order, title, status) values
  (p.id, 'analysis',         1, 'Análise da referência', 'queued'),
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
 insert into public.events(project_id, stage_key, message) values (p.id, 'analysis', 'Projeto criado; análise enfileirada.');
 return p;
end; $$;
revoke all on function public.create_project(text,text) from public, anon;
grant execute on function public.create_project(text,text) to authenticated;

-- Projetos só nascem por create_project: sem inserção direta (que pularia a trava acima).
drop policy if exists "projects owner access" on public.projects;
drop policy if exists "projects owner read" on public.projects;
create policy "projects owner read" on public.projects for select using (auth.uid() = owner_id);
