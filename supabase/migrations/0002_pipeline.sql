-- TikTok Studio · pipeline real: 9 etapas, fila automática, eventos e regeneração por item.
-- Aplicar depois de 0001_initial.sql.

-- ---------------------------------------------------------------------------
-- Artefatos: chave estável por item, para o worker poder regravar a mesma linha.
-- ---------------------------------------------------------------------------
alter table public.artifacts add column if not exists item_key text;
alter table public.artifacts add column if not exists updated_at timestamptz not null default now();
create unique index if not exists artifacts_item_key_idx on public.artifacts(project_id, item_key);
create index if not exists artifacts_stage_idx on public.artifacts(stage_id, name);

-- ---------------------------------------------------------------------------
-- Eventos: registro de atividade exibido no painel.
-- ---------------------------------------------------------------------------
create table if not exists public.events (
 id bigint generated always as identity primary key,
 project_id uuid not null references public.projects(id) on delete cascade,
 stage_key text,
 level text not null default 'info' check (level in ('info','warn','error')),
 message text not null,
 details jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);
create index if not exists events_project_idx on public.events(project_id, created_at desc);
alter table public.events enable row level security;
drop policy if exists "events owner read" on public.events;
create policy "events owner read" on public.events for select
 using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));

create index if not exists jobs_stage_idx on public.jobs(stage_id, created_at desc);

-- ---------------------------------------------------------------------------
-- O painel não escreve direto em etapas/artefatos: tudo passa pelas funções abaixo
-- (o worker usa a service role, que ignora RLS).
-- ---------------------------------------------------------------------------
drop policy if exists "stages owner access" on public.project_stages;
drop policy if exists "stages owner read" on public.project_stages;
create policy "stages owner read" on public.project_stages for select
 using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));

drop policy if exists "artifacts owner access" on public.artifacts;
drop policy if exists "artifacts owner read" on public.artifacts;
create policy "artifacts owner read" on public.artifacts for select
 using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));

drop policy if exists "approvals owner insert" on public.approvals;

drop policy if exists "heartbeat owner read" on public.worker_heartbeats;
create policy "heartbeat owner read" on public.worker_heartbeats for select to authenticated using (true);

create or replace function public.touch_project() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end; $$;
drop trigger if exists projects_touch on public.projects;
create trigger projects_touch before update on public.projects
 for each row execute function public.touch_project();

-- ---------------------------------------------------------------------------
-- Criar projeto: grava o projeto, as 9 etapas e enfileira a primeira.
-- ---------------------------------------------------------------------------
create or replace function public.create_project(p_title text, p_source text)
returns public.projects language plpgsql security definer set search_path=public as $$
declare p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), '');
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
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

-- ---------------------------------------------------------------------------
-- Decisão de etapa: aprovar libera e enfileira a seguinte; pedir ajustes
-- cria nova versão e reenfileira a mesma etapa com o comentário.
-- ---------------------------------------------------------------------------
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
    where project_id=s.project_id and stage_order=s.stage_order+1 and status='locked' for update;
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

-- ---------------------------------------------------------------------------
-- Tentar novamente uma etapa que falhou ou ficou bloqueada (login, limite, interface).
-- ---------------------------------------------------------------------------
create or replace function public.retry_stage(p_stage_id uuid)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.status not in ('failed','queued','in_progress','changes_requested') then
   raise exception 'Esta etapa não pode ser reexecutada agora';
 end if;
 if exists(select 1 from public.jobs j where j.stage_id=s.id and j.status in ('queued','running')) then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;
 update public.project_stages set status='queued', payload=payload-'error' where id=s.id returning * into s;
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (s.project_id, s.id, 'run_stage',
         jsonb_strip_nulls(jsonb_build_object('stage_key', s.stage_key, 'feedback', s.payload->>'review_comment', 'resume', true)));
 insert into public.events(project_id, stage_key, message)
 values (s.project_id, s.stage_key, format('Nova tentativa de "%s" enfileirada.', s.title));
 return s;
end; $$;
revoke all on function public.retry_stage(uuid) from public, anon;
grant execute on function public.retry_stage(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- Regenerar um único item (imagem/vídeo) de uma etapa em revisão.
-- ---------------------------------------------------------------------------
create or replace function public.regenerate_item(p_artifact_id uuid, p_comment text default null)
returns public.jobs language plpgsql security definer set search_path=public as $$
declare a public.artifacts; s public.project_stages; j public.jobs;
begin
 select ar.* into a from public.artifacts ar join public.projects p on p.id=ar.project_id
  where ar.id=p_artifact_id and p.owner_id=auth.uid();
 if not found then raise exception 'Artifact not found or access denied'; end if;
 if a.artifact_type not in ('image','video') then raise exception 'Somente imagens e vídeos podem ser regenerados'; end if;
 select * into s from public.project_stages where id=a.stage_id for update;
 if not found or s.status <> 'pending_review' then raise exception 'A etapa precisa estar em revisão'; end if;
 if exists(select 1 from public.jobs x where x.stage_id=s.id and x.status in ('queued','running')) then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;
 update public.project_stages set status='queued' where id=s.id;
 insert into public.jobs(project_id, stage_id, job_type, input)
 values (a.project_id, s.id, 'regen_item',
         jsonb_strip_nulls(jsonb_build_object('stage_key', s.stage_key, 'item_key', a.item_key, 'comment', nullif(btrim(p_comment), ''))))
 returning * into j;
 insert into public.events(project_id, stage_key, message, details)
 values (a.project_id, s.stage_key, format('Regeneração pedida: %s.', a.name), jsonb_build_object('comment', p_comment));
 return j;
end; $$;
revoke all on function public.regenerate_item(uuid,text) from public, anon;
grant execute on function public.regenerate_item(uuid,text) to authenticated;

-- ---------------------------------------------------------------------------
-- Realtime (ignora se a publicação não existir ou a tabela já estiver nela).
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
 foreach t in array array['projects','project_stages','artifacts','jobs','events','worker_heartbeats'] loop
   begin
     execute format('alter publication supabase_realtime add table public.%I', t);
   exception when others then null;
   end;
 end loop;
end $$;
