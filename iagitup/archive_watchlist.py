#!/usr/bin/env python3
"""archive_watchlist.py — Archive the top-N most-starred GitHub repos to the Internet Archive.

Designed to be run as a single-shot script (e.g. via cron). On each run it:
  1. Fetches the top-N repos from the GitHub Search API (sorted by stars,
     optionally filtered by --days, --since, or --until).
  2. Compares each repo's pushed_at timestamp against a local state cache.
  3. Skips repos that haven't changed since the last run.
  4. Archives new or updated repos using iagitup, injecting rich popularity metadata.
  5. Saves the updated state so the next run knows what was already archived.

Repos are archived in parallel using a configurable worker pool (--workers).

Usage:
    python archive_watchlist.py                  # archive top 100 (all-time), 4 workers
    python archive_watchlist.py --top 10       # quick test
    python archive_watchlist.py --days 7                            # last week
    python archive_watchlist.py --days 30                           # last month
    python archive_watchlist.py --since 2025-01-01 --until 2025-06-30  # date range
    python archive_watchlist.py --since 2025-01-01                  # since a date
    python archive_watchlist.py --until 2025-12-31                  # up to a date
    python archive_watchlist.py --max-size 500                       # skip repos > 500 MB
    python archive_watchlist.py --workers 8                         # more parallelism
    python archive_watchlist.py --top 500                           # paginated, max 1000
    python archive_watchlist.py --json-summary run.json --max-failures 3  # cron monitoring
    python archive_watchlist.py --keep-work-dir                     # keep clones for --resume
    python archive_watchlist.py --dry-run        # preview without uploading
    python archive_watchlist.py --state-file /path/to/state.json

Cron example (daily at 03:00):
    0 3 * * * cd /path/to/iagitup && python archive_watchlist.py >> watchlist.log 2>&1
"""

import argparse
import json
import logging
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from iagitup.iagitup import (
    IagitupError,
    __version__,
    _github_headers,
    find_existing_item,
    get_ia_credentials,
    repo_download,
    upload_ia,
)

log = logging.getLogger(__name__)

# Guards every write to the shared in-memory state dict *and* its
# serialisation in save_state().  json.dumps(indent=...) uses the pure-Python
# encoder, which iterates the dict lazily, so a concurrent insert from another
# worker raises "dictionary changed size during iteration".
_state_lock = threading.Lock()

# Relative path; resolves to cwd so the state file lives alongside the script
# when launched from the project root (typical cron setup).
DEFAULT_STATE_FILE = Path("watchlist_state.json")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
# The Search API only exposes the first 1000 results (10 pages of 100).
MAX_TOP_N = 1000
# Exit code used when --max-failures is exceeded; distinct from 1 (fatal setup
# errors) so cron wrappers can tell "ran, but too many failures" apart.
EXIT_TOO_MANY_FAILURES = 2


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

def fetch_top_repos(
    n: int,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Fetch the top-N most-starred GitHub repositories via the Search API.

    Uses the GITHUB_TOKEN environment variable (via ``_github_headers``) when
    available to raise the rate limit from 60 to 5 000 requests/hour.

    Args:
        n: Number of repositories to fetch, paginated 100 per request. The
            Search API exposes at most 1000 results (``MAX_TOP_N``).
        days: If set, only include repositories created within the last
            ``days`` days.  When ``None`` (default), no creation-date filter
            is applied and the query returns the all-time most-starred repos.
        since: If set, only include repositories created on or after this date
            (YYYY-MM-DD).  Mutually exclusive with ``days``.
        until: If set, only include repositories created on or before this date
            (YYYY-MM-DD).  Can be combined with ``since`` for a date range, or
            used alone (matches all repos created up to that date).

    Returns:
        List of raw repository dicts from the GitHub API.

    Raises:
        SystemExit: If the first page returns a non-200 status code.  A
            failure on a later page, or an exhausted rate limit, returns the
            repositories collected so far instead.
    """
    query = "stars:>1"
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query += f" created:>{cutoff}"
    elif since is not None or until is not None:
        if since and until:
            query += f" created:{since}..{until}"
        elif since:
            query += f" created:>={since}"
        else:
            query += f" created:<={until}"

    # The search endpoint has a secondary limit (10 req/min unauthenticated,
    # 30 with a token), so throttle page requests when no token is available.
    throttle = not os.environ.get("GITHUB_TOKEN")

    repos: list[dict] = []
    remaining = 9999
    page = 1
    while len(repos) < n:
        per_page = min(100, n - len(repos))
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
            "page": page,
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
            if page == 1:
                sys.exit(1)
            # Later page: keep the partial result rather than discarding work.
            return repos

        items = resp.json().get("items", [])
        repos.extend(items)

        remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
        if remaining == 0:
            reset = resp.headers.get("X-RateLimit-Reset")
            try:
                reset_str = datetime.fromtimestamp(int(reset), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except (TypeError, ValueError):
                reset_str = "unknown"
            log.error(
                f"GitHub API rate limit exhausted (X-RateLimit-Reset: {reset_str}); "
                f"returning {len(repos)} repositories collected so far."
            )
            return repos
        if remaining <= 5:
            log.warning(
                f"GitHub API rate limit almost exhausted ({remaining} requests left). "
                "Set GITHUB_TOKEN to increase the limit."
            )

        # A short page means GitHub has no more results for this query.
        if len(items) < per_page:
            break
        page += 1
        if len(repos) < n and throttle:
            time.sleep(2)

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

# Fallback lock for callers that pass ``failures`` without their own lock.
_FAILURES_LOCK = threading.Lock()

def archive_repo(
    repo_data: dict,
    rank: int,
    s3_access: str,
    s3_secret: str,
    state: dict,
    dry_run: bool,
    work_dir: Path | None = None,
    failures: list | None = None,
    failures_lock: "threading.Lock | None" = None,
    keep_work_dir: bool = False,
) -> str:
    """Download and archive a single repository, updating the state on success.

    Skip logic (two layers):
    - Local: if ``pushed_at`` matches the cached value the repo is unchanged
      and we return immediately without touching the network or IA.
    - Remote: ``find_existing_item`` checks IA *before* cloning, so even if
      the local state is lost, neither a redundant clone nor a duplicate
      upload happens; the state entry is re-recorded instead.

    The temporary directory created by ``repo_download`` is cleaned up in the
    finally block, including any wiki subdir, unless ``keep_work_dir`` is set.

    Args:
        repo_data: Raw repository dict from the GitHub API.
        rank: 1-based position in the top-N list.
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        state: Shared state dict (updated in-place on successful archive).
        dry_run: When True, log what would happen without uploading anything.
        work_dir: Parent directory for temporary clones (``None`` = system temp).
        failures: Optional list that receives ``{"repo", "error"}`` entries on
            failure, so callers can build a structured report.  The function
            still returns ``"failed"``; this is purely additive.
        failures_lock: Lock guarding appends to ``failures`` (worker threads
            share one list).  A module-level lock is used if omitted.
        keep_work_dir: When True, leave the temp clone on disk and log its
            path so the run can be resumed with the main CLI's ``--resume``.

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
        # Remote skip, checked *before* cloning: the Search API already gave
        # us pushed_at, so the identifier is known without a clone.
        existing = find_existing_item(repo_data, s3_access, s3_secret)
        if existing is not None:
            identifier = existing[0]
            log.info(f"           already archived → https://archive.org/details/{identifier}")
        else:
            # Pass the Search API dict so repo_download doesn't hit the
            # GitHub API a second time for metadata we already have.
            _, repo_folder = repo_download(
                repo_data["html_url"], work_dir=work_dir, repo_data=repo_data,
            )
            custom_meta = build_custom_meta(repo_data, rank)
            identifier, _, _ = upload_ia(
                repo_folder,
                repo_data,
                s3_access=s3_access,
                s3_secret=s3_secret,
                custom_meta=custom_meta,
            )
            log.info(f"           → https://archive.org/details/{identifier}")

        # Record the archive so future runs can skip this snapshot locally.
        with _state_lock:
            state[full_name] = {
                "pushed_at": pushed_at,
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "ia_identifier": identifier,
                "stars": stars,
            }
        return "archived"

    except IagitupError as exc:
        log.error(f"           FAILED: {exc}")
        if failures is not None:
            with failures_lock or _FAILURES_LOCK:
                failures.append({"repo": full_name, "error": str(exc)})
        return "failed"
    finally:
        if repo_folder is not None:
            if keep_work_dir:
                log.info(f"           Work dir kept: {repo_folder.parent} (resume with iagitup --resume)")
            else:
                # Clean the entire mkdtemp root (includes repo + wiki/ subdir).
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
        "--top", type=int, default=100,
        help=f"Number of top repositories to check (default: 100, max: {MAX_TOP_N}; fetched 100 per page).",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Only consider repos created within the last N days (default: all-time).",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Only consider repos created on or after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--until", type=str, default=None,
        help="Only consider repos created on or before this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--max-size", type=int, default=None,
        help="Skip repos larger than this size in MB (based on GitHub's reported size).",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel archive workers (default: 4).",
    )
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Directory for temporary clones (default: system temp directory).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be archived without uploading anything.",
    )
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE,
        help=f"Path to the state cache file (default: {DEFAULT_STATE_FILE}).",
    )
    parser.add_argument(
        "--json-summary", type=Path, default=None, metavar="PATH",
        help="Write a JSON run summary (counts, timings, failures) to PATH for cron monitoring.",
    )
    parser.add_argument(
        "--max-failures", type=int, default=None, metavar="N",
        help="Exit with code 2 if more than N repos fail (default: no threshold).",
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true",
        help="Keep temporary clone directories (path is logged) so a run can be resumed with iagitup --resume.",
    )
    parser.add_argument(
        "--version", "-v", action="version", version=__version__,
    )
    args = parser.parse_args()

    if args.top > MAX_TOP_N:
        parser.error(f"--top cannot exceed {MAX_TOP_N} (GitHub Search API result limit).")

    if args.days is not None and (args.since or args.until):
        parser.error("--days cannot be combined with --since/--until.")

    # Validate date format for --since and --until.
    for flag in ("since", "until"):
        value = getattr(args, flag)
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                parser.error(f"--{flag} must be a valid date in YYYY-MM-DD format.")

    if args.days is not None:
        log.info(f"Filtering repos created within the last {args.days} day(s).")
    if args.since or args.until:
        parts = []
        if args.since:
            parts.append(f"from {args.since}")
        if args.until:
            parts.append(f"until {args.until}")
        log.info(f"Filtering repos created {' '.join(parts)}.")
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

    started_at = datetime.now(timezone.utc)
    state = load_state(args.state_file)
    repos = fetch_top_repos(args.top, days=args.days, since=args.since, until=args.until)

    if args.max_size is not None:
        max_kb = args.max_size * 1024
        before = len(repos)
        repos = [r for r in repos if (r.get("size") or 0) <= max_kb]
        skipped = before - len(repos)
        if skipped:
            log.info(f"Filtered out {skipped} repo(s) exceeding {args.max_size} MB.")

    counts: dict[str, int] = {"archived": 0, "skipped": 0, "failed": 0}
    # _state_lock (module-level) guards the in-memory state dict and the
    # state file on disk; counts_lock guards the shared counters dict.
    counts_lock = threading.Lock()
    failures: list[dict] = []

    work_dir = Path(args.work_dir) if args.work_dir else None

    def run(rank: int, repo_data: dict) -> str:
        """Worker: archive one repo and persist state on success."""
        result = archive_repo(
            repo_data, rank, s3_access, s3_secret, state, args.dry_run,
            work_dir=work_dir,
            failures=failures, failures_lock=counts_lock,
            keep_work_dir=args.keep_work_dir,
        )
        with counts_lock:
            counts[result] += 1
        # Persist state only when something changed (not on skips) to reduce
        # unnecessary disk I/O; also ensures progress survives a mid-run crash.
        if not args.dry_run and result != "skipped":
            with _state_lock:
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
                    failures.append({"repo": name, "error": f"Unexpected error: {exc}"})

    verb = "would archive" if args.dry_run else "archived"
    log.info(
        f"\n:: Run complete — "
        f"{verb}: {counts['archived']} | "
        f"skipped: {counts['skipped']} | "
        f"failed: {counts['failed']} | "
        f"total checked: {len(repos)}"
    )

    if args.json_summary is not None:
        summary = {
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "archived": counts["archived"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "total_checked": len(repos),
            "dry_run": args.dry_run,
            "failures": failures,
        }
        args.json_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info(f"Wrote JSON summary to {args.json_summary}")

    if args.max_failures is not None and counts["failed"] > args.max_failures:
        log.error(f"{counts['failed']} failures exceed --max-failures {args.max_failures}.")
        sys.exit(EXIT_TOO_MANY_FAILURES)


if __name__ == "__main__":
    main()
