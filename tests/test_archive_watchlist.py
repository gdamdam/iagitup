"""Tests for archive_watchlist.py"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from iagitup.archive_watchlist import (
    _state_lock,
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


def _mock_response(items=None, status=200, remaining=1000, reset=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"X-RateLimit-Remaining": str(remaining)}
    if reset is not None:
        resp.headers["X-RateLimit-Reset"] = str(reset)
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

    def test_since_and_until_date_range(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response()) as mock_get:
            fetch_top_repos(10, since="2025-01-01", until="2025-06-30")
        _, kwargs = mock_get.call_args
        assert "created:2025-01-01..2025-06-30" in kwargs["params"]["q"]

    def test_since_only(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response()) as mock_get:
            fetch_top_repos(10, since="2025-01-01")
        _, kwargs = mock_get.call_args
        assert "created:>=2025-01-01" in kwargs["params"]["q"]

    def test_until_only(self):
        with patch("iagitup.archive_watchlist.requests.get",
                   return_value=_mock_response()) as mock_get:
            fetch_top_repos(10, until="2025-06-30")
        _, kwargs = mock_get.call_args
        assert "created:<=2025-06-30" in kwargs["params"]["q"]

    def test_paginates_until_n_collected(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        pages = [_mock_response([SAMPLE_REPO] * 100), _mock_response([SAMPLE_REPO] * 50)]
        with patch("iagitup.archive_watchlist.requests.get", side_effect=pages) as mock_get, \
             patch("iagitup.archive_watchlist.time.sleep") as mock_sleep:
            result = fetch_top_repos(150)
        assert len(result) == 150
        sent = [c.kwargs["params"] for c in mock_get.call_args_list]
        assert [(p["page"], p["per_page"]) for p in sent] == [(1, 100), (2, 50)]
        # Unauthenticated: one 2s pause between the two pages, none after the last.
        mock_sleep.assert_called_once_with(2)

    def test_no_sleep_with_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        pages = [_mock_response([SAMPLE_REPO] * 100), _mock_response([SAMPLE_REPO] * 100)]
        with patch("iagitup.archive_watchlist.requests.get", side_effect=pages), \
             patch("iagitup.archive_watchlist.time.sleep") as mock_sleep:
            fetch_top_repos(200)
        mock_sleep.assert_not_called()

    def test_stops_on_short_page(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        pages = [_mock_response([SAMPLE_REPO] * 100), _mock_response([SAMPLE_REPO] * 7)]
        with patch("iagitup.archive_watchlist.requests.get", side_effect=pages) as mock_get:
            result = fetch_top_repos(500)
        assert len(result) == 107
        assert mock_get.call_count == 2

    def test_rate_limit_zero_returns_partial(self, monkeypatch, caplog):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        pages = [_mock_response([SAMPLE_REPO] * 100, remaining=0, reset=1700000000)]
        with patch("iagitup.archive_watchlist.requests.get", side_effect=pages) as mock_get:
            with caplog.at_level(logging.ERROR):
                result = fetch_top_repos(300)
        assert len(result) == 100
        assert mock_get.call_count == 1
        assert any("2023-11-14 22:13:20 UTC" in r.message for r in caplog.records)

    def test_later_page_error_returns_partial(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        pages = [_mock_response([SAMPLE_REPO] * 100), _mock_response(status=502)]
        with patch("iagitup.archive_watchlist.requests.get", side_effect=pages):
            result = fetch_top_repos(200)
        assert len(result) == 100


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

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
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

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
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

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
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

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("ia-id", {}, "bundle")):
            archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False)

        assert not download_dir.exists()

    def test_keep_work_dir_preserves_clone_and_logs_path(self, tmp_path, caplog):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", side_effect=IagitupError("boom")):
            with caplog.at_level(logging.INFO):
                archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False, keep_work_dir=True)

        assert download_dir.exists()
        assert any(str(download_dir) in r.message for r in caplog.records)

    def test_failure_recorded_in_failures_list(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()
        failures: list = []

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", side_effect=IagitupError("boom")):
            result = archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False, failures=failures)

        assert result == "failed"
        assert failures == [{"repo": "owner/repo", "error": "boom"}]

    def test_upload_ia_receives_custom_meta(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        download_dir.mkdir()
        fake_folder = download_dir / "repo"
        fake_folder.mkdir()

        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("ia-id", {}, "b")) as mock_upload:
            archive_repo(SAMPLE_REPO, 3, "acc", "sec", {}, dry_run=False)

        _, kwargs = mock_upload.call_args
        meta = kwargs["custom_meta"]
        assert meta["github_rank"] == "3"
        assert meta["stars_count"] == "50000"
        assert meta["language"] == "Python"


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------

class TestWatchlistCli:
    def test_version_flag(self, capsys):
        from iagitup.archive_watchlist import main
        from iagitup.iagitup import __version__
        with pytest.raises(SystemExit, match="0"):
            with patch("sys.argv", ["archive-watchlist", "--version"]):
                main()
        assert __version__ in capsys.readouterr().out

    def test_top_n_exceeds_1000_exits(self):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            with patch("sys.argv", ["archive-watchlist", "--top", "1001"]):
                main()

    def test_days_with_since_exits(self):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            with patch("sys.argv", ["archive-watchlist", "--days", "7", "--since", "2025-01-01"]):
                main()

    def test_invalid_since_format_exits(self):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            with patch("sys.argv", ["archive-watchlist", "--since", "not-a-date"]):
                main()

    def test_invalid_until_format_exits(self):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            with patch("sys.argv", ["archive-watchlist", "--until", "01/30/2025"]):
                main()

    def test_max_size_filters_large_repos(self):
        from iagitup.archive_watchlist import main
        small_repo = {**SAMPLE_REPO, "full_name": "owner/small", "size": 100_000}
        large_repo = {**SAMPLE_REPO, "full_name": "owner/large", "size": 600_000}
        with patch("sys.argv", ["archive-watchlist", "--dry-run", "--top", "2", "--max-size", "500"]), \
             patch("iagitup.archive_watchlist.fetch_top_repos", return_value=[small_repo, large_repo]), \
             patch("iagitup.archive_watchlist.load_state", return_value={}), \
             patch("iagitup.archive_watchlist.save_state"), \
             patch("iagitup.archive_watchlist.archive_repo", return_value="archived") as mock_archive:
            main()
        # Only the small repo should be passed to archive_repo
        assert mock_archive.call_count == 1
        assert mock_archive.call_args[0][0]["full_name"] == "owner/small"

    def test_dry_run_skips_credentials(self):
        from iagitup.archive_watchlist import main
        with patch("sys.argv", ["archive-watchlist", "--dry-run", "--top", "1"]), \
             patch("iagitup.archive_watchlist.fetch_top_repos", return_value=[SAMPLE_REPO]), \
             patch("iagitup.archive_watchlist.load_state", return_value={}), \
             patch("iagitup.archive_watchlist.save_state"), \
             patch("iagitup.archive_watchlist.archive_repo", return_value="archived"):
            # Should not call get_ia_credentials in dry-run mode
            with patch("iagitup.archive_watchlist.get_ia_credentials") as mock_creds:
                main()
            mock_creds.assert_not_called()


    def _run_main(self, tmp_path, extra, archive_side_effect):
        from iagitup.archive_watchlist import main
        argv = ["archive-watchlist", "--top", "2", "--state-file", str(tmp_path / "s.json"), *extra]
        repo2 = {**SAMPLE_REPO, "full_name": "owner/other"}
        with patch("sys.argv", argv), \
             patch("iagitup.archive_watchlist.fetch_top_repos", return_value=[SAMPLE_REPO, repo2]), \
             patch("iagitup.archive_watchlist.get_ia_credentials", return_value=("a", "b")), \
             patch("iagitup.archive_watchlist.save_state"), \
             patch("iagitup.archive_watchlist.archive_repo", side_effect=archive_side_effect):
            main()

    def test_json_summary_written(self, tmp_path):
        out = tmp_path / "summary.json"

        def fake(repo, rank, *a, failures=None, **kw):
            if repo["full_name"] == "owner/other":
                failures.append({"repo": "owner/other", "error": "boom"})
                return "failed"
            return "archived"

        self._run_main(tmp_path, ["--json-summary", str(out)], fake)
        data = json.loads(out.read_text())
        assert data["archived"] == 1 and data["failed"] == 1 and data["skipped"] == 0
        assert data["total_checked"] == 2 and data["dry_run"] is False
        assert data["failures"] == [{"repo": "owner/other", "error": "boom"}]
        assert data["started_at"] <= data["finished_at"]

    def test_unexpected_error_recorded_as_failure(self, tmp_path):
        out = tmp_path / "summary.json"

        def fake(repo, *a, **kw):
            raise RuntimeError("kaboom")

        self._run_main(tmp_path, ["--json-summary", str(out)], fake)
        data = json.loads(out.read_text())
        assert data["failed"] == 2
        assert all("kaboom" in f["error"] for f in data["failures"])

    def test_max_failures_exceeded_exits_2(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            self._run_main(tmp_path, ["--max-failures", "1"], lambda *a, **kw: "failed")
        assert ei.value.code == 2

    def test_max_failures_not_exceeded_exits_0(self, tmp_path):
        self._run_main(tmp_path, ["--max-failures", "2"], lambda *a, **kw: "failed")

    def test_keep_work_dir_passed_through(self, tmp_path):
        seen = []

        def fake(*a, **kw):
            seen.append(kw["keep_work_dir"])
            return "archived"

        self._run_main(tmp_path, ["--keep-work-dir"], fake)
        assert seen == [True, True]


# ---------------------------------------------------------------------------
# Pre-clone duplicate check + state locking (3.6.3)
# ---------------------------------------------------------------------------

class TestArchiveRepoExistingItem:
    def test_existing_item_skips_clone_and_records_state(self):
        state = {}
        with patch("iagitup.archive_watchlist.find_existing_item", return_value=("ia-id", "stem")), \
             patch("iagitup.archive_watchlist.repo_download") as mock_download, \
             patch("iagitup.archive_watchlist.upload_ia") as mock_upload:
            result = archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)

        assert result == "archived"
        mock_download.assert_not_called()
        mock_upload.assert_not_called()
        assert state["owner/repo"]["ia_identifier"] == "ia-id"

    def test_search_metadata_is_passed_to_repo_download(self, tmp_path):
        download_dir = tmp_path / "tmpXXX"
        (download_dir / "repo").mkdir(parents=True)
        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download",
                   return_value=({}, download_dir / "repo")) as mock_download, \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("id", {}, "b")):
            archive_repo(SAMPLE_REPO, 1, "acc", "sec", {}, dry_run=False)
        assert mock_download.call_args.kwargs["repo_data"] is SAMPLE_REPO


class TestStateLocking:
    def test_state_write_and_save_hold_lock_but_upload_does_not(self, tmp_path):
        """The in-memory state write and the save must be serialised by
        _state_lock, while the (slow) upload must run outside it."""
        from iagitup.archive_watchlist import main
        seen = {}
        fake_folder = tmp_path / "tmpXXX" / "repo"
        fake_folder.mkdir(parents=True)

        def fake_upload(*a, **kw):
            seen["upload_locked"] = _state_lock.locked()
            return ("ia-id", {}, "b")

        def fake_save(path, state):
            seen["save_locked"] = _state_lock.locked()
            # json.dumps is what the real save does; it must not race.
            json.dumps(state, indent=2)

        with patch("sys.argv", ["archive-watchlist", "--top", "1", "--workers", "2"]), \
             patch("iagitup.archive_watchlist.get_ia_credentials", return_value=("a", "s")), \
             patch("iagitup.archive_watchlist.fetch_top_repos", return_value=[SAMPLE_REPO]), \
             patch("iagitup.archive_watchlist.load_state", return_value={}), \
             patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, fake_folder)), \
             patch("iagitup.archive_watchlist.save_state", side_effect=fake_save), \
             patch("iagitup.archive_watchlist.upload_ia", side_effect=fake_upload):
            main()

        assert seen["upload_locked"] is False
        assert seen["save_locked"] is True

    def test_archive_repo_writes_state_under_lock(self):
        """Patch dict.__setitem__ indirectly: use a dict subclass that records lock state."""
        class Spy(dict):
            def __setitem__(self, k, v):
                self.locked_at_write = _state_lock.locked()
                super().__setitem__(k, v)

        state = Spy()
        with patch("iagitup.archive_watchlist.find_existing_item", return_value=("ia-id", "stem")):
            archive_repo(SAMPLE_REPO, 1, "acc", "sec", state, dry_run=False)
        assert state.locked_at_write is True
        assert not _state_lock.locked()  # released afterwards


# ---------------------------------------------------------------------------
# Owner mode, extras, state keys
# ---------------------------------------------------------------------------

def _run_owner_main(argv, repos=None, **extra_patches):
    from iagitup.archive_watchlist import main
    repos = [SAMPLE_REPO] if repos is None else repos
    with patch("iagitup.archive_watchlist.fetch_owner_repos", return_value=repos) as owner, \
         patch("iagitup.archive_watchlist.fetch_top_repos") as top, \
         patch("iagitup.archive_watchlist.load_state", return_value={}) as load, \
         patch("iagitup.archive_watchlist.save_state") as save, \
         patch("iagitup.archive_watchlist.get_ia_credentials", return_value=("a", "b")), \
         patch("iagitup.archive_watchlist.archive_repo", return_value="archived") as arch:
        main(argv)
    return owner, top, load, save, arch


class TestOwnerMode:
    def test_uses_fetch_owner_repos_not_top(self):
        owner, top, _, _, arch = _run_owner_main(
            ["--owner", "torvalds", "--include-forks", "--skip-archived", "--max-repos", "5"]
        )
        owner.assert_called_once_with("torvalds", include_forks=True, skip_archived=True, max_repos=5)
        top.assert_not_called()
        assert arch.call_args.kwargs["include_rank"] is False

    def test_top_mode_still_includes_rank_and_defaults_top_100(self):
        from iagitup.archive_watchlist import main
        with patch("iagitup.archive_watchlist.fetch_top_repos", return_value=[SAMPLE_REPO]) as top, \
             patch("iagitup.archive_watchlist.load_state", return_value={}), \
             patch("iagitup.archive_watchlist.save_state"), \
             patch("iagitup.archive_watchlist.get_ia_credentials", return_value=("a", "b")), \
             patch("iagitup.archive_watchlist.archive_repo", return_value="archived") as arch:
            main([])
        assert top.call_args.args[0] == 100
        assert arch.call_args.kwargs["include_rank"] is True

    @pytest.mark.parametrize("flags", [
        ["--top", "5"], ["--days", "3"], ["--since", "2025-01-01"], ["--until", "2025-01-01"],
    ])
    def test_owner_rejects_top_mode_flags(self, flags):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            main(["--owner", "x", *flags])

    @pytest.mark.parametrize("flags", [["--include-forks"], ["--skip-archived"], ["--max-repos", "3"]])
    def test_owner_flags_require_owner(self, flags):
        from iagitup.archive_watchlist import main
        with pytest.raises(SystemExit, match="2"):
            main(flags)

    def test_owner_error_exits_1(self):
        from iagitup.archive_watchlist import main
        with patch("iagitup.archive_watchlist.fetch_owner_repos", side_effect=IagitupError("nope")), \
             patch("iagitup.archive_watchlist.get_ia_credentials", return_value=("a", "b")), \
             patch("iagitup.archive_watchlist.load_state", return_value={}):
            with pytest.raises(SystemExit, match="1"):
                main(["--owner", "x"])

    def test_no_state_never_loads_or_saves(self):
        _, _, load, save, arch = _run_owner_main(["--owner", "x", "--no-state"])
        load.assert_not_called()
        save.assert_not_called()
        assert arch.call_args.args[4] == {}

    def test_state_saved_by_default(self):
        _, _, load, save, _ = _run_owner_main(["--owner", "x"])
        load.assert_called_once()
        save.assert_called_once()

    def test_extras_flags_forwarded(self):
        *_, arch = _run_owner_main(["--owner", "x", "--releases", "--issues", "--submodules"])
        kw = arch.call_args.kwargs
        assert kw["releases"] and kw["issues"] and kw["submodules"]


class TestStateKeyAndMeta:
    def test_non_github_state_key_prefixed(self, tmp_path):
        repo = {**SAMPLE_REPO, "full_name": "g/p", "_platform": "gitlab.com"}
        folder = tmp_path / "tmpX" / "repo"
        folder.mkdir(parents=True)
        state = {}
        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("id", {}, "b")):
            archive_repo(repo, 1, "a", "s", state, dry_run=False)
        assert list(state) == ["gitlab.com/g/p"]
        # Second run: the prefixed key drives the local skip.
        assert archive_repo(repo, 1, "a", "s", state, dry_run=False) == "skipped"

    def test_rank_none_omits_github_rank(self):
        assert "github_rank" not in build_custom_meta(SAMPLE_REPO, rank=None)

    def test_subject_uses_platform_label(self):
        assert build_custom_meta({**SAMPLE_REPO, "_platform": "gitlab.com"}, 1)["subject"].startswith("GitLab;")
        assert build_custom_meta({**SAMPLE_REPO, "_platform": "git.example.org"}, 1)["subject"].startswith(
            "git.example.org;"
        )
        assert build_custom_meta(SAMPLE_REPO, 1)["subject"].startswith("GitHub;")

    def test_include_rank_false_omits_rank_in_upload(self, tmp_path):
        folder = tmp_path / "tmpX" / "repo"
        folder.mkdir(parents=True)
        with patch("iagitup.archive_watchlist.find_existing_item", return_value=None), \
             patch("iagitup.archive_watchlist.repo_download", return_value=({}, folder)), \
             patch("iagitup.archive_watchlist.upload_ia", return_value=("id", {}, "b")) as up:
            archive_repo(SAMPLE_REPO, 7, "a", "s", {}, dry_run=False, include_rank=False)
        assert "github_rank" not in up.call_args.kwargs["custom_meta"]


class TestArchiveRepoExtras:
    def _archive(self, tmp_path, repo=SAMPLE_REPO, **kw):
        folder = tmp_path / "tmpX" / "repo"
        folder.mkdir(parents=True)
        p = "iagitup.archive_watchlist."
        with patch(p + "find_existing_item", return_value=None), \
             patch(p + "repo_download", return_value=({}, folder)) as dl, \
             patch(p + "_item_names", return_value=("id", "stem", None)), \
             patch(p + "create_submodule_bundles", return_value=[folder / "s.bundle"]) as sub, \
             patch(p + "fetch_releases", return_value=[folder / "rel.json"]) as rel, \
             patch(p + "fetch_issues", return_value=[folder / "iss.json"]) as iss, \
             patch(p + "upload_ia", return_value=("id", {}, "stem")) as up:
            archive_repo(repo, 1, "a", "s", {}, dry_run=False, **kw)
        return dl, sub, rel, iss, up, folder

    def test_defaults_call_no_extras(self, tmp_path):
        dl, sub, rel, iss, up, _ = self._archive(tmp_path)
        sub.assert_not_called()
        rel.assert_not_called()
        iss.assert_not_called()
        assert up.call_args.kwargs["extra_files"] is None
        assert dl.call_args.kwargs["submodules"] is False

    def test_all_extras_on_github(self, tmp_path):
        dl, sub, rel, iss, up, folder = self._archive(
            tmp_path, releases=True, issues=True, submodules=True
        )
        assert dl.call_args.kwargs["submodules"] is True
        sub.assert_called_once_with(folder, "stem")
        rel.assert_called_once_with(SAMPLE_REPO, folder.parent)
        iss.assert_called_once_with(SAMPLE_REPO, folder.parent)
        assert len(up.call_args.kwargs["extra_files"]) == 3

    def test_releases_issues_skipped_off_github(self, tmp_path):
        repo = {**SAMPLE_REPO, "_platform": "gitlab.com"}
        _, sub, rel, iss, up, _ = self._archive(
            tmp_path, repo=repo, releases=True, issues=True, submodules=True
        )
        rel.assert_not_called()
        iss.assert_not_called()
        sub.assert_called_once()  # submodules are platform-agnostic
        assert len(up.call_args.kwargs["extra_files"]) == 1
