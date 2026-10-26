// The tools home (spec §6.3): hero, the three live tools, a tool filter, categories, and the visitor's own repositories.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { GitHubMark, Icon, ToolIcon } from "@/components/Icon";
import { Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, friendlyError } from "@/lib/api";
import { fmt } from "@/lib/format";
import { toolHash } from "@/lib/links";
import { toast } from "@/lib/toast";
import { TOOLS, filterTools, type Tool } from "@/lib/tools";
import { SignInLink } from "../Account";
import { useMe } from "../account";
import { isExpired, repoPath, reposQuery, type RepoRecord } from "../repo";
import { useConfig } from "../settings";

const LIVE = TOOLS.filter((t) => t.live);

function Tile({ t }: { t: Tool }) {
  return (
    <a className="tile" href={`#/${t.id}`}>
      <ToolIcon tool={t} size="lg" /><h3>{t.name}</h3><p>{t.description}</p>
      <span className="go">Open <Icon name="arrow" size={14} /></span>
    </a>
  );
}

function Card({ t }: { t: Tool }) {
  const body = (
    <span><b>{t.name}{t.live ? null : <span className="pill">Soon</span>}</b><span className="d">{t.description}</span></span>
  );
  return t.live
    ? <a className="card" href={`#/${t.id}`}><ToolIcon tool={t} size="sm" />{body}</a>
    : <div className="card soon" aria-disabled="true"><ToolIcon tool={t} size="sm" />{body}</div>;
}

function Categories({ query }: { query: string }) {
  const groups = filterTools(query);
  if (!groups.length) return <p className="empty-search">{`No tool matches “${query.trim()}”. Try “review” or “search”.`}</p>;
  return <>{groups.map(([name, tools]) => (
    <section key={name} className="cat"><h2>{name}</h2><div className="cards">{tools.map((t) => <Card key={t.id} t={t} />)}</div></section>
  ))}</>;
}

const IN_PROGRESS = new Set(["queued", "cloning", "indexing"]);
const LIST_POLL_MS = 5000;

function describe(r: RepoRecord): { desc: string; pill: string; cls: string } {
  if (isExpired(r)) return { desc: "Its index was removed to save space. Index it again to use it.", pill: "Expired", cls: "" };
  const failed = r.status === "failed";
  const desc = r.description
    || (r.ready ? `${fmt(r.files_total)} ${r.files_total === 1 ? "file" : "files"} indexed`
      : failed ? "Indexing failed"
      : r.files_total ? `${r.stage || "Indexing"} (${fmt(r.files_done)} of ${fmt(r.files_total)})`
      : r.status === "queued" && (r.queue_position || 0) > 1 ? `Waiting to start (number ${r.queue_position} in line)` : (r.stage || "Waiting to start"));
  return { desc, pill: r.ready ? "Ready" : failed ? "Failed" : "Indexing", cls: r.ready ? "ok" : failed ? "bad" : "busy" };
}

type ReposData = { repos: RepoRecord[] };

/** One of the caller's repositories: the name opens Ask (the whole row is the link); remove, and index again when expired. */
function MineRow({ r, onRemoved }: { r: RepoRecord; onRemoved: (full: string) => void }) {
  const qc = useQueryClient();
  const full = r.full_name || `${r.owner}/${r.name}`;
  const [owner, name] = full.split("/");
  const link = useRef<HTMLAnchorElement>(null);
  const refocus = useRef(false);
  const expired = isExpired(r);
  const { desc, pill, cls } = describe(r);

  const remove = useMutation({ mutationFn: () => api(repoPath(owner, name), { method: "DELETE" }) });
  const reindex = useMutation({ mutationFn: () => api<{ repo: RepoRecord }>("/api/repos", { method: "POST", body: { url: full } }) });

  // "Index again" goes away once indexing starts: keep keyboard focus on this row.
  useEffect(() => {
    if (!expired && refocus.current) { refocus.current = false; link.current?.focus(); }
  }, [expired]);

  return (
    <li className="row mine">
      <a className="nm" ref={link} href={toolHash("ask", owner, name)} title={full}>{full}</a>
      <span className="ds" title={desc}>{desc}</span>
      <span className={cls ? `pill ${cls}` : "pill"}><span className="dot" />{pill}</span>
      <span className="acts">
        {expired ? (
          <Button disabled={reindex.isPending} onClick={() => reindex.mutate(undefined, {
            onSuccess: ({ repo }) => {
              refocus.current = true;
              qc.setQueryData<ReposData>(reposQuery.queryKey, (d) => d && { repos: d.repos.map((x) => (x.full_name === r.full_name ? { ...x, ...repo } : x)) });
              toast(`Indexing ${full} again. It takes a minute or two.`);
            },
            onError: (err) => toast(friendlyError(err), { kind: "error" }),
          })}>
            <Icon name="refresh" size={14} />Index again
          </Button>
        ) : null}
        <Button variant="quiet" size="icon" aria-label={`Remove ${full} from my list`} title="Remove from my list" disabled={remove.isPending}
          onClick={() => remove.mutate(undefined, {
            onSuccess: () => onRemoved(r.full_name || full),
            onError: (err) => toast(friendlyError(err), { kind: "error" }),
          })}>
          <Icon name="x" size={16} />
        </Button>
      </span>
    </li>
  );
}

/** The caller's own repositories (spec §6.3, now private): GET /api/repos, polled while any is indexing. */
function Mine({ heading }: { heading: React.RefObject<HTMLHeadingElement | null> }) {
  const qc = useQueryClient();
  const me = useMe();
  const repos = useQuery({
    ...reposQuery,
    refetchInterval: (q) => ((q.state.data?.repos || []).some((r) => !r.ready && IN_PROGRESS.has(r.status || "")) ? LIST_POLL_MS : false),
  });
  const list = useRef<HTMLUListElement>(null);
  const focusAt = useRef<number | null>(null);

  function removed(full: string) {
    const before = qc.getQueryData<ReposData>(reposQuery.queryKey)?.repos || [];
    focusAt.current = Math.max(0, before.findIndex((x) => x.full_name === full));
    qc.setQueryData<ReposData>(reposQuery.queryKey, (d) => d && { repos: d.repos.filter((x) => x.full_name !== full) });
    toast(`Removed ${full} from your list.`);
  }

  // The removed row took keyboard focus with it: move to the row now in its place, the one before, or the heading.
  useEffect(() => {
    const at = focusAt.current;
    if (at === null) return;
    focusAt.current = null;
    const links = list.current?.querySelectorAll<HTMLElement>(".row .nm");
    (links?.[at] || links?.[at - 1] || heading.current)?.focus();
  }, [repos.data, heading]);

  if (repos.isPending || (repos.isFetching && repos.isError)) {
    return (
      <div className="rows recent" aria-busy="true">
        {[0, 1, 2].map((i) => <div key={i} className="row" aria-hidden="true"><Skel className="w-60" /><Skel className="w-80" /></div>)}
      </div>
    );
  }
  if (repos.isError) {
    return (
      <div className="rows recent">
        <StateBlock icon="alert" tone="error" title="Couldn't load your repositories" text="Check your connection and try again."
          actions={<Button onClick={() => void repos.refetch()}><Icon name="refresh" size={14} />Try again</Button>} />
      </div>
    );
  }
  const rows = repos.data.repos || [];
  if (!rows.length) {
    return (
      <div className="rows recent">
        <StateBlock icon="search" title="Nothing here yet"
          text="Open Ask or Find code and add a repository, for example pallets/flask. It will show up here, just for you."
          actions={me.user
            ? <Button asChild><a href="#/github"><GitHubMark size={16} />Explore your GitHub repositories</a></Button>
            : me.github_login ? <SignInLink /> : null} />
      </div>
    );
  }
  return (
    <>
      <ul className="rows recent" ref={list} aria-labelledby="mine-h">
        {rows.map((r) => <MineRow key={r.full_name} r={r} onRemoved={removed} />)}
      </ul>
      {me.user ? (
        <Button asChild className="ghcta"><a href="#/github"><GitHubMark size={16} />Explore your GitHub repositories</a></Button>
      ) : null}
    </>
  );
}

function mineIntro(me: { user: unknown; github_login: boolean }): string {
  if (me.user) return "Repositories you've explored, most recent first. Only you can see this list.";
  return me.github_login
    ? "Repositories you've explored in this browser, most recent first. Only you can see this list. Sign in with GitHub to keep it on every device."
    : "Repositories you've explored in this browser, most recent first. Only you can see this list.";
}

export default function Home() {
  const config = useConfig();
  const me = useMe();
  const mineHeading = useRef<HTMLHeadingElement>(null);
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState(false);
  const n = filterTools(query).reduce((sum, [, tools]) => sum + tools.length, 0);
  return (
    <div className="wrap">
      <section className="hero">
        <span className="tag">Free · bring your own key · no sign-up</span>
        <h1>Tools for understanding and reviewing GitHub code</h1>
        <p>Ask how a repository works, find code by meaning, and review pull requests. Pick a tool to start.</p>
      </section>
      <section className="panel" aria-label="Popular tools"><div className="tiles">{LIVE.map((t) => <Tile key={t.id} t={t} />)}</div></section>
      <div className="search">
        <form className="input" role="search" onSubmit={(e) => e.preventDefault()}>
          <Icon name="search" size={18} />
          <Input type="search" placeholder={`Search ${TOOLS.length} tools`} aria-label="Search tools" autoComplete="off"
            value={query} onChange={(e) => { setQuery(e.target.value); setTyped(true); }} />
        </form>
        {/* Announce a short count, not the whole list, as the visitor types. */}
        <p className="sr-only" role="status">{typed ? (n ? `${n} ${n === 1 ? "tool" : "tools"}` : "No tools match") : ""}</p>
      </div>
      <div><Categories query={query} /></div>
      <section className="split">
        <div>
          <h2>Private by design</h2>
          <p>RepoScout reads public code only, and you bring your own free <span>{config.provider}</span> key.</p>
          <ul className="checks">
            {["Your key stays in your browser and is sent only with your own requests.",
              "Answers link to the exact lines on GitHub, so you can check them.",
              "Find code works with no key at all."].map((s) => <li key={s}><Icon name="check" size={14} />{s}</li>)}
          </ul>
        </div>
        <div>
          <h2 id="mine-h" ref={mineHeading} tabIndex={-1}>Your repositories</h2>
          <p>{mineIntro(me)}</p>
          <Mine heading={mineHeading} />
        </div>
      </section>
    </div>
  );
}
