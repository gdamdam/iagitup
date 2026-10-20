"""Export extra GitHub metadata (releases, assets, issues, PRs) for archiving."""

import json
import logging
from pathlib import Path

import requests

from iagitup.iagitup import _github_headers

log = logging.getLogger(__name__)

API = "https://api.github.com"
PER_PAGE = 100


def _paginate(url: str, params: dict | None = None) -> list:
    """Collect every page of a GitHub list endpoint.

    Never raises: on a non-200 response, network error or exhausted rate
    limit it logs a warning and returns whatever was gathered so far, so a
    partial export is still better than aborting the whole archive run.
    """
    items: list = []
    page = 1
    while True:
        query = {**(params or {}), "per_page": PER_PAGE, "page": page}
        try:
            resp = requests.get(url, headers=_github_headers(), params=query, timeout=30)
        except requests.RequestException as exc:
            log.warning(f"GitHub request failed for {url}: {exc}")
            return items
        if resp.status_code != 200:
            log.warning(f"GitHub returned {resp.status_code} for {url}")
            return items
        batch = resp.json()
        items.extend(batch)
        # Fewer than a full page means we have reached the last one.
        if len(batch) < PER_PAGE:
            return items
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            log.warning(
                "GitHub rate limit reached; export is incomplete. "
                "Set GITHUB_TOKEN for a higher limit."
            )
            return items
        page += 1


def _write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _download(url: str, target: Path) -> bool:
    """Stream *url* to *target*; return False (and clean up) on failure."""
    try:
        with requests.get(url, headers=_github_headers(), stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                log.warning(f"Asset download returned {resp.status_code}: {url}")
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    except requests.RequestException as exc:
        log.warning(f"Asset download failed for {url}: {exc}")
        target.unlink(missing_ok=True)
        return False
    return True


def fetch_releases(
    repo_data: dict, dest_dir: Path, max_asset_mb: int | None = None
) -> list[Path]:
    """Save release metadata and assets under ``dest_dir/releases``.

    Args:
        repo_data: GitHub repos API dict (needs ``full_name``).
        dest_dir: Base directory; files are written relative to it.
        max_asset_mb: Skip assets larger than this many MiB (None = no limit).

    Returns:
        Paths of every file written (empty if the repo has no releases).
    """
    releases = _paginate(f"{API}/repos/{repo_data['full_name']}/releases")
    if not releases:
        return []
    root = Path(dest_dir) / "releases"
    written = [_write_json(root / "releases.json", releases)]
    for release in releases:
        # Path().name strips any directory parts so API data cannot escape root.
        tag = Path(str(release.get("tag_name", ""))).name
        for asset in release.get("assets", []):
            name = Path(str(asset.get("name", ""))).name
            if not tag or not name:
                continue
            size = asset.get("size", 0)
            if max_asset_mb is not None and size > max_asset_mb * 1024 * 1024:
                log.warning(f"Skipping {tag}/{name}: {size} bytes exceeds {max_asset_mb} MB")
                continue
            target = root / tag / name
            if _download(asset["browser_download_url"], target):
                written.append(target)
    return written


def fetch_issues(repo_data: dict, dest_dir: Path) -> list[Path]:
    """Save issues and pull requests (with comments) as JSON in ``dest_dir``.

    The issues endpoint also returns PRs (flagged by ``pull_request``), so
    they are split into ``issues.json`` and ``pulls.json``. Comments are
    fetched in bulk (two paginated calls) rather than per item to save rate
    limit, then attached by matching their parent URL.

    Returns:
        Paths of the JSON files written (a file is skipped when empty).
    """
    base = f"{API}/repos/{repo_data['full_name']}"
    items = _paginate(f"{base}/issues", {"state": "all"})
    issues = [i for i in items if "pull_request" not in i]
    pulls = [i for i in items if "pull_request" in i]

    for i in items:
        i["comments"] = []
    by_url = {i["url"]: i for i in items}
    comments = _paginate(f"{base}/issues/comments") + _paginate(f"{base}/pulls/comments")
    for c in comments:
        parent = by_url.get(c.get("issue_url") or c.get("pull_request_url"))
        if parent is not None:
            parent["comments"].append(c)

    written = []
    for name, data in (("issues.json", issues), ("pulls.json", pulls)):
        if data:
            written.append(_write_json(Path(dest_dir) / name, data))
    return written
