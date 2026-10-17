"""HTTP API + web UI + GitHub webhook receiver."""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict, deque
from importlib import resources
from threading import Lock

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .errors import IndexMismatchError, ProviderError, RepoScoutError
from .github import GitHubClient, PullRequestRef, verify_signature
from .review import Severity
from .services import Services, build_services

log = logging.getLogger("reposcout.server")

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    k: int | None = Field(default=None, ge=1, le=30)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)


class ReviewRequest(BaseModel):
    diff: str = Field(min_length=1, max_length=2_000_000)
    title: str = ""
    description: str = ""
    min_severity: Severity = Severity.nit


class IndexRequest(BaseModel):
    rebuild: bool = False


class Metrics:
    """Tiny in-process request metrics: count, errors and latency percentiles per route."""

    def __init__(self, window: int = 500):
        self._lock = Lock()
        self._data: dict[str, dict] = {}
        self.window = window

    def record(self, route: str, ms: float, error: bool) -> None:
        with self._lock:
            entry = self._data.setdefault(
                route, {"count": 0, "errors": 0, "latencies": deque(maxlen=self.window)}
            )
            entry["count"] += 1
            entry["errors"] += int(error)
            entry["latencies"].append(ms)

    def snapshot(self) -> dict:
        out = {}
        with self._lock:
            for route, entry in self._data.items():
                latencies = sorted(entry["latencies"])
                out[route] = {
                    "count": entry["count"],
                    "errors": entry["errors"],
                    "p50_ms": _percentile(latencies, 0.5),
                    "p95_ms": _percentile(latencies, 0.95),
                }
        return out


def _percentile(ordered: list[float], p: float) -> float | None:
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 1)


def create_app(
    settings: Settings | None = None, services: Services | None = None, github: GitHubClient | None = None
) -> FastAPI:
    settings = settings or Settings()
    services = services or build_services(settings)
    if github is None and settings.github_token:
        github = GitHubClient(settings.github_token, settings.github_api_url)

    app = FastAPI(
        title="RepoScout",
        version=__version__,
        description="Ask questions about a codebase and review pull requests with AI.",
    )
    metrics = Metrics()
    seen_deliveries: OrderedDict[str, None] = OrderedDict()
    recent_reviews: deque[dict] = deque(maxlen=50)
    app.state.services = services
    app.state.recent_reviews = recent_reviews

    @app.middleware("http")
    async def track(request: Request, call_next):
        started = time.perf_counter()
        error = True
        try:
            response = await call_next(request)
            error = response.status_code >= 500
            return response
        finally:
            route = request.scope.get("route")
            name = getattr(route, "path", request.url.path)
            metrics.record(f"{request.method} {name}", (time.perf_counter() - started) * 1000, error)

    @app.exception_handler(RepoScoutError)
    async def handle_error(request: Request, exc: RepoScoutError):
        status = (
            409 if isinstance(exc, IndexMismatchError) else 502 if isinstance(exc, ProviderError) else 400
        )
        return JSONResponse({"error": str(exc)}, status_code=status)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> str:
        return resources.files("reposcout").joinpath("static/index.html").read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/stats")
    def stats() -> dict:
        return {
            "repo": str(settings.repo_path),
            "index": services.store.stats(),
            "llm": services.llm.name if services.llm else None,
            "llm_usage": services.llm.usage.as_dict() if services.llm else None,
            "github_enabled": github is not None,
            "requests": metrics.snapshot(),
        }

    @app.post("/index")
    def index(req: IndexRequest) -> dict:
        result = services.indexer().index(settings.repo_path, rebuild=req.rebuild)
        return {**result.__dict__, "summary": result.summary()}

    @app.post("/search")
    def search(req: SearchRequest) -> dict:
        results = services.assistant.search(req.query, req.k)
        return {
            "results": [
                {
                    "path": r.path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "kind": r.kind,
                    "name": r.name,
                    "score": round(r.score, 4),
                    "text": r.text,
                }
                for r in results
            ]
        }

    @app.post("/ask")
    def ask(req: AskRequest) -> dict:
        return services.assistant.ask(req.question, req.k).to_dict()

    @app.post("/review")
    def review(req: ReviewRequest) -> dict:
        result = services.reviewer.review(req.diff, req.title, req.description)
        result.findings = [f for f in result.findings if f.severity.rank >= req.min_severity.rank]
        return {**result.to_dict(), "markdown": result.to_markdown()}

    @app.get("/reviews")
    def reviews() -> dict:
        return {"reviews": list(recent_reviews)}

    def run_pr_review(ref: PullRequestRef, head_sha: str, title: str, body: str) -> None:
        record = {
            "pull_request": str(ref),
            "head_sha": head_sha[:12],
            "status": "running",
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        recent_reviews.appendleft(record)
        try:
            diff = github.get_pull_request_diff(ref)
            result = services.reviewer.review(diff, title, body or "")
            github.post_review(ref, head_sha, result)
            record.update(status="posted", headline=result.headline(), latency_ms=result.stats.latency_ms)
        except Exception as exc:  # background task: record the failure instead of crashing
            log.exception("Review of %s failed", ref)
            record.update(status="failed", error=str(exc)[:300])

    @app.post("/webhooks/github")
    async def github_webhook(request: Request, background: BackgroundTasks):
        if not settings.github_webhook_secret:
            raise HTTPException(503, "Webhook secret not configured (REPOSCOUT_WEBHOOK_SECRET).")
        raw = await request.body()
        if not verify_signature(
            settings.github_webhook_secret, raw, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(401, "Invalid signature")
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return {"status": "pong"}
        if event != "pull_request":
            return {"status": "ignored", "reason": f"event {event!r}"}
        if github is None:
            raise HTTPException(503, "GITHUB_TOKEN not configured; cannot fetch or comment on PRs.")

        payload = json.loads(raw)
        action = payload.get("action")
        pr = payload.get("pull_request") or {}
        if action not in REVIEW_ACTIONS or pr.get("draft"):
            return {"status": "ignored", "reason": f"action {action!r}"}

        repo = payload["repository"]
        ref = PullRequestRef(repo["owner"]["login"], repo["name"], int(pr["number"]))
        head_sha = pr["head"]["sha"]
        key = f"{ref}@{head_sha}"
        if key in seen_deliveries:  # GitHub retries deliveries; never review the same commit twice
            return {"status": "duplicate", "pull_request": str(ref)}
        seen_deliveries[key] = None
        while len(seen_deliveries) > 1000:
            seen_deliveries.popitem(last=False)

        background.add_task(run_pr_review, ref, head_sha, pr.get("title", ""), pr.get("body") or "")
        return JSONResponse({"status": "queued", "pull_request": str(ref)}, status_code=202)

    return app
