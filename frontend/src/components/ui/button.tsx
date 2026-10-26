// shadcn/ui Button, restyled with the design system's .btn classes (ui.css).
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva("btn", {
  variants: {
    variant: { default: "", primary: "primary", quiet: "quiet" },
    size: { default: "", icon: "icon" },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export interface ButtonProps extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. an <a>) with the button's look. */
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} type={asChild ? undefined : (type ?? "button")} {...props} />;
}
