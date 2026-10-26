import { afterEach, describe, expect, test, vi } from "vitest";
import { ApiError, api, asApiError, postLocal, setCredentials } from "./api";

function stubFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const sentHeaders = (fetch: ReturnType<typeof stubFetch>) => fetch.mock.calls[0][1]?.headers as Record<string, string>;

afterEach(() => { vi.unstubAllGlobals(); setCredentials(() => ({ key: "", model: "" })); });

describe("api", () => {
  test("GET without a key sends no key headers and returns the JSON", async () => {
    const fetch = stubFetch(200, { repos: [] });
    setCredentials(() => ({ key: "gsk_saved", model: "m1" }));
    expect(await api("/api/repos")).toEqual({ repos: [] });
    expect(fetch.mock.calls[0][0]).toBe("/api/repos");
    expect(sentHeaders(fetch)).toEqual({ "Content-Type": "application/json" });
  });

  test("requests send the same-origin cookies that say whose list it is", async () => {
    const fetch = stubFetch(200, { ok: true });
    await api("/api/logout", { method: "POST", body: {} });
    expect(fetch.mock.calls[0][1]?.credentials).toBe("same-origin");
    expect(fetch.mock.calls[0][1]?.body).toBe("{}");
  });

  test("withKey sends the saved key and model; an explicit key wins and sends no model", async () => {
    const fetch = stubFetch(200, {});
    setCredentials(() => ({ key: "gsk_saved", model: "m1" }));
    await api("/api/review", { method: "POST", body: { url: "o/n#1" }, withKey: true });
    expect(sentHeaders(fetch)).toMatchObject({ "X-LLM-Key": "gsk_saved", "X-LLM-Model": "m1" });
    expect(fetch.mock.calls[0][1]?.body).toBe('{"url":"o/n#1"}');
    await api("/api/models", { method: "POST", key: "gsk_new" });
    expect(fetch.mock.calls[1][1]?.headers).toEqual({ "Content-Type": "application/json", "X-LLM-Key": "gsk_new" });
  });

  test("errors carry status, code and the server's message", async () => {
    stubFetch(401, { error: "Groq rejected your API key.", code: "bad_key" });
    const err = await api("/api/models").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 401, code: "bad_key", message: "Groq rejected your API key.", retryAfter: null });
  });

  test("validation errors and bodies that aren't JSON get plain messages", async () => {
    stubFetch(422, { detail: [{ msg: "too short" }] });
    await expect(api("/x")).rejects.toMatchObject({ code: "error", message: "Please check what you typed." });
    stubFetch(500, "<html>oops</html>");
    await expect(api("/x")).rejects.toMatchObject({ status: 500, message: "Something went wrong (500)." });
  });

  test("429 and 503 read the wait from the body, else from Retry-After", async () => {
    stubFetch(429, { error: "slow down", code: "too_many_requests", retry_after: 90 });
    await expect(api("/x")).rejects.toMatchObject({ retryAfter: 90 });
    stubFetch(503, { error: "busy", code: "busy" }, { "Retry-After": "12" });
    await expect(api("/x")).rejects.toMatchObject({ retryAfter: 12 });
    stubFetch(429, { error: "x", code: "one_at_a_time" }, { "Retry-After": "soon" });
    await expect(api("/x")).rejects.toMatchObject({ retryAfter: null });
  });

  test("a network failure is a status-0 'network' error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(api("/x")).rejects.toMatchObject({ status: 0, code: "network" });
    expect(asApiError(new Error("boom"))).toMatchObject({ status: 0, code: "network" });
    const own = new ApiError(400, "bad_link", "nope");
    expect(asApiError(own)).toBe(own);
  });
});

describe("postLocal (reposcout serve)", () => {
  test("returns the JSON, or throws the server's error text", async () => {
    stubFetch(200, { answer: "a" });
    expect(await postLocal("/ask", { question: "q?" })).toEqual({ answer: "a" });
    stubFetch(409, { error: "The index was built with another embedder." });
    await expect(postLocal("/ask", {})).rejects.toThrow("The index was built with another embedder.");
    stubFetch(422, { detail: [] });
    await expect(postLocal("/ask", {})).rejects.toThrow("Please check what you typed.");
    stubFetch(500, "nope");
    await expect(postLocal("/ask", {})).rejects.toThrow("Something went wrong (500).");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("down"); }));
    await expect(postLocal("/ask", {})).rejects.toThrow("Is `reposcout serve` still running?");
  });
});
