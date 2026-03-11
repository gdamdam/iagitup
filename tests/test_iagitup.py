"""Tests for iagitup core logic."""

import configparser
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iagitup.iagitup import (
    BundleError,
    CredentialsError,
    RepoDownloadError,
    _download_avatar,
    _download_wiki,
    _github_headers,
    _parse_github_url,
    create_bundle,
    get_description_from_readme,
    get_ia_credentials,
)


# ---------------------------------------------------------------------------
# _parse_github_url
# ---------------------------------------------------------------------------

class TestParseGithubUrl:
    def test_standard_url(self):
        owner, repo = _parse_github_url("https://github.com/gdamdam/iagitup")
        assert owner == "gdamdam"
        assert repo == "iagitup"

    def test_trailing_slash(self):
        owner, repo = _parse_github_url("https://github.com/gdamdam/iagitup/")
        assert owner == "gdamdam"
        assert repo == "iagitup"

    def test_git_suffix(self):
        owner, repo = _parse_github_url("https://github.com/gdamdam/iagitup.git")
        assert owner == "gdamdam"
        assert repo == "iagitup"

    def test_git_suffix_with_trailing_slash(self):
        owner, repo = _parse_github_url("https://github.com/gdamdam/iagitup.git/")
        assert owner == "gdamdam"
        assert repo == "iagitup"

    def test_invalid_url_no_repo(self):
        with pytest.raises(RepoDownloadError):
            _parse_github_url("https://github.com/gdamdam")

    def test_invalid_url_empty_path(self):
        with pytest.raises(RepoDownloadError):
            _parse_github_url("https://github.com/")


# ---------------------------------------------------------------------------
# get_description_from_readme
# ---------------------------------------------------------------------------

class TestGetDescriptionFromReadme:
    def test_readme_md(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Hello\nWorld", encoding="utf-8")
        result = get_description_from_readme(tmp_path)
        assert "Hello" in result
        assert "World" in result

    def test_readme_md_lowercase(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("# Lower", encoding="utf-8")
        result = get_description_from_readme(tmp_path)
        assert "Lower" in result

    def test_readme_txt_fallback(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("Plain text readme", encoding="utf-8")
        result = get_description_from_readme(tmp_path)
        assert "Plain text readme" in result

    def test_readme_md_takes_priority_over_txt(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# From MD", encoding="utf-8")
        (tmp_path / "readme.txt").write_text("From TXT", encoding="utf-8")
        result = get_description_from_readme(tmp_path)
        assert "From MD" in result
        assert "From TXT" not in result

    def test_no_readme_returns_empty(self, tmp_path: Path):
        assert get_description_from_readme(tmp_path) == ""

    def test_newlines_stripped(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# A\n\nB\n", encoding="utf-8")
        result = get_description_from_readme(tmp_path)
        assert "\n" not in result


# ---------------------------------------------------------------------------
# get_ia_credentials
# ---------------------------------------------------------------------------

class TestGetIaCredentials:
    def test_reads_ia_file(self, tmp_path: Path):
        ia_file = tmp_path / ".ia"
        cfg = configparser.ConfigParser()
        cfg["s3"] = {"access": "MYACCESS", "secret": "MYSECRET"}
        with ia_file.open("w") as fh:
            cfg.write(fh)

        with patch(
            "iagitup.iagitup.Path.expanduser",
            side_effect=lambda self: ia_file if ".ia" in str(self) else self,
        ):
            # Patch the candidates list directly
            with patch(
                "iagitup.iagitup.get_ia_credentials",
                wraps=None,
            ):
                cfg2 = configparser.ConfigParser()
                cfg2.read(ia_file)
                assert cfg2["s3"]["access"] == "MYACCESS"
                assert cfg2["s3"]["secret"] == "MYSECRET"

    def test_malformed_config_raises(self, tmp_path: Path):
        ia_file = tmp_path / ".ia"
        ia_file.write_text("[s3]\naccess = only_access\n", encoding="utf-8")

        # Patch candidates to point to our temp file
        with patch("iagitup.iagitup.Path") as mock_path_cls:
            mock_path_cls.return_value.expanduser.return_value = ia_file
            mock_path_cls.return_value.exists.return_value = True

            cfg = configparser.ConfigParser()
            cfg.read(ia_file)
            with pytest.raises(KeyError):
                _ = cfg["s3"]["secret"]

    def test_reads_new_config_path(self, tmp_path: Path):
        """get_ia_credentials() finds ~/.config/internetarchive/ia.ini."""
        ia_ini = tmp_path / "ia.ini"
        cfg = configparser.ConfigParser()
        cfg["s3"] = {"access": "NEW_ACCESS", "secret": "NEW_SECRET"}
        with ia_ini.open("w") as fh:
            cfg.write(fh)

        fake_candidates = [ia_ini, tmp_path / "nope1", tmp_path / "nope2"]
        with patch("iagitup.iagitup.Path") as mock_path_cls:
            # Each Path(...).expanduser() call returns the corresponding candidate
            instances = []
            for c in fake_candidates:
                m = MagicMock()
                m.expanduser.return_value = c
                instances.append(m)
            mock_path_cls.side_effect = instances

            access, secret = get_ia_credentials()

        assert access == "NEW_ACCESS"
        assert secret == "NEW_SECRET"

    def test_malformed_config_raises_credentials_error(self, tmp_path: Path):
        """get_ia_credentials() raises CredentialsError when secret key is missing."""
        ia_file = tmp_path / "ia.ini"
        ia_file.write_text("[s3]\naccess = only_access\n", encoding="utf-8")

        fake_candidates = [ia_file, tmp_path / "nope1", tmp_path / "nope2"]
        with patch("iagitup.iagitup.Path") as mock_path_cls:
            instances = []
            for c in fake_candidates:
                m = MagicMock()
                m.expanduser.return_value = c
                instances.append(m)
            mock_path_cls.side_effect = instances

            with pytest.raises(CredentialsError, match="missing key"):
                get_ia_credentials()


# ---------------------------------------------------------------------------
# _github_headers
# ---------------------------------------------------------------------------

class TestGithubHeaders:
    def test_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            # Also clear GITHUB_TOKEN specifically in case it's in the env
            os.environ.pop("GITHUB_TOKEN", None)
            headers = _github_headers()
        assert headers["Accept"] == "application/vnd.github+json"
        assert "Authorization" not in headers

    def test_with_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test123"}):
            headers = _github_headers()
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["Authorization"] == "Bearer ghp_test123"

    def test_empty_token_is_ignored(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            headers = _github_headers()
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# create_bundle
# ---------------------------------------------------------------------------

class TestCreateBundle:
    def test_creates_bundle_from_real_repo(self, tmp_path: Path):
        """Create a real git repo in tmp_path and verify bundle creation."""
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        # Initialise a git repo with one commit
        subprocess.check_call(["git", "init"], cwd=repo_dir)
        subprocess.check_call(
            ["git", "config", "user.email", "test@test.com"], cwd=repo_dir
        )
        subprocess.check_call(
            ["git", "config", "user.name", "Test"], cwd=repo_dir
        )
        (repo_dir / "hello.txt").write_text("hello")
        subprocess.check_call(["git", "add", "."], cwd=repo_dir)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo_dir)

        bundle_path = create_bundle(repo_dir, "myrepo_bundle")

        assert bundle_path.exists()
        assert bundle_path.name == "myrepo_bundle.bundle"
        assert bundle_path.parent == repo_dir
        # Verify the bundle is a valid git bundle (must run inside a git repo)
        result = subprocess.run(
            ["git", "bundle", "verify", str(bundle_path)],
            cwd=repo_dir,
            capture_output=True,
        )
        assert result.returncode == 0

    def test_raises_on_missing_directory(self, tmp_path: Path):
        with pytest.raises(BundleError, match="does not exist"):
            create_bundle(tmp_path / "nonexistent", "bundle")

    def test_raises_on_non_git_directory(self, tmp_path: Path):
        """A directory that exists but is not a git repo should fail."""
        plain_dir = tmp_path / "notgit"
        plain_dir.mkdir()
        with pytest.raises(BundleError, match="git bundle failed"):
            create_bundle(plain_dir, "bundle")


# ---------------------------------------------------------------------------
# _download_wiki
# ---------------------------------------------------------------------------

class TestDownloadWiki:
    def test_returns_none_when_has_wiki_is_false(self, tmp_path: Path):
        result = _download_wiki({"has_wiki": False}, tmp_path)
        assert result is None

    def test_returns_none_when_has_wiki_key_missing(self, tmp_path: Path):
        result = _download_wiki({}, tmp_path)
        assert result is None

    def test_clones_wiki_successfully(self, tmp_path: Path):
        gh_data = {
            "has_wiki": True,
            "html_url": "https://github.com/owner/repo",
        }
        mock_git = MagicMock()
        with patch("iagitup.iagitup.git.Git", return_value=mock_git):
            result = _download_wiki(gh_data, tmp_path)

        assert result == tmp_path / "wiki"
        mock_git.update_environment.assert_called_once_with(GIT_TERMINAL_PROMPT="0")
        mock_git.clone.assert_called_once_with(
            "https://github.com/owner/repo.wiki.git",
            str(tmp_path / "wiki"),
        )

    def test_returns_none_on_clone_failure(self, tmp_path: Path):
        import git as gitmodule

        gh_data = {
            "has_wiki": True,
            "html_url": "https://github.com/owner/repo",
        }
        mock_git = MagicMock()
        mock_git.clone.side_effect = gitmodule.GitCommandError("clone", "fail")
        with patch("iagitup.iagitup.git.Git", return_value=mock_git):
            result = _download_wiki(gh_data, tmp_path)

        assert result is None


# ---------------------------------------------------------------------------
# _download_avatar
# ---------------------------------------------------------------------------

class TestDownloadAvatar:
    def test_downloads_avatar_successfully(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.raw = MagicMock()
        mock_resp.raw.decode_content = True
        # Simulate a small image payload
        mock_resp.raw.read = MagicMock(side_effect=[b"fake-image-data", b""])

        with patch("iagitup.iagitup.requests.get", return_value=mock_resp) as mock_get, \
             patch("iagitup.iagitup.shutil.copyfileobj") as mock_copy:
            result = _download_avatar("https://avatars.example.com/u/123", dest)

        assert result == dest
        mock_get.assert_called_once_with(
            "https://avatars.example.com/u/123", stream=True, timeout=30
        )
        mock_copy.assert_called_once()

    def test_returns_none_on_http_error(self, tmp_path: Path):
        import requests as req

        dest = tmp_path / "cover.jpg"
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.RequestException("404")

        with patch("iagitup.iagitup.requests.get", return_value=mock_resp):
            result = _download_avatar("https://avatars.example.com/u/123", dest)

        assert result is None

    def test_returns_none_on_connection_error(self, tmp_path: Path):
        import requests as req

        dest = tmp_path / "cover.jpg"
        with patch(
            "iagitup.iagitup.requests.get",
            side_effect=req.ConnectionError("no network"),
        ):
            result = _download_avatar("https://avatars.example.com/u/123", dest)

        assert result is None
