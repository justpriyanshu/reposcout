"""The public web app: index any public GitHub repo, ask about it, review pull requests.

Visitors bring their own LLM API key. The key is sent with each request in the
``X-LLM-Key`` header, used for that one request and never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import re
import time
from threading import Lock
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .. import __version__, static_files
from ..assistant import RepoAssistant
from ..embeddings import Embedder
from ..errors import ProviderError, RepoScoutError
from ..llm import OpenAICompatibleLLM
from ..observability import TracedLLM, langfuse_client
from ..rerank import Reranker
from ..review import Reviewer, Severity
from ..review.scanners import make_scanners
from .accounts import (
    OAUTH_COOKIE,
    OAUTH_COOKIE_SECONDS,
    SESSION_COOKIE,
    VISITOR_COOKIE,
    VISITOR_COOKIE_DAYS,
    AuthError,
    GitHubOAuth,
    Owner,
    new_visitor_id,
    pkce_pair,
    safe_next,
)
from .config import PublicSettings
from .github_public import GitHubAPIError, PublicGitHub
from .limits import Busy, ConcurrencyLimit, RateLimiter
from .links import LinkError, RepoId, blob_url, parse_github_link, pr_line_url
from .workspace import QueueFullError, Workspace

log = logging.getLogger("reposcout.web")

MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
NOT_CHAT_MODELS = re.compile(r"whisper|guard|tts|orpheus|playai|distil|embed|allam", re.I)
MAX_BODY_BYTES = 64 * 1024
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Served with every page and API response (on hosts like Render there's no proxy adding them).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://avatars.githubusercontent.com; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
GITHUB_REPOS_TTL = 300
# Visitors' requests give up on a busy provider quickly instead of holding a server thread.
PUBLIC_RETRY_ATTEMPTS = 3
PUBLIC_MAX_RETRY_WAIT = 10.0


class BodySizeLimit:
    """Reject request bodies over ``max_bytes`` before they are read into memory."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = True
                if too_big:
                    return await self._reject(send)
        # Read the (small) body up front so an over-long chunked upload is refused too.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return await self.app(scope, _replay([message]), send)
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return await self._reject(send)
            if not message.get("more_body"):
                break
        return await self.app(
            scope, _replay([{"type": "http.request", "body": bytes(body), "more_body": False}], receive), send
        )

    @staticmethod
    async def _reject(send):
        response = JSONResponse({"error": "Request is too large.", "code": "too_large"}, 413)
        await send({"type": "http.response.start", "status": 413, "headers": response.raw_headers})
        await send({"type": "http.response.body", "body": response.body})


def _replay(messages: list[dict], then=None):
    queue = list(messages)

    async def receive():
        if queue:
            return queue.pop(0)
        if then is not None:
            return await then()  # e.g. waiting for the client to disconnect
        return {"type": "http.disconnect"}

    return receive


def visitor_key(ip: str) -> str:
    """Group IPv6 addresses by their /64 network: one home or server usually gets a whole /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


class LinkRequest(BaseModel):
    url: str = Field(min_length=3, max_length=300)
    refresh: bool = False


class Turn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(default="", max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=6)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class ReviewRequest(BaseModel):
    url: str = Field(min_length=3, max_length=300)
    min_severity: Severity = Severity.minor


def create_public_app(
    settings: PublicSettings,
    workspace: Workspace,
    embedder: Embedder,
    github: PublicGitHub,
    http: httpx.Client | None = None,
    reranker: Reranker | None = None,
) -> FastAPI:
    app = FastAPI(title="RepoScout", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.public_url = settings.public_url
    app.add_middleware(BodySizeLimit, max_bytes=MAX_BODY_BYTES)
    http = http or httpx.Client(timeout=settings.request_timeout)
    store = settings.rate_limit_storage
    index_limiter = RateLimiter(settings.index_per_hour, 3600, store, "index")
    llm_limiter = RateLimiter(settings.llm_per_10min, 600, store, "llm")
    search_limiter = RateLimiter(settings.search_per_10min, 600, store, "search")
    llm_slots = ConcurrencyLimit(settings.max_concurrent_llm, per_key=1)
    search_slots = ConcurrencyLimit(settings.max_concurrent_search, per_key=2)
    scanners = make_scanners()
    accounts = workspace.accounts
    oauth = (
        GitHubOAuth(settings.github_client_id, settings.github_client_secret, http)
        if settings.github_client_id and settings.github_client_secret
        else None
    )
    github_repos_cache: dict[str, tuple[float, list[dict]]] = {}
    key_cache: dict[str, float] = {}
    key_lock = Lock()

    # ------------------------------------------------------------------ helpers

    def client_ip(request: Request) -> str:
        """The visitor's address. Behind a proxy, only the proxy-set X-Real-IP is trusted:
        X-Forwarded-For can be extended by the visitor, so its first entry is forgeable."""
        if settings.trust_proxy:
            real = request.headers.get("x-real-ip", "").strip()
            if real:
                return visitor_key(real)
        return visitor_key(request.client.host) if request.client else "unknown"

    def owner_of(request: Request) -> Owner:
        """The signed-in GitHub user, or this browser's anonymous id (created on first use;
        the middleware below sets its cookie)."""
        found = accounts.session_user(request.cookies.get(SESSION_COOKIE))
        if found is not None:
            return Owner(found[0], found[1])
        visitor = request.cookies.get(VISITOR_COOKIE, "")
        if not VISITOR_RE.match(visitor):
            visitor = getattr(request.state, "new_visitor", None) or new_visitor_id()
            request.state.new_visitor = visitor
        return Owner(f"anon:{visitor}")

    def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )

    def base_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def mine(who: Owner, repo: RepoId) -> bool:
        return accounts.has(who.key, repo.key)

    def record_for(entry: dict) -> dict:
        """A list entry's current record, or an "expired" stub if the server dropped the
        index to save space (the person can index it again)."""
        record = workspace.get(RepoId(entry["owner"], entry["name"]))
        if record is not None:
            return record
        full_name = f"{entry['owner']}/{entry['name']}"
        return {
            "key": entry["repo_key"],
            "owner": entry["owner"],
            "name": entry["name"],
            "full_name": full_name,
            "status": "expired",
            "stage": "Removed to save space",
            "ready": False,
            "error": "",
            "queue_position": 0,
        }

    class _Slot:
        def __init__(self, limit_: ConcurrencyLimit, request: Request):
            self.cm = limit_.slot(client_ip(request))

        def __enter__(self):
            try:
                return self.cm.__enter__()
            except Busy as exc:
                if str(exc) == "visitor":
                    raise ApiError(
                        429, "one_at_a_time", "Wait for your previous request to finish, then try again.", 5
                    ) from exc
                raise ApiError(
                    503, "busy", "The server is busy with other requests. Try again in a moment.", 10
                ) from exc

        def __exit__(self, *exc_info):
            return self.cm.__exit__(*exc_info)

    def limit(limiter: RateLimiter, request: Request, what: str) -> None:
        wait = limiter.check(client_ip(request))
        if wait:
            raise ApiError(
                429,
                "too_many_requests",
                f"You've hit the limit for {what}. Try again in {int(wait // 60) + 1} minute(s).",
                wait,
            )

    def user_llm(request: Request) -> OpenAICompatibleLLM | TracedLLM:
        key = request.headers.get("x-llm-key", "").strip()
        if not key:
            raise ApiError(
                401, "no_key", f"Add your {settings.llm_provider_name} API key in Settings to use this."
            )
        model = request.headers.get("x-llm-model", "").strip() or settings.default_model
        if not MODEL_RE.match(model):
            raise ApiError(400, "bad_model", "That model name isn't valid.")
        llm = OpenAICompatibleLLM(
            model,
            settings.llm_base_url,
            key,
            timeout=settings.request_timeout,
            client=http,
            retry_attempts=PUBLIC_RETRY_ATTEMPTS,
            max_retry_wait=PUBLIC_MAX_RETRY_WAIT,
        )
        # Visitors' prompts are only traced when the operator opts in (see observability.py).
        client = langfuse_client() if settings.trace_public else None
        return TracedLLM(llm, client) if client is not None else llm

    def list_models(key: str) -> list[str]:
        try:
            resp = http.get(
                f"{settings.llm_base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}
            )
        except httpx.HTTPError as exc:
            raise ApiError(502, "provider_down", f"Couldn't reach {settings.llm_provider_name}.") from exc
        if resp.status_code in (401, 403):
            raise ApiError(401, "bad_key", f"{settings.llm_provider_name} rejected this API key.")
        if resp.status_code >= 400:
            raise ApiError(
                502, "provider_error", f"{settings.llm_provider_name} returned {resp.status_code}."
            )
        ids = [m.get("id", "") for m in resp.json().get("data", []) if m.get("active", True)]
        return sorted({i for i in ids if i and not NOT_CHAT_MODELS.search(i)})

    def check_key(llm: OpenAICompatibleLLM | TracedLLM) -> None:
        """Validate a key once per 10 minutes before long jobs (reviews swallow LLM errors)."""
        token = llm.headers.get("Authorization", "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with key_lock:
            if key_cache.get(digest, 0) > time.time():
                return
        list_models(token.removeprefix("Bearer "))
        with key_lock:
            key_cache[digest] = time.time() + 600
            if len(key_cache) > 5000:
                key_cache.clear()

    def ready_repo(owner: str, name: str, who: Owner) -> tuple[RepoId, dict, RepoAssistant]:
        repo = RepoId(owner, name)
        # Only repositories in your own list are visible to you.
        record = workspace.get(repo) if mine(who, repo) else None
        if record is None:
            raise ApiError(404, "not_indexed", "This repository hasn't been added yet.")
        store = workspace.store(repo)
        if not record["ready"] or store is None:
            raise ApiError(409, "not_ready", "This repository is still being indexed.")
        workspace.touch(repo)
        accounts.touch(who.key, repo.key)
        repo = RepoId(record["owner"], record["name"])
        assistant = RepoAssistant(
            store,
            embedder,
            None,
            settings.top_k,
            settings.max_context_chars,
            repo_name=repo.full_name,
            reranker=reranker,
        )
        return repo, record, assistant

    def sources_with_links(repo: RepoId, commit: str, sources: list[dict]) -> list[dict]:
        for s in sources:
            s["url"] = blob_url(repo, commit, s["path"], s["start_line"], s["end_line"])
        return sources

    # ------------------------------------------------------------------ errors

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status, headers=headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        text = str(exc)
        if "rejected the key" in text:
            return JSONResponse(
                {"error": f"{settings.llm_provider_name} rejected your API key.", "code": "bad_key"}, 401
            )
        if "rate limiting" in text:
            return JSONResponse(
                {
                    "error": f"{settings.llm_provider_name} is rate limiting your key. Wait a minute and try "
                    "again.",
                    "code": "provider_busy",
                },
                429,
            )
        if "404" in text and "model" in text.lower():
            return JSONResponse(
                {
                    "error": "Your key can't use this model. Pick another one in Settings.",
                    "code": "bad_model",
                },
                400,
            )
        return JSONResponse({"error": text, "code": "provider_error"}, 502)

    @app.exception_handler(RepoScoutError)
    async def repo_error(request: Request, exc: RepoScoutError):
        return JSONResponse({"error": str(exc), "code": "bad_request"}, 400)

    # ------------------------------------------------------------------ middleware

    @app.middleware("http")
    async def people_and_headers(request: Request, call_next):
        # Cookie-based sessions: refuse state-changing requests sent from other sites.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc not in _own_hosts(request):
                return JSONResponse({"error": "Cross-site request refused.", "code": "bad_origin"}, 403)
        response = await call_next(request)
        visitor = getattr(request.state, "new_visitor", None)
        if visitor:
            set_cookie(response, VISITOR_COOKIE, visitor, VISITOR_COOKIE_DAYS * 86400)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(("/api/docs", "/openapi.json")):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    # ------------------------------------------------------------------ people

    @app.get("/api/me")
    def me(request: Request) -> dict:
        return {"user": owner_of(request).user, "github_login": oauth is not None}

    @app.post("/api/logout")
    def logout(request: Request) -> Response:
        accounts.end_session(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(VISITOR_COOKIE, path="/")  # start a fresh anonymous list
        return response

    @app.get("/auth/github/login", include_in_schema=False)
    def github_login(request: Request, next: str = "/") -> Response:
        if oauth is None:
            raise ApiError(404, "no_github_login", "Sign-in with GitHub isn't set up on this server.")
        limit(index_limiter, request, "sign-in attempts")
        state = new_visitor_id() + new_visitor_id()
        verifier, challenge = pkce_pair()
        target = safe_next(next)
        response = RedirectResponse(
            oauth.authorize_url(f"{base_url(request)}/auth/github/callback", state, challenge), 302
        )
        set_cookie(response, OAUTH_COOKIE, f"{state}.{verifier}.{_b64(target)}", OAUTH_COOKIE_SECONDS)
        return response

    @app.get("/auth/github/callback", include_in_schema=False)
    def github_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
        stored = request.cookies.get(OAUTH_COOKIE, "")
        parts = stored.split(".")

        def fail(message: str) -> Response:
            response = RedirectResponse("/?" + urlencode({"login_error": message}), 302)
            response.delete_cookie(OAUTH_COOKIE, path="/")
            return response

        if oauth is None:
            return fail("Sign-in with GitHub isn't set up on this server.")
        if error:
            return fail(
                "Sign-in was cancelled." if error == "access_denied" else "GitHub couldn't sign you in."
            )
        if len(parts) != 3 or not code or not hmac.compare_digest(parts[0], state):
            return fail("That sign-in link has expired. Please try again.")
        try:
            profile = oauth.user_for_code(code, parts[1], f"{base_url(request)}/auth/github/callback")
        except AuthError as exc:
            return fail(str(exc))
        token = accounts.create_session(profile)
        visitor = request.cookies.get(VISITOR_COOKIE, "")
        if VISITOR_RE.match(visitor):
            accounts.merge(f"anon:{visitor}", f"gh:{int(profile['id'])}")
        response = RedirectResponse(safe_next(_unb64(parts[2])), 302)
        set_cookie(response, SESSION_COOKIE, token, settings.session_days * 86400)
        response.delete_cookie(OAUTH_COOKIE, path="/")
        return response

    @app.get("/api/me/github-repos")
    def my_github_repos(request: Request) -> dict:
        who = owner_of(request)
        if who.user is None:
            raise ApiError(401, "sign_in", "Sign in with GitHub to see your repositories.")
        login = who.user["login"]
        cached = github_repos_cache.get(login)
        if cached and cached[0] > time.time():
            return {"repos": cached[1]}
        limit(search_limiter, request, "requests")
        try:
            found = github.user_repos(login)
        except GitHubAPIError as exc:
            raise ApiError(502, "github_error", str(exc)) from exc
        repos = [
            {
                "full_name": r.get("full_name", ""),
                "description": (r.get("description") or "")[:300],
                "language": r.get("language") or "",
                "stars": int(r.get("stargazers_count") or 0),
                "pushed_at": r.get("pushed_at") or "",
                "html_url": r.get("html_url") or "",
                "fork": bool(r.get("fork")),
            }
            for r in found
        ]
        github_repos_cache[login] = (time.time() + GITHUB_REPOS_TTL, repos)
        if len(github_repos_cache) > 2000:
            github_repos_cache.clear()
        return {"repos": repos}

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def page() -> str:
        return static_files.page("app.html")

    static_files.add_routes(app)

    # Async so they answer even when every worker thread is busy.
    @app.get("/api/health")
    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    async def config() -> dict:
        return {
            "provider": settings.llm_provider_name,
            "key_url": settings.llm_key_url,
            "default_model": settings.default_model,
            "limits": {
                "max_repo_mb": settings.max_repo_mb,
                "max_files": settings.max_files,
                "max_pr_files": settings.max_pr_files,
            },
        }

    @app.post("/api/models")
    def models(request: Request) -> dict:
        key = request.headers.get("x-llm-key", "").strip()
        if not key:
            raise ApiError(401, "no_key", "Enter an API key first.")
        limit(search_limiter, request, "requests")
        with _Slot(search_slots, request):
            found = list_models(key)
        default = settings.default_model if settings.default_model in found else (found[0] if found else "")
        return {"models": found, "default": default}

    # ------------------------------------------------------------------ repositories

    @app.get("/api/repos")
    def repos(request: Request) -> dict:
        """Only the caller's own repositories, most recently used first."""
        who = owner_of(request)
        return {"repos": [record_for(e) for e in accounts.entries(who.key)]}

    @app.post("/api/repos", status_code=202)
    def add_repo(req: LinkRequest, request: Request) -> dict:
        try:
            link = parse_github_link(req.url)
        except LinkError as exc:
            raise ApiError(400, "bad_link", str(exc)) from exc
        if workspace.would_queue(link.repo, force=req.refresh):
            limit(index_limiter, request, "adding repositories")
        try:
            record = workspace.submit(link.repo, force=req.refresh, requested_by=client_ip(request))
        except QueueFullError as exc:
            raise ApiError(429, "queue_full", str(exc), 60) from exc
        accounts.link(owner_of(request).key, link.repo.key, record["owner"], record["name"])
        return {"repo": record, "link": {"kind": link.kind, "pull": link.pull}}

    @app.get("/api/repos/{owner}/{name}")
    def repo_status(owner: str, name: str, request: Request) -> dict:
        repo = RepoId(owner, name)
        who = owner_of(request)
        entry = next((e for e in accounts.entries(who.key, 1000) if e["repo_key"] == repo.key), None)
        if entry is None:
            raise ApiError(404, "not_indexed", "This repository hasn't been added yet.")
        return {"repo": record_for(entry)}

    @app.delete("/api/repos/{owner}/{name}")
    def forget_repo(owner: str, name: str, request: Request) -> dict:
        """Remove a repository from your own list (the shared index stays for others)."""
        accounts.unlink(owner_of(request).key, RepoId(owner, name).key)
        return {"ok": True}

    @app.post("/api/repos/{owner}/{name}/search")
    def search(owner: str, name: str, req: SearchRequest, request: Request) -> dict:
        limit(search_limiter, request, "searches")
        repo, record, assistant = ready_repo(owner, name, owner_of(request))
        with _Slot(search_slots, request):
            results = assistant.search(req.query, k=10)
        return {
            "results": [
                {
                    "path": r.path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "kind": r.kind,
                    "name": r.name,
                    "language": r.language,
                    "text": r.text,
                    "url": blob_url(repo, record["commit_sha"], r.path, r.start_line, r.end_line),
                }
                for r in results
            ]
        }

    @app.post("/api/repos/{owner}/{name}/ask")
    def ask(owner: str, name: str, req: AskRequest, request: Request) -> dict:
        llm = user_llm(request)
        limit(llm_limiter, request, "questions and reviews")
        repo, record, assistant = ready_repo(owner, name, owner_of(request))
        assistant.llm = llm
        with _Slot(llm_slots, request):
            answer = assistant.ask(req.question, history=[t.model_dump() for t in req.history])
        data = answer.to_dict()
        data["sources"] = sources_with_links(repo, record["commit_sha"], data["sources"])
        data["model"] = llm.model
        data["usage"] = llm.usage.as_dict()
        return data

    # ------------------------------------------------------------------ reviews

    @app.post("/api/review")
    def review(req: ReviewRequest, request: Request) -> dict:
        try:
            link = parse_github_link(req.url)
        except LinkError as exc:
            raise ApiError(400, "bad_link", str(exc)) from exc
        if not link.pull:
            raise ApiError(
                400, "not_a_pull", "That's a repository link. Paste a pull request link to review it."
            )
        llm = user_llm(request)
        limit(llm_limiter, request, "questions and reviews")
        with _Slot(llm_slots, request):
            return run_review(req, link, llm, owner_of(request))

    def run_review(req: ReviewRequest, link, llm: OpenAICompatibleLLM | TracedLLM, who: Owner) -> dict:
        check_key(llm)
        try:
            pr = github.pull(link.repo, link.pull)
        except GitHubAPIError as exc:
            if exc.status == 404:
                raise ApiError(
                    404, "pr_not_found", "Pull request not found (only public repositories)."
                ) from exc
            raise ApiError(502, "github_error", str(exc)) from exc
        changed = int(pr.get("changed_files") or 0)
        lines = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
        if changed > settings.max_pr_files or lines > settings.max_pr_lines:
            raise ApiError(
                413,
                "pr_too_big",
                f"This pull request changes {changed} files and {lines:,} lines. This server reviews up to "
                f"{settings.max_pr_files} files and {settings.max_pr_lines:,} lines.",
            )
        try:
            diff = github.pull_diff(link.repo, link.pull)
        except GitHubAPIError as exc:
            if exc.status == 413:
                raise ApiError(413, "pr_too_big", str(exc)) from exc
            raise ApiError(502, "github_error", str(exc)) from exc

        base = (pr.get("base") or {}).get("repo") or {}
        full_name = base.get("full_name") or link.repo.full_name
        owner, _, name = full_name.partition("/")
        repo = RepoId(owner, name)
        context = None
        # Repository context only from your own list (otherwise it would reveal what others indexed).
        record = workspace.get(repo) if mine(who, repo) else None
        store = workspace.store(repo) if record else None
        if record and record["ready"] and store is not None:
            context = RepoAssistant(
                store,
                embedder,
                None,
                settings.top_k,
                settings.max_context_chars,
                repo_name=repo.full_name,
                reranker=reranker,
            )
            workspace.touch(repo)

        reviewer = Reviewer(
            llm,
            context,
            min_severity=req.min_severity,
            workers=max(1, settings.review_workers),
            scanners=scanners,
        )
        result = reviewer.review(
            diff, pr.get("title") or "", pr.get("body") or "", load_source=github.source_loader(pr)
        )
        data = result.to_dict()
        for f in data["findings"]:
            f["url"] = pr_line_url(repo, link.pull, f["path"], f["line"])
        data["markdown"] = result.to_markdown()
        data["pull"] = {
            "number": link.pull,
            "title": pr.get("title") or "",
            "url": pr.get("html_url") or f"{repo.html_url}/pull/{link.pull}",
            "author": (pr.get("user") or {}).get("login") or "",
            "state": "merged" if pr.get("merged_at") else (pr.get("state") or ""),
            "additions": int(pr.get("additions") or 0),
            "deletions": int(pr.get("deletions") or 0),
            "changed_files": changed,
            "repo": repo.full_name,
        }
        data["used_repo_context"] = context is not None
        data["model"] = llm.model
        return data

    return app


def _own_hosts(request: Request) -> set[str]:
    """Host names this app is reached at. Only hosts are compared: behind a proxy the app
    may see http while the browser used https."""
    hosts = {request.headers.get("host", "")}
    public = request.app.state.public_url if hasattr(request.app.state, "public_url") else ""
    if public:
        hosts.add(urlparse(public).netloc)
    return hosts


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _unb64(text: str) -> str:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return "/"


def build_public_app(settings: PublicSettings | None = None, embedder: Embedder | None = None) -> FastAPI:
    """Wire everything from environment settings and start the indexing worker."""
    from ..config import Settings
    from ..embeddings import make_embedder
    from ..rerank import make_reranker

    settings = settings or PublicSettings()
    embedder = embedder or make_embedder(Settings())
    github = PublicGitHub(settings.github_token, settings.github_api_url)
    workspace = Workspace(settings, embedder, github)
    workspace.start()
    app = create_public_app(settings, workspace, embedder, github, reranker=make_reranker(Settings()))
    app.state.workspace = workspace
    return app
