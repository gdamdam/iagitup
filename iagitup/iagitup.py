#!/usr/bin/env python3
"""iagitup - Core logic for archiving git repositories to the Internet Archive.

This module handles the full archival pipeline:
  1. Parsing and validating repository URLs (GitHub, GitLab, Bitbucket, etc.)
  2. Fetching repository metadata (GitHub API for github.com, git history for others)
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
__version__ = "3.4.0"

import configparser
import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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


class LfsError(IagitupError):
    """Raised when a Git LFS operation fails."""


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

# Maps hostnames to human-readable labels used in IA subject tags.
# Unknown hostnames fall through to the raw hostname string.
_PLATFORM_LABELS = {
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "bitbucket.org": "Bitbucket",
    "codeberg.org": "Codeberg",
}


def _platform_label(hostname: str) -> str:
    """Return a human-readable label for a git hosting platform."""
    return _PLATFORM_LABELS.get(hostname, hostname)


def _parse_repo_url(url: str) -> tuple[str, str, str]:
    """Parse a git repository URL and return (owner, repo_name, hostname).

    Handles trailing slashes and .git suffixes gracefully.

    Args:
        url: Git repository URL, e.g. https://github.com/owner/repo.git

    Returns:
        Tuple of (owner, repo_name, hostname).

    Raises:
        RepoDownloadError: If the URL path does not contain both owner and repo.
    """
    parsed = urlparse(url.rstrip("/"))
    hostname = parsed.hostname or ""
    # Filter empty strings that arise from leading/trailing/double slashes.
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise RepoDownloadError(f"Invalid repository URL: {url}")
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo, hostname


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
# Generic (non-GitHub) metadata
# ---------------------------------------------------------------------------

def _build_repo_data_from_clone(
    url: str, owner: str, repo: str, hostname: str, repo_folder: Path,
) -> dict:
    """Build a metadata dict from a locally cloned repository.

    Used for non-GitHub platforms where no API metadata is available.
    Extracts the last commit date from the git history and constructs
    a dict that mirrors the shape of a GitHub API response.

    Args:
        url: Original repository URL.
        owner: Repository owner/namespace.
        repo: Repository name.
        hostname: Git hosting platform hostname.
        repo_folder: Path to the locally cloned repository.

    Returns:
        Metadata dict with the same keys used by the GitHub code path.
    """
    # Get the last commit date from git history.
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI"],
            cwd=repo_folder,
            capture_output=True,
            text=True,
            check=True,
        )
        raw_date = result.stdout.strip()
        # Parse the ISO 8601 date and convert to UTC.
        local_dt = datetime.fromisoformat(raw_date)
        utc_dt = local_dt.astimezone(timezone.utc)
        pushed_at = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (subprocess.CalledProcessError, ValueError):
        # Empty repo or unparseable date — fall back to current UTC time.
        pushed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Strip .git suffix so html_url is a clean browser-friendly link.
    html_url = url.removesuffix(".git").rstrip("/")
    owner_url = f"https://{hostname}/{owner}"

    # Return a dict that mirrors the shape of a GitHub API response so that
    # upload_ia() can treat both code paths identically.
    return {
        "clone_url": url,                  # used by repo_download for cloning
        "full_name": f"{owner}/{repo}",    # "owner/repo" — same as GitHub
        "html_url": html_url,              # browser URL for the repo
        "pushed_at": pushed_at,            # UTC timestamp of last commit
        "description": "",                 # no API description available
        "owner": {
            "login": owner,
            "html_url": owner_url,
            "avatar_url": None,            # no avatar without a platform API
        },
        "has_wiki": False,                 # wiki detection is GitHub-only
        "_platform": hostname,             # identifies the hosting platform
    }


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def repo_download(repo_url: str) -> tuple[dict, Path]:
    """Download a git repository to a local temporary directory.

    For GitHub URLs, metadata is fetched from the GitHub API.  For all other
    platforms the repository is cloned directly and metadata is extracted from
    the local git history via ``_build_repo_data_from_clone``.

    The caller is responsible for cleaning up the *parent* of the returned
    path (i.e. ``repo_folder.parent``), which is the mkdtemp root and may
    also contain a ``wiki/`` subdirectory created by ``_download_wiki``.

    Args:
        repo_url: The git repository URL.

    Returns:
        Tuple of (metadata_dict, local_repo_path).

    Raises:
        RepoDownloadError: If the API call / clone fails.
    """
    owner, repo_name, hostname = _parse_repo_url(repo_url)

    if hostname == "github.com":
        # --- GitHub path: rich API metadata ---
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}"

        resp = requests.get(api_url, headers=_github_headers(), timeout=30)
        if resp.status_code != 200:
            raise RepoDownloadError(
                f"GitHub API returned {resp.status_code} for {repo_url}"
            )

        repo_data: dict = resp.json()
        # Inject platform marker so upload_ia() can build platform-aware
        # identifiers and subject tags from a single code path.
        repo_data["_platform"] = "github.com"
        clone_url = repo_data["clone_url"]
    else:
        # --- Generic path: clone directly, extract metadata from git history ---
        # We don't call any platform API; metadata is built after the clone
        # completes via _build_repo_data_from_clone().
        clone_url = repo_url
        repo_data = None

    # mkdtemp creates an isolated directory; the wiki (if any) will live
    # alongside the repo clone inside this same root.
    download_dir = Path(tempfile.mkdtemp())
    repo_folder = download_dir / repo_name

    log.info(f"Cloning {clone_url} ...")
    try:
        # Use subprocess directly instead of GitPython so that git's
        # transfer progress (counting, compressing, receiving objects)
        # is streamed to stderr in real time.  The --progress flag forces
        # progress output even when stderr is not a TTY.
        subprocess.check_call(
            ["git", "clone", "--progress", clone_url, str(repo_folder)],
        )
    except subprocess.CalledProcessError as exc:
        # Clean the whole temp dir on failure so nothing leaks.
        shutil.rmtree(download_dir, ignore_errors=True)
        raise RepoDownloadError(
            f"Failed to clone {repo_url}: {exc}"
        ) from exc
    except KeyboardInterrupt:
        # User pressed Ctrl+C during clone — clean up before re-raising.
        shutil.rmtree(download_dir, ignore_errors=True)
        raise

    if repo_data is None:
        repo_data = _build_repo_data_from_clone(
            repo_url, owner, repo_name, hostname, repo_folder,
        )

    return repo_data, repo_folder


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
# Git LFS helpers
# ---------------------------------------------------------------------------

def _is_lfs_installed() -> bool:
    """Return True if the ``git-lfs`` binary is on ``$PATH``."""
    return shutil.which("git-lfs") is not None


def _detect_lfs(repo_folder: Path) -> bool:
    """Return True if the repo uses Git LFS (has ``filter=lfs`` in ``.gitattributes``)."""
    gitattributes = repo_folder / ".gitattributes"
    if not gitattributes.exists():
        return False
    try:
        content = gitattributes.read_text(encoding="utf-8", errors="replace")
        return "filter=lfs" in content
    except OSError:
        return False


def _fetch_and_archive_lfs(repo_folder: Path, archive_name: str) -> Path | None:
    """Fetch all LFS objects and create a tar.gz archive of ``.git/lfs/``.

    Returns the archive path on success, or ``None`` (with a warning) if
    git-lfs is not installed, the fetch fails, or the LFS directory is empty.
    """
    if not _is_lfs_installed():
        log.warning(
            "git-lfs is not installed — LFS objects will NOT be included in the archive. "
            "Install git-lfs to preserve large file content."
        )
        return None

    try:
        subprocess.check_call(
            ["git", "lfs", "fetch", "--all"],
            cwd=repo_folder,
        )
    except subprocess.CalledProcessError as exc:
        log.warning(f"git lfs fetch failed — LFS objects will be missing: {exc}")
        return None

    lfs_dir = repo_folder / ".git" / "lfs"
    if not lfs_dir.exists() or not any(lfs_dir.iterdir()):
        log.warning("LFS directory is empty after fetch — skipping LFS archive.")
        return None

    archive_path = repo_folder / f"{archive_name}_lfs.tar.gz"
    try:
        subprocess.check_call(
            ["tar", "-czf", str(archive_path), "-C", str(repo_folder / ".git"), "lfs"],
        )
    except subprocess.CalledProcessError as exc:
        log.warning(f"Failed to create LFS archive: {exc}")
        return None

    return archive_path


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_ia(
    repo_folder: Path,
    repo_data: dict,
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

    Note: callers must clean up ``repo_folder.parent`` (the mkdtemp root)
    after this function returns, as ``_download_wiki`` may have created a
    ``wiki/`` subdirectory alongside the main clone.

    Args:
        repo_folder: Path to the locally cloned repository.
        repo_data: Metadata dict (from GitHub API or ``_build_repo_data_from_clone``).
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        custom_meta: Optional extra metadata fields merged into the IA item
            (overrides defaults on key collision).

    Returns:
        Tuple of (ia_item_identifier, metadata_dict, bundle_filename_stem).

    Raises:
        UploadError: If bundle creation or any IA upload fails.
    """
    # --- Derive names and timestamps from the push date ---
    # Using pushed_at (not created_at) ensures a new IA item is created
    # whenever the repo receives new commits, while unchanged repos reuse
    # the same identifier and are de-duplicated via the early-exit below.
    pushed = datetime.strptime(repo_data["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")
    pushed_date = pushed.strftime("%Y-%m-%d_%H-%M-%S")     # for filenames / identifiers (no spaces)
    raw_pushed_date = pushed.strftime("%Y-%m-%d %H:%M:%S")  # human-readable, stored in IA metadata
    date = pushed.strftime("%Y-%m-%d")
    year = pushed.year

    # "owner/repo" -> "owner-repo" because IA identifiers cannot contain slashes.
    repo_name = repo_data["full_name"].replace("/", "-")
    original_url = repo_data["html_url"]
    # bundle_stem doubles as the IA item identifier suffix and the .bundle
    # filename, keeping both in sync for predictable download URLs.
    bundle_stem = f"{repo_name}_-_{pushed_date}"
    # IA identifier format: "<platform>-owner-repo_-_YYYY-MM-DD_HH-MM-SS"
    # Default to "github.com" for backward compatibility with existing dicts
    # (e.g. from archive_watchlist) that don't include "_platform".
    platform = repo_data.get("_platform", "github.com")
    itemname = f"{platform}-{repo_name}_-_{pushed_date}"

    owner_url = repo_data["owner"]["html_url"]
    owner_name = repo_data["owner"]["login"]

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
    # Non-GitHub platforms have avatar_url=None; skip the download to avoid
    # passing None to requests.get().
    avatar_url = repo_data["owner"].get("avatar_url")
    with ThreadPoolExecutor(max_workers=2) as pool:
        if avatar_url:
            avatar_future = pool.submit(
                _download_avatar,
                avatar_url,
                repo_folder / "cover.jpg",
            )
        else:
            avatar_future = None
        wiki_future = pool.submit(
            _download_wiki,
            repo_data,
            repo_folder.parent,  # wiki/ lands next to the main clone
        )
        cover_path = avatar_future.result() if avatar_future else None
        wiki_folder = wiki_future.result()

    # --- Detect and fetch LFS objects ---
    has_lfs = _detect_lfs(repo_folder)
    lfs_archive_path: Path | None = None
    if has_lfs:
        log.info("Git LFS detected — fetching LFS objects ...")
        lfs_archive_path = _fetch_and_archive_lfs(repo_folder, bundle_stem)

    # --- Build HTML restore instructions ---
    restore_snippet = (
        f'<pre><code>wget {ia_bundle_url}</code></pre>'
        f"and run: <pre><code>git clone {bundle_stem}.bundle</code></pre>"
    )
    if lfs_archive_path:
        lfs_url = f"https://archive.org/download/{itemname}/{bundle_stem}_lfs.tar.gz"
        restore_snippet += (
            f"<br/><br/>This repository uses Git LFS. To restore LFS objects:"
            f"<pre><code>cd {repo_data['full_name'].split('/')[-1]}\n"
            f"wget {lfs_url}\n"
            f"mkdir -p .git/lfs\n"
            f"tar -xzf {bundle_stem}_lfs.tar.gz -C .git/\n"
            f"git lfs install\n"
            f"git lfs checkout</code></pre>"
        )

    description = (
        f"<br/>{repo_data.get('description', '')}<br/><br/>"
        f"{get_description_from_readme(repo_folder)}<br/>"
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
        subject=f"{_platform_label(platform)};code;software;git",
        uploaded_with=uploader_tag,
        originalurl=original_url,
        pushed_date=raw_pushed_date,
        description=description,
    )
    if has_lfs:
        meta["has_lfs"] = "true"
    if custom_meta:
        meta.update(custom_meta)

    # --- Create the main git bundle ---
    try:
        bundle_file = create_bundle(repo_folder, bundle_stem)
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

        if lfs_archive_path and lfs_archive_path.exists():
            log.info("Uploading LFS archive ...")
            item.upload(
                str(lfs_archive_path),
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
