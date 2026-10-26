// shadcn/ui Sonner: the toast region, bottom-centre. Sonner places, stacks and animates the toasts,
// pauses their timers on hover and while the tab is hidden, and keeps at most three visible;
// lib/toast.ts renders each toast's content with the design system's look.
import { Toaster as Sonner } from "sonner";
import type { CSSProperties } from "react";

export function Toaster() {
  return (
    <Sonner
      position="bottom-center"
      expand
      visibleToasts={3}
      gap={8}
      offset={16}
      mobileOffset={16}
      hotkey={[]}
      containerAriaLabel="Notifications"
      style={{ "--width": "min(420px, calc(100vw - 32px))" } as CSSProperties}
      toastOptions={{ unstyled: true }}
    />
  );
}
