-- TikTok Studio · clipe-piloto. Na etapa de vídeos o worker gera só o primeiro clipe e para em revisão
-- (payload.pilot = true). Aprovar esse piloto NÃO conclui a etapa: ela volta para a fila para gerar os
-- clipes restantes (payload.pilot_approved = true); só a aprovação seguinte, do lote completo, a conclui.
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

 if p_decision='approve' and coalesce((s.payload->>'pilot')::boolean, false) and not coalesce((s.payload->>'pilot_approved')::boolean, false) then
   update public.project_stages set status='queued', payload=(payload - 'pilot') || jsonb_build_object('pilot_approved', true)
    where id=s.id returning * into s;
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key));
   insert into public.events(project_id, stage_key, message)
   values (s.project_id, s.stage_key, format('Clipe de teste de "%s" aprovado; gerando os demais.', s.title));
   return s;
 end if;

 if p_decision='approve' then
   update public.project_stages set status='approved', approved_at=now() where id=s.id returning * into s;
   select * into nxt from public.project_stages
    where project_id=s.project_id and stage_order>s.stage_order and status='locked'
    order by stage_order limit 1 for update;
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
