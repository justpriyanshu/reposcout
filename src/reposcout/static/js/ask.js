// Ask view: chat about one indexed repository. Answers are Markdown with [n] citations that link to GitHub.
import { ApiError, api, copyButton, friendlyError, h, icon, store } from "./core.js";
import { markdown } from "./markdown.js";
import { handleError, requireKey } from "./settings.js";
import { mountTool } from "./repo.js";

const STARTERS = [
  "What does this project do, and how is it organised?",
  "Where is the main entry point?",
  "How is configuration loaded?",
  "How are errors handled?",
];
const KEY_ERRORS = new Set(["no_key", "bad_key", "bad_model"]);
const SLOW_NOTE = "Still working. Free keys can take a little longer.";
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The answer as Markdown with each known [n] in the text turned into a badge that links to the code. */
function renderAnswer(text, sources) {
  const wrap = h("div", { class: "md" });
  wrap.innerHTML = markdown(text); // markdown() escapes everything first
  const byRef = Object.fromEntries((sources || []).map((s) => [s.ref, s]));
  const walker = document.createTreeWalker(wrap, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (!walker.currentNode.parentElement.closest("pre, code")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (!/\[\d+\]/.test(node.nodeValue)) continue;
    const frag = document.createDocumentFragment();
    node.nodeValue.split(/(\[\d+\])/).forEach((part) => {
      const m = part.match(/^\[(\d+)\]$/);
      const s = m && byRef[m[1]];
      if (s) frag.append(h("a", { class: "cite", href: s.url, target: "_blank", rel: "noopener", title: `${s.path}:${s.start_line}-${s.end_line}` }, m[1]));
      else frag.append(part);
    });
    node.replaceWith(frag);
  }
  return wrap;
}

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: true });
  if (!page || route.signal.aborted) return;
  const { content, record } = page;
  const { owner, name } = route;
  const chatKey = `rs.chat.${record.key || `${owner}/${name}`}`;
  const saved = store.get(chatKey);
  let turns = Array.isArray(saved) ? saved.filter((t) => t && typeof t.question === "string") : [];
  let fresh = null;
  const timers = new Set();
  onLeave(() => timers.forEach(clearTimeout));

  const thread = h("div", { class: "thread" });
  const status = h("div", { class: "sr-only", role: "status" });
  const textarea = h("textarea", { rows: "1", placeholder: `Ask about ${name}…`, "aria-label": "Your question", maxlength: "2000" });
  const sendBtn = h("button", { class: "btn primary icon", type: "submit", "aria-label": "Send" }, icon("arrow", 18));
  const clearBtn = h("button", { class: "btn quiet", type: "button", onclick: clear }, "Clear chat");
  const form = h("form", { class: "input", onsubmit: (e) => { e.preventDefault(); send(textarea.value); } }, textarea, sendBtn);
  const hint = h("div", { class: "hint" },
    h("span", { class: "keys" }, h("kbd", {}, "Enter"), " to send · ", h("kbd", {}, "Shift"), " + ", h("kbd", {}, "Enter"), " for a new line"), clearBtn);

  const busy = () => turns.some((t) => t.pending);
  const grow = () => { textarea.style.height = "auto"; textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`; };
  const sync = () => { sendBtn.disabled = !textarea.value.trim() || busy(); clearBtn.hidden = !turns.length; };
  textarea.addEventListener("input", () => { grow(); sync(); });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  content.replaceChildren(h("div", { class: "chat" }, thread, h("div", { class: "composer" }, form, hint)), status);

  const persist = () => store.set(chatKey, turns.filter((t) => !t.pending).slice(-20).map(({ question, answer, error, sources, cited, model, latency_ms }) =>
    ({ question, answer, error, sources, cited, model, latency_ms })));

  function answerCard(turn) {
    if (turn.pending) {
      return h("div", { class: "a" }, h("div", { class: "pending", role: "status" },
        h("span", { class: "lbl" }, "Reading the code…"),
        h("span", { class: "skel", style: "width:90%" }), h("span", { class: "skel", style: "width:74%" }), h("span", { class: "skel", style: "width:58%" }),
        turn.slow ? h("span", { class: "lbl" }, SLOW_NOTE) : null));
    }
    if (turn.error) {
      return h("div", { class: "banner error", role: "alert" }, icon("alert", 16), h("span", {}, turn.error),
        h("button", { class: "btn", type: "button", onclick: () => { turns.splice(turns.indexOf(turn), 1); paint(); send(turn.question); } }, "Try again"));
    }
    const cited = new Set(turn.cited || []);
    const card = h("div", { class: "a" }, renderAnswer(turn.answer, turn.sources));
    if (turn.sources && turn.sources.length) {
      card.append(h("div", { class: "srcs", "aria-label": "Sources" }, ...turn.sources.map((s) => {
        const isCited = cited.has(s.ref);
        return h("a", { class: isCited ? "src" : "src uncited", href: s.url, target: "_blank", rel: "noopener",
          title: `${s.name ? `${s.kind} ${s.name}` : s.path}${isCited ? "" : " (not cited)"}` },
          h("span", { class: "n" }, s.ref), h("span", {}, `${s.path}:${s.start_line}-${s.end_line}`));
      })));
    }
    return h("div", {}, card, h("div", { class: "afoot" },
      turn.model ? h("span", {}, turn.model) : null,
      turn.latency_ms ? h("span", {}, `${(turn.latency_ms / 1000).toFixed(1)}s`) : null,
      copyButton(() => turn.answer)));
  }

  function paint() {
    thread.replaceChildren();
    if (!turns.length) {
      thread.append(h("div", {},
        h("div", { class: "state", style: "padding-bottom:0" }, h("h3", {}, `Ask anything about ${name}`),
          h("p", {}, "Answers come from the indexed code and link to the exact lines on GitHub.")),
        h("div", { class: "starters" }, ...STARTERS.map((q) => h("button", { class: "starter", type: "button", onclick: () => send(q) }, q)))));
    }
    for (const t of turns) thread.append(h("div", { class: t === fresh ? "ex enter" : "ex" }, h("div", { class: "q" }, t.question), answerCard(t)));
    sync();
  }

  function clear() {
    turns = []; persist(); paint(); textarea.focus();
  }

  async function send(text) {
    const question = (text || "").trim();
    if (question.length < 2 || busy()) return;
    if (!requireKey(() => send(question))) return; // the draft stays in the box
    const history = turns.filter((t) => !t.error && !t.pending).slice(-3).map((t) => ({ question: t.question, answer: t.answer }));
    const turn = { question, pending: true };
    turns.push(turn);
    textarea.value = ""; grow(); paint();
    thread.lastElementChild?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest" });
    const slow = setTimeout(() => {
      turn.slow = true;
      const box = thread.lastElementChild?.querySelector(".pending");
      if (!route.signal.aborted && turn.pending && box) box.append(h("span", { class: "lbl" }, SLOW_NOTE));
    }, 8000);
    timers.add(slow);
    let failure = null;
    try {
      const data = await api(`/api/repos/${owner}/${name}/ask`, { method: "POST", body: { question, history }, withKey: true });
      Object.assign(turn, { pending: false, answer: data.answer, sources: data.sources, cited: data.cited, model: data.model, latency_ms: data.latency_ms });
    } catch (err) {
      failure = err instanceof ApiError ? err : new ApiError(0, "network", friendlyError(err));
    }
    clearTimeout(slow); timers.delete(slow);
    const kept = turns.includes(turn); // Clear chat during the request drops it
    if (failure && KEY_ERRORS.has(failure.code)) {
      if (kept) turns.splice(turns.indexOf(turn), 1);
      if (route.signal.aborted) return;
      paint(); textarea.value = question; grow(); sync();
      handleError(failure, () => send(question));
      return;
    }
    if (failure) Object.assign(turn, { pending: false, error: friendlyError(failure) });
    if (!kept) return;
    persist();
    if (route.signal.aborted) return; // never write to a page we left
    fresh = turn; paint(); fresh = null;
    status.textContent = turn.error ? "The answer failed." : "Answer ready.";
    textarea.focus({ preventScroll: true });
    // Bring the question to the top so the answer reads from its first line.
    thread.lastElementChild?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  }

  paint();
  if (matchMedia("(hover: hover)").matches) setTimeout(() => !route.signal.aborted && textarea.focus({ preventScroll: true }), 0);
}
