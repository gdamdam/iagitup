# iagitup.py - v1.1 - archive a github repository on archive.org

A small tool to archive a GitHub repository on the Internet Archive.
The script downloads the GitHub repository, creates a [git bundle](https://git-scm.com/docs/git-bundle) and uploads it on archive.org

The upload is enriched with metadata from the github api and the README.md

## Install

Prerequisites (with Debian or Ubuntu):

    apt update 
    apt install python python-dev python-virtualenv libffi-dev libssl-dev git

Clone the repo and create the virtualenv and all things...

    git clone https://github.com/gdamdam/iagitup.git ; cd iagitup

Create the virtualenv:

    virtualenv venv
    source venv/bin/activate

Install the requirements:

    pip install -r requirements.txt

If you don't already have an Internet Archive account, [register for one](https://archive.org/account/login.createaccount.php) to give the script upload privileges.

Configure `internetarchive` with your Internet Archive login details:

        ia configure


## Usage

        ./iagitup.py [-h] --githuburl/-u <github_repo_url>

You can add also custom metadata:

        ./iagitup.py [-h] --githuburl/-u <github_repo_url> --metadata=<key:value,key2:val2>

Example:

        ./iagitup.py -u https://github.com/<GITHUBUSER>/<RESPOSITORY>

The script downloads the git repo from github, creates a git bundle and uploads it on the Internet Archive.

The repo will be archived in an item at url containing the repository name and the date of the last push, something like:

    https://archive.org/details/github.com-<GITHUBUSER>-<RESPOSITORY>_-_<DATE-LAST-PUSH>

The git repo bundle will be available at url:

    https://archive.org/download/github.com-<GITHUBUSER>-<RESPOSITORY>_-_<DATE-LAST-PUSH>/<BUNDLENAME>.bundle

## Restore an archived github repository

Download the bundle file, form the archived item:

    https://archive.org/download/.../<ARCHIVED_REPO>.bundle
Just download the _.bundle_ file and run:

     git clone file.bundle -b master



## License (GPLv3)

Copyright (C) 2017 Giovanni Damiola

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
