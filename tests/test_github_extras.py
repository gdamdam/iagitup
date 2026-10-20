"""Tests for iagitup.github_extras (requests.get is mocked; no network)."""

import json
from unittest.mock import MagicMock, patch

import requests

from iagitup import github_extras as ge

REPO = {"full_name": "o/r"}


def _resp(data=None, status=200, remaining="10", content=b""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    r.headers = {"X-RateLimit-Remaining": remaining}
    r.iter_content.return_value = [content]
    r.__enter__.return_value = r
    return r


def test_paginate_two_pages():
    pages = [_resp([{"n": i} for i in range(100)]), _resp([{"n": 100}])]
    with patch("requests.get", side_effect=pages) as get:
        out = ge._paginate("u", {"state": "all"})
    assert len(out) == 101
    assert get.call_args_list[1].kwargs["params"]["page"] == 2
    assert get.call_args_list[0].kwargs["params"]["state"] == "all"


def test_paginate_403_returns_partial():
    with patch("requests.get", side_effect=[_resp([{"n": 1}] * 100), _resp(status=403)]):
        assert len(ge._paginate("u")) == 100


def test_paginate_request_exception():
    with patch("requests.get", side_effect=requests.ConnectionError("x")):
        assert ge._paginate("u") == []


def test_paginate_rate_limit_stops():
    with patch("requests.get", return_value=_resp([{"n": 1}] * 100, remaining="0")) as get:
        assert len(ge._paginate("u")) == 100
    assert get.call_count == 1


def _release(tag, assets):
    return {"tag_name": tag, "assets": assets}


def test_releases_sanitised_download(tmp_path):
    asset = {"name": "../../a.zip", "size": 3, "browser_download_url": "http://x/a"}
    responses = [_resp([_release("../evil", [asset])]), _resp(content=b"abc")]
    with patch("requests.get", side_effect=responses):
        files = ge.fetch_releases(REPO, tmp_path)
    target = tmp_path / "releases" / "evil" / "a.zip"
    assert target in files and target.read_bytes() == b"abc"
    assert (tmp_path / "releases" / "releases.json") in files
    assert not (tmp_path.parent / "a.zip").exists()


def test_releases_max_asset_skip(tmp_path):
    asset = {"name": "big.bin", "size": 5 * 1024 * 1024, "browser_download_url": "http://x/b"}
    with patch("requests.get", side_effect=[_resp([_release("v1", [asset])])]) as get:
        files = ge.fetch_releases(REPO, tmp_path, max_asset_mb=1)
    assert files == [tmp_path / "releases" / "releases.json"]
    assert get.call_count == 1


def test_releases_none_writes_nothing(tmp_path):
    with patch("requests.get", return_value=_resp([])):
        assert ge.fetch_releases(REPO, tmp_path / "d") == []
    assert not (tmp_path / "d").exists()


def test_issues_split_and_comments(tmp_path):
    items = [
        {"url": "I1", "number": 1},
        {"url": "P1", "number": 2, "pull_request": {}},
    ]
    ic = [{"id": 1, "issue_url": "I1"}, {"id": 2, "issue_url": "P1"}]
    pc = [{"id": 3, "pull_request_url": "P1"}]
    with patch("requests.get", side_effect=[_resp(items), _resp(ic), _resp(pc)]):
        files = ge.fetch_issues(REPO, tmp_path)
    assert {f.name for f in files} == {"issues.json", "pulls.json"}
    issues = json.loads((tmp_path / "issues.json").read_text())
    pulls = json.loads((tmp_path / "pulls.json").read_text())
    assert [c["id"] for c in issues[0]["comments"]] == [1]
    assert [c["id"] for c in pulls[0]["comments"]] == [2, 3]


def test_issues_403_writes_nothing(tmp_path):
    with patch("requests.get", return_value=_resp(status=403)):
        assert ge.fetch_issues(REPO, tmp_path) == []
