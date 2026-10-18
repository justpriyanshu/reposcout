"""Parse GitHub links typed by visitors, and build links back to GitHub."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..errors import RepoScoutError

_OWNER = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
_NAME = r"(?P<name>[A-Za-z0-9._-]{1,100})"
_URL_RE = re.compile(
    rf"^(?:https?://)?(?:www\.)?github\.com/{_OWNER}/{_NAME}(?P<rest>/.*)?$",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(rf"^{_OWNER}/{_NAME}(?:#(?P<number>\d{{1,7}}))?$")
_PULL_RE = re.compile(r"^/pulls?/(?P<number>\d{1,7})(?:/.*)?$")


class LinkError(RepoScoutError):
    pass


@dataclass(frozen=True)
class RepoId:
    owner: str
    name: str

    @property
    def key(self) -> str:
        """Filesystem- and URL-safe identity; GitHub names are case-insensitive."""
        return f"{self.owner.lower()}__{self.name.lower()}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True)
class GitHubLink:
    repo: RepoId
    pull: int | None = None

    @property
    def kind(self) -> str:
        return "pull" if self.pull else "repo"


def parse_github_link(text: str) -> GitHubLink:
    """Accept repo or pull request links in the forms people actually paste:

    ``https://github.com/o/r``, ``github.com/o/r.git``, ``.../tree/main/src``,
    ``.../pull/12``, ``.../pull/12/files``, ``o/r`` and ``o/r#12``."""
    value = (text or "").strip().rstrip("/")
    if not value:
        raise LinkError("Paste a GitHub repository or pull request link.")
    match = _URL_RE.match(value)
    number = None
    if match:
        rest = match.group("rest") or ""
        pull = _PULL_RE.match(rest)
        if pull:
            number = int(pull.group("number"))
    else:
        match = _SHORT_RE.match(value)
        if not match:
            raise LinkError(
                "That doesn't look like a GitHub link. Try https://github.com/owner/repo "
                "or a pull request link."
            )
        if match.group("number"):
            number = int(match.group("number"))
    name = match.group("name")
    if name.lower().endswith(".git"):
        name = name[:-4]
    if name in ("", ".", "..") or name.startswith("."):
        raise LinkError("That repository name isn't valid.")
    return GitHubLink(RepoId(match.group("owner"), name), number)


def blob_url(repo: RepoId, commit: str, path: str, start: int, end: int) -> str:
    anchor = f"#L{start}" if start == end else f"#L{start}-L{end}"
    return f"{repo.html_url}/blob/{commit or 'HEAD'}/{path}{anchor}"


def pr_line_url(repo: RepoId, number: int, path: str, line: int) -> str:
    """GitHub's 'Files changed' tab anchors each file by the SHA-256 of its path."""
    digest = hashlib.sha256(path.encode()).hexdigest()
    return f"{repo.html_url}/pull/{number}/files#diff-{digest}R{line}"
