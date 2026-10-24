// The local page served by `reposcout serve`: Ask, Review a diff and Find code over one local index.
import { $, codeBlock, h, icon, skel, stateBlock, store } from "./core.js";
import { markdown } from "./markdown.js";

const SEV_LABEL = { critical: "Critical", major: "Major", minor: "Minor", nit: "Nit" };

/* ================================================================ theme (same rs.theme storage as the public app) */
const systemDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => {
  const chosen = store.get("rs.theme");
  return chosen ? chosen === "dark" : systemDark.matches;
};
function applyTheme() {
  const chosen = store.get("rs.theme");
  if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
  else document.documentElement.removeAttribute("data-theme");
  const dark = isDark();
  const btn = $("#themeBtn");
  btn.replaceChildren(icon(dark ? "sun" : "moon", 18));
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
$("#themeBtn").addEventListener("click", () => { store.set("rs.theme", isDark() ? "light" : "dark"); applyTheme(); });
systemDark.addEventListener("change", applyTheme);
applyTheme();

/* ================================================================ network: the local server answers failures with {"error": ...} */
async function post(url, body) {
  let resp;
  try { resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch { throw new Error("Can't reach the server. Is `reposcout serve` still running?"); }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || (data.detail ? "Please check what you typed." : `Something went wrong (${resp.status}).`));
  return data;
}

/* ================================================================ stats panel */
const fmt = (n) => Number(n || 0).toLocaleString();
function paintStats(s) {
  const i = s.index || {};
  const name = String(s.repo || "").split(/[\\/]/).filter(Boolean).pop() || "Local index";
  const model = s.llm ? `model ${s.llm}` : "no LLM";
  $("#stats").replaceChildren(h("span", { class: "av", "aria-hidden": "true" }, name.replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "RS"),
    h("span", { class: "nm", title: s.repo || name }, name),
    h("span", { class: "meta" }, `${fmt(i.files)} files · ${fmt(i.chunks)} sections · ${model}`));
}
fetch("/stats").then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status))))).then(paintStats).catch(() => {
  $("#stats").replaceChildren(h("span", { class: "av", "aria-hidden": "true" }, "RS"), h("span", { class: "nm" }, "Local index"),
    h("span", { class: "meta" }, "Couldn't load the index stats. The tools below may still work."));
});

/* ================================================================ tabs */
const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(btn, focusInput = true) {
  for (const t of tabs) {
    const on = t === btn;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    $(`#${t.dataset.tab}`).hidden = !on;
  }
  if (focusInput) $(`#${btn.dataset.tab}`).querySelector("textarea, input")?.focus();
}
tabs.forEach((btn, i) => {
  btn.addEventListener("click", () => selectTab(btn));
  btn.addEventListener("keydown", (e) => {
    let next = null;
    if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
    else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === "Home") next = tabs[0];
    else if (e.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    e.preventDefault();
    next.focus();
    selectTab(next, false);
  });
});

/* ================================================================ shared run wrapper */
/** Run one request for a panel: skeletons while waiting, the error block on failure, one request at a time per panel. */
async function run(form, out, skeletons, work) {
  const btn = form.querySelector("button[type=submit]") || document.querySelector(`button[form="${form.id}"]`);
  if (btn.disabled) return;
  btn.disabled = true;
  out.setAttribute("aria-busy", "true");
  out.replaceChildren(skeletons());
  try { out.replaceChildren(...[].concat(await work())); }
  catch (err) {
    out.replaceChildren(stateBlock({ icon: "alert", tone: "error", title: "That didn't work", text: err.message,
      actions: [h("button", { class: "btn", type: "button", onclick: () => form.requestSubmit() }, "Try again")] }));
  } finally { btn.disabled = false; out.removeAttribute("aria-busy"); }
}
const answerSkeleton = () => h("div", { class: "a", "aria-hidden": "true" }, skel("w-80"), skel(), skel("w-60"));
const hitSkeleton = () => h("div", { "aria-hidden": "true" }, ...[0, 1].map(() => h("div", { class: "hit" },
  h("header", {}, skel("w-60")), h("div", { class: "hit-skel" }, skel(), skel("w-80"), skel("w-60")))));
const seconds = (ms) => `${((ms || 0) / 1000).toFixed(1)}s`;

/* ================================================================ Ask */
/** The answer as Markdown, with each known [n] turned into a badge (the local index has no URLs to link to). */
function renderAnswer(text, sources) {
  const wrap = h("div", { class: "md" });
  wrap.innerHTML = markdown(text); // markdown() escapes everything first
  const byRef = Object.fromEntries((sources || []).map((s) => [String(s.ref), s]));
  const walker = document.createTreeWalker(wrap, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (!walker.currentNode.parentElement.closest("pre, code")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (!/\[\d+\]/.test(node.nodeValue)) continue;
    const frag = document.createDocumentFragment();
    node.nodeValue.split(/(\[\d+\])/).forEach((part) => {
      const m = part.match(/^\[(\d+)\]$/);
      const s = m && byRef[m[1]];
      if (s) frag.append(h("span", { class: "cite", title: `${s.path}:${s.start_line}-${s.end_line}` }, m[1]));
      else frag.append(part);
    });
    node.replaceWith(frag);
  }
  return wrap;
}

$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const question = $("#question").value.trim();
  if (!question) { $("#question").focus(); return; }
  run(e.currentTarget, $("#ask-out"), answerSkeleton, async () => {
    const data = await post("/ask", { question });
    const cited = new Set(data.cited || []);
    const card = h("div", { class: "a" }, renderAnswer(data.answer, data.sources));
    if ((data.sources || []).length) {
      card.append(h("div", { class: "srcs", "aria-label": "Sources" }, data.sources.map((s) =>
        h("span", { class: cited.has(s.ref) ? "src" : "src uncited", title: `${s.name || s.path}${cited.has(s.ref) ? "" : " (not cited)"}` },
          h("span", { class: "n" }, s.ref), h("span", {}, `${s.path}:${s.start_line}-${s.end_line}`)))));
    }
    return [card, h("div", { class: "afoot" }, h("span", {}, seconds(data.latency_ms)))];
  });
});

/* ================================================================ Review a diff */
function findingCard(f) {
  const rule = String(f.source || "").replace(/^rule:/, "");
  return h("article", { class: `finding s-${f.severity}` },
    h("div", { class: "fh" }, h("span", { class: "sv" }, SEV_LABEL[f.severity] || f.severity), h("span", { class: "t" }, f.title),
      h("span", { class: "where" }, `${f.path}:${f.line}`)),
    h("p", {}, f.message),
    f.suggestion ? [h("p", { class: "fix" }, "Suggested change"), h("pre", {}, f.suggestion)] : null,
    h("div", { class: "by" }, f.source === "llm" ? [icon("bot", 14), `AI review · ${f.category}`] : [icon("shield", 14), `Fixed check (${rule}) · ${f.category}`]));
}

$("#review-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const diff = $("#diff").value;
  if (!diff.trim()) { $("#diff").focus(); return; }
  run(e.currentTarget, $("#review-out"), answerSkeleton, async () => {
    const data = await post("/review", { diff, min_severity: $("#min-sev").value });
    const findings = data.findings || [];
    const files = Object.entries(data.file_summaries || {});
    const stats = data.stats || {};
    const verdict = h("div", { class: "verdict stack" },
      h("strong", {}, data.headline),
      files.length ? h("ul", {}, files.map(([path, text]) => h("li", {}, h("b", {}, path), " ", text))) : null,
      h("span", { class: "note" }, `${seconds(stats.latency_ms)} · ${stats.llm_calls || 0} LLM call${stats.llm_calls === 1 ? "" : "s"}`));
    return [verdict, ...findings.map(findingCard)];
  });
});

/* ================================================================ Find code */
$("#find-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const query = $("#query").value.trim();
  if (!query) { $("#query").focus(); return; }
  run(e.currentTarget, $("#find-out"), hitSkeleton, async () => {
    const data = await post("/search", { query, k: 8 });
    if (!data.results.length) return stateBlock({ icon: "search", title: `No matches for “${query}”`, text: "Try other words, or describe what the code does." });
    return data.results.map((r) => h("article", { class: "hit" },
      h("header", {}, h("span", { class: "mono" }, `${r.path}:${r.start_line}-${r.end_line}`), r.name ? h("span", { class: "pill" }, r.name) : null),
      codeBlock(r.text, r.start_line, 14)));
  });
});

/* ================================================================ Ctrl/Cmd+Enter submits the form being typed in */
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    const form = e.target.closest?.("form");
    if (form) { e.preventDefault(); form.requestSubmit(); }
  }
});
