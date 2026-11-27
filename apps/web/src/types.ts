import { Clapperboard, FileText, Film, Image, LayoutGrid, Link2, Mic, Sparkles, UserRound, Video, type LucideIcon } from 'lucide-react';

export type StageStatus = 'locked' | 'queued' | 'in_progress' | 'pending_review' | 'approved' | 'changes_requested' | 'failed';

export type Project = { id: string; title: string; source_url: string | null; mode?: 'inspired' | 'clone' | 'story' | 'factory'; profile?: FactoryProfile | null; story_idea?: string | null; target_seconds?: number | null; min_scenes?: number | null; model?: string | null; effort?: string | null; parent_id?: string | null; part?: number; sequel_notes?: string | null; status: string; current_stage: string; updated_at: string; created_at: string };

export type FactoryProfile = { niche?: string; language?: string; style?: string; min_s?: number; max_s?: number; min_scenes?: number; references?: string[]; [key: string]: unknown };

export type StageItem = { key: string; name: string; prompt: string; kind?: string };
export type StageError = { code: string; message: string; action?: string; blocked?: boolean; item?: StageItem };
export type StagePayload = {
  summary?: string;
  skipped?: boolean;
  pilot?: boolean;
  pilot_remaining?: number;
  imported?: boolean;
  warnings?: string[];
  error?: StageError;
  review_comment?: string;
  story_cut?: { instruction?: string; target_s?: number; restore?: boolean };   // prompts de vídeo: história encurtada ou mudada
  local_dir?: string;
  progress?: { done: number; total: number; label: string };
  next_generation?: { service: string; count: number; unit: string };
  proposals?: { n: number; title: string; hook: string }[];   // Fábrica: as cinco propostas
  recommended?: number;
  chosen?: number;                                              // Fábrica: a história que você escolheu desenvolver
};
export type Stage = { id: string; project_id: string; stage_key: string; stage_order: number; title: string; status: StageStatus; current_version: number; payload: StagePayload };

export type Artifact = {
  id: string; project_id: string; stage_id: string | null; artifact_type: 'document' | 'image' | 'video' | 'evidence' | string;
  name: string; item_key: string | null; storage_path: string | null; local_relative_path: string | null; version: number;
  content: { markdown?: string; prompt?: string; width?: number; height?: number; duration?: number; note?: string; local?: boolean }; updated_at: string;
};

// Tarefa na fila ou rodando: mostra quais itens estão em ajuste neste momento.
export type ActiveJob = { id: string; stage_id: string | null; job_type: string; status: string; input: { item_key?: string; items?: { item_key: string }[] } };
export type EventRow = { id: number; project_id: string; stage_key: string | null; level: 'info' | 'warn' | 'error'; message: string; created_at: string };
export type MediaAccess = { worker_id: string; local_url: string | null; public_url: string | null; token: string };
export type Heartbeat = { worker_id: string; hostname: string | null; status: string; last_seen_at: string; details: { job?: { stage_key?: string } | null } };

export type Agent = 'claude' | 'chatgpt' | 'grok';
export const AGENT_LABEL: Record<Agent, string> = { claude: 'Claude', chatgpt: 'ChatGPT', grok: 'Grok' };
export const STAGE_META: Record<string, { icon: LucideIcon; subtitle: string; kind: 'texto' | 'geração'; agent: Agent }> = {
  analysis: { icon: Link2, subtitle: 'Gancho, CTA, estratégia e edição · documentos 01–02', kind: 'texto', agent: 'claude' },
  script: { icon: FileText, subtitle: 'Documento 03 · alemão com tradução', kind: 'texto', agent: 'claude' },
  character_bible: { icon: UserRound, subtitle: 'Documentos 04–06 · estilo, fichas e vozes', kind: 'texto', agent: 'claude' },
  character_images: { icon: Image, subtitle: 'Corpo inteiro de cada personagem', kind: 'geração', agent: 'chatgpt' },
  character_sheets: { icon: LayoutGrid, subtitle: 'Folhas 9:16 de cada personagem', kind: 'geração', agent: 'chatgpt' },
  visual_plan: { icon: Clapperboard, subtitle: 'Documento 07 · oito áreas e prompts', kind: 'texto', agent: 'claude' },
  scene_images: { icon: Image, subtitle: 'Ambientes e imagem-base de cada cena', kind: 'geração', agent: 'chatgpt' },
  video_prompts: { icon: Mic, subtitle: 'Documentos 08–10 · Grok, CapCut e publicação', kind: 'texto', agent: 'claude' },
  production: { icon: Video, subtitle: 'Um clipe por prompt aprovado · 9:16', kind: 'geração', agent: 'grok' },
  // Fábrica de roteiros + workflow de animação
  f_refs: { icon: Link2, subtitle: 'Fábrica · análise de até 5 referências e matriz de aprendizados', kind: 'texto', agent: 'claude' },
  f_proposals: { icon: Sparkles, subtitle: 'Fábrica · cinco histórias originais; você escolhe uma', kind: 'texto', agent: 'claude' },
  f_script: { icon: FileText, subtitle: 'Fábrica · roteiro completo com tempo de fala conferido', kind: 'texto', agent: 'claude' },
  f_bible: { icon: UserRound, subtitle: 'Fábrica · metadados, personagens, ambientes e continuidade', kind: 'texto', agent: 'claude' },
  f_workflow: { icon: Clapperboard, subtitle: 'Workflow · imagem de segundo zero, animação e diálogos por cena', kind: 'texto', agent: 'claude' },
};
export const FALLBACK_ICON = Film;

export const STATUS_LABEL: Record<StageStatus, string> = {
  locked: 'Bloqueada', queued: 'Na fila', in_progress: 'Em andamento', pending_review: 'Revisar',
  approved: 'Aprovada', changes_requested: 'Ajustes pedidos', failed: 'Parada',
};

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return 'agora';
  if (seconds < 3600) return `há ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `há ${Math.floor(seconds / 3600)} h`;
  return new Date(iso).toLocaleDateString('pt-BR');
}
