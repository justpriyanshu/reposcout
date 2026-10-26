// The JSON API client and the messages shown for failures (spec §10). No DOM.

export class ApiError extends Error {
  status: number;
  code: string;
  retryAfter: number | null;
  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

// settings.ts registers where the saved key and model come from, so this module has no import cycle.
let credentials = (): { key: string; model: string } => ({ key: "", model: "" });
export function setCredentials(fn: () => { key: string; model: string }): void { credentials = fn; }

interface ErrorBody { error?: string; code?: string; detail?: unknown; retry_after?: unknown }

function retryAfterOf(data: ErrorBody, resp: Response): number | null {
  const fromBody = Number(data.retry_after);
  if (data.retry_after !== undefined && data.retry_after !== null && Number.isFinite(fromBody)) return fromBody;
  const header = resp.headers.get("Retry-After");
  const seconds = header === null ? NaN : Number(header);
  return Number.isFinite(seconds) ? seconds : null;
}

export interface ApiOptions {
  method?: string;
  body?: unknown;
  /** Send this key (the key dialog checking a new key). */
  key?: string;
  /** Send the saved key and model. */
  withKey?: boolean;
  signal?: AbortSignal;
}

/** Call the JSON API. Throws ApiError. */
export async function api<T = unknown>(path: string, { method = "GET", body, key, withKey = false, signal }: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const saved = withKey ? credentials() : { key: "", model: "" };
  const k = key || saved.key;
  if (k) headers["X-LLM-Key"] = k;
  if (withKey && saved.model) headers["X-LLM-Model"] = saved.model;
  let resp: Response;
  try {
    // Same-origin cookies identify the visitor (signed in, or this browser's own anonymous list).
    resp = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined, signal, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again.");
  }
  let data: ErrorBody & Record<string, unknown> = {};
  try { data = ((await resp.json()) as typeof data) || {}; } catch { /* not JSON */ }
  if (!resp.ok) {
    const message = data.error || (Array.isArray(data.detail) ? "Please check what you typed." : `Something went wrong (${resp.status}).`);
    const retryAfter = resp.status === 429 || resp.status === 503 ? retryAfterOf(data, resp) : null;
    throw new ApiError(resp.status, data.code || "error", message, retryAfter);
  }
  return data as T;
}

/** Any thrown value as an ApiError (network failures and bugs become "network"). */
export const asApiError = (err: unknown): ApiError =>
  err instanceof ApiError ? err : new ApiError(0, "network", friendlyError(err));

const QUOTA_CODES = new Set(["too_many_requests", "queue_full"]);
export const KEY_ERRORS = new Set(["no_key", "bad_key", "bad_model"]);

/** The message to show for a failed request (spec §10). */
export function friendlyError(err: unknown): string {
  if (!err) return "Something went wrong.";
  const e = err as Partial<ApiError>;
  if (e.status === 0 || e.code === "network") return "Can't reach the server. Check your connection and try again.";
  // Only RepoScout's own quotas carry a known wait; other 429s (one at a time, the provider's or
  // GitHub's limits) explain themselves better in the server's message.
  if (e.status === 429 && QUOTA_CODES.has(e.code || "") && Number.isFinite(e.retryAfter) && (e.retryAfter as number) > 0) {
    return `You've hit the limit. Try again in ${Math.max(1, Math.ceil((e.retryAfter as number) / 60))} min.`;
  }
  if (e.status === 503) return "RepoScout is busy. Try again in a moment.";
  return e.message || "Something went wrong.";
}

/** The local server (`reposcout serve`) answers failures with {"error": ...} and no code. */
export async function postLocal<T = unknown>(url: string, body: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Can't reach the server. Is `reposcout serve` still running?");
  }
  const data = (await resp.json().catch(() => ({}))) as ErrorBody & T;
  if (!resp.ok) throw new Error(data.error || (data.detail ? "Please check what you typed." : `Something went wrong (${resp.status}).`));
  return data;
}
