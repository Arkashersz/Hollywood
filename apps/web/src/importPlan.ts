// Converte o texto colado pelo usuário em itens {name, prompt, refs?, start?} para o plano importado.
//
// Formato aceito (um bloco por item):
//   ## Nome do item
//   Referências: Nome A, Nome B        (imagens; opcional)
//   Imagem inicial: Nome da imagem     (vídeos; opcional; "anterior" = último quadro do clipe anterior)
//   ...texto do prompt...
// Sem títulos "##", os blocos são separados por uma linha com "---" e recebem nomes automáticos.

export type PlanItem = { name: string; prompt: string; refs?: string[]; start?: string };
export type PlanKey = 'character_images' | 'character_sheets' | 'scene_images' | 'production';
export type Plan = Partial<Record<PlanKey, PlanItem[]>>;

const REFS = /^(?:refer[eê]ncias?|refs?|references?)\s*:\s*(.*)$/i;
const START = /^(?:imagem inicial|in[ií]cio|start(?: image)?)\s*:\s*(.*)$/i;

export function parseItems(text: string, fallbackName: string): PlanItem[] {
  const clean = text.replace(/\r\n?/g, '\n').trim();
  if (!clean) return [];
  let blocks: { name: string; body: string }[];
  if (/^##\s+/m.test(clean)) {
    blocks = clean.split(/^##\s+/m).filter((b) => b.trim()).map((chunk) => {
      const [first, ...rest] = chunk.split('\n');
      return { name: first.trim(), body: rest.join('\n') };
    });
  } else {
    blocks = clean.split(/\n\s*-{3,}\s*\n/).filter((b) => b.trim()).map((body, i) => ({ name: `${fallbackName} ${i + 1}`, body }));
  }
  return blocks.map(({ name, body }) => {
    const lines = body.trim().split('\n');
    let refs: string[] | undefined;
    let start: string | undefined;
    while (lines.length) {
      const line = lines[0].trim();
      const r = REFS.exec(line);
      const s = START.exec(line);
      if (r) refs = r[1].split(/[,;]/).map((x) => x.trim()).filter(Boolean);
      else if (s) start = s[1].trim();
      else if (line !== '') break;
      lines.shift();
    }
    const item: PlanItem = { name, prompt: lines.join('\n').trim() };
    if (refs?.length) item.refs = refs;
    if (start) item.start = start;
    return item;
  });
}

export function buildPlan(texts: Record<PlanKey, string>): { plan: Plan; total: number; problem: string | null } {
  const names: Record<PlanKey, string> = { character_images: 'Personagem', character_sheets: 'Folha', scene_images: 'Cena', production: 'Clipe' };
  const plan: Plan = {};
  let total = 0;
  for (const key of Object.keys(names) as PlanKey[]) {
    const items = parseItems(texts[key], names[key]);
    if (!items.length) continue;
    const empty = items.find((i) => !i.name || !i.prompt);
    if (empty) return { plan, total, problem: `Item sem texto de prompt: “${empty.name || '(sem nome)'}”.` };
    plan[key] = items;
    total += items.length;
  }
  return { plan, total, problem: null };
}
