// The tools home (spec §6.3): hero, the three live tools, a tool filter, categories, and recent repositories.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Icon, ToolIcon } from "@/components/Icon";
import { Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmt } from "@/lib/format";
import { toolHash } from "@/lib/links";
import { TOOLS, filterTools, type Tool } from "@/lib/tools";
import { reposQuery, type RepoRecord } from "../repo";
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

function RecentRow({ r }: { r: RepoRecord }) {
  const failed = r.status === "failed";
  const desc = r.description
    || (r.ready ? `${fmt(r.files_total)} ${r.files_total === 1 ? "file" : "files"} indexed`
      : failed ? "Indexing failed"
      : r.files_total ? `${r.stage || "Indexing"} (${fmt(r.files_done)} of ${fmt(r.files_total)})`
      : r.status === "queued" && (r.queue_position || 0) > 1 ? `Waiting to start (number ${r.queue_position} in line)` : (r.stage || "Waiting to start"));
  const cls = r.ready ? "ok" : failed ? "bad" : "busy";
  return (
    <a className="row" href={toolHash("ask", r.owner, r.name)}>
      <span className="nm" title={r.full_name}>{r.full_name}</span><span className="ds" title={desc}>{desc}</span>
      <span className={`pill ${cls}`}><span className="dot" />{r.ready ? "Ready" : failed ? "Failed" : "Indexing"}</span>
    </a>
  );
}

function Recent() {
  const repos = useQuery(reposQuery);
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
        <StateBlock icon="alert" tone="error" title="Couldn't load recent repositories" text="Check your connection and try again."
          actions={<Button onClick={() => void repos.refetch()}><Icon name="refresh" size={14} />Try again</Button>} />
      </div>
    );
  }
  const list = repos.data.repos || [];
  return (
    <div className="rows recent">
      {list.length
        ? list.map((r) => <RecentRow key={r.full_name} r={r} />)
        : <StateBlock icon="search" title="Nothing explored yet"
            text="Open Ask or Find code and add a repository, for example pallets/flask; it will show up here for everyone." />}
    </div>
  );
}

export default function Home() {
  const config = useConfig();
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
          <h2>Recently explored</h2>
          <p>Repositories other people have already indexed. Open one to skip the wait.</p>
          <Recent />
        </div>
      </section>
    </div>
  );
}
