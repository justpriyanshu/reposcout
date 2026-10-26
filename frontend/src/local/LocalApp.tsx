// The local page served by `reposcout serve`: Ask, Review a diff and Find code over one local index.
import { useMutation, useQuery, type UseMutationResult } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Icon, LogoMark } from "@/components/Icon";
import { ThemeButton } from "@/components/ThemeButton";
import { CodeBlock, FileLoc, Html, Skel, StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, postLocal } from "@/lib/api";
import { addCitations, type Source } from "@/lib/cite";
import { fmt, seconds } from "@/lib/format";
import { markdown } from "@/lib/markdown";

const SEV_LABEL: Record<string, string> = { critical: "Critical", major: "Major", minor: "Minor", nit: "Nit" };
type TabId = "ask" | "review" | "find";

interface Stats { repo?: string; llm?: string | null; index?: { files?: number; chunks?: number } }
interface AskReply { answer: string; sources?: Source[]; cited?: number[]; latency_ms?: number }
interface Finding { path: string; line: number; severity: string; category: string; title: string; message: string; suggestion?: string; source?: string }
interface ReviewReply { headline: string; findings?: Finding[]; file_summaries?: Record<string, string>; stats?: { latency_ms?: number; llm_calls?: number } }
interface Hit { path: string; start_line: number; end_line: number; name?: string; text: string }

/* ================================================================ stats panel */
function StatsBar() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api<Stats>("/stats") });
  let content: ReactNode;
  if (stats.isPending) {
    content = <><span className="av" aria-hidden="true">RS</span><span className="nm">Loading index…</span><span className="meta"><Skel className="w-60" /></span></>;
  } else if (stats.isError) {
    content = <><span className="av" aria-hidden="true">RS</span><span className="nm">Local index</span>
      <span className="meta">Couldn't load the index stats. The tools below may still work.</span></>;
  } else {
    const s = stats.data;
    const i = s.index || {};
    const name = String(s.repo || "").split(/[\\/]/).filter(Boolean).pop() || "Local index";
    const model = s.llm ? `model ${s.llm}` : "no LLM";
    content = <>
      <span className="av" aria-hidden="true">{name.replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "RS"}</span>
      <span className="nm" title={s.repo || name}>{name}</span>
      <span className="meta">{`${fmt(i.files)} files · ${fmt(i.chunks)} sections · ${model}`}</span>
    </>;
  }
  return <div className="repobar" id="stats" aria-live="polite">{content}</div>;
}

/* ================================================================ shared output */
/** One panel's result area: skeletons while waiting, the error block on failure, else the result. */
function Out<T, V>({ id, run, skeleton, retry, children }: {
  id: string; run: UseMutationResult<T, Error, V>; skeleton: ReactNode; retry: () => void; children: (data: T) => ReactNode;
}) {
  return (
    <div className="out" id={id} aria-live="polite" aria-busy={run.isPending || undefined}>
      {run.isPending ? skeleton
        : run.isError ? <StateBlock icon="alert" tone="error" title="That didn't work" text={run.error.message} actions={<Button onClick={retry}>Try again</Button>} />
        : run.isSuccess ? children(run.data)
        : null}
    </div>
  );
}

const AnswerSkeleton = () => <div className="a" aria-hidden="true"><Skel className="w-80" /><Skel /><Skel className="w-60" /></div>;
const HitSkeleton = () => (
  <div aria-hidden="true">{[0, 1].map((i) => (
    <div key={i} className="hit"><header><Skel className="w-60" /></header><div className="hit-skel"><Skel /><Skel className="w-80" /><Skel className="w-60" /></div></div>
  ))}</div>
);
const Keys = () => <span className="keys"><kbd>Ctrl/⌘</kbd> + <kbd>Enter</kbd></span>;

/* ================================================================ Ask */
function AskPanel({ field }: { field: React.RefObject<HTMLTextAreaElement | null> }) {
  const form = useRef<HTMLFormElement>(null);
  const [question, setQuestion] = useState("");
  const ask = useMutation({ mutationFn: (q: string) => postLocal<AskReply>("/ask", { question: q }) });
  function submit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q) { field.current?.focus(); return; }
    if (!ask.isPending) ask.mutate(q);
  }
  return (
    <>
      <form className="input box" id="ask-form" ref={form} onSubmit={submit}>
        <Textarea id="question" ref={field} rows={3} aria-label="Your question" placeholder="Ask about the codebase, e.g. “How are webhook signatures verified?”"
          value={question} onChange={(e) => setQuestion(e.target.value)} />
      </form>
      <div className="actrow"><Button variant="primary" type="submit" form="ask-form" disabled={ask.isPending}>Ask</Button><Keys /></div>
      <Out id="ask-out" run={ask} skeleton={<AnswerSkeleton />} retry={() => form.current?.requestSubmit()}>
        {(data) => {
          const cited = new Set(data.cited || []);
          return <>
            <div className="a">
              {/* the local index has no URLs to link to: citations are plain badges */}
              <Html className="md" html={addCitations(markdown(data.answer), data.sources, false)} />
              {(data.sources || []).length ? (
                <div className="srcs" aria-label="Sources">
                  {data.sources!.map((s) => (
                    <span key={s.ref} className={cited.has(s.ref) ? "src" : "src uncited"}
                      title={`${s.path}:${s.start_line}-${s.end_line}${s.name ? ` (${s.name})` : ""}${cited.has(s.ref) ? "" : ", not cited"}`}>
                      <span className="n">{s.ref}</span><FileLoc path={s.path} start={s.start_line} end={s.end_line} max={40} />
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="afoot"><span>{seconds(data.latency_ms)}</span></div>
          </>;
        }}
      </Out>
    </>
  );
}

/* ================================================================ Review a diff */
function FindingCard({ f }: { f: Finding }) {
  const rule = String(f.source || "").replace(/^rule:/, "");
  return (
    <article className={`finding s-${f.severity}`}>
      <div className="fh">
        <span className="sv">{SEV_LABEL[f.severity] || f.severity}</span><span className="t">{f.title}</span>
        <span className="where" title={`${f.path}:${f.line}`}><FileLoc path={f.path} start={f.line} max={40} /></span>
      </div>
      <p>{f.message}</p>
      {f.suggestion ? <><p className="fix">Suggested change</p><pre>{f.suggestion}</pre></> : null}
      <div className="by">
        {f.source === "llm" ? <><Icon name="bot" size={14} />{`AI review · ${f.category}`}</> : <><Icon name="shield" size={14} />{`Fixed check (${rule}) · ${f.category}`}</>}
      </div>
    </article>
  );
}

function ReviewPanel({ field }: { field: React.RefObject<HTMLTextAreaElement | null> }) {
  const form = useRef<HTMLFormElement>(null);
  const [diff, setDiff] = useState("");
  const [minSev, setMinSev] = useState("nit");
  const review = useMutation({ mutationFn: (v: { diff: string; min_severity: string }) => postLocal<ReviewReply>("/review", v) });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!diff.trim()) { field.current?.focus(); return; }
    if (!review.isPending) review.mutate({ diff, min_severity: minSev });
  }
  return (
    <>
      <form className="input box" id="review-form" ref={form} onSubmit={submit}>
        <Textarea className="mono" id="diff" ref={field} rows={8} aria-label="Unified diff" placeholder="Paste a unified diff (output of git diff)…"
          value={diff} onChange={(e) => setDiff(e.target.value)} />
      </form>
      <div className="actrow">
        <Button variant="primary" type="submit" form="review-form" disabled={review.isPending}>Review</Button>
        <label className="minsev" htmlFor="min-sev">Minimum severity
          <span className="input">
            <select id="min-sev" value={minSev} onChange={(e) => setMinSev(e.target.value)}>
              <option value="nit">Nit</option><option value="minor">Minor</option><option value="major">Major</option><option value="critical">Critical</option>
            </select>
          </span>
        </label>
        <Keys />
      </div>
      <Out id="review-out" run={review} skeleton={<AnswerSkeleton />} retry={() => form.current?.requestSubmit()}>
        {(data) => {
          const files = Object.entries(data.file_summaries || {});
          const stats = data.stats || {};
          return <>
            <div className="verdict stack">
              <strong>{data.headline}</strong>
              {files.length ? <ul>{files.map(([path, text]) => <li key={path}><b>{path}</b> {text}</li>)}</ul> : null}
              <span className="note">{`${seconds(stats.latency_ms)} · ${stats.llm_calls || 0} LLM call${stats.llm_calls === 1 ? "" : "s"}`}</span>
            </div>
            {(data.findings || []).map((f, i) => <FindingCard key={`${f.path}:${f.line}:${i}`} f={f} />)}
          </>;
        }}
      </Out>
    </>
  );
}

/* ================================================================ Find code */
function FindPanel({ field }: { field: React.RefObject<HTMLInputElement | null> }) {
  const form = useRef<HTMLFormElement>(null);
  const [query, setQuery] = useState("");
  const find = useMutation({ mutationFn: (q: string) => postLocal<{ results: Hit[] }>("/search", { query: q, k: 8 }) });
  function submit(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) { field.current?.focus(); return; }
    if (!find.isPending) find.mutate(q);
  }
  return (
    <>
      <form className="input find" id="find-form" role="search" ref={form} onSubmit={submit}>
        <Icon name="search" size={18} />
        <Input type="search" id="query" ref={field} aria-label="Search the code" placeholder="Function names, keywords, or a description" autoComplete="off"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button variant="primary" type="submit" disabled={find.isPending}>Search</Button>
      </form>
      <Out id="find-out" run={find} skeleton={<HitSkeleton />} retry={() => form.current?.requestSubmit()}>
        {(data) => !data.results.length
          ? <StateBlock icon="search" title={`No matches for “${find.variables}”`} text="Try other words, or describe what the code does." />
          : data.results.map((r, i) => (
            <article key={`${r.path}:${r.start_line}:${i}`} className="hit">
              <header>
                <span className="path" title={`${r.path}:${r.start_line}-${r.end_line}`}><FileLoc path={r.path} start={r.start_line} end={r.end_line} /></span>
                {r.name ? <span className="pill" title={r.name}>{r.name}</span> : null}
              </header>
              <CodeBlock text={r.text} firstLine={r.start_line} maxLines={14} />
            </article>
          ))}
      </Out>
    </>
  );
}

/* ================================================================ page */
export function LocalApp() {
  const [tab, setTab] = useState<TabId>("ask");
  const [focusTab, setFocusTab] = useState<{ tab: TabId } | null>(null);
  const askField = useRef<HTMLTextAreaElement>(null);
  const reviewField = useRef<HTMLTextAreaElement>(null);
  const findField = useRef<HTMLInputElement>(null);

  // A click on a tab puts the cursor in its field; arrow keys only move between tabs.
  useEffect(() => {
    if (!focusTab) return;
    ({ ask: askField, review: reviewField, find: findField })[focusTab.tab].current?.focus();
  }, [focusTab]);

  // Ctrl/Cmd+Enter submits the form being typed in.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        const form = (e.target as Element | null)?.closest?.("form");
        if (form) { e.preventDefault(); form.requestSubmit(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const trigger = (id: TabId, label: string) => (
    <TabsTrigger value={id} onClick={() => setFocusTab({ tab: id })}>{label}</TabsTrigger>
  );

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as TabId)} activationMode="automatic">
      <header className="nav"><div className="wrap">
        <span className="logo"><LogoMark /><span className="t">RepoScout</span></span>
        <TabsList aria-label="Tools">
          {trigger("ask", "Ask")}
          {trigger("review", "Review a diff")}
          {trigger("find", "Find code")}
        </TabsList>
        <div className="right"><ThemeButton /></div>
      </div></header>
      <main className="wrap local" id="main" tabIndex={-1}>
        <StatsBar />
        <TabsContent value="ask" hidden={tab !== "ask"}><AskPanel field={askField} /></TabsContent>
        <TabsContent value="review" hidden={tab !== "review"}><ReviewPanel field={reviewField} /></TabsContent>
        <TabsContent value="find" hidden={tab !== "find"}><FindPanel field={findField} /></TabsContent>
      </main>
    </Tabs>
  );
}
