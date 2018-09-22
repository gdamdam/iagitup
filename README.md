# iagitup - v1.5 - archive a git repository from GitHub to the Internet Archive

A command line tool to archive a git repository from GitHub to the Internet Archive. 
The script downloads the GitHub repository, creates a [git bundle](https://git-scm.com/docs/git-bundle) and uploads it on an Internet Archive item with metadata from the GitHub API and the README.md

## Install iagitup

Prerequisites (with Debian or Ubuntu):

    sudo apt update && sudo apt install python python-dev libffi-dev libssl-dev git

Clone the repo and install the package...

    git clone https://github.com/gdamdam/iagitup.git
    cd iagitup
    pip install .

## Configure the Internet Archive CLI

If you don't already have an account on archive.org,
[register](https://archive.org/account/login.createaccount.php)
to give the script yours upload privileges.

Configure [internetarchive](https://github.com/jjjake/internetarchive) with your Internet Archive login details typing:

    ia configure

## Usage

To upload a repo:

    iagitup  <github_repo_url>

You can add also custom metadata:

    iagitup --metadata=<key:value,key2:val2> <github_repo_url>

To know the version:

    iagitup -v

Example:

    iagitup https://github.com/<GITHUBUSER>/<RESPOSITORY>

The script downloads the git repo from github, creates a git bundle and uploads it on the Internet Archive.

The repo will be archived in an item at url containing the repository name and the date of the last push, something like:

    https://archive.org/details/github.com-<GITHUBUSER>-<RESPOSITORY>_-_<DATE-LAST-PUSH>

The git repo bundle will be available at url:

    https://archive.org/download/github.com-<GITHUBUSER>-<RESPOSITORY>_-_<DATE-LAST-PUSH>/<BUNDLENAME>.bundle

## Restore an archived github repository

Download the bundle file, form the archived item:

    https://archive.org/download/.../<ARCHIVED_REPO>.bundle
Just download the _.bundle_ file and run:

    git clone file.bundle


## License (GPLv3)

Copyright (C) 2017-2018 Giovanni Damiola

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
