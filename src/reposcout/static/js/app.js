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
let controller = null;
const onLeave = (fn) => { if (typeof fn === "function") cleanups.push(fn); };

/** Abort the old view's requests first (route.signal), then run its cleanups. */
function leave() {
  controller?.abort();
  controller = null;
  const fns = cleanups; cleanups = [];
  for (const fn of fns) { try { fn(); } catch (err) { console.error(err); } }
}

function titleFor(route) {
  if (route.view === "home") return "RepoScout: free tools for GitHub repositories";
  const name = tool(route.tool).name;
  return route.owner ? `${name} · ${route.owner}/${route.name} · RepoScout` : `${name} · RepoScout`;
}

function showFailure(main, err) {
  console.error(err);
  main.removeAttribute("aria-busy");
  main.replaceChildren(h("div", { class: "wrap" }, stateBlock({ icon: "alert", tone: "error", title: "This page didn't load",
    text: "Something went wrong on our side, or RepoScout was just updated. Reload to try again.",
    actions: [h("button", { class: "btn", type: "button", onclick: () => location.reload() }, "Reload")] })));
}

async function render(navigated) {
  const redirect = legacyRedirect(location.hash);
  if (redirect) { location.replace(redirect); return; } // fires hashchange; Back skips the old link
  leave();
  const ticket = ++current;
  controller = new AbortController();
  const signal = controller.signal;
  // Views check route.signal.aborted after each await: it is aborted on the next navigation.
  const route = { ...parseRoute(location.hash), signal };
  const main = $("#view");
  setCurrentTool(route.view === "tool" ? route.tool : null);
  document.title = titleFor(route);
  main.setAttribute("aria-busy", "true");
  if (navigated) { main.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  let mod;
  try {
    mod = await VIEWS[route.view === "tool" ? route.tool : "home"]();
  } catch (err) {
    if (ticket === current) showFailure(main, err);
    return;
  }
  if (ticket !== current) return; // a newer navigation won
  main.removeAttribute("aria-busy");
  main.replaceChildren();
  try {
    const result = mod.render(main, route, onLeave);
    if (result && typeof result.then === "function") {
      result.then(
        // A cleanup that arrives after the visitor has left runs at once instead of joining the next view's.
        (fn) => { if (!signal.aborted) onLeave(fn); else if (typeof fn === "function") fn(); },
        (err) => { if (!signal.aborted) showFailure(main, err); });
    } else {
      onLeave(result);
    }
  } catch (err) {
    showFailure(main, err);
  }
}

applyTheme();
mountNav();
// A button, not an href="#view" link: in a hash router a fragment link would navigate home.
$("#skipLink").addEventListener("click", () => $("#view").focus());
loadConfig();
window.addEventListener("hashchange", () => render(true));
render(false);
