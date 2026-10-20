"""Tests for iagitup.platforms (GitLab / Gitea / Forgejo API metadata)."""

from unittest.mock import MagicMock, patch

import requests

from iagitup.iagitup import upload_ia
from iagitup.platforms import fetch_platform_metadata

GITLAB_JSON = {
    "path_with_namespace": "grp/proj",
    "web_url": "https://gitlab.com/grp/proj",
    "http_url_to_repo": "https://gitlab.com/grp/proj.git",
    "last_activity_at": "2026-02-03T12:00:00.000+02:00",
    "description": None,
    "avatar_url": None,
    "namespace": {
        "path": "grp",
        "web_url": "https://gitlab.com/groups/grp",
        "avatar_url": "/uploads/-/system/group/avatar/1/a.png",
    },
    "wiki_enabled": True,
    "star_count": 7,
    "forks_count": 2,
}

GITEA_JSON = {
    "full_name": "o/r",
    "html_url": "https://codeberg.org/o/r",
    "clone_url": "https://codeberg.org/o/r.git",
    "updated_at": "2026-02-03T10:00:00Z",
    "description": "hello",
    "owner": {"login": "o", "avatar_url": "https://codeberg.org/avatar/1"},
    "has_wiki": True,
    "stars_count": 5,
    "forks_count": 1,
}


def _resp(status=200, payload=None, bad_json=False):
    r = MagicMock()
    r.status_code = status
    if bad_json:
        r.json.side_effect = ValueError("bad")
    else:
        r.json.return_value = payload
    return r


class TestGitlab:
    def test_maps_fields(self):
        with patch("iagitup.platforms.requests.get", return_value=_resp(payload=GITLAB_JSON)) as g, \
             patch.dict("os.environ", {"GITLAB_TOKEN": "tok"}):
            d = fetch_platform_metadata("https://gitlab.com/grp/proj.git")
        url = g.call_args.args[0]
        assert url == "https://gitlab.com/api/v4/projects/grp%2Fproj"
        assert g.call_args.kwargs["headers"] == {"PRIVATE-TOKEN": "tok"}
        assert g.call_args.kwargs["timeout"] == 10
        assert d["full_name"] == "grp/proj"
        assert d["pushed_at"] == "2026-02-03T10:00:00Z"
        assert d["description"] == ""
        assert d["owner"]["avatar_url"] == "https://gitlab.com/uploads/-/system/group/avatar/1/a.png"
        assert d["owner"]["login"] == "grp"
        assert d["has_wiki"] is True
        assert d["stargazers_count"] == 7
        assert d["_platform"] == "gitlab.com"

    def test_non_200_returns_none(self):
        with patch("iagitup.platforms.requests.get", return_value=_resp(404)):
            assert fetch_platform_metadata("https://gitlab.com/grp/proj") is None


class TestGitea:
    def test_codeberg_maps_fields(self):
        with patch("iagitup.platforms.requests.get", return_value=_resp(payload=GITEA_JSON)) as g:
            d = fetch_platform_metadata("https://codeberg.org/o/r")
        assert g.call_args.args[0] == "https://codeberg.org/api/v1/repos/o/r"
        assert d["owner"]["html_url"] == "https://codeberg.org/o"
        assert d["stargazers_count"] == 5
        assert d["pushed_at"] == "2026-02-03T10:00:00Z"
        assert d["_platform"] == "codeberg.org"


class TestRouting:
    def test_self_hosted_falls_back_to_gitlab(self):
        responses = [_resp(404), _resp(payload=GITLAB_JSON)]
        with patch("iagitup.platforms.requests.get", side_effect=responses) as g:
            d = fetch_platform_metadata("https://git.example.org/grp/proj")
        assert "/api/v1/repos/" in g.call_args_list[0].args[0]
        assert "/api/v4/projects/" in g.call_args_list[1].args[0]
        assert d["_platform"] == "git.example.org"

    def test_github_and_bitbucket_return_none_without_request(self):
        with patch("iagitup.platforms.requests.get") as g:
            assert fetch_platform_metadata("https://github.com/o/r") is None
            assert fetch_platform_metadata("https://bitbucket.org/o/r") is None
        g.assert_not_called()

    def test_request_exception_and_bad_json_return_none(self):
        with patch("iagitup.platforms.requests.get", side_effect=requests.ConnectionError("x")):
            assert fetch_platform_metadata("https://codeberg.org/o/r") is None
        with patch("iagitup.platforms.requests.get", return_value=_resp(bad_json=True)):
            assert fetch_platform_metadata("https://codeberg.org/o/r") is None


def test_upload_ia_adds_star_and_fork_counts(tmp_path):
    repo_folder = tmp_path / "repo"
    repo_folder.mkdir()
    mock_item = MagicMock()
    mock_item.exists = False
    mock_session = MagicMock()
    mock_session.get_item.return_value = mock_item
    fake_bundle = repo_folder / "owner-repo_-_2026-01-15_10-30-00.bundle"
    fake_bundle.write_text("bundle")
    data = {
        "full_name": "owner/repo",
        "html_url": "https://codeberg.org/owner/repo",
        "pushed_at": "2026-01-15T10:30:00Z",
        "description": "A test repo",
        "owner": {"login": "owner", "html_url": "https://codeberg.org/owner", "avatar_url": None},
        "has_wiki": False,
        "stargazers_count": 5,
        "forks_count": 1,
        "_platform": "codeberg.org",
    }
    with patch("iagitup.iagitup.internetarchive.get_session", return_value=mock_session), \
         patch("iagitup.iagitup._download_avatar", return_value=None), \
         patch("iagitup.iagitup._download_wiki", return_value=None), \
         patch("iagitup.iagitup.create_bundle", return_value=fake_bundle), \
         patch("iagitup.iagitup.get_description_from_readme", return_value="readme"):
        _, meta, _ = upload_ia(repo_folder, data, s3_access="a", s3_secret="s")
    assert meta["stars_count"] == "5"
    assert meta["forks_count"] == "1"
