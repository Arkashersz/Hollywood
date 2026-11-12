import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Session, SupabaseClient } from '@supabase/supabase-js';
import {
  Activity, AlertTriangle, Check, ChevronRight, Clapperboard, Cloud, Film, FolderOpen, Link2, LogOut, MessageSquareWarning,
  Bell, ChevronDown, ChevronLeft, Clock, Download, Monitor, Plus, SkipForward, X, RefreshCw, RotateCcw, Settings2, ShieldCheck, Sparkles, Trash2,
} from 'lucide-react';
import { Markdown } from './Markdown';
import { buildPlan, type PlanKey } from './importPlan';
import { AGENT_LABEL, FALLBACK_ICON, STAGE_META, STATUS_LABEL, timeAgo, type Artifact, type EventRow, type Heartbeat, type Project, type Stage } from './types';

type View = 'projects' | 'activity' | 'integrations';
const BUCKET = 'artifacts';
const TABLES = ['projects', 'project_stages', 'artifacts', 'jobs', 'events', 'worker_heartbeats'];

export function Studio({ db, session }: { db: SupabaseClient; session: Session }) {
  const [view, setView] = useState<View>('projects');
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [allStages, setAllStages] = useState<Stage[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState('');
  const [live, setLive] = useState(false);
  useEffect(() => { if (!notice) return; const t = window.setTimeout(() => setNotice(''), 12000); return () => window.clearTimeout(t); }, [notice]);
  const selectedRef = useRef<string | null>(null);
  const signedAt = useRef<Record<string, number>>({});
  selectedRef.current = selectedId;

  const refresh = useCallback(async () => {
    const [p, h, all] = await Promise.all([
      db.from('projects').select('*').order('updated_at', { ascending: false }),
      db.from('worker_heartbeats').select('*').order('last_seen_at', { ascending: false }).limit(1),
      db.from('project_stages').select('*').order('stage_order'),
    ]);
    if (p.error) { setNotice(`Sem acesso ao Supabase: ${p.error.message}`); return; }
    const list = (p.data ?? []) as Project[];
    setProjects(list);
    setAllStages((all.data ?? []) as Stage[]);
    setHeartbeat(((h.data ?? [])[0] as Heartbeat) ?? null);
    const current = selectedRef.current && list.some((x) => x.id === selectedRef.current) ? selectedRef.current : list[0]?.id ?? null;
    if (current !== selectedRef.current) setSelectedId(current);

    const eventsQuery = db.from('events').select('*').order('created_at', { ascending: false }).limit(80);
    if (!current) {
      setStages([]); setArtifacts([]);
      setEvents(((await eventsQuery).data ?? []) as EventRow[]);
      return;
    }
    const [s, a, e] = await Promise.all([
      db.from('project_stages').select('*').eq('project_id', current).order('stage_order'),
      db.from('artifacts').select('*').eq('project_id', current).order('name'),
      eventsQuery,
    ]);
    if (selectedRef.current && selectedRef.current !== current) return; // o usuário trocou de projeto enquanto carregava
    setStages((s.data ?? []) as Stage[]);
    setArtifacts((a.data ?? []) as Artifact[]);
    setEvents((e.data ?? []) as EventRow[]);
  }, [db]);

  // Tempo real, com atualização periódica como reserva (redes que bloqueiam WebSocket).
  useEffect(() => {
    let timer: number | undefined;
    const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(refresh, 400); };
    const channel = db.channel('studio');
    for (const table of TABLES) channel.on('postgres_changes', { event: '*', schema: 'public', table }, schedule);
    channel.subscribe((status) => setLive(status === 'SUBSCRIBED'));
    refresh();
    const poll = window.setInterval(refresh, 15000);
    return () => { window.clearTimeout(timer); window.clearInterval(poll); db.removeChannel(channel); };
  }, [db, refresh]);

  useEffect(() => { refresh(); }, [selectedId, refresh]);

  // URLs assinadas das prévias (bucket privado); renovadas antes de expirar ou quando o arquivo muda.
  useEffect(() => {
    const now = Date.now();
    const wanted = artifacts.filter((a) => a.storage_path).map((a) => ({ key: `${a.storage_path}|${a.updated_at}`, path: a.storage_path as string }));
    const missing = wanted.filter((w) => !urls[w.key] || now - (signedAt.current[w.key] ?? 0) > 45 * 60 * 1000);
    if (!missing.length) return;
    let cancelled = false;
    db.storage.from(BUCKET).createSignedUrls(missing.map((m) => m.path), 3600).then(({ data }) => {
      if (cancelled || !data) return;
      const next: Record<string, string> = {};
      data.forEach((row, i) => { if (row.signedUrl) { next[missing[i].key] = row.signedUrl; signedAt.current[missing[i].key] = Date.now(); } });
      if (Object.keys(next).length) setUrls((old) => ({ ...old, ...next }));
    });
    return () => { cancelled = true; };
  }, [artifacts, db, urls]);

  const call = useCallback(async (fn: string, args: Record<string, unknown>, ok: string) => {
    const { error } = await db.rpc(fn, args);
    setNotice(error ? `Não foi possível concluir: ${error.message}` : ok);
    await refresh();
    return !error;
  }, [db, refresh]);

  const attention = useMemo(() => allStages.filter((x) => x.status === 'pending_review' || x.status === 'failed')
    .map((x) => ({ stage: x, project: projects.find((pr) => pr.id === x.project_id) })).filter((x) => !!x.project) as { stage: Stage; project: Project }[], [allStages, projects]);
  const seenAttention = useRef<number | null>(null);
  useEffect(() => {
    document.title = attention.length ? `(${attention.length}) TikTok Studio` : 'TikTok Studio';
    if (seenAttention.current !== null && attention.length > seenAttention.current && 'Notification' in window && Notification.permission === 'granted') {
      new Notification('TikTok Studio', { body: 'Uma etapa está esperando a sua aprovação.' });
    }
    seenAttention.current = attention.length;
  }, [attention.length]);
  // Cria a parte seguinte de um projeto concluído e abre o novo projeto. Devolve a mensagem de erro, ou null.
  const createSequel = useCallback(async (parentId: string, source: string, title: string, notes: string): Promise<string | null> => {
    const { data, error } = await db.rpc('create_sequel', { p_parent: parentId, p_source: source || null, p_title: title || null, p_notes: notes || null });
    if (error) { setNotice(`Não foi possível criar a continuação: ${error.message}`); return error.message; }
    setNotice(source ? 'Continuação criada. O Claude analisa a parte 2 do original assim que o worker estiver online.' : 'Continuação criada. O Claude resume a história e propõe direções assim que o worker estiver online.');
    await refresh();
    const created = data as { id?: string } | null;
    if (created?.id) setSelectedId(created.id);
    return null;
  }, [db, refresh]);
  // Exclui o projeto e, depois, os arquivos dele no Storage. Devolve a mensagem de erro, ou null se deu certo.
  const deleteProject = useCallback(async (id: string, paths: string[]): Promise<string | null> => {
    const { error } = await db.rpc('delete_project', { p_project_id: id });
    if (error) { setNotice(`Não foi possível excluir: ${error.message}`); return error.message; }
    if (paths.length) await db.storage.from(BUCKET).remove(paths);   // se falhar, só sobram arquivos órfãos; o projeto já saiu
    setNotice('Projeto excluído.');
    setSelectedId(null);
    await refresh();
    return null;
  }, [db, refresh]);
  const download = useCallback(async (path: string, name: string) => {
    const { data, error } = await db.storage.from(BUCKET).createSignedUrl(path, 300, { download: name });
    if (error || !data) { setNotice(`Não foi possível baixar: ${error?.message ?? 'sem link'}`); return; }
    window.open(data.signedUrl, '_blank', 'noopener');
  }, [db]);

  const workerOnline = !!heartbeat && heartbeat.status === 'online' && Date.now() - new Date(heartbeat.last_seen_at).getTime() < 60000;
  const project = projects.find((p) => p.id === selectedId) ?? null;
  const initials = (session.user.email ?? 'TS').slice(0, 2).toUpperCase();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="brandmark"><Clapperboard size={21} /></div><div><b>TIKTOK STUDIO</b><small>CREATIVE WORKFLOW</small></div></div>
        <div className="navlabel">WORKSPACE</div>
        <button className={`nav ${view === 'projects' ? 'active' : ''}`} onClick={() => setView('projects')} title="Projetos"><FolderOpen size={17} /> <span>Projetos</span></button>
        <button className={`nav ${view === 'activity' ? 'active' : ''}`} onClick={() => setView('activity')} title="Atividade"><Activity size={17} /> <span>Atividade</span></button>
        <button className={`nav ${view === 'integrations' ? 'active' : ''}`} onClick={() => setView('integrations')} title="Integrações"><Settings2 size={17} /> <span>Integrações</span></button>
        <div className="sidebottom">
          <div className="localcard">
            <span className={`pulse ${workerOnline ? 'on' : ''}`} /> Worker local <b>{workerOnline ? 'Online' : 'Não conectado'}</b>
            <small>{heartbeat ? `${heartbeat.hostname ?? heartbeat.worker_id} · visto ${timeAgo(heartbeat.last_seen_at)}` : 'Inicie o worker no computador principal.'}</small>
          </div>
          <div className="profile">
            <div className="avatar">{initials}</div>
            <div><b>{session.user.email}</b><small>Conta proprietária</small></div>
            <button className="iconbtn" title="Sair" onClick={() => db.auth.signOut()}><LogOut size={15} /></button>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="crumb">Workspace <ChevronRight size={14} /> <b>{view === 'projects' ? 'Projetos' : view === 'activity' ? 'Atividade' : 'Integrações'}</b></div>
          <div className="topright">
            <span className="secure"><ShieldCheck size={15} /> <span>Aprovação manual ativada</span></span>
            <button className="iconbtn" title="Atualizar" aria-label="Atualizar" onClick={refresh}><RefreshCw size={16} /></button>
            <button className="iconbtn signout" title="Sair" aria-label="Sair" onClick={() => db.auth.signOut()}><LogOut size={16} /></button>
          </div>
        </header>
        <section className="content">
          {notice && <div className="notice" role="status"><Sparkles size={17} /><span>{notice}</span><button className="linkbtn" onClick={() => setNotice('')}>fechar</button></div>}
          {view === 'projects' && (
            <ProjectsView projects={projects} project={project} stages={stages} allStages={allStages} attention={attention} artifacts={artifacts} urls={urls} live={live} workerOnline={workerOnline}
              onSelect={setSelectedId} call={call} download={download} deleteProject={deleteProject} createSequel={createSequel} />
          )}
          {view === 'activity' && <ActivityView events={events} projects={projects} />}
          {view === 'integrations' && <IntegrationsView heartbeat={heartbeat} workerOnline={workerOnline} />}
          <footer><span>TIKTOK STUDIO · V0.3.0</span><span>Claude analisa · ChatGPT imagens · Grok vídeos</span></footer>
        </section>
      </main>
    </div>
  );
}

type Call = (fn: string, args: Record<string, unknown>, ok: string) => Promise<boolean>;

type Attention = { stage: Stage; project: Project }[];
type Download = (path: string, name: string) => void;

function projectState(project: Project, stages: Stage[]): { label: string; tone: string } {
  if (project.status === 'completed') return { label: 'Concluído', tone: 'ok' };
  if (stages.some((x) => x.status === 'failed')) return { label: 'Parada · precisa de você', tone: 'bad' };
  if (stages.some((x) => x.status === 'pending_review')) return { label: 'Aguardando sua aprovação', tone: 'accent' };
  if (stages.some((x) => x.status === 'in_progress')) return { label: 'Em andamento', tone: 'warn' };
  if (stages.some((x) => x.status === 'queued' || x.status === 'changes_requested')) return { label: 'Na fila do worker', tone: 'warn' };
  return { label: 'Em produção', tone: 'muted' };
}

function ProjectsView(props: {
  projects: Project[]; project: Project | null; stages: Stage[]; allStages: Stage[]; attention: Attention; artifacts: Artifact[]; urls: Record<string, string>; live: boolean; workerOnline: boolean;
  onSelect: (id: string) => void; call: Call; download: Download; deleteProject: (id: string, paths: string[]) => Promise<string | null>;
  createSequel: (parentId: string, source: string, title: string, notes: string) => Promise<string | null>;
}) {
  const { projects, project, stages, allStages, attention, artifacts, urls, live, workerOnline, onSelect, call, download, deleteProject, createSequel } = props;
  const [notifyAsked, setNotifyAsked] = useState(() => !('Notification' in window) || Notification.permission !== 'default');
  const [source, setSource] = useState('');
  const [title, setTitle] = useState('');
  const [mode, setMode] = useState<'link' | 'prompts'>('link');
  const [kind, setKind] = useState<'inspired' | 'clone'>('inspired');
  const [texts, setTexts] = useState<Record<PlanKey, string>>({ character_images: '', character_sheets: '', scene_images: '', production: '' });
  const built = useMemo(() => buildPlan(texts), [texts]);
  const [stageId, setStageId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [sequelOpen, setSequelOpen] = useState(false);
  const [sequelSource, setSequelSource] = useState('');
  const [sequelNotes, setSequelNotes] = useState('');
  const [sequelBusy, setSequelBusy] = useState(false);
  const [sequelError, setSequelError] = useState('');
  const approved = stages.filter((s) => s.status === 'approved').length;
  const current = stages.find((s) => s.status !== 'approved') ?? stages[stages.length - 1] ?? null;
  const stage = stages.find((s) => s.id === stageId) ?? current;
  const readyMedia = artifacts.filter((a) => a.artifact_type === 'image' || a.artifact_type === 'video').length;
  const heroItem = artifacts.find((a) => a.artifact_type === 'image' && a.storage_path);
  const heroUrl = heroItem?.storage_path ? urls[`${heroItem.storage_path}|${heroItem.updated_at}`] : undefined;

  useEffect(() => { setStageId(null); setConfirmDelete(false); setDeleteError(''); setSequelOpen(false); setSequelSource(''); setSequelNotes(''); setSequelError(''); }, [project?.id]);

  async function makeSequel() {
    if (!project) return;
    setSequelBusy(true); setSequelError('');
    const error = await createSequel(project.id, sequelSource.trim(), '', sequelNotes.trim());
    setSequelBusy(false);
    if (error) setSequelError(error);
  }

  async function removeProject() {
    if (!project) return;
    setDeleting(true); setDeleteError('');
    const error = await deleteProject(project.id, artifacts.map((a) => a.storage_path).filter((p): p is string => !!p));
    setDeleting(false);
    if (error) setDeleteError(error); else setConfirmDelete(false);
  }

  async function create() {
    if (!source.trim()) return;
    if (await call('create_project', { p_title: title, p_source: source, p_mode: kind }, kind === 'clone' ? 'Projeto criado (cópia fiel). A análise começa assim que o worker estiver online.' : 'Projeto criado. A análise começa assim que o worker estiver online.')) { setSource(''); setTitle(''); }
  }

  async function createFromPrompts() {
    if (!built.total || built.problem) return;
    if (await call('create_project_from_plan', { p_title: title, p_source: source, p_plan: built.plan },
      `Projeto criado com ${built.total} prompt(s). A geração começa assim que o worker estiver online; cada etapa para na sua aprovação.`)) {
      setTexts({ character_images: '', character_sheets: '', scene_images: '', production: '' }); setTitle(''); setSource(''); setMode('link');
    }
  }

  return (
    <>
      <div className="heading">
        <div><div className="eyebrow">CENTRAL DE PRODUÇÃO</div><h1>Seus projetos</h1><p>Cada etapa para para a sua aprovação antes de seguir.</p></div>
        <div className="cloudtag"><Cloud size={16} /> {live ? 'Supabase em tempo real' : 'Supabase: atualização a cada 15 s'}</div>
      </div>

      {attention.length > 0 && (
        <div className="attention">
          <div className="attentionhead"><b><Bell size={15} /> Precisa de você agora</b><em>{attention.length}</em></div>
          <div className="attentioncards">
            {attention.map(({ stage: st, project: pr }) => (
              <button key={st.id} className={`attcard ${st.status}`} onClick={() => onSelect(pr.id)}>
                <span className={`agent ${STAGE_META[st.stage_key]?.agent ?? ''}`}>{STAGE_META[st.stage_key] ? AGENT_LABEL[STAGE_META[st.stage_key].agent] : ''}</span>
                <b>{pr.title}</b><small>{st.title}</small>
                <i>{st.status === 'failed' ? 'Parada · ver o que fazer' : 'Revisar e aprovar'} <ChevronRight size={14} /></i>
              </button>
            ))}
          </div>
          {!notifyAsked && <button className="linkbtn" onClick={() => { setNotifyAsked(true); Notification.requestPermission(); }}>Avisar no navegador quando uma etapa ficar pronta</button>}
        </div>
      )}
      <div className="modetabs" role="tablist" aria-label="Como começar">
        <button role="tab" aria-selected={mode === 'link'} className={mode === 'link' ? 'on' : ''} onClick={() => setMode('link')}><Link2 size={15} /> A partir de um vídeo do TikTok</button>
        <button role="tab" aria-selected={mode === 'prompts'} className={mode === 'prompts' ? 'on' : ''} onClick={() => setMode('prompts')}><SkipForward size={15} /> Já tenho os prompts</button>
      </div>
      {mode === 'prompts' && (
        <div className="importbox">
          <div className="importintro">
            <b>Comece direto na geração</b>
            <small>Cole os prompts que você já tem. As etapas de texto são puladas; só as etapas com prompts rodam, e cada uma para para a sua aprovação.
              Use um bloco por item: uma linha <code>## Nome</code> e, embaixo, o prompt. Para reaproveitar uma imagem, escreva <code>Referências: Nome A, Nome B</code>;
              nos vídeos, <code>Imagem inicial: Nome da imagem</code> (ou <code>anterior</code>). Sem <code>##</code>, separe os itens com uma linha <code>---</code>.</small>
          </div>
          {IMPORT_FIELDS.map((f) => (
            <label className="importfield" key={f.key}>
              <span><b>{f.label}</b> <em>{f.agent}</em><small>{f.hint}</small></span>
              <textarea rows={5} value={texts[f.key]} onChange={(e) => setTexts((old) => ({ ...old, [f.key]: e.target.value }))} placeholder={f.placeholder} spellCheck={false} />
              <small className="count">{built.plan[f.key]?.length ?? 0} item(ns)</small>
            </label>
          ))}
          <div className="importfoot">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Título (opcional)" />
            <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Link de referência (opcional)" />
            <button className="primary" disabled={!built.total || !!built.problem} onClick={createFromPrompts}>Criar com {built.total || 0} prompt(s) <ChevronRight size={16} /></button>
          </div>
          {built.problem && <div className="callout warn"><AlertTriangle size={15} /><span>{built.problem}</span></div>}
        </div>
      )}
      {mode === 'link' && <div className="create">
        <div className="createicon"><Plus size={20} /></div>
        <div className="createcopy"><b>Começar uma nova produção</b><small>Link do TikTok ou caminho de um .mp4 no PC do worker.</small></div>
        <div className="createfields">
          <div className="createinput"><Link2 size={16} /><input value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://www.tiktok.com/@.../video/..." onKeyDown={(e) => e.key === 'Enter' && create()} /></div>
          <div className="createinput"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Título (opcional)" onKeyDown={(e) => e.key === 'Enter' && create()} />
            <button onClick={create} disabled={!source.trim()}>Criar projeto <ChevronRight size={16} /></button></div>
        </div>
        <div className="kindpick" role="radiogroup" aria-label="Como fazer o roteiro">
          <button role="radio" aria-checked={kind === 'inspired'} className={kind === 'inspired' ? 'on' : ''} onClick={() => setKind('inspired')}>
            <b>Inspirado</b><small>Entende a história e cria um roteiro novo, mudando algumas coisas.</small></button>
          <button role="radio" aria-checked={kind === 'clone'} className={kind === 'clone' ? 'on' : ''} onClick={() => setKind('clone')}>
            <b>Cópia fiel</b><small>Roteiro, cortes e câmera idênticos ao original; só troca o personagem (ex.: banana → morango).</small></button>
        </div>
      </div>}
      {!workerOnline && <div className="notice warn"><Monitor size={17} /><span>O worker não está online: as etapas ficam na fila até você iniciá-lo no computador principal (<code>worker\start-worker.ps1</code>).</span></div>}

      <div className="sectionhead"><div><h2>Produções</h2><small>{projects.length} projeto(s)</small></div></div>
      {projects.length === 0 ? <div className="empty">Nenhum projeto ainda. Cole um link acima para começar.</div> : (
        <div className="projectlayout">
          <div className="projectlist">
            {projects.map((p) => {
              const mine = allStages.filter((x) => x.project_id === p.id);
              const state = projectState(p, mine);
              const nowStage = mine.find((x) => x.status !== 'approved') ?? null;
              const agent = nowStage ? STAGE_META[nowStage.stage_key]?.agent : undefined;
              return (
                <button key={p.id} onClick={() => onSelect(p.id)} className={`project ${project?.id === p.id ? 'selected' : ''}`}>
                  <div className="projectthumb"><Film size={21} /></div>
                  <div className="projectmeta">
                    <b>{p.title}</b>{(p.part ?? 1) > 1 && <em className="partbadge">Parte {p.part}</em>}
                    <div className="seg mini" aria-hidden="true">{mine.map((x) => <i key={x.id} className={x.status} />)}</div>
                    <span className={`state ${state.tone}`}>{state.label}</span>
                    <small>{nowStage ? STAGE_TITLE(nowStage.stage_key) : 'Todas as etapas aprovadas'}{agent ? ` · ${AGENT_LABEL[agent]}` : ''} · {timeAgo(p.updated_at)}</small>
                  </div>
                  <ChevronRight size={17} />
                </button>
              );
            })}
          </div>
          {project && (
            <div className="detail">
              <div className="detailtop">
                {heroUrl && <img className="hero" src={heroUrl} alt="" />}
                <div className="herotext"><div className="eyebrow">{project.id.slice(0, 8).toUpperCase()}</div><h3>{project.title}</h3><small className="source">{(project.part ?? 1) > 1 ? `Parte ${project.part} · ` : ''}{project.mode === 'clone' ? 'Cópia fiel · ' : ''}{project.source_url ?? (project.parent_id ? 'continuação sem vídeo de referência' : '')}</small></div>
                <span className={`statuspill st-${projectState(project, stages).tone}`}>{projectState(project, stages).label}</span>
                {!confirmDelete
                  ? <button className="ghost danger" onClick={() => setConfirmDelete(true)} title="Excluir este projeto"><Trash2 size={14} /> Excluir</button>
                  : <span className="confirmdel"><small>Apagar “{project.title}” e todos os arquivos dele?</small>
                      <button className="ghost danger" onClick={removeProject} disabled={deleting}><Trash2 size={14} /> {deleting ? 'Excluindo…' : 'Sim, excluir'}</button>
                      <button className="ghost" onClick={() => setConfirmDelete(false)} disabled={deleting}>Cancelar</button></span>}
                {deleteError && <div className="callout warn" style={{ flexBasis: '100%' }}><AlertTriangle size={15} /><span>Não foi possível excluir: {deleteError}</span></div>}
              </div>
              {project.status === 'completed' && (
                <div className="sequel">
                  {!sequelOpen
                    ? <button className="primary" onClick={() => setSequelOpen(true)}><Plus size={16} /> Criar parte {(project.part ?? 1) + 1}</button>
                    : (
                      <div className="sequelform">
                        <b>Parte {(project.part ?? 1) + 1} da história</b>
                        <small>O Claude lê o roteiro e os personagens desta produção e escreve a continuação. Cada etapa continua parando para a sua aprovação.</small>
                        <div className="createinput"><Link2 size={16} /><input value={sequelSource} onChange={(e) => setSequelSource(e.target.value)} aria-label="Link da parte seguinte do original" placeholder="Link da parte seguinte do vídeo original (opcional)" /></div>
                        <small>{sequelSource.trim() ? 'Com link: o Claude analisa esse vídeo e continua a sua história seguindo o que acontece nele' + (project.mode === 'clone' ? ', em cópia fiel.' : '.') : 'Sem link: o Claude cria a continuação a partir da sua história e propõe três direções para você escolher.'}</small>
                        <textarea rows={3} value={sequelNotes} onChange={(e) => setSequelNotes(e.target.value)} aria-label="Ideias para a continuação" placeholder="Ideias para a continuação (opcional): o que você quer que aconteça, novos personagens, o tom…" />
                        <div className="sequelactions">
                          <button className="primary" onClick={makeSequel} disabled={sequelBusy}>{sequelBusy ? 'Criando…' : `Criar parte ${(project.part ?? 1) + 1}`} <ChevronRight size={16} /></button>
                          <button className="ghost" onClick={() => setSequelOpen(false)} disabled={sequelBusy}>Cancelar</button>
                        </div>
                        {sequelError && <div className="callout warn"><AlertTriangle size={15} /><span>{sequelError}</span></div>}
                      </div>
                    )}
                </div>
              )}
              <div className="stats">
                <div><small>Etapas aprovadas</small><b>{approved}<span>/{stages.length}</span></b></div>
                <div><small>Imagens e vídeos</small><b>{readyMedia}<span> prontos</span></b></div>
                <div><small>Agora</small><b className="nowline">{current ? current.title : '—'}</b>{current && STAGE_META[current.stage_key] && <span className={`agent ${STAGE_META[current.stage_key].agent}`}>{AGENT_LABEL[STAGE_META[current.stage_key].agent]}</span>}</div>
              </div>
              {!workerOnline && stages.some((x) => x.status === 'queued' || x.status === 'in_progress') && (
                <div className="callout warn"><Clock size={15} /><span>O worker está offline. Esta etapa começa quando o computador principal estiver ligado com o worker rodando.</span></div>
              )}
              <div className="progresshead"><b>Fluxo de produção</b><small>{approved} de {stages.length} etapas aprovadas</small></div>
              <div className="seg" aria-hidden="true">{stages.map((s) => <i key={s.id} className={s.status} />)}</div>
              <details className="fold" open>
              <summary><span>Etapas do fluxo</span><em>{approved}/{stages.length}</em><ChevronDown size={16} className="chev" /></summary>
              <div className="stages">
                {stages.map((s) => {
                  const meta = STAGE_META[s.stage_key];
                  const Icon = meta?.icon ?? FALLBACK_ICON;
                  return (
                    <button className={`stage ${s.status} ${stage?.id === s.id ? 'open' : ''}`} key={s.id} onClick={() => setStageId(s.id)}>
                      <div className="stageicon"><Icon size={17} /></div>
                      <div className="stagetext"><b>{String(s.stage_order).padStart(2, '0')} · {s.title}</b><small>{meta?.subtitle}</small></div>
                      <div className="stagestatus">{s.status === 'approved' && (s.payload?.skipped ? <SkipForward size={13} /> : <Check size={13} />)}{s.payload?.skipped ? 'Pulada' : STATUS_LABEL[s.status]}</div>
                    </button>
                  );
                })}
              </div>
              </details>
              {stage && <StagePanel key={stage.id} stage={stage} next={stages.filter((s) => s.stage_order > stage.stage_order && s.status === 'locked').sort((a, b) => a.stage_order - b.stage_order)[0] ?? null}
                artifacts={artifacts.filter((a) => a.stage_id === stage.id)} urls={urls} call={call} download={download} />}
            </div>
          )}
        </div>
      )}
    </>
  );
}

const IMPORT_FIELDS: { key: PlanKey; label: string; agent: string; hint: string; placeholder: string }[] = [
  { key: 'character_images', label: 'Imagens dos personagens', agent: 'ChatGPT', hint: 'Corpo inteiro de cada personagem.', placeholder: '## Anna\nFull-body character on a plain background, ...' },
  { key: 'character_sheets', label: 'Folhas 16:9 dos personagens', agent: 'ChatGPT', hint: 'Use Referências: para apontar a imagem do personagem.', placeholder: '## Folha da Anna\nReferências: Anna\n16:9 character sheet, ...' },
  { key: 'scene_images', label: 'Ambientes e cenas', agent: 'ChatGPT', hint: 'Imagem-base de cada cena; referencie personagens e ambientes pelo nome.', placeholder: '## Cozinha\nEmpty bright kitchen, ...\n\n## Cena 1\nReferências: Anna, Cozinha\nAnna at the table, ...' },
  { key: 'production', label: 'Vídeos', agent: 'Grok', hint: 'Um clipe por prompt, sempre em 720p. Imagem inicial: nome de uma imagem acima. Duração: 6, 10 ou 15 (o Python escolhe no Grok; sem isso usa o que o prompt cita, ou 6 s).', placeholder: '## Clipe 1\nImagem inicial: Cena 1\nAnna says "Hallo" ...' },
];

const STAGE_TITLES: Record<string, string> = {
  analysis: 'Análise da referência', script: 'Roteiro', character_bible: 'Personagens e vozes', character_images: 'Imagens dos personagens',
  character_sheets: 'Folhas dos personagens', visual_plan: 'Ambientes e cenas', scene_images: 'Imagens de cenas', video_prompts: 'Prompts de vídeo', production: 'Vídeos no Grok',
};
const STAGE_TITLE = (key: string) => STAGE_TITLES[key] ?? key;
const GENERATION_STAGES = ['character_images', 'character_sheets', 'scene_images', 'production'];

function StagePanel({ stage, next, artifacts, urls, call, download }: { stage: Stage; next: Stage | null; artifacts: Artifact[]; urls: Record<string, string>; call: Call; download: Download }) {
  const [open, setOpen] = useState<number | null>(null);
  const [comment, setComment] = useState('');
  const [asking, setAsking] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [busy, setBusy] = useState(false);
  const payload = stage.payload ?? {};
  const docs = artifacts.filter((a) => a.artifact_type === 'document').sort((a, b) => (a.item_key ?? '').localeCompare(b.item_key ?? ''));
  const media = artifacts.filter((a) => a.artifact_type !== 'document').sort((a, b) => (a.item_key ?? '').localeCompare(b.item_key ?? ''));
  const reviewing = stage.status === 'pending_review';
  const adjustable = GENERATION_STAGES.includes(stage.stage_key) && ['failed', 'pending_review', 'approved'].includes(stage.status);
  const [fixPrompt, setFixPrompt] = useState('');
  const failedItem = stage.status === 'failed' ? payload.error?.item : undefined;
  useEffect(() => { setFixPrompt(failedItem?.prompt ?? ''); }, [failedItem?.key, failedItem?.prompt]);
  const gallery = media.filter((m) => m.artifact_type === 'image' && m.storage_path);
  const generation = payload.next_generation;

  async function run(fn: string, args: Record<string, unknown>, ok: string) {
    setBusy(true);
    const done = await call(fn, args, ok);
    setBusy(false);
    if (done) { setComment(''); setAsking(false); setReopening(false); }
  }

  return (
    <div className="stagepanel">
      <div className="panelhead">
        <div><div className="eyebrow">ETAPA {String(stage.stage_order).padStart(2, '0')} · VERSÃO {stage.current_version}</div><h4>{stage.title}</h4></div>
        <span className={`statuspill s-${stage.status}`}>{payload.skipped ? 'Pulada' : STATUS_LABEL[stage.status]}</span>
      </div>
      {STAGE_META[stage.stage_key] && <div><span className={`agent ${STAGE_META[stage.stage_key].agent}`}>Feito por {AGENT_LABEL[STAGE_META[stage.stage_key].agent]}</span></div>}

      {stage.status === 'locked' && <p className="muted">Esta etapa é liberada quando a anterior for aprovada.</p>}
      {(stage.status === 'queued' || stage.status === 'in_progress' || stage.status === 'changes_requested') && (
        <p className="muted">{payload.progress ? `${payload.progress.label} (${payload.progress.done}/${payload.progress.total})` : stage.status === 'in_progress' ? 'O worker está trabalhando nesta etapa…' : 'Aguardando o worker.'}</p>
      )}
      {payload.progress && payload.progress.total > 0 && <div className="progress"><i style={{ width: `${(payload.progress.done / payload.progress.total) * 100}%` }} /></div>}
      {payload.summary && stage.status !== 'locked' && <p className="summary">{payload.summary}</p>}
      {payload.review_comment && stage.current_version > 1 && <p className="muted">Ajuste pedido nesta versão: “{payload.review_comment}”</p>}
      {(payload.warnings ?? []).map((w) => <div className="callout warn" key={w}><AlertTriangle size={15} /><span>{w}</span></div>)}
      {payload.error && (
        <div className="callout error">
          <MessageSquareWarning size={16} />
          <div><b>{payload.error.blocked ? 'Precisa de você' : 'Falhou'} · {payload.error.code}</b><span>{payload.error.message}</span>{payload.error.action && <span className="action">{payload.error.action}</span>}</div>
        </div>
      )}
      {failedItem && (
        <div className="fixitem">
          <b>Parou em: {failedItem.name}</b>
          <small>Se o prompt foi recusado ou não gera, ajuste o texto e tente de novo a partir deste item. Os itens já prontos são mantidos.</small>
          <textarea rows={6} value={fixPrompt} onChange={(e) => setFixPrompt(e.target.value)} spellCheck={false} aria-label={`Prompt de ${failedItem.name}`} />
          <button disabled={busy || !fixPrompt.trim()} onClick={() => run('adjust_item', { p_stage_id: stage.id, p_item_key: failedItem.key, p_prompt: fixPrompt.trim() === failedItem.prompt.trim() ? null : fixPrompt, p_comment: null }, 'Ajuste enviado; o worker refaz a partir deste item.')}>
            <RotateCcw size={16} /> Salvar prompt e tentar de novo</button>
        </div>
      )}

      {docs.length > 0 && (
        <details className="fold" open={reviewing}>
          <summary><span>Documentos</span><em>{docs.length}</em><ChevronDown size={16} className="chev" /></summary>
          {docs.map((doc, i) => (
            <details className="doc" key={doc.id} open={reviewing && i === 0}>
              <summary>{doc.name}<small>v{doc.version} · {timeAgo(doc.updated_at)}</small></summary>
              <Markdown text={doc.content.markdown ?? ''} />
            </details>
          ))}
        </details>
      )}

      {media.length > 0 && (
        <details className="fold" open={reviewing || stage.status === 'in_progress' || stage.status === 'failed'}>
          <summary><span>{media.every((m) => m.artifact_type === 'video') ? 'Vídeos' : media.every((m) => m.artifact_type === 'image') ? 'Imagens' : 'Arquivos'}</span><em>{media.length}</em><ChevronDown size={16} className="chev" /></summary>
          {adjustable && stage.status === 'approved' && <p className="muted">Quer mudar algo? Use <b>Ajustar</b> em qualquer item: só ele é refeito e a etapa volta para a sua revisão. Confira depois se as etapas seguintes ainda servem.</p>}
          <div className="mediagrid">
            {media.map((item) => <MediaCard key={item.id} item={item} stageId={stage.id} url={item.storage_path ? urls[`${item.storage_path}|${item.updated_at}`] : undefined}
              canRegenerate={adjustable && (item.artifact_type === 'image' || item.artifact_type === 'video') && !!item.item_key} call={call} download={download}
              onOpen={item.artifact_type === 'image' && item.storage_path ? () => setOpen(gallery.findIndex((g) => g.id === item.id)) : undefined} />)}
          </div>
        </details>
      )}

      {open !== null && gallery[open] && <Lightbox items={gallery} index={open} urls={urls} setIndex={setOpen} download={download} />}

      {stage.status === 'failed' && (
        <div className="approval"><div><b>Etapa parada</b><small>Resolva o que foi indicado acima; os itens já prontos são mantidos.</small></div>
          <button disabled={busy} onClick={() => run('retry_stage', { p_stage_id: stage.id }, 'Nova tentativa enfileirada.')}><RotateCcw size={16} /> Tentar novamente</button></div>
      )}
      {stage.status === 'approved' && (
        <div className="approval">
          <div>
            <b>Voltar para esta etapa</b>
            <small>{reopening
              ? 'A etapa volta para revisão com o conteúdo atual: ajuste o que precisar e aprove de novo. As etapas seguintes que já andaram são bloqueadas e refeitas depois da sua aprovação (seus prompts editados são mantidos).'
              : 'Precisa corrigir algo feito aqui? Volte a esta etapa sem perder o trabalho.'}</small>
          </div>
          <div className="approvalbtns">
            {reopening
              ? <><button className="ghost" onClick={() => setReopening(false)}>Cancelar</button>
                <button disabled={busy} onClick={() => run('reopen_stage', { p_stage_id: stage.id, p_reason: null }, 'Voltou para esta etapa; ajuste e aprove de novo.')}><RotateCcw size={16} /> Confirmar e voltar</button></>
              : <button className="ghost" disabled={busy} onClick={() => setReopening(true)}><RotateCcw size={16} /> Voltar para esta etapa</button>}
          </div>
        </div>
      )}
      {reviewing && (
        <div className="approval">
          <div>
            <b>Central de aprovação</b>
            <small>{payload.pilot ? `Este é só o clipe de teste. Aprovar autoriza o worker a gerar os outros ${payload.pilot_remaining ?? ''} clipes no Grok; se algo estiver errado, peça ajustes e só este clipe é refeito.` : generation && next
              ? `Aprovar autoriza o worker a gerar ${generation.count} ${generation.unit} no ${generation.service} (etapa “${next.title}”).`
              : next ? `Aprovar libera a etapa “${next.title}”.` : 'Aprovar conclui o projeto.'}</small>
            {asking && <textarea autoFocus value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Descreva o que deve mudar nesta etapa…" rows={3} />}
          </div>
          <div className="approvalbtns">
            {asking
              ? <><button className="ghost" onClick={() => setAsking(false)}>Cancelar</button>
                <button disabled={busy || !comment.trim()} onClick={() => run('decide_stage', { p_stage_id: stage.id, p_decision: 'request_changes', p_comment: comment }, 'Ajustes enviados; a etapa será refeita.')}>Enviar ajustes</button></>
              : <><button className="ghost" disabled={busy} onClick={() => setAsking(true)}>Pedir ajustes</button>
                <button disabled={busy} onClick={() => run('decide_stage', { p_stage_id: stage.id, p_decision: 'approve' }, 'Etapa aprovada.')}><Check size={16} /> {payload.pilot ? 'Aprovar teste e gerar o restante' : generation ? 'Aprovar e autorizar geração' : 'Aprovar etapa'}</button></>}
          </div>
        </div>
      )}
    </div>
  );
}

function MediaCard({ item, stageId, url, canRegenerate, call, download, onOpen }: { item: Artifact; stageId: string; url?: string; canRegenerate: boolean; call: Call; download: Download; onOpen?: () => void }) {
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState('');
  const original = item.content.prompt ?? '';
  const [prompt, setPrompt] = useState(original);
  useEffect(() => { if (!asking) setPrompt(original); }, [original, asking]);
  const size = item.content.width && item.content.height ? `${item.content.width}×${item.content.height}` : item.content.duration ? `${item.content.duration} s` : '';
  return (
    <figure className={`mediacard ${item.artifact_type}`}>
      {item.artifact_type === 'video'
        ? (url ? <video src={url} controls preload="metadata" /> : <div className="placeholder">{item.content.note ?? 'Prévia indisponível'}</div>)
        : (url ? <button type="button" className="imgbtn" onClick={onOpen} aria-label={`Ampliar ${item.name}`}><img src={url} alt={item.name} loading="lazy" /></button> : <div className="placeholder">Carregando prévia…</div>)}
      <figcaption>
        <b>{item.name}</b>
        <small>{[size, item.local_relative_path].filter(Boolean).join(' · ')}</small>
        {item.content.prompt && <details><summary>Prompt usado</summary><pre>{item.content.prompt}</pre></details>}
        {item.storage_path && <button className="ghost small" onClick={() => download(item.storage_path as string, item.name.replace(/[^\w.-]+/g, '_') + (item.artifact_type === 'video' ? '.mp4' : '.jpg'))}><Download size={13} /> Baixar prévia</button>}
        {canRegenerate && (asking ? (
          <div className="regen">
            {original && <textarea rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} spellCheck={false} aria-label={`Prompt de ${item.name}`} />}
            <input autoFocus value={comment} onChange={(e) => setComment(e.target.value)} placeholder="O que ajustar (opcional)" />
            <button onClick={async () => { if (await call('adjust_item', { p_stage_id: stageId, p_item_key: item.item_key, p_prompt: prompt.trim() && prompt.trim() !== original.trim() ? prompt : null, p_comment: comment.trim() || null }, `Ajuste enfileirado: ${item.name}.`)) { setAsking(false); setComment(''); } }}>Gerar de novo</button>
            <button className="ghost" onClick={() => setAsking(false)}>Cancelar</button>
          </div>
        ) : <button className="ghost small" onClick={() => setAsking(true)}><RotateCcw size={13} /> Ajustar</button>)}
      </figcaption>
    </figure>
  );
}

function Lightbox({ items, index, urls, setIndex, download }: { items: Artifact[]; index: number; urls: Record<string, string>; setIndex: (i: number | null) => void; download: Download }) {
  const item = items[index];
  const url = item.storage_path ? urls[`${item.storage_path}|${item.updated_at}`] : undefined;
  const go = useCallback((delta: number) => setIndex((index + delta + items.length) % items.length), [index, items.length, setIndex]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setIndex(null); else if (e.key === 'ArrowRight') go(1); else if (e.key === 'ArrowLeft') go(-1); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, setIndex]);
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={item.name} onClick={() => setIndex(null)}>
      <div className="lbbody" onClick={(e) => e.stopPropagation()}>
        <div className="lbimg">{url ? <img src={url} alt={item.name} /> : <div className="placeholder">Carregando…</div>}</div>
        <aside className="lbside">
          <div className="lbtop"><span>{index + 1} de {items.length}</span><button className="iconbtn" aria-label="Fechar" onClick={() => setIndex(null)}><X size={16} /></button></div>
          <b>{item.name}</b>
          <small>{[item.content.width && item.content.height ? `${item.content.width}×${item.content.height}` : '', item.local_relative_path].filter(Boolean).join(' · ')}</small>
          {item.content.prompt && <><div className="eyebrow">PROMPT USADO</div><pre>{item.content.prompt}</pre></>}
          <div className="lbbtns">
            <button className="ghost" onClick={() => go(-1)} disabled={items.length < 2}><ChevronLeft size={15} /> Anterior</button>
            <button className="ghost" onClick={() => go(1)} disabled={items.length < 2}>Próxima <ChevronRight size={15} /></button>
            {item.storage_path && <button className="primary" onClick={() => download(item.storage_path as string, item.name.replace(/[^\w.-]+/g, '_') + '.jpg')}><Download size={14} /> Baixar</button>}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ActivityView({ events, projects }: { events: EventRow[]; projects: Project[] }) {
  const titles = useMemo(() => Object.fromEntries(projects.map((p) => [p.id, p.title])), [projects]);
  const [shown, setShown] = useState<Record<string, number>>({});
  const groups = useMemo(() => {
    const map = new Map<string, EventRow[]>();
    for (const e of events) map.set(e.project_id, [...(map.get(e.project_id) ?? []), e]);
    return [...map.entries()];
  }, [events]);
  return (
    <>
      <div className="heading"><div><div className="eyebrow">REGISTRO</div><h1>Atividade</h1><p>O que o worker fez, onde parou e por quê.</p></div></div>
      {events.length === 0 ? <div className="empty">Nenhum evento registrado ainda.</div> : (
        <div className="eventgroups">
          {groups.map(([projectId, rows], index) => (
            <details className="fold" key={projectId} open={index === 0}>
              <summary><span>{titles[projectId] ?? 'Projeto'}</span><em>{rows.length}</em><ChevronDown size={16} className="chev" /></summary>
              <div className="eventlist">
                {rows.slice(0, shown[projectId] ?? 10).map((e) => (
                  <div className={`event ${e.level}`} key={e.id}>
                    <span className="dot">{(() => { const I = (e.stage_key && STAGE_META[e.stage_key]?.icon) || Activity; return <I size={14} />; })()}</span>
                    <div><b>{e.message}</b><small>{e.stage_key ? `${STAGE_TITLE(e.stage_key)} · ` : ''}{new Date(e.created_at).toLocaleString('pt-BR')}</small></div>
                  </div>
                ))}
                {rows.length > (shown[projectId] ?? 10) && <button className="ghost small" onClick={() => setShown((old) => ({ ...old, [projectId]: (old[projectId] ?? 10) + 20 }))}>Mostrar mais ({rows.length - (shown[projectId] ?? 10)})</button>}
              </div>
            </details>
          ))}
        </div>
      )}
    </>
  );
}

function IntegrationsView({ heartbeat, workerOnline }: { heartbeat: Heartbeat | null; workerOnline: boolean }) {
  return (
    <>
      <div className="heading"><div><div className="eyebrow">COMO FUNCIONA</div><h1>Integrações</h1><p>O Python conduz; o Claude analisa e escreve; o ChatGPT gera imagens e o Grok gera vídeos.</p></div></div>
      <div className="bottomgrid">
        <div className="miniinfo"><div className="miniicon"><Monitor size={18} /></div>
          <div><b>Worker no computador principal</b><small>{heartbeat ? `${heartbeat.worker_id} em ${heartbeat.hostname ?? '—'} · visto ${timeAgo(heartbeat.last_seen_at)}${heartbeat.details?.job?.stage_key ? ` · executando ${STAGE_TITLE(heartbeat.details.job.stage_key)}` : ''}` : 'Nunca conectou a este projeto Supabase.'}</small></div>
          <span className={`tag ${workerOnline ? 'ok' : 'muted'}`}>{workerOnline ? 'Online' : 'Offline'}</span></div>
        <div className="miniinfo"><div className="miniicon"><Cloud size={18} /></div>
          <div><b>Supabase</b><small>Login, fila de tarefas, aprovações, eventos e prévias. Os arquivos originais ficam no PC do worker.</small></div>
          <span className="tag ok">Conectado</span></div>
      </div>
      <div className="doc static">
        <Markdown text={[
          '### Quem faz o quê',
          '- **Claude** (Claude Code do PC do worker, com a sua assinatura): vê os quadros do vídeo, identifica gancho, CTA e estratégia, escreve o roteiro e cria os prompts de imagem e de vídeo.',
          '- **ChatGPT** (navegador): gera as imagens a partir dos prompts aprovados.',
          '- **Grok** (navegador): gera os vídeos a partir dos prompts aprovados.',
          '- **Python**: baixa e prepara o vídeo, entrega os pedidos, confere os formatos e guarda tudo.',
          '',
          '### No computador do worker',
          '0. `claude` — rode uma vez para fazer login no Claude Code.',
          '1. `python -m app.cli login` — abre o Chrome do worker; entre no ChatGPT e no Grok e feche a janela.',
          '2. `python -m app.cli check` — confere se as duas sessões estão ativas e se os seletores da interface ainda batem (não envia nada).',
          '3. `worker\\start-worker.ps1` — mantém o worker rodando.',
          '',
          '### Quando uma etapa para',
          '- **Login, verificação de segurança ou limite de uso:** o worker não tenta contornar. Resolva na janela do Chrome do worker (ou aguarde a cota) e clique em **Tentar novamente**; o que já foi gerado é mantido.',
          '- **A interface do site mudou:** ajuste `worker/selectors.json` com a ajuda do `check`, ou gere o item manualmente e salve o arquivo no caminho indicado no erro; ao tentar novamente o worker aceita o arquivo existente.',
          '- **Senhas, cookies e a chave secreta do Supabase nunca saem do computador do worker.**',
        ].join('\n')} />
      </div>
    </>
  );
}
