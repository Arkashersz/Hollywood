-- TikTok Studio · voltar para uma etapa anterior já aprovada.
-- A etapa escolhida volta para "em revisão" (conteúdo mantido: dá para ajustar itens, pedir ajustes ou aprovar de novo).
-- As etapas seguintes que já tinham andado são bloqueadas e ganham nova versão, de modo que, ao aprovar a escolhida,
-- tudo o que depende dela é refeito. Prompts editados pelo usuário (prompt_overrides) são mantidos.
create or replace function public.reopen_stage(p_stage_id uuid, p_reason text default null)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; v_reason text := nullif(btrim(p_reason), ''); v_reset int;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.status <> 'approved' then raise exception 'Só dá para voltar para uma etapa já aprovada'; end if;
 if exists(select 1 from public.jobs j where j.project_id=s.project_id and j.status='running') then
   raise exception 'Há uma tarefa em andamento neste projeto; espere terminar (ou parar) e tente de novo';
 end if;

 update public.jobs set status='cancelled', finished_at=now() where project_id=s.project_id and status='queued';

 update public.project_stages
    set status='locked', current_version=current_version+1, approved_at=null, submitted_at=null,
        payload = payload - 'error' - 'progress' - 'pilot' - 'pilot_remaining' - 'pilot_approved' - 'next_generation' - 'warnings' - 'review_comment'
  where project_id=s.project_id and stage_order > s.stage_order and status <> 'locked';
 get diagnostics v_reset = row_count;

 update public.project_stages set status='pending_review', approved_at=null, payload = payload - 'error' - 'progress'
  where id=s.id returning * into s;
 update public.projects set status='active', current_stage=s.stage_key where id=s.project_id;
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, format('Voltou para "%s"; %s etapa(s) seguinte(s) serão refeitas depois da sua aprovação.', s.title, v_reset),
         jsonb_build_object('reason', v_reason, 'reset_stages', v_reset));
 return s;
end; $$;
revoke all on function public.reopen_stage(uuid,text) from public, anon;
grant execute on function public.reopen_stage(uuid,text) to authenticated;
