// Review a PR view (spec §6.7). Needs no index; spends the visitor's own key, so it starts only on their click.
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Icon } from "@/components/Icon";
import { Html, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, KEY_ERRORS, api, asApiError, friendlyError } from "@/lib/api";
import { fmt, plural } from "@/lib/format";
import { toolHash } from "@/lib/links";
import { escapeHtml, inline } from "@/lib/markdown";
import { SEVERITIES, approveReview, consumeApproval, defaultSeverities, isApproved, parsePrInput, reviewCacheKey, type Severity, type SeverityCounts } from "@/lib/reviewdata";
import type { ToolId } from "@/lib/routes";
import { store } from "@/lib/store";
import { toast } from "@/lib/toast";
import { LookupTool, Picker, ToolFrame, reload, type RepoRoute } from "../repo";
import { handleError, onKeyChange, openKeyDialog, settings, useConfig } from "../settings";

const SEV_LABEL: Record<string, string> = { critical: "Critical", major: "Major", minor: "Minor", nit: "Nit" };
const STATE_TONE: Record<string, string> = { open: "ok", merged: "busy", closed: "bad" };
const hoverDevice = () => matchMedia("(hover: hover)").matches;

export interface Finding {
  path: string; line: number; severity: string; category: string; title: string; message?: string;
  suggestion?: string; source?: string; url?: string;
}
export interface ReviewData {
  pull: { repo?: string; number: number; title?: string; state?: string; author?: string; url?: string; changed_files?: number; additions?: number; deletions?: number };
  findings?: Finding[];
  files_reviewed?: string[];
  files_skipped?: Record<string, string>;
  file_summaries?: Record<string, string>;
  counts?: SeverityCounts;
  used_repo_context?: boolean;
  stats?: { llm_errors?: number };
  markdown?: string;
}

/** Start a review the visitor asked for: approve it for this tab, then open its page. */
export function requestReview(owner: string, name: string, pull: number) {
  approveReview(owner, name, pull);
  const next = toolHash("review", owner, name, pull);
  if (location.hash === next) reload(); else location.hash = next;
}

/* ================================================================ #/review/o/n */
function PrForm({ owner, name }: { owner: string; name: string }) {
  const config = useConfig();
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (hoverDevice()) input.current?.focus({ preventScroll: true }); }, []);
  function submit(e: FormEvent) {
    e.preventDefault();
    const pr = parsePrInput(value, owner, name);
    if (!pr) { setInvalid("Enter a pull request number like 1234, or paste its link."); input.current?.focus(); return; }
    setInvalid("");
    requestReview(pr.owner, pr.name, pr.pull);
  }
  return (
    <>
      <form className="input prform" noValidate onSubmit={submit}>
        <Icon name="link" size={18} />
        <Input ref={input} type="text" placeholder="Number or link, e.g. 1234" aria-label="Pull request number or link" autoComplete="off"
          autoCapitalize="none" spellCheck={false} aria-describedby="pr-msg" aria-invalid={invalid ? true : undefined}
          value={value} onChange={(e) => { setValue(e.target.value); if (invalid) setInvalid(""); }} />
        <Button variant="primary" type="submit">Review</Button>
      </form>
      <p id="pr-msg" className="invalid" role="alert" hidden={!invalid}>{invalid}</p>
      <p className="note">{`The review uses your ${config.provider} key, which spends a little of your free quota. Nothing is posted to GitHub.`}</p>
      <p className="note"><a href={`https://github.com/${owner}/${name}/pulls`} target="_blank" rel="noopener">Browse open pull requests on GitHub</a></p>
    </>
  );
}

/* ================================================================ #/review/o/n/num */
/** A plain card for the steps before the result: no key, "Start the review?", the progress. */
const StepCard = ({ title, children }: { title: string; children: ReactNode }) => <section className="progress"><h2>{title}</h2>{children}</section>;

type Phase = "nokey" | "cached" | "confirm" | "run";

function ReviewPage({ owner, name, pull }: { owner: string; name: string; pull: number }) {
  const config = useConfig();
  const url = `https://github.com/${owner}/${name}/pull/${pull}`;
  const model = settings.model || config.default_model;
  const cacheKey = reviewCacheKey(owner, name, pull, settings.model);
  // Decided once when the page opens (the router remounts the view for a fresh start).
  const [phase] = useState<Phase>(() => {
    if (!settings.key) return "nokey";
    const cached = store.get<ReviewData>(cacheKey, true);
    if (cached && cached.pull) { consumeApproval(); return "cached"; }
    if (!isApproved(owner, name, pull)) return "confirm";
    consumeApproval();
    return "run";
  });
  const runAgain = () => { store.del(cacheKey); approveReview(owner, name, pull); reload(); };

  if (phase === "nokey") return <NoKey />;
  if (phase === "cached") return <Result data={store.get<ReviewData>(cacheKey, true)!} runAgain={runAgain} fresh={false} />;
  if (phase === "confirm") {
    return (
      <StepCard title="Start the review?">
        <p>RepoScout will read the changes in <a href={url} target="_blank" rel="noopener">{`${owner}/${name} #${pull}`}</a>
          {` and review them with your ${config.provider} key (model ${model}). This uses some of your free quota.`}</p>
        <div className="actions"><StartButton onClick={() => { approveReview(owner, name, pull); reload(); }} /></div>
      </StepCard>
    );
  }
  return <Run owner={owner} name={name} pull={pull} url={url} cacheKey={cacheKey} model={model} runAgain={runAgain} />;
}

function StartButton({ onClick }: { onClick: () => void }) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (hoverDevice()) btn.current?.focus({ preventScroll: true }); }, []);
  return <Button ref={btn} variant="primary" onClick={onClick}>Start review</Button>;
}

function NoKey() {
  const config = useConfig();
  // Start over once a key is saved, from this button or the nav (one reload, so an approval survives).
  useEffect(() => onKeyChange(() => { if (settings.key) reload(); }), []);
  return (
    <StepCard title="Add your API key to start">
      <p>{`Reviews use your own free ${config.provider} key. It stays in your browser.`}</p>
      <div className="actions"><Button variant="primary" onClick={() => openKeyDialog("")}>Add API key</Button></div>
    </StepCard>
  );
}

function Run({ owner, name, pull, url, cacheKey, model, runAgain }: {
  owner: string; name: string; pull: number; url: string; cacheKey: string; model: string; runAgain: () => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  const [status, setStatus] = useState(`Reviewing pull request #${pull}`);
  const review = useMutation({
    mutationFn: () => api<ReviewData>("/api/review", { method: "POST", body: { url, min_severity: "nit" }, withKey: true }),
    // A mutation-level callback, so the result is kept even if the visitor left: it cost quota.
    onSuccess: (data) => store.set(cacheKey, data, true),
  });
  const retry = () => { approveReview(owner, name, pull); reload(); };

  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    review.mutate(undefined, {
      onSuccess: () => setStatus("Review ready."),
      onError: (err) => {
        setStatus("The review failed.");
        const failure = asApiError(err);
        if (KEY_ERRORS.has(failure.code)) handleError(failure, retry);
      },
      onSettled: () => clearInterval(timer),
    });
    return () => clearInterval(timer);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  let body;
  if (review.isSuccess) body = <Result data={review.data} runAgain={runAgain} fresh />;
  else if (review.isError) {
    body = (
      <div className="enter">
        <StateBlock icon="alert" tone="error" title="The review didn't finish" text={friendlyError(review.error instanceof ApiError ? review.error : asApiError(review.error))}
          actions={<>
            <Button variant="primary" onClick={retry}>Try again</Button>
            <Button asChild><a href={toolHash("review", owner, name)}>Review another pull request</a></Button>
          </>} />
      </div>
    );
  } else {
    const line = elapsed < 4 ? "Fetching the pull request…"
      : `Reviewing each changed file with ${model || "the AI model"}… ${elapsed}s. Free keys can take a minute on bigger pull requests.`;
    body = (
      <StepCard title={`Reviewing pull request #${pull}`}>
        <p>{line}</p>
        <div className="bar wait" role="progressbar" aria-label="Review progress"><span /></div>
      </StepCard>
    );
  }
  return (
    <>
      <div aria-busy={review.isPending || review.isIdle ? true : undefined}>{body}</div>
      <p className="sr-only" role="status">{status}</p>
    </>
  );
}

/* ================================================================ result */
function FindingCard({ f }: { f: Finding }) {
  const rule = String(f.source || "").replace(/^rule:/, "");
  return (
    <article className={`finding s-${f.severity}`}>
      <div className="fh">
        <span className="sv">{SEV_LABEL[f.severity] || f.severity}</span><span className="t">{f.title}</span>
        {f.url ? <a className="where" href={f.url} target="_blank" rel="noopener">{`line ${f.line}`}</a> : <span className="where">{`line ${f.line}`}</span>}
      </div>
      {/* escaped first; inline() adds code, bold and https links */}
      <Html as="p" html={inline(escapeHtml(f.message || ""))} />
      {f.suggestion ? <><p className="fix">Suggested change</p><pre>{f.suggestion}</pre></> : null}
      <div className="by">
        {f.source === "llm" ? <><Icon name="bot" size={14} />{`AI review · ${f.category}`}</> : <><Icon name="shield" size={14} />{`Fixed check (${rule}) · ${f.category}`}</>}
      </div>
    </article>
  );
}

export function Result({ data, runAgain, fresh }: { data: ReviewData; runAgain: () => void; fresh: boolean }) {
  const pr = data.pull;
  const [prOwner, prName] = String(pr.repo || "").split("/");
  const findings = Array.isArray(data.findings) ? data.findings : [];
  const reviewed = Array.isArray(data.files_reviewed) ? data.files_reviewed : [];
  const summaries = data.file_summaries || {};
  const counts = data.counts || {};
  const total = SEVERITIES.reduce((a, s) => a + (counts[s] || 0), 0);
  const [active, setActive] = useState<Set<Severity>>(() => defaultSeverities(counts));
  const firstChip = useRef<HTMLButtonElement>(null);

  const copy = () => {
    const done = () => toast("Review copied as Markdown. Paste it into a GitHub comment.", { kind: "success" });
    const fail = () => toast("Couldn't copy. Select the text and copy it instead.", { kind: "error" });
    if (navigator.clipboard) navigator.clipboard.writeText(data.markdown || "").then(done, fail); else fail();
  };
  const toggle = (s: Severity) => setActive((prev) => { const next = new Set(prev); if (next.has(s)) next.delete(s); else next.add(s); return next; });

  const shown = findings.filter((f) => active.has(f.severity as Severity));
  const byFile: Record<string, Finding[]> = {};
  for (const f of shown) (byFile[f.path] ||= []).push(f);
  const files = shown.length || !total ? reviewed.filter((p) => byFile[p] || summaries[p]) : [];
  const skipped = Object.entries(data.files_skipped || {});
  const chips = SEVERITIES.filter((s) => counts[s]);

  const notes = [];
  if (!data.used_repo_context && prOwner && prName) {
    notes.push(<div key="tip" className="banner"><Icon name="info" size={16} /><span>Tip: <a href={toolHash("ask", prOwner, prName)}>{`index ${pr.repo}`}</a> first, and reviews will also read related code from the project.</span></div>);
  }
  if (skipped.length) notes.push(<p key="skip" className="note">{`Not reviewed: ${skipped.map(([p, why]) => `${p} (${why})`).join(", ")}`}</p>);
  if (data.stats && data.stats.llm_errors) {
    notes.push(<div key="llm" className="banner error"><Icon name="alert" size={16} />
      <span>{`The AI couldn't review ${data.stats.llm_errors} file(s), usually because of rate limits. Fixed checks still ran. Try "Run again" in a minute.`}</span></div>);
  }

  return (
    <div className={fresh ? "result enter" : "result"}>
      <div className="prhead">
        <h2>{pr.title || `Pull request #${pr.number}`}</h2>
        <div className="prmeta">
          {pr.state ? <span className={`pill ${STATE_TONE[pr.state] || ""}`}>{pr.state[0].toUpperCase() + pr.state.slice(1)}</span> : null}
          <span>{pr.author ? `#${pr.number} by ${pr.author}` : `#${pr.number}`}</span>
          <span>{plural(pr.changed_files || 0, "file")}</span>
          <span className="mono"><span className="plus">{`+${fmt(pr.additions)}`}</span> <span className="minus">{`−${fmt(pr.deletions)}`}</span></span>
          <a href={pr.url} target="_blank" rel="noopener">Open on GitHub<Icon name="ext" size={12} /></a>
        </div>
      </div>
      <div className="verdict">
        {total ? null : <span className="okmark"><Icon name="check" size={16} /></span>}
        <strong>{total ? `${plural(total, "thing")} to look at` : "No problems found"}</strong>
        <span className="note">{`${plural(reviewed.length, "file")} reviewed`}</span>
        <div className="act">
          <Button onClick={copy}><Icon name="copy" size={14} />Copy as Markdown</Button>
          <Button onClick={runAgain}><Icon name="refresh" size={14} />Run again</Button>
        </div>
        {total ? (
          <div className="chips" role="group" aria-label="Filter by severity">
            {chips.map((s, i) => (
              <button key={s} ref={i === 0 ? firstChip : undefined} className={`sev s-${s}`} type="button" aria-pressed={active.has(s)} onClick={() => toggle(s)}>
                {`${fmt(counts[s])} ${SEV_LABEL[s]}`}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {notes.length ? <div className="notes">{notes}</div> : null}
      <div className="fgroups">
        {files.map((path) => {
          const found = (byFile[path] || []).slice().sort((a, b) => a.line - b.line);
          return (
            <section key={path} className="fgroup">
              <h3><span>{path}</span>{found.length ? <span className="pill">{fmt(found.length)}</span> : null}</h3>
              {summaries[path] ? <p>{summaries[path]}</p> : null}
              {found.map((f, i) => <FindingCard key={`${f.line}:${i}`} f={f} />)}
            </section>
          );
        })}
        {!shown.length && total ? (
          <StateBlock icon="search" title="Every finding is filtered out" text="Turn a severity back on above, or show them all."
            actions={<Button onClick={() => { setActive(new Set(SEVERITIES)); firstChip.current?.focus(); }}>Show all findings</Button>} />
        ) : null}
      </div>
    </div>
  );
}

export default function Review({ route }: { route: (RepoRoute & { pull?: number }) | { tool: ToolId } }) {
  if (!("owner" in route)) return <ToolFrame toolId="review"><Picker toolId="review" /></ToolFrame>;
  return (
    <LookupTool route={route}>
      {route.pull ? <ReviewPage owner={route.owner} name={route.name} pull={route.pull} /> : <PrForm owner={route.owner} name={route.name} />}
    </LookupTool>
  );
}
