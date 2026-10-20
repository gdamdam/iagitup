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
import json
import logging
import shutil
import sys
from pathlib import Path

from iagitup.github_extras import fetch_issues, fetch_releases
from iagitup.iagitup import (
    IagitupError,
    __version__,
    _build_repo_data_from_clone,
    _item_names,
    _parse_repo_url,
    create_submodule_bundles,
    fetch_repo_metadata,
    find_existing_item,
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
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Directory for temporary clones (default: system temp directory).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview the identifier and metadata without uploading (no credentials needed). "
             "GitHub repos are not cloned.",
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true",
        help="Never delete the temporary clone; its path is printed to stderr at the end.",
    )
    parser.add_argument(
        "--resume", metavar="PATH", type=str, default=None,
        help="Resume from a clone kept with --keep-work-dir instead of downloading again "
             "(implies --keep-work-dir). For GitHub repos metadata is re-fetched, so if the "
             "repo was pushed to since the clone, the identifier reflects the newer date.",
    )
    parser.add_argument(
        "--submodules", action="store_true",
        help="Also clone submodules and upload each as an extra bundle.",
    )
    parser.add_argument(
        "--releases", action="store_true",
        help="Also archive GitHub release notes and assets (GitHub only).",
    )
    parser.add_argument(
        "--issues", action="store_true",
        help="Also archive GitHub issues and pull requests (GitHub only).",
    )
    parser.add_argument("--version", "-v", action="version", version=__version__)
    parser.add_argument(
        "repo_url", type=str,
        help="Git repository URL to archive (GitHub, GitLab, Bitbucket, or any HTTPS git URL)",
    )
    args = parser.parse_args()

    # Dry-run never touches IA, so skip credentials (which may prompt).
    s3_access = s3_secret = ""
    if not args.dry_run:
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

    resume_folder = Path(args.resume) if args.resume else None
    if resume_folder is not None and not (resume_folder / ".git").exists():
        print(
            f"Error: --resume path is not a git clone (no .git in {resume_folder})",
            file=sys.stderr,
        )
        sys.exit(1)
    # A user-supplied --resume directory must never be deleted.
    keep = args.keep_work_dir or resume_folder is not None

    repo_folder = None  # track for cleanup in all exit paths
    existing = None  # (identifier, bundle_stem) when the snapshot is already on IA
    try:
        work_dir = Path(args.work_dir) if args.work_dir else None

        # GitHub metadata is available before cloning, so the duplicate check
        # can run first and skip the (potentially huge) clone entirely.
        # Non-GitHub metadata only exists after the clone, so check afterwards.
        repo_data = fetch_repo_metadata(args.repo_url)
        is_github = repo_data is not None
        if (args.releases or args.issues) and not is_github:
            print(
                ":: Warning: --releases/--issues are GitHub-only; ignoring.",
                file=sys.stderr,
            )

        upload_folder = None
        if resume_folder is not None:
            repo_folder = resume_folder
            if repo_data is None:
                owner, repo, host = _parse_repo_url(args.repo_url)
                repo_data = _build_repo_data_from_clone(
                    args.repo_url, owner, repo, host, repo_folder,
                )
            if not args.dry_run:
                existing = find_existing_item(repo_data, s3_access, s3_secret)
        elif args.dry_run and is_github:
            # The push date comes from the API, so nothing needs cloning; the
            # missing folder just means readme/LFS detection finds nothing.
            upload_folder = Path("<nonexistent>")
        else:
            if is_github and not args.dry_run:
                existing = find_existing_item(repo_data, s3_access, s3_secret)
            if existing is None:
                print(f":: Downloading {args.repo_url} ...")
                prefetched = repo_data
                repo_data, repo_folder = repo_download(
                    args.repo_url, work_dir=work_dir, repo_data=prefetched,
                    submodules=args.submodules,
                )
                if prefetched is None and not args.dry_run:
                    existing = find_existing_item(repo_data, s3_access, s3_secret)

        if existing is None:
            extra_files: list[Path] = []
            if not args.dry_run and repo_folder is not None:
                if args.submodules:
                    bundle_stem = _item_names(repo_data)[1]
                    extra_files += create_submodule_bundles(repo_folder, bundle_stem)
                if is_github and args.releases:
                    extra_files += fetch_releases(repo_data, repo_folder.parent)
                if is_github and args.issues:
                    extra_files += fetch_issues(repo_data, repo_folder.parent)

            identifier, meta, bundle_stem = upload_ia(
                upload_folder or repo_folder,
                repo_data,
                s3_access=s3_access,
                s3_secret=s3_secret,
                custom_meta=custom_meta,
                extra_files=extra_files or None,
                dry_run=args.dry_run,
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
        # --keep-work-dir/--resume skip this so an interrupted upload can resume.
        if repo_folder is not None:
            if keep:
                print(f":: Work directory kept: {repo_folder}", file=sys.stderr)
            else:
                shutil.rmtree(repo_folder.parent, ignore_errors=True)

    if args.dry_run:
        print("\n:: DRY RUN — nothing was uploaded.")
        print(f"   Identifier:  {identifier}")
        print(f"   Git bundle:  https://archive.org/download/{identifier}/{bundle_stem}.bundle")
        print(json.dumps(meta, indent=2))
        return

    if existing is not None:
        identifier, bundle_stem = existing
        print("\n:: This repository snapshot is already archived — nothing uploaded.")
        print(f"   Archived repository: https://archive.org/details/{identifier}")
        print(
            f"   Git bundle:          https://archive.org/download/{identifier}/{bundle_stem}.bundle"
        )
        return

    # Print a human-friendly summary with direct links to the archived item.
    print("\n:: Upload FINISHED.")
    print(f"   Identifier:          {meta['title']}")
    print(f"   Archived repository: https://archive.org/details/{identifier}")
    print(
        f"   Git bundle:          https://archive.org/download/{identifier}/{bundle_stem}.bundle"
    )


if __name__ == "__main__":
    main()
