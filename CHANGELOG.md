# Changelog

All notable changes to this project will be documented in this file.

## [3.7.0] - 2026-10-20

### Added

- **`archive-watchlist` pagination** -- `--top` now accepts up to 1000 (10 pages of 100). Pages are paced at 2 s without `GITHUB_TOKEN` to respect the Search API secondary limit; when the rate limit is exhausted the run continues with what was fetched and logs the reset time.
- **`archive-watchlist --json-summary PATH`** -- machine-readable run summary (`started_at`, `finished_at`, `archived`, `skipped`, `failed`, `total_checked`, `dry_run`, `failures[{repo,error}]`) for cron monitoring.
- **`archive-watchlist --max-failures N`** -- exit code 2 when more than N repos fail.
- **`--keep-work-dir`** (both commands) and **`iagitup --resume PATH`** -- keep the clone on disk and resume an interrupted upload from it instead of re-cloning multi-gigabyte repos. `--resume` implies `--keep-work-dir`.
- **`iagitup --dry-run`** -- print the identifier, bundle URL and metadata as JSON without uploading. GitHub URLs are previewed without cloning; other hosts are cloned (the push date is only known from the clone) but nothing is uploaded. No IA credentials needed.
- **Rich metadata for GitLab, Codeberg and Gitea/Forgejo** (new `iagitup/platforms.py`) -- description, avatar, wiki, star/fork counts and `pushed_at` now come from the platform API, including self-hosted instances (probed Gitea then GitLab). `GITLAB_TOKEN` is honoured. IA items get `stars_count` / `forks_count` for every platform that reports them.
- **`iagitup --releases` / `--issues`** (GitHub only, new `iagitup/github_extras.py`) -- upload `releases/releases.json` plus every release asset under `releases/<tag>/`, and `issues.json` / `pulls.json` with comments attached. Asset names are sanitised; `max_asset_mb` is available on the API.
- **`iagitup --submodules`** -- clone with `--recurse-submodules` and upload one verified bundle per submodule under `submodules/`, plus a copy of `.gitmodules`, so the archive is restorable.
- **Bundle verification** -- `git bundle verify` runs on every bundle before upload (`verify_bundle`); corruption raises `BundleError` instead of reaching IA.
- `upload_ia(extra_files=..., dry_run=...)` and `repo_download(submodules=...)` parameters; `create_submodule_bundles()`.

### Changed

- PyPI classifier: Development Status is now `5 - Production/Stable`.
- `archive_repo()` accepts optional `failures`, `failures_lock` and `keep_work_dir` arguments (all default to the previous behaviour).

## [3.6.3] - 2026-10-20

### Fixed

- **Race in `archive-watchlist` state save** -- workers wrote to the shared state dict while another worker serialised it with `json.dumps`, which could raise "dictionary changed size during iteration", miscount a successful archive as failed, and drop that save. All state writes and saves are now serialised by a single lock.
- **Literal `None` in IA descriptions** -- GitHub returns `"description": null` for repos without one, which rendered as the text "None" at the top of the item description.
- **Redundant clone of already-archived snapshots** -- both the CLI and `archive-watchlist` now check whether the IA item exists *before* cloning (GitHub URLs; non-GitHub URLs are checked right after the clone, before bundling). `archive-watchlist` also no longer calls the GitHub repos API a second time for metadata the Search API already returned.
- **Misleading "Upload FINISHED" on duplicates** -- the CLI now prints "already archived -- nothing uploaded" with the existing item links instead.
- **Clone hardening** -- `git clone` is invoked with `--` before the URL so a `-`-prefixed URL can't be parsed as a git option, and with `GIT_TERMINAL_PROMPT=0` so a private or missing repo fails fast instead of hanging on a credential prompt (matching the wiki clone). Credential helpers still work; interactive password entry does not.

### Added

- `fetch_repo_metadata(repo_url)` and `find_existing_item(repo_data, s3_access, s3_secret)` public helpers.
- `repo_download(..., repo_data=...)` accepts pre-fetched GitHub metadata to skip the API call.
- `ruff check` step in GitHub Actions and GitLab CI.

## [3.6.2] - 2026-03-11

### Changed

- `archive-watchlist`: `--top-n` renamed to `--top`.

## [3.6.1] - 2026-03-11

### Added

- `--work-dir` option (CLI and `archive-watchlist`) to choose the directory for temporary clones instead of the system temp directory.

## [3.6.0] - 2026-03-11

### Added

- `--max-size N` flag for `archive-watchlist` -- skip repositories larger than N MB (based on GitHub's reported size) to avoid enormous clones.

## [3.5.0] - 2026-03-11

### Added

- `--since` / `--until` date range filters for `archive-watchlist` (YYYY-MM-DD), mutually exclusive with `--days`. README and `--help` updated.

## [3.4.0] - 2026-03-11

### Added

- **`--days N` flag for `archive-watchlist`** -- filter repositories by creation date, archiving only repos created within the last N days. Uses GitHub's `created:>YYYY-MM-DD` search qualifier. Without `--days`, behavior is unchanged (all-time most-starred).

## [3.3.0] - 2026-03-11

### Added

- **Multi-platform git hosting support** -- iagitup now accepts any HTTPS git URL, not just GitHub. GitLab, Bitbucket, Codeberg, self-hosted Gitea/Forgejo, and any other HTTPS-accessible git repository can be archived.
- **Clone progress output** -- `git clone --progress` is now used via `subprocess`, so transfer progress (counting, compressing, receiving objects) is streamed to stderr in real time. No more "stuck" clones with large repos.
- **Bulletproof temp file cleanup** -- the entire download+upload is wrapped in a single `try/finally` block that always cleans up the temp directory, even on `Ctrl+C` (`KeyboardInterrupt`) or unexpected errors. This prevents cloned repos from accumulating in `/tmp` and filling the user's disk.
- New `_build_repo_data_from_clone()` function extracts metadata (last commit date, owner, repo name) from the local git history for non-GitHub platforms.
- `_PLATFORM_LABELS` dict and `_platform_label()` helper map hostnames to human-readable names (e.g. `gitlab.com` → `GitLab`) for IA subject tags.
- `_parse_repo_url()` now returns a 3-tuple `(owner, repo, hostname)` instead of the old 2-tuple, enabling platform-aware routing.
- Platform-aware IA item identifiers: `{platform}-{owner}-{repo}_-_{date}` (e.g. `gitlab.com-user-proj_-_2026-03-01_12-00-00`).
- Platform-aware IA subject tags: e.g. `GitLab;code;software;git` instead of hardcoded `GitHub;code;software;git`.
- Avatar download is skipped when `avatar_url` is `None` (non-GitHub platforms don't provide one).
- New tests: `TestParseRepoUrl` (GitLab, Bitbucket, Codeberg, self-hosted URLs), `TestPlatformLabel`, `TestBuildRepoDataFromClone` (real git repo, empty repo fallback, `.git` suffix stripping), generic `repo_download` test verifying no GitHub API call, platform-aware `upload_ia` tests.
- CLI `--help` and `PROGRAM_DESCRIPTION` updated to mention multi-platform support.
- `pyproject.toml` keywords now include `gitlab`, `bitbucket`, `gitea`.

### Changed

- **`repo_download()`**: parameter renamed `github_repo_url` → `repo_url`. GitHub URLs use the API path; all other URLs clone directly and build metadata from git history. Clone now uses `subprocess.check_call` with `--progress` instead of GitPython's `git.Git().clone()`.
- **`upload_ia()`**: parameters renamed `gh_repo_folder` → `repo_folder`, `gh_repo_data` → `repo_data`. Now reads `_platform` from the metadata dict (defaults to `"github.com"` for backward compatibility with `archive_watchlist`).
- **CLI (`__main__.py`)**: argument `github_url` → `repo_url`. Download + upload now wrapped in a single `try/finally` for guaranteed cleanup. `KeyboardInterrupt` is caught with a user-friendly message.
- `_parse_github_url` renamed to `_parse_repo_url` with a generic error message (`"Invalid repository URL"` instead of `"Invalid GitHub URL"`).
- `__init__.py` docstring updated from "GitHub repositories" to "git repositories".
- `pyproject.toml` description updated to "Archive git repositories to the Internet Archive."
- README updated: intro paragraph, features list, prerequisites, usage examples, metadata table, and "How it works" section all reflect multi-platform support.
- Version bumped to `3.3.0`.

### Unchanged

- **`archive_watchlist.py`** is not modified -- it's inherently GitHub-specific (Search API, stars). It continues to work because `upload_ia()` defaults `_platform` to `"github.com"` via `.get()`.
- **`_download_wiki()`** is not modified -- it already returns `None` when `has_wiki` is `False`, which is the value set for all non-GitHub platforms.

## [3.2.0] - 2026-02-28

- Git LFS support: detect, fetch, and archive LFS objects.

## [3.1.2] - Previous releases

- See git history for earlier changes.
