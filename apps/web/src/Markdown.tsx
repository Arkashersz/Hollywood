import { useMemo } from 'react';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

// O texto vem de um modelo e de metadados de terceiros: é sempre higienizado, sem imagens remotas nem scripts.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => {
    const raw = marked.parse(text, { gfm: true, async: false }) as string;
    return DOMPurify.sanitize(raw, { FORBID_TAGS: ['img', 'style', 'form', 'input', 'iframe', 'video', 'audio', 'svg'], FORBID_ATTR: ['style', 'class', 'id'] });
  }, [text]);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
