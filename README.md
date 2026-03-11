<h1 align="center">iagitup</h1>

<p align="center">
  <img src="https://img.shields.io/badge/version-3.5.0-blue?style=flat" alt="Version 3.5.0">
  <a href="https://pypi.org/project/iagitup/"><img src="https://img.shields.io/pypi/v/iagitup?style=flat&cache_seconds=0" alt="PyPI version"></a>
  <a href="https://pypi.org/project/iagitup/"><img src="https://img.shields.io/pypi/pyversions/iagitup?style=flat&cache_seconds=0" alt="Python versions"></a>
  <a href="https://github.com/gdamdam/iagitup/actions/workflows/tests.yml"><img src="https://github.com/gdamdam/iagitup/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/pypi/l/iagitup?style=flat&cache_seconds=0" alt="License: GPLv3"></a>
</p>


**Archive git repositories to the [Internet Archive](https://archive.org).**

`iagitup` clones a git repository from GitHub, GitLab, Bitbucket, Codeberg, or any HTTPS git URL, creates a portable [git bundle](https://git-scm.com/docs/git-bundle), and uploads it to the Internet Archive with rich metadata. GitHub repos get full API metadata; all other platforms extract metadata from the local git history. If the repository has a wiki (GitHub only), that is bundled and uploaded too. A companion command, `archive-watchlist`, continuously archives the most-starred repositories on GitHub -- either all-time, by recency (`--days`), or within a custom date range (`--since`/`--until`).

---

## Features

- **Multi-platform support** -- GitHub, GitLab, Bitbucket, Codeberg, self-hosted Gitea/Forgejo, and any HTTPS git URL.
- **Full-fidelity snapshots** -- every branch, tag, and ref is preserved in a single git bundle.
- **Git LFS support** -- LFS-enabled repos are detected automatically; large file objects are fetched and archived alongside the bundle.
- **Wiki archiving** -- wiki repositories are detected and bundled automatically (GitHub only).
- **Rich IA metadata** -- description, README, topics, language, stars, and more are attached to each item. GitHub repos get full API metadata; other platforms extract metadata from git history.
- **Duplicate prevention** -- two layers (local state cache + IA item check) ensure the same snapshot is never uploaded twice.
- **Bulk archiving** -- `archive-watchlist` fetches and archives the top-N most-starred GitHub repos on a schedule, with `--days`, `--since`, and `--until` filters for trending repos.
- **Parallel workers** -- configurable concurrency for bulk runs.
- **Custom metadata** -- pass extra `key:value` pairs to enrich any upload.

---

## Installation

### From PyPI

```bash
pip install iagitup
```

This installs two commands: **`iagitup`** and **`archive-watchlist`**.

### From source

```bash
git clone https://github.com/gdamdam/iagitup.git
cd iagitup
pip install .
```

### Prerequisites

- Python **3.10, 3.11, 3.12, or 3.13**
- `git` on `$PATH`
- `git-lfs` on `$PATH` *(optional — needed to archive LFS objects; repos are still archived without it, but LFS pointers won't resolve)*
- An [Internet Archive account](https://archive.org/account/login.createaccount.php)
- **HTTPS URLs** are required (SSH `user@host:path` syntax is not supported)
- For non-GitHub platforms, standard git authentication applies (credential helpers, `~/.netrc`, etc.)

---

## Quick Start

### Archive a single repository

```bash
# GitHub
iagitup https://github.com/torvalds/linux

# GitLab
iagitup https://gitlab.com/inkscape/inkscape

# Bitbucket
iagitup https://bitbucket.org/berkeleylab/upcxx

# Codeberg
iagitup https://codeberg.org/forgejo/forgejo

# Any HTTPS git URL
iagitup https://git.example.com/org/project
```

```
:: Downloading https://github.com/torvalds/linux ...
:: Cloning https://github.com/torvalds/linux.git ...
:: Uploading bundle: torvalds-linux_-_2026-02-28_10-00-00.bundle
:: Upload FINISHED.
   Identifier:          github.com-torvalds-linux_-_2026-02-28_10-00-00
   Archived repository: https://archive.org/details/github.com-torvalds-linux_-_2026-02-28_10-00-00
   Git bundle:          https://archive.org/download/github.com-torvalds-linux_-_2026-02-28_10-00-00/torvalds-linux_-_2026-02-28_10-00-00.bundle
```

### Bulk-archive top starred repos

```bash
# Preview the top 10 without uploading
archive-watchlist --dry-run --top-n 10

# Full run with 8 parallel workers
archive-watchlist --workers 8

# Archive the most-starred repos created in the last 7 days
archive-watchlist --days 7

# Archive repos created in a specific date range
archive-watchlist --since 2025-01-01 --until 2025-06-30

# Archive repos created since a specific date
archive-watchlist --since 2025-06-01

# Preview trending repos from the last month
archive-watchlist --days 30 --dry-run --top-n 20
```

---

## Usage

### `iagitup`

```bash
iagitup [options] <repo_url>
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `repo_url` | -- | *(required)* | Git repository URL to archive (GitHub, GitLab, Bitbucket, or any HTTPS git URL) |
| `--metadata` | `-m` | -- | Custom metadata fields (see [Custom Metadata](#custom-metadata)) |
| `--version` | `-v` | -- | Print version and exit |

### `archive-watchlist`

```bash
archive-watchlist [options]
```

| Flag | Default | Description |
|---|---|---|
| `--top-n N` | `100` | Number of top repositories to fetch and check (max 100) |
| `--days N` | *(all-time)* | Only consider repos created within the last N days |
| `--since DATE` | -- | Only consider repos created on or after DATE (YYYY-MM-DD) |
| `--until DATE` | -- | Only consider repos created on or before DATE (YYYY-MM-DD) |
| `--workers N` | `4` | Number of parallel archive workers |
| `--dry-run` | off | Preview what would be archived -- no uploads, no state changes |
| `--state-file PATH` | `./watchlist_state.json` | Path to the persistent state cache |
| `--version` / `-v` | -- | Print version and exit |

Examples:

```bash
# Use a custom state file
archive-watchlist --state-file /var/lib/iagitup/state.json

# Archive trending repos from the past week
archive-watchlist --days 7

# Archive repos from a date range with a custom state file
archive-watchlist --since 2025-01-01 --until 2025-12-31 --state-file /var/lib/iagitup/state.json

# Combine with top-n for a quick daily trending sweep
archive-watchlist --days 1 --top-n 20 --dry-run
```

---

## Configuration

### GitHub Authentication (GitHub repos only)

Unauthenticated GitHub API calls are rate-limited to **60 requests/hour**. Set `GITHUB_TOKEN` to raise this to **5,000/hour**:

```bash
export GITHUB_TOKEN=ghp_your_token_here
iagitup https://github.com/user/repo
```

Generate a token at <https://github.com/settings/tokens> -- no specific scopes are required for public repositories.

### Internet Archive Credentials

On first run, if no credentials are found, iagitup will prompt you to run `ia configure` interactively. Credentials are stored in `~/.ia` or `~/.config/ia.ini` and reused on subsequent runs.

You can also configure them manually:

```bash
ia configure
```

Or create `~/.ia` directly:

```ini
[s3]
access = YOUR_ACCESS_KEY
secret = YOUR_SECRET_KEY
```

Find your keys at <https://archive.org/account/s3.php>.

---

## Custom Metadata

Pass additional Internet Archive metadata fields as comma-separated `key:value` pairs:

```bash
iagitup --metadata="subject:python;cli,creator:myorg" https://github.com/user/repo
```

Custom fields are **merged** into the default metadata. Any key that matches a default field will override it.

### Default metadata fields

| Field | Value |
|---|---|
| `mediatype` | `software` |
| `collection` | `open_source_software` |
| `creator` | GitHub owner login |
| `title` | IA item identifier |
| `date` | Last push date (`YYYY-MM-DD`) |
| `year` | Last push year |
| `subject` | `{Platform};code;software;git` (e.g. `GitHub`, `GitLab`, `Bitbucket`) |
| `originalurl` | Repository URL |
| `pushed_date` | Full push timestamp (`YYYY-MM-DD HH:MM:SS`) |
| `uploaded_with` | `iagitup-vX.X.X` |
| `description` | HTML: repo description + README + restore instructions |

### Extra fields added by `archive-watchlist`

| Field | Value |
|---|---|
| `stars_count` | Stargazer count at time of archive |
| `forks_count` | Fork count |
| `watchers_count` | Watcher count |
| `language` | Primary programming language |
| `topics` | Semicolon-joined topic list |
| `github_rank` | Position in the top-N list |
| `subject` | Extended: base tags + language + topics |

---

## How It Works

### Single repository (`iagitup`)

1. **Fetches metadata** -- for GitHub repos, from the GitHub API (`pushed_at`, description, owner, etc.); for all other platforms, from the local git history after cloning.
2. **Checks for duplicates** -- the IA item identifier is derived from the platform hostname, repo name, and `pushed_at` timestamp (`{platform}-{owner}-{repo}_-_{YYYY-MM-DD_HH-MM-SS}`). If an item with that identifier already exists, iagitup exits early.
3. **Clones the repository** in full (all branches and tags).
4. **Downloads the owner's avatar** as a cover image (`cover.jpg`), concurrently with wiki cloning (GitHub only; skipped for other platforms).
5. **Creates git bundles** (`git bundle create --all`) for the repository and, if present, the wiki.
6. **Builds an HTML description** from the repo description, README (`.md` or `.txt`), and restore instructions.
7. **Uploads** the bundle(s) and cover image to the Internet Archive.

Each archived repository becomes a single IA item containing:

| File | Description |
|---|---|
| `<bundle_name>.bundle` | Full git bundle (all branches + tags) |
| `cover.jpg` | Repository owner's avatar |
| `<bundle_name>_wiki.bundle` | Wiki git bundle *(if wiki exists)* |
| `<bundle_name>_lfs.tar.gz` | Git LFS objects *(if repo uses LFS)* |

### Bulk archiving (`archive-watchlist`)

1. Fetches the top-N repos from the GitHub Search API (sorted by stars, optionally filtered by `--days`, `--since`, or `--until`).
2. Compares each repo's `pushed_at` against a local state cache.
3. **Skips** unchanged repos instantly (no network, no IA call).
4. **Archives** new or updated repos via iagitup, enriched with popularity metadata.
5. Repos are processed **in parallel** across a configurable worker pool.
6. State is saved to disk after each archive -- a crash mid-run loses at most one item.

#### Duplicate prevention

Two independent layers prevent the same snapshot from being uploaded twice:

| Layer | Where | How |
|---|---|---|
| **Local cache** | `archive_watchlist.py` | Compares `pushed_at` to the state file -- instant skip, zero network traffic |
| **IA item check** | `iagitup.upload_ia` | Checks `item.exists` on IA before any heavy work -- safe even if the state file is deleted |

A new push changes `pushed_at`, generates a new IA item identifier, and triggers a fresh archive -- preserving the full history of snapshots.

#### Cron setup

Add to your crontab (`crontab -e`) to run daily at 03:00:

```cron
0 3 * * * archive-watchlist >> watchlist.log 2>&1
```

Set `GITHUB_TOKEN` in the cron environment to avoid rate limiting:

```cron
GITHUB_TOKEN=ghp_your_token_here
0 3 * * * archive-watchlist >> watchlist.log 2>&1
```

#### State file

The state file (`watchlist_state.json`) tracks the last-seen snapshot of each repository:

```json
{
  "owner/repo": {
    "pushed_at": "2026-02-01T12:00:00Z",
    "archived_at": "2026-02-02T03:00:00Z",
    "ia_identifier": "github.com-owner-repo_-_2026-02-01_12-00-00",
    "stars": 470000
  }
}
```

To force a re-archive of a specific repo, delete its entry from the file or change `pushed_at` to an old value.

---

## Restoring an Archived Repository

1. Find the `.bundle` file in the archived IA item.
2. Download it:

```bash
wget https://archive.org/download/<identifier>/<bundle>.bundle
```

3. Clone from the bundle:

```bash
git clone <bundle>.bundle my-repo
```

All branches and tags are preserved in the bundle.

### Restoring LFS objects

If the archived item includes an `_lfs.tar.gz` file:

```bash
cd my-repo
wget https://archive.org/download/<identifier>/<bundle>_lfs.tar.gz
mkdir -p .git/lfs
tar -xzf <bundle>_lfs.tar.gz -C .git/
git lfs install
git lfs checkout
```

---

## Contributing

Contributions are welcome. Please open an issue or submit a pull request at <https://github.com/gdamdam/iagitup>.

The project uses GitHub Actions and GitLab CI for automated testing across Python 3.10, 3.11, 3.12, and 3.13.

---

## License

**GPLv3** -- Copyright (C) 2018-2026 Giovanni Damiola

This program is free software: you can redistribute it and/or modify it under the terms of the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0) as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
