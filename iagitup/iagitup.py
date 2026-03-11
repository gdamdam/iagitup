#!/usr/bin/env python3
"""iagitup - Core logic for archiving GitHub repositories to the Internet Archive.

This module handles the full archival pipeline:
  1. Parsing and validating GitHub URLs
  2. Fetching repository metadata from the GitHub API
  3. Cloning repos (and optional wikis) to temporary directories
  4. Creating ``git bundle`` snapshots (single-file, fully portable archives)
  5. Uploading bundles + metadata to the Internet Archive via the S3-like API
  6. Reading IA credentials from the ``ia`` CLI config files

All public functions raise subclasses of ``IagitupError`` on failure so that
callers can handle errors uniformly.
"""

__author__ = "Giovanni Damiola"
__copyright__ = "Copyright 2018-2026, Giovanni Damiola"
__main_name__ = "iagitup"
__license__ = "GPLv3"
__version__ = "v3.1.2"

import configparser
import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import git
import internetarchive
import requests
from markdown2 import markdown_path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IagitupError(Exception):
    """Base exception for iagitup errors."""


class RepoDownloadError(IagitupError):
    """Raised when a repository download fails."""


class BundleError(IagitupError):
    """Raised when git bundle creation fails."""


class UploadError(IagitupError):
    """Raised when an upload to the Internet Archive fails."""


class CredentialsError(IagitupError):
    """Raised when Internet Archive credentials are missing or invalid."""


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _parse_github_url(url: str) -> tuple[str, str]:
    """Parse a GitHub URL and return (owner, repo_name).

    Handles trailing slashes and .git suffixes gracefully.

    Args:
        url: GitHub repository URL, e.g. https://github.com/owner/repo.git

    Returns:
        Tuple of (owner, repo_name).

    Raises:
        RepoDownloadError: If the URL path does not contain both owner and repo.
    """
    parsed = urlparse(url.rstrip("/"))
    # Filter empty strings that arise from leading/trailing/double slashes.
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise RepoDownloadError(f"Invalid GitHub URL: {url}")
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def _github_headers() -> dict[str, str]:
    """Return HTTP headers for GitHub API requests.

    Includes an Authorization header when the GITHUB_TOKEN environment
    variable is set, raising the rate limit from 60 to 5 000 req/hour.

    Returns:
        Dict of HTTP headers.
    """
    # Request the v3 JSON format explicitly; without this header GitHub may
    # return different representations for some endpoints.
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def repo_download(github_repo_url: str) -> tuple[dict, Path]:
    """Download a GitHub repository to a local temporary directory.

    The caller is responsible for cleaning up the *parent* of the returned
    path (i.e. ``repo_folder.parent``), which is the mkdtemp root and may
    also contain a ``wiki/`` subdirectory created by ``_download_wiki``.

    Args:
        github_repo_url: The GitHub repository URL.

    Returns:
        Tuple of (github_api_metadata_dict, local_repo_path).

    Raises:
        RepoDownloadError: If the GitHub API call fails or the clone fails.
    """
    owner, repo_name = _parse_github_url(github_repo_url)
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}"

    resp = requests.get(api_url, headers=_github_headers(), timeout=30)
    if resp.status_code != 200:
        raise RepoDownloadError(
            f"GitHub API returned {resp.status_code} for {github_repo_url}"
        )

    gh_repo_data: dict = resp.json()

    # mkdtemp creates an isolated directory; the wiki (if any) will live
    # alongside the repo clone inside this same root.
    download_dir = Path(tempfile.mkdtemp())
    repo_folder = download_dir / repo_name

    log.info(f"Cloning {gh_repo_data['clone_url']} ...")
    try:
        git.Git().clone(gh_repo_data["clone_url"], str(repo_folder))
    except git.GitCommandError as exc:
        # Clean the whole temp dir on failure so nothing leaks.
        shutil.rmtree(download_dir, ignore_errors=True)
        raise RepoDownloadError(
            f"Failed to clone {github_repo_url}: {exc}"
        ) from exc

    return gh_repo_data, repo_folder


def _download_avatar(avatar_url: str, dest_path: Path) -> Path | None:
    """Download the repository owner's avatar image to *dest_path*.

    Args:
        avatar_url: URL of the avatar image.
        dest_path: Filesystem path to write the image to.

    Returns:
        *dest_path* on success, ``None`` if the download fails.
    """
    try:
        pic = requests.get(avatar_url, stream=True, timeout=30)
        pic.raise_for_status()
        # Let urllib3 handle Content-Encoding decompression transparently.
        pic.raw.decode_content = True
        with dest_path.open("wb") as fh:
            shutil.copyfileobj(pic.raw, fh)
        return dest_path
    except requests.RequestException as exc:
        log.warning(f"Could not download avatar: {exc}")
        return None


def _download_wiki(gh_repo_data: dict, dest_dir: Path) -> Path | None:
    """Clone the repository wiki into *dest_dir*/wiki, if one exists.

    GitHub sets ``has_wiki: true`` even for repos whose wiki has never been
    edited, so a failed clone is silently ignored rather than treated as an
    error.

    Args:
        gh_repo_data: GitHub API metadata dict for the repository.
        dest_dir: Parent directory in which the ``wiki/`` clone will be placed.

    Returns:
        Path to the cloned wiki folder, or ``None`` if unavailable.
    """
    if not gh_repo_data.get("has_wiki"):
        return None

    wiki_url = gh_repo_data["html_url"] + ".wiki.git"
    wiki_folder = dest_dir / "wiki"
    try:
        g = git.Git()
        # Disable interactive credential prompts so the clone fails fast
        # instead of hanging when the wiki requires authentication.
        g.update_environment(GIT_TERMINAL_PROMPT="0")
        g.clone(wiki_url, str(wiki_folder))
        log.info("Wiki cloned successfully.")
        return wiki_folder
    except git.GitCommandError:
        # Wiki flag is true but the wiki may be empty or disabled.
        log.info("Wiki not available or empty — skipping.")
        return None


# ---------------------------------------------------------------------------
# README / description
# ---------------------------------------------------------------------------

def get_description_from_readme(repo_folder: Path) -> str:
    """Return an HTML string built from the repository README, or ``""`` if absent.

    Checks for ``README.md`` (and common case variants) then falls back to
    ``readme.txt``. The Markdown is converted to HTML via markdown2.

    Args:
        repo_folder: Path to the locally cloned repository.

    Returns:
        HTML string with newlines stripped, or empty string.
    """
    # Try the three most common casing conventions; a case-insensitive glob
    # would be cleaner but may behave unexpectedly on case-sensitive filesystems.
    for name in ("README.md", "readme.md", "Readme.md"):
        readme = repo_folder / name
        if readme.exists():
            # Strip newlines so the HTML can be safely embedded in IA metadata
            # (which is a single-line XML value).
            return markdown_path(str(readme)).replace("\n", "")

    txt = repo_folder / "readme.txt"
    if txt.exists():
        return " ".join(txt.read_text(encoding="utf-8").splitlines())

    return ""


# ---------------------------------------------------------------------------
# Bundle creation
# ---------------------------------------------------------------------------

def create_bundle(repo_folder: Path, bundle_name: str) -> Path:
    """Create a ``git bundle`` containing all refs of *repo_folder*.

    The bundle file is written inside *repo_folder* itself so it is
    automatically included in any cleanup of that directory.

    Args:
        repo_folder: Path to the local git repository.
        bundle_name: Stem name for the bundle file (no ``.bundle`` extension).

    Returns:
        Path to the created ``.bundle`` file.

    Raises:
        BundleError: If *repo_folder* does not exist or ``git bundle`` fails.
    """
    if not repo_folder.exists():
        raise BundleError(f"Repository directory does not exist: {repo_folder}")

    bundle_path = repo_folder / f"{bundle_name}.bundle"
    try:
        # --all bundles every ref (branches, tags, etc.) into a single file
        # that can later be cloned with ``git clone <file>.bundle``.
        subprocess.check_call(
            ["git", "bundle", "create", bundle_path.name, "--all"],
            cwd=repo_folder,
        )
    except subprocess.CalledProcessError as exc:
        raise BundleError(f"git bundle failed in {repo_folder}: {exc}") from exc

    return bundle_path


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_ia(
    gh_repo_folder: Path,
    gh_repo_data: dict,
    s3_access: str,
    s3_secret: str,
    custom_meta: dict | None = None,
) -> tuple[str, dict, str]:
    """Upload the repository (and optional wiki) bundle to the Internet Archive.

    Workflow
    --------
    1. Compute the IA item identifier from the repo name and last-push date.
    2. **Early-exit**: check whether the item already exists on IA *before*
       doing any heavy work — avoids redundant clones and uploads.
    3. Download the owner avatar and clone the wiki **concurrently** (both
       are pure network I/O with no dependency on each other).
    4. Build the HTML description (now that wiki status is known).
    5. Create git bundles for the repo and, if available, the wiki.
    6. Upload all files to the IA item sequentially.

    Note: callers must clean up ``gh_repo_folder.parent`` (the mkdtemp root)
    after this function returns, as ``_download_wiki`` may have created a
    ``wiki/`` subdirectory alongside the main clone.

    Args:
        gh_repo_folder: Path to the locally cloned repository.
        gh_repo_data: Metadata dict from the GitHub API.
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        custom_meta: Optional extra metadata fields merged into the IA item
            (overrides defaults on key collision).

    Returns:
        Tuple of (ia_item_identifier, metadata_dict, bundle_filename_stem).

    Raises:
        UploadError: If bundle creation or any IA upload fails.
    """
    # --- Derive names and timestamps from the GitHub push date ---
    # Using pushed_at (not created_at) ensures a new IA item is created
    # whenever the repo receives new commits, while unchanged repos reuse
    # the same identifier and are de-duplicated via the early-exit below.
    pushed = datetime.strptime(gh_repo_data["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")
    pushed_date = pushed.strftime("%Y-%m-%d_%H-%M-%S")     # for filenames / identifiers (no spaces)
    raw_pushed_date = pushed.strftime("%Y-%m-%d %H:%M:%S")  # human-readable, stored in IA metadata
    date = pushed.strftime("%Y-%m-%d")
    year = pushed.year

    # "owner/repo" -> "owner-repo" because IA identifiers cannot contain slashes.
    repo_name = gh_repo_data["full_name"].replace("/", "-")
    original_url = gh_repo_data["html_url"]
    # bundle_stem doubles as the IA item identifier suffix and the .bundle
    # filename, keeping both in sync for predictable download URLs.
    bundle_stem = f"{repo_name}_-_{pushed_date}"
    # IA identifier format: "github.com-owner-repo_-_YYYY-MM-DD_HH-MM-SS"
    itemname = f"github.com-{repo_name}_-_{pushed_date}"

    owner_url = gh_repo_data["owner"]["html_url"]
    owner_name = gh_repo_data["owner"]["login"]

    # --- Early-exit: avoid redundant work if IA already has this snapshot ---
    # The identifier encodes pushed_at, so a new push creates a new item and
    # is always re-archived; only the exact same snapshot is skipped.
    ia_bundle_url = (
        f"https://archive.org/download/{itemname}/{bundle_stem}.bundle"
    )
    try:
        session = internetarchive.get_session(
            config={"s3": {"access": s3_access, "secret": s3_secret}}
        )
        item = session.get_item(itemname)
    except Exception as exc:
        raise UploadError(f"Failed to connect to Internet Archive: {exc}") from exc

    if item.exists:
        log.warning("This repository snapshot is already archived.")
        log.info(f"Archived URL: https://archive.org/details/{itemname}")
        log.info(f"Bundle URL:   {ia_bundle_url}")
        return itemname, {"title": itemname}, bundle_stem

    # --- Concurrently download avatar + clone wiki (both are network I/O) ---
    cover_path: Path | None = None
    wiki_folder: Path | None = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        avatar_future = pool.submit(
            _download_avatar,
            gh_repo_data["owner"]["avatar_url"],
            gh_repo_folder / "cover.jpg",
        )
        wiki_future = pool.submit(
            _download_wiki,
            gh_repo_data,
            gh_repo_folder.parent,  # wiki/ lands next to the main clone
        )
        cover_path = avatar_future.result()
        wiki_folder = wiki_future.result()

    # --- Build HTML restore instructions ---
    restore_snippet = (
        f'<pre><code>wget {ia_bundle_url}</code></pre>'
        f"and run: <pre><code>git clone {bundle_stem}.bundle</code></pre>"
    )

    description = (
        f"<br/>{gh_repo_data.get('description', '')}<br/><br/>"
        f"{get_description_from_readme(gh_repo_folder)}<br/>"
        f"{restore_snippet}<br/><br/>"
        f'Source: <a href="{original_url}">{original_url}</a><br/>'
        f'Uploader: <a href="{owner_url}">{owner_name}</a><br/>'
        f"Upload date: {date}"
    )

    # --- Optionally bundle the wiki and append a link to the description ---
    wiki_bundle_path: Path | None = None
    if wiki_folder:
        wiki_stem = f"{bundle_stem}_wiki"
        try:
            wiki_bundle_path = create_bundle(wiki_folder, wiki_stem)
            wiki_url = (
                f"https://archive.org/download/{itemname}/{wiki_stem}.bundle"
            )
            description += (
                f'<br/><br/>Wiki bundle: <a href="{wiki_url}">{wiki_stem}.bundle</a>'
            )
            log.info("Wiki bundle created.")
        except BundleError as exc:
            log.warning(f"Could not create wiki bundle: {exc}")

    # --- Assemble full IA metadata ---
    uploader_tag = f"{__main_name__}-{__version__}"
    meta: dict = dict(
        mediatype="software",
        creator=owner_name,
        collection="open_source_software",
        title=itemname,
        year=str(year),
        date=date,
        subject="GitHub;code;software;git",
        uploaded_with=uploader_tag,
        originalurl=original_url,
        pushed_date=raw_pushed_date,
        description=description,
    )
    if custom_meta:
        meta.update(custom_meta)

    # --- Create the main git bundle ---
    try:
        bundle_file = create_bundle(gh_repo_folder, bundle_stem)
    except BundleError as exc:
        raise UploadError(f"Bundle creation failed: {exc}") from exc

    # --- Upload all files to the IA item sequentially ---
    try:
        log.info(f"Uploading bundle: {bundle_file.name}")
        # retries and timeout are set deliberately high because IA uploads
        # can be slow and flaky; 9001 is effectively "keep trying".
        # delete=False keeps the local file so the caller can clean up the
        # entire temp dir at once; cover and wiki use delete=True since they
        # are auxiliary files we don't need to keep around.
        item.upload(
            str(bundle_file),
            metadata=meta,
            retries=9001,
            request_kwargs={"timeout": 9001},
            delete=False,
        )

        if cover_path and cover_path.exists():
            log.info("Uploading cover image ...")
            item.upload(
                str(cover_path),
                retries=9001,
                request_kwargs={"timeout": 9001},
                delete=True,
            )

        if wiki_bundle_path and wiki_bundle_path.exists():
            log.info("Uploading wiki bundle ...")
            item.upload(
                str(wiki_bundle_path),
                retries=9001,
                request_kwargs={"timeout": 9001},
                delete=True,
            )

    except Exception as exc:
        raise UploadError(f"Upload failed: {exc}") from exc

    return itemname, meta, bundle_stem


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def get_ia_credentials() -> tuple[str, str]:
    """Read Internet Archive S3 credentials from the local config file.

    Checks ``~/.ia`` then ``~/.config/ia.ini``. If neither exists the user
    is prompted to run ``ia configure`` interactively.

    Returns:
        Tuple of (s3_access_key, s3_secret_key).

    Raises:
        CredentialsError: If credentials cannot be found or the config is malformed.
    """
    # The ``ia`` CLI has changed its default config location over time.
    # Check all known paths in order of preference (newest convention first).
    candidates = [
        Path("~/.config/internetarchive/ia.ini").expanduser(),
        Path("~/.ia").expanduser(),
        Path("~/.config/ia.ini").expanduser(),
    ]
    config_file = next((p for p in candidates if p.exists()), None)

    if config_file is None:
        print(
            "\nWARNING: Internet Archive credentials not found.\n"
            "Register at https://archive.org/account/login.createaccount.php\n"
        )
        try:
            if subprocess.call(["ia", "configure"]) != 0:
                raise CredentialsError("'ia configure' did not complete successfully.")
        except FileNotFoundError as exc:
            raise CredentialsError(
                "Could not find the 'ia' command — is internetarchive installed?"
            ) from exc
        # Re-check after interactive configuration
        config_file = next((p for p in candidates if p.exists()), None)
        if config_file is None:
            raise CredentialsError("Config file still missing after running 'ia configure'.")

    config = configparser.ConfigParser()
    config.read(config_file)
    try:
        return config["s3"]["access"], config["s3"]["secret"]
    except KeyError as exc:
        raise CredentialsError(
            f"Malformed credentials file {config_file}, missing key: {exc}"
        ) from exc
