#!/usr/bin/env python3
"""archive_watchlist.py — Archive the top-N most-starred GitHub repos to the Internet Archive.

Designed to be run as a single-shot script (e.g. via cron). On each run it:
  1. Fetches the top-N repos from the GitHub Search API (sorted by stars).
  2. Compares each repo's pushed_at timestamp against a local state cache.
  3. Skips repos that haven't changed since the last run.
  4. Archives new or updated repos using iagitup, injecting rich popularity metadata.
  5. Saves the updated state so the next run knows what was already archived.

Usage:
    python archive_watchlist.py               # archive top 100
    python archive_watchlist.py --top-n 10    # quick test
    python archive_watchlist.py --dry-run     # preview without uploading
    python archive_watchlist.py --state-file /path/to/state.json

Cron example (daily at 03:00):
    0 3 * * * cd /path/to/iagitup && python archive_watchlist.py >> watchlist.log 2>&1
"""

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from iagitup.iagitup import (
    IagitupError,
    _github_headers,
    get_ia_credentials,
    repo_download,
    upload_ia,
)

log = logging.getLogger(__name__)

DEFAULT_STATE_FILE = Path("watchlist_state.json")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    """Load the watchlist state from disk, returning an empty dict if absent."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(f"Could not read state file {path}: {exc} — starting fresh.")
    return {}


def save_state(path: Path, state: dict) -> None:
    """Persist the watchlist state to disk atomically."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def fetch_top_repos(n: int) -> list[dict]:
    """Fetch the top-N most-starred GitHub repositories.

    Args:
        n: Number of repositories to fetch (max 100 per GitHub Search API page).

    Returns:
        List of repository dicts from the GitHub API.

    Raises:
        SystemExit: On API error or rate-limit exhaustion.
    """
    per_page = min(n, 100)
    params = {
        "q": "stars:>1",
        "sort": "stars",
        "order": "desc",
        "per_page": per_page,
    }
    resp = requests.get(
        GITHUB_SEARCH_URL,
        headers=_github_headers(),
        params=params,
        timeout=30,
    )

    remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
    if remaining <= 5:
        log.warning(
            f"GitHub API rate limit almost exhausted ({remaining} requests left). "
            "Set GITHUB_TOKEN to increase the limit."
        )

    if resp.status_code != 200:
        log.error(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    repos = resp.json().get("items", [])
    log.info(f"Fetched {len(repos)} repositories from GitHub (rate limit remaining: {remaining}).")
    return repos


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def build_custom_meta(repo_data: dict, rank: int) -> dict:
    """Build extra IA metadata from GitHub popularity fields.

    Args:
        repo_data: Raw GitHub API repository dict.
        rank: 1-based position in the top-N list.

    Returns:
        Dict of additional metadata fields to merge into the IA item.
    """
    topics = repo_data.get("topics") or []
    language = repo_data.get("language") or ""

    # Extend the default subject with language and topics
    subject_parts = ["GitHub", "code", "software", "git"]
    if language:
        subject_parts.append(language)
    subject_parts.extend(topics)

    return {
        "stars_count": str(repo_data.get("stargazers_count", "")),
        "forks_count": str(repo_data.get("forks_count", "")),
        "watchers_count": str(repo_data.get("watchers_count", "")),
        "language": language,
        "topics": ";".join(topics),
        "github_rank": str(rank),
        "subject": ";".join(subject_parts),
    }


# ---------------------------------------------------------------------------
# Archive logic
# ---------------------------------------------------------------------------

def archive_repo(
    repo_data: dict,
    rank: int,
    s3_access: str,
    s3_secret: str,
    state: dict,
    dry_run: bool,
) -> str:
    """Download and archive a single repository, updating the state on success.

    Args:
        repo_data: Raw GitHub API repository dict.
        rank: 1-based position in the top-N list.
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        state: Mutable state dict (updated in-place on success).
        dry_run: If True, log what would happen without uploading.

    Returns:
        "skipped", "archived", or "failed".
    """
    full_name = repo_data["full_name"]
    pushed_at = repo_data["pushed_at"]
    cached = state.get(full_name, {})

    if cached.get("pushed_at") == pushed_at:
        log.info(f"  SKIP    [{rank:>3}] {full_name} — no new commits since last run.")
        return "skipped"

    stars = repo_data.get("stargazers_count", 0)
    reason = "new" if full_name not in state else "updated"
    prefix = "DRY-RUN" if dry_run else "ARCHIVE"
    log.info(f"  {prefix} [{rank:>3}] {full_name} ({stars:,} ★) [{reason}]")

    if dry_run:
        return "archived"

    repo_folder: Path | None = None
    try:
        _, repo_folder = repo_download(repo_data["html_url"])
        custom_meta = build_custom_meta(repo_data, rank)
        identifier, _, _ = upload_ia(
            repo_folder,
            repo_data,
            s3_access=s3_access,
            s3_secret=s3_secret,
            custom_meta=custom_meta,
        )
        log.info(f"           → https://archive.org/details/{identifier}")

        state[full_name] = {
            "pushed_at": pushed_at,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "ia_identifier": identifier,
            "stars": stars,
        }
        return "archived"

    except IagitupError as exc:
        log.error(f"           FAILED: {exc}")
        return "failed"
    finally:
        if repo_folder and repo_folder.exists():
            shutil.rmtree(repo_folder, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Archive the top-N most-starred GitHub repos to the Internet Archive."
    )
    parser.add_argument(
        "--top-n", type=int, default=100,
        help="Number of top repositories to check (default: 100, max: 100).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be archived without uploading anything.",
    )
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE,
        help=f"Path to the state cache file (default: {DEFAULT_STATE_FILE}).",
    )
    args = parser.parse_args()

    if args.top_n > 100:
        parser.error("--top-n cannot exceed 100 (GitHub Search API limit per page).")

    if args.dry_run:
        log.info("DRY-RUN mode — nothing will be uploaded.")

    # Credentials (skip in dry-run since we won't upload)
    s3_access = s3_secret = ""
    if not args.dry_run:
        try:
            s3_access, s3_secret = get_ia_credentials()
        except IagitupError as exc:
            log.error(f"Credentials error: {exc}")
            sys.exit(1)

    state = load_state(args.state_file)
    repos = fetch_top_repos(args.top_n)

    counts: dict[str, int] = {"archived": 0, "skipped": 0, "failed": 0}
    for rank, repo_data in enumerate(repos, start=1):
        result = archive_repo(
            repo_data, rank, s3_access, s3_secret, state, args.dry_run
        )
        counts[result] += 1

        # Save state after every archive attempt so a crash mid-run doesn't lose progress
        if not args.dry_run:
            save_state(args.state_file, state)

    verb = "would archive" if args.dry_run else "archived"
    log.info(
        f"\n:: Run complete — "
        f"{verb}: {counts['archived']} | "
        f"skipped: {counts['skipped']} | "
        f"failed: {counts['failed']} | "
        f"total checked: {len(repos)}"
    )


if __name__ == "__main__":
    main()
