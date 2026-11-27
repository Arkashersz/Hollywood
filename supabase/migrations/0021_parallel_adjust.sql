-- TikTok Studio · ajustar outros itens enquanto um ajuste ainda está rodando.
-- Cada pedido vira a sua própria tarefa 'regen_item'; o worker junta as da mesma etapa e as roda ao mesmo tempo.
-- Continua proibido: ajustar um item que já está sendo ajustado, e ajustar enquanto a etapa inteira está sendo gerada.

-- Confere se a etapa aceita um novo ajuste dos itens p_keys. Uso interno das funções abaixo.
create or replace function public.studio_check_adjust(p_stage public.project_stages, p_keys text[])
returns void language plpgsql security definer set search_path=public as $$
declare v_busy text;
begin
 if p_stage.stage_key not in ('character_images','character_sheets','scene_images','production') then
   raise exception 'Esta etapa não tem imagens ou vídeos para ajustar';
 end if;
 if not (p_stage.status in ('failed','pending_review','approved')
         or (p_stage.status in ('queued','in_progress')
             and exists(select 1 from public.jobs j where j.stage_id=p_stage.id and j.status in ('queued','running') and j.job_type='regen_item'))) then
   raise exception 'Esta etapa não pode ser ajustada agora';
 end if;
 if exists(select 1 from public.jobs j where j.stage_id=p_stage.id and j.status in ('queued','running') and j.job_type <> 'regen_item') then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;

 select string_agg(distinct b.key, ', ') into v_busy from (
   select j.input->>'item_key' as key from public.jobs j where j.stage_id=p_stage.id and j.status in ('queued','running')
   union all
   select e->>'item_key' from public.jobs j,
          lateral jsonb_array_elements(case when jsonb_typeof(j.input->'items')='array' then j.input->'items' else '[]'::jsonb end) e
    where j.stage_id=p_stage.id and j.status in ('queued','running')
 ) b where b.key = any(p_keys);
 if v_busy is not null then
   raise exception 'Já há um ajuste em andamento para: %. Espere ele terminar para ajustar de novo.', v_busy;
 end if;
end; $$;
revoke all on function public.studio_check_adjust(public.project_stages, text[]) from public, anon, authenticated;

create or replace function public.adjust_item(p_stage_id uuid, p_item_key text, p_prompt text default null, p_comment text default null)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; v_prompt text := nullif(btrim(p_prompt), ''); v_comment text := nullif(btrim(p_comment), ''); v_payload jsonb;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if nullif(btrim(p_item_key), '') is null then raise exception 'Informe o item'; end if;
 if v_prompt is not null and length(v_prompt) > 8000 then raise exception 'Prompt grande demais (máximo 8000 caracteres)'; end if;
 perform public.studio_check_adjust(s, array[p_item_key]);

 v_payload := s.payload - 'error';
 if v_prompt is not null then
   v_payload := v_payload || jsonb_build_object('prompt_overrides',
     coalesce(v_payload->'prompt_overrides', '{}'::jsonb) || jsonb_build_object(p_item_key, v_prompt));
 end if;

 if s.status = 'failed' then
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key, 'resume', true, 'force_items', jsonb_build_array(p_item_key)));
 else
   -- clock_timestamp: pedidos seguidos têm horários diferentes, e o worker respeita a ordem em que foram feitos
   insert into public.jobs(project_id, stage_id, job_type, input, created_at)
   values (s.project_id, s.id, 'regen_item',
           jsonb_strip_nulls(jsonb_build_object('stage_key', s.stage_key, 'item_key', p_item_key, 'comment', v_comment, 'prompt', v_prompt)),
           clock_timestamp());
   update public.projects set status='active' where id=s.project_id and status='completed';
 end if;
 update public.project_stages set status=case when s.status='in_progress' then s.status else 'queued' end, payload=v_payload
  where id=s.id returning * into s;
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, format('Ajuste pedido em "%s": %s.', s.title, p_item_key),
         jsonb_build_object('comment', v_comment, 'prompt_changed', v_prompt is not null));
 return s;
end; $$;
revoke all on function public.adjust_item(uuid,text,text,text) from public, anon;
grant execute on function public.adjust_item(uuid,text,text,text) to authenticated;

create or replace function public.adjust_items(p_stage_id uuid, p_items jsonb)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; v_items jsonb := '[]'::jsonb; v_keys text[] := '{}'; e jsonb; v_key text; v_comment text;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then raise exception 'Selecione ao menos um item'; end if;
 if jsonb_array_length(p_items) > 200 then raise exception 'Itens demais de uma vez (máximo 200)'; end if;

 for e in select * from jsonb_array_elements(p_items) loop
   v_key := nullif(btrim(e->>'key'), '');
   v_comment := nullif(btrim(e->>'comment'), '');
   if v_key is null then raise exception 'Informe o item'; end if;
   if v_comment is not null and length(v_comment) > 4000 then raise exception 'Observação grande demais (máximo 4000 caracteres)'; end if;
   if v_key = any(v_keys) then continue; end if;
   v_keys := v_keys || v_key;
   v_items := v_items || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object('item_key', v_key, 'comment', v_comment)));
 end loop;
 perform public.studio_check_adjust(s, v_keys);

 if s.status = 'failed' then
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key, 'resume', true, 'force_items', to_jsonb(v_keys)));
 else
   insert into public.jobs(project_id, stage_id, job_type, input, created_at)
   values (s.project_id, s.id, 'regen_item', jsonb_build_object('stage_key', s.stage_key, 'items', v_items), clock_timestamp());
   update public.projects set status='active' where id=s.project_id and status='completed';
 end if;
 update public.project_stages set status=case when s.status='in_progress' then s.status else 'queued' end, payload=payload - 'error'
  where id=s.id returning * into s;
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, format('Ajuste pedido em "%s": %s itens.', s.title, cardinality(v_keys)),
         jsonb_build_object('items', to_jsonb(v_keys)));
 return s;
end; $$;
revoke all on function public.adjust_items(uuid,jsonb) from public, anon;
grant execute on function public.adjust_items(uuid,jsonb) to authenticated;
