// GitHub links in, tool routes out. Pure: no DOM, tested under Node.

/** "https://github.com/o/n/pull/12", "o/n" or "o/n#12" -> {owner, name, pull}, or null. */
export function parseLink(text) {
  const v = (text || "").trim().replace(/\/+$/, "");
  let m = v.match(/^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})(\/.*)?$/i);
  if (m) { const name = m[2].replace(/\.git$/i, ""); const pr = (m[3] || "").match(/^\/pulls?\/(\d+)/);
    return { owner: m[1], name, pull: pr ? +pr[1] : null }; }
  m = v.match(/^([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})(?:#(\d+))?$/);
  if (m) return { owner: m[1], name: m[2].replace(/\.git$/i, ""), pull: m[3] ? +m[3] : null };
  return null;
}

/** The hash for a tool page: "#/ask", "#/ask/o/n" or "#/review/o/n/12". */
export function toolHash(tool, owner, name, pull) {
  const parts = [tool];
  if (owner && name) {
    parts.push(owner, name);
    if (pull !== undefined && pull !== null && pull !== "") parts.push(pull);
  }
  return "#/" + parts.map((p) => encodeURIComponent(String(p))).join("/");
}
