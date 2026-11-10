"""JavaScript executado dentro da página para ler o estado da conversa.

`SNAPSHOT` devolve a última resposta já convertida para Markdown (lida do DOM renderizado,
sem usar a área de transferência nem APIs internas do site), as imagens grandes da resposta
e se a geração ainda está em andamento.
"""

SNAPSHOT = r"""
(sel) => {
  const q = (s, root) => { if (!s) return []; try { return [...(root || document).querySelectorAll(s)]; } catch (e) { return []; } };
  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const SKIP = new Set(['button','svg','style','script','nav','img','video','audio','canvas','select','textarea','input','form']);

  const inline = (node) => [...node.childNodes].map(md).join('');
  const cell = (node) => inline(node).trim().replace(/\n+/g, '<br>').replace(/\|/g, '\\|');
  const list = (node, depth) => {
    const ordered = node.tagName.toLowerCase() === 'ol';
    let n = parseInt(node.getAttribute('start') || '1', 10);
    const out = [];
    for (const li of node.children) {
      if (li.tagName.toLowerCase() !== 'li') continue;
      let body = '';
      let nested = '';
      for (const child of li.childNodes) {
        const tag = child.nodeType === 1 ? child.tagName.toLowerCase() : '';
        if (tag === 'ul' || tag === 'ol') nested += '\n' + list(child, depth + 1);
        else body += md(child);
      }
      body = body.trim().replace(/\n{2,}/g, '\n').replace(/\n/g, '\n' + '  '.repeat(depth + 1));
      out.push('  '.repeat(depth) + (ordered ? (n++) + '. ' : '- ') + body + nested);
    }
    return out.join('\n');
  };
  const table = (node) => {
    const rows = q('tr', node).map((tr) => [...tr.children].map(cell));
    if (!rows.length) return '';
    const width = Math.max(...rows.map((r) => r.length));
    const line = (r) => '| ' + Array.from({ length: width }, (_, i) => r[i] || '').join(' | ') + ' |';
    return [line(rows[0]), '| ' + Array(width).fill('---').join(' | ') + ' |', ...rows.slice(1).map(line)].join('\n');
  };
  function md(node) {
    if (node.nodeType === 3) return node.nodeValue.replace(/\s+/g, ' ');
    if (node.nodeType !== 1) return '';
    const tag = node.tagName.toLowerCase();
    if (SKIP.has(tag) || node.classList.contains('sr-only')) return '';
    if (/^h[1-6]$/.test(tag)) return '\n\n' + '#'.repeat(+tag[1]) + ' ' + inline(node).trim() + '\n\n';
    switch (tag) {
      case 'p': return '\n\n' + inline(node).trim() + '\n\n';
      case 'br': return '\n';
      case 'hr': return '\n\n---\n\n';
      case 'strong': case 'b': { const t = inline(node).trim(); return t ? '**' + t + '**' : ''; }
      case 'em': case 'i': { const t = inline(node).trim(); return t ? '*' + t + '*' : ''; }
      case 'code': return '`' + node.textContent + '`';
      case 'pre': {
        const code = node.querySelector('code');
        const lang = code ? ((code.className.match(/language-([\w+-]+)/) || [])[1] || '') : '';
        const text = (code ? code.textContent : node.innerText).replace(/\n+$/, '');
        const fence = text.includes('```') ? '````' : '```';
        return '\n\n' + fence + lang + '\n' + text + '\n' + fence + '\n\n';
      }
      case 'ul': case 'ol': return '\n\n' + list(node, 0) + '\n\n';
      case 'blockquote': return '\n\n' + inline(node).trim().split('\n').map((l) => '> ' + l).join('\n') + '\n\n';
      case 'a': { const t = inline(node).trim(); const href = node.getAttribute('href') || ''; return /^https?:/.test(href) && t ? '[' + t + '](' + href + ')' : t; }
      case 'table': return '\n\n' + table(node) + '\n\n';
      default: return inline(node);
    }
  }

  // A resposta pode vir em vários blocos seguidos (ex.: galeria da imagem + texto): junta tudo o que vem depois da última mensagem do usuário.
  const isUser = (u) => !!sel.user && (u.matches(sel.user) || !!u.querySelector(sel.user));
  let all = q(sel.turn);
  let units, reply;
  if (all.length) {
    let lastUser = -1;
    all.forEach((u, i) => { if (isUser(u)) lastUser = i; });
    units = all.filter((u) => !isUser(u));
    reply = all.slice(lastUser + 1).filter((u) => !isUser(u));
  } else {
    units = q(sel.assistant);
    reply = units.length ? [units[units.length - 1]] : [];
  }
  const images = [];
  let text = '';
  if (reply.length) {
    text = reply.map(md).join('\n\n').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    for (const unit of reply) {
      for (const img of q('img', unit)) {
        const w = img.naturalWidth || 0, h = img.naturalHeight || 0;
        if (img.complete && Math.max(w, h) >= 512 && img.currentSrc) images.push({ src: img.currentSrc, w, h });
      }
    }
  }
  const alerts = q(sel.alert).filter(visible).map((el) => el.innerText.trim()).filter(Boolean).slice(0, 5);
  return { count: units.length, text, images, busy: q(sel.stop).some(visible), alerts };
}
"""

VIDEOS = r"""
(sel) => {
  const out = [];
  let nodes = [];
  try { nodes = [...document.querySelectorAll(sel.video)]; } catch (e) { nodes = []; }
  for (const v of nodes) {
    const src = v.currentSrc || v.src || (v.querySelector('source') || {}).src || '';
    if (!src) continue;
    out.push({ src, duration: Number.isFinite(v.duration) ? v.duration : 0, ready: v.readyState, w: v.videoWidth, h: v.videoHeight });
  }
  let alerts = [];
  try { alerts = [...document.querySelectorAll(sel.alert)].filter((el) => el.getClientRects().length).map((el) => el.innerText.trim()).filter(Boolean).slice(0, 5); } catch (e) {}
  return { videos: out, alerts };
}
"""

# Baixa um recurso com a sessão da própria página (para URLs blob:/data: ou que exijam os cookies do site).
FETCH_BASE64 = r"""
async (url) => {
  const response = await fetch(url, { credentials: 'include' });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  const blob = await response.blob();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
"""
