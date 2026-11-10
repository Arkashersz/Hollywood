import { Clapperboard, FileText, Film, Image, LayoutGrid, Link2, Mic, UserRound, Video, type LucideIcon } from 'lucide-react';

export type StageStatus = 'locked' | 'queued' | 'in_progress' | 'pending_review' | 'approved' | 'changes_requested' | 'failed';

export type Project = { id: string; title: string; source_url: string | null; status: string; current_stage: string; updated_at: string; created_at: string };

export type StageError = { code: string; message: string; action?: string; blocked?: boolean };
export type StagePayload = {
  summary?: string;
  warnings?: string[];
  error?: StageError;
  review_comment?: string;
  local_dir?: string;
  progress?: { done: number; total: number; label: string };
  next_generation?: { service: string; count: number; unit: string };
};
export type Stage = { id: string; project_id: string; stage_key: string; stage_order: number; title: string; status: StageStatus; current_version: number; payload: StagePayload };

export type Artifact = {
  id: string; project_id: string; stage_id: string | null; artifact_type: 'document' | 'image' | 'video' | 'evidence' | string;
  name: string; item_key: string | null; storage_path: string | null; local_relative_path: string | null; version: number;
  content: { markdown?: string; prompt?: string; width?: number; height?: number; duration?: number; note?: string }; updated_at: string;
};

export type EventRow = { id: number; project_id: string; stage_key: string | null; level: 'info' | 'warn' | 'error'; message: string; created_at: string };
export type Heartbeat = { worker_id: string; hostname: string | null; status: string; last_seen_at: string; details: { job?: { stage_key?: string } | null } };

export type Agent = 'claude' | 'chatgpt' | 'grok';
export const AGENT_LABEL: Record<Agent, string> = { claude: 'Claude', chatgpt: 'ChatGPT', grok: 'Grok' };
export const STAGE_META: Record<string, { icon: LucideIcon; subtitle: string; kind: 'texto' | 'geração'; agent: Agent }> = {
  analysis: { icon: Link2, subtitle: 'Gancho, CTA, estratégia e edição · documentos 01–02', kind: 'texto', agent: 'claude' },
  script: { icon: FileText, subtitle: 'Documento 03 · alemão com tradução', kind: 'texto', agent: 'claude' },
  character_bible: { icon: UserRound, subtitle: 'Documentos 04–06 · estilo, fichas e vozes', kind: 'texto', agent: 'claude' },
  character_images: { icon: Image, subtitle: 'Corpo inteiro de cada personagem', kind: 'geração', agent: 'chatgpt' },
  character_sheets: { icon: LayoutGrid, subtitle: 'Folhas 16:9 de cada personagem', kind: 'geração', agent: 'chatgpt' },
  visual_plan: { icon: Clapperboard, subtitle: 'Documento 07 · oito áreas e prompts', kind: 'texto', agent: 'claude' },
  scene_images: { icon: Image, subtitle: 'Ambientes e imagem-base de cada cena', kind: 'geração', agent: 'chatgpt' },
  video_prompts: { icon: Mic, subtitle: 'Documentos 08–10 · Grok, CapCut e publicação', kind: 'texto', agent: 'claude' },
  production: { icon: Video, subtitle: 'Um clipe por prompt aprovado', kind: 'geração', agent: 'grok' },
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
