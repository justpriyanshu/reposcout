# Sub-project 0: Redesign the RepoScout UI as a GitHub toolkit

Status: draft for review · Date: 2026-10-24 · Sub-project 0 of the "GitHub toolkit" programme

## 1. Intent

RepoScout is growing from "ask about a public repo and review a PR" into a toolkit of tools for GitHub
repositories. The current UI (the `app.html` single-page app and the `ui.css` system from the last six
commits) reads as generic and AI-made, is not friendly to use, and its colours are weak. This
sub-project replaces the whole look and the information architecture with one built for a growing set
of tools, and rebuilds every existing feature on it with the same behaviour.

Later sub-projects (sign in with GitHub, posting reviews, issue helper, CI explainer, release notes,
repo health, onboarding guide) each add one tool to this structure. The redesign must make adding a
tool cheap: one tile, one tool page, one script.

### Where the design comes from

The owner picked two references after rejecting three earlier prototype rounds (an IDE-style
"Workbench", a keyboard-first "Command" and an editorial "Paper"; then three claymorphism riffs of
"Command"). Both rounds were rejected as "not looking good".

- **Look, from pdf-tools.com:** white page, light grey panels that group white cards, one sharp blue for
  links and small labels, black primary buttons with outlined secondary buttons, short heavy headings,
  and colour coming only from each product's icon.
- **UX, from pdf.imagestool.com:** the home page *is* the toolkit. Big tiles for the main tools come
  first, then a box that filters tools by name, then every tool grouped by category as compact cards
  with an icon, a name and one line of description. The top nav has an "All tools" menu plus direct
  links to the most-used tools. Each tool has its own page. On phones the tiles become a list.

The approved prototype that combines them is `tools.html` (a throwaway file in the session scratchpad,
not in the repo). This document describes everything in it, so the builder does not need the file.

## 2. Goals, non-goals, anti-goals

**Goals**
- A new design system (tokens and components) used by every screen, in light and dark.
- A tool-first information architecture: pick a tool, then give it a repository or pull request.
- Every existing feature rebuilt on it with the same behaviour: adding a repository and watching it
  index, Ask, Find code, Review a PR, and API key settings.
- The local `reposcout serve` page moved onto the same design system.
- JavaScript moved out of the HTML, so the Caddy CSP can drop `'unsafe-inline'` from `script-src`.
- Every state designed: loading, empty, error with a next step, rate-limited, busy, no key.

**Non-goals (this sub-project)**
- Sign-in, private repositories, "My repositories", or any signed-in page (sub-project 1).
- Any new tool. The six future tools appear only as disabled "Soon" cards.
- Changes to the JSON API, except where section 7 says so (it doesn't change any).
- A settings page. The key dialog stays a dialog.

**Anti-goals (a failure even if it works)**
- Anything that reads as an AI-generated landing page: a gradient hero, a three-feature row with
  icons as the main content, stacked pills and badges, or staggered fades on everything.
- More than one accent colour competing for attention, or colour used as decoration.
- A layout that needs horizontal scrolling at 360 px, or touch targets under 44 px on touch screens.
- Any external JavaScript, any new Python or JS dependency, or any font loaded from a third party.

## 3. Constraints

- No external JS and no build step. Plain ES modules served by the app; the CSP allows `'self'`.
- BYOK stays exactly as now: the Groq key lives in `localStorage` (remembered) or `sessionStorage`
  (not remembered) and is sent only in the `X-LLM-Key` header of the visitor's own requests.
- Reviews spend the visitor's quota, so a review only starts after the visitor's own click (today's
  `approvedReview` rule). Opening a review link directly shows a "Start review" step first.
- System fonts only (`system-ui` for text, `ui-monospace` for code). Google Fonts is removed from both
  pages and from the CSP.
- Tests run on Python 3.12 (`uv run --python 3.12 ...`); `ruff check` and `ruff format` stay clean.
- The app runs on a t3.small; nothing here adds server memory or CPU in any meaningful way.

## 4. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Split the UI into a small HTML shell, one shared stylesheet and a handful of ES modules (one per tool), served from a hashed static path | Needs a static-asset route and tests; in return the CSP drops inline scripts, and each new tool is one file and one tile |
| B | Keep one `app.html` with all CSS and JS inline | Least change, but the file is already ~900 lines, every tool makes it worse, and the CSP must keep `'unsafe-inline'` for scripts |
| C | Server-rendered pages per tool (Jinja) with real URLs | Less JS and nicer links, but adds a dependency and rewrites most of the frontend twice (now and again for each tool) |

The owner chose **A**.

## 5. Design system

All values below are tokens on `:root` in `ui.css`. Components use tokens only, never one-off values.

### 5.1 Type: four sizes

| Token | Size | Use |
|---|---|---|
| `--t-sm` | 14px | Metadata, labels, captions, code |
| `--t-base` | 16px | Body text, inputs, buttons |
| `--t-lg` | 20px | Section and card titles, dialog titles, the hero sub-line |
| `--t-xl` | `clamp(30px, 4.2vw, 44px)` | Page titles (the hero headline, each tool page's name) |

Fonts: `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif` and
`ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace`. Hierarchy comes from weight:
800 for page titles with `-0.03em` tracking and 1.08 line height, 700–750 for titles, 600 for labels
and buttons, 400 for body text at 1.55 line height. Two exceptions are allowed below the scale: 12px
uppercase eyebrow labels (letter-spacing `.06em`) and 11px citation numbers inside 18px badges.

### 5.2 Colour

Light (default when the system is light) and dark (when the system is dark, or chosen with the theme
button; the choice is remembered in `localStorage`).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#0C0D10` | Page |
| `--panel` | `#F4F5F7` | `#131519` | Grey panels that group cards; repo bar; verdict bar |
| `--card` | `#FFFFFF` | `#181A1F` | Cards, tiles, inputs, dialogs |
| `--hover` | `#F7F8FA` | `#1E2026` | Hover fill |
| `--code` | `#F6F7F9` | `#121418` | Code blocks |
| `--line` / `--line-strong` | `#E7E9ED` / `#D5D9E0` | `#262930` / `#353943` | Borders |
| `--text` / `--text-2` / `--text-3` | `#0D0F12` / `#4A5260` / `#6A7280` | `#F1F3F6` / `#B3BAC6` / `#8A92A0` | Text, secondary, tertiary |
| `--ink` / `--ink-fg` | `#0D0F12` / `#FFFFFF` | `#F1F3F6` / `#0C0D10` | Primary buttons, the user's chat bubble |
| `--blue` / `--blue-soft` | `#0D59F2` / `#EAF1FF` | `#6F9BFF` / `#18234A` | Links, focus ring, the hero tag, citations, the progress bar |
| Status | ok `#128048`, busy = blue, bad `#C4281C`, each with a `-soft` fill | lighter equivalents | Ready / Indexing / Failed pills |
| Severity | critical = bad, major `#B54A06`, minor `#8A6A00`, nit = text-2, each with `-soft` | lighter equivalents | Review findings and filters |

Blue is the only accent. Black-on-white (`--ink`) is the primary action colour.

**Tool colours.** Each tool has a main colour and a tint, used only in its icon:

| Tool | Main (light) | Category |
|---|---|---|
| Ask | `#6447E8` violet | Understand |
| Find code | `#0A9784` teal | Understand |
| Review a PR | `#EA5A1A` orange | Review |
| Onboarding guide (soon) | `#D99A00` amber | Understand |
| Post a review (soon) | `#2C6CF0` blue | Review |
| Issue helper (soon) | `#1F9D4C` green | Maintain |
| Explain a CI failure (soon) | `#D93545` red | Maintain |
| Release notes (soon) | `#C2409A` pink | Maintain |
| Repo health (soon) | `#0B8BC2` cyan | Maintain |

Every text/background pair must reach WCAG AA (4.5:1 for body text, 3:1 for large text and UI
parts) in both themes. The builder checks the pairs and adjusts a value if one fails.

### 5.3 Space, radius, elevation, motion

- Spacing: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64 px (`--s-1` … `--s-16`).
- Radius: 6 (small), 8 (buttons, inputs), 12 (cards), 16 (panels, dialogs, menus), full (pills).
- Elevation: borders do most of the work. `--shadow` (a 1–2 px hairline shadow) on tiles; `--shadow-pop`
  only on floating things: the tools menu, dialogs, toasts and the chat composer.
- Motion tokens: `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`, `--d-fast: 120ms`, `--d-base: 180ms`.

### 5.4 Components

Each is a CSS class set in `ui.css`, plus a JS helper in `core.js` where it has behaviour.

- **Button:** `.btn` (outlined), `.btn.primary` (ink), `.btn.quiet` (no border), `.btn.icon` (square).
  40 px tall, 44 px minimum on touch screens. Press: `scale(0.97)`.
- **Input group:** `.input`, an icon, the field and an optional button inside one bordered box, with a
  blue focus ring on `:focus-within`.
- **Tool icon:** `.ticon` (sm 32, md 44, lg 56): a tinted rounded square with a two-tone 24×24 SVG
  glyph (stroke in the tool colour, an inner shape filled at 22% opacity). The glyphs are inline SVG
  strings in the tool registry; no image files.
- **Tile** (home, main tools): a large icon, a title, one line and "Open →". On phones: icon left, no
  "Open".
- **Tool card** (home categories, compact): a small icon, a name, one line. Disabled "Soon" cards are
  shown at 62% opacity with a "Soon" pill, are not links, and are skipped by the keyboard.
- **Panel:** `.panel`, a grey rounded box grouping cards.
- **Pill:** status (Ready / Indexing / Failed) with a dot; severity label (uppercase, 12 px).
- **Chip:** recent repositories under a picker; severity filters (`aria-pressed`).
- **Repo bar:** initials, `owner/name` in mono, a meta line (branch, commit and file count, or
  "Indexing now", or "Indexing failed"), and three actions: an icon button "Refresh index"
  (`POST /api/repos` with `refresh: true`, then the toast "Refreshing the index in the background. You can
  keep using it meanwhile."), an icon link "Open on GitHub", and a "Change" button. On phones the two icon
  actions stay as 44 px icon buttons and the meta line truncates. A "Last refresh failed: …" error banner
  shows under the bar when the record carries an error, as today.
- **Dialog:** native `<dialog>` for the API key.
- **Menu:** the "All tools" dropdown (desktop) or full-width sheet (phones).
- **Toast:** bottom-centre stack, built in vanilla JS following Sonner's behaviour: 4 s for info, 6 s
  with an action, errors stay until dismissed when they carry a Retry; timers pause on hover and while
  the tab is hidden; the same enter/exit direction; max 3 visible.
- **Skeletons:** for every list and result area (repos, search hits, answers, review). Spinners only
  inside the indexing step list.
- **States:** an empty, error or info block with a title, one line and an optional action.

## 6. Information architecture and screens

### 6.1 Routes (hash-based, unchanged mechanism)

| Route | Screen |
|---|---|
| `#/` | Home: the toolkit |
| `#/ask` · `#/find` · `#/review` | Tool page, no repository picked yet |
| `#/ask/{owner}/{name}` · `#/find/{owner}/{name}` | Tool page for that repository |
| `#/review/{owner}/{name}` | Review page for that repository, with a PR-number box |
| `#/review/{owner}/{name}/{number}` | Review of that pull request ("Start review" step unless just requested) |

Old links keep working: `#/r/{o}/{n}` → `#/ask/{o}/{n}`, `#/r/{o}/{n}/search` → `#/find/{o}/{n}`,
`#/r/{o}/{n}/review` → `#/review/{o}/{n}`, `#/pr/{o}/{n}/{num}` → `#/review/{o}/{n}/{num}`, all via
`location.replace` so Back works. Unknown routes go home.

### 6.2 Navigation (every page)

Sticky top bar, 60 px, translucent background with blur, bottom border:
- Left: the logo (a blue-to-violet rounded mark with a magnifier, then "RepoScout"), the **All tools**
  button, and direct links **Ask**, **Find code**, **Review a PR** (the current one is marked
  `aria-current`).
- Right: the key status (a green dot and "Groq key", or an amber dot and "Add API key"), which opens
  the key dialog, and the theme button (moon or sun).
- **All tools** opens a menu with three columns (Understand, Review, Maintain) listing every tool with
  its small icon; "Soon" items are shown but disabled. It closes on Esc (focus returns to the
  button), on an outside click and on navigation. Focus moves to the first live item when it opens.
- Under 820 px: the direct links and the key label are hidden; a ☰ button opens the same menu as a
  full-width sheet.

### 6.3 Home (`#/`)

1. **Hero**, centred on desktop and left-aligned on phones: a blue tag "Free · bring your own key ·
   no sign-up", the headline "Tools for understanding and reviewing GitHub code" (`--t-xl`, max 24ch),
   and the line "Ask how a repository works, find code by meaning, and review pull requests. Pick a
   tool to start." (`--t-lg`, `--text-2`).
2. **Main tools panel:** a grey panel with three tiles (Ask, Find code, Review a PR), three columns
   and one column under 720 px.
3. **Tool search:** an input "Search 9 tools" that filters the category list as you type (name and
   description, case-insensitive). No results: "No tool matches “…”. Try “review” or “search”."
4. **Categories:** Understand, Review, Maintain, each a heading and a grid of tool cards (four columns,
   two under 960 px, one under 720 px). The three live tools appear here too, as on imagestool.
5. **Two columns** (one on phones): "Private by design" with three check-marked lines (the key stays
   in the browser; answers link to the exact lines; Find code needs no key), and "Recently explored",
   today's public list from `GET /api/repos` as rows (superseded on 2026-10-26: it is now "Your
   repositories", only the caller's own list; see docs/DEPLOY-RENDER.md "Privacy") (name in mono, description or progress, and a
   Ready / Indexing / Failed pill), linking to `#/ask/{owner}/{name}`. States: a skeleton of 3 rows
   while loading; "Nothing explored yet. Open Ask or Find code and add a repository, for example
   pallets/flask; it will show up here for everyone." when empty; an error block with "Try again" on failure.
6. **Footer:** "RepoScout reads public code. Your API key stays in your browser and is sent only with
   your own requests."

### 6.4 Tool page layout (Ask, Find code, Review a PR)

- Breadcrumb "All tools / {Tool}", then the tool header: the large icon, the name (`--t-xl`) and its
  one-line description.
- Two columns on desktop: the main column and a 280 px side column. The side column lists "Also for
  this repository" (or "Other tools" before a repository is picked) with the other live tools, linking
  to the same repository. Under 900 px the side column is hidden.
- **No repository yet** (`#/ask`, `#/find`, `#/review`): a picker card titled "Which repository?" (for
  Review: "Which pull request?"), one line of help, a link input with a black "Continue" button, and up
  to three repository chips under a "Try" label: ready repositories from the shared `GET /api/repos`
  list first (newest first), topped up from today's examples (`pallets/flask`, `psf/requests`,
  `tiangolo/fastapi`, `osm-search/Nominatim`) so a fresh server still offers three. No per-visitor
  history is stored. Accepted input is whatever `parseLink` accepts today
  (`owner/repo`, GitHub URLs, PR URLs). Pasting a PR link on any tool goes to
  `#/review/{o}/{n}/{num}`. Invalid input shows an inline message under the field ("Paste a link like
  github.com/owner/repo"), not a toast.
- **Repository picked:** the repo bar, then the tool body.
  - **Ask and Find code** need the index. If `GET /api/repos/{o}/{n}` returns 404, the page calls
    `POST /api/repos` exactly as today and shows the **indexing card**: "Getting {repo} ready", "This
    happens once per repository. You can leave this page open, or come back later.", a progress bar
    (determinate once `files_total` is known) and the four steps (Waiting for a free slot / Downloading
    the code / Reading and indexing files (x of y) / Ready), polling every 1.5 s, then the tool appears
    without a reload. A failure shows "Couldn't index {repo}", the reason, "Try again" (refresh) and
    "Pick another repository". A refresh in progress shows today's info banner above the tool.
  - **Review** does not need the index, as today. If the repository isn't indexed, the repo bar's meta
    line says "Not indexed. Reviews read only the changed files." with an "Index it" action, matching
    today's tip.

### 6.5 Ask

Behaviour exactly as today: up to 3 previous turns sent as history; the chat saved per repository
(`rs.chat.{key}`, last 20 turns); Enter sends and Shift+Enter adds a line; the textarea grows up to
180 px; a key prompt when there's no key; a "Still working" note after 8 s; errors inline with "Try
again"; the question scrolled to the top when the answer arrives.

Look: the user's question is a right-aligned ink bubble. The answer is a bordered card with Markdown
(today's safe renderer), citation badges `[n]` in blue linking to GitHub, then a "Sources" row of
chips (number, `path:start-end`, uncited ones dimmed), then a footer line (model · seconds · Copy).
The empty state is "Ask anything about {name}" with the four starter questions as buttons in two
columns. The composer sticks to the bottom with `--shadow-pop`; under it, "Enter to send · Shift +
Enter for a new line" (hidden on touch screens) and "Clear chat".

### 6.6 Find code

As today: a search input ("Function names, keywords, or a description") with a black "Search" button,
the note "Works without an API key. Matches exact words and meaning.", then the results as cards: a
header with `path:start-end` linking to GitHub and a pill with the symbol, then the code with line
numbers (up to 14 lines, matching lines highlighted with `--blue-soft`). States: two skeleton cards
while searching; "No matches for “…”" with a hint; an error block with "Try again".

### 6.7 Review a PR

- `#/review/{o}/{n}`: an input for the PR number or link and a black "Review" button, with the note "The
  review uses your Groq key, which spends a little of your free quota. Nothing is posted to GitHub."
- `#/review/{o}/{n}/{num}`: no key gives a card "Add your API key to start" with a button; a direct
  link (not just requested in this tab) gives "Start the review?" with the model name and a "Start
  review" button; then a progress card ("Reviewing pull request #n", the elapsed-time message from
  today) and the result. Cached results (`sessionStorage`, per model) show at once with "Run again".
- Result: the PR title (`--t-lg`), meta (state pill, number and author, file count, `+a −d`, "Open on
  GitHub"), then a grey verdict bar: "{n} things to look at" or "No problems found", "{n} files
  reviewed", "Copy as Markdown" and "Run again", and the severity filter chips (nits off by default
  unless they are the only findings, as today). Then findings grouped by file: mono path with a count,
  the file summary, and one card per finding with an uppercase severity label in the severity colour,
  the title, a "line n" link, the message, an optional "Suggested change" code block and the source
  ("AI review · category" or "Fixed check (rule) · category"). The notes from today stay: the "index
  it first" tip, skipped files, and the "AI couldn't review n files" banner.

### 6.8 API key dialog

Same fields and behaviour as today (key with Show/Hide, a model select filled by "Check key" via
`POST /api/models`, "Remember on this device", the privacy line, "Remove key", "Cancel", "Save"),
restyled with the new components. Opening it from a blocked action (Ask, Review) runs that action
after a successful save, as today.

### 6.9 Local page (`reposcout serve`)

Moves onto `ui.css` and the same nav bar, without the tools menu and without repository picking (it
serves one local index). The nav shows the logo and three tabs: **Ask**, **Review a diff**, **Find code**.
The stats (files, sections, model) sit in a repo-bar-style panel under the nav. Each tab keeps its
current behaviour and endpoints (`/ask`, `/review`, `/search`, `/stats`) and uses the same answer,
finding and hit components as the public app. Its JS moves to `static/js/local.js`, importing
`core.js`.

## 7. Architecture

### 7.1 Files

```
src/reposcout/static/
  app.html          public shell: nav, <main id="view">, footer, key dialog, toast region; no inline JS or CSS
  index.html        local shell, same rule
  ui.css            the design system: tokens, base, components, layouts for both pages
  js/core.js        h(), icons, store, api(), ApiError, toast, skeleton/state helpers, safe Markdown, parseLink
  js/tools.js       the tool registry: id, name, category, description, colours, glyph, live flag
  js/nav.js         top bar, All tools menu, theme, key status
  js/key.js         the API key dialog
  js/home.js        home page
  js/repo.js        repository picker, repo bar, indexing card (shared by the tool pages)
  js/ask.js  js/find.js  js/review.js   one module per tool
  js/app.js         router (including old-route redirects) and start-up
  js/local.js       the local page
```

The builder may split or merge modules if a file gets very small or very large, but keeps one module
per tool.

### 7.2 Serving

- `static_files.py` gains an asset route shared by both apps: `GET /static/{version}/{path}` serves an
  allow-listed file from the package's `static/` directory (`ui.css` and `js/*.js`, nothing else),
  with the right content type and `Cache-Control: public, max-age=31536000, immutable`.
- `{version}` is a hash of all served static files, computed once at start-up. Pages reference
  `/static/{version}/ui.css` and `/static/{version}/js/app.js` (as `<script type="module">`), so
  relative imports between modules resolve inside the same version and stay cache-safe. A request with
  an old version still gets the current file (so a page open during a deploy keeps working), but with
  `Cache-Control: no-cache`.
- Path handling rejects anything not in the allow-list (no `..`, no absolute paths, no other
  extensions) with 404.
- The existing `/ui.css` route stays for one release as an alias, so cached old pages don't break.
- `pyproject.toml` package data currently ships only `static/*.html` and `static/*.css`; add
  `static/js/*.js` so installed builds (and the Docker image) include the modules.

### 7.3 Security headers

`deploy/Caddyfile` CSP becomes:
`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`.
Google Fonts are removed from `style-src` and `font-src`. `style-src` keeps `'unsafe-inline'` because
the JS sets `style` attributes (progress, textarea height); no inline `<style>` blocks remain. No inline
event handler attributes (`onclick=`, `onsubmit=`) anywhere; all events use `addEventListener`.

### 7.4 What does not change

The JSON API and its error format, `PublicSettings`, the workspace, limits, BYOK headers, storage
keys (`rs.key`, `rs.model`, `rs.chat.*`, `rs.review.*`), and the server-side behaviour of every route.

## 8. Motion

Only `transform` and `opacity` animate; nothing causes layout shift.

| What | Motion | Why |
|---|---|---|
| Buttons, chips, tiles | `scale(0.97)` (tiles 0.99) on press, 120 ms ease-out | Feedback that the press registered |
| Tile "Open →" arrow | `translateX(3px)` on hover, 180 ms, hover-capable pointers only | Points at where the tile goes |
| All tools menu | fade + `scale(.97) translateY(-4px)` → rest, 160 ms ease-out, origin top-left | Comes from its button |
| Key dialog | fade + `scale(.97)`, 200 ms ease-out, centred | Standard modal entrance |
| New answer, review result | fade + `translateY(6px)` → 0, 220 ms ease-out, once | The result arriving; no stagger |
| Toasts | enter/exit along the same path, 200 ms | Spatial consistency |
| Skeletons | opacity pulse 1.4 s | Shows that something is loading |
| Route changes, tab switches, filters, the theme switch | none | Frequent, keyboard-driven actions |

`prefers-reduced-motion: reduce`: every movement becomes a plain fade of the same length or less; press
scales are removed. The smooth scroll after an answer becomes instant.

## 9. Accessibility and responsiveness

- Visible focus everywhere: a 2 px blue outline with 2 px offset.
- Everything works by keyboard: the menu (open, move, Esc), tiles, cards, chips, filters, the dialog
  (native focus trap) and the composer.
- `aria-live="polite"` on results, the thread, indexing progress and the tool-search results;
  `aria-busy` while loading; `role="alert"` on blocking errors; `aria-current` in the nav;
  `aria-pressed` on filters; disabled "Soon" cards are `aria-disabled` and not focusable.
- Labels on every input and icon-only button.
- Touch targets at least 44 px on `pointer: coarse`.
- Layout tested at 360, 390, 768, 1280 and 1600 px with no horizontal page scroll (code blocks scroll
  inside themselves). Long repository names truncate with an ellipsis in rows and wrap in titles.

## 10. Every state (checklist)

| Where | Loading | Empty | Error (with next step) | Other |
|---|---|---|---|---|
| Recently explored | 3 skeleton rows | "Nothing explored yet…" | "Couldn't load recent repositories" + Try again | |
| Tool search | | "No tool matches…" | | |
| Repository picker | | | Invalid link inline under the field | |
| Indexing | step list + bar | | "Couldn't index…" + Try again / Pick another | queue position ("number n in line") |
| Ask | skeleton answer, then "Still working…" after 8 s | starters | inline error + Try again; key errors open the key dialog | no key: dialog opens on send |
| Find code | 2 skeleton cards | "No matches…" | error block + Try again | |
| Review | progress card with elapsed time | | error card + Try again | no key card; "Start the review?" step; cached result |
| Any request | | | 429 rate limit: "You've hit the limit. Try again in {n} min." from `retry_after`; 503 busy: "RepoScout is busy. Try again in a moment." with Retry; network down: "Can't reach the server…" | |

## 11. Testing and verification

- **Python tests (TDD):** the asset route (serves the allow-listed files with the right type and
  immutable cache; old versions get `no-cache`; traversal, unknown paths and other extensions get 404);
  both pages reference `/static/{version}/…`, contain no `{{…}}` placeholders, no inline `<script>`
  with content, no `<style>` blocks, no `on*=` attributes, and no `fonts.googleapis.com`; the `/ui.css`
  alias still works. The existing UI assertions in `tests/test_web.py` and
  `tests/test_github_server.py` are updated to the new URLs. All other tests must still pass
  unchanged.
- **Manual end to end** on `reposcout serve-public` with a small repository: add a repo and watch it
  index, Ask, Find code, review a PR, set and remove the key, old `#/r/…` and `#/pr/…` links redirect;
  and `reposcout serve` for the local page.
- **Screenshots** (headless Chrome is on the dev machine) at 390 and 1280 px, light and dark, plus one
  with reduced motion, of home, each tool page and the key dialog.
- **Design passes** from the plan: find-animation-opportunities, improve-animations,
  review-animations, then break-ui (long names, 0 and 500 results, slow and failed requests, tiny and
  huge screens, keyboard only), fixing what breaks.

## 12. Calls made by the designer (not decided by the owner)

- The "Soon" cards for the six future tools stay visible (the owner approved the prototype with them).
- The three live tools appear both as tiles and in the category list, as on imagestool.
- Review does not require indexing (as today for PR links; today's in-repo Review tab sat behind
  indexing, which this changes deliberately); Ask and Find code index on first visit.
- Refresh index and Open on GitHub stay, as icon actions in the repo bar.
- Picker chips come from the shared public list (ready ones), topped up with today's examples.
- The key stays a dialog; there is no settings page.
- Old hash links redirect to the new routes.
- The local page gets the new look but keeps its tab structure, since it serves a single repository.
- Toast timings follow Sonner's defaults, adapted as listed in 5.4.

## 13. Left to the builder

Exact module boundaries inside section 7.1, the SVG glyph details within the described style,
contrast adjustments needed to pass AA, the order of tasks, and the copy for any error message not
quoted here (short, plain English, and it says what to do next).
