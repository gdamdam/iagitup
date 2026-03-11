# Changelog

All notable changes to this project will be documented in this file.

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
