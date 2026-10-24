// The top bar (spec §6.2): All tools menu, direct tool links, key status and theme.

import { $, h, icon, store, toolIcon } from "./core.js";
import { config, onKeyChange, openKeyDialog, settings } from "./settings.js";
import { CATEGORIES, TOOLS } from "./tools.js";

/* ================================================================ theme */
const systemDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => {
  const chosen = store.get("rs.theme");
  return chosen ? chosen === "dark" : systemDark.matches;
};

/** Apply the saved theme ("light" | "dark" in rs.theme; absent = follow the system) and update the theme button. */
export function applyTheme() {
  const chosen = store.get("rs.theme");
  if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
  else document.documentElement.removeAttribute("data-theme");
  const btn = $("#themeBtn");
  if (btn) {
    const dark = isDark();
    btn.replaceChildren(icon(dark ? "sun" : "moon", 18));
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
}

function toggleTheme() {
  store.set("rs.theme", isDark() ? "light" : "dark");
  applyTheme();
}

/* ================================================================ key status */
function paintKey() {
  const has = !!settings.key;
  $("#keyDot").classList.toggle("off", !has);
  $("#keyLabel").textContent = has ? `${config.provider} key` : "Add API key";
  $("#keyBtn").setAttribute("aria-label", has ? `${config.provider} key settings` : "Add your API key");
}

/* ================================================================ All tools menu */
let menu = null;
let currentTool = null;
let opener = null;

function menuButtons() { return document.querySelectorAll("[data-menu-btn]"); }
function liveItems() { return menu ? [...menu.querySelectorAll("a[href]")] : []; }

function buildMenu() {
  return h("nav", { class: "menu", id: "toolsMenu", "aria-label": "All tools" },
    ...CATEGORIES.map((cat) => h("div", {},
      h("h4", {}, cat),
      ...TOOLS.filter((t) => t.category === cat).map((t) => t.live
        // aria-current here too: on phones the direct links that carry it are hidden.
        ? h("a", { href: `#/${t.id}`, "data-nav": t.id, "aria-current": t.id === currentTool ? "page" : null }, toolIcon(t, "sm"), t.name)
        : h("a", { class: "soon", "aria-disabled": "true" }, toolIcon(t, "sm"), t.name, h("span", { class: "pill" }, "Soon"))))));
}

function openMenu(btn) {
  if (menu) return;
  opener = btn;
  menu = buildMenu();
  menu.addEventListener("keydown", onMenuKey);
  // Picking the tool already open changes no hash, so close on the click itself.
  menu.addEventListener("click", (e) => { if (e.target.closest("a[href]")) closeMenu(); });
  $("#navpos").append(menu);
  menuButtons().forEach((b) => b.setAttribute("aria-expanded", "true"));
  document.addEventListener("pointerdown", onOutside, true);
  liveItems()[0]?.focus();
}

/** Close the menu; `restoreFocus` returns focus to the button that opened it (Esc). */
function closeMenu(restoreFocus = false) {
  if (!menu) return;
  menu.remove(); menu = null;
  menuButtons().forEach((b) => b.setAttribute("aria-expanded", "false"));
  document.removeEventListener("pointerdown", onOutside, true);
  if (restoreFocus) opener?.focus();
  opener = null;
}

function onOutside(e) {
  if (menu && !menu.contains(e.target) && !e.target.closest("[data-menu-btn]")) closeMenu();
}

function onMenuKey(e) {
  const items = liveItems();
  const i = items.indexOf(document.activeElement);
  const go = (n) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
  if (e.key === "ArrowDown") go(i + 1);
  else if (e.key === "ArrowUp") go(i - 1);
  else if (e.key === "Home") go(0);
  else if (e.key === "End") go(items.length - 1);
}

/* ================================================================ mount */
/** Wire the static nav markup in app.html. Call once at start-up. */
export function mountNav() {
  menuButtons().forEach((btn) => btn.addEventListener("click", () => (menu ? closeMenu() : openMenu(btn))));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu) { e.preventDefault(); closeMenu(true); } });
  // Leaving the menu by Tab closes it; so does any navigation.
  document.addEventListener("focusin", (e) => { if (menu && !menu.contains(e.target) && !e.target.closest("[data-menu-btn]")) closeMenu(); });
  window.addEventListener("hashchange", () => closeMenu());
  $("#keyBtn").addEventListener("click", () => openKeyDialog());
  $("#themeBtn").addEventListener("click", toggleTheme);
  systemDark.addEventListener("change", applyTheme);
  onKeyChange(paintKey);
  applyTheme();
  paintKey();
}

/** Mark the current tool's direct link and menu item with aria-current (null on home). */
export function setCurrentTool(id) {
  currentTool = id;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === id) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
