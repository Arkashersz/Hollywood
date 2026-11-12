-- TikTok Studio · ajustar um item (imagem/vídeo) a qualquer momento: etapa parada, em revisão ou já aprovada.
-- Pode trocar o prompt do item. Numa etapa parada, refaz o item e segue com o que faltava; numa etapa em revisão
-- ou aprovada, refaz só aquele item e a etapa volta para a sua revisão.
create or replace function public.adjust_item(p_stage_id uuid, p_item_key text, p_prompt text default null, p_comment text default null)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; v_prompt text := nullif(btrim(p_prompt), ''); v_comment text := nullif(btrim(p_comment), ''); v_payload jsonb;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.stage_key not in ('character_images','character_sheets','scene_images','production') then
   raise exception 'Esta etapa não tem imagens ou vídeos para ajustar';
 end if;
 if s.status not in ('failed','pending_review','approved') then raise exception 'Esta etapa não pode ser ajustada agora'; end if;
 if nullif(btrim(p_item_key), '') is null then raise exception 'Informe o item'; end if;
 if v_prompt is not null and length(v_prompt) > 8000 then raise exception 'Prompt grande demais (máximo 8000 caracteres)'; end if;
 if exists(select 1 from public.jobs j where j.stage_id=s.id and j.status in ('queued','running')) then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;

 v_payload := s.payload - 'error';
 if v_prompt is not null then
   v_payload := v_payload || jsonb_build_object('prompt_overrides',
     coalesce(v_payload->'prompt_overrides', '{}'::jsonb) || jsonb_build_object(p_item_key, v_prompt));
 end if;

 if s.status = 'failed' then
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key, 'resume', true, 'force_items', jsonb_build_array(p_item_key)));
 else
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'regen_item',
           jsonb_strip_nulls(jsonb_build_object('stage_key', s.stage_key, 'item_key', p_item_key, 'comment', v_comment)));
   update public.projects set status='active' where id=s.project_id and status='completed';
 end if;
 update public.project_stages set status='queued', payload=v_payload where id=s.id returning * into s;
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, format('Ajuste pedido em "%s": %s.', s.title, p_item_key),
         jsonb_build_object('comment', v_comment, 'prompt_changed', v_prompt is not null));
 return s;
end; $$;
revoke all on function public.adjust_item(uuid,text,text,text) from public, anon;
grant execute on function public.adjust_item(uuid,text,text,text) to authenticated;
