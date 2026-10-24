# Tool-first UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace RepoScout's UI with the tool-first design system in the spec, rebuilding every existing feature on it with unchanged behaviour and no inline scripts.

**Architecture:** Two HTML shells (`app.html` public, `index.html` local) load one shared `ui.css` and plain ES modules from `/static/{version}/…`, served by a new allow-listed asset route in `static_files.py`. Pure logic (links, routes, Markdown, tool filtering) lives in DOM-free modules covered by `node --test`; DOM code is verified in headless Chrome. The JSON API does not change.

**Tech Stack:** Python 3.12, FastAPI, pytest with `TestClient`; vanilla ES modules; Node 24 built-in test runner (`node --test`, no packages); headless Google Chrome for screenshots.

**Spec:** `docs/superpowers/specs/2026-10-24-ui-redesign-design.md`

## Global Constraints

- No external JS, no npm packages, no new Python dependencies, no build step. System fonts only; remove every `fonts.googleapis.com` / `fonts.gstatic.com` reference.
- No inline `<script>` with content, no `<style>` blocks, no `on*=` attributes in HTML or in JS-generated markup. Events via `addEventListener` only.
- Tokens only (spec §5): four type sizes `--t-sm 14px`, `--t-base 16px`, `--t-lg 20px`, `--t-xl clamp(30px, 4.2vw, 44px)`; one accent `--blue` (`#0D59F2` light / `#6F9BFF` dark); primary buttons use `--ink`. Exact colour tables are in spec §5.2.
- Motion: only `transform`/`opacity`; durations and curves from spec §8; `prefers-reduced-motion` turns movement into fades; no animation on route change, tab switch, filters or theme switch.
- Touch targets ≥ 44 px on `pointer: coarse`; no horizontal page scroll at 360 px; WCAG AA contrast in both themes.
- Behaviour, API calls, storage keys (`rs.key`, `rs.model`, `rs.chat.{key}`, `rs.review.{…}`, plus new `rs.theme`) and copy listed in the spec are kept exactly.
- Tests: `uv run --python 3.12 --with-editable . --with pytest python -m pytest -q` and `node --test tests/js/`; lint `ruff check src tests && ruff format --check src tests`.
- Commits: small, one logical change, plain messages, **no `Co-Authored-By` or other AI-attribution lines**.
- UI tasks must be built with the `emil-design-eng` and `animate` skills (and `animation-performance` for anything that moves); Task 10 runs the review skills.

## Review Focus

1. **Very long `owner/name` and file paths** (100-char repo names, deep paths): expect truncation with ellipsis in rows, chips and the repo bar, wrapping in titles, never page-wide horizontal scroll. Pinned by the break-ui pass in Task 10 and the 360 px screenshots.
2. **Hand-typed or stale hashes** (`#/ask/onlyowner`, `#/review/o/n/abc`, `#/r/o/n/review`, `#/unknown`): expect a redirect to the nearest valid screen, never a blank page or a JS error. Pinned by `routes.test.mjs` in Task 3.
3. **Pasted links with trailing junk** (`https://github.com/o/r/pull/12/files`, `github.com/o/r.git/`, ` o/r `, `o/r#12`): expect the same parse as today's `parseLink`. Pinned by `links.test.mjs` in Task 3.
4. **Markdown from the model containing HTML or `javascript:` links**: expect it escaped or dropped, exactly as today's renderer. Pinned by `markdown.test.mjs` in Task 3.
5. **An open page during a deploy** (old version prefix in the URL): expect the current file with `no-cache`, never a 404 that breaks module imports. Pinned by `test_old_version_still_served_without_cache` in Task 1.

---

## File map

| File | Responsibility |
|---|---|
| `src/reposcout/static_files.py` | Version hash, allow-list, page templating, asset and alias routes |
| `src/reposcout/web/app.py`, `src/reposcout/server.py` | Call `static_files.add_routes(app)` instead of their own `/ui.css` routes |
| `pyproject.toml` | Ship `static/js/*.js` |
| `deploy/Caddyfile` | New CSP |
| `src/reposcout/static/ui.css` | Whole design system (rewritten) |
| `src/reposcout/static/app.html`, `index.html` | Shells (rewritten) |
| `src/reposcout/static/js/links.js` | `parseLink`, hash builders (pure) |
| `src/reposcout/static/js/routes.js` | `parseRoute`, `legacyRedirect` (pure) |
| `src/reposcout/static/js/markdown.js` | `escapeHtml`, `inline`, `markdown` (pure, moved from today's app.html) |
| `src/reposcout/static/js/tools.js` | Tool registry, `filterTools`, `pickerRepos` (pure) |
| `src/reposcout/static/js/core.js` | `h`, `icon`, `ICONS`, `store`, `api`, `ApiError`, `toast`, `skel`, `stateBlock`, `codeBlock`, `copyButton`, `toolIcon`, `fmt` |
| `src/reposcout/static/js/settings.js` | Key/model storage, key dialog, `requireKey` |
| `src/reposcout/static/js/nav.js` | Top bar, All-tools menu, theme |
| `src/reposcout/static/js/home.js` | Home view |
| `src/reposcout/static/js/repo.js` | Tool page frame, picker, repo bar, indexing card |
| `src/reposcout/static/js/ask.js`, `find.js`, `review.js` | One module per tool |
| `src/reposcout/static/js/app.js` | Router and start-up |
| `src/reposcout/static/js/local.js` | Local page |
| `tests/test_static_files.py` | Asset route and page hygiene |
| `tests/js/*.test.mjs` | Pure-module tests |
| `.github/workflows/ci.yml` | Add `node --test tests/js/` |

---

### Task 1: Hashed static asset route

**Files:**
- Modify: `src/reposcout/static_files.py`, `src/reposcout/web/app.py:303-309`, `src/reposcout/server.py:124-130`, `pyproject.toml:36`
- Create: `tests/test_static_files.py`, `src/reposcout/static/js/.keep` (deleted in Task 3)
- Modify tests: `tests/test_web.py:360-363`, `tests/test_github_server.py:159-162`

**Interfaces:**
- Produces: `static_files.version() -> str` (12 hex chars, sha256 over the sorted allow-listed files' names and bytes, cached); `static_files.ALLOWED: frozenset[str]` computed from the package: `"ui.css"` plus every `js/*.js`; `static_files.page(name: str) -> str` replacing `{{static}}` with `/static/{version()}`; `static_files.add_routes(app: FastAPI) -> None` registering `GET /static/{ver}/{path:path}` and the `GET /ui.css` alias. HTML shells reference assets only as `{{static}}/ui.css` and `{{static}}/js/app.js` (or `local.js`).

- [ ] **Step 1: Write the failing tests** in `tests/test_static_files.py`, using a bare `FastAPI()` with `add_routes`:
  - `test_asset_served_with_immutable_cache`: `GET /static/{version()}/ui.css` → 200, `text/css`, `cache-control == "public, max-age=31536000, immutable"`.
  - `test_js_served_as_javascript`: for every `p` in `ALLOWED` ending in `.js` (skip the test while there are none; Task 3 adds them), the response content type starts with `text/javascript`.
  - `test_old_version_still_served_without_cache`: `GET /static/000000000000/ui.css` → 200 with `cache-control == "no-cache"`.
  - `test_unknown_and_traversal_paths_404`: each of `../pyproject.toml`, `js/../../web/app.py`, `app.html`, `nope.css`, `%2e%2e/x`, `/etc/passwd` → 404.
  - `test_page_inserts_static_prefix`: `page("app.html")` contains `f"/static/{version()}/"` and no `{{`.
  - `test_ui_css_alias`: `GET /ui.css` → 200 `text/css`.
- [ ] **Step 2:** Run `uv run --python 3.12 --with-editable . --with pytest python -m pytest tests/test_static_files.py -q`. Expected: FAIL (`add_routes`/`version` missing).
- [ ] **Step 3:** Implement in `static_files.py`. Lookup is by exact membership in `ALLOWED` (no path joining of user input). Old `{{asset_version}}` placeholder and `stylesheet()` are removed; until Task 3 rewrites the shells, make `page()` also replace `/ui.css?v={{asset_version}}` → `{{static}}/ui.css` so the existing pages keep working. Replace the `/ui.css` routes in `web/app.py` and `server.py` with `static_files.add_routes(app)`. Add `"static/js/*.js"` to `pyproject.toml` package data.
- [ ] **Step 4:** Update the two existing assertions to `assert f"/static/{static_files.version()}/ui.css" in page and "{{" not in page` and fetch the CSS from that URL.
- [ ] **Step 5:** Run the full suite on 3.12 and ruff. Expected: all pass (149 + new).
- [ ] **Step 6:** Commit `Serve UI assets from a hashed, allow-listed static path`.

### Task 2: Design system stylesheet and CSP

**Files:**
- Rewrite: `src/reposcout/static/ui.css`
- Modify: `deploy/Caddyfile` (CSP line), `tests/test_static_files.py`

**Interfaces:**
- Produces: the tokens of spec §5 and these classes, used by every later task: `.wrap`, `.btn` (`.primary`, `.quiet`, `.icon`), `.input`, `.ticon` (`.sm`, `.lg`, inline `--main`/`--tint`), `.tile`, `.tiles`, `.card`, `.cards`, `.card.soon`, `.panel`, `.pill` (`.ok`, `.busy`, `.bad`), `.tag`, `.chip`, `.sev` + `.s-{critical|major|minor|nit}`, `.repobar`, `.pick`, `.progress`, `.bar`, `.steps`, `.state`, `.skel`, `.toast` + `#toasts`, `dialog`, `.nav`, `.menu`, `.crumbs`, `.toolhead`, `.toolgrid`, `.aside`, `.thread`, `.ex`, `.q`, `.a`, `.cite`, `.srcs`, `.src`, `.composer`, `.starters`, `.hits`, `.hit`, `.ln`, `.verdict`, `.fgroup`, `.finding`, `.banner` (+ `.error`), `.hero`, `.split`, `.rows`, `.row`, `.enter`, `.sr-only`. Dark theme via `:root[data-theme="dark"]`, with the same values under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`.

- [ ] **Step 1: Write failing tests** in `tests/test_static_files.py`:
  - `test_stylesheet_defines_the_design_tokens`: CSS contains each of `--t-sm: 14px`, `--t-base: 16px`, `--t-lg: 20px`, `--t-xl: clamp(30px, 4.2vw, 44px)`, `--blue: #0D59F2`, `--ink:`, `--panel:`, `[data-theme="dark"]`, `prefers-reduced-motion`, `pointer: coarse`.
  - `test_no_third_party_fonts`: neither CSS nor any allow-listed file contains `fonts.googleapis` or `fonts.gstatic`.
  - `test_caddy_csp_has_no_inline_scripts`: the CSP line in `deploy/Caddyfile` contains `script-src 'self';` and not `fonts.googleapis`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write `ui.css` from spec §5, §8 and §9 (the approved prototype's CSS is the starting point). Set the CSP to the exact string in spec §7.3.
- [ ] **Step 4:** Run the suite and ruff. Expected: PASS. The old pages will look unstyled until Tasks 3 and 9; that is expected on this branch.
- [ ] **Step 5:** Commit `Add the new design system and tighten the CSP`.

### Task 3: Pure modules, shell, navigation, key dialog and router

**Files:**
- Create: `js/links.js`, `js/routes.js`, `js/markdown.js`, `js/tools.js`, `js/core.js`, `js/settings.js`, `js/nav.js`, `js/app.js` (all under `src/reposcout/static/`); `tests/js/links.test.mjs`, `tests/js/routes.test.mjs`, `tests/js/markdown.test.mjs`, `tests/js/tools.test.mjs`
- Rewrite: `src/reposcout/static/app.html`
- Modify: `tests/test_static_files.py`, `.github/workflows/ci.yml`
- Delete: `src/reposcout/static/js/.keep`

**Interfaces:**
- `links.js`: `parseLink(text: string) -> {owner, name, pull: number|null} | null` (today's regexes verbatim); `toolHash(tool: string, owner?: string, name?: string, pull?: number|string) -> string` giving `#/ask`, `#/ask/o/n`, `#/review/o/n/12` with each part `encodeURIComponent`-ed.
- `routes.js`: `parseRoute(hash: string) -> {view: "home"} | {view: "tool", tool: "ask"|"find"|"review", owner?: string, name?: string, pull?: number}`; `legacyRedirect(hash: string) -> string | null` (spec §6.1 table). Invalid shapes (one path part after a tool, non-numeric pull, unknown tool) degrade to the nearest valid route: `#/ask/x` → tool page without repo; `#/review/o/n/abc` → `#/review/o/n`.
- `markdown.js`: `escapeHtml(s)`, `inline(escaped)`, `markdown(src) -> string` moved unchanged from today's `app.html` lines 443-489.
- `tools.js`: `TOOLS` (array of `{id, name, category, description, color, glyph, live}`, the nine tools and copy from spec §5.2 and prototype), `CATEGORIES = ["Understand", "Review", "Maintain"]`, `filterTools(query: string) -> Array<[category, tools[]]>` (case-insensitive over name + description, empty categories dropped), `EXAMPLES` (the four example repos), `pickerRepos(repos: Array<{full_name, ready}>, n = 3) -> string[]` (ready ones in given order, then examples not already present, max n).
- `core.js`: `h(tag, attrs, ...children)`, `icon(name, size)`, `toolIcon(tool, size: "sm"|"md"|"lg")`, `store.{get,set,del}`, `api(path, {method, body, key, withKey})`, `class ApiError {status, code, message, retryAfter}`, `toast(message, {action?: {label, run}, kind?: "info"|"success"|"error"})` (timings, pausing, max 3 visible: spec §5.4), `skel(cls)`, `stateBlock({icon, title, text, actions, tone})`, `codeBlock(text, firstLine, maxLines, highlight?: number[])`, `copyButton(getText)`, `fmt(n)`, `friendlyError(err) -> string` (spec §10 copy for 429/503/network).
- `settings.js`: `settings.{key, model, remember, save(key, model, remember)}`, `openKeyDialog(message?: string, then?: () => void)`, `requireKey(then) -> boolean`, `onKeyChange(fn)`.
- `nav.js`: `mountNav()`, `setCurrentTool(id | null)`, `applyTheme()`; theme stored in `rs.theme` (`"light"|"dark"`, absent = system).
- `app.js`: registers views `{home, ask, find, review}` lazily via dynamic `import("./home.js")` etc., each exporting `render(main: HTMLElement, route) -> void | (() => void)` (the return is a cleanup). Calls `legacyRedirect` first (via `location.replace`), focuses `<main>` and scrolls to top on navigation, sets `document.title`.

- [ ] **Step 1: Write failing Node tests:**
  - `links.test.mjs`: the cases from `tests/test_web.py::test_parse_github_link` that the JS mirrors (e.g. `"https://github.com/psf/requests"` → `{owner:"psf",name:"requests",pull:null}`, `"github.com/o/r/pull/12/files"` → pull 12, `"o/r#7"` → pull 7, `"o/r.git"` → name `r`, `"not a link"` → `null`); `toolHash("review","o w","n",3) === "#/review/o%20w/n/3"`.
  - `routes.test.mjs`: `parseRoute("")` and `("#/zzz")` → home; `("#/find/psf/requests")` → tool find with owner/name; `("#/review/o/n/12")` → pull 12; `("#/review/o/n/abc")` → no pull; `("#/ask/onlyowner")` → no owner; `legacyRedirect` for `#/r/o/n` → `#/ask/o/n`, `#/r/o/n/search` → `#/find/o/n`, `#/r/o/n/review` → `#/review/o/n`, `#/pr/o/n/5` → `#/review/o/n/5`, `#/ask/o/n` → `null`.
  - `markdown.test.mjs`: `markdown("<img src=x onerror=alert(1)>")` contains `&lt;img` and no `<img`; `markdown("[x](javascript:alert(1))")` contains no `href="javascript`; a fenced block becomes `<pre><code>` with escaped content; a pipe table becomes `<table>`.
  - `tools.test.mjs`: `TOOLS.length === 9`, three `live`; `filterTools("")` returns three categories; `filterTools("REVIEW")` includes Review a PR and Post a review; `filterTools("zzz")` is `[]`; `pickerRepos([{full_name:"a/b",ready:true},{full_name:"c/d",ready:false}])` → `["a/b","pallets/flask","psf/requests"]`.
- [ ] **Step 2:** Run `node --test tests/js/`. Expected: FAIL (modules missing).
- [ ] **Step 3:** Implement the pure modules; then `core.js`, `settings.js`, `nav.js`, `app.js` and the new `app.html` shell (nav markup, `<main id="view" tabindex="-1">`, footer, key dialog markup from spec §6.8, `<div id="toasts" aria-live="polite">`, one `<script type="module" src="{{static}}/js/app.js">`). Views not built yet render a placeholder `stateBlock` titled with the tool's name; Task 4 replaces home's.
- [ ] **Step 4: Add page-hygiene tests** to `tests/test_static_files.py`: `test_public_page_has_no_inline_code` asserting `page("app.html")` has no `<style`, no `<script>` with content (regex `<script(?![^>]*\bsrc=)[^>]*>\s*\S`), no ` on[a-z]+=` attribute, and references `/js/app.js`. Run pytest and `node --test tests/js/`. Expected: PASS.
- [ ] **Step 5:** Manual check: `uv run --python 3.12 --with-editable . reposcout serve-public --port 8000`, open it, and confirm: nav renders; All tools opens and closes (Esc, outside click); theme toggles and persists across reload; key dialog checks a key, saves and removes; `#/r/psf/requests` lands on `#/ask/psf/requests`; the DevTools console is clean. CSP compliance is covered by the hygiene tests (no inline code anywhere).
- [ ] **Step 6:** Add `- run: node --test tests/js/` after `pytest` in `.github/workflows/ci.yml`. Commit `Add the app shell, navigation, key dialog and router` (pure modules and tests may be a separate earlier commit `Add tested link, route, Markdown and tool modules`).

### Task 4: Home page

**Files:** Create `src/reposcout/static/js/home.js`.

**Interfaces:** Consumes `TOOLS`, `CATEGORIES`, `filterTools`, `toolIcon`, `api`, `stateBlock`, `skel`, `toolHash`. Produces `render(main)`.

- [ ] **Step 1:** Build the home from spec §6.3 exactly (hero copy, three tiles in a `.panel`, "Search 9 tools" filter wired to `filterTools`, category cards with disabled "Soon" cards, "Private by design" checks, "Recently explored" from `GET /api/repos` with skeleton / empty / error states, rows linking to `toolHash("ask", owner, name)`). The tool-search input re-renders only the category list, keeping focus.
- [ ] **Step 2:** Verify with headless Chrome against the running server: `google-chrome --headless=new --window-size=1280,1900 --screenshot=… http://127.0.0.1:8000/` and at 390 px, light and dark (add `--force-dark-mode` for dark; the page follows the system theme when `rs.theme` is unset). Typing "rev" in the filter (manual) leaves two cards.
- [ ] **Step 3:** Commit `Build the tools home page`.

### Task 5: Tool page frame, repository picker, repo bar and indexing

**Files:** Create `src/reposcout/static/js/repo.js`.

**Interfaces:**
- Consumes `TOOLS`, `pickerRepos`, `parseLink`, `toolHash`, `api`, `toast`, `toolIcon`, `friendlyError`.
- Produces `toolFrame(main, toolId) -> {body: HTMLElement, aside: HTMLElement}` (breadcrumb, header, two-column grid, aside with other live tools; aside links include the repo once set via `setAsideRepo(owner, name)`); `renderPicker(body, toolId)`; `repoBar(record | {full_name, owner, name}, {indexed: boolean}) -> HTMLElement`; `ensureIndexed(body, owner, name, onLeave) -> Promise<record | null>` that GETs the record, POSTs `/api/repos` on 404, shows the indexing card and polls every 1500 ms until ready (resolves the record) or failed (renders the failure state and resolves `null`); cancels polling through `onLeave`.

- [ ] **Step 1:** Implement per spec §6.4: picker copy per tool; chips from `pickerRepos(await api("/api/repos").repos)`, falling back to `EXAMPLES` if the request fails; inline validation message; PR links from any tool go to `toolHash("review", o, n, pull)`. Repo bar actions per spec §5.4 (Refresh index with its toast, Open on GitHub, Change → `toolHash(toolId)`), plus the "Last refresh failed" and "Refreshing in the background" banners. Indexing card with determinate bar via `transform: scaleX(files_done / files_total)` and the four steps, including "number n in line".
- [ ] **Step 2:** Verify manually with the server: `#/ask` shows the picker and chips; entering `pallets/flask` indexes (use a small repo, e.g. `pallets/markupsafe`) and the card advances to ready; a bad link shows the inline message; failure path by entering a non-existent repo shows "Couldn't index…" with both actions.
- [ ] **Step 3:** Commit `Add the repository picker, repo bar and indexing card`.

### Task 6: Ask

**Files:** Create `src/reposcout/static/js/ask.js`.

**Interfaces:** Consumes `toolFrame`, `renderPicker`, `ensureIndexed`, `repoBar`, `markdown`, `requireKey`, `openKeyDialog`, `api`, `store`, `copyButton`, `friendlyError`. Produces `render(main, route)`.

- [ ] **Step 1:** Port today's `tabAsk` and `renderAnswer` (app.html 673-740, 490-507) onto spec §6.5's look: ink question bubble, bordered answer card, blue citation badges linking to GitHub with `path:start-end` titles, source chips (uncited dimmed), footer (model · seconds · Copy), starters, sticky composer and hint, `rs.chat.{key}` persistence (last 20), 3-turn history, 8 s "Still working" note, inline error with Try again, key errors re-opening the dialog and restoring the draft, the answer entering with `.enter` once.
- [ ] **Step 2:** Verify with the server and a real Groq key on a small indexed repo: ask a starter, ask a follow-up, reload (chat persists), Clear chat, remove the key and send (dialog opens, then the question sends after saving).
- [ ] **Step 3:** Commit `Rebuild Ask on the new design`.

### Task 7: Find code

**Files:** Create `src/reposcout/static/js/find.js`.

**Interfaces:** Consumes `toolFrame`, `renderPicker`, `ensureIndexed`, `api`, `codeBlock`, `stateBlock`, `skel`, `friendlyError`. Produces `render(main, route)`.

- [ ] **Step 1:** Port today's `tabSearch` (app.html 743-765) to spec §6.6: input + black Search button, the note, two skeleton cards, result cards with `path:start-end` link and symbol pill, `codeBlock(text, start_line, 14, matchedLines)` where matched lines are those containing any query word of 3+ letters (case-insensitive), empty and error states.
- [ ] **Step 2:** Verify: search a known function name (results), a nonsense word (empty state), with no key set (works).
- [ ] **Step 3:** Commit `Rebuild Find code on the new design`.

### Task 8: Review a PR

**Files:** Create `src/reposcout/static/js/review.js`.

**Interfaces:** Consumes `toolFrame`, `renderPicker`, `repoBar`, `api`, `store`, `requireKey`, `openKeyDialog`, `toast`, `toolHash`, `inline`, `escapeHtml`, `friendlyError`. Produces `render(main, route)` and `requestReview(owner, name, pull)` (sets the in-memory approval, then navigates; today's `openReview`).

- [ ] **Step 1:** Port today's `viewReview`/`paintReview` (app.html 783-898) to spec §6.7: PR-number form on `#/review/o/n`; on `#/review/o/n/num` the no-key card, "Start the review?" unless approved in this tab, progress card with today's elapsed-time copy, cache in `sessionStorage` per model with "Run again", PR title/meta, grey verdict bar with Copy as Markdown and Run again, severity chips (nits off unless the only findings), file groups and finding cards, the three notes. Repo bar shows indexed or "Not indexed. Reviews read only the changed files." with an "Index it" action that calls `POST /api/repos` and toasts.
- [ ] **Step 2:** Verify on a small public PR with a real key: start from `#/review`, paste a PR link, confirm the start step on a directly opened link, filters, Copy as Markdown, reload shows the cached result.
- [ ] **Step 3:** Commit `Rebuild Review a PR on the new design`.

### Task 9: Local page

**Files:** Rewrite `src/reposcout/static/index.html`; create `src/reposcout/static/js/local.js`; modify `tests/test_static_files.py`.

**Interfaces:** Consumes `core.js` (`h`, `api`-like POST helper or its own `post`, `codeBlock`, `stateBlock`, `skel`), `markdown`. Produces nothing.

- [ ] **Step 1: Failing test** `test_local_page_has_no_inline_code`: same assertions as the public page for `page("index.html")`, referencing `/js/local.js`. Run; expect FAIL.
- [ ] **Step 2:** Build per spec §6.9: nav (logo + tabs Ask / Review a diff / Find code as a proper `tablist` with arrow keys), a stats panel from `/stats` (files, sections, model or "no LLM"), and the three tabs with today's behaviour and endpoints, rendering answers, findings and hits with the shared classes. Ctrl/⌘+Enter submits.
- [ ] **Step 3:** Run pytest (PASS); manual check with `reposcout serve` on this repo after `reposcout index`.
- [ ] **Step 4:** Commit `Move the local page onto the new design system`.

### Task 10: Design passes, stress test and verification

**Files:** Any of the above; `docs/DESIGN.md` (UI section, if it describes the old UI).

- [ ] **Step 1:** Run the skills in order on the finished UI and record findings in the PR description: `find-animation-opportunities`, `improve-animations` (its audit and plan template), `review-animations` (read `.agents/skills/review-animations/SKILL.md` and `STANDARDS.md` and apply them; it is manual-invoke only). Apply the accepted fixes.
- [ ] **Step 2:** `break-ui`: worst-case data (a 100-character repo name, 0 and 500 search results by stubbing in DevTools, empty `/api/repos`, slow and failed requests via DevTools throttling/blocking, 429 and 503 responses, 360 px and 2560 px wide, keyboard only, 200% zoom). Fix everything that breaks.
- [ ] **Step 3:** Screenshots with headless Chrome at 390 and 1280 px, light and dark (`--force-dark-mode`), plus one with `--force-prefers-reduced-motion`, of home, each tool page (picker and result), the indexing card and the key dialog; keep them out of the repo.
- [ ] **Step 4:** Update `docs/DESIGN.md` and `README.md` only where they describe the old UI or Google Fonts.
- [ ] **Step 5:** Full verification on Python 3.12: `ruff check src tests && ruff format --check src tests`, `uv run --python 3.12 --with-editable . --with pytest python -m pytest -q`, `node --test tests/js/`. Then the manual end-to-end from spec §11.
- [ ] **Step 6:** Commit the fixes in logical commits.
