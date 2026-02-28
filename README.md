# iagitup — archive a GitHub repository to the Internet Archive

Downloads a GitHub repository, creates a [git bundle](https://git-scm.com/docs/git-bundle), and uploads it to an Internet Archive item with full metadata from the GitHub API and an HTML description from the repository README. If the repository has a wiki, it is bundled and uploaded to the same item.

## Prerequisites

- Python **3.10+**
- `git` installed and on `$PATH`
- An [Internet Archive account](https://archive.org/account/login.createaccount.php)

## Install

### From PyPI

```bash
pip install iagitup
```

### From source

```bash
git clone https://github.com/gdamdam/iagitup.git
cd iagitup
pip install .
```

## Usage

Archive a single repository:

```bash
iagitup <github_repo_url>
```

With custom metadata:

```bash
iagitup --metadata=<key:value,key2:val2> <github_repo_url>
```

Example:

```bash
iagitup https://github.com/user/repo
```

The archived item URL will be:

```
https://archive.org/details/github.com-<USER>-<REPO>_-_<DATE-LAST-PUSH>
```

The git bundle will be at:

```
https://archive.org/download/github.com-<USER>-<REPO>_-_<DATE-LAST-PUSH>/<BUNDLE>.bundle
```

### GitHub API rate limits

Unauthenticated requests are limited to 60/hour. Set a `GITHUB_TOKEN` environment variable to raise this to 5 000/hour:

```bash
export GITHUB_TOKEN=ghp_...
iagitup https://github.com/user/repo
```

## Restore an archived repository

Download the `.bundle` file from the archived item and run:

```bash
git clone file.bundle
```

---

## archive_watchlist — bulk-archive the top GitHub repositories

`archive_watchlist.py` is a companion script that fetches the top-N most-starred repositories from GitHub and archives any that are new or have been updated since the last run.

### How it works

1. Queries the GitHub Search API for the top-N repos sorted by stars.
2. Compares each repo's `pushed_at` timestamp against a local state cache (`watchlist_state.json`).
3. **Skips** repos unchanged since the last run (fast, no network traffic).
4. **Archives** new or updated repos via iagitup with extra metadata (stars, forks, language, topics, rank).
5. Repos are archived **in parallel** using a configurable worker pool.
6. State is saved to disk after each archive so a crash mid-run doesn't lose progress.

### Duplicate prevention (two layers)

- **Local cache**: `pushed_at` timestamp check — instant skip without cloning or hitting IA.
- **IA item check**: `upload_ia` checks whether the IA item identifier already exists before doing any heavy work. Since the identifier encodes the `pushed_at` date, the same snapshot is never uploaded twice.

A repo that receives new commits will have a different `pushed_at`, producing a new IA item — preserving the full archive history.

### Usage

```bash
python archive_watchlist.py                  # archive top 100, 4 parallel workers
python archive_watchlist.py --top-n 10       # quick test
python archive_watchlist.py --workers 8      # increase parallelism
python archive_watchlist.py --dry-run        # preview without uploading
python archive_watchlist.py --state-file /path/to/state.json
```

### Cron example (daily at 03:00)

```cron
0 3 * * * cd /path/to/iagitup && python archive_watchlist.py >> watchlist.log 2>&1
```

### State file format

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

---

## License (GPLv3)

Copyright (C) 2018-2026 Giovanni Damiola

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
