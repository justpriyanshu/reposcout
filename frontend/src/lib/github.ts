// The signed-in person's public GitHub repositories (GET /api/me/github-repos). Pure: no DOM.

export interface GithubRepo {
  full_name: string;
  description?: string | null;
  language?: string | null;
  stars?: number | null;
  pushed_at?: string | null;
  html_url?: string | null;
  fork?: boolean | null;
}

/** Repositories whose name, description or language contains the query (case-insensitive), in the given order. */
export function filterGithubRepos(repos: readonly GithubRepo[] | null | undefined, query: string | null | undefined): GithubRepo[] {
  const q = String(query || "").trim().toLowerCase();
  const list = (repos || []).filter((r) => r && r.full_name);
  if (!q) return list;
  return list.filter((r) => `${r.full_name} ${r.description || ""} ${r.language || ""}`.toLowerCase().includes(q));
}

const DAY = 86_400_000;

/** "today", "yesterday", "3 days ago", "2 weeks ago", "5 months ago", "2 years ago"; "" for no or a bad date. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "";
  const days = Math.floor(Math.max(0, now - t) / DAY);
  const ago = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (days < 1) return "today";
  if (days < 2) return "yesterday";
  if (days < 14) return ago(days, "day");
  if (days < 60) return ago(Math.floor(days / 7), "week");
  if (days < 365) return ago(Math.floor(days / 30), "month");
  return ago(Math.floor(days / 365), "year");
}
