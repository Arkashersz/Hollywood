import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { Session, SupabaseClient } from '@supabase/supabase-js';
import {
  Activity, AlertTriangle, Check, ChevronRight, Clapperboard, Cloud, Film, FolderOpen, Link2, LogOut, MessageSquareWarning,
  Bell, ChevronDown, ChevronLeft, Clock, Download, Monitor, Plus, SkipForward, X, RefreshCw, RotateCcw, Settings2, ShieldCheck, Sparkles, Trash2,
} from 'lucide-react';
import { Markdown } from './Markdown';
import { buildPlan, type PlanKey } from './importPlan';
import { AGENT_LABEL, FALLBACK_ICON, STAGE_META, STATUS_LABEL, timeAgo, type Artifact, type EventRow, type Heartbeat, type MediaAccess, type Project, type Stage } from './types';

type View = 'projects' | 'activity' | 'integrations';
const BUCKET = 'artifacts';
const MODELS: { id: string; label: string }[] = [
  { id: '', label: 'Padrão do PC do worker' }, { id: 'claude-opus-5-5', label: 'Claude Opus 5.5' }, { id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' },
  { id: 'claude-haiku-5-5', label: 'Claude Haiku 5.5' }, { id: 'claude-fable-5-2', label: 'Claude Fable 5.2' },
];
const modelLabel = (id?: string | null) => MODELS.find((m) => m.id === (id ?? ''))?.label ?? id ?? MODELS[0].label;
// Nível de esforço do Claude (o mesmo do chat): mais alto pensa mais, demora mais e gasta mais cota.
const EFFORTS: { id: string; label: string }[] = [
  { id: '', label: 'Esforço padrão do PC' }, { id: 'low', label: 'Baixo' }, { id: 'medium', label: 'Médio' },
  { id: 'high', label: 'Alto' }, { id: 'xhigh', label: 'Extra alto' }, { id: 'max', label: 'Máximo' },
];
const effortLabel = (id?: string | null) => EFFORTS.find((e) => e.id === (id ?? ''))?.label ?? id ?? EFFORTS[0].label;
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
  const [media, setMedia] = useState<MediaAccess | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState('');
  const [live, setLive] = useState(false);
  useEffect(() => { if (!notice) return; const t = window.setTimeout(() => setNotice(''), 12000); return () => window.clearTimeout(t); }, [notice]);
  const selectedRef = useRef<string | null>(null);
  const signedAt = useRef<Record<string, number>>({});
  selectedRef.current = selectedId;

  const refresh = useCallback(async () => {
    const [p, h, all, m] = await Promise.all([
      db.from('projects').select('*').order('updated_at', { ascending: false }),
      db.from('worker_heartbeats').select('*').order('last_seen_at', { ascending: false }).limit(1),
      db.from('project_stages').select('*').order('stage_order'),
      db.from('worker_media').select('*').limit(1),
    ]);
    if (p.error) { setNotice(`Sem acesso ao Supabase: ${p.error.message}`); return; }
    const list = (p.data ?? []) as Project[];
    setProjects(list);
    setAllStages((all.data ?? []) as Stage[]);
    setHeartbeat(((h.data ?? [])[0] as Heartbeat) ?? null);
    setMedia(m.error ? null : ((m.data ?? [])[0] as MediaAccess) ?? null);   // endereço e senha do servidor de vídeos do PC
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
    const links = source ? source.split('\n').length : 0;
    setNotice(links > 1 ? `Continuação criada. O Claude analisa os ${links} vídeos e junta tudo numa história só assim que o worker estiver online.` : source ? 'Continuação criada. O Claude analisa a parte 2 do original assim que o worker estiver online.' :'Continuação criada. O Claude resume a história e propõe direções assim que o worker estiver online.');
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
    <MediaContext.Provider value={{ access: media, online: workerOnline, reload: refresh }}>
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
    </MediaContext.Provider>
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
  const [mode, setMode] = useState<'factory' | 'link' | 'story' | 'prompts'>('factory');
  const [fx, setFx] = useState<FactoryForm>(FACTORY_DEFAULTS);
  const [idea, setIdea] = useState('');
  const [seconds, setSeconds] = useState(60);
  const [minScenes, setMinScenes] = useState(8);
  const [kind, setKind] = useState<'inspired' | 'clone'>('inspired');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [texts, setTexts] = useState<Record<PlanKey, string>>({ character_images: '', character_sheets: '', scene_images: '', production: '' });
  const built = useMemo(() => buildPlan(texts), [texts]);
  const [stageId, setStageId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [sequelOpen, setSequelOpen] = useState(false);
  const [sequelSources, setSequelSources] = useState<string[]>(['']);   // um link por vídeo, na ordem da história
  const sequelLinks = sequelSources.map((s) => s.trim()).filter(Boolean);
  const [sequelNotes, setSequelNotes] = useState('');
  const [sequelBusy, setSequelBusy] = useState(false);
  const [sequelError, setSequelError] = useState('');
  const approved = stages.filter((s) => s.status === 'approved').length;
  const current = stages.find((s) => s.status !== 'approved') ?? stages[stages.length - 1] ?? null;
  const stage = stages.find((s) => s.id === stageId) ?? current;
  const readyMedia = artifacts.filter((a) => a.artifact_type === 'image' || a.artifact_type === 'video').length;
  const heroItem = artifacts.find((a) => a.artifact_type === 'image' && a.storage_path);
  const heroUrl = heroItem?.storage_path ? urls[`${heroItem.storage_path}|${heroItem.updated_at}`] : undefined;

  useEffect(() => { setStageId(null); setConfirmDelete(false); setDeleteError(''); setSequelOpen(false); setSequelSources(['']); setSequelNotes(''); setSequelError(''); }, [project?.id]);

  async function makeSequel() {
    if (!project) return;
    setSequelBusy(true); setSequelError('');
    const error = await createSequel(project.id, sequelLinks.join('\n'), '', sequelNotes.trim());
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
    if (await call('create_project', { p_title: title, p_source: source, p_mode: kind, p_model: model || null, p_effort: effort || null }, kind === 'clone' ? 'Projeto criado (cópia fiel). A análise começa assim que o worker estiver online.' : 'Projeto criado. A análise começa assim que o worker estiver online.')) { setSource(''); setTitle(''); }
  }

  const storyProblem = !idea.trim() ? '' : seconds < 15 || seconds > 300 ? 'A duração precisa ficar entre 15 e 300 segundos.'
    : minScenes < 1 || minScenes > 60 ? 'A quantidade mínima de cenas precisa ficar entre 1 e 60.'
    : minScenes * 6 > seconds + 10 ? `${minScenes} cenas de no mínimo 6 s passam de ${seconds} s. Aumente a duração ou reduza as cenas.` : '';
  const factoryRefs = useMemo(() => fx.references.split(/\r?\n/).map((r) => r.trim()).filter(Boolean), [fx.references]);
  const factoryProblem = fx.min_s < 15 || fx.min_s > 240 ? 'A duração mínima precisa ficar entre 15 e 240 segundos.'
    : fx.max_s < fx.min_s || fx.max_s > 300 ? 'A duração máxima precisa ficar entre a mínima e 300 segundos.'
    : fx.min_scenes < 1 || fx.min_scenes > 60 ? 'A quantidade mínima de cenas precisa ficar entre 1 e 60.'
    : fx.scene_min_s < 1 || fx.scene_max_s < fx.scene_min_s || fx.scene_max_s > 10 ? 'A duração por cena precisa ficar entre 1 e 10 segundos (limite de 10 s por clipe).'
    : fx.min_scenes * fx.scene_min_s > fx.max_s ? `${fx.min_scenes} cenas de no mínimo ${fx.scene_min_s} s somam ${fx.min_scenes * fx.scene_min_s} s, mais do que o máximo de ${fx.max_s} s. Reduza as cenas ou aumente a duração.`
    : factoryRefs.length > 5 ? 'Informe no máximo 5 referências.' : '';
  async function createFactory() {
    if (factoryProblem) return;
    if (await call('create_factory_project', { p_title: title, p_profile: { ...fx, references: factoryRefs }, p_model: model || null, p_effort: effort || null },
      'Projeto criado na Fábrica de roteiros. A análise das referências começa assim que o worker estiver online; cada etapa para na sua aprovação.')) { setTitle(''); setFx((old) => ({ ...old, references: '' })); }
  }

  async function createStory() {
    if (!idea.trim() || storyProblem) return;
    if (await call('create_story_project', { p_title: title, p_idea: idea, p_seconds: seconds, p_min_scenes: minScenes, p_model: model || null, p_effort: effort || null },
      'História criada. O briefing começa assim que o worker estiver online; cada etapa para na sua aprovação.')) { setIdea(''); setTitle(''); }
  }

  async function createFromPrompts() {
    if (!built.total || built.problem) return;
    if (await call('create_project_from_plan', { p_title: title, p_source: source, p_plan: built.plan, p_model: model || null, p_effort: effort || null },
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
        <button role="tab" aria-selected={mode === 'factory'} className={mode === 'factory' ? 'on' : ''} onClick={() => setMode('factory')}><Clapperboard size={15} /> Fábrica de roteiros</button>
        <button role="tab" aria-selected={mode === 'link'} className={mode === 'link' ? 'on' : ''} onClick={() => setMode('link')}><Link2 size={15} /> A partir de um vídeo do TikTok</button>
        <button role="tab" aria-selected={mode === 'story'} className={mode === 'story' ? 'on' : ''} onClick={() => setMode('story')}><Sparkles size={15} /> Tenho uma ideia de história</button>
        <button role="tab" aria-selected={mode === 'prompts'} className={mode === 'prompts' ? 'on' : ''} onClick={() => setMode('prompts')}><SkipForward size={15} /> Já tenho os prompts</button>
      </div>
      <div className="modelpick">
        <span><b>Modelo e esforço do Claude neste projeto</b><small>Escrevem análise, roteiro, personagens e prompts. Esforço mais alto pensa mais, demora mais e gasta mais cota. Imagens e vídeos seguem ChatGPT e Grok.</small></span>
        <div className="modelselects">
          <select aria-label="Modelo do Claude" value={model} onChange={(e) => setModel(e.target.value)}>{MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select>
          <select aria-label="Nível de esforço" value={effort} onChange={(e) => setEffort(e.target.value)}>{EFFORTS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select>
        </div>
      </div>
      {mode === 'factory' && (
        <div className="importbox factorybox">
          <div className="importintro">
            <b>Fábrica de roteiros → workflow de animação · tudo em 9:16</b>
            <small>Você define o perfil e, se quiser, até 5 vídeos de referência. O Claude analisa e compara as referências, escreve cinco histórias originais (você escolhe uma), o roteiro completo e a bíblia de personagens e ambientes (a Fábrica termina aqui). No workflow de animação, cada cena sai com a imagem de segundo zero, a animação e câmera e os diálogos. Depois o ChatGPT gera as imagens e o Grok os vídeos de até 10 s. Cada etapa para na sua aprovação.</small>
          </div>
          <div className="factorygrid">
            <label><b>Nicho</b><input value={fx.niche} onChange={(e) => setFx({ ...fx, niche: e.target.value })} /></label>
            <label><b>Idioma das falas</b><select value={fx.language} onChange={(e) => setFx({ ...fx, language: e.target.value })}>{LANGUAGE_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label className="wide"><b>Público-alvo</b><input value={fx.audience} onChange={(e) => setFx({ ...fx, audience: e.target.value })} /></label>
            <label><b>Estilo visual</b><select value={fx.style} onChange={(e) => setFx({ ...fx, style: e.target.value })}>{STYLE_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            {fx.style === 'custom' && <label><b>Descreva o estilo</b><input value={fx.style_custom} onChange={(e) => setFx({ ...fx, style_custom: e.target.value })} placeholder="Ex.: neon noir, luz azul, chuva" /></label>}
            <label><b>Áudio</b><select value={fx.audio} onChange={(e) => setFx({ ...fx, audio: e.target.value })}><option value="dialogos">Diálogos</option><option value="narracao">Narração</option><option value="ambos">Narração e diálogos</option></select></label>
          </div>
          <div className="storynums">
            <label><b>Duração total mín. (s)</b><input type="number" min={15} max={240} value={fx.min_s} onChange={(e) => setFx({ ...fx, min_s: Number(e.target.value) })} /></label>
            <label><b>máx. (s)</b><input type="number" min={15} max={300} value={fx.max_s} onChange={(e) => setFx({ ...fx, max_s: Number(e.target.value) })} /></label>
            <label><b>Mínimo de cenas</b><input type="number" min={1} max={60} value={fx.min_scenes} onChange={(e) => setFx({ ...fx, min_scenes: Number(e.target.value) })} /></label>
            <label><b>Cena mín. (s)</b><input type="number" min={1} max={10} value={fx.scene_min_s} onChange={(e) => setFx({ ...fx, scene_min_s: Number(e.target.value) })} /></label>
            <label><b>Cena máx. (s)</b><input type="number" min={1} max={10} value={fx.scene_max_s} onChange={(e) => setFx({ ...fx, scene_max_s: Number(e.target.value) })} /></label>
            <small>Cada cena vira um clipe de no máximo 10 s (o Grok gera 6 ou 10 s; o excesso se corta na edição). A fala de cada cena é conferida contra o tempo dela.</small>
          </div>
          <label className="importfield">
            <span><b>Referências (até 5)</b><small>Um link do TikTok ou caminho de .mp4 no PC do worker por linha. Opcional: sem referências, as propostas partem do perfil. Com menos de cinco, o painel avisa e você decide se segue.</small></span>
            <textarea rows={4} value={fx.references} onChange={(e) => setFx({ ...fx, references: e.target.value })} spellCheck={false} placeholder={'https://www.tiktok.com/@.../video/...\nC:\\videos\\referencia2.mp4'} />
            <small className="count">{factoryRefs.length} de 5 referência(s)</small>
          </label>
          <label className="importfield">
            <span><b>Restrições</b><small>Figurino, personagens, temas e linguagem que o roteiro deve respeitar. Opcional.</small></span>
            <textarea rows={2} value={fx.restrictions} onChange={(e) => setFx({ ...fx, restrictions: e.target.value })} maxLength={2000} />
          </label>
          <div className="importfoot storyfoot">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Título (opcional)" />
            <button className="primary" disabled={!!factoryProblem} onClick={createFactory}>Criar na Fábrica <ChevronRight size={16} /></button>
          </div>
          {factoryProblem && <div className="callout warn"><AlertTriangle size={15} /><span>{factoryProblem}</span></div>}
        </div>
      )}
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
      {mode === 'story' && (
        <div className="importbox storybox">
          <div className="importintro">
            <b>Conte a ideia; o Claude escreve o roteiro</b>
            <small>O roteiro sai em alemão, com gancho de 5 s impactantes no início e final aberto para a parte 2 (o mesmo padrão dos outros projetos). Você define a duração do vídeo e quantas cenas, no mínimo, ele terá.</small>
          </div>
          <label className="importfield">
            <span><b>Ideia da história</b><small>Quanto mais detalhe (personagens, local, conflito, tom), mais fiel o roteiro. Pode ser só uma frase.</small></span>
            <textarea rows={6} value={idea} onChange={(e) => setIdea(e.target.value)} maxLength={6000} placeholder="Ex.: Uma menina descobre que o espelho do avô mostra o que vai acontecer daqui a uma hora. Hoje ele mostra ela mesma desaparecendo." />
          </label>
          <div className="storynums">
            <label><b>Duração do vídeo (s)</b><input type="number" min={15} max={300} step={5} value={seconds} onChange={(e) => setSeconds(Number(e.target.value))} /></label>
            <label><b>Mínimo de cenas</b><input type="number" min={1} max={60} value={minScenes} onChange={(e) => setMinScenes(Number(e.target.value))} /></label>
            <small>Cada cena vira um clipe de 6, 10 ou 15 s no Grok, então a soma pode variar uns segundos para cima.</small>
          </div>
          <div className="importfoot storyfoot">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Título (opcional)" />
            <button className="primary" disabled={!idea.trim() || !!storyProblem} onClick={createStory}>Criar história <ChevronRight size={16} /></button>
          </div>
          {storyProblem && <div className="callout warn"><AlertTriangle size={15} /><span>{storyProblem}</span></div>}
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
      {projects.length === 0 ? <div className="empty">Nenhum projeto ainda. Cole um link ou conte uma ideia acima para começar.</div> : (
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
                <div className="herotext"><div className="eyebrow">{project.id.slice(0, 8).toUpperCase()}</div><h3>{project.title}</h3><small className="source">{(project.part ?? 1) > 1 ? `Parte ${project.part} · ` : ''}{project.mode === 'clone' ? 'Cópia fiel · ' : ''}{project.mode === 'factory' ? `Fábrica · ${project.profile?.niche ?? ''} · ${project.profile?.min_s ?? '?'}–${project.profile?.max_s ?? '?'} s · mín. ${project.profile?.min_scenes ?? '?'} cenas · 9:16` : project.mode === 'story' ? `Ideia própria · ${project.target_seconds ?? '?'} s · mín. ${project.min_scenes ?? '?'} cenas` : (project.source_url?.split('\n').join(' + ') ?? (project.parent_id ?'continuação sem vídeo de referência' : ''))}</small>
                  <label className="modelinline">Modelo: <select value={project.model ?? ''} onChange={(e) => call('set_project_model', { p_project_id: project.id, p_model: e.target.value || null }, `Modelo do projeto: ${modelLabel(e.target.value)} (vale a partir da próxima tarefa).`)}>
                    {MODELS.some((m) => m.id === (project.model ?? '')) ? null : <option value={project.model ?? ''}>{modelLabel(project.model)}</option>}
                    {MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select>
                    <select aria-label="Nível de esforço do projeto" value={project.effort ?? ''} onChange={(e) => call('set_project_effort', { p_project_id: project.id, p_effort: e.target.value || null }, `Esforço do projeto: ${effortLabel(e.target.value)} (vale a partir da próxima tarefa).`)}>
                      {EFFORTS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label></div>
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
                  <button className="ghost" onClick={() => call('open_project_folder', { p_project_id: project.id }, 'Abrindo a pasta no PC do worker…')} title="Abre a pasta com os vídeos deste projeto no computador onde o worker roda"><FolderOpen size={16} /> Abrir pasta dos vídeos</button>
                  {!sequelOpen
                    ? <button className="primary" onClick={() => setSequelOpen(true)}><Plus size={16} /> Criar parte {(project.part ?? 1) + 1}</button>
                    : (
                      <div className="sequelform">
                        <b>Parte {(project.part ?? 1) + 1} da história</b>
                        <small>O Claude lê o roteiro e os personagens desta produção e escreve a continuação. Cada etapa continua parando para a sua aprovação.</small>
                        {sequelSources.map((value, i) => (
                          <div className="sequellink" key={i}>
                            <div className="createinput"><Link2 size={16} /><input value={value} onChange={(e) => setSequelSources((old) => old.map((x, n) => (n === i ? e.target.value : x)))}
                              aria-label={i === 0 ? 'Link da parte seguinte do original' : `Link do vídeo ${i + 1}`}
                              placeholder={i === 0 ? 'Link da parte seguinte do vídeo original (opcional)' : `Link do vídeo ${i + 1}, o que vem depois na história`} /></div>
                            {sequelSources.length > 1 && <button className="ghost" onClick={() => setSequelSources((old) => old.filter((_, n) => n !== i))} disabled={sequelBusy} aria-label={`Remover o vídeo ${i + 1}`} title="Remover este vídeo"><Trash2 size={14} /></button>}
                          </div>
                        ))}
                        {sequelSources.length < MAX_SEQUEL_LINKS && <button className="ghost small" onClick={() => setSequelSources((old) => [...old, ''])} disabled={sequelBusy}><Plus size={14} /> Adicionar outro vídeo</button>}
                        <small>{sequelLinks.length > 1 ? `Com ${sequelLinks.length} links: o Claude analisa os ${sequelLinks.length} vídeos, na ordem acima, e junta tudo numa única parte ${(project.part ?? 1) + 1}, com a história em sequência num só vídeo` + (project.mode === 'clone' ? ', em cópia fiel.' : '.')
                          : sequelLinks.length ? 'Com link: o Claude analisa esse vídeo e continua a sua história seguindo o que acontece nele' + (project.mode === 'clone' ? ', em cópia fiel.' : '.') : 'Sem link: o Claude cria a continuação a partir da sua história e propõe três direções para você escolher.'}</small>
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

const MAX_SEQUEL_LINKS = 5;

type FactoryForm = { niche: string; language: string; audience: string; style: string; style_custom: string; min_s: number; max_s: number; min_scenes: number; scene_min_s: number; scene_max_s: number; audio: string; restrictions: string; references: string };
const FACTORY_DEFAULTS: FactoryForm = { niche: 'Frutinhas animadas (novela das frutas)', language: 'pt-BR', audience: 'Público brasileiro de TikTok, todas as idades', style: '3d', style_custom: '', min_s: 90, max_s: 120, min_scenes: 12, scene_min_s: 6, scene_max_s: 10, audio: 'dialogos', restrictions: '', references: '' };
const LANGUAGE_OPTIONS: [string, string][] = [['pt-BR', 'Português do Brasil'], ['en', 'Inglês'], ['es', 'Espanhol'], ['de', 'Alemão']];
const STYLE_OPTIONS: [string, string][] = [['3d', '3D cinematográfico estilizado'], ['clay', 'Claymation / massinha'], ['anime', 'Anime cinematográfico'], ['cartoon2d', 'Cartoon 2D moderno'], ['cel3d', 'Cel shading 3D'],
  ['storybook', 'Livro ilustrado / storybook'], ['watercolor', 'Aquarela'], ['comic', 'Comic / graphic novel'], ['lowpoly', 'Low-poly 3D'], ['toy', 'Miniaturas / toy world'], ['retro', 'Retro CGI'],
  ['fantasy_real', 'Fotorealismo fantástico'], ['custom', 'Outro (descrever)']];

const IMPORT_FIELDS: { key: PlanKey; label: string; agent: string; hint: string; placeholder: string }[] = [
  { key: 'character_images', label: 'Imagens dos personagens', agent: 'ChatGPT', hint: 'Corpo inteiro de cada personagem.', placeholder: '## Anna\nFull-body character on a plain background, ...' },
  { key: 'character_sheets', label: 'Folhas 9:16 dos personagens', agent: 'ChatGPT', hint: 'Use Referências: para apontar a imagem do personagem.', placeholder: '## Folha da Anna\nReferências: Anna\n16:9 character sheet, ...' },
  { key: 'scene_images', label: 'Ambientes e cenas', agent: 'ChatGPT', hint: 'Imagem-base de cada cena; referencie personagens e ambientes pelo nome.', placeholder: '## Cozinha\nEmpty bright kitchen, ...\n\n## Cena 1\nReferências: Anna, Cozinha\nAnna at the table, ...' },
  { key: 'production', label: 'Vídeos', agent: 'Grok', hint: 'Um clipe por prompt, sempre em 720p. Imagem inicial: nome de uma imagem acima. Duração: 6, 10 ou 15 (o Python escolhe no Grok; sem isso usa o que o prompt cita, ou 6 s).', placeholder: '## Clipe 1\nImagem inicial: Cena 1\nAnna says "Hallo" ...' },
];

const STAGE_TITLES: Record<string, string> = {
  analysis: 'Análise da referência', script: 'Roteiro', character_bible: 'Personagens e vozes', character_images: 'Imagens dos personagens',
  character_sheets: 'Folhas dos personagens', f_refs: 'Referências e aprendizados', f_proposals: 'Cinco propostas', f_script: 'Roteiro completo', f_bible: 'Personagens e ambientes',
  f_workflow: 'Workflow de animação', visual_plan: 'Ambientes e cenas', scene_images: 'Imagens de cenas', video_prompts: 'Prompts de vídeo', production: 'Vídeos no Grok',
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
  const [selecting, setSelecting] = useState(false);
  const [notes, setNotes] = useState<Record<string, string>>({});   // item_key -> observação própria (a chave existir = item selecionado)
  const [bulk, setBulk] = useState('');
  const regenerable = media.filter((m) => (m.artifact_type === 'image' || m.artifact_type === 'video') && !!m.item_key);
  const chosen = regenerable.filter((m) => (m.item_key as string) in notes);
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

      {stage.stage_key === 'f_proposals' && reviewing && !!payload.proposals?.length && (
        <div className="proposals">
          <b>Qual história desenvolver?</b>
          <small>Leia as cinco no documento abaixo e escolha. Sem escolha, o roteiro segue a recomendada (★). Depois aprove a etapa.</small>
          <div className="proposalbtns">
            {payload.proposals.map((p) => (
              <button key={p.n} disabled={busy} className={`ghost ${(payload.chosen ?? payload.recommended) === p.n ? 'on' : ''}`} aria-pressed={(payload.chosen ?? payload.recommended) === p.n} title={p.hook}
                onClick={() => run('choose_proposal', { p_stage_id: stage.id, p_number: p.n }, `História ${p.n} escolhida; aprove a etapa para desenvolvê-la.`)}>
                <b>{p.n}{p.n === payload.recommended ? ' ★' : ''}</b> {p.title}</button>
            ))}
          </div>
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
          {adjustable && regenerable.length > 1 && (
            <div className="selbar">
              {selecting
                ? <>
                    <span><b>{chosen.length}</b> selecionado(s)</span>
                    <button className="ghost small" onClick={() => setNotes(Object.fromEntries(regenerable.map((m) => [m.item_key as string, notes[m.item_key as string] ?? ''])))}>Selecionar todos</button>
                    <button className="ghost small" onClick={() => setNotes({})}>Limpar</button>
                    <button className="ghost small" onClick={() => { setSelecting(false); setNotes({}); setBulk(''); }}>Sair da seleção</button>
                  </>
                : <button className="ghost small" onClick={() => setSelecting(true)}><Check size={13} /> Selecionar vários para ajustar</button>}
            </div>
          )}
          <div className="mediagrid">
            {media.map((item) => {
              const k = item.item_key ?? '';
              const can = adjustable && (item.artifact_type === 'image' || item.artifact_type === 'video') && !!item.item_key;
              return <MediaCard key={item.id} item={item} stageId={stage.id} url={item.storage_path ? urls[`${item.storage_path}|${item.updated_at}`] : undefined}
                canRegenerate={can} call={call} download={download}
                select={selecting && can ? { on: k in notes, note: notes[k] ?? '', toggle: () => setNotes((old) => { const n = { ...old }; if (k in n) delete n[k]; else n[k] = ''; return n; }), setNote: (v) => setNotes((old) => ({ ...old, [k]: v })) } : undefined}
                onOpen={item.artifact_type === 'image' && item.storage_path ? () => setOpen(gallery.findIndex((g) => g.id === item.id)) : undefined} />;
            })}
          </div>
          {selecting && chosen.length > 0 && (
            <div className="bulkbar">
              <textarea rows={2} value={bulk} onChange={(e) => setBulk(e.target.value)} placeholder={`O que corrigir nos ${chosen.length} itens (vale para todos; use o campo de cada imagem para algo específico)…`} />
              <button disabled={busy} onClick={async () => {
                setBusy(true);
                const ok = await call('adjust_items', { p_stage_id: stage.id, p_items: chosen.map((m) => ({ key: m.item_key, comment: [bulk.trim(), notes[m.item_key as string]?.trim()].filter(Boolean).join('\n') || null })) }, `Ajuste enfileirado para ${chosen.length} itens.`);
                setBusy(false);
                if (ok) { setSelecting(false); setNotes({}); setBulk(''); }
              }}><RotateCcw size={15} /> Gerar {chosen.length} de novo</button>
            </div>
          )}
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

type Select = { on: boolean; note: string; toggle: () => void; setNote: (v: string) => void };

function MediaCard({ item, stageId, url, canRegenerate, call, download, onOpen, select }: { item: Artifact; stageId: string; url?: string; canRegenerate: boolean; call: Call; download: Download; onOpen?: () => void; select?: Select }) {
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState('');
  const original = item.content.prompt ?? '';
  const [prompt, setPrompt] = useState(original);
  useEffect(() => { if (!asking) setPrompt(original); }, [original, asking]);
  const { access } = useContext(MediaContext);
  const localUrl = localVideoUrl(item, access);
  const size = item.content.width && item.content.height ? `${item.content.width}×${item.content.height}` : item.content.duration ? `${item.content.duration} s` : '';
  return (
    <figure className={`mediacard ${item.artifact_type}${select?.on ? ' picked' : ''}`}>
      {select && <label className="pick"><input type="checkbox" checked={select.on} onChange={select.toggle} /> {select.on ? 'Selecionado' : 'Selecionar'}</label>}
      {item.artifact_type === 'video'
        ? (item.content.local ? <LocalVideo item={item} /> : url ? <video src={url} controls playsInline preload="metadata" /> : <div className="placeholder">{item.content.note ?? 'Prévia indisponível'}</div>)
        : (url ? <button type="button" className="imgbtn" onClick={onOpen} aria-label={`Ampliar ${item.name}`}><img src={url} alt={item.name} loading="lazy" /></button> : <div className="placeholder">Carregando prévia…</div>)}
      <figcaption>
        <b>{item.name}</b>
        <small>{[size, item.local_relative_path].filter(Boolean).join(' · ')}</small>
        {item.content.prompt && <details><summary>Prompt usado</summary><pre>{item.content.prompt}</pre></details>}
        {localUrl ? <a className="ghost small" href={localUrl} download={item.name.replace(/[^\w.-]+/g, '_') + '.mp4'}><Download size={13} /> Baixar vídeo</a>
          : item.storage_path && <button className="ghost small" onClick={() => download(item.storage_path as string, item.name.replace(/[^\w.-]+/g, '_') + (item.artifact_type === 'video' ? '.mp4' : '.jpg'))}><Download size={13} /> Baixar prévia</button>}
        {select?.on && <input className="picknote" value={select.note} onChange={(e) => select.setNote(e.target.value)} placeholder="O que está errado nesta (opcional)" aria-label={`Observação para ${item.name}`} />}
        {canRegenerate && !select && (asking ? (
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

// Vídeos não vão ao Supabase: ficam na pasta do projeto, no PC do worker. O painel os busca lá, quando você dá play:
// no próprio PC pelo endereço local; fora de casa (celular, site, aplicativo) pelo túnel que o worker abre.
// O endereço e a senha vêm da tabela worker_media, que só o dono lê.
type MediaCtx = { access: MediaAccess | null; online: boolean; reload: () => void };
const MediaContext = createContext<MediaCtx>({ access: null, online: false, reload: () => {} });
const SAME_PC = ['127.0.0.1', 'localhost', '[::1]'].includes(window.location.hostname);

function localVideoUrl(item: Artifact, access: MediaAccess | null): string | undefined {
  if (item.artifact_type !== 'video' || !item.content.local || !item.local_relative_path || !access) return undefined;
  const base = (SAME_PC ? access.local_url ?? access.public_url : access.public_url)?.replace(/\/$/, '');
  if (!base) return undefined;
  // `v` muda a cada vez que o worker regrava o vídeo (ajuste): o arquivo tem o mesmo nome, e sem isso o player
  // e o navegador continuariam mostrando o vídeo antigo até um F5.
  return `${base}/v/${item.project_id}/${item.local_relative_path.split('/').map(encodeURIComponent).join('/')}?k=${encodeURIComponent(access.token)}&v=${encodeURIComponent(item.updated_at)}`;
}

function LocalVideo({ item }: { item: Artifact }) {
  const { access, online, reload } = useContext(MediaContext);
  const [failed, setFailed] = useState(false);
  const [force, setForce] = useState(false);
  const src = localVideoUrl(item, access);
  useEffect(() => { setFailed(false); }, [src]);
  if (src && (online || force) && !failed) {
    return <video key={src} src={src} controls playsInline preload="metadata" onError={() => { setFailed(true); reload(); }} />;
  }
  const retry = () => { setForce(true); setFailed(false); reload(); };
  let why: string;
  if (!access) why = 'O vídeo está só no computador. O painel ainda não recebeu o endereço dele: ligue o worker (e aplique a migração 0018 no Supabase, se for a primeira vez).';
  else if (!online && !force) why = 'O computador está desligado ou o worker parado. O vídeo está só nele: ligue o worker para assistir daqui.';
  else if (!src) why = 'O vídeo está no computador, mas o acesso de fora de casa não está ligado. No PC, instale o cloudflared (winget install Cloudflare.cloudflared) e reinicie o worker.';
  else why = 'Não consegui abrir o vídeo no computador (worker desligado, sem internet ou túnel reiniciando). Tente de novo em instantes.';
  return <div className="placeholder videoerr" role="status"><span>{why}</span><button className="ghost small" onClick={retry}><RefreshCw size={13} /> Tentar de novo</button></div>;
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
