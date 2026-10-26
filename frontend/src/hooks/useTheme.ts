// The theme (spec §6.2): "light" | "dark" in localStorage rs.theme; absent = follow the system.
// theme-init.js applies the saved choice before first paint; this keeps it in sync afterwards.
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { store } from "@/lib/store";

const QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((fn) => fn());

function subscribe(fn: () => void) {
  listeners.add(fn);
  const media = matchMedia(QUERY);
  media.addEventListener("change", fn);
  return () => { listeners.delete(fn); media.removeEventListener("change", fn); };
}

function isDark(): boolean {
  const chosen = store.get("rs.theme");
  return chosen ? chosen === "dark" : matchMedia(QUERY).matches;
}

function applyTheme(): void {
  const chosen = store.get("rs.theme");
  if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
  else document.documentElement.removeAttribute("data-theme");
}

/** Whether the page is dark now, and a toggle that saves the opposite choice. */
export function useTheme(): { dark: boolean; toggle: () => void } {
  const dark = useSyncExternalStore(subscribe, isDark);
  useEffect(applyTheme, [dark]);
  const toggle = useCallback(() => {
    store.set("rs.theme", isDark() ? "light" : "dark");
    applyTheme();
    notify();
  }, []);
  return { dark, toggle };
}
