# iagitup — archive GitHub repositories to the Internet Archive

`iagitup` downloads a GitHub repository, creates a portable [git bundle](https://git-scm.com/docs/git-bundle), and uploads it to the [Internet Archive](https://archive.org) with rich metadata. If the repository has a wiki, that is bundled and uploaded too.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Install](#install)
- [iagitup — single repository](#iagitup--single-repository)
  - [Basic usage](#basic-usage)
  - [Options](#options)
  - [Custom metadata](#custom-metadata)
  - [GitHub authentication](#github-authentication)
  - [Internet Archive credentials](#internet-archive-credentials)
  - [What gets archived](#what-gets-archived)
  - [IA item structure](#ia-item-structure)
  - [Duplicate prevention](#duplicate-prevention)
- [archive_watchlist — bulk archiving](#archive_watchlist--bulk-archiving)
  - [How it works](#how-it-works)
  - [Options](#options-1)
  - [Duplicate prevention](#duplicate-prevention-1)
  - [Extra metadata](#extra-metadata)
  - [Cron setup](#cron-setup)
  - [State file](#state-file)
- [Restore an archived repository](#restore-an-archived-repository)
- [License](#license)

---

## Prerequisites

- Python **3.10+**
- `git` on `$PATH`
- An [Internet Archive account](https://archive.org/account/login.createaccount.php)

---

## Install

### From PyPI

```bash
pip install iagitup
```

This installs two commands: `iagitup` and `archive-watchlist`.

### From source

```bash
git clone https://github.com/gdamdam/iagitup.git
cd iagitup
pip install .
```

---

## iagitup — single repository

### Basic usage

```bash
iagitup <github_repo_url>
```

Example:

```bash
iagitup https://github.com/torvalds/linux
```

Output:

```
:: Downloading https://github.com/torvalds/linux ...
:: Cloning https://github.com/torvalds/linux.git ...
:: Uploading bundle: torvalds-linux_-_2026-02-28_10-00-00.bundle
:: Upload FINISHED.
   Identifier:          github.com-torvalds-linux_-_2026-02-28_10-00-00
   Archived repository: https://archive.org/details/github.com-torvalds-linux_-_2026-02-28_10-00-00
   Git bundle:          https://archive.org/download/github.com-torvalds-linux_-_2026-02-28_10-00-00/torvalds-linux_-_2026-02-28_10-00-00.bundle
```

### Options

| Flag | Short | Default | Description |
|---|---|---|---|
| `github_url` | — | *(required)* | GitHub repository URL to archive |
| `--metadata` | `-m` | — | Custom metadata fields (see below) |
| `--version` | `-v` | — | Print version and exit |

### Custom metadata

Pass additional Internet Archive metadata fields as comma-separated `key:value` pairs:

```bash
iagitup --metadata="subject:python;cli,creator:myorg" https://github.com/user/repo
```

Custom fields are **merged** into the default metadata. Any key that matches a default field will override it.

### GitHub authentication

Unauthenticated GitHub API calls are rate-limited to **60 requests/hour**. Set `GITHUB_TOKEN` to raise this to **5 000/hour**:

```bash
export GITHUB_TOKEN=ghp_your_token_here
iagitup https://github.com/user/repo
```

Generate a token at <https://github.com/settings/tokens> — no specific scopes are required for public repositories.

### Internet Archive credentials

On first run, if no credentials are found, iagitup will prompt you to run `ia configure` interactively. Credentials are stored in `~/.ia` or `~/.config/ia.ini` and re-used on subsequent runs.

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

### What gets archived

For every repository, iagitup:

1. **Calls the GitHub API** to fetch full repository metadata (`pushed_at`, description, owner, topics, language, …).
2. **Clones the repository** in full (all branches and tags).
3. **Downloads the owner's avatar** as a cover image (`cover.jpg`).
4. **Clones the wiki** (if `has_wiki` is set), concurrently with the avatar download.
5. **Checks IA for an existing item** with the same identifier — returns early if already archived.
6. **Creates a git bundle** (`git bundle create --all`) containing every ref.
7. **Creates a wiki bundle** if the wiki clone succeeded.
8. **Builds an HTML description** from the repo description + README (`.md` or `.txt`) + restore instructions.
9. **Uploads** the bundle, cover image, and wiki bundle (if present) to the IA item.

### IA item structure

Each archived repository becomes a single Internet Archive item containing:

| File | Description |
|---|---|
| `<bundle_name>.bundle` | Full git bundle (all branches + tags) |
| `cover.jpg` | Repository owner's avatar |
| `<bundle_name>_wiki.bundle` | Wiki git bundle *(if wiki exists)* |

**Automatic metadata fields** set on every item:

| Field | Value |
|---|---|
| `mediatype` | `software` |
| `collection` | `open_source_software` |
| `creator` | GitHub owner login |
| `title` | IA item identifier |
| `date` | Last push date (`YYYY-MM-DD`) |
| `year` | Last push year |
| `subject` | `GitHub;code;software;git` |
| `originalurl` | GitHub repository URL |
| `pushed_date` | Full push timestamp (`YYYY-MM-DD HH:MM:SS`) |
| `uploaded_with` | `iagitup-vX.X.X` |
| `description` | HTML: repo description + README + restore instructions |

### Duplicate prevention

The IA item identifier is derived from the repository name and the `pushed_at` timestamp:

```
github.com-{owner}-{repo}_-_{YYYY-MM-DD_HH-MM-SS}
```

- **Same snapshot**: if `pushed_at` is unchanged the identifier is identical and `iagitup` returns immediately without re-uploading.
- **New commits**: a different `pushed_at` produces a new identifier and a new IA item — the previous snapshot is preserved.

---

## archive_watchlist — bulk archiving

`archive-watchlist` (installed as a console command by `pip install iagitup`) runs as a single-shot script (scheduled via cron or similar) to continuously archive the **top-N most-starred GitHub repositories**.

### How it works

1. Fetches the top-N repos from the GitHub Search API (sorted by stars).
2. Compares each repo's `pushed_at` against a local state cache.
3. **Skips** unchanged repos instantly (no network, no IA call).
4. **Archives** new or updated repos via iagitup, enriched with popularity metadata.
5. Repos are processed **in parallel** across a configurable worker pool.
6. State is saved to disk after each archive — a crash mid-run loses at most one item.

### Options

```bash
archive-watchlist [options]
```

| Flag | Default | Description |
|---|---|---|
| `--top-n N` | `100` | Number of top repositories to fetch and check (max 100) |
| `--workers N` | `4` | Number of parallel archive workers |
| `--dry-run` | off | Preview what would be archived — no uploads, no state changes |
| `--state-file PATH` | `./watchlist_state.json` | Path to the persistent state cache |

Examples:

```bash
# Preview the top 10 without uploading
archive-watchlist --dry-run --top-n 10

# Full run with more parallelism
archive-watchlist --workers 8

# Use a custom state file
archive-watchlist --state-file /var/lib/iagitup/state.json
```

### Duplicate prevention

Two independent layers prevent the same snapshot from being uploaded twice:

| Layer | Where | How |
|---|---|---|
| **Local cache** | `archive_watchlist.py` | Compares `pushed_at` to the state file — instant skip, zero network traffic |
| **IA item check** | `iagitup.upload_ia` | Checks `item.exists` on IA before any heavy work — safe even if the state file is deleted |

A new push changes `pushed_at`, generates a new IA item identifier, and triggers a fresh archive — preserving the full history of snapshots.

### Extra metadata

In addition to the standard iagitup fields, `archive_watchlist` injects:

| Field | Value |
|---|---|
| `stars_count` | Stargazer count at time of archive |
| `forks_count` | Fork count |
| `watchers_count` | Watcher count |
| `language` | Primary programming language |
| `topics` | Semicolon-joined topic list |
| `github_rank` | Position in the top-N list |
| `subject` | Extended: base tags + language + topics |

### Cron setup

Add to your crontab (`crontab -e`) to run daily at 03:00:

```cron
0 3 * * * archive-watchlist >> watchlist.log 2>&1
```

Set `GITHUB_TOKEN` in the cron environment to avoid rate limiting:

```cron
GITHUB_TOKEN=ghp_your_token_here
0 3 * * * archive-watchlist >> watchlist.log 2>&1
```

### State file

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

## Restore an archived repository

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

---

## License (GPLv3)

Copyright (C) 2018-2026 Giovanni Damiola

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
