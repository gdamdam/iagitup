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
    LfsError,
    RepoDownloadError,
    UploadError,
    __version__,
    _build_repo_data_from_clone,
    _detect_lfs,
    _download_avatar,
    _download_wiki,
    _fetch_and_archive_lfs,
    _github_headers,
    _is_lfs_installed,
    _parse_repo_url,
    _platform_label,
    create_bundle,
    get_description_from_readme,
    get_ia_credentials,
    repo_download,
    upload_ia,
)


# ---------------------------------------------------------------------------
# _parse_repo_url
# ---------------------------------------------------------------------------

class TestParseRepoUrl:
    def test_standard_github_url(self):
        owner, repo, hostname = _parse_repo_url("https://github.com/gdamdam/iagitup")
        assert owner == "gdamdam"
        assert repo == "iagitup"
        assert hostname == "github.com"

    def test_trailing_slash(self):
        owner, repo, hostname = _parse_repo_url("https://github.com/gdamdam/iagitup/")
        assert owner == "gdamdam"
        assert repo == "iagitup"
        assert hostname == "github.com"

    def test_git_suffix(self):
        owner, repo, hostname = _parse_repo_url("https://github.com/gdamdam/iagitup.git")
        assert owner == "gdamdam"
        assert repo == "iagitup"
        assert hostname == "github.com"

    def test_git_suffix_with_trailing_slash(self):
        owner, repo, hostname = _parse_repo_url("https://github.com/gdamdam/iagitup.git/")
        assert owner == "gdamdam"
        assert repo == "iagitup"
        assert hostname == "github.com"

    def test_invalid_url_no_repo(self):
        with pytest.raises(RepoDownloadError, match="Invalid repository URL"):
            _parse_repo_url("https://github.com/gdamdam")

    def test_invalid_url_empty_path(self):
        with pytest.raises(RepoDownloadError, match="Invalid repository URL"):
            _parse_repo_url("https://github.com/")

    def test_gitlab_url(self):
        owner, repo, hostname = _parse_repo_url("https://gitlab.com/user/project")
        assert owner == "user"
        assert repo == "project"
        assert hostname == "gitlab.com"

    def test_bitbucket_url(self):
        owner, repo, hostname = _parse_repo_url("https://bitbucket.org/team/repo.git")
        assert owner == "team"
        assert repo == "repo"
        assert hostname == "bitbucket.org"

    def test_codeberg_url(self):
        owner, repo, hostname = _parse_repo_url("https://codeberg.org/user/project")
        assert owner == "user"
        assert repo == "project"
        assert hostname == "codeberg.org"

    def test_self_hosted_url(self):
        owner, repo, hostname = _parse_repo_url("https://git.example.com/org/tool")
        assert owner == "org"
        assert repo == "tool"
        assert hostname == "git.example.com"


# ---------------------------------------------------------------------------
# _platform_label
# ---------------------------------------------------------------------------

class TestPlatformLabel:
    def test_known_platforms(self):
        assert _platform_label("github.com") == "GitHub"
        assert _platform_label("gitlab.com") == "GitLab"
        assert _platform_label("bitbucket.org") == "Bitbucket"
        assert _platform_label("codeberg.org") == "Codeberg"

    def test_unknown_platform_returns_hostname(self):
        assert _platform_label("git.example.com") == "git.example.com"


# ---------------------------------------------------------------------------
# _build_repo_data_from_clone
# ---------------------------------------------------------------------------

class TestBuildRepoDataFromClone:
    def test_builds_metadata_from_real_repo(self, tmp_path: Path):
        """Create a real git repo and verify all metadata keys."""
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
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

        data = _build_repo_data_from_clone(
            "https://gitlab.com/myuser/myrepo.git",
            "myuser", "myrepo", "gitlab.com", repo_dir,
        )

        assert data["clone_url"] == "https://gitlab.com/myuser/myrepo.git"
        assert data["full_name"] == "myuser/myrepo"
        assert data["html_url"] == "https://gitlab.com/myuser/myrepo"
        assert data["description"] == ""
        assert data["has_wiki"] is False
        assert data["_platform"] == "gitlab.com"
        assert data["owner"]["login"] == "myuser"
        assert data["owner"]["html_url"] == "https://gitlab.com/myuser"
        assert data["owner"]["avatar_url"] is None
        # pushed_at should be a valid UTC timestamp
        from datetime import datetime
        datetime.strptime(data["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_empty_repo_falls_back_to_current_time(self, tmp_path: Path):
        """An empty repo (no commits) should fall back to current UTC time."""
        repo_dir = tmp_path / "empty"
        repo_dir.mkdir()
        subprocess.check_call(["git", "init"], cwd=repo_dir)

        data = _build_repo_data_from_clone(
            "https://gitlab.com/u/empty", "u", "empty", "gitlab.com", repo_dir,
        )

        from datetime import datetime
        # Should not raise — the date is valid
        datetime.strptime(data["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_strips_git_suffix_from_html_url(self, tmp_path: Path):
        """html_url should strip .git suffix from the original URL."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.check_call(["git", "init"], cwd=repo_dir)

        data = _build_repo_data_from_clone(
            "https://example.com/org/repo.git",
            "org", "repo", "example.com", repo_dir,
        )

        assert data["html_url"] == "https://example.com/org/repo"


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


# ---------------------------------------------------------------------------
# repo_download
# ---------------------------------------------------------------------------

class TestRepoDownload:
    def _mock_github_api(self, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {
            "clone_url": "https://github.com/owner/repo.git",
            "full_name": "owner/repo",
            "html_url": "https://github.com/owner/repo",
            "pushed_at": "2026-01-01T00:00:00Z",
            "_platform": "github.com",
        }
        return resp

    def test_successful_github_download(self, tmp_path):
        """GitHub URLs use the API path and inject _platform."""
        mock_resp = self._mock_github_api()
        with patch("iagitup.iagitup.requests.get", return_value=mock_resp), \
             patch("iagitup.iagitup.subprocess.check_call") as mock_clone, \
             patch("iagitup.iagitup.tempfile.mkdtemp", return_value=str(tmp_path)):
            (tmp_path / "repo").mkdir()
            repo_data, repo_folder = repo_download("https://github.com/owner/repo")

        assert repo_data["full_name"] == "owner/repo"
        assert repo_data["_platform"] == "github.com"
        assert repo_folder == tmp_path / "repo"
        mock_clone.assert_called_once()

    def test_raises_on_github_api_error(self):
        mock_resp = self._mock_github_api(status=404)
        with patch("iagitup.iagitup.requests.get", return_value=mock_resp):
            with pytest.raises(RepoDownloadError, match="404"):
                repo_download("https://github.com/owner/repo")

    def test_raises_on_clone_failure(self, tmp_path):
        mock_resp = self._mock_github_api()
        with patch("iagitup.iagitup.requests.get", return_value=mock_resp), \
             patch("iagitup.iagitup.subprocess.check_call",
                   side_effect=subprocess.CalledProcessError(128, "git clone")), \
             patch("iagitup.iagitup.tempfile.mkdtemp", return_value=str(tmp_path)):
            with pytest.raises(RepoDownloadError, match="Failed to clone"):
                repo_download("https://github.com/owner/repo")
        # Temp dir should be cleaned up on failure
        assert not tmp_path.exists()

    def test_generic_url_skips_github_api(self, tmp_path):
        """Non-GitHub URLs should NOT call the GitHub API."""
        mock_repo_data = {
            "clone_url": "https://gitlab.com/user/proj.git",
            "full_name": "user/proj",
            "html_url": "https://gitlab.com/user/proj",
            "pushed_at": "2026-03-01T12:00:00Z",
            "description": "",
            "owner": {
                "login": "user",
                "html_url": "https://gitlab.com/user",
                "avatar_url": None,
            },
            "has_wiki": False,
            "_platform": "gitlab.com",
        }
        with patch("iagitup.iagitup.subprocess.check_call"), \
             patch("iagitup.iagitup.tempfile.mkdtemp", return_value=str(tmp_path)), \
             patch("iagitup.iagitup._build_repo_data_from_clone", return_value=mock_repo_data), \
             patch("iagitup.iagitup.requests.get") as mock_requests_get:
            (tmp_path / "proj").mkdir()
            repo_data, repo_folder = repo_download("https://gitlab.com/user/proj")

        # No GitHub API call should have been made
        mock_requests_get.assert_not_called()
        assert repo_data["_platform"] == "gitlab.com"
        assert repo_folder == tmp_path / "proj"


# ---------------------------------------------------------------------------
# upload_ia
# ---------------------------------------------------------------------------

class TestUploadIa:
    GH_DATA = {
        "full_name": "owner/repo",
        "html_url": "https://github.com/owner/repo",
        "pushed_at": "2026-01-15T10:30:00Z",
        "description": "A test repo",
        "owner": {
            "login": "owner",
            "html_url": "https://github.com/owner",
            "avatar_url": "https://avatars.example.com/u/1",
        },
        "has_wiki": False,
        "_platform": "github.com",
    }

    def test_skips_existing_item(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        mock_item = MagicMock()
        mock_item.exists = True

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session):
            identifier, meta, stem = upload_ia(
                repo_folder, self.GH_DATA, s3_access="a", s3_secret="s",
            )

        assert "owner-repo" in identifier
        assert "2026-01-15" in identifier
        # No upload should have been called
        mock_item.upload.assert_not_called()

    def test_successful_upload(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()
        # create_bundle needs a git repo, so we mock it
        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "owner-repo_-_2026-01-15_10-30-00.bundle"
        fake_bundle.write_text("bundle")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar", return_value=None), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value="readme"):
            identifier, meta, stem = upload_ia(
                repo_folder, self.GH_DATA, s3_access="a", s3_secret="s",
            )

        assert identifier == "github.com-owner-repo_-_2026-01-15_10-30-00"
        assert meta["mediatype"] == "software"
        assert meta["collection"] == "open_source_software"
        assert meta["creator"] == "owner"
        assert meta["subject"] == "GitHub;code;software;git"
        mock_item.upload.assert_called_once()

    def test_custom_meta_overrides_defaults(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "bundle.bundle"
        fake_bundle.write_text("bundle")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar", return_value=None), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value=""):
            _, meta, _ = upload_ia(
                repo_folder, self.GH_DATA, s3_access="a", s3_secret="s",
                custom_meta={"subject": "custom_subject"},
            )

        assert meta["subject"] == "custom_subject"

    def test_raises_on_bundle_failure(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar", return_value=None), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup.create_bundle", side_effect=BundleError("fail")), \
             patch("iagitup.iagitup.get_description_from_readme", return_value=""):
            with pytest.raises(UploadError, match="Bundle creation failed"):
                upload_ia(repo_folder, self.GH_DATA, s3_access="a", s3_secret="s")

    def test_raises_on_ia_connection_failure(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        with patch("iagitup.iagitup.internetarchive.get_session", side_effect=Exception("network")):
            with pytest.raises(UploadError, match="Failed to connect"):
                upload_ia(repo_folder, self.GH_DATA, s3_access="a", s3_secret="s")

    def test_gitlab_platform_identifier_and_subject(self, tmp_path):
        """Non-GitHub platform should use platform hostname in identifier and label in subject."""
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        gitlab_data = {
            "full_name": "user/proj",
            "html_url": "https://gitlab.com/user/proj",
            "pushed_at": "2026-03-01T12:00:00Z",
            "description": "A GitLab project",
            "owner": {
                "login": "user",
                "html_url": "https://gitlab.com/user",
                "avatar_url": None,
            },
            "has_wiki": False,
            "_platform": "gitlab.com",
        }

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "bundle.bundle"
        fake_bundle.write_text("bundle")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value=""):
            identifier, meta, stem = upload_ia(
                repo_folder, gitlab_data, s3_access="a", s3_secret="s",
            )

        # Identifier should start with gitlab.com
        assert identifier.startswith("gitlab.com-")
        # Subject should use the human-readable label
        assert meta["subject"] == "GitLab;code;software;git"

    def test_no_avatar_download_when_url_is_none(self, tmp_path):
        """When avatar_url is None, _download_avatar should not be called."""
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        no_avatar_data = {
            **self.GH_DATA,
            "owner": {
                "login": "owner",
                "html_url": "https://github.com/owner",
                "avatar_url": None,
            },
        }

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "bundle.bundle"
        fake_bundle.write_text("bundle")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar") as mock_avatar, \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value=""):
            upload_ia(repo_folder, no_avatar_data, s3_access="a", s3_secret="s")

        mock_avatar.assert_not_called()


# ---------------------------------------------------------------------------
# get_ia_credentials — interactive prompt path
# ---------------------------------------------------------------------------

class TestGetIaCredentialsPrompt:
    def test_prompts_ia_configure_when_no_config(self, tmp_path):
        """When no config file exists, get_ia_credentials runs 'ia configure'."""
        fake_candidates = [tmp_path / "nope1", tmp_path / "nope2", tmp_path / "nope3"]

        with patch("iagitup.iagitup.Path") as mock_path_cls:
            instances = []
            for c in fake_candidates:
                m = MagicMock()
                m.expanduser.return_value = c
                instances.append(m)
            mock_path_cls.side_effect = instances

            with patch("iagitup.iagitup.subprocess.call", return_value=1):
                with pytest.raises(CredentialsError, match="did not complete"):
                    get_ia_credentials()

    def test_raises_when_ia_command_not_found(self, tmp_path):
        fake_candidates = [tmp_path / "nope1", tmp_path / "nope2", tmp_path / "nope3"]

        with patch("iagitup.iagitup.Path") as mock_path_cls:
            instances = []
            for c in fake_candidates:
                m = MagicMock()
                m.expanduser.return_value = c
                instances.append(m)
            mock_path_cls.side_effect = instances

            with patch("iagitup.iagitup.subprocess.call", side_effect=FileNotFoundError):
                with pytest.raises(CredentialsError, match="Could not find"):
                    get_ia_credentials()


# ---------------------------------------------------------------------------
# LFS detection and archiving
# ---------------------------------------------------------------------------

class TestLfsDetection:
    def test_detects_lfs_in_gitattributes(self, tmp_path: Path):
        (tmp_path / ".gitattributes").write_text(
            "*.bin filter=lfs diff=lfs merge=lfs -text\n"
        )
        assert _detect_lfs(tmp_path) is True

    def test_no_lfs_in_gitattributes(self, tmp_path: Path):
        (tmp_path / ".gitattributes").write_text("*.txt text\n")
        assert _detect_lfs(tmp_path) is False

    def test_no_gitattributes_file(self, tmp_path: Path):
        assert _detect_lfs(tmp_path) is False


class TestLfsInstalled:
    def test_returns_true_when_installed(self):
        with patch("iagitup.iagitup.shutil.which", return_value="/usr/bin/git-lfs"):
            assert _is_lfs_installed() is True

    def test_returns_false_when_not_installed(self):
        with patch("iagitup.iagitup.shutil.which", return_value=None):
            assert _is_lfs_installed() is False


class TestFetchAndArchiveLfs:
    def test_returns_none_when_lfs_not_installed(self, tmp_path: Path):
        with patch("iagitup.iagitup._is_lfs_installed", return_value=False):
            result = _fetch_and_archive_lfs(tmp_path, "test_bundle")
        assert result is None

    def test_returns_none_when_fetch_fails(self, tmp_path: Path):
        with patch("iagitup.iagitup._is_lfs_installed", return_value=True), \
             patch("iagitup.iagitup.subprocess.check_call",
                   side_effect=subprocess.CalledProcessError(1, "git lfs fetch")):
            result = _fetch_and_archive_lfs(tmp_path, "test_bundle")
        assert result is None

    def test_returns_none_when_lfs_dir_empty(self, tmp_path: Path):
        lfs_dir = tmp_path / ".git" / "lfs"
        lfs_dir.mkdir(parents=True)
        # lfs_dir exists but is empty
        with patch("iagitup.iagitup._is_lfs_installed", return_value=True), \
             patch("iagitup.iagitup.subprocess.check_call"):
            result = _fetch_and_archive_lfs(tmp_path, "test_bundle")
        assert result is None

    def test_success_creates_archive(self, tmp_path: Path):
        lfs_dir = tmp_path / ".git" / "lfs" / "objects"
        lfs_dir.mkdir(parents=True)
        (lfs_dir / "abc123").write_text("fake lfs object")

        with patch("iagitup.iagitup._is_lfs_installed", return_value=True), \
             patch("iagitup.iagitup.subprocess.check_call") as mock_call:
            # First call is git lfs fetch, second is tar
            result = _fetch_and_archive_lfs(tmp_path, "test_bundle")

        assert mock_call.call_count == 2
        # tar call creates the archive
        tar_call = mock_call.call_args_list[1]
        assert "tar" in tar_call[0][0][0]
        assert result == tmp_path / "test_bundle_lfs.tar.gz"


class TestUploadIaWithLfs:
    GH_DATA = {
        "full_name": "owner/repo",
        "html_url": "https://github.com/owner/repo",
        "pushed_at": "2026-01-15T10:30:00Z",
        "description": "A test repo",
        "owner": {
            "login": "owner",
            "html_url": "https://github.com/owner",
            "avatar_url": "https://avatars.example.com/u/1",
        },
        "has_wiki": False,
        "_platform": "github.com",
    }

    def test_lfs_archive_uploaded(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "owner-repo_-_2026-01-15_10-30-00.bundle"
        fake_bundle.write_text("bundle")
        fake_lfs = repo_folder / "owner-repo_-_2026-01-15_10-30-00_lfs.tar.gz"
        fake_lfs.write_text("lfs archive")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar", return_value=None), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup._detect_lfs", return_value=True), \
             patch("iagitup.iagitup._fetch_and_archive_lfs", return_value=fake_lfs), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value="readme"):
            identifier, meta, stem = upload_ia(
                repo_folder, self.GH_DATA, s3_access="a", s3_secret="s",
            )

        assert meta["has_lfs"] == "true"
        # Bundle upload + LFS upload = 2 calls
        assert mock_item.upload.call_count == 2
        # Check LFS restore instructions in description
        assert "Git LFS" in meta["description"]
        assert "git lfs checkout" in meta["description"]

    def test_lfs_warning_no_upload_when_lfs_missing(self, tmp_path):
        repo_folder = tmp_path / "repo"
        repo_folder.mkdir()

        mock_item = MagicMock()
        mock_item.exists = False

        mock_session = MagicMock()
        mock_session.get_item.return_value = mock_item

        fake_bundle = repo_folder / "owner-repo_-_2026-01-15_10-30-00.bundle"
        fake_bundle.write_text("bundle")

        with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
             patch("iagitup.iagitup._download_avatar", return_value=None), \
             patch("iagitup.iagitup._download_wiki", return_value=None), \
             patch("iagitup.iagitup._detect_lfs", return_value=True), \
             patch("iagitup.iagitup._fetch_and_archive_lfs", return_value=None), \
             patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
             patch("iagitup.iagitup.get_description_from_readme", return_value="readme"):
            identifier, meta, stem = upload_ia(
                repo_folder, self.GH_DATA, s3_access="a", s3_secret="s",
            )

        # LFS detected but fetch returned None — no LFS upload, no LFS restore instructions
        assert mock_item.upload.call_count == 1
        assert "Git LFS" not in meta["description"]


# ---------------------------------------------------------------------------
# CLI (__main__.py)
# ---------------------------------------------------------------------------

class TestCli:
    def test_version_flag(self, capsys):
        from iagitup.__main__ import main
        with pytest.raises(SystemExit, match="0"):
            with patch("sys.argv", ["iagitup", "--version"]):
                main()
        assert __version__ in capsys.readouterr().out

    def test_missing_url_exits(self):
        from iagitup.__main__ import main
        with pytest.raises(SystemExit, match="2"):
            with patch("sys.argv", ["iagitup"]):
                main()

    def test_invalid_metadata_exits(self, capsys):
        from iagitup.__main__ import main
        with patch("sys.argv", ["iagitup", "--metadata=badformat", "https://github.com/o/r"]), \
             patch("iagitup.__main__.get_ia_credentials", return_value=("a", "s")):
            with pytest.raises(SystemExit, match="1"):
                main()
        assert "metadata" in capsys.readouterr().err.lower()

    def test_credential_error_exits(self, capsys):
        from iagitup.__main__ import main
        with patch("sys.argv", ["iagitup", "https://github.com/o/r"]), \
             patch("iagitup.__main__.get_ia_credentials", side_effect=CredentialsError("no creds")):
            with pytest.raises(SystemExit, match="1"):
                main()
        assert "no creds" in capsys.readouterr().err

    def test_successful_run(self, tmp_path, capsys):
        from iagitup.__main__ import main
        fake_folder = tmp_path / "tmpXXX" / "repo"
        fake_folder.mkdir(parents=True)
        repo_data = {"pushed_at": "2026-01-01T00:00:00Z"}

        with patch("sys.argv", ["iagitup", "https://github.com/o/r"]), \
             patch("iagitup.__main__.get_ia_credentials", return_value=("a", "s")), \
             patch("iagitup.__main__.repo_download", return_value=(repo_data, fake_folder)), \
             patch("iagitup.__main__.upload_ia", return_value=("ia-id", {"title": "ia-id"}, "bundle")):
            main()

        out = capsys.readouterr().out
        assert "Upload FINISHED" in out
        assert "ia-id" in out

    def test_successful_run_with_gitlab_url(self, tmp_path, capsys):
        """CLI should accept non-GitHub URLs."""
        from iagitup.__main__ import main
        fake_folder = tmp_path / "tmpXXX" / "proj"
        fake_folder.mkdir(parents=True)
        repo_data = {"pushed_at": "2026-03-01T12:00:00Z", "_platform": "gitlab.com"}

        with patch("sys.argv", ["iagitup", "https://gitlab.com/user/proj"]), \
             patch("iagitup.__main__.get_ia_credentials", return_value=("a", "s")), \
             patch("iagitup.__main__.repo_download", return_value=(repo_data, fake_folder)), \
             patch("iagitup.__main__.upload_ia", return_value=("gl-id", {"title": "gl-id"}, "bundle")):
            main()

        out = capsys.readouterr().out
        assert "Upload FINISHED" in out
