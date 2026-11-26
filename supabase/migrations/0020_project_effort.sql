-- TikTok Studio · nível de esforço do Claude por projeto (low, medium, high, xhigh, max), junto com o modelo.
-- Vazio = o CLAUDE_EFFORT do worker/.env. Vale para todas as etapas de texto (análise, roteiro, personagens, prompts).
-- As funções de criação ganham o parâmetro p_effort no fim (opcional). Para não reescrever cada uma, a versão atual
-- é renomeada para *_base (sem acesso direto) e a nova chama a base e grava o esforço na mesma transação, antes de o
-- worker poder pegar a primeira tarefa.

alter table public.projects add column if not exists effort text;
alter table public.projects drop constraint if exists projects_effort_check;
alter table public.projects add constraint projects_effort_check check (effort is null or effort in ('low','medium','high','xhigh','max'));

create or replace function public.studio_effort(p_effort text)
returns text language plpgsql immutable set search_path=public as $$
declare v text := lower(nullif(btrim(p_effort), ''));
begin
 if v is not null and v not in ('low','medium','high','xhigh','max') then raise exception 'Nível de esforço inválido: %', p_effort; end if;
 return v;
end; $$;

alter function public.create_project(text,text,text,text) rename to create_project_base;
alter function public.create_project_from_plan(text,text,jsonb,text) rename to create_project_from_plan_base;
alter function public.create_story_project(text,text,int,int,text) rename to create_story_project_base;
alter function public.create_factory_project(text,jsonb,text) rename to create_factory_project_base;
alter function public.create_sequel(uuid,text,text,text,text) rename to create_sequel_base;
revoke all on function public.create_project_base(text,text,text,text) from public, anon, authenticated;
revoke all on function public.create_project_from_plan_base(text,text,jsonb,text) from public, anon, authenticated;
revoke all on function public.create_story_project_base(text,text,int,int,text) from public, anon, authenticated;
revoke all on function public.create_factory_project_base(text,jsonb,text) from public, anon, authenticated;
revoke all on function public.create_sequel_base(uuid,text,text,text,text) from public, anon, authenticated;

create or replace function public.create_project(p_title text, p_source text, p_mode text default 'inspired', p_model text default null, p_effort text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 p := public.create_project_base(p_title, p_source, p_mode, p_model);
 update public.projects set effort = v_effort where id = p.id returning * into p;
 return p;
end; $$;

create or replace function public.create_project_from_plan(p_title text, p_source text, p_plan jsonb, p_model text default null, p_effort text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 p := public.create_project_from_plan_base(p_title, p_source, p_plan, p_model);
 update public.projects set effort = v_effort where id = p.id returning * into p;
 return p;
end; $$;

create or replace function public.create_story_project(p_title text, p_idea text, p_seconds int, p_min_scenes int, p_model text default null, p_effort text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 p := public.create_story_project_base(p_title, p_idea, p_seconds, p_min_scenes, p_model);
 update public.projects set effort = v_effort where id = p.id returning * into p;
 return p;
end; $$;

create or replace function public.create_factory_project(p_title text, p_profile jsonb, p_model text default null, p_effort text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 p := public.create_factory_project_base(p_title, p_profile, p_model);
 update public.projects set effort = v_effort where id = p.id returning * into p;
 return p;
end; $$;

-- A continuação herda o esforço da parte anterior, a menos que outro seja escolhido.
create or replace function public.create_sequel(p_parent uuid, p_source text default null, p_title text default null, p_notes text default null, p_model text default null, p_effort text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 p := public.create_sequel_base(p_parent, p_source, p_title, p_notes, p_model);
 update public.projects set effort = coalesce(v_effort, (select effort from public.projects where id = p_parent)) where id = p.id returning * into p;
 return p;
end; $$;

revoke all on function public.create_project(text,text,text,text,text) from public, anon;
grant execute on function public.create_project(text,text,text,text,text) to authenticated;
revoke all on function public.create_project_from_plan(text,text,jsonb,text,text) from public, anon;
grant execute on function public.create_project_from_plan(text,text,jsonb,text,text) to authenticated;
revoke all on function public.create_story_project(text,text,int,int,text,text) from public, anon;
grant execute on function public.create_story_project(text,text,int,int,text,text) to authenticated;
revoke all on function public.create_factory_project(text,jsonb,text,text) from public, anon;
grant execute on function public.create_factory_project(text,jsonb,text,text) to authenticated;
revoke all on function public.create_sequel(uuid,text,text,text,text,text) from public, anon;
grant execute on function public.create_sequel(uuid,text,text,text,text,text) to authenticated;

-- Trocar o esforço de um projeto já criado (vale a partir da próxima tarefa).
create or replace function public.set_project_effort(p_project_id uuid, p_effort text)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; v_effort text := public.studio_effort(p_effort);
begin
 update public.projects set effort = v_effort where id = p_project_id and owner_id = auth.uid() returning * into p;
 if p.id is null then raise exception 'Project not found or access denied'; end if;
 return p;
end; $$;
revoke all on function public.set_project_effort(uuid,text) from public, anon;
grant execute on function public.set_project_effort(uuid,text) to authenticated;
