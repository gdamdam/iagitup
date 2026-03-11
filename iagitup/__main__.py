#!/usr/bin/env python3
# iagitup - Archive a GitHub repository to the Internet Archive.
#
# Copyright (C) 2018-2026 Giovanni Damiola
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""CLI entry point for ``python -m iagitup`` and the ``iagitup`` console script.

Orchestrates the three-step workflow: validate credentials, download the repo,
and upload the archive to the Internet Archive. Temporary files are always
cleaned up, even when the upload fails.
"""

import argparse
import logging
import shutil
import sys

from iagitup.iagitup import (
    IagitupError,
    __version__,
    get_ia_credentials,
    repo_download,
    upload_ia,
)

PROGRAM_DESCRIPTION = (
    "Archive a git repository to the Internet Archive. "
    "Supports GitHub, GitLab, Bitbucket, Codeberg, and any HTTPS git URL. "
    "Downloads the repo, creates a git bundle, and uploads it to archive.org. "
    "Git LFS objects are detected and archived automatically when git-lfs is installed. "
    "https://github.com/gdamdam/iagitup"
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=":: %(message)s")

    parser = argparse.ArgumentParser(description=PROGRAM_DESCRIPTION)
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        type=str,
        help="Custom metadata as comma-separated key:value pairs (e.g. foo:bar,baz:qux)",
    )
    parser.add_argument("--version", "-v", action="version", version=__version__)
    parser.add_argument(
        "repo_url", type=str,
        help="Git repository URL to archive (GitHub, GitLab, Bitbucket, or any HTTPS git URL)",
    )
    args = parser.parse_args()

    try:
        s3_access, s3_secret = get_ia_credentials()
    except IagitupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Parse the free-form metadata string into a dict.  Using split(":", 1)
    # allows values to contain colons (e.g. "url:https://example.com").
    custom_meta: dict | None = None
    if args.metadata is not None:
        try:
            custom_meta = dict(pair.split(":", 1) for pair in args.metadata.split(","))
        except ValueError:
            print(
                "Error: --metadata must be formatted as key:value,key2:value2",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f":: Downloading {args.repo_url} ...")
    repo_folder = None  # track for cleanup in all exit paths
    try:
        repo_data, repo_folder = repo_download(args.repo_url)

        identifier, meta, bundle_stem = upload_ia(
            repo_folder,
            repo_data,
            s3_access=s3_access,
            s3_secret=s3_secret,
            custom_meta=custom_meta,
        )
    except KeyboardInterrupt:
        print("\n:: Interrupted — cleaning up temporary files ...", file=sys.stderr)
        sys.exit(130)
    except IagitupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Always clean the entire mkdtemp root (repo + any wiki/ subdir),
        # regardless of success, failure, or Ctrl+C.  This prevents cloned
        # repos from accumulating in /tmp and filling the user's disk.
        if repo_folder is not None:
            shutil.rmtree(repo_folder.parent, ignore_errors=True)

    # Print a human-friendly summary with direct links to the archived item.
    print("\n:: Upload FINISHED.")
    print(f"   Identifier:          {meta['title']}")
    print(f"   Archived repository: https://archive.org/details/{identifier}")
    print(
        f"   Git bundle:          https://archive.org/download/{identifier}/{bundle_stem}.bundle"
    )


if __name__ == "__main__":
    main()
