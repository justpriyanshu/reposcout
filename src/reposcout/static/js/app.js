// Start-up and the router: old-link redirects, lazy views, focus and titles on navigation.

import { $, h, stateBlock } from "./core.js";
import { applyTheme, mountNav, setCurrentTool } from "./nav.js";
import { legacyRedirect, parseRoute } from "./routes.js";
import { loadConfig } from "./settings.js";
import { tool } from "./tools.js";

// Each view module exports render(main, route, onLeave) and may return a cleanup function.
const VIEWS = {
  home: () => import("./home.js"),
  ask: () => import("./ask.js"),
  find: () => import("./find.js"),
  review: () => import("./review.js"),
};

let cleanups = [];
let current = 0;
const onLeave = (fn) => { if (typeof fn === "function") cleanups.push(fn); };

function leave() {
  const fns = cleanups; cleanups = [];
  for (const fn of fns) { try { fn(); } catch (err) { console.error(err); } }
}

function titleFor(route) {
  if (route.view === "home") return "RepoScout: free tools for GitHub repositories";
  const name = tool(route.tool).name;
  return route.owner ? `${name} · ${route.owner}/${route.name} · RepoScout` : `${name} · RepoScout`;
}

async function render(navigated) {
  const redirect = legacyRedirect(location.hash);
  if (redirect) { location.replace(redirect); return; } // fires hashchange; Back skips the old link
  leave();
  const ticket = ++current;
  const route = parseRoute(location.hash);
  const main = $("#view");
  setCurrentTool(route.view === "tool" ? route.tool : null);
  document.title = titleFor(route);
  main.setAttribute("aria-busy", "true");
  if (navigated) { main.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  let mod;
  try {
    mod = await VIEWS[route.view === "tool" ? route.tool : "home"]();
  } catch (err) {
    if (ticket !== current) return;
    console.error(err);
    main.removeAttribute("aria-busy");
    main.replaceChildren(h("div", { class: "wrap" }, stateBlock({ icon: "alert", tone: "error", title: "This page didn't load",
      text: "RepoScout may have just been updated. Reload to get the latest version.",
      actions: [h("button", { class: "btn", type: "button", onclick: () => location.reload() }, "Reload")] })));
    return;
  }
  if (ticket !== current) return; // a newer navigation won
  main.removeAttribute("aria-busy");
  main.replaceChildren();
  const cleanup = mod.render(main, route, onLeave);
  onLeave(cleanup);
}

applyTheme();
mountNav();
loadConfig();
window.addEventListener("hashchange", () => render(true));
render(false);
