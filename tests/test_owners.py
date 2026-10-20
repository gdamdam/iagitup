"""Tests for iagitup.owners (all HTTP mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from iagitup.iagitup import IagitupError
from iagitup.owners import fetch_owner_repos, parse_owner_url


def _resp(data=None, status=200, remaining=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {} if remaining is None else {"X-RateLimit-Remaining": str(remaining)}
    r.json.return_value = data if data is not None else []
    r.text = "body"
    return r


def _gh(name, pushed="2026-01-01T00:00:00Z", **kw):
    return {"full_name": f"o/{name}", "pushed_at": pushed, "fork": False, "archived": False, **kw}


def _router(routes):
    """requests.get stand-in: routes maps URL -> response or list of responses (per call)."""
    calls = []

    def fake(url, headers=None, params=None, timeout=None):
        assert timeout, "every request needs a timeout"
        calls.append((url, dict(params or {})))
        val = routes.get(url)
        if val is None:
            return _resp(status=404)
        if isinstance(val, list):
            return val.pop(0)
        return val

    fake.calls = calls
    return fake


def _gl(name, last="2026-01-01T00:00:00Z", **kw):
    return {
        "path_with_namespace": f"g/{name}", "web_url": f"https://gitlab.com/g/{name}",
        "http_url_to_repo": f"https://gitlab.com/g/{name}.git", "last_activity_at": last,
        "namespace": {"path": "g"}, **kw,
    }


def _gt(name, upd="2026-01-01T00:00:00Z", **kw):
    return {
        "full_name": f"g/{name}", "html_url": f"https://codeberg.org/g/{name}",
        "clone_url": f"https://codeberg.org/g/{name}.git", "updated_at": upd,
        "owner": {"login": "g"}, **kw,
    }


class TestParseOwnerUrl:
    @pytest.mark.parametrize("text,expected", [
        ("torvalds", ("github.com", "torvalds")),
        ("https://github.com/torvalds", ("github.com", "torvalds")),
        ("https://github.com/torvalds/", ("github.com", "torvalds")),
        ("https://gitlab.com/gitlab-org", ("gitlab.com", "gitlab-org")),
        ("https://github.com/torvalds/linux", None),
        ("https://gitlab.com/g/sub/proj", None),
        ("https://github.com", None),
        ("torvalds/linux", None),
        ("", None),
    ])
    def test_cases(self, text, expected):
        assert parse_owner_url(text) == expected


class TestGitHub:
    def test_user_uses_users_endpoint(self):
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}),
            "https://api.github.com/users/u/repos": _resp([_gh("a")]),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("u")
        assert repos[0]["_platform"] == "github.com"
        assert fake.calls[1][1]["type"] == "owner"

    def test_org_uses_orgs_endpoint(self):
        fake = _router({
            "https://api.github.com/users/o": _resp({"type": "Organization"}),
            "https://api.github.com/orgs/o/repos": _resp([_gh("a")]),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            fetch_owner_repos("https://github.com/o")
        assert fake.calls[1][0].endswith("/orgs/o/repos")
        assert fake.calls[1][1]["type"] == "all"

    def test_paginates_until_short_page(self):
        page1 = [_gh(f"r{i}", f"2026-01-{i % 28 + 1:02d}T00:00:00Z") for i in range(100)]
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}),
            "https://api.github.com/users/u/repos": [_resp(page1), _resp([_gh("last")])],
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("u")
        assert len(repos) == 101
        assert [c[1].get("page") for c in fake.calls[1:]] == [1, 2]

    def test_filters_forks_and_archived(self):
        data = [_gh("a"), _gh("f", fork=True), _gh("z", archived=True)]
        routes = lambda: _router({  # noqa: E731
            "https://api.github.com/users/u": _resp({"type": "User"}),
            "https://api.github.com/users/u/repos": _resp(list(data)),
        })
        with patch("iagitup.owners.requests.get", side_effect=routes()):
            names = {r["full_name"] for r in fetch_owner_repos("u")}
        assert names == {"o/a", "o/z"}
        with patch("iagitup.owners.requests.get", side_effect=routes()):
            names = {r["full_name"] for r in fetch_owner_repos("u", include_forks=True, skip_archived=True)}
        assert names == {"o/a", "o/f"}

    def test_max_repos_keeps_most_recent_after_filtering(self):
        data = [
            _gh("old", "2020-01-01T00:00:00Z"),
            _gh("fork-new", "2026-03-01T00:00:00Z", fork=True),
            _gh("mid", "2024-01-01T00:00:00Z"),
            _gh("new", "2025-01-01T00:00:00Z"),
        ]
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}),
            "https://api.github.com/users/u/repos": _resp(data),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("u", max_repos=2)
        assert [r["full_name"] for r in repos] == ["o/new", "o/mid"]

    def test_empty_repo_skipped(self):
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}),
            "https://api.github.com/users/u/repos": _resp([_gh("a"), _gh("empty", None)]),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            assert [r["full_name"] for r in fetch_owner_repos("u")] == ["o/a"]

    def test_not_found_raises(self):
        with patch("iagitup.owners.requests.get", side_effect=_router({})):
            with pytest.raises(IagitupError, match="404"):
                fetch_owner_repos("ghost")

    def test_server_error_raises_with_status(self):
        fake = _router({"https://api.github.com/users/u": _resp(status=500)})
        with patch("iagitup.owners.requests.get", side_effect=fake):
            with pytest.raises(IagitupError, match="500"):
                fetch_owner_repos("u")

    def test_rate_limit_mentions_token(self):
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}, remaining=1),
            "https://api.github.com/users/u/repos": _resp(status=403, remaining=0),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            with pytest.raises(IagitupError, match="GITHUB_TOKEN"):
                fetch_owner_repos("u")

    def test_last_request_spent_on_final_page_still_succeeds(self):
        # The final (short) page arrives with zero quota left: the listing is
        # complete, so it must be returned rather than discarded.
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}, remaining=1),
            "https://api.github.com/users/u/repos": _resp([_gh("a")], remaining=0),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            assert [r["full_name"] for r in fetch_owner_repos("u")] == ["o/a"]

    def test_quota_exhausted_mid_listing_raises(self):
        full_page = [_gh(f"r{i}") for i in range(100)]
        fake = _router({
            "https://api.github.com/users/u": _resp({"type": "User"}, remaining=2),
            "https://api.github.com/users/u/repos": _resp(full_page, remaining=0),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            with pytest.raises(IagitupError, match="GITHUB_TOKEN"):
                fetch_owner_repos("u")


class TestOtherHosts:
    def test_gitlab_group_then_user_fallback(self):
        fake = _router({
            "https://gitlab.com/api/v4/users/g/projects": _resp([
                _gl("a"), _gl("f", forked_from_project={"id": 1}), _gl("z", archived=True),
            ]),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("https://gitlab.com/g", include_forks=True)
        assert fake.calls[0][0].endswith("/groups/g/projects")
        assert fake.calls[0][1]["include_subgroups"] == "true"
        assert {r["full_name"]: (r["fork"], r["archived"]) for r in repos} == {
            "g/a": (False, False), "g/f": (True, False), "g/z": (False, True),
        }
        assert repos[0]["_platform"] == "gitlab.com"

    def test_gitlab_group_endpoint_used_when_present(self):
        fake = _router({"https://gitlab.com/api/v4/groups/g/projects": _resp([_gl("a")])})
        with patch("iagitup.owners.requests.get", side_effect=fake):
            assert len(fetch_owner_repos("https://gitlab.com/g")) == 1
        assert len(fake.calls) == 1

    def test_gitea_org_then_user_fallback(self):
        fake = _router({
            "https://codeberg.org/api/v1/users/g/repos": _resp([_gt("a", fork=True), _gt("b", archived=True)]),
        })
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("https://codeberg.org/g", include_forks=True)
        assert fake.calls[0][0].endswith("/orgs/g/repos") and fake.calls[0][1]["limit"] == 50
        assert {r["full_name"]: (r["fork"], r["archived"]) for r in repos} == {
            "g/a": (True, False), "g/b": (False, True),
        }

    def test_gitea_empty_updated_at_skipped(self):
        fake = _router({"https://codeberg.org/api/v1/orgs/g/repos": _resp([_gt("a"), _gt("e", upd=None)])})
        with patch("iagitup.owners.requests.get", side_effect=fake):
            assert [r["full_name"] for r in fetch_owner_repos("https://codeberg.org/g")] == ["g/a"]

    def test_bitbucket_unsupported(self):
        with pytest.raises(IagitupError, match="owner listing not supported for bitbucket.org"):
            fetch_owner_repos("https://bitbucket.org/team")

    def test_unknown_host_probes_gitea_then_gitlab(self):
        fake = _router({"https://git.example.org/api/v4/groups/g/projects": _resp([_gl("a")])})
        with patch("iagitup.owners.requests.get", side_effect=fake):
            repos = fetch_owner_repos("https://git.example.org/g")
        assert repos[0]["_platform"] == "git.example.org"

    def test_unknown_host_nothing_matches(self):
        with patch("iagitup.owners.requests.get", side_effect=_router({})):
            with pytest.raises(IagitupError, match="owner listing not supported for git.example.org"):
                fetch_owner_repos("https://git.example.org/g")

    def test_non_owner_input_rejected(self):
        with pytest.raises(IagitupError):
            fetch_owner_repos("https://github.com/o/r")
