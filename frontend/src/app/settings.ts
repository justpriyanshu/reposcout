// The visitor's own API key (BYOK): storage, provider config, the key dialog's state, and what
// happens after a key error. The key lives in localStorage (rs.key, rs.model) when remembered,
// sessionStorage otherwise, and is only ever sent in the X-LLM-Key header of the visitor's own requests.
import { useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { ApiError, KEY_ERRORS, api, friendlyError, setCredentials } from "@/lib/api";
import { store } from "@/lib/store";
import { toast } from "@/lib/toast";
import { queryClient } from "./queryClient";

/* ================================================================ key storage */
const keyListeners = new Set<() => void>();
const keyChanged = () => keyListeners.forEach((fn) => fn());

/** Call fn whenever the key changes. Returns an unsubscribe function. */
export function onKeyChange(fn: () => void): () => void {
  keyListeners.add(fn);
  return () => { keyListeners.delete(fn); };
}

export const settings = {
  get key(): string { return store.get<string>("rs.key") || store.get<string>("rs.key", true) || ""; },
  get model(): string { return store.get<string>("rs.model") || store.get<string>("rs.model", true) || ""; },
  get remember(): boolean { return !!store.get("rs.key"); },
  save(key: string, model: string, remember: boolean): void {
    store.del("rs.key"); store.del("rs.model");
    if (key) { store.set("rs.key", key, !remember); store.set("rs.model", model, !remember); }
    keyChanged();
  },
};
setCredentials(() => ({ key: settings.key, model: settings.model }));

/** The saved key ("" when none), re-rendering when it changes. */
export const useSavedKey = (): string => useSyncExternalStore(onKeyChange, () => settings.key);

/* ================================================================ provider config */
export interface Config { provider: string; key_url: string; default_model: string }
export const DEFAULT_CONFIG: Config = { provider: "Groq", key_url: "https://console.groq.com/keys", default_model: "" };
const configQuery = { queryKey: ["config"], queryFn: () => api<Partial<Config>>("/api/config"), staleTime: Infinity };

/** Provider details from /api/config; the defaults until it loads (or if it fails). */
export function useConfig(): Config {
  const { data } = useQuery(configQuery);
  return { ...DEFAULT_CONFIG, ...data };
}

/** The same, outside React. */
export const currentConfig = (): Config => ({ ...DEFAULT_CONFIG, ...queryClient.getQueryData<Partial<Config>>(configQuery.queryKey) });

/* ================================================================ key dialog state */
export interface KeyDialogState {
  open: boolean;
  /** Why the dialog opened (shown as an error), or "". */
  message: string;
  /** Runs after a successful save. */
  then: (() => void) | null;
  /** Bumped on every open, so the form resets. */
  seq: number;
  /** What had focus when the dialog opened; focus goes back there when it closes. */
  opener: HTMLElement | null;
}

let dialog: KeyDialogState = { open: false, message: "", then: null, seq: 0, opener: null };
const dialogListeners = new Set<() => void>();
const setDialog = (next: KeyDialogState) => { dialog = next; dialogListeners.forEach((fn) => fn()); };

export function useKeyDialog(): KeyDialogState {
  return useSyncExternalStore((fn) => { dialogListeners.add(fn); return () => { dialogListeners.delete(fn); }; }, () => dialog);
}

/** Open the key dialog. `message` explains why (shown as an error); `then` runs after a successful save. */
export function openKeyDialog(message = "", then: (() => void) | null = null): void {
  const opener = dialog.open ? dialog.opener : document.activeElement instanceof HTMLElement ? document.activeElement : null;
  setDialog({ open: true, message, then, seq: dialog.seq + 1, opener });
}

export function closeKeyDialog(): void {
  if (dialog.open) setDialog({ ...dialog, open: false, then: null });
}

/** True when a key is saved; otherwise opens the dialog (running `then` after a save) and returns false. */
export function requireKey(then: () => void, message = ""): boolean {
  if (settings.key) return true;
  openKeyDialog(message, then);
  return false;
}

/** Key errors reopen the dialog with the server's message (then `retry` after a save); anything else is a toast. */
export function handleError(err: unknown, retry?: () => void): void {
  if (err instanceof ApiError && KEY_ERRORS.has(err.code)) openKeyDialog(err.message, retry || null);
  else toast(friendlyError(err), { kind: "error", action: retry ? { label: "Retry", run: retry } : null });
}
