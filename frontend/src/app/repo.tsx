// The tool page frame, repository picker, repo bar and indexing card (spec §5.4, §6.4, §10).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Icon, ToolIcon } from "@/components/Icon";
import { Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, api, friendlyError } from "@/lib/api";
import { fmt } from "@/lib/format";
import { parseLink, toolHash } from "@/lib/links";
import { approveReview } from "@/lib/reviewdata";
import type { ToolId } from "@/lib/routes";
import { EXAMPLES, TOOLS, pickerRepos, tool } from "@/lib/tools";
import { toast } from "@/lib/toast";

export const POLL_MS = 1500;
const MAX_MISSES = 8; // consecutive failed polls before giving up
const STEPS: [string, string][] = [["queued", "Waiting for a free slot"], ["cloning", "Downloading the code"], ["indexing", "Reading and indexing files"], ["ready", "Ready"]];

export interface RepoRecord {
  owner?: string;
  name?: string;
  full_name?: string;
  key?: string;
  /** queued, cloning, indexing, ready, failed, or expired (the server dropped the index to save space; ready is false). */
  status?: string;
  ready?: boolean;
  error?: string | null;
  stage?: string;
  description?: string;
  files_total?: number;
  files_done?: number;
  queue_position?: number;
  commit_sha?: string;
  branch?: string;
}

export interface RepoRoute { tool: ToolId; owner: string; name: string }

export const repoPath = (owner: string, name: string) => `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
/** The caller's own list (signed in, or this browser's), most recently used first. */
export const reposQuery = { queryKey: ["repos"], queryFn: () => api<{ repos: RepoRecord[] }>("/api/repos") };
/** Indexed once but dropped since: it needs indexing again before it can be used. */
export const isExpired = (r: RepoRecord | null | undefined): boolean => !!r && r.status === "expired" && !r.ready;
const repoKey = (owner: string, name: string, ...rest: string[]) => ["repo", owner.toLowerCase(), name.toLowerCase(), ...rest];

/** Run the router again for the same hash (a fresh view). */
export const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

/* ================================================================ frame */
function Aside({ toolId, repo }: { toolId: ToolId; repo: { owner: string; name: string } | null }) {
  const others = TOOLS.filter((t) => t.live && t.id !== toolId);
  return (
    <aside className="aside">
      <div className="othersbox">
        <h3>{repo ? "Also for this repository" : "Other tools"}</h3>
        <nav className="others" aria-label={repo ? "Other tools for this repository" : "Other tools"}>
          {others.map((t) => (
            <a key={t.id} href={repo ? toolHash(t.id, repo.owner, repo.name) : toolHash(t.id)}><ToolIcon tool={t} size="sm" />{t.name}</a>
          ))}
        </nav>
      </div>
    </aside>
  );
}

/** Breadcrumb, header and the two-column grid; `children` fill the main column. */
export function ToolFrame({ toolId, repo = null, children }: { toolId: ToolId; repo?: { owner: string; name: string } | null; children: ReactNode }) {
  const t = tool(toolId)!;
  return (
    <div className="wrap">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="#/">All tools</a><span aria-hidden="true">/</span><span aria-current="page">{t.name}</span>
      </nav>
      <div className="toolhead"><ToolIcon tool={t} size="lg" /><div><h1>{t.name}</h1><p>{t.description}</p></div></div>
      <div className="toolgrid">
        <div className="toolmain">{children}</div>
        <Aside toolId={toolId} repo={repo} />
      </div>
    </div>
  );
}

/* ================================================================ picker */
const PICK = {
  review: { title: "Which pull request?", help: "Paste a pull request link, or pick a repository and type its number.", placeholder: "github.com/owner/repo/pull/123" },
  other: { title: "Which repository?", help: "Paste a public GitHub repository. The first time, RepoScout reads and indexes it; that takes a minute or two.", placeholder: "github.com/owner/repo" },
};

/** The "Which repository?" card with a link field and up to three suggestions. */
export function Picker({ toolId }: { toolId: ToolId }) {
  const copy = toolId === "review" ? PICK.review : PICK.other;
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const repos = useQuery(reposQuery);
  const names = repos.isSuccess ? pickerRepos(repos.data.repos) : repos.isError ? EXAMPLES.slice(0, 3) : null;

  function submit(e: FormEvent) {
    e.preventDefault();
    const link = parseLink(value);
    if (!link) { setInvalid("Paste a link like github.com/owner/repo"); input.current?.focus(); return; }
    setInvalid("");
    // A PR link goes to Review from any tool, and starts at once: the visitor just asked for it.
    if (link.pull) approveReview(link.owner, link.name, link.pull);
    location.hash = link.pull ? toolHash("review", link.owner, link.name, link.pull) : toolHash(toolId, link.owner, link.name);
  }

  return (
    <section className="pick">
      <h2>{copy.title}</h2>
      <p>{copy.help}</p>
      <form className="input" noValidate onSubmit={submit}>
        <Icon name="link" size={18} />
        <Input id="pick-link" ref={input} type="text" name="link" placeholder={copy.placeholder} aria-label="GitHub link" autoComplete="off"
          autoCapitalize="none" spellCheck={false} aria-describedby="pick-msg" aria-invalid={invalid ? true : undefined}
          value={value} onChange={(e) => { setValue(e.target.value); if (invalid) setInvalid(""); }} />
        <Button variant="primary" type="submit">Continue</Button>
      </form>
      <p id="pick-msg" className="invalid" role="alert" hidden={!invalid}>{invalid}</p>
      <p className="try">Try</p>
      <div className="recent" role="group" aria-label="Try one of these" aria-busy={names ? undefined : true}>
        {names
          ? names.map((full) => { const [o, n] = full.split("/"); return <a key={full} className="chip" href={toolHash(toolId, o, n)}>{full}</a>; })
          : [0, 1, 2].map((i) => <span key={i} className="chip" aria-hidden="true"><Skel /></span>)}
      </div>
    </section>
  );
}

/* ================================================================ repo bar */
const initials = (full: string) => full.replace(/[^A-Za-z0-9]/g, "").slice(0, 2);

function metaOf(r: RepoRecord, indexed: boolean): string {
  if (!indexed) return "Not indexed. Reviews read only the changed files.";
  if (r.status === "failed") return "Indexing failed";
  if (r.status === "unknown") return "Couldn't check the index";
  if (r.status === undefined) return "Checking the index…";
  if (!r.ready) return "Indexing now";
  const commit = (r.commit_sha || "").slice(0, 7);
  const where = [r.branch, commit].filter(Boolean).join(" @ ");
  return [where, r.files_total ? `${fmt(r.files_total)} files indexed` : ""].filter(Boolean).join(" · ") || "Ready";
}

/** The bar under the tool header: initials, owner/name, a meta line and the actions, with banners beneath. */
export function RepoBar({ record, indexed = true, toolId }: { record: RepoRecord; indexed?: boolean; toolId: ToolId }) {
  // "Index it" turns a not-indexed bar into an indexed one for the record the server returned.
  const [started, setStarted] = useState<RepoRecord | null>(null);
  const acts = useRef<HTMLDivElement>(null);
  const shown = started || record;
  const isIndexed = indexed || !!started;
  const full = shown.full_name || `${shown.owner}/${shown.name}`;

  const refresh = useMutation({ mutationFn: () => api("/api/repos", { method: "POST", body: { url: full, refresh: true } }) });
  const index = useMutation({ mutationFn: () => api<{ repo: RepoRecord }>("/api/repos", { method: "POST", body: { url: full } }) });

  useEffect(() => {
    if (started) acts.current?.querySelector<HTMLElement>(".btn")?.focus(); // keep keyboard focus on the bar
  }, [started]);

  return (
    <div className="repowrap">
      <div className="repobar">
        <span className="av" aria-hidden="true">{initials(full)}</span>
        <span className="nm" title={full}>{full}</span>
        <span className="meta">{metaOf(shown, isIndexed)}</span>
        <div className="acts" ref={acts}>
          {isIndexed && shown.ready ? (
            <Button size="icon" aria-label="Refresh index" title="Refresh index" disabled={refresh.isPending}
              onClick={() => refresh.mutate(undefined, {
                onSuccess: () => toast("Refreshing the index in the background. You can keep using it meanwhile."),
                onError: (err) => toast(friendlyError(err), { kind: "error" }),
              })}>
              <Icon name="refresh" size={16} />
            </Button>
          ) : null}
          {!isIndexed ? (
            <Button disabled={index.isPending}
              onClick={() => index.mutate(undefined, {
                onSuccess: ({ repo }) => { toast("Indexing started. You can keep reviewing meanwhile."); setStarted(repo); },
                onError: (err) => toast(friendlyError(err), { kind: "error" }),
              })}>
              Index it
            </Button>
          ) : null}
          <Button asChild size="icon">
            <a href={`https://github.com/${full}`} target="_blank" rel="noopener" aria-label="Open on GitHub" title="Open on GitHub"><Icon name="ext" size={16} /></a>
          </Button>
          <Button asChild><a href={toolHash(toolId)}>Change</a></Button>
        </div>
      </div>
      {isIndexed && shown.ready && shown.status && shown.status !== "ready" ? (
        <div className="banner" role="status"><Icon name="info" size={16} />Refreshing the index in the background. Answers use the previous version until it's done.</div>
      ) : isIndexed && shown.ready && shown.error ? (
        <div className="banner error"><Icon name="alert" size={16} />{`Last refresh failed: ${shown.error}`}</div>
      ) : null}
    </div>
  );
}

/* ================================================================ indexing */
function stepLabel(key: string, label: string, r: RepoRecord): string {
  if (key === "indexing" && r.files_total) return `${label} (${fmt(r.files_done)} of ${fmt(r.files_total)})`;
  if (key === "queued" && (r.queue_position || 0) > 1) return `${label} (number ${r.queue_position} in line)`;
  return label;
}

function IndexingCard({ record, full }: { record: RepoRecord; full: string }) {
  const at = Math.max(0, STEPS.findIndex(([k]) => k === record.status));
  const part = record.status === "indexing" && (record.files_total || 0) > 0
    ? Math.min(1, Math.max(0, (record.files_done || 0) / (record.files_total as number))) : null;
  return (
    <section className="progress">
      <h2>{`Getting ${full} ready`}</h2>
      <p>This happens once per repository. You can leave this page open, or come back later.</p>
      <div className={part === null ? "bar wait" : "bar"} role="progressbar" aria-label="Indexing progress" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={part === null ? undefined : Math.round(part * 100)}>
        <span style={part === null ? undefined : { transform: `scaleX(${part})` }} />
      </div>
      <ul className="steps">
        {STEPS.map(([key, label], i) => {
          const done = i < at || (i === at && key === "ready");
          return (
            <li key={key} className={done ? "done" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>
              <span className="mk">{done ? <Icon name="check" size={11} /> : null}</span>{stepLabel(key, label, record)}
            </li>
          );
        })}
      </ul>
      {/* Announced only when the step changes: the text stays the same between polls. */}
      <p className="sr-only" role="status">{STEPS[at][1]}</p>
    </section>
  );
}

function FailureCard({ full, reason, refresh, toolId, onRetry }: { full: string; reason: string; refresh: boolean; toolId: ToolId; onRetry: () => void }) {
  const retry = useMutation({ mutationFn: () => api("/api/repos", { method: "POST", body: { url: full, refresh: true } }) });
  return (
    <StateBlock icon="alert" tone="error" title={`Couldn't index ${full}`} text={reason || "Indexing failed."} actions={<>
      <Button variant="primary" disabled={retry.isPending} onClick={() => {
        if (!refresh) { onRetry(); return; }
        retry.mutate(undefined, { onSuccess: onRetry, onError: (err) => toast(friendlyError(err), { kind: "error" }) });
      }}>Try again</Button>
      <Button asChild><a href={toolHash(toolId)}>Pick another repository</a></Button>
    </>} />
  );
}

/**
 * Make sure owner/name is indexed: GET the record, POST /api/repos on 404 (not in the caller's list
 * yet) or when its index expired, then poll every 1.5 s
 * until it is ready or failed. A few failed polls in a row are tolerated (a blip); the eighth ends it.
 */
export function useIndexedRepo(owner: string, name: string) {
  const qc = useQueryClient();
  const key = repoKey(owner, name);
  const misses = useRef(0);
  const query = useQuery({
    queryKey: key,
    staleTime: 0,
    queryFn: async (): Promise<RepoRecord> => {
      const prev = qc.getQueryData<RepoRecord>(key);
      try {
        const { repo } = await api<{ repo: RepoRecord }>(repoPath(owner, name));
        misses.current = 0;
        if (isExpired(repo)) return (await api<{ repo: RepoRecord }>("/api/repos", { method: "POST", body: { url: `${owner}/${name}` } })).repo;
        return repo;
      } catch (err) {
        if (!prev) {
          if (!(err instanceof ApiError) || err.status !== 404) throw err;
          return (await api<{ repo: RepoRecord }>("/api/repos", { method: "POST", body: { url: `${owner}/${name}` } })).repo;
        }
        if (!prev.ready && ++misses.current < MAX_MISSES) return prev; // a blip: keep polling
        throw err;
      }
    },
    refetchInterval: (q) => {
      const r = q.state.data;
      return q.state.status !== "error" && r && !r.ready && r.status !== "failed" && !isExpired(r) ? POLL_MS : false;
    },
  });
  const retry = () => { misses.current = 0; void qc.resetQueries({ queryKey: key }); };
  return { query, retry };
}

/** A tool that needs the index (Ask, Find code): repo bar, then indexing, then `children(record)`. */
export function IndexedTool({ route, children }: { route: RepoRoute; children: (record: RepoRecord) => ReactNode }) {
  const { owner, name, tool: toolId } = route;
  const full = `${owner}/${name}`;
  const { query, retry } = useIndexedRepo(owner, name);
  const record = query.data;
  const sawIndexing = useRef(false);
  const [entering, setEntering] = useState(false);

  let body: ReactNode = null;
  let bar: RepoRecord = { owner, name, full_name: full };
  if (record?.ready) {
    bar = record;
    body = children(record);
  } else if (query.isError) {
    bar = { ...(record || bar), status: "failed" }; // the bar must not keep saying "Checking the index…"
    body = <FailureCard full={full} reason={friendlyError(query.error)} refresh={false} toolId={toolId} onRetry={retry} />;
  } else if (record?.status === "failed") {
    bar = record;
    body = <FailureCard full={full} reason={record.error || ""} refresh toolId={toolId} onRetry={retry} />;
  } else if (record) {
    bar = record;
    sawIndexing.current = true;
    body = <IndexingCard record={record} full={record.full_name || full} />;
  }

  // The tool replaces the indexing card the visitor was watching: let it arrive rather than jump in.
  const ready = !!record?.ready;
  useEffect(() => { if (ready && sawIndexing.current) { sawIndexing.current = false; setEntering(true); } }, [ready]);

  return (
    <ToolFrame toolId={toolId} repo={{ owner, name }}>
      <RepoBar record={bar} toolId={toolId} />
      <div className={entering ? "toolbody enter" : "toolbody"} onAnimationEnd={(e) => { if (e.target === e.currentTarget) setEntering(false); }}>
        {body}
      </div>
    </ToolFrame>
  );
}

/** A tool that doesn't need the index (Review): look the repository up for the bar, then `children`. */
export function LookupTool({ route, children }: { route: RepoRoute; children: ReactNode }) {
  const { owner, name, tool: toolId } = route;
  const full = `${owner}/${name}`;
  const lookup = useQuery({
    queryKey: repoKey(owner, name, "lookup"),
    staleTime: 0,
    queryFn: async (): Promise<RepoRecord | null> => {
      try { return (await api<{ repo: RepoRecord }>(repoPath(owner, name))).repo; }
      catch (err) { if (err instanceof ApiError && err.status === 404) return null; throw err; }
    },
  });
  const base = { owner, name, full_name: full };
  const bar = lookup.isPending ? <RepoBar record={base} toolId={toolId} />
    : lookup.isError ? <RepoBar record={{ ...base, status: "unknown" }} toolId={toolId} /> // claim neither missing nor ready
    : lookup.data && !isExpired(lookup.data) ? <RepoBar record={lookup.data} toolId={toolId} />
    : <RepoBar record={base} indexed={false} toolId={toolId} />;
  return (
    <ToolFrame toolId={toolId} repo={{ owner, name }}>
      {bar}
      <div className="toolbody">{lookup.isPending ? null : children}</div>
    </ToolFrame>
  );
}
