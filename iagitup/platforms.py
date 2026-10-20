"""Platform API metadata for non-GitHub hosts (GitLab, Codeberg/Gitea/Forgejo).

``fetch_platform_metadata`` returns a dict with the same shape as the GitHub
API response used throughout iagitup (``full_name``, ``html_url``,
``clone_url``, ``pushed_at`` as ``YYYY-MM-DDTHH:MM:SSZ``, ``description``,
``owner.login/html_url/avatar_url``, ``has_wiki``, ``_platform``), or
``None`` when the host is unknown or its API is unreachable so callers fall
back to clone-derived metadata.
"""

from __future__ import annotations


def fetch_platform_metadata(repo_url: str) -> dict | None:
    """Return API metadata for *repo_url* on a supported non-GitHub host, else ``None``."""
    # TODO(feature-4): implemented by the platforms agent.
    return None
