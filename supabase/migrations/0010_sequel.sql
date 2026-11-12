-- TikTok Studio · "Criar parte 2".
-- Um projeto concluído pode gerar uma continuação: com o link da parte 2 do vídeo original (o Claude analisa esse vídeo e
-- continua a SUA história seguindo o que acontece nele) ou sem link (o Claude inventa a continuação a partir do resumo da parte anterior).
alter table public.projects add column if not exists parent_id uuid references public.projects(id) on delete set null;
alter table public.projects add column if not exists part int not null default 1 check (part >= 1);
alter table public.projects add column if not exists sequel_notes text;

create or replace function public.create_sequel(p_parent uuid, p_source text default null, p_title text default null, p_notes text default null)
returns public.projects language plpgsql security definer set search_path=public as $$
declare par public.projects; p public.projects; s_id uuid; v_source text := nullif(btrim(p_source), ''); v_notes text := nullif(btrim(p_notes), ''); v_owner uuid; v_part int;
begin
 if auth.uid() is null then raise exception 'Not authenticated'; end if;
 select user_id into v_owner from public.studio_owner;
 if v_owner is null or v_owner <> auth.uid() then raise exception 'Este TikTok Studio pertence a outra conta.'; end if;
 select * into par from public.projects where id = p_parent and owner_id = auth.uid();
 if par.id is null then raise exception 'Projeto de origem não encontrado'; end if;
 if par.status <> 'completed' then raise exception 'A parte 2 só pode ser criada depois que o projeto estiver concluído.'; end if;
 if v_notes is not null and length(v_notes) > 4000 then raise exception 'As ideias para a continuação passam de 4000 caracteres'; end if;
 v_part := par.part + 1;
 insert into public.projects(owner_id, title, source_url, status, current_stage, mode, parent_id, part, sequel_notes)
 values (auth.uid(), coalesce(nullif(btrim(p_title), ''), regexp_replace(par.title, '\s*·?\s*Parte \d+$', '') || ' · Parte ' || v_part),
         v_source, 'active', 'analysis', case when v_source is null then 'inspired' else par.mode end, par.id, v_part, v_notes)
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
revoke all on function public.create_sequel(uuid,text,text,text) from public, anon;
grant execute on function public.create_sequel(uuid,text,text,text) to authenticated;
