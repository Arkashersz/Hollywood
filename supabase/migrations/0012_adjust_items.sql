-- TikTok Studio · ajustar vários itens de uma vez (imagens/vídeos), cada um com a sua observação.
-- p_items: [{"key": "img-cena-a", "comment": "casaco mais escuro"}, ...]. Mesmas regras de adjust_item:
-- etapa parada refaz os itens e segue; etapa em revisão/aprovada refaz só eles e volta para a revisão.
create or replace function public.adjust_items(p_stage_id uuid, p_items jsonb)
returns public.project_stages language plpgsql security definer set search_path=public as $$
declare s public.project_stages; v_items jsonb := '[]'::jsonb; v_keys jsonb := '[]'::jsonb; e jsonb; v_key text; v_comment text;
begin
 select st.* into s from public.project_stages st join public.projects p on p.id=st.project_id
  where st.id=p_stage_id and p.owner_id=auth.uid() for update of st;
 if not found then raise exception 'Stage not found or access denied'; end if;
 if s.stage_key not in ('character_images','character_sheets','scene_images','production') then
   raise exception 'Esta etapa não tem imagens ou vídeos para ajustar';
 end if;
 if s.status not in ('failed','pending_review','approved') then raise exception 'Esta etapa não pode ser ajustada agora'; end if;
 if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then raise exception 'Selecione ao menos um item'; end if;
 if jsonb_array_length(p_items) > 200 then raise exception 'Itens demais de uma vez (máximo 200)'; end if;
 if exists(select 1 from public.jobs j where j.stage_id=s.id and j.status in ('queued','running')) then
   raise exception 'Já existe uma tarefa em andamento para esta etapa';
 end if;

 for e in select * from jsonb_array_elements(p_items) loop
   v_key := nullif(btrim(e->>'key'), '');
   v_comment := nullif(btrim(e->>'comment'), '');
   if v_key is null then raise exception 'Informe o item'; end if;
   if v_comment is not null and length(v_comment) > 4000 then raise exception 'Observação grande demais (máximo 4000 caracteres)'; end if;
   if v_keys ? v_key then continue; end if;
   v_keys := v_keys || to_jsonb(v_key);
   v_items := v_items || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object('item_key', v_key, 'comment', v_comment)));
 end loop;

 if s.status = 'failed' then
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'run_stage', jsonb_build_object('stage_key', s.stage_key, 'resume', true, 'force_items', v_keys));
 else
   insert into public.jobs(project_id, stage_id, job_type, input)
   values (s.project_id, s.id, 'regen_item', jsonb_build_object('stage_key', s.stage_key, 'items', v_items));
   update public.projects set status='active' where id=s.project_id and status='completed';
 end if;
 update public.project_stages set status='queued', payload=payload - 'error' where id=s.id returning * into s;
 insert into public.events(project_id, stage_key, message, details)
 values (s.project_id, s.stage_key, format('Ajuste pedido em "%s": %s itens.', s.title, jsonb_array_length(v_keys)),
         jsonb_build_object('items', v_keys));
 return s;
end; $$;
revoke all on function public.adjust_items(uuid,jsonb) from public, anon;
grant execute on function public.adjust_items(uuid,jsonb) to authenticated;
