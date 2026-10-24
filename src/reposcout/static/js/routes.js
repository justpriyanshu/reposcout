// Hash routes (spec §6.1). Pure: no DOM, tested under Node.

const TOOL_IDS = ["ask", "find", "review"];
const OWNER_RE = /^[A-Za-z0-9-]{1,39}$/;
const NAME_RE = /^[A-Za-z0-9._-]{1,100}$/;

function parts(hash) {
  try {
    return String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return null; // malformed percent-encoding
  }
}

/** "#/review/o/n/12" -> {view: "tool", tool: "review", owner: "o", name: "n", pull: 12}; anything unknown -> home. */
export function parseRoute(hash) {
  const p = parts(hash);
  if (p === null) {
    const tool = String(hash || "").replace(/^#\/?/, "").split("/")[0];
    return TOOL_IDS.includes(tool) ? { view: "tool", tool } : { view: "home" };
  }
  const [tool, owner, name, num] = p;
  if (!TOOL_IDS.includes(tool)) return { view: "home" };
  const route = { view: "tool", tool };
  if (!OWNER_RE.test(owner || "") || !NAME_RE.test(name || "")) return route; // same shapes as parseLink: else the picker
  route.owner = owner;
  route.name = name;
  if (tool === "review" && /^\d+$/.test(num || "") && +num > 0) route.pull = +num;
  return route;
}

const LEGACY_TABS = { "": "ask", ask: "ask", search: "find", review: "review" };

/** Old links (#/r/o/n[/tab], #/pr/o/n/num) -> the new hash, or null when the hash is current. */
export function legacyRedirect(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  if (raw[0] === "r" && raw.length >= 3) {
    const tool = LEGACY_TABS[raw[3] || ""] || "ask";
    return `#/${tool}/${raw[1]}/${raw[2]}`;
  }
  if (raw[0] === "pr" && raw.length >= 3) return `#/review/${raw[1]}/${raw[2]}${raw[3] ? "/" + raw[3] : ""}`;
  return null;
}
