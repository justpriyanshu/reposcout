// Hash routes (spec §6.1). Pure: no DOM.

export type ToolId = "ask" | "find" | "review";

export type Route =
  | { view: "home" }
  | { view: "github" }
  | { view: "tool"; tool: ToolId; owner?: string; name?: string; pull?: number };

const TOOL_IDS: readonly string[] = ["ask", "find", "review"];
const OWNER_RE = /^[A-Za-z0-9-]{1,39}$/;
const NAME_RE = /^[A-Za-z0-9._-]{1,100}$/;
const isTool = (id: string | undefined): id is ToolId => !!id && TOOL_IDS.includes(id);

function parts(hash: string | null | undefined): string[] | null {
  try {
    return String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return null; // malformed percent-encoding
  }
}

/** "#/review/o/n/12" -> {view: "tool", tool: "review", owner: "o", name: "n", pull: 12}; "#/github" -> the
 * signed-in person's GitHub repositories; anything unknown -> home. */
export function parseRoute(hash: string | null | undefined): Route {
  const p = parts(hash);
  if (p === null) {
    const tool = String(hash || "").replace(/^#\/?/, "").split("/")[0];
    return isTool(tool) ? { view: "tool", tool } : { view: "home" };
  }
  const [tool, owner, name, num] = p;
  if (tool === "github" && p.length === 1) return { view: "github" };
  if (!isTool(tool)) return { view: "home" };
  const route: Route = { view: "tool", tool };
  if (!OWNER_RE.test(owner || "") || !NAME_RE.test(name || "")) return route; // same shapes as parseLink: else the picker
  route.owner = owner;
  route.name = name;
  if (tool === "review" && /^\d+$/.test(num || "") && +num > 0) route.pull = +num;
  return route;
}

const LEGACY_TABS: Record<string, ToolId> = { "": "ask", ask: "ask", search: "find", review: "review" };

/** Old links (#/r/o/n[/tab], #/pr/o/n/num) -> the new hash, or null when the hash is current. */
export function legacyRedirect(hash: string | null | undefined): string | null {
  const raw = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  if (raw[0] === "r" && raw.length >= 3) {
    const tool = LEGACY_TABS[raw[3] || ""] || "ask";
    return `#/${tool}/${raw[1]}/${raw[2]}`;
  }
  if (raw[0] === "pr" && raw.length >= 3) return `#/review/${raw[1]}/${raw[2]}${raw[3] ? "/" + raw[3] : ""}`;
  return null;
}
