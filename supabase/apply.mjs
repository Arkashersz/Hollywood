// Aplica as migrações pendentes no banco do Supabase.
// Uso: npm install --no-save pg && node supabase/apply.mjs [--dry-run]
// Lê SUPABASE_URL e SUPABASE_DB_PASSWORD do ambiente ou de worker/.env (a senha só é necessária aqui;
// pode ser apagada do .env depois). Opcional: SUPABASE_DB_HOST / SUPABASE_DB_USER para usar o pooler.
import pg from 'pg';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const envFile = join(here, '..', 'worker', '.env');
const env = { ...process.env };
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (match && env[match[1]] === undefined) env[match[1]] = match[2];
  }
}
const ref = (env.SUPABASE_URL || '').match(/^https:\/\/([a-z0-9]+)\.supabase\.co/)?.[1];
if (!ref || !env.SUPABASE_DB_PASSWORD) {
  console.error('Defina SUPABASE_URL e SUPABASE_DB_PASSWORD (em worker/.env ou no ambiente).');
  process.exit(1);
}
const dryRun = process.argv.includes('--dry-run');
const TABLES = ['projects', 'project_stages', 'approvals', 'jobs', 'artifacts', 'worker_heartbeats', 'events'];

// Conexão direta primeiro; se a rede não tiver IPv6, tenta os poolers regionais.
const REGIONS = ['sa-east-1', 'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'eu-central-1', 'eu-central-2', 'eu-west-1', 'eu-west-2', 'eu-west-3',
  'eu-north-1', 'ca-central-1', 'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1', 'ap-northeast-2', 'ap-south-1'];
const targets = env.SUPABASE_DB_HOST
  ? [{ host: env.SUPABASE_DB_HOST, user: env.SUPABASE_DB_USER || `postgres.${ref}` }]
  : [{ host: `db.${ref}.supabase.co`, user: 'postgres' },
    ...REGIONS.flatMap((r) => [0, 1].map((n) => ({ host: `aws-${n}-${r}.pooler.supabase.com`, user: `postgres.${ref}` })))];

async function connect() {
  let last;
  for (const target of targets) {
    const client = new pg.Client({ ...target, port: 5432, database: 'postgres', password: env.SUPABASE_DB_PASSWORD,
      ssl: { rejectUnauthorized: false }, connectionTimeoutMillis: 8000 });
    client.on('error', () => {});
    try {
      await client.connect();
      console.log('Conectado a', target.host);
      return client;
    } catch (error) {
      last = error;
      await client.end().catch(() => {});
    }
  }
  throw new Error(`Não foi possível conectar ao banco: ${last?.message}`);
}

const db = await connect();
try {
  const existing = (await db.query(`select table_name from information_schema.tables where table_schema='public' order by 1`)).rows.map((r) => r.table_name);
  const tracked = existing.includes('studio_migrations');
  const clash = existing.filter((t) => TABLES.includes(t));
  console.log('Tabelas em public:', existing.length ? existing.join(', ') : '(nenhuma)');
  if (!tracked && clash.length) {
    throw new Error(`O banco já tem tabelas do TikTok Studio (${clash.join(', ')}) sem registro de migrações. Nada foi alterado; confira antes de continuar.`);
  }
  const done = tracked ? new Set((await db.query('select name from public.studio_migrations')).rows.map((r) => r.name)) : new Set();
  const pending = readdirSync(join(here, 'migrations')).filter((f) => f.endsWith('.sql')).sort().filter((f) => !done.has(f));
  console.log('Pendentes:', pending.length ? pending.join(', ') : '(nenhuma)');
  if (!dryRun) {
    for (const file of pending) {
      await db.query('begin');
      try {
        await db.query(`create table if not exists public.studio_migrations (name text primary key, applied_at timestamptz not null default now());
                        alter table public.studio_migrations enable row level security;`);
        await db.query(readFileSync(join(here, 'migrations', file), 'utf8'));
        await db.query('insert into public.studio_migrations(name) values ($1)', [file]);
        await db.query('commit');
        console.log('ok ', file);
      } catch (error) {
        await db.query('rollback');
        throw new Error(`${file}: ${error.message}`);
      }
    }
    await db.query(`notify pgrst, 'reload schema'`);
  }
  const check = await db.query(`select
    (select count(*) from information_schema.tables where table_schema='public' and table_name = any($1)) as tables,
    (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'
      and p.proname in ('create_project','decide_stage','retry_stage','regenerate_item','claim_next_job')) as functions,
    (select count(*) from storage.buckets where id='artifacts') as buckets,
    (select count(*) from pg_tables where schemaname='public' and tablename = any($1) and not rowsecurity) as tables_without_rls`, [TABLES]);
  console.log('Estado:', JSON.stringify(check.rows[0]));
} finally {
  await db.end();
}
