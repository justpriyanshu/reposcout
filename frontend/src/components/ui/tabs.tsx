// shadcn/ui Tabs on Radix, restyled with the nav's .navlink look. Arrow keys, Home and End move
// between tabs and select them (automatic activation), with a roving tabindex.
import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("tabs", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return <TabsPrimitive.Trigger className={cn("navlink", className)} {...props} />;
}

/** Panels stay mounted so typed text and results survive a tab switch; pass `hidden` for inactive ones. */
export function TabsContent(props: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content forceMount tabIndex={-1} {...props} />;
}
