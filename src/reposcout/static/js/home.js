// The tools home (spec §6.3): hero, the three live tools, a tool filter, categories, and recent repositories.
import { h, icon, toolIcon, api, skel, stateBlock, fmt } from "./core.js";
import { TOOLS, filterTools } from "./tools.js";
import { toolHash } from "./links.js";

const LIVE = TOOLS.filter((t) => t.live);

function tile(t) {
  return h("a", { class: "tile", href: `#/${t.id}` }, toolIcon(t, "lg"), h("h3", {}, t.name), h("p", {}, t.description),
    h("span", { class: "go" }, "Open ", icon("arrow", 14)));
}

function card(t) {
  const body = (extra) => h("span", {}, h("b", {}, t.name, extra), h("span", { class: "d" }, t.description));
  return t.live
    ? h("a", { class: "card", href: `#/${t.id}` }, toolIcon(t, "sm"), body())
    : h("div", { class: "card soon", "aria-disabled": "true" }, toolIcon(t, "sm"), body(h("span", { class: "pill" }, "Soon")));
}

function categories(query) {
  const groups = filterTools(query);
  if (!groups.length) return [h("p", { class: "empty-search" }, `No tool matches “${query.trim()}”. Try “review” or “search”.`)];
  return groups.map(([name, tools]) => h("section", { class: "cat" }, h("h2", {}, name), h("div", { class: "cards" }, tools.map(card))));
}

function recentRow(r) {
  const failed = r.status === "failed";
  const desc = r.description
    || (r.ready ? `${fmt(r.files_total)} files indexed`
      : failed ? "Indexing failed"
      : r.files_total ? `${r.stage || "Indexing"} (${fmt(r.files_done)} of ${fmt(r.files_total)})` : (r.stage || "Waiting to start"));
  const cls = r.ready ? "ok" : failed ? "bad" : "busy";
  return h("a", { class: "row", href: toolHash("ask", r.owner, r.name) },
    h("span", { class: "nm" }, r.full_name), h("span", { class: "ds" }, desc),
    h("span", { class: `pill ${cls}` }, h("span", { class: "dot" }), r.ready ? "Ready" : failed ? "Failed" : "Indexing"));
}

function loadRecent(box, signal) {
  box.setAttribute("aria-busy", "true");
  box.replaceChildren(...[0, 1, 2].map(() => h("div", { class: "row", "aria-hidden": "true" }, skel("w-60"), skel("w-80"))));
  api("/api/repos").then(({ repos }) => {
    if (signal.aborted) return;
    box.removeAttribute("aria-busy");
    if (!repos || !repos.length) {
      box.replaceChildren(stateBlock({ icon: "search", title: "Nothing explored yet",
        text: "Open Ask or Find code and add a repository, for example pallets/flask; it will show up here for everyone." }));
      return;
    }
    box.replaceChildren(...repos.map(recentRow));
  }).catch(() => {
    if (signal.aborted) return;
    box.removeAttribute("aria-busy");
    box.replaceChildren(stateBlock({ icon: "alert", tone: "error", title: "Couldn't load recent repositories",
      text: "Check your connection and try again.",
      actions: [h("button", { class: "btn", type: "button", onclick: () => loadRecent(box, signal) }, icon("refresh", 14), "Try again")] }));
  });
}

export function render(main, route) {
  const list = h("div", { "aria-live": "polite" }, categories(""));
  const input = h("input", { type: "search", placeholder: `Search ${TOOLS.length} tools`, "aria-label": "Search tools", autocomplete: "off",
    oninput: () => list.replaceChildren(...categories(input.value)) });
  const recent = h("div", { class: "rows", style: "margin-top:var(--s-4)" });

  main.append(h("div", { class: "wrap" },
    h("section", { class: "hero" },
      h("span", { class: "tag" }, "Free · bring your own key · no sign-up"),
      h("h1", {}, "Tools for understanding and reviewing GitHub code"),
      h("p", {}, "Ask how a repository works, find code by meaning, and review pull requests. Pick a tool to start.")),
    h("section", { class: "panel", "aria-label": "Popular tools" }, h("div", { class: "tiles" }, LIVE.map(tile))),
    h("div", { class: "search" }, h("form", { class: "input", role: "search", onsubmit: (e) => e.preventDefault() }, icon("search", 18), input)),
    list,
    h("section", { class: "split" },
      h("div", {}, h("h2", {}, "Private by design"),
        h("p", {}, "RepoScout reads public code only, and you bring your own free Groq key."),
        h("ul", { class: "checks" },
          ["Your key stays in your browser and is sent only with your own requests.",
            "Answers link to the exact lines on GitHub, so you can check them.",
            "Find code works with no key at all."].map((s) => h("li", {}, icon("check", 14), s)))),
      h("div", {}, h("h2", {}, "Recently explored"),
        h("p", {}, "Repositories other people have already indexed. Open one to skip the wait."), recent))));
  loadRecent(recent, route.signal);
}
