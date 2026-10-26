"""Read-only GitHub API access for public repositories (a token is optional)."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ..errors import RepoScoutError
from .links import RepoId

MAX_DIFF_BYTES = 2_000_000


class GitHubAPIError(RepoScoutError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class PublicGitHub:
    def __init__(
        self,
        token: str = "",
        api_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
        timeout: float = 20,
        raw_url: str = "https://raw.githubusercontent.com",
    ):
        self.api_url = api_url.rstrip("/")
        self.raw_url = raw_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "reposcout",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, accept: str | None = None) -> httpx.Response:
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        try:
            resp = self.client.get(f"{self.api_url}{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubAPIError(f"Could not reach GitHub: {exc}") from exc
        self._raise_for(resp)
        return resp

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise GitHubAPIError("Not found on GitHub. Only public repositories are supported.", 404)
        if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubAPIError(
                "GitHub's hourly request limit for this server is used up. Try again later.", 429
            )
        if resp.status_code >= 400:
            raise GitHubAPIError(f"GitHub returned {resp.status_code}: {resp.text[:200]}", resp.status_code)

    def repo(self, repo: RepoId) -> dict:
        return self._get(f"/repos/{repo.owner}/{repo.name}").json()

    def pull(self, repo: RepoId, number: int) -> dict:
        return self._get(f"/repos/{repo.owner}/{repo.name}/pulls/{number}").json()

    def raw_file(self, repo_full_name: str, sha: str, path: str, max_bytes: int = 400_000) -> str | None:
        """A public file's text at a commit, from raw.githubusercontent.com (which doesn't
        count against the API's hourly limit). None if missing or too big."""
        url = f"{self.raw_url}/{quote(repo_full_name)}/{quote(sha)}/{quote(path)}"
        try:
            with self.client.stream("GET", url, headers={"User-Agent": "reposcout"}) as resp:
                if resp.status_code != 200:
                    return None
                body = bytearray()
                for piece in resp.iter_bytes():
                    body.extend(piece)
                    if len(body) > max_bytes:
                        return None
        except httpx.HTTPError:
            return None
        return body.decode("utf-8", "replace")

    def source_loader(self, pr: dict):
        head = pr.get("head") or {}
        repo = (head.get("repo") or {}).get("full_name")
        sha = head.get("sha")
        if not repo or not sha:
            return None
        return lambda path: self.raw_file(repo, sha, path)

    def pull_diff(self, repo: RepoId, number: int, max_bytes: int = MAX_DIFF_BYTES) -> str:
        """The pull request as a unified diff; refuses diffs over ``max_bytes``."""
        headers = {**self.headers, "Accept": "application/vnd.github.diff"}
        url = f"{self.api_url}/repos/{repo.owner}/{repo.name}/pulls/{number}"
        try:
            with self.client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for(resp)
                body = bytearray()
                for piece in resp.iter_bytes():
                    body.extend(piece)
                    if len(body) > max_bytes:
                        raise GitHubAPIError(
                            f"This pull request's diff is larger than {max_bytes // 1_000_000} MB, "
                            "too big to review here.",
                            413,
                        )
        except httpx.HTTPError as exc:
            raise GitHubAPIError(f"Could not reach GitHub: {exc}") from exc
        return body.decode("utf-8", "replace")
