<h1 align="center">iagitup</h1>

<p align="center">
  <img src="https://img.shields.io/badge/version-3.7.0-blue?style=flat" alt="Version 3.7.0">
  <a href="https://pypi.org/project/iagitup/"><img src="https://img.shields.io/pypi/v/iagitup?style=flat&cache_seconds=0" alt="PyPI version"></a>
  <a href="https://pypi.org/project/iagitup/"><img src="https://img.shields.io/pypi/pyversions/iagitup?style=flat&cache_seconds=0" alt="Python versions"></a>  <a href="https://github.com/gdamdam/iagitup/actions/workflows/tests.yml"><img src="https://github.com/gdamdam/iagitup/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/pypi/l/iagitup?style=flat&cache_seconds=0" alt="License: GPLv3"></a>
</p>

**Archive git repositories to the [Internet Archive](https://archive.org).**

`iagitup` clones a repository, packs it into a [git bundle](https://git-scm.com/docs/git-bundle), and uploads it to archive.org with metadata.
`archive-watchlist` does the same for the most-starred GitHub repos on a schedule.

---

## Features

- **Any HTTPS git host** -- GitHub, GitLab, Bitbucket, Codeberg, self-hosted Gitea/Forgejo, or a plain URL.
- **Complete snapshots** -- every branch, tag and ref in one verified bundle.
- **Wikis, LFS, submodules** -- archived alongside the main bundle.
- **Releases, issues, pull requests** -- optional GitHub exports, assets included.
- **Rich metadata** -- description, README, avatar, stars and forks from the platform API.
- **No duplicates** -- a snapshot is uploaded once; a new push creates a new item.
- **Resumable** -- keep the clone and retry an interrupted upload without re-cloning.
- **Bulk mode** -- top 1000 GitHub repos, date filters, parallel workers, JSON summaries.

---

## Installation

```bash
pip install iagitup
```

Installs two commands: `iagitup` and `archive-watchlist`.

Requirements:

- Python 3.10+
- `git` on `$PATH` (`git-lfs` too, if you want LFS objects)
- An [Internet Archive account](https://archive.org/account/login.createaccount.php)
- HTTPS repository URLs (SSH syntax is not supported)

---

## Quick Start

```bash
iagitup https://github.com/torvalds/linux
iagitup https://gitlab.com/inkscape/inkscape
iagitup https://codeberg.org/forgejo/forgejo
```

```
:: Downloading https://github.com/torvalds/linux ...
:: Uploading bundle: torvalds-linux_-_2026-02-28_10-00-00.bundle
:: Upload FINISHED.
   Archived repository: https://archive.org/details/github.com-torvalds-linux_-_2026-02-28_10-00-00
   Git bundle:          https://archive.org/download/github.com-torvalds-linux_-_2026-02-28_10-00-00/torvalds-linux_-_2026-02-28_10-00-00.bundle
```

Bulk mode:

```bash
archive-watchlist --dry-run --top 10          # preview
archive-watchlist --workers 8                 # top 100, all-time
archive-watchlist --days 7                    # trending this week
archive-watchlist --since 2025-01-01 --until 2025-06-30
```

---

## Usage

### `iagitup [options] <repo_url>`

| Flag | Description |
|---|---|
| `-m, --metadata` | Extra metadata as `key:value,key2:value2` |
| `--dry-run` | Print identifier and metadata, upload nothing. No credentials needed |
| `--submodules` | Clone recursively and upload one bundle per submodule |
| `--releases` | Archive release notes and assets (GitHub only) |
| `--issues` | Archive issues and pull requests with comments (GitHub only) |
| `--work-dir DIR` | Where to put temporary clones (default: system temp) |
| `--keep-work-dir` | Keep the clone after the run and print its path |
| `--resume PATH` | Upload from a kept clone instead of cloning again |
| `-v, --version` | Print version |

Resume an interrupted upload:

```bash
iagitup --keep-work-dir https://github.com/user/repo
# ... fails; stderr shows ":: Work directory kept: /tmp/tmpabc123/repo"
iagitup --resume /tmp/tmpabc123/repo https://github.com/user/repo
```

### `archive-watchlist [options]`

| Flag | Default | Description |
|---|---|---|
| `--top N` | 100 | Repos to check, up to 1000 |
| `--days N` | all-time | Only repos created in the last N days |
| `--since DATE` | | Only repos created on or after `YYYY-MM-DD` |
| `--until DATE` | | Only repos created on or before `YYYY-MM-DD` |
| `--max-size MB` | no limit | Skip repos larger than this |
| `--workers N` | 4 | Parallel uploads |
| `--dry-run` | | Preview only |
| `--state-file PATH` | `./watchlist_state.json` | Where the last-seen snapshots are recorded |
| `--json-summary PATH` | | Write a run summary (counts, timings, failures) as JSON |
| `--max-failures N` | | Exit with code 2 if more than N repos fail |
| `--work-dir DIR` | system temp | Where to put temporary clones |
| `--keep-work-dir` | | Keep clones for `iagitup --resume` |

Cron, daily at 03:00:

```cron
GITHUB_TOKEN=ghp_your_token_here
0 3 * * * archive-watchlist --json-summary /var/log/watchlist.json >> watchlist.log 2>&1
```

---

## Configuration

**Internet Archive credentials.** On first run you are asked to run `ia configure`. Keys are read from `~/.config/internetarchive/ia.ini`, `~/.ia` or `~/.config/ia.ini`. Get them at <https://archive.org/account/s3.php>.

**`GITHUB_TOKEN`** raises the GitHub API limit from 60 to 5,000 requests per hour. No scopes needed for public repos. Get one at <https://github.com/settings/tokens>.

**`GITLAB_TOKEN`** authenticates GitLab API calls for private projects and higher limits.

---

## What gets archived

Each snapshot is one IA item named `{host}-{owner}-{repo}_-_{YYYY-MM-DD_HH-MM-SS}`, dated by the last push.

| File | When |
|---|---|
| `<name>.bundle` | always |
| `cover.jpg` | the platform reports an avatar |
| `<name>_wiki.bundle` | the repo has a wiki |
| `<name>_lfs.tar.gz` | the repo uses Git LFS |
| `submodules/<name>_submodule_<path>.bundle` and `<name>_gitmodules.txt` | `--submodules` |
| `releases/releases.json`, `releases/<tag>/<asset>` | `--releases` |
| `issues.json`, `pulls.json` | `--issues` |

Item metadata: `title`, `date`, `year`, `creator`, `originalurl`, `pushed_date`, `subject` (`GitHub;code;software;git`), `stars_count`, `forks_count`, `has_lfs`, and an HTML `description` built from the repo description, README and restore instructions. `archive-watchlist` adds `language`, `topics`, `watchers_count` and `github_rank`. Fields passed with `--metadata` override these.

---

## How it works

1. Fetch metadata from the platform API, or from git history if there is none.
2. Skip if that snapshot already exists on archive.org. For GitHub this happens before cloning.
3. Clone, fetch the wiki, avatar and LFS objects, and any requested extras.
4. Create and verify the bundles.
5. Upload everything to one item.

`archive-watchlist` also keeps a local state file of `pushed_at` per repo, so unchanged repos are skipped with no network calls. Delete a repo's entry to force a re-archive.

---

## Restoring

```bash
wget https://archive.org/download/<identifier>/<name>.bundle
git clone <name>.bundle my-repo
```

LFS objects:

```bash
cd my-repo
wget https://archive.org/download/<identifier>/<name>_lfs.tar.gz
tar -xzf <name>_lfs.tar.gz -C .git/
git lfs install && git lfs checkout
```

Submodules: clone each `submodules/*.bundle` into the path listed in `<name>_gitmodules.txt`.

---

## Contributing

Issues and pull requests are welcome at <https://github.com/gdamdam/iagitup>. Tests run on Python 3.10 to 3.13.

## License

GPLv3. Copyright (C) 2018-2026 Giovanni Damiola. See [LICENSE](LICENSE).
