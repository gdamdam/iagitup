"""Platform API metadata for non-GitHub hosts (GitLab, Codeberg/Gitea/Forgejo).

``fetch_platform_metadata`` returns a dict with the same shape as the GitHub
API response used throughout iagitup (``full_name``, ``html_url``,
``clone_url``, ``pushed_at`` as ``YYYY-MM-DDTHH:MM:SSZ``, ``description``,
``owner.login/html_url/avatar_url``, ``has_wiki``, ``_platform``), or
``None`` when the host is unknown or its API is unreachable so callers fall
back to clone-derived metadata.

This module must not import ``iagitup.iagitup`` (which imports it).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger(__name__)

_TIMEOUT = 10


def _parse(repo_url: str) -> tuple[str, str, str] | None:
    """Return (owner, repo, host) from *repo_url*, or None if it has no owner/repo."""
    parsed = urlparse(repo_url.rstrip("/"))
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parsed.hostname or len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git"), parsed.hostname


def _normalise_ts(value: str | None) -> str | None:
    """Convert an ISO-8601 timestamp to ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(url: str, headers: dict | None = None) -> dict | None:
    """GET *url* and return the JSON object on HTTP 200, else None (logged)."""
    try:
        resp = requests.get(url, headers=headers or {}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        log.info(f"Platform API request failed for {url}: {exc}")
        return None
    if resp.status_code != 200:
        log.info(f"Platform API returned {resp.status_code} for {url}")
        return None
    try:
        data = resp.json()
    except ValueError:
        log.info(f"Platform API returned malformed JSON for {url}")
        return None
    if not isinstance(data, dict):
        log.info(f"Platform API returned unexpected JSON for {url}")
        return None
    return data


def _fetch_gitlab(owner: str, repo: str, host: str) -> dict | None:
    """Fetch and map GitLab ``/api/v4/projects/:id`` metadata."""
    # GitLab addresses projects by URL-encoded "namespace/path"; safe="" so
    # the slash is encoded too.
    url = f"https://{host}/api/v4/projects/{quote(f'{owner}/{repo}', safe='')}"
    headers = {}
    # A token lets private/internal projects and rate-limited hosts work.
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        headers["PRIVATE-TOKEN"] = token
    data = _get_json(url, headers)
    if data is None:
        return None
    return map_gitlab_project(data, host, owner, url)


def map_gitlab_project(data: dict, host: str, owner: str, url: str = "") -> dict | None:
    """Map a GitLab project JSON object to the GitHub-shaped dict, or None.

    Shared by single-repo lookup and owner listing so both produce identical
    shapes.  *url* is only used in the log message on a malformed response.
    """
    try:
        ns = data.get("namespace") or {}
        avatar = data.get("avatar_url") or ns.get("avatar_url")
        if avatar and avatar.startswith("/"):
            # Self-hosted GitLab may return a host-relative path.
            avatar = f"https://{host}{avatar}"
        pushed_at = _normalise_ts(data.get("last_activity_at"))
        if not pushed_at:
            return None
        return {
            "full_name": data["path_with_namespace"],
            "html_url": data["web_url"],
            "clone_url": data["http_url_to_repo"],
            "pushed_at": pushed_at,
            "description": data.get("description") or "",
            "owner": {
                "login": ns.get("path") or owner,
                "html_url": ns.get("web_url") or f"https://{host}/{owner}",
                "avatar_url": avatar or None,
            },
            "has_wiki": bool(data.get("wiki_enabled", False)),
            "stargazers_count": data.get("star_count", 0),
            "forks_count": data.get("forks_count", 0),
            "_platform": host,
        }
    except (KeyError, TypeError, AttributeError) as exc:
        log.info(f"Unexpected GitLab response shape for {url}: {exc!r}")
        return None


def _fetch_gitea(owner: str, repo: str, host: str) -> dict | None:
    """Fetch and map Gitea/Forgejo/Codeberg ``/api/v1/repos/:owner/:repo``."""
    url = f"https://{host}/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    data = _get_json(url)
    if data is None:
        return None
    return map_gitea_repo(data, host, url)


def map_gitea_repo(data: dict, host: str, url: str = "") -> dict | None:
    """Map a Gitea/Forgejo repo JSON object to the GitHub-shaped dict, or None.

    Shared by single-repo lookup and owner listing.
    """
    try:
        own = data["owner"]
        pushed_at = _normalise_ts(data.get("updated_at"))
        if not pushed_at:
            return None
        login = own["login"]
        return {
            "full_name": data["full_name"],
            "html_url": data["html_url"],
            "clone_url": data["clone_url"],
            "pushed_at": pushed_at,
            "description": data.get("description") or "",
            "owner": {
                "login": login,
                "html_url": f"https://{host}/{login}",
                "avatar_url": own.get("avatar_url") or None,
            },
            "has_wiki": bool(data.get("has_wiki", False)),
            "stargazers_count": data.get("stars_count", 0),
            "forks_count": data.get("forks_count", 0),
            "_platform": host,
        }
    except (KeyError, TypeError, AttributeError) as exc:
        log.info(f"Unexpected Gitea response shape for {url}: {exc!r}")
        return None


def fetch_platform_metadata(repo_url: str) -> dict | None:
    """Return API metadata for *repo_url* on a supported non-GitHub host, else ``None``.

    gitlab.com and codeberg.org are routed directly; unknown (self-hosted)
    hosts are probed as Gitea/Forgejo first, then GitLab.  Any failure yields
    ``None`` so the caller falls back to metadata derived from the clone.
    GitHub and Bitbucket are intentionally unsupported here.
    """
    parsed = _parse(repo_url)
    if parsed is None:
        return None
    owner, repo, host = parsed
    if host in ("github.com", "bitbucket.org"):
        return None
    if host == "gitlab.com":
        return _fetch_gitlab(owner, repo, host)
    if host == "codeberg.org":
        return _fetch_gitea(owner, repo, host)
    return _fetch_gitea(owner, repo, host) or _fetch_gitlab(owner, repo, host)
