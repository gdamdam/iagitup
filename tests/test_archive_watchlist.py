"""Tests for archive_watchlist.py"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iagitup.archive_watchlist import (
    archive_repo,
    build_custom_meta,
    fetch_top_repos,
    load_state,
    save_state,
)
from iagitup.iagitup import IagitupError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_REPO = {
    "full_name": "owner/repo",
    "html_url": "https://github.com/owner/repo",
    "pushed_at": "2026-01-01T00:00:00Z",
    "stargazers_count": 50000,
    "forks_count": 1000,
    "watchers_count": 1500,
    "language": "Python",
    "topics": ["tool", "cli"],
}


def _mock_response(items=None, status=200, remaining=1000):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"X-RateLimit-Remaining": str(remaining)}
    resp.json.return_value = {"items": items or []}
    resp.text = "error details"
    return resp


# ---------------------------------------------------------------------------
# load_state / save_state
# ---------------------------------------------------------------------------

class TestLoadState:
    def test_returns_empty_when_file_missing(self, tmp_path):
        assert load_state(tmp_path / "nope.json") == {}

    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / "state.json"
        data = {"owner/repo": {"pushed_at": "2026-01-01T00:00:00Z", "stars": 50000}}
        f.write_text(json.dumps(data), encoding="utf-8")
        assert load_state(f) == data

    def test_returns_empty_on_invalid_json(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text("not valid json {{", encoding="utf-8")
        assert load_state(f) == {}


class TestSaveState:
    def test_roundtrip(self, tmp_path):
        f = tmp_path / "state.json"
        data = {"owner/repo": {"pushed_at": "2026-01-01T00:00:00Z", "stars": 50000}}
        save_state(f, data)
        assert load_state(f) == data

    def test_no_tmp_file_left_after_save(self, tmp_path):
        f = tmp_path / "state.json"
        save_state(f, {"x": 1})
        assert not (tmp_path / "state.tmp").exists()
        assert f.exists()

    def test_overwrites_existing_state(self, tmp_path):
        f = tmp_path / "state.json"
        save_state(f, {"a": 1})
        save_state(f, {"b": 2})
        assert load_state(f) == {"b": 2}


# ---------------------------------------------------------------------------
# build_custom_meta
# ---------------------------------------------------------------------------

class TestBuildCustomMeta:
    def test_all_fields_present(self):
        meta = build_custom_meta(SAMPLE_REPO, rank=1)
        for key in ("stars_count", "forks_count", "watchers_count",
                    "language", "topics", "github_rank", "subject"):
            assert key in meta

    def test_counts_are_strings(self):
        meta = build_custom_meta(SAMPLE_REPO, rank=1)
        assert meta["stars_count"] == "50000"
        assert meta["forks_count"] == "1000"
        assert meta["watchers_count"] == "1500"

    def test_rank_set_correctly(self):
        assert build_custom_meta(SAMPLE_REPO, rank=42)["github_rank"] == "42"

    def test_language_included_in_subject(self):
        meta = build_custom_meta(SAMPLE_REPO, rank=1)
        assert "Python" in meta["subject"].split(";")

    def test_topics_joined_with_semicolons(self):
        meta = build_custom_meta(SAMPLE_REPO, rank=1)
        assert meta["topics"] == "tool;cli"
        assert "tool" in meta["subject"]
        assert "cli" in meta["subject"]

    def test_no_language_omitted_from_subject(self):
        repo = {**SAMPLE_REPO, "language": None}
        meta = build_custom_meta(repo, rank=1)
        assert meta["language"] == ""
        assert "" not in meta["subject"].split(";")

    def test_no_topics(self):
        repo = {**SAMPLE_REPO, "topics": []}
        meta = build_custom_meta(repo, rank=1)
        assert meta["topics"] == ""

    def test_base_subject_always_present(self):
        repo = {**SAMPLE_REPO, "language": None, "topics": []}
        meta = build_custom_meta(repo, rank=1)
        for base in ("GitHub", "code", "software", "git"):
            assert base in meta["subject"].split(";")


# ---------------------------------------------------------------------------
# fetch_top_repos
# ---------------------------------------------------------------------------

class TestFetchTopRepos:
    def test_returns_items(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response([SAMPLE_REPO])):
            result = fetch_top_repos(1)
        assert result == [SAMPLE_REPO]

    def test_respects_top_n_in_per_page_param(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response()) as mock_get:
            fetch_top_repos(10)
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["per_page"] == 10

    def test_caps_per_page_at_100(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response()) as mock_get:
            fetch_top_repos(100)
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["per_page"] == 100

    def test_exits_on_api_error(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response(status=403)):
            with pytest.raises(SystemExit):
                fetch_top_repos(10)

    def test_warns_on_low_rate_limit(self, caplog):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response(remaining=3)):
            with caplog.at_level(logging.WARNING, logger="archive_watchlist"):
                fetch_top_repos(1)
        assert any("rate limit" in r.message.lower() for r in caplog.records)

    def test_no_warning_on_healthy_rate_limit(self, caplog):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response(remaining=100)):
            with caplog.at_level(logging.WARNING, logger="archive_watchlist"):
                fetch_top_repos(1)
        assert not any("rate limit" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# archive_repo
# ---------------------------------------------------------------------------

class TestArchiveRepo:
    def test_skips_when_pushed_at_unchanged(self):
        state = {"owner/repo": {"pushed_at": SAMPLE_REPO["pushed_at"]}}
        result = archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)
        assert result == "skipped"

    def test_skip_does_not_mutate_state(self):
        state = {"owner/repo": {"pushed_at": SAMPLE_REPO["pushed_at"], "stars": 1}}
        archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)
        assert state["owner/repo"]["stars"] == 1  # unchanged

    def test_dry_run_new_repo_returns_archived(self):
        result = archive_repo(SAMPLE_REPO, 1, "", "", {}, dry_run=True)
        assert result == "archived"

    def test_dry_run_updated_repo_returns_archived(self):
        state = {"owner/repo": {"pushed_at": "2020-01-01T00:00:00Z"}}
        result = archive_repo(SAMPLE_REPO, 1, "", "", state, dry_run=True)
        assert result == "archived"

    def test_dry_run_does_not_mutate_state(self):
        state = {}
        archive_repo(SAMPLE_REPO, 1, "", "", state, dry_run=True)
        assert state == {}

    def test_successful_archive_updates_state(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()
        state = {}

        with patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("ia-id-123", {}, "bundle")):
            result = archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)

        assert result == "archived"
        assert "owner/repo" in state
        assert state["owner/repo"]["pushed_at"] == SAMPLE_REPO["pushed_at"]
        assert state["owner/repo"]["ia_identifier"] == "ia-id-123"
        assert state["owner/repo"]["stars"] == 50000
        assert "archived_at" in state["owner/repo"]

    def test_returns_failed_on_iagitup_error(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()
        state = {}

        with patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", side_effect=IagitupError("boom")):
            result = archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)

        assert result == "failed"
        assert "owner/repo" not in state  # state must not be updated on failure

    def test_cleanup_on_failure(self, tmp_path):
        # Simulate the mkdtemp structure: download_dir / repo_name
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()
        (fake_folder / "bundle.bundle").write_text("data")

        with patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", side_effect=IagitupError("boom")):
            archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False)

        # The entire mkdtemp root (download_dir) should be removed, not just
        # the repo subfolder, so the wiki/ sibling is also cleaned up.
        assert not download_dir.exists()

    def test_cleanup_on_success(self, tmp_path):
        # Simulate the mkdtemp structure: download_dir / repo_name
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()

        with patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("ia-id", {}, "bundle")):
            archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False)

        assert not download_dir.exists()

    def test_upload_ia_receives_custom_meta(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()

        with patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("ia-id", {}, "b")) as mock_upload:
            archive_repo(SAMPLE_REPO, 3, "acc", "sec", {}, dry_run=False)

        _, kwargs = mock_upload.call_args
        meta = kwargs["custom_meta"]
        assert meta["github_rank"] == "3"
        assert meta["stars_count"] == "50000"
        assert meta["language"] == "Python"
