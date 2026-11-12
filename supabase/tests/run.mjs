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
assert.equal(stages.length, 9);
assert.deepEqual(stages.map((s) => s.status), ['queued', ...Array(8).fill('locked')]);
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
step('create_project grava 9 etapas e enfileira a análise');

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
for (let i = 0; i < 9; i++) {
  await as(null, 'service_role');
  await db.query(`update jobs set status='succeeded' where project_id=$1`, [project.id]);
  await db.query(`update project_stages set status='pending_review' where id=$1`, [stages[i].id]);
  await as(A);
  [s] = await q(`select * from decide_stage($1,'approve')`, [stages[i].id]);
  assert.equal(s.status, 'approved');
  const after = await q(`select * from project_stages where project_id=$1 order by stage_order`, [project.id]);
  if (i < 8) {
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
assert.equal((await q(`select * from approvals where project_id=$1`, [project.id])).length, 10);
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

console.log('Todas as verificações de SQL passaram.');
