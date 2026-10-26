// Citation badges in a rendered answer: every known "[n]" outside code becomes a badge. Pure: no DOM.
import { escapeHtml } from "./markdown";

export interface Source {
  ref: number;
  path: string;
  start_line: number;
  end_line: number;
  url?: string;
  name?: string;
  kind?: string;
}

/**
 * Replace "[n]" in the text parts of `html` (escaped Markdown output) with a citation badge for
 * source n: a link to the code when the source has a URL (public app), otherwise a plain badge
 * (the local index). Text inside <pre>/<code>, tags and unknown refs are left alone.
 */
export function addCitations(html: string, sources: readonly Source[] | null | undefined, linked = true): string {
  const byRef = new Map((sources || []).map((s) => [String(s.ref), s]));
  if (!byRef.size) return html;
  let code = 0;
  return html.split(/(<[^>]*>)/).map((part) => {
    if (part.startsWith("<")) {
      const tag = part.match(/^<(\/?)(pre|code)\b/i);
      if (tag) code += tag[1] ? -1 : 1;
      return part;
    }
    if (code > 0 || !/\[\d+\]/.test(part)) return part;
    return part.replace(/\[(\d+)\]/g, (whole, n: string) => {
      const s = byRef.get(n);
      if (!s) return whole;
      const title = escapeHtml(`${s.path}:${s.start_line}-${s.end_line}`);
      return linked && s.url
        ? `<a class="cite" href="${escapeHtml(s.url)}" target="_blank" rel="noopener" title="${title}">${n}</a>`
        : `<span class="cite" title="${title}">${n}</span>`;
    });
  }).join("");
}
