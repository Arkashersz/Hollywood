// Valida as migrações em um Postgres local (PGlite), sem tocar no Supabase real.
// Uso: npm install --no-save @electric-sql/pglite && node supabase/tests/run.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'migrations');
const db = new PGlite();
const A = '00000000-0000-0000-0000-00000000000a';
const B = '00000000-0000-0000-0000-00000000000b';

// Mínimo do ambiente Supabase de que as migrações dependem.
await db.exec(`
 create role anon; create role authenticated; create role service_role bypassrls;
 create schema auth; create schema storage;
 create table auth.users(id uuid primary key);
 create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
 create table storage.buckets(id text primary key, name text, public boolean);
 create table storage.objects(id uuid primary key default gen_random_uuid(), bucket_id text, name text);
 alter table storage.objects enable row level security;
 create function storage.foldername(name text) returns text[] language sql immutable as $$ select (string_to_array(name, '/'))[1:array_length(string_to_array(name, '/'),1)-1] $$;
 grant usage on schema auth, storage, public to anon, authenticated, service_role;
 grant select on storage.objects to authenticated;
 grant all on storage.objects to service_role;
 alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
 alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
 insert into auth.users values ('${A}'), ('${B}');
`);

for (const file of readdirSync(dir).sort()) {
  const sql = readFileSync(join(dir, file), 'utf8').replace(/create extension if not exists pgcrypto;/i, '');
  await db.exec(sql);
  console.log('ok  migração', file);
}

const as = async (uid, role = 'authenticated') => {
  await db.exec(`reset role; select set_config('test.uid', '${uid ?? ''}', false); set role ${role};`);
};
const q = async (sql, params) => (await db.query(sql, params)).rows;
const fails = async (sql, params, pattern) => {
  await assert.rejects(() => db.query(sql, params), pattern);
};
const step = (name) => console.log('ok ', name);

// --- criar projeto -----------------------------------------------------------
await as(A);
const [project] = await q(`select * from create_project($1,$2)`, ['  Teste  ', 'https://www.tiktok.com/@x/video/1']);
assert.equal(project.title, 'Teste');
assert.equal(project.status, 'active');
let stages = await q(`select * from project_stages where project_id=$1 order by stage_order`, [project.id]);
assert.equal(stages.length, 8);
assert.deepEqual(stages.map((s) => s.status), ['queued', ...Array(7).fill('locked')]);
let jobs = await q(`select * from jobs where project_id=$1`, [project.id]);
assert.equal(jobs.length, 1);
assert.equal(jobs[0].input.stage_key, 'analysis');
await fails(`select create_project('x', '  ')`, [], /Informe o link/);
await as(B);
await fails(`select create_project('invasor', 'https://x')`, [], /pertence a outra conta/);
await fails(`insert into projects(owner_id,title) values ($1,'direto')`, [B], /row-level security/);
await as(A);
assert.equal((await q(`select * from studio_owner`)).length, 0, 'tabela do dono não é legível pelo painel');
step('só o dono cria projetos; inserção direta e leitura do dono são bloqueadas');
step('create_project grava 8 etapas (sem imagens de corpo inteiro) e enfileira a análise');

// --- isolamento entre usuários ----------------------------------------------
await as(B);
assert.equal((await q(`select * from projects`)).length, 0);
assert.equal((await q(`select * from project_stages`)).length, 0);
assert.equal((await q(`select * from jobs`)).length, 0);
await fails(`select decide_stage($1,'approve')`, [stages[0].id], /access denied/);
await fails(`select retry_stage($1)`, [stages[0].id], /access denied/);
step('outro usuário não enxerga nem decide');

// --- o painel não escreve direto ---------------------------------------------
await as(A);
await db.query(`update project_stages set status='approved' where project_id=$1`, [project.id]);
stages = await q(`select * from project_stages where project_id=$1 order by stage_order`, [project.id]);
assert.equal(stages[0].status, 'queued', 'update direto do painel deve ser ignorado pelo RLS');
await fails(`insert into approvals(project_id,stage_id,user_id,decision,version) values ($1,$2,$3,'approve',1)`, [project.id, stages[0].id, A], /row-level security/);
await fails(`select claim_next_job('x')`, [], /permission denied/);
await fails(`select decide_stage($1,'approve')`, [stages[0].id], /not awaiting review/);
step('painel não consegue pular aprovação nem reivindicar jobs');

// --- worker ------------------------------------------------------------------
await as(null, 'service_role');
const [claimed] = await q(`select * from claim_next_job('home-pc-01')`);
assert.equal(claimed.status, 'running');
assert.equal((await q(`select * from claim_next_job('home-pc-01')`)).length, 0);
await db.query(`update jobs set status='succeeded' where id=$1`, [claimed.id]);
await db.query(`update project_stages set status='pending_review' where id=$1`, [stages[0].id]);
step('worker reivindica o job uma única vez');

// --- pedir ajustes -----------------------------------------------------------
await as(A);
await fails(`select decide_stage($1,'request_changes','  ')`, [stages[0].id], /Descreva o ajuste/);
let [s] = await q(`select * from decide_stage($1,'request_changes','Refazer o gancho')`, [stages[0].id]);
assert.equal(s.status, 'changes_requested');
assert.equal(s.current_version, 2);
jobs = await q(`select * from jobs where project_id=$1 and status='queued'`, [project.id]);
assert.equal(jobs.length, 1);
assert.equal(jobs[0].input.feedback, 'Refazer o gancho');
await fails(`select retry_stage($1)`, [stages[0].id], /Já existe uma tarefa/);
step('pedir ajustes cria versão 2 e reenfileira com o comentário');

// --- falha + nova tentativa ----------------------------------------------------
await as(null, 'service_role');
await db.query(`update jobs set status='blocked' where project_id=$1 and status='queued'`, [project.id]);
await db.query(`update project_stages set status='failed', payload=payload||'{"error":{"code":"LIMIT_REACHED"}}' where id=$1`, [stages[0].id]);
await as(A);
[s] = await q(`select * from retry_stage($1)`, [stages[0].id]);
assert.equal(s.status, 'queued');
assert.equal(s.payload.error, undefined);
jobs = await q(`select * from jobs where project_id=$1 and status='queued'`, [project.id]);
assert.equal(jobs[0].input.resume, true);
assert.equal(jobs[0].input.feedback, 'Refazer o gancho');
step('retry_stage limpa o erro e reenfileira preservando o comentário');

// --- regenerar item ------------------------------------------------------------
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1 and status='queued'`, [project.id]);
await db.query(`update project_stages set status='pending_review' where id=$1`, [stages[0].id]);
const [img] = await q(`insert into artifacts(project_id,stage_id,artifact_type,name,item_key) values ($1,$2,'image','Folha 1s','img-sheet') returning *`, [project.id, stages[0].id]);
const [doc] = await q(`insert into artifacts(project_id,stage_id,artifact_type,name,item_key) values ($1,$2,'document','Doc 01','doc-01') returning *`, [project.id, stages[0].id]);
await fails(`insert into artifacts(project_id,stage_id,artifact_type,name,item_key) values ($1,$2,'image','dup','img-sheet')`, [project.id, stages[0].id], /unique/);
await as(A);
await fails(`select regenerate_item($1,'x')`, [doc.id], /Somente imagens/);
const [regen] = await q(`select * from regenerate_item($1,'mais claro')`, [img.id]);
assert.equal(regen.job_type, 'regen_item');
assert.equal(regen.input.item_key, 'img-sheet');
await fails(`select regenerate_item($1,'de novo')`, [img.id], /precisa estar em revisão/);
await as(B);
await fails(`select regenerate_item($1,'x')`, [img.id], /access denied/);
step('regenerate_item só para imagem/vídeo, uma tarefa por vez, só do dono');

// --- aprovar até o fim -----------------------------------------------------------
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [project.id]);
for (let i = 0; i < 8; i++) {
  await as(null, 'service_role');
  await db.query(`update jobs set status='succeeded' where project_id=$1`, [project.id]);
  await db.query(`update project_stages set status='pending_review' where id=$1`, [stages[i].id]);
  await as(A);
  [s] = await q(`select * from decide_stage($1,'approve')`, [stages[i].id]);
  assert.equal(s.status, 'approved');
  const after = await q(`select * from project_stages where project_id=$1 order by stage_order`, [project.id]);
  if (i < 7) {
    assert.equal(after[i + 1].status, 'queued');
    const queued = await q(`select * from jobs where project_id=$1 and status='queued'`, [project.id]);
    assert.equal(queued.length, 1);
    assert.equal(queued[0].input.stage_key, after[i + 1].stage_key);
    assert.ok(after.slice(i + 2).every((x) => x.status === 'locked'), 'etapas seguintes continuam bloqueadas');
  }
}
const [done] = await q(`select * from projects where id=$1`, [project.id]);
assert.equal(done.status, 'completed');
assert.equal(done.current_stage, 'production');
assert.equal((await q(`select * from approvals where project_id=$1`, [project.id])).length, 9);
assert.ok((await q(`select * from events where project_id=$1`, [project.id])).length >= 12);
step('aprovações liberam uma etapa por vez e concluem o projeto');

// --- começar de uma etapa avançada (prompts prontos) -------------------------------
await as(B);
await fails(`select create_project_from_plan('x', null, '{"production":[{"name":"a","prompt":"b"}]}')`, [], /pertence a outra conta/);
await as(A);
await fails(`select create_project_from_plan('x', null, '{}')`, [], /pelo menos um prompt/);
await fails(`select create_project_from_plan('x', null, '{"production":[{"name":"a"}]}')`, [], /nome e prompt/);
await fails(`select create_project_from_plan('x', null, '{"scene_images":"não é lista"}')`, [], /Lista inválida/);
const plan = { character_images: [{ name: 'Anna', prompt: 'p1' }], production: [{ name: 'Clipe 1', prompt: 'v1', start: 'Anna' }] };
const [imp] = await q(`select * from create_project_from_plan($1,$2,$3::jsonb)`, ['Meu', null, JSON.stringify(plan)]);
assert.equal(imp.current_stage, 'character_images');
assert.equal(imp.source_url, 'Prompts prontos (sem vídeo de referência)');
let istages = await q(`select stage_key, status, payload from project_stages where project_id=$1 order by stage_order`, [imp.id]);
assert.deepEqual(istages.map((x) => x.status), ['approved', 'approved', 'approved', 'queued', 'approved', 'approved', 'approved', 'approved', 'locked']);
assert.equal(istages[0].payload.skipped, true);
assert.equal((await q(`select * from jobs where project_id=$1`, [imp.id])).length, 1);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [imp.id]);
await db.query(`update project_stages set status='pending_review' where project_id=$1 and stage_key='character_images'`, [imp.id]);
await as(A);
const [cimg] = await q(`select id from project_stages where project_id=$1 and stage_key='character_images'`, [imp.id]);
await q(`select * from decide_stage($1,'approve')`, [cimg.id]);
istages = await q(`select stage_key, status from project_stages where project_id=$1 order by stage_order`, [imp.id]);
assert.equal(istages.find((x) => x.stage_key === 'production').status, 'queued', 'a aprovação pula as etapas já puladas e libera a próxima com itens');
assert.equal((await q(`select input from jobs where project_id=$1 and status='queued'`, [imp.id]))[0].input.stage_key, 'production');
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [imp.id]);
await db.query(`update project_stages set status='pending_review' where project_id=$1 and stage_key='production'`, [imp.id]);
await as(A);
const [prod] = await q(`select id from project_stages where project_id=$1 and stage_key='production'`, [imp.id]);
await q(`select * from decide_stage($1,'approve')`, [prod.id]);
assert.equal((await q(`select status from projects where id=$1`, [imp.id]))[0].status, 'completed');
step('projeto com prompts prontos: etapas puladas, fila na primeira com itens, aprovação segue em frente e conclui');

// --- clipe-piloto: aprovar o primeiro vídeo libera os demais ------------------------------
const [pp] = await q(`select * from create_project_from_plan('Piloto', null, $1::jsonb)`, [JSON.stringify({ production: [{ name: 'a', prompt: 'p' }, { name: 'b', prompt: 'q' }] })]);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [pp.id]);
await db.query(`update project_stages set status='pending_review', payload=payload||'{"pilot":true}' where project_id=$1 and stage_key='production'`, [pp.id]);
await as(A);
const [pilotStage] = await q(`select id from project_stages where project_id=$1 and stage_key='production'`, [pp.id]);
let [after] = await q(`select * from decide_stage($1,'approve')`, [pilotStage.id]);
assert.equal(after.status, 'queued', 'aprovar o piloto reenfileira a mesma etapa em vez de concluí-la');
assert.equal(after.payload.pilot, undefined);
assert.equal(after.payload.pilot_approved, true);
assert.equal((await q(`select status from projects where id=$1`, [pp.id]))[0].status, 'active');
assert.equal((await q(`select * from jobs where project_id=$1 and status='queued'`, [pp.id])).length, 1);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [pp.id]);
await db.query(`update project_stages set status='pending_review' where id=$1`, [pilotStage.id]);
await as(A);
[after] = await q(`select * from decide_stage($1,'approve')`, [pilotStage.id]);
assert.equal(after.status, 'approved', 'a aprovação do lote completo conclui a etapa');
assert.equal((await q(`select status from projects where id=$1`, [pp.id]))[0].status, 'completed');
step('clipe-piloto: aprovar o primeiro vídeo gera o restante; só a segunda aprovação conclui');

// --- storage ------------------------------------------------------------------
await as(null, 'service_role');
await db.query(`insert into storage.objects(bucket_id,name) values ('artifacts',$1),('artifacts','outro/x.jpg')`, [`${project.id}/imagens/a.jpg`]);
await as(A);
assert.equal((await q(`select * from storage.objects`)).length, 1);
await as(B);
assert.equal((await q(`select * from storage.objects`)).length, 0);
step('bucket: só o dono do projeto lê as prévias');

console.log('\nTodas as verificações de SQL passaram.');

// --- modo cópia fiel (no fim: o projeto extra enfileira uma análise) ---------------
await as(A);
assert.equal(project.mode, 'inspired', 'modo padrão é inspirado');
const [copy] = await q(`select * from create_project($1,$2,$3)`, ['Cópia', 'https://www.tiktok.com/@x/video/9', 'clone']);
assert.equal(copy.mode, 'clone');
assert.equal((await q(`select title from project_stages where project_id=$1 and stage_key='script'`, [copy.id]))[0].title, 'Roteiro fiel ao original');
await fails(`select create_project('x','https://x','qualquer')`, [], /Modo inválido/);
step('create_project aceita o modo cópia fiel (padrão: inspirado)');

// --- excluir projeto -----------------------------------------------------------
await as(B);
await fails(`select delete_project($1)`, [copy.id], /Projeto não encontrado/);
await as(null, 'service_role');
await db.query(`update jobs set status='running' where project_id=$1`, [copy.id]);
await as(A);
await fails(`select delete_project($1)`, [copy.id], /trabalhando neste projeto/);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [copy.id]);
await as(A);
await q(`select delete_project($1)`, [copy.id]);
assert.equal((await q(`select * from projects where id=$1`, [copy.id])).length, 0);
assert.equal((await q(`select * from project_stages where project_id=$1`, [copy.id])).length, 0);
assert.equal((await q(`select * from jobs where project_id=$1`, [copy.id])).length, 0);
step('delete_project: só o dono, não durante uma tarefa em andamento, e apaga tudo em cascata');

// --- ajustar item depois de aprovada / parada ------------------------------------
await as(A);
const [ap] = await q(`select * from create_project_from_plan('Ajuste', null, $1::jsonb)`, [JSON.stringify({ scene_images: [{ name: 'a', prompt: 'p' }] })]);
await as(null, 'service_role');
const [gs] = await q(`select * from project_stages where project_id=$1 and stage_key='scene_images'`, [ap.id]);
await db.query(`update jobs set status='succeeded' where project_id=$1`, [ap.id]);
await db.query(`update project_stages set status='failed', payload=payload||'{"error":{"code":"X","message":"m"}}'::jsonb where id=$1`, [gs.id]);
await as(B);
await fails(`select adjust_item($1,'img-cena-a','novo')`, [gs.id], /Stage not found/);
await as(A);
await fails(`select adjust_item($1,'  ','novo')`, [gs.id], /Informe o item/);
await fails(`select adjust_item((select id from project_stages where project_id=$1 and stage_key='script'),'x')`, [ap.id], /não tem imagens ou vídeos/);
let [adj] = await q(`select * from adjust_item($1,'img-cena-a','prompt novo')`, [gs.id]);
assert.equal(adj.status, 'queued');
assert.equal(adj.payload.error, undefined);
assert.equal(adj.payload.prompt_overrides['img-cena-a'], 'prompt novo');
let jb = (await q(`select * from jobs where stage_id=$1 and status='queued'`, [gs.id]))[0];
assert.equal(jb.job_type, 'run_stage');
assert.deepEqual(jb.input.force_items, ['img-cena-a']);
await fails(`select adjust_item($1,'img-cena-a','outro')`, [gs.id], /não pode ser ajustada agora/);
// etapa já aprovada: refaz só o item e volta para revisão
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where stage_id=$1`, [gs.id]);
await db.query(`update project_stages set status='approved' where id=$1`, [gs.id]);
await as(A);
[adj] = await q(`select * from adjust_item($1,'img-cena-a',null,'mais claro')`, [gs.id]);
assert.equal(adj.status, 'queued');
assert.equal(adj.payload.prompt_overrides['img-cena-a'], 'prompt novo', 'prompt anterior é mantido quando não se envia outro');
jb = (await q(`select * from jobs where stage_id=$1 and status='queued'`, [gs.id]))[0];
assert.equal(jb.job_type, 'regen_item');
assert.equal(jb.input.item_key, 'img-cena-a');
assert.equal(jb.input.comment, 'mais claro');
step('adjust_item: etapa parada refaz o item e segue; etapa aprovada refaz só o item; só o dono');

// --- ajustar outro item enquanto um ajuste ainda roda ----------------------------
const activeJobs = async () => { await as(null, 'service_role'); const rows = await q(`select * from jobs where stage_id=$1 and status in ('queued','running') order by created_at`, [gs.id]); await as(A); return rows; };
await as(A);
// o ajuste de img-cena-a (acima) continua na fila
await fails(`select adjust_item($1,'img-cena-a',null,'de novo')`, [gs.id], /Já há um ajuste em andamento para: img-cena-a/);
[adj] = await q(`select * from adjust_item($1,'img-cena-b','prompt b','casaco azul')`, [gs.id]);
assert.equal(adj.status, 'queued');
let live = await activeJobs();
assert.deepEqual(live.map((j) => j.input.item_key), ['img-cena-a', 'img-cena-b']);
assert.equal(live[1].input.prompt, 'prompt b', 'o prompt editado vai na própria tarefa');
await as(null, 'service_role');
await db.query(`update jobs set status='running' where stage_id=$1 and status='queued'`, [gs.id]);
await db.query(`update project_stages set status='in_progress' where id=$1`, [gs.id]);
await as(A);
await fails(`select adjust_items($1, '[{"key":"img-cena-c"},{"key":"img-cena-b"}]'::jsonb)`, [gs.id], /Já há um ajuste em andamento para: img-cena-b/);
[adj] = await q(`select * from adjust_items($1, '[{"key":"img-cena-c","comment":"x"},{"key":"img-cena-d"}]'::jsonb)`, [gs.id]);
assert.equal(adj.status, 'in_progress', 'a etapa que o worker já está processando não volta para a fila');
await fails(`select adjust_item($1,'img-cena-d')`, [gs.id], /Já há um ajuste em andamento para: img-cena-d/);
await fails(`select decide_stage($1,'approve')`, [gs.id], /not awaiting review/);
await fails(`select studio_check_adjust((select s from project_stages s where s.id=$1), array['x'])`, [gs.id], /permission denied/);
await as(B);
await fails(`select adjust_item($1,'img-cena-e')`, [gs.id], /Stage not found/);
// enquanto a etapa inteira está sendo gerada, não entra ajuste
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where stage_id=$1`, [gs.id]);
await db.query(`insert into jobs(project_id, stage_id, job_type, input) values ($1,$2,'run_stage','{}'::jsonb)`, [ap.id, gs.id]);
await as(A);
await fails(`select adjust_item($1,'img-cena-e')`, [gs.id], /não pode ser ajustada agora/);
step('adjust: outros itens entram enquanto um ajuste roda; o mesmo item não; geração completa continua bloqueando');

// --- voltar para uma etapa anterior ---------------------------------------------
await as(A);
const [rp] = await q(`select * from create_project_from_plan('Voltar', null, $1::jsonb)`, [JSON.stringify({ scene_images: [{ name: 'a', prompt: 'p' }] })]);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [rp.id]);
await db.query(`update project_stages set status='approved' where project_id=$1 and stage_order<=6`, [rp.id]);
await db.query(`update project_stages set status='pending_review' where project_id=$1 and stage_order=7`, [rp.id]);
const [r7] = await q(`select * from project_stages where project_id=$1 and stage_order=7`, [rp.id]);
const [r4] = await q(`select * from project_stages where project_id=$1 and stage_order=4`, [rp.id]);
await as(B);
await fails(`select reopen_stage($1)`, [r4.id], /Stage not found/);
await as(A);
await fails(`select reopen_stage($1)`, [r7.id], /já aprovada/);
const [ro] = await q(`select * from reopen_stage($1)`, [r4.id]);
assert.equal(ro.status, 'pending_review');
const aft = await q(`select stage_order,status,current_version from project_stages where project_id=$1 order by stage_order`, [rp.id]);
assert.ok(aft.filter((x) => x.stage_order > 4 && x.stage_order <= 7).every((x) => x.status === 'locked' && x.current_version >= 2), 'seguintes bloqueadas com nova versão');
assert.ok(aft.filter((x) => x.stage_order < 4).every((x) => x.status === 'approved'));
const [rpr] = await q(`select status,current_stage from projects where id=$1`, [rp.id]);
assert.equal(rpr.current_stage, 'character_images');
// aprovar de novo libera e enfileira a etapa seguinte
await q(`select * from decide_stage($1,'approve')`, [r4.id]);
assert.equal((await q(`select status from project_stages where id=$1`, [(await q(`select id from project_stages where project_id=$1 and stage_order=5`, [rp.id]))[0].id]))[0].status, 'queued');
step('reopen_stage: volta a uma etapa aprovada, bloqueia as seguintes e permite aprovar de novo; só o dono');

// --- criar parte 2 -------------------------------------------------------------
await as(A);
const [par] = await q(`select * from create_project('Banana', 'https://tiktok.com/p1', 'clone')`);
await fails(`select create_sequel($1)`, [par.id], /depois que o projeto estiver concluído/);
await as(null, 'service_role');
await db.query(`update projects set status='completed' where id=$1`, [par.id]);
await as(B);
await fails(`select create_sequel($1)`, [par.id], /pertence a outra conta|não encontrado/);
await as(A);
await fails(`select create_sequel($1, null, null, $2)`, [par.id, 'x'.repeat(4001)], /4000/);
const [s2] = await q(`select * from create_sequel($1, null, null, 'um dragão aparece')`, [par.id]);
assert.equal(s2.part, 2); assert.equal(s2.parent_id, par.id); assert.equal(s2.mode, 'inspired', 'sem link não há o que copiar');
assert.equal(s2.source_url, null); assert.equal(s2.sequel_notes, 'um dragão aparece'); assert.match(s2.title, /Banana · Parte 2/);
const st2 = await q(`select stage_key, title, status from project_stages where project_id=$1 order by stage_order`, [s2.id]);
assert.equal(st2.length, 8); assert.equal(st2[0].title, 'Resumo da parte anterior'); assert.equal(st2[0].status, 'queued');
assert.equal((await q(`select * from jobs where project_id=$1 and status='queued'`, [s2.id])).length, 1);
await as(null, 'service_role');
await db.query(`update projects set status='completed' where id=$1`, [s2.id]);
await as(A);
const [s3] = await q(`select * from create_sequel($1, 'https://tiktok.com/parte-3', 'Meu título')`, [s2.id]);
assert.equal(s3.part, 3); assert.equal(s3.title, 'Meu título'); assert.equal(s3.source_url, 'https://tiktok.com/parte-3');
assert.equal((await q(`select title from project_stages where project_id=$1 and stage_key='analysis'`, [s3.id]))[0].title, 'Análise da parte 2 da referência');
const [s4] = await q(`select * from create_sequel($1, 'https://tiktok.com/x')`, [par.id]);
assert.equal(s4.mode, 'clone', 'com link a continuação herda o modo do projeto original');
step('create_sequel: só projeto concluído e dono; com link analisa a referência, sem link resume a parte anterior');

// --- história a partir de uma ideia ---------------------------------------------
await as(A);
const [story] = await q(`select * from create_story_project($1,$2,$3,$4)`, ['  Espelho ', ' Uma menina e um espelho ', 36, 6]);
assert.equal(story.mode, 'story'); assert.equal(story.source_url, null);
assert.equal(story.story_idea, 'Uma menina e um espelho'); assert.equal(story.target_seconds, 36); assert.equal(story.min_scenes, 6);
const storyStages = await q(`select * from project_stages where project_id=$1 order by stage_order`, [story.id]);
assert.equal(storyStages.length, 8); assert.equal(storyStages[0].title, 'Briefing da história'); assert.equal(storyStages[0].status, 'queued');
assert.equal((await q(`select * from jobs where project_id=$1`, [story.id])).length, 1);
await fails(`select create_story_project('x', '  ', 60, 5)`, [], /Conte a ideia/);
await fails(`select create_story_project('x', 'ideia', 10, 5)`, [], /entre 15 e 300/);
await fails(`select create_story_project('x', 'ideia', 60, 0)`, [], /entre 1 e 60/);
await fails(`select create_story_project('x', 'ideia', 30, 10)`, [], /passa de 30 s/);
await as(B);
await fails(`select create_story_project('x', 'ideia', 60, 5)`, [], /pertence a outra conta/);
await as(A);
step('create_story_project grava ideia, duração e cenas mínimas, e valida os limites');

// --- Fábrica de roteiros + workflow de animação ------------------------------------
await as(A);
const prof = { niche: 'Frutinhas', min_s: 90, max_s: 120, min_scenes: 12, references: ['https://tiktok.com/a'], audio: 'ambos' };
const [fac] = await q(`select * from create_factory_project($1,$2::jsonb)`, ['  Novela ', JSON.stringify(prof)]);
assert.equal(fac.mode, 'factory'); assert.equal(fac.title, 'Novela'); assert.equal(fac.current_stage, 'f_refs'); assert.equal(fac.profile.niche, 'Frutinhas');
const facStages = await q(`select stage_key, stage_order, title, status from project_stages where project_id=$1 order by stage_order`, [fac.id]);
assert.deepEqual(facStages.map((x) => x.stage_key), ['f_refs', 'f_proposals', 'f_script', 'f_bible', 'f_workflow', 'character_sheets', 'scene_images', 'production']);
assert.deepEqual(facStages.map((x) => x.status), ['queued', ...Array(7).fill('locked')]);
assert.ok(facStages.filter((x) => /9:16/.test(x.title)).length === 3 && !facStages.some((x) => /16:9/.test(x.title)), 'tudo em 9:16');
assert.equal((await q(`select * from jobs where project_id=$1`, [fac.id]))[0].input.stage_key, 'f_refs');
await fails(`select create_factory_project('x', '[]'::jsonb)`, [], /inválido/);
await fails(`select create_factory_project('x', $1::jsonb)`, [JSON.stringify({ references: ['1', '2', '3', '4', '5', '6'] })], /no máximo 5/);
await fails(`select create_factory_project('x', $1::jsonb)`, [JSON.stringify({ min_s: 100, max_s: 60 })], /máxima/);
await fails(`select create_factory_project('x', $1::jsonb)`, [JSON.stringify({ scene_max_s: 15 })], /10 segundos/);
await fails(`select create_factory_project('x', $1::jsonb)`, [JSON.stringify({ min_s: 30, max_s: 60, min_scenes: 12 })], /somam 72 s/);
await fails(`select create_factory_project('x', $1::jsonb)`, [JSON.stringify({ audio: 'mudo' })], /áudio inválido/);
const [dflt] = await q(`select * from create_factory_project('Padrão', '{}'::jsonb)`);
assert.equal(dflt.mode, 'factory');
await as(B);
await fails(`select create_factory_project('invasor', '{}'::jsonb)`, [], /pertence a outra conta/);
await as(A);
// escolher a história: só na etapa de propostas e enquanto ela está em revisão
const facIds = Object.fromEntries((await q(`select id, stage_key from project_stages where project_id=$1`, [fac.id])).map((x) => [x.stage_key, x.id]));
await fails(`select choose_proposal($1, 2)`, [facIds.f_proposals], /em revisão/);
await as(null, 'service_role');
await db.query(`update project_stages set status='pending_review' where id=$1 or id=$2`, [facIds.f_proposals, facIds.f_refs]);
await as(A);
await fails(`select choose_proposal($1, 2)`, [facIds.f_refs], /não tem propostas/);
await fails(`select choose_proposal($1, 6)`, [facIds.f_proposals], /de 1 a 5/);
const [chosen] = await q(`select * from choose_proposal($1, 4)`, [facIds.f_proposals]);
assert.equal(chosen.payload.chosen, 4);
await as(B);
await fails(`select choose_proposal($1, 1)`, [facIds.f_proposals], /not found|access denied/);
await as(A);
// aprovar as propostas libera o roteiro e mantém a escolha
await q(`select * from decide_stage($1,'approve')`, [facIds.f_proposals]);
const [afterApprove] = await q(`select status, payload from project_stages where id=$1`, [facIds.f_proposals]);
assert.equal(afterApprove.status, 'approved'); assert.equal(afterApprove.payload.chosen, 4);
assert.equal((await q(`select status from project_stages where id=$1`, [facIds.f_script]))[0].status, 'queued');
step('create_factory_project / choose_proposal: perfil validado, 8 etapas em 9:16, escolha da história e só o dono');

// acesso remoto aos vídeos: endereço e senha só para o dono; ninguém escreve pelo painel
await as(null, 'service_role');
await db.query(`insert into worker_media(worker_id, local_url, public_url, token) values ('pc', 'http://127.0.0.1:8765', 'https://x.trycloudflare.com', 'senha')`);
await as(A);
assert.equal((await q(`select token from worker_media`)).length, 1);
await as(B);
assert.equal((await q(`select token from worker_media`)).length, 0, 'outra conta não vê a senha dos vídeos');
await fails(`insert into worker_media(worker_id, token) values ('x', 'y')`, [], /row-level security|permission denied/);
await as(A);
await fails(`insert into worker_media(worker_id, token) values ('x', 'y')`, [], /row-level security|permission denied/);
step('worker_media: só o dono lê a senha dos vídeos e o painel não escreve');

// nível de esforço do Claude por projeto
await as(A);
const [ef1] = await q(`select * from create_project('Esforço', 'https://tiktok.com/e1', 'inspired', 'claude-opus-5-5', 'high')`);
assert.equal(ef1.effort, 'high'); assert.equal(ef1.model, 'claude-opus-5-5');
assert.equal((await q(`select * from jobs where project_id=$1`, [ef1.id])).length, 1, 'a análise continua enfileirada');
assert.equal((await q(`select * from create_project('Sem esforço', 'https://tiktok.com/e2')`))[0].effort, null, 'sem escolha vale o padrão do worker');
await fails(`select create_project('x', 'https://tiktok.com/e3', 'inspired', null, 'turbo')`, [], /esforço inválido/);
assert.equal((await q(`select * from create_story_project('H', 'Uma ideia', 60, 6, null, 'max')`))[0].effort, 'max');
assert.equal((await q(`select * from create_factory_project('F', '{}'::jsonb, null, 'MEDIUM')`))[0].effort, 'medium');
assert.equal((await q(`select * from create_project_from_plan('P', null, '{"production":[{"name":"C1","prompt":"x"}]}'::jsonb, null, 'low')`))[0].effort, 'low');
await as(null, 'service_role');
await db.query(`update projects set status='completed' where id=$1`, [ef1.id]);
await as(A);
assert.equal((await q(`select * from create_sequel($1)`, [ef1.id]))[0].effort, 'high', 'a continuação herda o esforço');
assert.equal((await q(`select * from create_sequel($1, null, null, null, null, 'xhigh')`, [ef1.id]))[0].effort, 'xhigh');
assert.equal((await q(`select * from set_project_effort($1, 'low')`, [ef1.id]))[0].effort, 'low');
assert.equal((await q(`select * from set_project_effort($1, '')`, [ef1.id]))[0].effort, null);
await fails(`select create_project_base('x', 'https://tiktok.com/e4', 'inspired', null)`, [], /permission denied/);
await as(B);
await fails(`select set_project_effort($1, 'max')`, [ef1.id], /not found|access denied/);
step('esforço: escolhido na criação de cada tipo de projeto, herdado pela continuação, trocado depois; só o dono');

// ajustar a história na etapa de prompts de vídeo
await as(A);
const [hp] = await q(`select * from create_project('História', 'https://tiktok.com/h1', 'clone')`);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where project_id=$1`, [hp.id]);
await db.query(`update project_stages set status='failed', payload='{"error":{"code":"X"},"review_comment":"antigo"}'::jsonb where project_id=$1 and stage_key='video_prompts'`, [hp.id]);
await db.query(`update project_stages set payload='{"prompt_overrides":{"vid-clipe-01":"x"}}'::jsonb where project_id=$1 and stage_key='production'`, [hp.id]);
const [hs] = await q(`select * from project_stages where project_id=$1 and stage_key='video_prompts'`, [hp.id]);
const [hx] = await q(`select * from project_stages where project_id=$1 and stage_key='script'`, [hp.id]);
await as(B);
await fails(`select adjust_story($1, 'curto', 120)`, [hs.id], /Stage not found/);
await as(A);
await fails(`select adjust_story($1, 'curto', 120)`, [hx.id], /prompts de vídeo/);
await fails(`select adjust_story($1)`, [hs.id], /duração desejada/);
await fails(`select adjust_story($1, null, 5)`, [hs.id], /entre 30 segundos/);
await fails(`select adjust_story($1, null, null, true)`, [hs.id], /já está completa/);
let [h] = await q(`select * from adjust_story($1, ' focar no campeonato ', 180)`, [hs.id]);
assert.equal(h.status, 'queued'); assert.equal(h.current_version, hs.current_version + 1);
assert.equal(h.payload.story_cut.instruction, 'focar no campeonato'); assert.equal(h.payload.story_cut.target_s, 180);
assert.ok(!h.payload.error && !h.payload.review_comment);
assert.deepEqual((await q(`select payload from project_stages where project_id=$1 and stage_key='production'`, [hp.id]))[0].payload, {});
assert.equal((await q(`select * from jobs where stage_id=$1 and status='queued' and job_type='run_stage'`, [hs.id])).length, 1);
await fails(`select adjust_story($1, 'outra')`, [hs.id], /não está pronta/);
await as(null, 'service_role');
await db.query(`update jobs set status='succeeded' where stage_id=$1`, [hs.id]);
await db.query(`update project_stages set status='pending_review' where id=$1`, [hs.id]);
await as(A);
[h] = await q(`select * from adjust_story($1, null, null, true)`, [hs.id]);
assert.equal(h.payload.story_cut.restore, true);
step('adjust_story: encurtar e voltar à história completa na etapa de prompts de vídeo; só o dono');

console.log('Todas as verificações de SQL passaram.');
