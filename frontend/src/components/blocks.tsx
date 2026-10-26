// Small shared blocks: skeletons, empty/error/info states, file locations, code and copy buttons.
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { shortPath } from "@/lib/paths";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Icon, type IconName } from "./Icon";

/** A skeleton bar; `className` adds size classes such as "w-60" or "block". */
export const Skel = ({ className }: { className?: string }) => <span className={cn("skel", className)} aria-hidden="true" />;

/** An empty, error or info block: icon, title, one line and optional actions. */
export function StateBlock({ icon, title, text, actions, tone, className }: {
  icon?: IconName; title: ReactNode; text?: ReactNode; actions?: ReactNode; tone?: "error"; className?: string;
}) {
  return (
    <div className={cn("state", tone, className)} role={tone === "error" ? "alert" : undefined}>
      {icon ? <Icon name={icon} size={20} /> : null}
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {actions ? <div className="actions">{actions}</div> : null}
    </div>
  );
}

/** "path:start-end" on one line: a long path is cut in the middle (and by CSS), the line range always shows. */
export function FileLoc({ path, start, end, max = 48 }: { path: string; start: number; end?: number; max?: number }) {
  return (
    <span className="loc">
      <span className="p">{shortPath(path, max)}</span>
      <span className="l">{end === undefined ? `:${start}` : `:${start}-${end}`}</span>
    </span>
  );
}

/** Numbered code lines starting at `firstLine`, cut at `maxLines`; `highlight` lists line numbers (as shown) to mark. */
export function CodeBlock({ text, firstLine, maxLines, highlight = [] }: { text: string; firstLine: number; maxLines: number; highlight?: number[] }) {
  const lines = String(text).split("\n");
  const shown = lines.slice(0, maxLines);
  const marked = new Set(highlight);
  // The line-number gutter fits the longest number shown (six-digit lines in big files).
  const width = `${Math.max(4, String(firstLine + shown.length - 1).length)}ch`;
  return (
    <pre className="code" style={{ "--ln-w": width } as CSSProperties}>
      <code>
        {shown.map((line, i) => (
          <span key={i} className={marked.has(firstLine + i) ? "ln hl" : "ln"} data-n={firstLine + i}>{line || " "}</span>
        ))}
        {lines.length > maxLines ? <span className="ln" data-n="">…</span> : null}
      </code>
    </pre>
  );
}

/** A "Copy" button that copies getText() and shows "Copied" briefly. */
export function CopyButton({ getText }: { getText: () => string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = () => {
    navigator.clipboard?.writeText(getText()).then(() => {
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    }, () => toast("Couldn't copy. Select the text and copy it instead.", { kind: "error" }));
  };
  return (
    <Button variant="quiet" onClick={copy}>
      <Icon name={copied ? "check" : "copy"} size={14} /><span>{copied ? "Copied" : "Copy"}</span>
    </Button>
  );
}

/** Rendered Markdown (already escaped and sanitised by lib/markdown). */
export function Html({ html, className, as: Tag = "div" }: { html: string; className?: string; as?: "div" | "p" }) {
  return <Tag className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
