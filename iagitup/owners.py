"""List every repository of a user/organization on GitHub, GitLab or Gitea-family hosts.

Results are GitHub-shaped dicts (see ``iagitup.platforms``) so ``upload_ia``
and ``archive_watchlist.archive_repo`` can consume them unchanged.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlparse

import requests

from iagitup.iagitup import IagitupError, _github_headers
from iagitup.platforms import map_gitea_repo, map_gitlab_project

log = logging.getLogger(__name__)

_TIMEOUT = 30
_GITEA_PAGE = 50  # Gitea/Forgejo cap `limit` at 50 by default
_GITHUB_PAGE = 100
_GITLAB_PAGE = 100
_UNSUPPORTED = ("bitbucket.org",)
_GITEA_HOSTS = ("codeberg.org",)
_RATE_MSG = "GitHub API rate limit exhausted; set GITHUB_TOKEN to raise the limit."


def parse_owner_url(url_or_name: str) -> tuple[str, str] | None:
    """Return ``(hostname, owner_path)`` if the input names an owner, else None.

    A bare name (``torvalds``) means github.com.  A URL counts as an owner
    only with exactly one path segment: on GitLab a two-segment path may be
    either ``group/subgroup`` or ``owner/repo``, so subgroups are out of scope.
    """
    text = url_or_name.strip()
    if not text:
        return None
    if "://" not in text:
        return ("github.com", text) if "/" not in text else None
    parsed = urlparse(text)
    parts = [p for p in parsed.path.split("/") if p]
    if not parsed.hostname or len(parts) != 1:
        return None
    return parsed.hostname, parts[0]


def _get(url: str, headers: dict, params: dict) -> requests.Response:
    """GET with a timeout, converting transport errors to IagitupError."""
    try:
        return requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise IagitupError(f"Request to {url} failed: {exc}") from exc


def _paginate(url: str, headers: dict, params: dict, per_page: int,
              per_page_key: str, github: bool = False) -> list[dict] | None:
    """Fetch every page of a list endpoint; None when page 1 is a 404.

    Stops on a short page.  The 404-on-first-page signal lets callers fall
    back from the org/group endpoint to the user endpoint.
    """
    items: list[dict] = []
    page = 1
    while True:
        resp = _get(url, headers, {**params, per_page_key: per_page, "page": page})
        exhausted = github and resp.headers.get("X-RateLimit-Remaining") == "0"
        if resp.status_code == 404 and page == 1:
            return None
        if resp.status_code != 200:
            # A 403/429 with no quota left is a rate limit, not a permissions error.
            if exhausted:
                raise IagitupError(_RATE_MSG)
            raise IagitupError(f"API error {resp.status_code} for {url}: {resp.text[:200]}")
        batch = resp.json()
        items.extend(batch)
        if len(batch) < per_page:
            # Complete listing: spending the last request on it is fine.
            return items
        if exhausted:
            # More pages are needed but no quota is left; a partial owner
            # listing would silently skip repos, so fail loudly instead.
            raise IagitupError(_RATE_MSG)
        page += 1


def _github_repos(owner: str) -> list[dict]:
    headers = _github_headers()
    name = quote(owner, safe="")
    resp = _get(f"https://api.github.com/users/{name}", headers, {})
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        raise IagitupError(_RATE_MSG)
    if resp.status_code == 404:
        raise IagitupError(f"GitHub user/organization not found: {owner} (404)")
    if resp.status_code != 200:
        raise IagitupError(f"GitHub API error {resp.status_code} for {owner}: {resp.text[:200]}")
    # Organizations need /orgs for private/internal repos; users use /users.
    if resp.json().get("type") == "Organization":
        url, params = f"https://api.github.com/orgs/{name}/repos", {"type": "all"}
    else:
        url, params = f"https://api.github.com/users/{name}/repos", {"type": "owner"}
    items = _paginate(url, headers, params, _GITHUB_PAGE, "per_page", github=True) or []
    for item in items:
        item["_platform"] = "github.com"
    return items


def _gitlab_repos(owner: str, host: str) -> list[dict]:
    headers = {}
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        headers["PRIVATE-TOKEN"] = token
    base = f"https://{host}/api/v4"
    name = quote(owner, safe="")
    raw = _paginate(
        f"{base}/groups/{name}/projects", headers,
        {"include_subgroups": "true"}, _GITLAB_PAGE, "per_page",
    )
    if raw is None:
        raw = _paginate(f"{base}/users/{name}/projects", headers, {}, _GITLAB_PAGE, "per_page")
    if raw is None:
        raise IagitupError(f"Owner not found on {host}: {owner} (404)")
    out = []
    for p in raw:
        mapped = map_gitlab_project(p, host, owner)
        if mapped is None:
            # No last_activity_at (or malformed): same treatment as an empty repo.
            log.info(f"Skipping {p.get('path_with_namespace', '?')}: no activity date.")
            continue
        mapped.update(
            language=None,
            topics=p.get("topics") or p.get("tag_list") or [],
            fork="forked_from_project" in p,
            archived=bool(p.get("archived", False)),
        )
        out.append(mapped)
    return out


def _gitea_repos(owner: str, host: str) -> list[dict]:
    base = f"https://{host}/api/v1"
    name = quote(owner, safe="")
    raw = _paginate(f"{base}/orgs/{name}/repos", {}, {}, _GITEA_PAGE, "limit")
    if raw is None:
        raw = _paginate(f"{base}/users/{name}/repos", {}, {}, _GITEA_PAGE, "limit")
    if raw is None:
        raise IagitupError(f"Owner not found on {host}: {owner} (404)")
    out = []
    for r in raw:
        mapped = map_gitea_repo(r, host)
        if mapped is None:
            log.info(f"Skipping {r.get('full_name', '?')}: no update date.")
            continue
        mapped.update(
            language=r.get("language") or None,
            topics=r.get("topics") or [],
            fork=bool(r.get("fork", False)),
            archived=bool(r.get("archived", False)),
        )
        out.append(mapped)
    return out


def fetch_owner_repos(
    url_or_name: str,
    include_forks: bool = False,
    skip_archived: bool = False,
    max_repos: int | None = None,
) -> list[dict]:
    """Return all repos of a user/org as GitHub-shaped dicts, most recently pushed first.

    Order of operations matters: paginate fully, drop forks/archived, then
    truncate, so ``max_repos`` keeps the most recently active *wanted* repos.

    Raises:
        IagitupError: input is not an owner, host unsupported, owner not found,
            API error, or GitHub rate limit exhausted.
    """
    parsed = parse_owner_url(url_or_name)
    if parsed is None:
        raise IagitupError(f"Not a user/organization URL or name: {url_or_name}")
    host, owner = parsed

    if host == "github.com":
        repos = _github_repos(owner)
    elif host in _UNSUPPORTED:
        raise IagitupError(f"owner listing not supported for {host}")
    elif host == "gitlab.com":
        repos = _gitlab_repos(owner, host)
    elif host in _GITEA_HOSTS:
        repos = _gitea_repos(owner, host)
    else:
        # Self-hosted: probe Gitea/Forgejo first, then GitLab (as platforms.py does).
        try:
            repos = _gitea_repos(owner, host)
        except IagitupError as gitea_exc:
            try:
                repos = _gitlab_repos(owner, host)
            except IagitupError:
                raise IagitupError(
                    f"owner listing not supported for {host} ({gitea_exc})"
                ) from None

    kept = []
    for r in repos:
        if not r.get("pushed_at"):
            log.info(f"Skipping {r.get('full_name', '?')}: empty repository (no pushed_at).")
            continue
        if r.get("fork") and not include_forks:
            continue
        if r.get("archived") and skip_archived:
            continue
        kept.append(r)
    kept.sort(key=lambda r: r["pushed_at"], reverse=True)
    if max_repos is not None:
        kept = kept[:max_repos]
    log.info(f"Found {len(kept)} repositories for {owner} on {host}.")
    return kept
