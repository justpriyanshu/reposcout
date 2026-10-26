// shadcn/ui Input. The bordered box, icon and focus ring come from the surrounding `.input` group
// (InputGroup), so the field itself stays bare, as in the design system.
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(className)} {...props} />;
}

/** The `.input` group: an optional icon, the field and an optional button in one bordered box. */
export function InputGroup({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("input", className)} {...props} />;
}
