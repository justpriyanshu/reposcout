// Safe Markdown for model answers: escape everything, then add a small set of formatting.
// Moved unchanged from the old app.html (only `esc` is renamed `escapeHtml`). Pure: no DOM, tested under Node.

export const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export function inline(text) {
  // text is already escaped; add code, bold, italics and safe links
  const codes = [];
  text = text.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  text = text.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  return text;
}
export function markdown(src) {
  const out = []; const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  let i = 0, para = [], list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(escapeHtml(para.join(" ")))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(escapeHtml(x))}</li>`).join("")}</${list.tag}>`); list = null; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+-]*)/);
    if (fence) {
      flushPara(); flushList(); const code = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flushPara(); flushList();
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(escapeHtml(c.trim())));
      const head = cells(line); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { flushPara(); flushList(); out.push(`<h3>${inline(escapeHtml(heading[2]))}</h3>`); i++; continue; }
    const item = line.match(/^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/);
    if (item) {
      flushPara(); const tag = item[1] ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(item[3]); i++; continue;
    }
    if (/^\s*>\s?/.test(line)) { flushPara(); flushList(); out.push(`<blockquote>${inline(escapeHtml(line.replace(/^\s*>\s?/, "")))}</blockquote>`); i++; continue; }
    if (!line.trim()) { flushPara(); flushList(); i++; continue; }
    if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); i++; continue; }
    flushList(); para.push(line.trim()); i++;
  }
  flushPara(); flushList();
  return out.join("");
}
