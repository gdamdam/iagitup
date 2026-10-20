"""Tests for the main-CLI flags: --dry-run, --keep-work-dir, --resume,
--submodules, --releases, --issues."""

from pathlib import Path
from unittest.mock import patch

import pytest

from iagitup.__main__ import main

GH = "https://github.com/o/r"
GL = "https://gitlab.com/u/proj"
REPO_DATA = {"pushed_at": "2026-01-01T00:00:00Z"}


def _run(argv, **patches):
    """Run main() with the given argv and patches keyed by __main__ attribute."""
    ctxs = [patch("sys.argv", ["iagitup", *argv])]
    ctxs += [patch(f"iagitup.__main__.{k}", **v) for k, v in patches.items()]
    for c in ctxs:
        c.start()
    try:
        main()
    finally:
        for c in reversed(ctxs):
            c.stop()


@pytest.fixture
def clone(tmp_path):
    folder = tmp_path / "tmpXXX" / "repo"
    (folder / ".git").mkdir(parents=True)
    return folder


class TestDryRun:
    def test_github_no_clone_no_credentials(self, capsys):
        meta = {"title": "t"}
        with patch("iagitup.__main__.upload_ia", return_value=("ia-id", meta, "stem")) as up:
            _run(
                ["--dry-run", GH],
                get_ia_credentials={"side_effect": AssertionError("no creds in dry-run")},
                fetch_repo_metadata={"return_value": REPO_DATA},
                find_existing_item={"side_effect": AssertionError("no dup check")},
                repo_download={"side_effect": AssertionError("no clone")},
            )
        assert up.call_args.kwargs["dry_run"] is True
        out = capsys.readouterr().out
        assert "ia-id" in out and "ia-id/stem.bundle" in out
        assert '"title": "t"' in out
        assert "Upload FINISHED" not in out

    def test_non_github_clones_then_dry_runs(self, clone, capsys):
        with patch("iagitup.__main__.upload_ia", return_value=("gl-id", {"a": 1}, "stem")) as up:
            _run(
                ["--dry-run", GL],
                fetch_repo_metadata={"return_value": None},
                repo_download={"return_value": (REPO_DATA, clone)},
            )
        assert up.call_args.kwargs["dry_run"] is True
        assert up.call_args.args[0] == clone
        assert not clone.parent.exists()  # temp clone still cleaned


class TestKeepAndResume:
    def test_keep_work_dir_preserves_and_reports(self, clone, capsys):
        _run(
            ["--keep-work-dir", GL],
            get_ia_credentials={"return_value": ("a", "s")},
            fetch_repo_metadata={"return_value": None},
            repo_download={"return_value": (REPO_DATA, clone)},
            find_existing_item={"return_value": None},
            upload_ia={"return_value": ("id", {"title": "id"}, "stem")},
        )
        assert clone.exists()
        assert str(clone) in capsys.readouterr().err

    def test_keep_work_dir_preserves_on_failure(self, clone):
        from iagitup.iagitup import IagitupError
        with pytest.raises(SystemExit, match="1"):
            _run(
                ["--keep-work-dir", GL],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": None},
                repo_download={"return_value": (REPO_DATA, clone)},
                find_existing_item={"return_value": None},
                upload_ia={"side_effect": IagitupError("boom")},
            )
        assert clone.exists()

    def test_resume_skips_download_and_keeps_dir(self, clone):
        with patch("iagitup.__main__.upload_ia", return_value=("id", {"title": "id"}, "s")) as up:
            _run(
                ["--resume", str(clone), GH],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": REPO_DATA},
                find_existing_item={"return_value": None},
                repo_download={"side_effect": AssertionError("must not download")},
            )
        assert up.call_args.args[0] == clone
        assert clone.exists()

    def test_resume_non_github_builds_data_from_clone(self, clone):
        with patch("iagitup.__main__.upload_ia", return_value=("id", {"title": "id"}, "s")), \
             patch("iagitup.__main__._parse_repo_url", return_value=("u", "proj", "gitlab.com")), \
             patch("iagitup.__main__._build_repo_data_from_clone", return_value=REPO_DATA) as build:
            _run(
                ["--resume", str(clone), GL],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": None},
                find_existing_item={"return_value": None},
            )
        build.assert_called_once_with(GL, "u", "proj", "gitlab.com", clone)

    def test_resume_existing_item_skips_upload(self, clone, capsys):
        with patch("iagitup.__main__.upload_ia") as up:
            _run(
                ["--resume", str(clone), GH],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": REPO_DATA},
                find_existing_item={"return_value": ("id", "stem")},
            )
        up.assert_not_called()
        assert "already archived" in capsys.readouterr().out

    def test_resume_requires_git_clone(self, tmp_path, capsys):
        with pytest.raises(SystemExit, match="1"):
            _run(
                ["--resume", str(tmp_path), GH],
                get_ia_credentials={"return_value": ("a", "s")},
            )
        assert "--resume" in capsys.readouterr().err


class TestExtras:
    def test_submodules_releases_issues_passed_as_extra_files(self, clone):
        sub, rel, iss = Path("s.bundle"), Path("rel.zip"), Path("iss.json")
        with patch("iagitup.__main__.upload_ia", return_value=("id", {"title": "id"}, "s")) as up, \
             patch("iagitup.__main__._item_names", return_value=("id", "stem", None)):
            _run(
                ["--submodules", "--releases", "--issues", GH],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": REPO_DATA},
                find_existing_item={"return_value": None},
                repo_download={"return_value": (REPO_DATA, clone)},
                create_submodule_bundles={"return_value": [sub]},
                fetch_releases={"return_value": [rel]},
                fetch_issues={"return_value": [iss]},
            )
        assert up.call_args.kwargs["extra_files"] == [sub, rel, iss]

    def test_submodules_passed_to_download(self, clone):
        with patch("iagitup.__main__.upload_ia", return_value=("id", {"title": "id"}, "s")), \
             patch("iagitup.__main__._item_names", return_value=("id", "stem", None)), \
             patch("iagitup.__main__.repo_download", return_value=(REPO_DATA, clone)) as dl:
            _run(
                ["--submodules", GL],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": None},
                find_existing_item={"return_value": None},
                create_submodule_bundles={"return_value": []},
            )
        assert dl.call_args.kwargs["submodules"] is True

    def test_releases_issues_ignored_for_non_github(self, clone, capsys):
        with patch("iagitup.__main__.upload_ia", return_value=("id", {"title": "id"}, "s")) as up:
            _run(
                ["--releases", "--issues", GL],
                get_ia_credentials={"return_value": ("a", "s")},
                fetch_repo_metadata={"return_value": None},
                find_existing_item={"return_value": None},
                repo_download={"return_value": (REPO_DATA, clone)},
                fetch_releases={"side_effect": AssertionError("github only")},
                fetch_issues={"side_effect": AssertionError("github only")},
            )
        assert up.call_args.kwargs["extra_files"] is None
        assert "GitHub-only" in capsys.readouterr().err
