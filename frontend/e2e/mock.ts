// A tiny in-browser API mock: every request to the app's API goes through page.route, and any
// request that no handler answers fails the test (so nothing ever reaches a real server).
import { expect, type Page, type Request } from "@playwright/test";

export interface Reply { status?: number; json?: unknown; headers?: Record<string, string> }
export type Handler = (req: Request, url: URL) => Reply | Promise<Reply>;

export interface Mock {
  /** Register a handler for "METHOD /path" (exact pathname). */
  on(route: string, handler: Handler | Reply): void;
  /** Requests seen, in order, as "METHOD /path". */
  calls: string[];
  /** The requests for one route. */
  requests(route: string): Request[];
  /** Fail the test if any request had no handler. */
  verify(): void;
}

// Paths the two pages call (the public JSON API, and the local server's endpoints).
const API = /^\/(api\/|stats$|ask$|review$|reviews$|search$|health$)/;

export async function mockApi(page: Page): Promise<Mock> {
  const handlers = new Map<string, Handler | Reply>();
  const seen: { route: string; req: Request }[] = [];
  const unhandled: string[] = [];
  await page.route((url) => API.test(url.pathname), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const key = `${req.method()} ${url.pathname}`;
    seen.push({ route: key, req });
    const handler = handlers.get(key);
    if (!handler) {
      unhandled.push(key);
      await route.fulfill({ status: 599, json: { error: `unmocked ${key}`, code: "unmocked" } });
      return;
    }
    const reply = typeof handler === "function" ? await handler(req, url) : handler;
    await route.fulfill({ status: reply.status ?? 200, json: reply.json ?? {}, headers: reply.headers });
  });
  return {
    on(route, handler) { handlers.set(route, handler); },
    get calls() { return seen.map((s) => s.route); },
    requests(route) { return seen.filter((s) => s.route === route).map((s) => s.req); },
    verify() { expect(unhandled, "requests without a mock").toEqual([]); },
  };
}

export const CONFIG = { provider: "Groq", key_url: "https://console.groq.com/keys", default_model: "openai/gpt-oss-20b" };

/** Save an API key the way the key dialog does (remembered: localStorage). */
export async function saveKey(page: Page, key = "gsk_test", model = "openai/gpt-oss-20b") {
  await page.addInitScript(([k, m]) => {
    localStorage.setItem("rs.key", JSON.stringify(k));
    localStorage.setItem("rs.model", JSON.stringify(m));
  }, [key, model]);
}

export const readyRepo = (owner: string, name: string, extra: Record<string, unknown> = {}) => ({
  owner, name, full_name: `${owner}/${name}`, key: `${owner}/${name}`.toLowerCase(), status: "ready", ready: true,
  files_total: 42, files_done: 42, commit_sha: "c0ffee1234567", branch: "main", error: null, ...extra,
});
