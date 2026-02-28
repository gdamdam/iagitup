# iagitup — archive a GitHub repository to the Internet Archive

Downloads a GitHub repository, creates a [git bundle](https://git-scm.com/docs/git-bundle), and uploads it to an Internet Archive item with metadata from the GitHub API and a description from the repository README. Optionally archives the wiki too.

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

The archived item URL will look like:

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

## License (GPLv3)

Copyright (C) 2018-2026 Giovanni Damiola

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
