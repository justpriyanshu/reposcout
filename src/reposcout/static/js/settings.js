// The visitor's own API key (BYOK): storage, the key dialog, and what happens after a key error.
// The key lives in localStorage when remembered, sessionStorage otherwise, and is only ever sent
// in the X-LLM-Key header of the visitor's own requests.

import { $, api, friendlyError, h, setCredentials, store, toast } from "./core.js";

const listeners = new Set();
const changed = () => listeners.forEach((fn) => fn());

/** Call fn whenever the key or the provider config changes. Returns an unsubscribe function. */
export function onKeyChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export const settings = {
  get key() { return store.get("rs.key") || store.get("rs.key", true) || ""; },
  get model() { return store.get("rs.model") || store.get("rs.model", true) || ""; },
  get remember() { return !!store.get("rs.key"); },
  save(key, model, remember) {
    store.del("rs.key"); store.del("rs.model");
    if (key) { store.set("rs.key", key, !remember); store.set("rs.model", model, !remember); }
    changed();
  },
};
setCredentials(() => ({ key: settings.key, model: settings.model }));

/** Provider details from /api/config; defaults until it loads. */
export const config = { provider: "Groq", key_url: "https://console.groq.com/keys", default_model: "" };
export async function loadConfig() {
  try { Object.assign(config, await api("/api/config")); } catch { /* keep the defaults */ }
  document.querySelectorAll("[data-provider]").forEach((el) => { el.textContent = config.provider; });
  const link = $("#keyLink"); if (link) link.href = config.key_url;
  changed();
}

/* ================================================================ dialog */
let afterSave = null;
let wired = false;

function setMessage(text, tone = "") {
  const msg = $("#keyMsg");
  msg.textContent = text || "";
  msg.className = tone ? `field-msg ${tone}` : "field-msg";
}

function setShown(show) {
  $("#keyInput").type = show ? "text" : "password";
  const btn = $("#toggleKey");
  btn.textContent = show ? "Hide" : "Show";
  btn.setAttribute("aria-label", show ? "Hide key" : "Show key");
}

async function loadModels() {
  const key = $("#keyInput").value.trim();
  if (!key) { setMessage("Paste your key first.", "err"); $("#keyInput").focus(); return false; }
  setMessage("Checking…");
  const check = $("#checkKey"); check.disabled = true;
  try {
    const data = await api("/api/models", { method: "POST", key });
    const want = settings.model || data.default;
    $("#modelSelect").replaceChildren(...data.models.map((m) => h("option", { value: m, selected: m === want }, m)));
    setMessage(`Key works. ${data.models.length} models available.`, "ok");
    return true;
  } catch (err) {
    setMessage(friendlyError(err), "err");
    return false;
  } finally {
    check.disabled = false;
  }
}

async function save() {
  const key = $("#keyInput").value.trim();
  if (key && !$("#modelSelect").value && !(await loadModels())) return;
  const next = afterSave; afterSave = null;
  settings.save(key, $("#modelSelect").value, $("#rememberKey").checked);
  $("#keyDialog").close();
  if (key) toast("Key saved.", { kind: "success" });
  if (key && next) next();
}

function remove() {
  settings.save("", "", false);
  $("#keyInput").value = "";
  afterSave = null;
  $("#keyDialog").close();
  toast("Key removed from this browser.");
}

function wire() {
  if (wired) return;
  wired = true;
  $("#toggleKey").addEventListener("click", () => setShown($("#keyInput").type === "password"));
  $("#checkKey").addEventListener("click", loadModels);
  $("#forgetKey").addEventListener("click", remove);
  $("#cancelKey").addEventListener("click", () => $("#keyDialog").close());
  // Save is the form's submit button, so Enter in the key field saves.
  $("#keyForm").addEventListener("submit", (e) => { e.preventDefault(); save(); });
  // A newly pasted key invalidates the models loaded for the previous one.
  $("#keyInput").addEventListener("input", () => {
    $("#modelSelect").replaceChildren(h("option", { value: "" }, "Check your key to load models"));
    setMessage("");
  });
  $("#keyDialog").addEventListener("close", () => setShown(false));
}

/** Open the key dialog. `message` explains why (shown as an error); `then` runs after a successful save. */
export function openKeyDialog(message, then) {
  wire();
  const dlg = $("#keyDialog");
  afterSave = then || null;
  $("#keyInput").value = settings.key;
  $("#rememberKey").checked = settings.remember;
  $("#modelSelect").replaceChildren(h("option", { value: settings.model || "" }, settings.model || "Check your key to load models"));
  setMessage(message || "", message ? "err" : "");
  setShown(false);
  $("#forgetKey").hidden = !settings.key;
  if (!dlg.open) dlg.showModal();
  $("#keyInput").focus();
}

/** True when a key is saved; otherwise opens the dialog (running `then` after a save) and returns false. */
export function requireKey(then) {
  if (settings.key) return true;
  openKeyDialog("", then);
  return false;
}

const KEY_ERRORS = new Set(["no_key", "bad_key", "bad_model"]);

/** Key errors reopen the dialog with the server's message (then `retry` after a save); anything else is a toast. */
export function handleError(err, retry) {
  if (KEY_ERRORS.has(err?.code)) openKeyDialog(err.message, retry);
  else toast(friendlyError(err), { kind: "error", action: retry ? { label: "Retry", run: retry } : null });
}
