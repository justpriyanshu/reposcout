// "Your GitHub repositories" (#/github): the signed-in person's public repositories, most recently
// pushed first, with a filter and an Explore button that indexes one and opens it in Ask.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { GitHubMark, Icon } from "@/components/Icon";
import { Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, api, friendlyError } from "@/lib/api";
import { fmt } from "@/lib/format";
import { filterGithubRepos, timeAgo, type GithubRepo } from "@/lib/github";
import { toolHash } from "@/lib/links";
import { toast } from "@/lib/toast";
import { SignInLink } from "../Account";
import { ME_KEY, useMe } from "../account";
import { reposQuery, type RepoRecord } from "../repo";

const count = (n: number) => `${fmt(n)} ${n === 1 ? "repository" : "repositories"}`;
const isSignInError = (err: unknown) => err instanceof ApiError && (err.status === 401 || err.code === "sign_in");

function Frame({ sub, children }: { sub: ReactNode; children: ReactNode }) {
  return (
    <div className="wrap ghpage">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="#/">All tools</a><span aria-hidden="true">/</span><span aria-current="page">Your GitHub repositories</span>
      </nav>
      <div className="toolhead">
        <span className="ticon lg gh"><GitHubMark size={28} /></span>
        <div><h1>Your GitHub repositories</h1><p>{sub}</p></div>
      </div>
      <div className="ghbody">{children}</div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="rows" aria-busy="true">
      {[0, 1, 2, 3, 4].map((i) => <div key={i} className="row" aria-hidden="true"><Skel className="w-60" /><Skel className="w-80" /></div>)}
    </div>
  );
}

function SignedOut({ offered }: { offered: boolean }) {
  return offered
    ? <StateBlock icon="lock" title="Sign in to see your repositories"
        text="Sign in with GitHub to pick one of your own public repositories. RepoScout reads only your public profile."
        actions={<SignInLink />} />
    : <StateBlock icon="lock" title="Sign-in isn't available here"
        text="This RepoScout server doesn't offer GitHub sign-in. You can still paste any public repository into Ask or Find code."
        actions={<Button asChild><a href={toolHash("ask")}>Open Ask</a></Button>} />;
}

function Row({ r, pending, busy, onExplore }: { r: GithubRepo; pending: boolean; busy: boolean; onExplore: () => void }) {
  const full = r.full_name;
  const pushed = timeAgo(r.pushed_at);
  return (
    <li className="row gh">
      <span className="nm" title={full}>{full}</span>
      <span className="ds" title={r.description || undefined}>{r.description || "No description"}</span>
      <span className="mt">
        {r.fork ? <span className="pill">Fork</span> : null}
        {r.language ? <span>{r.language}</span> : null}
        <span><Icon name="star" size={13} />{fmt(r.stars)}<span className="sr-only">{r.stars === 1 ? " star" : " stars"}</span></span>
        {pushed ? <span>{`Updated ${pushed}`}</span> : null}
      </span>
      <span className="acts">
        <Button asChild variant="quiet" size="icon">
          <a href={r.html_url || `https://github.com/${full}`} target="_blank" rel="noopener" aria-label={`Open ${full} on GitHub`} title="Open on GitHub">
            <Icon name="ext" size={16} />
          </a>
        </Button>
        <Button aria-label={`${pending ? "Starting" : "Explore"} ${full}`} disabled={busy} aria-busy={pending || undefined} onClick={onExplore}>
          {pending ? "Starting…" : "Explore"}
        </Button>
      </span>
    </li>
  );
}

export default function GitHub() {
  const me = useMe();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState(false);
  const repos = useQuery({
    queryKey: ["github-repos"],
    queryFn: () => api<{ repos: GithubRepo[] }>("/api/me/github-repos"),
    enabled: !!me.user,
    staleTime: 60_000,
  });
  const explore = useMutation({
    mutationFn: (full: string) => api<{ repo: RepoRecord }>("/api/repos", { method: "POST", body: { url: full } }),
    onSuccess: (_data, full) => {
      void qc.invalidateQueries({ queryKey: reposQuery.queryKey });
      const [owner, name] = full.split("/");
      location.hash = toolHash("ask", owner, name);
    },
    onError: (err) => {
      if (isSignInError(err)) void qc.invalidateQueries({ queryKey: ME_KEY }); // the session ended meanwhile
      toast(friendlyError(err), { kind: "error" });
    },
  });

  if (!me.loaded) return <Frame sub="Your public repositories on GitHub."><Skeleton /></Frame>;
  if (!me.user || (repos.isError && isSignInError(repos.error))) {
    return <Frame sub="Explore your own public repositories."><SignedOut offered={me.github_login} /></Frame>;
  }

  const sub = <>Public repositories of <a href={me.user.html_url} target="_blank" rel="noopener">{`@${me.user.login}`}</a>, most recently pushed first. Explore one to ask about it or find code in it.</>;
  let body: ReactNode;
  if (repos.isPending || (repos.isFetching && repos.isError)) {
    body = <Skeleton />;
  } else if (repos.isError) {
    body = <StateBlock icon="alert" tone="error" title="Couldn't load your repositories" text={friendlyError(repos.error)}
      actions={<Button onClick={() => void repos.refetch()}><Icon name="refresh" size={14} />Try again</Button>} />;
  } else {
    const all = repos.data.repos || [];
    const shown = filterGithubRepos(all, query);
    body = !all.length
      ? <StateBlock icon="search" title="No public repositories yet" text="Repositories you make public on GitHub will show up here." />
      : <>
          <div className="ghfilter">
            <form className="input" role="search" onSubmit={(e) => e.preventDefault()}>
              <Icon name="search" size={18} />
              <Input type="search" placeholder={`Filter ${count(all.length)}`} aria-label="Filter your repositories"
                autoComplete="off" value={query} onChange={(e) => { setQuery(e.target.value); setTyped(true); }} />
            </form>
            <p className="sr-only" role="status">{typed ? (shown.length ? count(shown.length) : "No repositories match") : ""}</p>
          </div>
          {shown.length
            ? <ul className="rows" aria-label="Your public repositories">
                {shown.map((r) => (
                  <Row key={r.full_name} r={r} pending={explore.isPending && explore.variables === r.full_name} busy={explore.isPending}
                    onExplore={() => explore.mutate(r.full_name)} />
                ))}
              </ul>
            : <p className="empty-search">{`No repository matches “${query.trim()}”.`}</p>}
        </>;
  }
  return <Frame sub={sub}>{body}</Frame>;
}
