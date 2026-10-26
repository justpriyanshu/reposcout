"""Minimal GitHub REST client: fetch PR diffs and post reviews with inline comments."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .errors import RepoScoutError
from .review import ReviewResult, comment_body

log = logging.getLogger(__name__)

PR_REF_RE = re.compile(
    r"^(?:https?://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
    r"(?:#|/pulls?/)(?P<number>\d+)/?$"
)
MAX_INLINE_COMMENTS = 30


class GitHubError(RepoScoutError):
    pass


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pr_ref(value: str) -> PullRequestRef:
    """Accept ``owner/repo#12`` or ``https://github.com/owner/repo/pull/12``."""
    match = PR_REF_RE.match(value.strip())
    if not match:
        raise GitHubError(f"Not a pull request reference: {value!r} (use owner/repo#123 or a PR URL)")
    return PullRequestRef(match["owner"], match["repo"], int(match["number"]))


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check GitHub's ``X-Hub-Signature-256`` header (HMAC-SHA256 of the raw body)."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def review_comments(result: ReviewResult, limit: int = MAX_INLINE_COMMENTS) -> list[dict]:
    return [
        {"path": f.path, "line": f.line, "side": "RIGHT", "body": comment_body(f)}
        for f in result.findings[:limit]
    ]


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
        timeout: float = 30,
    ):
        if not token:
            raise GitHubError("A GitHub token is required (set GITHUB_TOKEN).")
        self.api_url = api_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "reposcout",
        }

    def _request(
        self, method: str, path: str, *, accept: str | None = None, json: dict | None = None
    ) -> httpx.Response:
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        try:
            resp = self.client.request(method, f"{self.api_url}{path}", headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        return resp

    def get_pull_request(self, pr: PullRequestRef) -> dict:
        resp = self._request("GET", f"/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}")
        if resp.status_code == 404:
            raise GitHubError(f"{pr} not found, or the token cannot see it.")
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub error {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def file_at(self, repo_full_name: str, sha: str, path: str, max_bytes: int = 400_000) -> str | None:
        """A file's text at a commit (for scanners that read whole files), or None."""
        resp = self._request(
            "GET",
            f"/repos/{repo_full_name}/contents/{quote(path)}?ref={quote(sha)}",
            accept="application/vnd.github.raw",
        )
        if resp.status_code >= 400 or len(resp.content) > max_bytes:
            return None
        return resp.content.decode("utf-8", "replace")

    def source_loader(self, pr: dict):
        """Loads changed files at the pull request's head commit (works for forks too)."""
        head = pr.get("head") or {}
        repo = (head.get("repo") or {}).get("full_name")
        sha = head.get("sha")
        if not repo or not sha:
            return None
        return lambda path: self.file_at(repo, sha, path)

    def get_pull_request_diff(self, pr: PullRequestRef) -> str:
        resp = self._request(
            "GET", f"/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}", accept="application/vnd.github.diff"
        )
        if resp.status_code >= 400:
            raise GitHubError(f"Could not fetch diff for {pr}: {resp.status_code} {resp.text[:300]}")
        return resp.text

    def whoami(self) -> str | None:
        """The login the token posts as, or None if GitHub won't say (e.g. the Actions token)."""
        try:
            resp = self._request("GET", "/user")
        except GitHubError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.json().get("login") or None

    def post_review(self, pr: PullRequestRef, commit_id: str, result: ReviewResult) -> dict:
        """Post one review with inline comments. Falls back to a summary-only review if
        GitHub rejects any comment position (HTTP 422)."""
        comments = review_comments(result)
        inline_paths = {(c["path"], c["line"]) for c in comments}
        leftover = [f for f in result.findings if (f.path, f.line) not in inline_paths]
        body = result.to_markdown(include_findings=False)
        if leftover:
            body += "\n**More findings**\n\n" + "\n".join(
                f"- `{f.path}:{f.line}` {f.title}" for f in leftover
            )
        payload = {"commit_id": commit_id, "event": "COMMENT", "body": body, "comments": comments}
        path = f"/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews"
        resp = self._request("POST", path, json=payload)
        if resp.status_code == 422 and comments:
            log.warning("GitHub rejected inline comments (%s); posting summary only", resp.text[:200])
            payload = {"commit_id": commit_id, "event": "COMMENT", "body": result.to_markdown()}
            resp = self._request("POST", path, json=payload)
        if resp.status_code >= 400:
            raise GitHubError(f"Could not post review on {pr}: {resp.status_code} {resp.text[:300]}")
        return resp.json()
