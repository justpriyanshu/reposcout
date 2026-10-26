// Find code view: search one indexed repository. Needs no API key.
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/Icon";
import { CodeBlock, FileLoc, Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, asApiError, friendlyError } from "@/lib/api";
import { matchedLines } from "@/lib/matched";
import type { ToolId } from "@/lib/routes";
import { IndexedTool, Picker, ToolFrame, repoPath, type RepoRoute } from "../repo";

interface Hit { path: string; start_line: number; end_line: number; url: string; name?: string; kind?: string; language?: string; text: string }

const hoverDevice = () => matchMedia("(hover: hover)").matches;

function HitCard({ x, query }: { x: Hit; query: string }) {
  const label = x.name ? `${x.kind} ${x.name}` : x.language;
  return (
    <article className="hit">
      <header>
        <a href={x.url} target="_blank" rel="noopener" title={`${x.path}:${x.start_line}-${x.end_line}`}><FileLoc path={x.path} start={x.start_line} end={x.end_line} /></a>
        {label ? <span className="pill" title={label}>{label}</span> : null}
      </header>
      <CodeBlock text={x.text} firstLine={x.start_line} maxLines={14} highlight={matchedLines(x.text, query, x.start_line)} />
    </article>
  );
}

const Skeletons = () => <>{[0, 1].map((i) => (
  <div key={i} className="hit" aria-hidden="true"><header><Skel className="w-60" /></header><div className="hit-skel"><Skel /><Skel className="w-80" /><Skel className="w-60" /></div></div>
))}</>;

function FindBody({ owner, name }: { owner: string; name: string }) {
  const [value, setValue] = useState("");
  const input = useRef<HTMLInputElement>(null);
  // The mutation follows the latest search only: an older reply never overwrites a newer one.
  const search = useMutation({
    mutationFn: (query: string) => api<{ results: Hit[] }>(`${repoPath(owner, name)}/search`, { method: "POST", body: { query } }),
  });
  const query = search.variables || "";

  useEffect(() => { if (hoverDevice()) input.current?.focus({ preventScroll: true }); }, []);

  function run(e?: FormEvent) {
    e?.preventDefault();
    const q = value.trim();
    if (q) search.mutate(q);
  }

  return (
    <>
      <form className="input find" role="search" onSubmit={run}>
        <Icon name="search" size={18} />
        <Input ref={input} type="search" placeholder="Function names, keywords, or a description" aria-label="Search the code" maxLength={300} autoComplete="off"
          value={value} onChange={(e) => setValue(e.target.value)} />
        <Button variant="primary" type="submit" disabled={search.isPending}>Search</Button>
      </form>
      <p className="note">Works without an API key. Matches exact words and meaning.</p>
      <div className="hits" aria-live="polite" aria-busy={search.isPending || undefined}>
        {search.isPending ? <Skeletons />
          : search.isError ? (
            <StateBlock icon="alert" tone="error" title="Search didn't work" text={friendlyError(asApiError(search.error))}
              actions={<Button onClick={() => search.mutate(query)}>Try again</Button>} />
          ) : search.isSuccess && !search.data.results.length ? (
            <StateBlock icon="search" title={`No matches for “${query}”`} text="Try a function or file name, or describe what the code does in other words." />
          ) : search.isSuccess ? search.data.results.map((x, i) => <HitCard key={`${x.path}:${x.start_line}:${i}`} x={x} query={query} />)
          : null}
      </div>
    </>
  );
}

export default function Find({ route }: { route: RepoRoute | { tool: ToolId } }) {
  if (!("owner" in route)) return <ToolFrame toolId="find"><Picker toolId="find" /></ToolFrame>;
  return <IndexedTool route={route}>{() => <FindBody owner={route.owner} name={route.name} />}</IndexedTool>;
}
