// The tool page frame, repository picker, repo bar and indexing card (spec §5.4, §6.4, §10).
import { h, icon, toolIcon, api, ApiError, toast, skel, stateBlock, fmt, friendlyError } from "./core.js";
import { TOOLS, tool, pickerRepos, EXAMPLES } from "./tools.js";
import { parseLink, toolHash } from "./links.js";
import { approveReview } from "./reviewdata.js";

const POLL_MS = 1500;
const MAX_MISSES = 8; // consecutive failed polls before giving up
const STEPS = [["queued", "Waiting for a free slot"], ["cloning", "Downloading the code"], ["indexing", "Reading and indexing files"], ["ready", "Ready"]];

/** The tool whose page is showing, read from the hash ("#/ask/o/n" -> "ask"); used for "Change" and "Pick another". */
function currentTool(fallback = "ask") {
  let id = "";
  try { id = decodeURIComponent(location.hash.split("/")[1] || "").trim(); } catch {}
  return tool(id)?.live ? id : fallback;
}

/** Run the router again for the same hash (a fresh GET, a fresh view). */
const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

const repoPath = (owner, name) => `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;

/* ================================================================ frame */
let frame = null; // the frame on screen, for setAsideRepo

function fillAside(aside, toolId, repo) {
  const others = TOOLS.filter((t) => t.live && t.id !== toolId);
  aside.replaceChildren(h("div", { class: "othersbox" },
    h("h3", {}, repo ? "Also for this repository" : "Other tools"),
    h("nav", { class: "others", "aria-label": repo ? "Other tools for this repository" : "Other tools" },
      others.map((t) => h("a", { href: repo ? toolHash(t.id, repo.owner, repo.name) : toolHash(t.id) }, toolIcon(t, "sm"), t.name)))));
}

/** Breadcrumb, header and the two-column grid. Returns the main column and the side column. */
export function toolFrame(main, toolId) {
  const t = tool(toolId);
  const body = h("div", { class: "toolmain" });
  const aside = h("aside", { class: "aside" });
  fillAside(aside, toolId, null);
  frame = { aside, toolId };
  main.append(h("div", { class: "wrap" },
    h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, h("a", { href: "#/" }, "All tools"), h("span", { "aria-hidden": "true" }, "/"), h("span", { "aria-current": "page" }, t.name)),
    h("div", { class: "toolhead" }, toolIcon(t, "lg"), h("div", {}, h("h1", {}, t.name), h("p", {}, t.description))),
    h("div", { class: "toolgrid" }, body, aside)));
  return { body, aside };
}

/** Point the side column's links at this repository. */
export function setAsideRepo(owner, name) {
  if (frame) fillAside(frame.aside, frame.toolId, { owner, name });
}

/* ================================================================ picker */
const PICK = {
  review: { title: "Which pull request?", help: "Paste a pull request link, or pick a repository and type its number.", placeholder: "github.com/owner/repo/pull/123" },
  other: { title: "Which repository?", help: "Paste a public GitHub repository. The first time, RepoScout reads and indexes it; that takes a minute or two.", placeholder: "github.com/owner/repo" },
};

/** The "Which repository?" card with a link field and up to three suggestions. */
export function renderPicker(body, toolId) {
  const copy = toolId === "review" ? PICK.review : PICK.other;
  const input = h("input", { id: "pick-link", type: "text", name: "link", placeholder: copy.placeholder, "aria-label": "GitHub link",
    autocomplete: "off", autocapitalize: "none", spellcheck: "false", "aria-describedby": "pick-msg" });
  const msg = h("p", { id: "pick-msg", class: "invalid", hidden: true });
  const form = h("form", { class: "input", novalidate: true }, icon("link", 18), input, h("button", { class: "btn primary", type: "submit" }, "Continue"));
  const setInvalid = (text) => {
    msg.textContent = text; msg.hidden = !text;
    if (text) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const link = parseLink(input.value);
    if (!link) { setInvalid("Paste a link like github.com/owner/repo"); input.focus(); return; }
    setInvalid("");
    // A PR link goes to Review from any tool, and starts at once: the visitor just asked for it.
    if (link.pull) approveReview(link.owner, link.name, link.pull);
    location.hash = link.pull ? toolHash("review", link.owner, link.name, link.pull) : toolHash(toolId, link.owner, link.name);
  });
  input.addEventListener("input", () => { if (!msg.hidden) setInvalid(""); });

  const chips = h("div", { class: "recent", role: "group", "aria-label": "Try one of these", "aria-busy": "true" },
    [0, 1, 2].map(() => h("span", { class: "chip", "aria-hidden": "true" }, skel())));
  const show = (names) => {
    chips.removeAttribute("aria-busy");
    chips.replaceChildren(...names.map((full) => {
      const [o, n] = full.split("/");
      return h("a", { class: "chip", href: toolHash(toolId, o, n) }, full);
    }));
  };
  body.replaceChildren(h("section", { class: "pick" }, h("h2", {}, copy.title), h("p", {}, copy.help), form, msg,
    h("p", { class: "try" }, "Try"), chips));
  api("/api/repos").then(
    (data) => { if (chips.isConnected) show(pickerRepos(data.repos)); },
    () => { if (chips.isConnected) show(EXAMPLES.slice(0, 3)); });
}

/* ================================================================ repo bar */
const initials = (full) => full.replace(/[^A-Za-z0-9]/g, "").slice(0, 2);

function metaOf(r, indexed) {
  if (!indexed) return "Not indexed. Reviews read only the changed files.";
  if (r.status === "failed") return "Indexing failed";
  if (r.status === undefined) return "Checking the index…";
  if (!r.ready) return "Indexing now";
  const commit = (r.commit_sha || "").slice(0, 7);
  const where = [r.branch, commit].filter(Boolean).join(" @ ");
  return [where, r.files_total ? `${fmt(r.files_total)} files indexed` : ""].filter(Boolean).join(" · ") || "Ready";
}

/** The bar under the tool header: initials, owner/name, a meta line and the actions, with banners beneath. */
export function repoBar(record, { indexed = true, tool: toolId } = {}) {
  const full = record.full_name || `${record.owner}/${record.name}`;
  const tid = toolId || currentTool();
  const wrap = h("div", { class: "repowrap" });
  const acts = h("div", { class: "acts" });
  wrap.append(h("div", { class: "repobar" }, h("span", { class: "av", "aria-hidden": "true" }, initials(full)),
    h("span", { class: "nm", title: full }, full), h("span", { class: "meta" }, metaOf(record, indexed)), acts));

  if (indexed && record.status !== "failed") {
    const refresh = h("button", { class: "btn icon", type: "button", "aria-label": "Refresh index", title: "Refresh index" }, icon("refresh", 16));
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      try {
        await api("/api/repos", { method: "POST", body: { url: full, refresh: true } });
        toast("Refreshing the index in the background. You can keep using it meanwhile.");
      } catch (err) { toast(friendlyError(err), { kind: "error" }); }
      refresh.disabled = false;
    });
    acts.append(refresh);
  } else if (!indexed) {
    const index = h("button", { class: "btn", type: "button" }, "Index it");
    index.addEventListener("click", async () => {
      index.disabled = true;
      try {
        const { repo } = await api("/api/repos", { method: "POST", body: { url: full } });
        toast("Indexing started. You can keep reviewing meanwhile.");
        const next = repoBar(repo, { indexed: true, tool: tid });
        wrap.replaceWith(next);
        next.querySelector(".acts .btn")?.focus(); // keep keyboard focus on the bar
      } catch (err) { toast(friendlyError(err), { kind: "error" }); index.disabled = false; }
    });
    acts.append(index);
  }
  acts.append(
    h("a", { class: "btn icon", href: `https://github.com/${full}`, target: "_blank", rel: "noopener", "aria-label": "Open on GitHub", title: "Open on GitHub" }, icon("ext", 16)),
    h("a", { class: "btn", href: toolHash(tid) }, "Change"));

  if (indexed && record.ready) {
    if (record.status && record.status !== "ready") {
      wrap.append(h("div", { class: "banner", role: "status" }, icon("info", 16), "Refreshing the index in the background. Answers use the previous version until it's done."));
    } else if (record.error) {
      wrap.append(h("div", { class: "banner error" }, icon("alert", 16), `Last refresh failed: ${record.error}`));
    }
  }
  return wrap;
}

/* ================================================================ indexing */
function failureCard(body, owner, name, reason, { refresh, tool: toolId }) {
  const full = `${owner}/${name}`;
  const retry = h("button", { class: "btn primary", type: "button" }, "Try again");
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      if (refresh) await api("/api/repos", { method: "POST", body: { url: full, refresh: true } });
      reload();
    } catch (err) { toast(friendlyError(err), { kind: "error" }); retry.disabled = false; }
  });
  body.replaceChildren(stateBlock({ icon: "alert", tone: "error", title: `Couldn't index ${full}`, text: reason || "Indexing failed.",
    actions: [retry, h("a", { class: "btn", href: toolHash(toolId) }, "Pick another repository")] }));
}

function stepLabel(key, label, r) {
  if (key === "indexing" && r.files_total) return `${label} (${fmt(r.files_done)} of ${fmt(r.files_total)})`;
  if (key === "queued" && r.queue_position > 1) return `${label} (number ${r.queue_position} in line)`;
  return label;
}

function indexingCard(full) {
  const fill = h("span");
  const bar = h("div", { class: "bar wait", role: "progressbar", "aria-label": "Indexing progress", "aria-valuemin": "0", "aria-valuemax": "100" }, fill);
  const steps = h("ul", { class: "steps" });
  const status = h("p", { class: "sr-only", role: "status" }); // announced only when the step changes
  const card = h("section", { class: "progress" }, h("h2", {}, `Getting ${full} ready`),
    h("p", {}, "This happens once per repository. You can leave this page open, or come back later."), bar, steps, status);
  let shownStep = -1, shownText = "";
  return {
    card,
    paint(r) {
      const at = Math.max(0, STEPS.findIndex(([k]) => k === r.status));
      const labels = STEPS.map(([key, label]) => stepLabel(key, label, r));
      const text = `${at}|${labels.join("|")}`;
      if (text !== shownText) { // rebuild only when something visible changed
        shownText = text;
        steps.replaceChildren(...STEPS.map(([key], i) => {
          const done = i < at || (i === at && key === "ready");
          return h("li", { class: done ? "done" : i === at ? "now" : "", "aria-current": i === at ? "step" : null },
            h("span", { class: "mk" }, done ? icon("check", 11) : null), labels[i]);
        }));
      }
      if (at !== shownStep) { shownStep = at; status.textContent = STEPS[at][1]; }
      if (r.status === "indexing" && r.files_total > 0) {
        const part = Math.min(1, Math.max(0, r.files_done / r.files_total));
        bar.classList.remove("wait");
        fill.style.transform = `scaleX(${part})`;
        bar.setAttribute("aria-valuenow", String(Math.round(part * 100)));
      } else {
        bar.classList.add("wait");
        fill.style.transform = "";
        bar.removeAttribute("aria-valuenow");
      }
    },
  };
}

/**
 * Make sure owner/name is indexed. GETs the record, POSTs /api/repos on 404, shows the indexing card in
 * `body` and polls every 1.5 s. Resolves the ready record, or null after rendering the failure state
 * (or when the visitor leaves). The caller renders the tool into `body` afterwards.
 * `opts.onRecord(record)` is called with the first record and with the final one (to refresh the repo bar).
 */
export async function ensureIndexed(body, owner, name, onLeave, opts = {}) {
  const full = `${owner}/${name}`;
  const toolId = opts.tool || currentTool();
  const note = typeof opts.onRecord === "function" ? opts.onRecord : () => {};
  let stopped = false, timer = null, wake = null;
  onLeave(() => { stopped = true; clearTimeout(timer); wake?.(); });
  const sleep = (ms) => new Promise((resolve) => { wake = resolve; timer = setTimeout(resolve, ms); });
  const gone = () => stopped || !body.isConnected;

  let record;
  try {
    try { record = (await api(repoPath(owner, name))).repo; }
    catch (err) {
      if (!(err instanceof ApiError) || err.status !== 404) throw err;
      if (gone()) return null;
      record = (await api("/api/repos", { method: "POST", body: { url: full } })).repo;
    }
  } catch (err) {
    if (gone()) return null;
    failureCard(body, owner, name, friendlyError(err), { refresh: false, tool: toolId });
    return null;
  }
  if (gone()) return null;
  note(record);
  if (record.ready) return record;
  if (record.status === "failed") { failureCard(body, owner, name, record.error, { refresh: true, tool: toolId }); return null; }

  const view = indexingCard(record.full_name || full);
  view.paint(record);
  body.replaceChildren(view.card);
  let misses = 0;
  for (;;) {
    await sleep(POLL_MS);
    if (gone()) return null;
    try { record = (await api(repoPath(owner, name))).repo; misses = 0; }
    catch (err) {
      if (gone()) return null;
      if (++misses < MAX_MISSES) continue; // a blip: keep polling
      failureCard(body, owner, name, friendlyError(err), { refresh: false, tool: toolId });
      return null;
    }
    if (gone()) return null;
    if (record.ready) { note(record); return record; }
    if (record.status === "failed") { note(record); failureCard(body, owner, name, record.error, { refresh: true, tool: toolId }); return null; }
    view.paint(record);
  }
}

/* ================================================================ tool page */
/**
 * Shared start of every tool view: the frame, then the picker (no repository) or the repo bar and,
 * for tools that need the index, the indexing card. Resolves `{content, record}` when the tool body
 * can be rendered into `content`, or null when it can't (picker shown, failed, or the visitor left).
 * With `index: false` (Review) the record may be null.
 */
export async function mountTool(main, route, onLeave, { index = true } = {}) {
  const { body } = toolFrame(main, route.tool);
  if (!route.owner) { renderPicker(body, route.tool); return null; }
  const { owner, name } = route;
  const full = `${owner}/${name}`;
  setAsideRepo(owner, name);
  let bar = repoBar({ owner, name, full_name: full }, { indexed: true, tool: route.tool });
  const content = h("div", { class: "toolbody" });
  body.append(bar, content);
  const swap = (next) => { bar.replaceWith(next); bar = next; };

  if (index) {
    const record = await ensureIndexed(content, owner, name, onLeave, { tool: route.tool, onRecord: (r) => swap(repoBar(r, { indexed: true, tool: route.tool })) });
    if (!record || route.signal?.aborted) return null;
    // The tool replaces the indexing card the visitor was watching: let it arrive rather than jump in.
    if (content.querySelector(".progress")) {
      content.classList.add("enter");
      content.addEventListener("animationend", function done(e) { if (e.target === content) { content.classList.remove("enter"); content.removeEventListener("animationend", done); } });
    }
    return { content, record };
  }
  let record = null, missing = false;
  try { record = (await api(repoPath(owner, name))).repo; }
  catch (err) { missing = err instanceof ApiError && err.status === 404; }
  if (route.signal?.aborted) return null;
  if (record) swap(repoBar(record, { indexed: true, tool: route.tool }));
  else if (missing) swap(repoBar({ owner, name, full_name: full }, { indexed: false, tool: route.tool }));
  else swap(repoBar({ owner, name, full_name: full, status: "ready", ready: true }, { indexed: true, tool: route.tool })); // unknown: don't claim it is missing
  return { content, record };
}
