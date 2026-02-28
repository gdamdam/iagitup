#!/usr/bin/env python3
"""iagitup - Core logic for archiving GitHub repositories to the Internet Archive."""

__author__ = "Giovanni Damiola"
__copyright__ = "Copyright 2018-2026, Giovanni Damiola"
__main_name__ = "iagitup"
__license__ = "GPLv3"
__version__ = "v3.1.0"

import configparser
import logging
import os
import shutil
import subprocess
import tempfile
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

    Args:
        url: GitHub repository URL (with or without trailing slash / .git suffix).

    Returns:
        Tuple of (owner, repo_name).

    Raises:
        RepoDownloadError: If the URL cannot be parsed.
    """
    parsed = urlparse(url.rstrip("/"))
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise RepoDownloadError(f"Invalid GitHub URL: {url}")
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def _github_headers() -> dict[str, str]:
    """Build request headers, including auth token if available via GITHUB_TOKEN."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def repo_download(github_repo_url: str) -> tuple[dict, Path]:
    """Download a GitHub repository to a local temp directory.

    Args:
        github_repo_url: The GitHub repository URL.

    Returns:
        Tuple of (github_api_metadata, local_repo_path).

    Raises:
        RepoDownloadError: If the API call or clone fails.
    """
    owner, repo_name = _parse_github_url(github_repo_url)
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}"

    resp = requests.get(api_url, headers=_github_headers(), timeout=30)
    if resp.status_code != 200:
        raise RepoDownloadError(
            f"GitHub API returned {resp.status_code} for {github_repo_url}"
        )

    gh_repo_data: dict = resp.json()
    download_dir = Path(tempfile.mkdtemp())
    repo_folder = download_dir / repo_name

    log.info(f"Cloning {gh_repo_data['clone_url']} ...")
    try:
        git.Git().clone(gh_repo_data["clone_url"], str(repo_folder))
    except git.GitCommandError as exc:
        shutil.rmtree(download_dir, ignore_errors=True)
        raise RepoDownloadError(
            f"Failed to clone {github_repo_url}: {exc}"
        ) from exc

    return gh_repo_data, repo_folder


def _download_wiki(gh_repo_data: dict, dest_dir: Path) -> Path | None:
    """Clone the repository wiki if one exists.

    Args:
        gh_repo_data: GitHub API metadata dict.
        dest_dir: Parent directory in which to place the wiki clone.

    Returns:
        Path to the cloned wiki folder, or None if unavailable.
    """
    if not gh_repo_data.get("has_wiki"):
        return None

    wiki_url = gh_repo_data["html_url"] + ".wiki.git"
    wiki_folder = dest_dir / "wiki"
    try:
        git.Git().clone(wiki_url, str(wiki_folder))
        log.info("Wiki cloned successfully.")
        return wiki_folder
    except git.GitCommandError:
        log.info("Wiki not available or empty — skipping.")
        return None


# ---------------------------------------------------------------------------
# README / description
# ---------------------------------------------------------------------------

def get_description_from_readme(repo_folder: Path) -> str:
    """Return an HTML string from the repo's README, or empty string if absent.

    Checks for README.md (case-insensitive variants) then readme.txt.

    Args:
        repo_folder: Path to the local repository.

    Returns:
        HTML string.
    """
    for name in ("README.md", "readme.md", "Readme.md"):
        readme = repo_folder / name
        if readme.exists():
            return markdown_path(str(readme)).replace("\n", "")

    txt = repo_folder / "readme.txt"
    if txt.exists():
        return " ".join(txt.read_text(encoding="utf-8").splitlines())

    return ""


# ---------------------------------------------------------------------------
# Bundle creation
# ---------------------------------------------------------------------------

def create_bundle(repo_folder: Path, bundle_name: str) -> Path:
    """Create a git bundle of all refs in the repository.

    Args:
        repo_folder: Path to the local git repository.
        bundle_name: Stem name for the bundle file (no extension).

    Returns:
        Path to the created .bundle file.

    Raises:
        BundleError: If the repository folder is missing or git fails.
    """
    if not repo_folder.exists():
        raise BundleError(f"Repository directory does not exist: {repo_folder}")

    bundle_path = repo_folder / f"{bundle_name}.bundle"
    try:
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

    Args:
        gh_repo_folder: Path to the locally cloned repository.
        gh_repo_data: Metadata dict from the GitHub API.
        s3_access: Internet Archive S3 access key.
        s3_secret: Internet Archive S3 secret key.
        custom_meta: Optional extra metadata fields to merge into the IA item.

    Returns:
        Tuple of (ia_item_identifier, metadata_dict, bundle_filename_stem).

    Raises:
        UploadError: If bundle creation or upload fails.
    """
    pushed = datetime.strptime(gh_repo_data["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")
    pushed_date = pushed.strftime("%Y-%m-%d_%H-%M-%S")
    raw_pushed_date = pushed.strftime("%Y-%m-%d %H:%M:%S")
    date = pushed.strftime("%Y-%m-%d")
    year = pushed.year

    repo_name = gh_repo_data["full_name"].replace("/", "-")
    original_url = gh_repo_data["html_url"]
    bundle_stem = f"{repo_name}_-_{pushed_date}"

    owner_url = gh_repo_data["owner"]["html_url"]
    owner_name = gh_repo_data["owner"]["login"]

    # Download avatar as cover image
    cover_path = gh_repo_folder / "cover.jpg"
    avatar_url = gh_repo_data["owner"]["avatar_url"]
    try:
        pic = requests.get(avatar_url, stream=True, timeout=30)
        pic.raise_for_status()
        pic.raw.decode_content = True
        with cover_path.open("wb") as fh:
            shutil.copyfileobj(pic.raw, fh)
    except requests.RequestException as exc:
        log.warning(f"Could not download avatar: {exc}")
        cover_path = None  # type: ignore[assignment]

    # Build restore instructions
    ia_bundle_url = (
        f"https://archive.org/download/github.com-{bundle_stem}/{bundle_stem}.bundle"
    )
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

    # Wiki (implements TODO items)
    wiki_bundle_path: Path | None = None
    wiki_folder = _download_wiki(gh_repo_data, gh_repo_folder.parent)
    if wiki_folder:
        wiki_stem = f"{bundle_stem}_wiki"
        try:
            wiki_bundle_path = create_bundle(wiki_folder, wiki_stem)
            wiki_url = (
                f"https://archive.org/download/github.com-{bundle_stem}/{wiki_stem}.bundle"
            )
            description += (
                f'<br/><br/>Wiki bundle: <a href="{wiki_url}">{wiki_stem}.bundle</a>'
            )
            log.info("Wiki bundle created.")
        except BundleError as exc:
            log.warning(f"Could not create wiki bundle: {exc}")

    uploader_tag = f"{__main_name__}-{__version__}"
    itemname = f"github.com-{repo_name}_-_{pushed_date}"

    meta: dict = dict(
        mediatype="software",
        creator=owner_name,
        collection="open_source_software",
        title=itemname,
        year=year,
        date=date,
        subject="GitHub;code;software;git",
        uploaded_with=uploader_tag,
        originalurl=original_url,
        pushed_date=raw_pushed_date,
        description=description,
    )

    if custom_meta:
        meta.update(custom_meta)

    # Create main bundle
    try:
        bundle_file = create_bundle(gh_repo_folder, bundle_stem)
    except BundleError as exc:
        raise UploadError(f"Bundle creation failed: {exc}") from exc

    # Upload to Internet Archive
    try:
        log.info(f"Creating Internet Archive item: {itemname}")
        session = internetarchive.get_session(
            config={"s3": {"access": s3_access, "secret": s3_secret}}
        )
        item = session.get_item(itemname)

        if item.exists:
            log.warning("This repository appears to already be archived.")
            log.info(f"Archived URL:    https://archive.org/details/{itemname}")
            log.info(f"Bundle URL:      {ia_bundle_url}")
            return itemname, meta, bundle_stem

        log.info(f"Uploading bundle: {bundle_file.name}")
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

    If no config file is found, prompts the user to run ``ia configure``.

    Returns:
        Tuple of (s3_access_key, s3_secret_key).

    Raises:
        CredentialsError: If credentials cannot be obtained.
    """
    candidates = [
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
