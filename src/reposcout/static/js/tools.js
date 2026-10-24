// The tool registry: every tool, live or coming soon. Pure: no DOM, tested under Node.
// `color` names the tool's CSS colour pair in ui.css (--c-{color} and --c-{color}-t).
// `glyph` is the inner markup of a 24x24 two-tone SVG; the `.fill` shape is tinted by ui.css.

export const CATEGORIES = ["Understand", "Review", "Maintain"];

export const TOOLS = [
  { id: "ask", name: "Ask", category: "Understand", color: "ask", live: true,
    description: "Ask how the code works and get answers that link to the exact lines.",
    glyph: '<path class="fill" d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4z"/><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4z"/><path d="M8.5 8.5h7M8.5 11.5h4"/>' },
  { id: "find", name: "Find code", category: "Understand", color: "find", live: true,
    description: "Search by name or by meaning. Works without an API key.",
    glyph: '<circle class="fill" cx="10.5" cy="10.5" r="6.5"/><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/><path d="m8.5 9-1.5 1.5L8.5 12M12.5 9l1.5 1.5-1.5 1.5"/>' },
  { id: "review", name: "Review a PR", category: "Review", color: "review", live: true,
    description: "Fixed safety checks plus an AI review that reads the code around the change.",
    glyph: '<rect class="fill" x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="7.5" r="2"/><circle cx="8" cy="16.5" r="2"/><circle cx="16" cy="16.5" r="2"/><path d="M8 9.5v5M16 14.5V11a2.5 2.5 0 0 0-2.5-2.5H11"/>' },
  { id: "guide", name: "Onboarding guide", category: "Understand", color: "guide", live: false,
    description: "A tour of the codebase for new contributors, with citations.",
    glyph: '<path class="fill" d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4z"/><path d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z"/>' },
  { id: "post", name: "Post a review", category: "Review", color: "post", live: false,
    description: "Send a RepoScout review to GitHub as inline comments, after a preview.",
    glyph: '<path class="fill" d="M3 11 21 3l-8 18-2-8z"/><path d="M3 11 21 3l-8 18-2-8zM11 13l10-10"/>' },
  { id: "issues", name: "Issue helper", category: "Maintain", color: "issues", live: false,
    description: "Summarise an issue, find the code it's about, and spot duplicates.",
    glyph: '<circle class="fill" cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.2"/>' },
  { id: "ci", name: "Explain a CI failure", category: "Maintain", color: "ci", live: false,
    description: "Read a failed Actions log, point at the code and suggest a fix.",
    glyph: '<path class="fill" d="M12 3 2.5 20h19z"/><path d="M12 3 2.5 20h19zM12 10v4.5M12 17.2v.1"/>' },
  { id: "release", name: "Release notes", category: "Maintain", color: "release", live: false,
    description: "Draft a changelog from the pull requests merged between two tags.",
    glyph: '<path class="fill" d="M4 4h16v16H4z"/><path d="M4 4h16v16H4zM8 9h8M8 13h8M8 17h5"/>' },
  { id: "health", name: "Repo health", category: "Maintain", color: "health", live: false,
    description: "Stale PRs, ageing issues, failing workflows and missing files.",
    glyph: '<path class="fill" d="M3 12h4l2-5 4 10 2-5h6v8H3z"/><path d="M3 12h4l2-5 4 10 2-5h6"/>' },
];

export const tool = (id) => TOOLS.find((t) => t.id === id) || null;

/** Tools whose name or description contains the query (case-insensitive), grouped by category; empty groups dropped. */
export function filterTools(query) {
  const q = String(query || "").trim().toLowerCase();
  const match = (t) => !q || `${t.name} ${t.description}`.toLowerCase().includes(q);
  return CATEGORIES.map((c) => [c, TOOLS.filter((t) => t.category === c && match(t))]).filter(([, ts]) => ts.length);
}

export const EXAMPLES = ["pallets/flask", "psf/requests", "tiangolo/fastapi", "osm-search/Nominatim"];

/** Repositories to suggest under a picker: ready ones in the given order, then examples not already listed, at most n. */
export function pickerRepos(repos, n = 3) {
  const out = (repos || []).filter((r) => r && r.ready).map((r) => r.full_name);
  const seen = new Set(out.map((s) => s.toLowerCase()));
  for (const ex of EXAMPLES) if (!seen.has(ex.toLowerCase())) out.push(ex);
  return out.slice(0, n);
}
