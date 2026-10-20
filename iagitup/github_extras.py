"""GitHub release assets and issue / pull-request exports.

Both functions write files under *dest_dir* (the mkdtemp root, i.e.
``repo_folder.parent``) and return the list of created paths so the caller
can pass them to ``upload_ia(extra_files=...)``.  They never raise on a
missing or empty resource; network/API failures are logged and yield ``[]``.
"""

from __future__ import annotations

from pathlib import Path


def fetch_releases(repo_data: dict, dest_dir: Path) -> list[Path]:
    """Download release metadata and assets into ``dest_dir/releases/``.

    Writes ``releases/releases.json`` (full API payload) and every asset as
    ``releases/<tag>/<asset-name>``.
    """
    # TODO(feature-5): implemented by the extras agent.
    return []


def fetch_issues(repo_data: dict, dest_dir: Path) -> list[Path]:
    """Export issues and pull requests (with comments) as JSON into ``dest_dir``.

    Writes ``issues.json`` and ``pulls.json``.
    """
    # TODO(feature-5): implemented by the extras agent.
    return []
