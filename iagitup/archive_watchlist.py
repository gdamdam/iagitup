#!/usr/bin/env python3
"""archive_watchlist.py — Archive the top-N most-starred GitHub repos to the Internet Archive.

Designed to be run as a single-shot script (e.g. via cron). On each run it:
  1. Fetches the top-N repos from the GitHub Search API (sorted by stars).
  2. Compares each repo's pushed_at timestamp against a local state cache.
  3. Skips repos that haven't changed since the last run.
  4. Archives new or updated repos using iagitup, injecting rich popularity metadata.
  5. Saves the updated state so the next run knows what was already archived.

Repos are archived in parallel using a configurable worker pool (--workers).

Usage:
    python archive_watchlist.py                  # archive top 100, 4 workers
    python archive_watchlist.py --top-n 10       # quick test
    python archive_watchlist.py --workers 8      # more parallelism
    python archive_watchlist.py --dry-run        # preview without uploading
    python archive_watchlist.py --state-file /path/to/state.json

Cron example (daily at 03:00):
    0 3 * * * cd /path/to/iagitup && python archive_watchlist.py >> watchlist.log 2>&1
"""

import argparse
import json
import logging
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Relative path; resolves to cwd so the state file lives alongside the script
# when launched from the project root (typical cron setup).
DEFAULT_STATE_FILE = Path("watchlist_state.json")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    """Load the watchlist state cache from disk.

    The state maps each ``owner/repo`` full name to a dict containing the
    last-seen ``pushed_at`` timestamp, the IA identifier, and other metadata.
    Returns an empty dict (and logs a warning) if the file is absent or corrupt.

    Args:
        path: Path to the JSON state file.

    Returns:
        State dict, or ``{}`` on any read/parse error.
    """
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(f"Could not read state file {path}: {exc} — starting fresh.")
    return {}


def save_state(path: Path, state: dict) -> None:
    """Persist the watchlist state to disk atomically via a tmp-then-rename write.

    Writing to a temporary file and renaming ensures the state file is never
    left in a partially-written state if the process is interrupted.

    Args:
        path: Destination path for the JSON state file.
        state: Current state dict to serialise.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def fetch_top_repos(n: int) -> list[dict]:
    """Fetch the top-N most-starred GitHub repositories via the Search API.

    Uses the GITHUB_TOKEN environment variable (via ``_github_headers``) when
    available to raise the rate limit from 60 to 5 000 requests/hour.

    Args:
        n: Number of repositories to fetch. Capped at 100 (API page limit).

    Returns:
        List of raw repository dicts from the GitHub API.

    Raises:
        SystemExit: If the API returns a non-200 status code.
    """
    # GitHub Search API returns at most 100 results per page; we don't
    # paginate because the top-100-by-stars use case rarely needs more.
    per_page = min(n, 100)
    params = {
        # "stars:>1" is a minimal filter that effectively means "all repos
        # with at least 2 stars", combined with sort=stars to get the top ones.
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

    # Check status before reading rate-limit headers: on error responses
    # these headers may be absent, and the fallback 9999 would be misleading.
    if resp.status_code != 200:
        log.error(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
    if remaining <= 5:
        log.warning(
            f"GitHub API rate limit almost exhausted ({remaining} requests left). "
            "Set GITHUB_TOKEN to increase the limit."
        )

    repos = resp.json().get("items", [])
    log.info(f"Fetched {len(repos)} repositories from GitHub (rate limit remaining: {remaining}).")
    return repos


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def build_custom_meta(repo_data: dict, rank: int) -> dict:
    """Build the extra IA metadata fields derived from GitHub popularity data.

    These fields are merged (via ``custom_meta``) into the default iagitup
    metadata, enriching each archived item with current popularity signals.

    Args:
        repo_data: Raw repository dict from the GitHub API.
        rank: 1-based position in the top-N list at the time of archiving.

    Returns:
        Dict of additional IA metadata fields.
    """
    # GitHub returns None (not missing key) when no topics/language are set.
    topics = repo_data.get("topics") or []
    language = repo_data.get("language") or ""

    # Extend the default iagitup subject tags with the primary language and
    # user-defined topics so the IA item is more discoverable in search.
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

    Skip logic (two layers):
    - Local: if ``pushed_at`` matches the cached value the repo is unchanged
      and we return immediately without touching the network or IA.
    - Remote: ``upload_ia`` also checks ``item.exists`` on IA, so even if the
      local state is lost, a duplicate upload is still prevented.

    The temporary directory created by ``repo_download`` is always cleaned up
    in the finally block, including any wiki subdir.

    Args:
        repo_data: Raw repository dict from the GitHub API.
        rank: 1-based position in the top-N list.
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        state: Shared state dict (updated in-place on successful archive).
        dry_run: When True, log what would happen without uploading anything.

    Returns:
        ``"skipped"``, ``"archived"``, or ``"failed"``.
    """
    full_name = repo_data["full_name"]
    pushed_at = repo_data["pushed_at"]
    cached = state.get(full_name, {})

    # Local skip: repo hasn't been pushed to since last run.
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

        # Record the successful archive so future runs can skip this snapshot.
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
        # Clean the entire mkdtemp root (includes repo + wiki/ subdir).
        if repo_folder is not None:
            shutil.rmtree(repo_folder.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, fetch the top repos, and archive them in parallel."""
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
        "--workers", type=int, default=4,
        help="Number of parallel archive workers (default: 4).",
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

    # Credentials are not needed in dry-run mode; skip the interactive
    # prompt that get_ia_credentials() might trigger if no config exists.
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
    # save_lock guards concurrent writes to the state file on disk.
    # The in-memory state dict doesn't need a lock because each thread
    # writes to a unique key (the repo's full_name).
    # counts_lock guards the shared counters dict.
    save_lock = threading.Lock()
    counts_lock = threading.Lock()

    def run(rank: int, repo_data: dict) -> str:
        """Worker: archive one repo and persist state on success."""
        result = archive_repo(repo_data, rank, s3_access, s3_secret, state, args.dry_run)
        with counts_lock:
            counts[result] += 1
        # Persist state only when something changed (not on skips) to reduce
        # unnecessary disk I/O; also ensures progress survives a mid-run crash.
        if not args.dry_run and result != "skipped":
            with save_lock:
                save_state(args.state_file, state)
        return result

    log.info(f"Starting archival with {args.workers} worker(s) ...")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run, rank, repo_data): repo_data["full_name"]
            for rank, repo_data in enumerate(repos, start=1)
        }
        # as_completed yields futures in the order they finish, not submission
        # order -- this lets us log results as soon as they're available.
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as exc:
                # Catch anything that archive_repo didn't handle internally.
                log.error(f"Unexpected error archiving {name}: {exc}")
                with counts_lock:
                    counts["failed"] += 1

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
