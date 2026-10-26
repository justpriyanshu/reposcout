// localStorage / sessionStorage with JSON values. The keys are shared with the old UI
// (rs.theme, rs.key, rs.model, rs.chat.*, rs.review.*), so visitors keep their settings.

const area = (session: boolean): Storage => (session ? sessionStorage : localStorage);

export const store = {
  get<T = unknown>(k: string, session = false): T | null {
    try { return JSON.parse(area(session).getItem(k) as string) as T | null; } catch { return null; }
  },
  set(k: string, v: unknown, session = false): void {
    try { area(session).setItem(k, JSON.stringify(v)); } catch { /* storage full or blocked */ }
  },
  del(k: string): void {
    try { localStorage.removeItem(k); sessionStorage.removeItem(k); } catch { /* blocked */ }
  },
};
