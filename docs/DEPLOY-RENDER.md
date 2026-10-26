# Deploying the public RepoScout app on Render (free, no credit card)

This runs the public app (`reposcout serve-public`: anyone can paste a public GitHub repo or
pull request and use their own Groq key) on Render's free plan, with free services that
need no credit card for what Render's free plan can't do.

```
visitor ──HTTPS──> Render web service (Dockerfile.render, 512 MB)
                      ├──> Neon Postgres + pgvector   indexes and the job queue survive restarts
                      ├──> Jina AI (optional)         code embeddings + reranking
                      ├──> Groq                       with the visitor's own key
                      ├──> Sentry (optional)          errors
                      └──> GitHub                     clone, PR diffs
```

## What the free plan means for this app

| Render free plan | What RepoScout does about it |
|---|---|
| The disk is wiped on every restart, redeploy and sleep | Indexes, the repository registry and queued jobs live in Neon Postgres (`REPOSCOUT_DATABASE_URL`). Without it the app still works, but every repository is re-indexed after each restart. |
| The service sleeps after 15 minutes without visitors and takes about a minute to wake | Nothing to do; queued indexing jobs resume when it wakes. |
| 512 MB RAM and a fraction of a CPU, so no Ollama | Embeddings come from Jina's API (or the model-free `hash` embedder); chat uses the visitor's Groq key. The idle app uses about 75 MB. |
| No background workers, no Redis that keeps data | Indexing runs in a thread inside the web service, with its queue in Postgres. Rate limits count in memory, which is right for one instance. |
| HTTPS is provided | Caddy (in `deploy/`) is only needed when you host on your own server. |
| Requests arrive through Render's proxy, and Render doesn't document a client-IP header that visitors can't forge | Leave `REPOSCOUT_TRUST_PROXY` unset. Per-visitor rate limits then see the proxy's address, so they act as overall limits; the concurrency caps still protect the server. |

Free tiers change; check render.com/docs/free, neon.com/pricing and jina.ai before relying on
the numbers here.

## 1. Neon: a free Postgres with pgvector

1. Sign up at neon.com (GitHub login works; no card).
2. Create a project in the region closest to your Render region (e.g. AWS US West 2 for Render Oregon).
3. Copy the connection string (the "pooled" one is fine). It looks like
   `postgresql://user:password@ep-....aws.neon.tech/neondb?sslmode=require`.

RepoScout creates the pgvector extension and its tables on first start. The free plan has
0.5 GB per project; `REPOSCOUT_MAX_TOTAL_CHUNKS` (default 60,000, about 250 MB with 768-number
vectors) evicts the least recently used repositories before that fills up.

## 2. Optional: Jina for better search

Without it, set `REPOSCOUT_EMBED_PROVIDER=hash` (matches words only, no model).
With it, search understands meaning:

1. Get a key at jina.ai (10 million free tokens; non-commercial use).
2. Set `REPOSCOUT_EMBED_PROVIDER=jina`, `REPOSCOUT_JINA_API_KEY=...` and, for reranking,
   `REPOSCOUT_RERANK_PROVIDER=jina`.

Changing the embedder later means existing indexes are rebuilt the next time each repository
is refreshed.

## 3. Optional: "Sign in with GitHub"

Visitors can use the app without an account (each browser gets its own private list). To let
them sign in with GitHub, so their list follows them across devices and they can pick from
their own public repositories:

1. github.com → **Settings → Developer settings → OAuth Apps → New OAuth App** (free).
2. **Homepage URL:** your Render URL, e.g. `https://reposcout.onrender.com`.
   **Authorization callback URL:** the same plus `/auth/github/callback`.
3. Create a client secret, and set `REPOSCOUT_GITHUB_CLIENT_ID`, `REPOSCOUT_GITHUB_CLIENT_SECRET`
   and `REPOSCOUT_PUBLIC_URL` (the Render URL) on the service.

The app asks GitHub for no permissions (public profile only), uses the GitHub token once to
learn who signed in, and doesn't keep it. Sessions are stored in Postgres, so sign-ins survive
restarts too.

## 4. Create the Render web service

Render dashboard → **New → Web Service** → connect the GitHub repository →

- **Language:** Docker, **Dockerfile path:** `./Dockerfile.render`
- **Instance type:** Free
- **Health check path:** `/health`
- **Environment variables:**

| Variable | Value |
|---|---|
| `REPOSCOUT_DATABASE_URL` | the Neon connection string |
| `REPOSCOUT_EMBED_PROVIDER` | `jina` (or `hash`) |
| `REPOSCOUT_JINA_API_KEY` | your Jina key (if using Jina) |
| `REPOSCOUT_RERANK_PROVIDER` | `jina` (optional) |
| `GITHUB_TOKEN` | optional: a fine-grained token with public read access raises GitHub's limit from 60 to 5,000 requests/hour |
| `SENTRY_DSN` | optional, from a free Sentry project |
| `REPOSCOUT_GITHUB_CLIENT_ID`, `REPOSCOUT_GITHUB_CLIENT_SECRET` | optional, for "Sign in with GitHub" (step 3) |
| `REPOSCOUT_PUBLIC_URL` | your Render URL, e.g. `https://reposcout.onrender.com` (sign-in callback; also turns on Secure cookies) |

The image builds the web UI, installs the dependencies with uv, and includes the tree-sitter
grammars and gitleaks, so nothing is downloaded at run time.

> `render.yaml` in this repository describes a different service: the single-repository app
> (`reposcout serve`) for one repository you choose. Use the steps above for the public app.

## 5. Check it

Open the service URL: the home page should load, adding a small public repository should show
indexing progress, and after a redeploy (Manual Deploy → Deploy latest commit) the repository
should still be there. `GET /health` returns `{"status": "ok"}`.

## Settings for the public app

All are environment variables; the defaults suit the free plan.

| Variable | Default | Meaning |
|---|---|---|
| `REPOSCOUT_DATABASE_URL` (or `DATABASE_URL`) | | Postgres with pgvector for indexes and the registry; empty = files in `REPOSCOUT_DATA_DIR` |
| `REPOSCOUT_DATA_DIR` | `~/.reposcout-data` (`/data` in Docker) | Temporary checkouts (and indexes without a database) |
| `REPOSCOUT_MAX_TOTAL_CHUNKS` | `60000` | Chunks kept across all repositories; least recently used are evicted |
| `REPOSCOUT_MAX_REPOS` | `30` | Repositories kept |
| `REPOSCOUT_MAX_REPO_MB` / `MAX_FILES` / `MAX_CHUNKS` | `80` / `3000` / `12000` | Per-repository limits |
| `REPOSCOUT_RATE_LIMIT_STORAGE` | `memory://` | `redis://host:6379` shares rate limits across several instances (needs `pip install redis`) |
| `REPOSCOUT_TRACE_PUBLIC` | off | Record visitors' LLM calls in Langfuse (needs `LANGFUSE_*` keys; prompts contain their questions) |
| `REPOSCOUT_SCANNERS` | `bandit,gitleaks` | Static analysers used in reviews |
| `REPOSCOUT_GITHUB_CLIENT_ID` / `_SECRET` | | GitHub OAuth App for optional sign-in |
| `REPOSCOUT_PUBLIC_URL` | taken from the request | Public address, for the sign-in callback |
| `REPOSCOUT_COOKIE_SECURE` | on when `PUBLIC_URL` is https | Send cookies over HTTPS only |
| `REPOSCOUT_SESSION_DAYS` | `30` | How long a sign-in lasts |

## Privacy: what each person sees

Each person has a private list of the repositories they added: signed-in people under their
GitHub account, everyone else under an anonymous cookie in their browser. Listing, status,
search, questions and the repository context used in reviews only work for repositories in
your own list, so nobody can see or probe what others explored. Indexes themselves are shared
behind the scenes (one per public repository) to save storage, so a repository someone else
indexed is ready instantly when you add it. Chat history, saved reviews and your Groq key stay
in your browser. The queue position ("number 3 in line") is the only sign that others are using
the server.
