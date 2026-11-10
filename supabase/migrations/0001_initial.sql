create extension if not exists pgcrypto;

create type public.project_status as enum ('draft','active','paused','completed','archived');
create type public.stage_status as enum ('locked','queued','in_progress','pending_review','approved','changes_requested','failed');
create type public.job_status as enum ('queued','running','succeeded','failed','blocked','cancelled');

create table public.projects (
 id uuid primary key default gen_random_uuid(),
 owner_id uuid not null references auth.users(id) on delete cascade,
 title text not null,
 source_url text,
 source_video_path text,
 status public.project_status not null default 'draft',
 current_stage text not null default 'analysis',
 metadata jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);

create table public.project_stages (
 id uuid primary key default gen_random_uuid(),
 project_id uuid not null references public.projects(id) on delete cascade,
 stage_key text not null,
 stage_order int not null,
 title text not null,
 status public.stage_status not null default 'locked',
 current_version int not null default 1,
 payload jsonb not null default '{}'::jsonb,
 submitted_at timestamptz,
 approved_at timestamptz,
 unique(project_id, stage_key), unique(project_id, stage_order)
);

create table public.approvals (
 id uuid primary key default gen_random_uuid(),
 project_id uuid not null references public.projects(id) on delete cascade,
 stage_id uuid not null references public.project_stages(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade,
 decision text not null check (decision in ('approve','request_changes')),
 version int not null,
 comment text,
 created_at timestamptz not null default now()
);

create table public.jobs (
 id uuid primary key default gen_random_uuid(),
 project_id uuid not null references public.projects(id) on delete cascade,
 stage_id uuid references public.project_stages(id) on delete set null,
 job_type text not null,
 status public.job_status not null default 'queued',
 input jsonb not null default '{}'::jsonb,
 output jsonb not null default '{}'::jsonb,
 worker_id text,
 attempts int not null default 0,
 error_code text,
 error_message text,
 created_at timestamptz not null default now(),
 started_at timestamptz,
 finished_at timestamptz
);

create table public.artifacts (
 id uuid primary key default gen_random_uuid(),
 project_id uuid not null references public.projects(id) on delete cascade,
 stage_id uuid references public.project_stages(id) on delete set null,
 artifact_type text not null,
 name text not null,
 storage_path text,
 local_relative_path text,
 version int not null default 1,
 content jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);

create table public.worker_heartbeats (
 worker_id text primary key,
 hostname text,
 status text not null default 'offline',
 last_seen_at timestamptz not null default now(),
 details jsonb not null default '{}'::jsonb
);

create index jobs_queue_idx on public.jobs(status,created_at) where status='queued';
create index projects_owner_idx on public.projects(owner_id,updated_at desc);
create index stages_project_idx on public.project_stages(project_id,stage_order);

-- RLS: usuário só enxerga projetos próprios. Tabelas derivadas seguem o owner do projeto.
alter table public.projects enable row level security;
alter table public.project_stages enable row level security;
alter table public.approvals enable row level security;
alter table public.jobs enable row level security;
alter table public.artifacts enable row level security;
alter table public.worker_heartbeats enable row level security;

create policy "projects owner access" on public.projects for all using (auth.uid()=owner_id) with check (auth.uid()=owner_id);
create policy "stages owner access" on public.project_stages for all using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid())) with check (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));
create policy "approvals owner read" on public.approvals for select using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));
create policy "approvals owner insert" on public.approvals for insert with check (auth.uid()=user_id and exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));
create policy "jobs owner access" on public.jobs for select using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));
create policy "artifacts owner access" on public.artifacts for all using (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid())) with check (exists(select 1 from public.projects p where p.id=project_id and p.owner_id=auth.uid()));
create policy "heartbeat owner read" on public.worker_heartbeats for select using (true);

-- Approval is atomic: only the current review stage can be approved, and the next stage is unlocked.
create or replace function public.decide_stage(p_stage_id uuid,p_decision text,p_comment text default null)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; p_owner uuid;
begin
 if p_decision not in ('approve','request_changes') then raise exception 'Invalid decision'; end if;
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id where st.id=p_stage_id and p.owner_id=auth.uid() for update;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.status <> 'pending_review' then raise exception 'Stage is not awaiting review'; end if;
 insert into public.approvals(project_id,stage_id,user_id,decision,version,comment) values(s.project_id,s.id,auth.uid(),p_decision,s.current_version,p_comment);
 if p_decision='approve' then
   update public.project_stages set status='approved',approved_at=now() where id=s.id returning * into s;
   update public.project_stages set status='queued' where project_id=s.project_id and stage_order=s.stage_order+1 and status='locked';
 else
   update public.project_stages set status='changes_requested',payload=payload||jsonb_build_object('review_comment',p_comment) where id=s.id returning * into s;
 end if;
 return s;
end; $$;
revoke all on function public.decide_stage(uuid,text,text) from public;
grant execute on function public.decide_stage(uuid,text,text) to authenticated;

-- Worker claim: service role only. Queue claiming is atomic.
create or replace function public.claim_next_job(p_worker_id text)
returns setof public.jobs language plpgsql security definer set search_path=public as $$
declare j public.jobs;
begin
 select * into j from public.jobs where status='queued' order by created_at for update skip locked limit 1;
 if not found then return; end if;
 update public.jobs set status='running',worker_id=p_worker_id,attempts=attempts+1,started_at=now() where id=j.id returning * into j;
 return next j;
end; $$;
revoke all on function public.claim_next_job(text) from public,anon,authenticated;
grant execute on function public.claim_next_job(text) to service_role;
