// Toasts (spec §5.4) on Sonner: info 4 s, with an action 6 s, errors 6 s, and an error with an
// action stays until dismissed. Sonner pauses timers on hover and while the tab is hidden.
import { toast as sonner } from "sonner";
import { Icon } from "@/components/Icon";

export type ToastKind = "info" | "success" | "error";
export interface ToastAction { label: string; run: () => void }

/** Show a toast; returns a function that dismisses it. */
export function toast(message: string, { action = null, kind = "info" }: { action?: ToastAction | null; kind?: ToastKind } = {}): () => void {
  const sticky = kind === "error" && !!action;
  const duration = sticky ? Infinity : action || kind === "error" ? 6000 : 4000;
  const id = sonner.custom((t) => (
    <div className={`toast ${kind}`}>
      <Icon name={kind === "error" ? "alert" : kind === "success" ? "check" : "info"} size={18} />
      <span className="msg">{message}</span>
      {action ? (
        <button className="btn" type="button" onClick={() => { sonner.dismiss(t); action.run(); }}>{action.label}</button>
      ) : null}
      <button className="btn quiet icon" type="button" aria-label="Dismiss" onClick={() => sonner.dismiss(t)}>
        <Icon name="x" size={16} />
      </button>
    </div>
  ), { duration });
  return () => { sonner.dismiss(id); };
}
