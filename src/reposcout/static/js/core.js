// Shared DOM and network helpers: element builder, icons, storage, the API client, toasts and small blocks.
import { shortPath } from "./paths.js";

export const $ = (sel, root = document) => root.querySelector(sel);

/** Build an element. `on*` attributes become event listeners; false/null/undefined attributes and children are skipped. */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c.nodeType ? c : String(c));
  return node;
}

/* ================================================================ icons */
export const ICONS = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  review: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v7M18 15.5V9a3 3 0 0 0-3-3h-4"/><path d="m13 4-2 2 2 2"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  ext: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9c0 5-6 4-12 8"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 14h.01M15 14h.01"/>',
  shield: '<path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>',
};

const SVG = "http://www.w3.org/2000/svg";
function svg(inner, size, strokeWidth) {
  const s = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": strokeWidth, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) s.setAttribute(k, v);
  s.innerHTML = inner; // trusted constants only (ICONS, the tool registry)
  return s;
}

/** A 24x24 line icon from ICONS by name. */
export const icon = (name, size = 18) => svg(ICONS[name] || "", size, 2);

const TICON_GLYPH = { sm: 18, md: 22, lg: 28 };
/** A tool's tinted icon square. `tool` is a registry entry; size is "sm" (32), "md" (44) or "lg" (56). */
export function toolIcon(tool, size = "md") {
  const span = h("span", { class: size === "md" ? "ticon" : `ticon ${size}`, style: `--main:var(--c-${tool.color});--tint:var(--c-${tool.color}-t)` });
  span.append(svg(tool.glyph, TICON_GLYPH[size] || 22, 1.8));
  return span;
}

/* ================================================================ storage */
export const store = {
  get(k, session = false) { try { return JSON.parse((session ? sessionStorage : localStorage).getItem(k)); } catch { return null; } },
  set(k, v, session = false) { try { (session ? sessionStorage : localStorage).setItem(k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem(k); sessionStorage.removeItem(k); } catch {} },
};

/* ================================================================ API */
export class ApiError extends Error {
  constructor(status, code, message, retryAfter = null) {
    super(message);
    this.status = status; this.code = code; this.retryAfter = retryAfter;
  }
}

// settings.js registers where the saved key and model come from, so this module has no import cycle.
let credentials = () => ({ key: "", model: "" });
export function setCredentials(fn) { credentials = fn; }

function retryAfterOf(data, resp) {
  const fromBody = Number(data.retry_after);
  if (data.retry_after !== undefined && data.retry_after !== null && Number.isFinite(fromBody)) return fromBody;
  const header = resp.headers.get("Retry-After");
  const seconds = header === null ? NaN : Number(header);
  return Number.isFinite(seconds) ? seconds : null;
}

/** Call the JSON API. `key` sends that key; `withKey` sends the saved key and model. Throws ApiError. */
export async function api(path, { method = "GET", body, key, withKey = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  const saved = withKey ? credentials() : { key: "", model: "" };
  const k = key || saved.key;
  if (k) headers["X-LLM-Key"] = k;
  if (withKey && saved.model) headers["X-LLM-Model"] = saved.model;
  let resp;
  try { resp = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined }); }
  catch { throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again."); }
  let data = {};
  try { data = (await resp.json()) || {}; } catch {}
  if (!resp.ok) {
    const message = data.error || (Array.isArray(data.detail) ? "Please check what you typed." : `Something went wrong (${resp.status}).`);
    const retryAfter = resp.status === 429 || resp.status === 503 ? retryAfterOf(data, resp) : null;
    throw new ApiError(resp.status, data.code || "error", message, retryAfter);
  }
  return data;
}

const QUOTA_CODES = new Set(["too_many_requests", "queue_full"]);

/** The message to show for a failed request (spec §10). */
export function friendlyError(err) {
  if (!err) return "Something went wrong.";
  if (err.status === 0 || err.code === "network") return "Can't reach the server. Check your connection and try again.";
  // Only RepoScout's own quotas carry a known wait; other 429s (one at a time, the provider's or
  // GitHub's limits) explain themselves better in the server's message.
  if (err.status === 429 && QUOTA_CODES.has(err.code) && Number.isFinite(err.retryAfter) && err.retryAfter > 0) {
    return `You've hit the limit. Try again in ${Math.max(1, Math.ceil(err.retryAfter / 60))} min.`;
  }
  if (err.status === 503) return "RepoScout is busy. Try again in a moment.";
  return err.message || "Something went wrong.";
}

/* ================================================================ toasts (spec §5.4) */
const MAX_TOASTS = 3;
const live = [];

/** Show a toast. Info 4 s, with an action 6 s; an error with an action stays until dismissed. */
export function toast(message, { action = null, kind = "info" } = {}) {
  const region = $("#toasts");
  if (!region) return () => {};
  // #toasts is already a polite live region; only errors interrupt.
  const el = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" },
    icon(kind === "error" ? "alert" : kind === "success" ? "check" : "info", 18), h("span", { class: "msg" }, message));
  const sticky = kind === "error" && !!action;
  let left = action ? 6000 : kind === "error" ? 6000 : 4000, started = 0, timer = null, closed = false;
  const pause = () => { if (timer) { clearTimeout(timer); timer = null; left -= Date.now() - started; } };
  const resume = () => {
    if (sticky || closed || timer || document.hidden) return;
    started = Date.now(); timer = setTimeout(close, Math.max(left, 1500));
  };
  const onVisibility = () => (document.hidden ? pause() : resume());
  function close() {
    if (closed) return;
    closed = true; clearTimeout(timer); timer = null;
    document.removeEventListener("visibilitychange", onVisibility);
    live.splice(live.indexOf(close), 1);
    el.classList.add("leaving");
    // Remove once the exit transition ends; the timeout covers reduced motion and hidden tabs.
    const done = () => el.remove();
    el.addEventListener("transitionend", done, { once: true });
    setTimeout(done, 400);
  }
  if (action) el.append(h("button", { class: "btn", type: "button", onclick: () => { close(); action.run(); } }, action.label));
  el.append(h("button", { class: "btn quiet icon", type: "button", "aria-label": "Dismiss", onclick: close }, icon("x", 16)));
  el.addEventListener("pointerenter", pause);
  el.addEventListener("pointerleave", resume);
  document.addEventListener("visibilitychange", onVisibility);
  region.append(el);
  live.push(close);
  while (live.length > MAX_TOASTS) live[0]();
  resume();
  return close;
}

/* ================================================================ small blocks */
/** "path:start-end" for one line: a long path is cut in the middle (and by CSS), the line range always shows. */
export const fileLoc = (path, start, end, max = 48) =>
  h("span", { class: "loc" }, h("span", { class: "p" }, shortPath(path, max)), h("span", { class: "l" }, end === undefined ? `:${start}` : `:${start}-${end}`));

export const fmt = (n) => Number(n || 0).toLocaleString();

/** A skeleton bar; `cls` adds size classes such as "w-60" or "block". */
export const skel = (cls = "") => h("span", { class: cls ? `skel ${cls}` : "skel", "aria-hidden": "true" });

/** An empty, error or info block: icon, title, one line and optional actions. */
export function stateBlock({ icon: name, title, text, actions = [], tone = "" }) {
  return h("div", { class: tone ? `state ${tone}` : "state", role: tone === "error" ? "alert" : null },
    name ? icon(name, 20) : null, h("h3", {}, title), text ? h("p", {}, text) : null,
    actions && actions.length ? h("div", { class: "actions" }, ...actions) : null);
}

/** Numbered code lines starting at `firstLine`, cut at `maxLines`; `highlight` lists line numbers (as shown) to mark. */
export function codeBlock(text, firstLine, maxLines, highlight = []) {
  const lines = String(text).split("\n"); const shown = lines.slice(0, maxLines);
  const marked = new Set(highlight);
  const code = h("code", {}, ...shown.map((line, i) =>
    h("span", { class: marked.has(firstLine + i) ? "ln hl" : "ln", "data-n": firstLine + i }, line || " ")));
  if (lines.length > maxLines) code.append(h("span", { class: "ln", "data-n": "" }, "…"));
  const pre = h("pre", { class: "code" }, code);
  // The line-number gutter fits the longest number shown (six-digit lines in big files).
  pre.style.setProperty("--ln-w", `${Math.max(4, String(firstLine + shown.length - 1).length)}ch`);
  return pre;
}

/** A "Copy" button that copies getText() and shows "Copied" briefly. */
export function copyButton(getText) {
  const label = h("span", {}, "Copy");
  const btn = h("button", { class: "btn quiet", type: "button" }, icon("copy", 14), label);
  let reset = null;
  btn.addEventListener("click", () => navigator.clipboard?.writeText(getText()).then(() => {
    btn.replaceChildren(icon("check", 14), label); label.textContent = "Copied";
    clearTimeout(reset); reset = setTimeout(() => { btn.replaceChildren(icon("copy", 14), label); label.textContent = "Copy"; }, 1600);
  }, () => toast("Couldn't copy. Select the text and copy it instead.", { kind: "error" })));
  return btn;
}
