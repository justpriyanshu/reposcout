"""The public web app: index any public GitHub repo, ask about it, review pull requests.

Visitors bring their own LLM API key. The key is sent with each request in the
``X-LLM-Key`` header, used for that one request and never stored or logged.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import time
from threading import Lock

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
from .config import PublicSettings
from .github_public import GitHubAPIError, PublicGitHub
from .limits import Busy, ConcurrencyLimit, RateLimiter
from .links import LinkError, RepoId, blob_url, parse_github_link, pr_line_url
from .workspace import QueueFullError, Workspace

log = logging.getLogger("reposcout.web")

MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
NOT_CHAT_MODELS = re.compile(r"whisper|guard|tts|orpheus|playai|distil|embed|allam", re.I)
MAX_BODY_BYTES = 64 * 1024
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
    app.add_middleware(BodySizeLimit, max_bytes=MAX_BODY_BYTES)
    http = http or httpx.Client(timeout=settings.request_timeout)
    store = settings.rate_limit_storage
    index_limiter = RateLimiter(settings.index_per_hour, 3600, store, "index")
    llm_limiter = RateLimiter(settings.llm_per_10min, 600, store, "llm")
    search_limiter = RateLimiter(settings.search_per_10min, 600, store, "search")
    llm_slots = ConcurrencyLimit(settings.max_concurrent_llm, per_key=1)
    search_slots = ConcurrencyLimit(settings.max_concurrent_search, per_key=2)
    scanners = make_scanners()
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

    def ready_repo(owner: str, name: str) -> tuple[RepoId, dict, RepoAssistant]:
        repo = RepoId(owner, name)
        record = workspace.get(repo)
        if record is None:
            raise ApiError(404, "not_indexed", "This repository hasn't been added yet.")
        store = workspace.store(repo)
        if not record["ready"] or store is None:
            raise ApiError(409, "not_ready", "This repository is still being indexed.")
        workspace.touch(repo)
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
    def repos() -> dict:
        return {"repos": workspace.list()}

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
        return {"repo": record, "link": {"kind": link.kind, "pull": link.pull}}

    @app.get("/api/repos/{owner}/{name}")
    def repo_status(owner: str, name: str) -> dict:
        record = workspace.get(RepoId(owner, name))
        if record is None:
            raise ApiError(404, "not_indexed", "This repository hasn't been added yet.")
        return {"repo": record}

    @app.post("/api/repos/{owner}/{name}/search")
    def search(owner: str, name: str, req: SearchRequest, request: Request) -> dict:
        limit(search_limiter, request, "searches")
        repo, record, assistant = ready_repo(owner, name)
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
        repo, record, assistant = ready_repo(owner, name)
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
            return run_review(req, link, llm)

    def run_review(req: ReviewRequest, link, llm: OpenAICompatibleLLM | TracedLLM) -> dict:
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
        record = workspace.get(repo)
        store = workspace.store(repo)
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
