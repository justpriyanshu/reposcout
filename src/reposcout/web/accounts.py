"""People: optional GitHub sign-in, and each person's private list of repositories.

Everyone gets an *owner key*. Signed-in people are ``gh:<GitHub user id>``; anonymous
visitors are ``anon:<random id>`` from an HttpOnly cookie, so they also get a private list
without an account. Signing in moves the browser's anonymous list into the account.

Indexes stay shared (one per public repository, see workspace.py); what is per person is
the *list* that says which repositories you added. The API only shows a repository, its
status, search and answers to people who have it in their list, so nobody can see or probe
what others explored.

Sign-in is GitHub's OAuth web flow with ``state`` and PKCE and no scopes (public profile
only). The GitHub access token is used once to read who signed in and then discarded:
nothing that can act on anyone's GitHub account is stored. Sessions are random tokens in
an HttpOnly cookie; only their SHA-256 is stored.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..errors import RepoScoutError

SESSION_COOKIE = "rs_session"
VISITOR_COOKIE = "rs_visitor"
OAUTH_COOKIE = "rs_oauth"
OAUTH_COOKIE_SECONDS = 600
VISITOR_COOKIE_DAYS = 365

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    login TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS user_repos (
    owner_key TEXT NOT NULL,
    repo_key TEXT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at REAL NOT NULL,
    last_used REAL NOT NULL,
    PRIMARY KEY (owner_key, repo_key)
);
CREATE INDEX IF NOT EXISTS idx_user_repos_recent ON user_repos(owner_key, last_used);
"""
PG_SCHEMA = SQLITE_SCHEMA.replace(" REAL ", " DOUBLE PRECISION ")

MAX_LIST = 50  # repositories kept per person (the oldest drop off the list, not the index)


class AuthError(RepoScoutError):
    """Sign-in with GitHub failed; the message is safe to show."""


@dataclass(frozen=True)
class Owner:
    key: str  # gh:<id> or anon:<random>
    user: dict | None = None  # {"login", "name", "avatar_url", "html_url"} when signed in

    @property
    def signed_in(self) -> bool:
        return self.user is not None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_visitor_id() -> str:
    return secrets.token_urlsafe(18)


class Accounts:
    """Users, sessions and per-person repository lists, stored next to the workspace
    registry (``workspace.db`` or the Postgres database)."""

    def __init__(self, db, lock, postgres: bool, session_days: int = 30):
        self._db = db
        self._lock = lock
        self.session_seconds = session_days * 86400
        with self._lock, self._db:
            if postgres:
                for statement in (s.strip() for s in PG_SCHEMA.split(";")):
                    if statement:
                        self._db.execute(statement)
            else:
                self._db.executescript(SQLITE_SCHEMA)

    # ------------------------------------------------------------------ sessions

    def create_session(self, user: dict) -> str:
        """Store/refresh the GitHub user and start a session. Returns the cookie token."""
        now = time.time()
        token = secrets.token_urlsafe(32)
        uid = f"gh:{int(user['id'])}"
        with self._lock, self._db:
            if self._db.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
                self._db.execute(
                    "UPDATE users SET login = ?, name = ?, avatar_url = ?, last_seen = ? WHERE id = ?",
                    (
                        str(user["login"]),
                        str(user.get("name") or ""),
                        str(user.get("avatar_url") or ""),
                        now,
                        uid,
                    ),
                )
            else:
                self._db.execute(
                    "INSERT INTO users (id, login, name, avatar_url, created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        uid,
                        str(user["login"]),
                        str(user.get("name") or ""),
                        str(user.get("avatar_url") or ""),
                        now,
                        now,
                    ),
                )
            self._db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self._db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash(token), uid, now, now + self.session_seconds),
            )
        return token

    def session_user(self, token: str | None) -> tuple[str, dict] | None:
        """(owner key, public profile) for a valid session token, else None."""
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT u.id, u.login, u.name, u.avatar_url, s.expires_at FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (_hash(token),),
            ).fetchone()
        if row is None or row["expires_at"] < time.time():
            return None
        profile = {
            "login": row["login"],
            "name": row["name"],
            "avatar_url": row["avatar_url"],
            "html_url": f"https://github.com/{row['login']}",
        }
        return row["id"], profile

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self._db:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))

    # ------------------------------------------------------------------ lists

    def link(self, owner_key: str, repo_key: str, owner: str, name: str) -> None:
        """Put a repository at the top of this person's list."""
        now = time.time()
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM user_repos WHERE owner_key = ? AND repo_key = ?", (owner_key, repo_key)
            ).fetchone():
                self._db.execute(
                    "UPDATE user_repos SET last_used = ?, owner = ?, name = ? WHERE owner_key = ? AND repo_key = ?",
                    (now, owner, name, owner_key, repo_key),
                )
            else:
                self._db.execute(
                    "INSERT INTO user_repos (owner_key, repo_key, owner, name, added_at, last_used) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (owner_key, repo_key, owner, name, now, now),
                )
            rows = self._db.execute(
                "SELECT repo_key FROM user_repos WHERE owner_key = ? ORDER BY last_used DESC", (owner_key,)
            ).fetchall()
            for row in rows[MAX_LIST:]:
                self._db.execute(
                    "DELETE FROM user_repos WHERE owner_key = ? AND repo_key = ?",
                    (owner_key, row["repo_key"]),
                )

    def unlink(self, owner_key: str, repo_key: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM user_repos WHERE owner_key = ? AND repo_key = ?", (owner_key, repo_key)
            )

    def has(self, owner_key: str, repo_key: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM user_repos WHERE owner_key = ? AND repo_key = ?", (owner_key, repo_key)
            ).fetchone()
        return row is not None

    def touch(self, owner_key: str, repo_key: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE user_repos SET last_used = ? WHERE owner_key = ? AND repo_key = ?",
                (time.time(), owner_key, repo_key),
            )

    def entries(self, owner_key: str, limit: int = 24) -> list[dict]:
        """This person's list, most recently used first: repo_key, owner, name."""
        with self._lock:
            rows = self._db.execute(
                "SELECT repo_key, owner, name FROM user_repos WHERE owner_key = ? ORDER BY last_used DESC LIMIT ?",
                (owner_key, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def merge(self, from_key: str, into_key: str) -> None:
        """Move an anonymous browser's list into a signed-in account."""
        if from_key == into_key:
            return
        for entry in reversed(self.entries(from_key, MAX_LIST)):  # oldest first keeps the order
            self.link(into_key, entry["repo_key"], entry["owner"], entry["name"])
        with self._lock, self._db:
            self._db.execute("DELETE FROM user_repos WHERE owner_key = ?", (from_key,))


# --------------------------------------------------------------------------- GitHub OAuth


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class GitHubOAuth:
    """GitHub's OAuth web application flow for an OAuth App (no scopes: public data only)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: httpx.Client,
        web_url: str = "https://github.com",
        api_url: str = "https://api.github.com",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http
        self.web_url = web_url.rstrip("/")
        self.api_url = api_url.rstrip("/")

    def authorize_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        query = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "",
            "allow_signup": "true",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.web_url}/login/oauth/authorize?{urlencode(query)}"

    def user_for_code(self, code: str, verifier: str, redirect_uri: str) -> dict:
        """Exchange the callback's code for a token, read the profile, forget the token."""
        try:
            resp = self.http.post(
                f"{self.web_url}/login/oauth/access_token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AuthError("Couldn't reach GitHub to finish signing in. Please try again.") from exc
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        token = data.get("access_token")
        if resp.status_code >= 400 or not token:
            raise AuthError("GitHub didn't accept the sign-in. Please try again.")
        try:
            user = self.http.get(
                f"{self.api_url}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "reposcout",
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError("Couldn't read your GitHub profile. Please try again.") from exc
        if user.status_code >= 400:
            raise AuthError("Couldn't read your GitHub profile. Please try again.")
        profile = user.json()
        if not profile.get("id") or not profile.get("login"):
            raise AuthError("GitHub returned an unexpected profile.")
        return profile


def safe_next(value: str | None) -> str:
    """Only same-site paths are allowed after sign-in (no open redirects)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ch in value for ch in "\r\n"):
        return "/"
    return value[:500]
