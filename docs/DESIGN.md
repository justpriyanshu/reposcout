# Design notes

Why RepoScout is built the way it is, and the trade-offs behind each choice. Useful when explaining the project in an interview.

## 1. One-sentence pitch

RepoScout indexes a code repository so you can ask questions about it in plain English, and it reviews code changes by combining fast pattern-based checks with an LLM that can see related code from the rest of the repository.

## 2. Indexing

**Chunking follows code structure.** Embedding a whole file blurs many topics into one vector; fixed-size windows cut functions in half. Python files are parsed with `ast`, so each chunk is a complete function, method or class with its decorators. Definitions longer than the limit (80 lines by default) are split along their nested definitions (a big class becomes one chunk per method plus a "header" chunk; a big factory function becomes one chunk per inner function), and only code with no nested definitions falls back to overlapping windows. Thirteen other languages are parsed with tree-sitter (grammars from `tree-sitter-language-pack`, downloaded once and cached; Docker images include them). Each language's *tags query*, the same query GitHub's code navigation uses, marks definitions, and the chunker applies the same split-along-nested-definitions rule as for Python, starting each chunk at its `export`, decorator, attribute or doc comment. Markdown, SQL and shell, and any language whose grammar can't be loaded, use regexes that recognise definition lines (`func`, `fn`, `class`, `export function`, Markdown headings...): coarser for unusual formatting, but never a hard dependency.

**A small symbol graph.** The tags queries also mark call sites (`@reference.call`); a syntax-tree walk fills in call shapes some queries miss (TypeScript's has none). Every chunk stores the names it calls in a `refs` table, so "which chunks call `verify_token`?" is an indexed lookup. It is name-based, not type-resolved like SCIP or a language server: two functions with the same name share callers. That keeps it language-agnostic and cheap, and the reviewer only uses it to pick context, where an extra snippet costs little.

**Each chunk is embedded with a small header** (`File: path`, `Function: name`). The name and path carry a lot of meaning that the code body alone does not.

**Incremental re-indexing.** Every file's SHA-256 is stored. A re-index reads each file, compares hashes, and only re-chunks and re-embeds files that changed; files that disappeared are removed. Replacing a file's chunks happens in one SQLite transaction, so a crash never leaves half a file indexed. Two version markers make upgrades safe: `schema_version` (the database layout) and `chunker_version` (chunking logic). If either changes, the affected data is rebuilt automatically.

**Embedding-model safety.** Vectors from different models live in different spaces, so comparing them gives meaningless results. The index records which embedder built it and refuses queries from a different one, with a message telling the user to run `index --rebuild`.

**File selection.** `git ls-files --cached --others --exclude-standard` gives exactly the files git would consider, respecting every nested `.gitignore`. Outside git, the root `.gitignore` is applied with `pathspec`. Built-in ignores cover dependencies, build output, lockfiles, binaries and minified files; `.reposcoutignore` adds project-specific rules. Files with NUL bytes (binary) or above the size limit are skipped.

## 3. Storage and search

**SQLite instead of a vector database.** One file holds chunk text, metadata, vectors (as float32 blobs), an FTS5 full-text index and, when the extension loads, a `sqlite-vec` index. sqlite-vec searches the vectors inside SQLite, in C, without loading them into Python, so memory stays flat as the index grows. Without the extension (some Python builds can't load extensions) the store loads the matrix into NumPy and does one matrix-vector product: a few milliseconds for tens of thousands of chunks. Both are exact searches; they return the same ranking (a test checks this). The float32 blobs stay the source of truth, so an index written without the extension is rebuilt into `chunks_vec` the next time it is opened with it. sqlite-vec's ANN indexes, or HNSW, would be the next step past a few hundred thousand chunks.

**Postgres for the public app.** Render's free plan wipes its disk on every restart, so the public app can put its registry and every index in one Postgres database instead (`pg_store.py`, same `BaseStore` interface, e.g. Neon's free tier). Vectors are pgvector `halfvec` (half the storage of `vector`, which matters in a 0.5 GB free database) and keyword search is a `tsvector` with English stemming and a GIN index. Search is exact within one repository (a B-tree on `index_id` narrows it to a few thousand rows): a global HNSW index would have to filter by repository after the approximate search, losing recall for no speed gain at this size. Each index is a *generation* row: a refresh builds a new one and the registry switches to it in one update; the old one is retired for a minute, so requests still reading it finish, then dropped. The queue of indexing jobs is the registry itself, so a restart picks up where it stopped. All the store and workspace tests run against both backends.

**Hybrid search.** Embeddings find code that means the same thing with different words ("authenticate" vs `verify_token`); keyword search finds exact identifiers and rare terms that embeddings smear out. Both run, each returns its top 50, and the lists are merged.

**Reciprocal Rank Fusion.** BM25 scores and cosine similarities are on different, query-dependent scales, so adding them needs fragile tuning. RRF ignores raw scores and uses ranks: `score = Σ 1 / (60 + rank)`. A chunk ranked well by both systems wins. It is simple, robust and a standard technique in search.

**Keyword search details.** The FTS5 table uses the Porter stemmer (`retried` matches `retries`), and each chunk's indexed text also includes its identifiers split into words (`getUserById` → `get user by id`), so natural-language questions match code identifiers.

**Optional reranking.** Hybrid search scores the query and each chunk separately. A cross-encoder reranker (Jina's API) reads them together, which judges relevance better, so it re-orders the top 30 candidates. It is off by default, costs a network round trip per search, and if the API fails the fused order is used: reranking may improve search but must never break it.

**Source-first ranking.** Tests and documentation repeat the vocabulary of the code they describe, and often won the ranking for "how does X work" questions. Their fused score is multiplied by 0.5 (tests) or 0.6 (docs) unless the question mentions tests or docs. Measured on the bundled eval, recall@5 went from 0.55 to 0.80.

## 4. Question answering

The top snippets are numbered and placed in the prompt within a character budget. The system prompt tells the model to use only those snippets, cite them as `[n]`, and say when the answer is not there. Citations in the reply are parsed and checked against the real snippet count, so the UI can mark which sources were actually used and ignore invented numbers. If retrieval returns nothing, the model is not called at all, which avoids a confident answer built on no evidence.

## 5. Code review

**Diff parsing keeps new-file line numbers.** GitHub only accepts an inline comment on a line that appears in the diff, identified by its line number in the new file. The parser tracks old and new line numbers through every hunk, handles new, deleted, renamed and binary files, "no newline at end of file" markers, and code lines that start with `--` or `++`.

**Two reviewers, different strengths.**

| | Rules | LLM |
|---|---|---|
| Finds | secrets, injection sinks, disabled TLS, debugger statements, unsafe deserialisation | logic errors, missing edge cases, error handling, design problems |
| Speed / cost | microseconds, free | seconds, tokens |
| Behaviour | deterministic, never hallucinates | probabilistic |

Rules run on every added line (with language filters, and some skipped in test files so fake passwords in fixtures are not flagged). The LLM reviews each file separately, which keeps prompts small and lets files be reviewed in parallel.

**Cross-file context.** A diff alone hides the signatures and behaviour of the functions it calls, and the code that calls the functions it changes. For each file, the reviewer extracts called names from the added lines and looks up their definitions; finds the functions the diff modifies (definitions on added lines, plus the indexed chunks the added lines fall in) and looks up their callers in the symbol graph; then tops up with a hybrid search. Up to three snippets are added, marked "context only, do not review", with callers labelled "calls X" so the model checks whether the change breaks them.

**Established scanners next to the rules.** Bandit reads the whole new version of each changed Python file (fetched from the working tree, or from GitHub at the PR's head commit), so it understands code rather than single lines; gitleaks matches about 200 provider-specific secret formats in the added lines. Their findings are filtered to added lines, labelled `rule:bandit-B602` or `rule:gitleaks-...`, and merged with the built-in rules (which win ties against the LLM). Semgrep is supported but off by default in the app: starting it takes seconds per file on a small server, so it runs over the whole codebase in CI instead. A scanner that is missing or crashes is skipped; it never fails a review.

**Guarding against bad model output.**
1. The prompt prints line numbers next to each diff line and asks for JSON in a fixed shape (JSON mode is enabled when the provider supports it, with an automatic fallback when it does not).
2. The reply is parsed tolerantly (code fences and surrounding prose are stripped).
3. Each finding is validated individually with Pydantic; severity and category synonyms (`high`, `correctness`...) are normalised; invalid items are dropped without losing the valid ones.
4. A finding on a line not in the diff is moved to the nearest added line if it is within three lines (models are often off by one), otherwise discarded as hallucinated.
5. Findings are deduplicated per `(file, line, category)`, rules winning ties, then capped per file by severity.

Every drop is counted (`dropped_invalid`, `dropped_off_diff`, `snapped_lines`, `duplicates_removed`) so model quality problems show up in numbers rather than silently.

**Failure isolation.** If the model call fails for a file, that file still gets its rule findings. A single unreadable file never aborts indexing, but a provider outage stops it immediately instead of failing every file one by one.

## 6. GitHub integration

**Posting.** One review per run, with up to 30 inline comments on the new-file side and the rest listed in the summary. If GitHub rejects the review because a position is invalid (HTTP 422), it is re-posted as a summary-only review, so feedback is never lost.

**Webhooks.** The raw body is verified with HMAC-SHA256 against `X-Hub-Signature-256` using a constant-time comparison. Only `pull_request` events with actions `opened`, `synchronize`, `reopened` and `ready_for_review` on non-draft PRs trigger a review. The handler returns `202 Accepted` immediately and reviews in a background task, because GitHub times out slow webhook deliveries. GitHub can deliver the same event more than once, so the server remembers `repo#pr@head_sha` keys (bounded to the last 1,000) and ignores repeats: a commit is reviewed exactly once.

## 7. Reliability details

- HTTP clients retry 429 and 5xx responses with exponential backoff and honour `Retry-After`.
- Free LLM tiers change their limits often, so `REPOSCOUT_LLM_FALLBACKS` chains providers (Groq → Gemini → OpenRouter...). With fallbacks configured, each model gives up after two quick attempts instead of waiting out a rate limit, and the next one answers. It is a small wrapper rather than LiteLLM, whose size matters on a 512 MB server; all these providers speak the OpenAI API anyway.
- Rate limits use the `limits` library (slowapi's engine) with a moving window; counters are in memory for one server and can move to Redis with one setting.
- Optional monitoring: Sentry for errors (visitors' API keys and request bodies are stripped before sending) and Langfuse for LLM traces (prompt, answer, tokens, latency; built on OpenTelemetry). Visitors' prompts in the public app are only traced when the operator opts in.
- Clear, actionable errors: "Ollama is not running", "run `ollama pull X`", "the index was built with a different model".
- Per-route request counts, error counts and p50/p95 latency at `/stats`, plus LLM token usage.
- The CLI's `--fail-on major` exit code lets the review act as a CI gate.

## 8. Evaluation

Changing a prompt, a chunk size or a ranking weight can quietly make things worse, so the project ships labelled datasets and metrics:

- **Retrieval**: questions labelled with the file or function that answers them; reports hit@1, recall@k, MRR (mean reciprocal rank) and latency.
- **Review**: diffs with seeded issues at known lines plus clean diffs; a finding counts if it is in the right file within two lines. Reports precision (how many findings were real), recall (how many real issues were found), F1, and the false-alarm rate on clean code.

The eval datasets are excluded from the index (`.reposcoutignore`), because they contain the test questions word for word and would leak the answers.

## 9. Likely interview questions

**Why not just send the whole repository to the LLM?** Most repositories do not fit in a context window, cost grows with every token, and models answer worse when the relevant code is buried in irrelevant code. Retrieval sends only the few chunks that matter.

**How do you stop the model from making things up?** Answers are constrained to numbered snippets with citations that are checked; the model is not called when retrieval finds nothing; review output must pass a schema and point at real diff lines; and evals measure false alarms on clean code.

**What would you change to scale to a monorepo with millions of lines?** Switch the exact vector search to an ANN index (sqlite-vec's or pgvector's HNSW), move embedding into a batched background queue with workers, shard the index per directory or service, and make the symbol graph type-aware (SCIP indexes from each language's compiler).

**Why RRF instead of a weighted sum of scores?** The two score types are not comparable and their ranges change from query to query; rank-based fusion needs no calibration and is hard to break.

**What was the hardest bug?** Line numbers. A comment on a line GitHub does not consider part of the diff fails the whole review with a 422, which is why the parser tracks new-file numbers precisely, the reviewer validates and snaps lines, and posting falls back to a summary review.
