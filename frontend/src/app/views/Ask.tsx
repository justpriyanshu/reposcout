// Ask view: chat about one indexed repository. Answers are Markdown with [n] citations that link to GitHub.
import { useMutation } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/Icon";
import { CopyButton, FileLoc, Html, Skel } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { ApiError, KEY_ERRORS, api, asApiError, friendlyError } from "@/lib/api";
import { addCitations, type Source } from "@/lib/cite";
import { markdown } from "@/lib/markdown";
import type { ToolId } from "@/lib/routes";
import { store } from "@/lib/store";
import { IndexedTool, Picker, ToolFrame, repoPath, type RepoRecord, type RepoRoute } from "../repo";
import { handleError, requireKey } from "../settings";

const STARTERS = [
  "What does this project do, and how is it organised?",
  "Where is the main entry point?",
  "How is configuration loaded?",
  "How are errors handled?",
];
const SLOW_NOTE = "Still working. Free keys can take a little longer.";
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const hoverDevice = () => matchMedia("(hover: hover)").matches;

interface Turn {
  question: string;
  answer?: string;
  error?: string;
  sources?: Source[];
  cited?: number[];
  model?: string;
  latency_ms?: number;
  pending?: boolean;
  slow?: boolean;
}
interface AskReply { answer: string; sources: Source[]; cited: number[]; model: string; latency_ms: number }
interface AskVars { question: string; history: { question: string; answer?: string }[] }

function AnswerCard({ turn, retry }: { turn: Turn; retry: () => void }) {
  if (turn.pending) {
    return (
      <div className="a">
        <div className="pending" role="status">
          <span className="lbl">Reading the code…</span>
          <Skel className="w-90" /><Skel className="w-75" /><Skel className="w-60" />
          {turn.slow ? <span className="lbl">{SLOW_NOTE}</span> : null}
        </div>
      </div>
    );
  }
  if (turn.error) {
    return (
      <div className="banner error" role="alert"><Icon name="alert" size={16} /><span>{turn.error}</span>
        <Button onClick={retry}>Try again</Button>
      </div>
    );
  }
  const cited = new Set(turn.cited || []);
  return (
    <div>
      <div className="a">
        <Html className="md" html={addCitations(markdown(turn.answer), turn.sources)} />
        {turn.sources && turn.sources.length ? (
          <div className="srcs" aria-label="Sources">
            {turn.sources.map((s) => {
              const isCited = cited.has(s.ref);
              return (
                <a key={s.ref} className={isCited ? "src" : "src uncited"} href={s.url} target="_blank" rel="noopener"
                  title={`${s.path}:${s.start_line}-${s.end_line}${s.name ? ` (${s.kind} ${s.name})` : ""}${isCited ? "" : ", not cited"}`}>
                  <span className="n">{s.ref}</span><FileLoc path={s.path} start={s.start_line} end={s.end_line} max={40} />
                </a>
              );
            })}
          </div>
        ) : null}
      </div>
      <div className="afoot">
        {turn.model ? <span className="model" title={turn.model}>{turn.model}</span> : null}
        {turn.latency_ms ? <span>{`${(turn.latency_ms / 1000).toFixed(1)}s`}</span> : null}
        <CopyButton getText={() => turn.answer || ""} />
      </div>
    </div>
  );
}

function Chat({ owner, name, record }: { owner: string; name: string; record: RepoRecord }) {
  const chatKey = `rs.chat.${record.key || `${owner}/${name}`}`;
  // The turns are kept in a ref (as the request outlives a re-render, and even the page) and drawn on bump().
  const turns = useRef<Turn[]>([]);
  const [loaded] = useState(() => {
    const saved = store.get<Turn[]>(chatKey);
    turns.current = Array.isArray(saved) ? saved.filter((t) => t && typeof t.question === "string") : [];
    return true;
  });
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [fresh, setFresh] = useState<Turn | null>(null);
  const [scroll, setScroll] = useState<{ block: ScrollLogicalPosition } | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const ask = useMutation({
    mutationFn: ({ question, history }: AskVars) =>
      api<AskReply>(`${repoPath(owner, name)}/ask`, { method: "POST", body: { question, history }, withKey: true }),
  });

  useEffect(() => {
    alive.current = true;
    const pending = timers.current;
    if (hoverDevice()) setTimeout(() => alive.current && textarea.current?.focus({ preventScroll: true }), 0);
    return () => { alive.current = false; pending.forEach(clearTimeout); };
  }, []);

  const grow = () => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  };
  useLayoutEffect(grow, [draft]);
  useLayoutEffect(() => {
    if (!scroll) return;
    thread.current?.lastElementChild?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: scroll.block });
    setScroll(null);
  }, [scroll]);

  const busy = turns.current.some((t) => t.pending);
  const persist = () => store.set(chatKey, turns.current.filter((t) => !t.pending).slice(-20).map(({ question, answer, error, sources, cited, model, latency_ms }) =>
    ({ question, answer, error, sources, cited, model, latency_ms })));

  function clear() {
    turns.current = [];
    persist(); bump();
    textarea.current?.focus();
  }

  async function send(text: string) {
    const question = (text || "").trim();
    if (question.length < 2 || turns.current.some((t) => t.pending)) return;
    if (!requireKey(() => void send(question), "Add your API key to ask questions.")) return; // the draft stays in the box
    const history = turns.current.filter((t) => !t.error && !t.pending).slice(-3).map((t) => ({ question: t.question, answer: t.answer }));
    const turn: Turn = { question, pending: true };
    turns.current = [...turns.current, turn];
    setDraft(""); bump();
    setScroll({ block: "nearest" });
    const slow = setTimeout(() => { turn.slow = true; if (alive.current) bump(); }, 8000);
    timers.current.add(slow);
    let failure: ApiError | null = null;
    try {
      const data = await ask.mutateAsync({ question, history });
      Object.assign(turn, { pending: false, answer: data.answer, sources: data.sources, cited: data.cited, model: data.model, latency_ms: data.latency_ms });
    } catch (err) {
      failure = asApiError(err);
    }
    clearTimeout(slow); timers.current.delete(slow);
    const kept = turns.current.includes(turn); // Clear chat during the request drops it
    if (failure && KEY_ERRORS.has(failure.code)) {
      if (kept) turns.current = turns.current.filter((t) => t !== turn);
      if (!alive.current) return;
      bump(); setDraft(question);
      handleError(failure, () => void send(question));
      return;
    }
    if (failure) Object.assign(turn, { pending: false, error: friendlyError(failure) });
    if (!kept) return;
    turns.current = [...turns.current];
    persist(); // kept even if the visitor left: the answer cost quota
    if (!alive.current) return; // never write to a page we left
    setFresh(turn); bump();
    setStatus(turn.error ? "The answer failed." : "Answer ready.");
    textarea.current?.focus({ preventScroll: true });
    // Bring the question to the top so the answer reads from its first line.
    setScroll({ block: "start" });
  }

  function submit(e: FormEvent) { e.preventDefault(); void send(draft); }

  return (
    <>
      <div className="chat">
        <div className="thread" ref={thread}>
          {loaded && !turns.current.length ? (
            <div>
              <div className="state intro"><h3>{`Ask anything about ${name}`}</h3><p>Answers come from the indexed code and link to the exact lines on GitHub.</p></div>
              <div className="starters">
                {STARTERS.map((q) => <button key={q} className="starter" type="button" onClick={() => void send(q)}>{q}</button>)}
              </div>
            </div>
          ) : null}
          {turns.current.map((t, i) => (
            <div key={i} className="ex">
              <div className="q">{t.question}</div>
              {/* Only the arriving answer animates; the question above it is already on screen. */}
              <div className={t === fresh ? "enter" : undefined}>
                <AnswerCard turn={t} retry={() => { turns.current = turns.current.filter((x) => x !== t); bump(); void send(t.question); }} />
              </div>
            </div>
          ))}
        </div>
        <div className="composer">
          <form className="input" onSubmit={submit}>
            <Textarea ref={textarea} rows={1} placeholder={name.length > 28 ? "Ask about this repository…" : `Ask about ${name}…`}
              aria-label="Your question" maxLength={2000} value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); }
              }} />
            <Button variant="primary" size="icon" type="submit" aria-label="Send" disabled={!draft.trim() || busy}><Icon name="arrow" size={18} /></Button>
          </form>
          <div className="hint">
            <span className="keys"><kbd>Enter</kbd> to send · <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line</span>
            <Button variant="quiet" hidden={!turns.current.length} onClick={clear}>Clear chat</Button>
          </div>
        </div>
      </div>
      <div className="sr-only" role="status">{status}</div>
    </>
  );
}

export default function Ask({ route }: { route: RepoRoute | { tool: ToolId } }) {
  if (!("owner" in route)) return <ToolFrame toolId="ask"><Picker toolId="ask" /></ToolFrame>;
  return <IndexedTool route={route}>{(record) => <Chat owner={route.owner} name={route.name} record={record} />}</IndexedTool>;
}
