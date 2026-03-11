"""iagitup — Archive GitHub repositories to the Internet Archive.

This package provides CLI tools and a Python API for cloning GitHub repos,
bundling them with ``git bundle``, and uploading the result to archive.org
as permanent, publicly accessible snapshots.
"""

# Re-export __version__ so callers can do ``import iagitup; iagitup.__version__``.
from iagitup.iagitup import __version__  # noqa: F401
