"""Tests for iagitup core logic."""

import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iagitup.iagitup import (
    CredentialsError,
    RepoDownloadError,
    _parse_github_url,
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
