// Who is signed in, the sign-in link, and the error the server hands back after a failed sign-in. Pure: no DOM.

export interface User { login: string; name: string; avatar_url: string; html_url: string }

/** GET /api/me. `github_login` is false when the server has no GitHub OAuth app: no sign-in UI at all. */
export interface Me { user: User | null; github_login: boolean }

export const SIGNED_OUT: Me = { user: null, github_login: false };

/** /api/me's reply as a Me, whatever came back (an older server, or nothing). */
export function asMe(data: unknown): Me {
  const d = (data && typeof data === "object" ? data : {}) as { user?: Partial<User> | null; github_login?: unknown };
  const u = d.user;
  const user = u && typeof u === "object" && typeof u.login === "string" && u.login
    ? { login: u.login, name: String(u.name || ""), avatar_url: String(u.avatar_url || ""), html_url: String(u.html_url || `https://github.com/${u.login}`) }
    : null;
  return { user, github_login: d.github_login === true };
}

/**
 * The sign-in link. It is a plain navigation, not a request: the server sends the visitor to GitHub
 * and back, then on to `next` (this page and route, e.g. "/#/ask/pallets/flask").
 */
export function signInUrl(pathname: string, hash: string): string {
  return `/auth/github/login?next=${encodeURIComponent((pathname || "/") + (hash || ""))}`;
}

const GENERIC = "Couldn't sign in with GitHub. Try again.";

/**
 * Take ?login_error=… off a query string: the message to show (null when there is none) and the
 * query without it ("" or "?a=b"), for history.replaceState.
 */
export function takeLoginError(search: string): { message: string | null; search: string } {
  const params = new URLSearchParams(search);
  if (!params.has("login_error")) return { message: null, search };
  const raw = (params.get("login_error") || "").replace(/\s+/g, " ").trim();
  params.delete("login_error");
  const rest = params.toString();
  return { message: raw ? raw.slice(0, 200) : GENERIC, search: rest ? `?${rest}` : "" };
}

/** Up to two letters for an avatar that didn't load. */
export const initialsOf = (login: string): string => login.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
