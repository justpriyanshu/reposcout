// Find code view: search one indexed repository. Needs no API key.
import { ApiError, api, codeBlock, fileLoc, friendlyError, h, icon, skel, stateBlock } from "./core.js";
import { matchedLines } from "./matched.js";
import { mountTool, repoPath } from "./repo.js";

export { matchedLines };

const skeletons = () => [0, 1].map(() => h("div", { class: "hit", "aria-hidden": "true" },
  h("header", {}, skel("w-60")),
  h("div", { class: "hit-skel" }, skel(), skel("w-80"), skel("w-60"))));

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: true });
  if (!page || route.signal.aborted) return;
  const { content } = page;
  const { owner, name } = route;
  let seq = 0;

  const input = h("input", { type: "search", placeholder: "Function names, keywords, or a description", "aria-label": "Search the code", maxlength: "300", autocomplete: "off" });
  const button = h("button", { class: "btn primary", type: "submit" }, "Search");
  const form = h("form", { class: "input find", role: "search", onsubmit: (e) => { e.preventDefault(); run(); } }, icon("search", 18), input, button);
  const hits = h("div", { class: "hits", "aria-live": "polite" });
  content.replaceChildren(form, h("p", { class: "note" }, "Works without an API key. Matches exact words and meaning."), hits);

  function card(x, query) {
    const label = x.name ? `${x.kind} ${x.name}` : x.language;
    return h("article", { class: "hit" },
      h("header", {}, h("a", { href: x.url, target: "_blank", rel: "noopener", title: `${x.path}:${x.start_line}-${x.end_line}` }, fileLoc(x.path, x.start_line, x.end_line)),
        label ? h("span", { class: "pill", title: label }, label) : null),
      codeBlock(x.text, x.start_line, 14, matchedLines(x.text, query, x.start_line)));
  }

  async function run() {
    const query = input.value.trim();
    if (!query) return;
    const mine = ++seq;
    button.disabled = true;
    hits.setAttribute("aria-busy", "true");
    hits.replaceChildren(...skeletons());
    let result = null, failure = null;
    try {
      result = await api(`${repoPath(owner, name)}/search`, { method: "POST", body: { query } });
    } catch (err) {
      failure = err instanceof ApiError ? err : new ApiError(0, "network", friendlyError(err));
    }
    if (route.signal.aborted || mine !== seq) return; // left the page, or a newer search took over
    button.disabled = false;
    hits.removeAttribute("aria-busy");
    if (failure) {
      hits.replaceChildren(stateBlock({ icon: "alert", tone: "error", title: "Search didn't work", text: friendlyError(failure),
        actions: [h("button", { class: "btn", type: "button", onclick: run }, "Try again")] }));
    } else if (!result.results.length) {
      hits.replaceChildren(stateBlock({ icon: "search", title: `No matches for “${query}”`,
        text: "Try a function or file name, or describe what the code does in other words." }));
    } else {
      hits.replaceChildren(...result.results.map((x) => card(x, query)));
    }
  }

  if (matchMedia("(hover: hover)").matches) setTimeout(() => !route.signal.aborted && input.focus({ preventScroll: true }), 0);
}
