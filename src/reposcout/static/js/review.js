// Review a PR view (spec §6.7). Needs no index; spends the visitor's own key, so it starts only on their click.
import { ApiError, api, fmt, friendlyError, h, icon, stateBlock, store, toast } from "./core.js";
import { escapeHtml, inline } from "./markdown.js";
import { config, handleError, onKeyChange, openKeyDialog, settings } from "./settings.js";
import { toolHash } from "./links.js";
import { mountTool } from "./repo.js";
import { SEVERITIES, approveReview, consumeApproval, defaultSeverities, isApproved, parsePrInput, reviewCacheKey } from "./reviewdata.js";

const SEV_LABEL = { critical: "Critical", major: "Major", minor: "Minor", nit: "Nit" };
const STATE_TONE = { open: "ok", merged: "busy", closed: "bad" };
const KEY_ERRORS = new Set(["no_key", "bad_key", "bad_model"]);
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;

/** Run the router again for the same hash. */
const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

/** Start a review the visitor asked for: approve it for this tab, then open its page. */
export function requestReview(owner, name, pull) {
  approveReview(owner, name, pull);
  const next = toolHash("review", owner, name, pull);
  if (location.hash === next) reload(); else location.hash = next;
}

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: false });
  if (!page || route.signal.aborted) return;
  const { content } = page;
  if (route.pull) reviewPage(content, route, onLeave);
  else prForm(content, route);
}

/* ================================================================ #/review/o/n */
function prForm(content, { owner, name, signal }) {
  const input = h("input", { type: "text", placeholder: "Number or link, e.g. 1234", "aria-label": "Pull request number or link",
    autocomplete: "off", autocapitalize: "none", spellcheck: "false", "aria-describedby": "pr-msg" });
  const msg = h("p", { id: "pr-msg", class: "invalid", hidden: true });
  const setInvalid = (text) => {
    msg.textContent = text; msg.hidden = !text;
    if (text) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
  };
  const form = h("form", { class: "input prform", novalidate: true }, icon("link", 18), input, h("button", { class: "btn primary", type: "submit" }, "Review"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const pr = parsePrInput(input.value, owner, name);
    if (!pr) { setInvalid("Enter a pull request number like 1234, or paste its link."); input.focus(); return; }
    setInvalid("");
    requestReview(pr.owner, pr.name, pr.pull);
  });
  input.addEventListener("input", () => { if (!msg.hidden) setInvalid(""); });
  content.replaceChildren(form, msg,
    h("p", { class: "note" }, `The review uses your ${config.provider} key, which spends a little of your free quota. Nothing is posted to GitHub.`));
  if (matchMedia("(hover: hover)").matches) setTimeout(() => !signal.aborted && input.focus({ preventScroll: true }), 0);
}

/* ================================================================ #/review/o/n/num */
/** A plain card for the steps before the result: no key, "Start the review?", the progress. */
const stepCard = (title, ...rest) => h("section", { class: "progress" }, h("h2", {}, title), ...rest);

function reviewPage(content, route, onLeave) {
  const { owner, name, pull } = route;
  document.title = `Review #${pull} · ${owner}/${name} · RepoScout`;
  const url = `https://github.com/${owner}/${name}/pull/${pull}`;
  const model = () => settings.model || config.default_model;
  const status = h("p", { class: "sr-only", role: "status" });
  const body = h("div");
  content.replaceChildren(body, status);

  if (!settings.key) {
    // Start over once a key is saved, from this button or the nav (one reload, so an approval survives).
    const off = onKeyChange(() => { if (settings.key && !route.signal.aborted) reload(); });
    onLeave(off);
    body.replaceChildren(stepCard("Add your API key to start",
      h("p", {}, `Reviews use your own free ${config.provider} key. It stays in your browser.`),
      h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => openKeyDialog("") }, "Add API key"))));
    return;
  }

  const cacheKey = reviewCacheKey(owner, name, pull, settings.model);
  const runAgain = () => { store.del(cacheKey); approveReview(owner, name, pull); reload(); };
  const cached = store.get(cacheKey, true);
  if (cached && cached.pull) { consumeApproval(); paintResult(body, cached, runAgain, false); return; }

  if (!isApproved(owner, name, pull)) {
    const start = h("button", { class: "btn primary", type: "button", onclick: () => { approveReview(owner, name, pull); reload(); } }, "Start review");
    body.replaceChildren(stepCard("Start the review?",
      h("p", {}, "RepoScout will read the changes in ", h("a", { href: url, target: "_blank", rel: "noopener" }, `${owner}/${name} #${pull}`),
        ` and review them with your ${config.provider} key (model ${model()}). This uses some of your free quota.`),
      h("div", { class: "actions" }, start)));
    if (matchMedia("(hover: hover)").matches) setTimeout(() => !route.signal.aborted && start.focus({ preventScroll: true }), 0);
    return;
  }
  consumeApproval();
  run(body, status, route, onLeave, { url, cacheKey, model, runAgain });
}

async function run(body, status, route, onLeave, { url, cacheKey, model, runAgain }) {
  const { owner, name, pull } = route;
  const started = Date.now();
  const line = h("p", {}, "Fetching the pull request…");
  body.replaceChildren(stepCard(`Reviewing pull request #${pull}`, line,
    h("div", { class: "bar wait", role: "progressbar", "aria-label": "Review progress" }, h("span"))));
  status.textContent = `Reviewing pull request #${pull}`;
  body.setAttribute("aria-busy", "true");
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    line.textContent = s < 4 ? "Fetching the pull request…"
      : `Reviewing each changed file with ${model() || "the AI model"}… ${s}s. Free keys can take a minute on bigger pull requests.`;
  }, 1000);
  onLeave(() => clearInterval(timer));

  let data = null, failure = null;
  try {
    data = await api("/api/review", { method: "POST", body: { url, min_severity: "nit" }, withKey: true });
  } catch (err) {
    failure = err instanceof ApiError ? err : new ApiError(0, "network", friendlyError(err));
  }
  clearInterval(timer);
  if (data) store.set(cacheKey, data, true); // kept even if the visitor left: it cost quota
  if (route.signal.aborted) return;
  body.removeAttribute("aria-busy");
  if (data) {
    paintResult(body, data, runAgain, true);
    status.textContent = "Review ready.";
    return;
  }
  const retry = () => { approveReview(owner, name, pull); reload(); };
  body.replaceChildren(h("div", { class: "enter" }, stateBlock({ icon: "alert", tone: "error", title: "The review didn't finish", text: friendlyError(failure),
    actions: [h("button", { class: "btn primary", type: "button", onclick: retry }, "Try again"),
      h("a", { class: "btn", href: toolHash("review", owner, name) }, "Review another pull request")] })));
  status.textContent = "The review failed.";
  if (KEY_ERRORS.has(failure.code)) handleError(failure, retry);
}

/* ================================================================ result */
function findingCard(f) {
  const message = h("p");
  message.innerHTML = inline(escapeHtml(f.message || "")); // escaped first; inline() adds code, bold and https links
  const rule = String(f.source || "").replace(/^rule:/, "");
  return h("article", { class: `finding s-${f.severity}` },
    h("div", { class: "fh" }, h("span", { class: "sv" }, SEV_LABEL[f.severity] || f.severity), h("span", { class: "t" }, f.title),
      f.url ? h("a", { class: "where", href: f.url, target: "_blank", rel: "noopener" }, `line ${f.line}`) : h("span", { class: "where" }, `line ${f.line}`)),
    message,
    f.suggestion ? [h("p", { class: "fix" }, "Suggested change"), h("pre", {}, f.suggestion)] : null,
    h("div", { class: "by" }, f.source === "llm" ? [icon("bot", 14), `AI review · ${f.category}`] : [icon("shield", 14), `Fixed check (${rule}) · ${f.category}`]));
}

function paintResult(body, data, runAgain, fresh) {
  const pr = data.pull;
  const [prOwner, prName] = String(pr.repo || "").split("/");
  const findings = Array.isArray(data.findings) ? data.findings : [];
  const reviewed = Array.isArray(data.files_reviewed) ? data.files_reviewed : [];
  const summaries = data.file_summaries || {};
  const counts = data.counts || {};
  const total = SEVERITIES.reduce((a, s) => a + (counts[s] || 0), 0);
  const active = defaultSeverities(counts);

  const head = h("div", { class: "prhead" }, h("h2", {}, pr.title || `Pull request #${pr.number}`),
    h("div", { class: "prmeta" },
      pr.state ? h("span", { class: `pill ${STATE_TONE[pr.state] || ""}` }, pr.state[0].toUpperCase() + pr.state.slice(1)) : null,
      h("span", {}, pr.author ? `#${pr.number} by ${pr.author}` : `#${pr.number}`),
      h("span", {}, plural(pr.changed_files || 0, "file")),
      h("span", { class: "mono" }, h("span", { class: "plus" }, `+${fmt(pr.additions)}`), " ", h("span", { class: "minus" }, `−${fmt(pr.deletions)}`)),
      h("a", { href: pr.url, target: "_blank", rel: "noopener" }, "Open on GitHub", icon("ext", 12))));

  const copy = h("button", { class: "btn", type: "button" }, icon("copy", 14), "Copy as Markdown");
  copy.addEventListener("click", () => {
    const done = () => toast("Review copied as Markdown. Paste it into a GitHub comment.", { kind: "success" });
    const fail = () => toast("Couldn't copy. Select the text and copy it instead.", { kind: "error" });
    if (navigator.clipboard) navigator.clipboard.writeText(data.markdown || "").then(done, fail); else fail();
  });
  const again = h("button", { class: "btn", type: "button", onclick: runAgain }, icon("refresh", 14), "Run again");

  const chips = SEVERITIES.filter((s) => counts[s]).map((s) => {
    const chip = h("button", { class: `sev s-${s}`, type: "button", "aria-pressed": String(active.has(s)) }, `${fmt(counts[s])} ${SEV_LABEL[s]}`);
    chip.addEventListener("click", () => { if (active.has(s)) active.delete(s); else active.add(s); sync(); });
    return { s, chip };
  });
  const verdict = h("div", { class: "verdict" },
    total ? null : h("span", { class: "okmark" }, icon("check", 16)),
    h("strong", {}, total ? `${plural(total, "thing")} to look at` : "No problems found"),
    h("span", { class: "note" }, `${plural(reviewed.length, "file")} reviewed`),
    h("div", { class: "act" }, copy, again),
    total ? h("div", { class: "chips", role: "group", "aria-label": "Filter by severity" }, chips.map((c) => c.chip)) : null);

  const notes = [];
  if (!data.used_repo_context && prOwner && prName) {
    notes.push(h("div", { class: "banner" }, icon("info", 16), h("span", {}, "Tip: ",
      h("a", { href: toolHash("ask", prOwner, prName) }, `index ${pr.repo}`), " first, and reviews will also read related code from the project.")));
  }
  const skipped = Object.entries(data.files_skipped || {});
  if (skipped.length) notes.push(h("p", { class: "note" }, `Not reviewed: ${skipped.map(([p, why]) => `${p} (${why})`).join(", ")}`));
  if (data.stats && data.stats.llm_errors) {
    notes.push(h("div", { class: "banner error" }, icon("alert", 16),
      h("span", {}, `The AI couldn't review ${data.stats.llm_errors} file(s), usually because of rate limits. Fixed checks still ran. Try "Run again" in a minute.`)));
  }

  const list = h("div", { class: "fgroups" });
  function paintList() {
    const shown = findings.filter((f) => active.has(f.severity));
    const byFile = {};
    for (const f of shown) (byFile[f.path] ||= []).push(f);
    const files = shown.length || !total ? reviewed.filter((p) => byFile[p] || summaries[p]) : [];
    list.replaceChildren(...files.map((path) => {
      const found = (byFile[path] || []).slice().sort((a, b) => a.line - b.line);
      return h("section", { class: "fgroup" },
        h("h3", {}, h("span", {}, path), found.length ? h("span", { class: "pill" }, fmt(found.length)) : null),
        summaries[path] ? h("p", {}, summaries[path]) : null,
        found.map(findingCard));
    }));
    if (!shown.length && total) {
      const all = h("button", { class: "btn", type: "button", onclick: () => { SEVERITIES.forEach((x) => active.add(x)); sync(); chips[0]?.chip.focus(); } }, "Show all findings");
      list.append(stateBlock({ icon: "search", title: "Every finding is filtered out", text: "Turn a severity back on above, or show them all.", actions: [all] }));
    }
  }
  function sync() {
    for (const { s, chip } of chips) chip.setAttribute("aria-pressed", String(active.has(s)));
    paintList();
  }
  paintList();
  body.replaceChildren(h("div", { class: fresh ? "result enter" : "result" }, head, verdict, notes.length ? h("div", { class: "notes" }, notes) : null, list));
}
