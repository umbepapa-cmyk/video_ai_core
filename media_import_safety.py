"""
Safety utilities for media import and curation pipelines.

All import flows must use these helpers instead of move/delete on user-facing paths.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

# Allowed directory name segments for destructive operations (staging/tmp only).
_STAGING_DIR_NAMES = frozenset({"tmp", "temp", "staging", "__pycache__", ".cache"})


def safe_copy(src: Path, dst: Path) -> Path:
    """
    Copy *src* to *dst*, creating parent directories as needed.

    The source file is never modified, moved, or deleted.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"Source not found or not a file: {src}")
    if src.resolve() == dst.resolve():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def file_sha256(path: Path, *, chunk_size: int = 65536) -> str:
    """Return hex SHA256 digest for a file (used for import dedup)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def collect_file_hashes(root: Path, *, recursive: bool = True) -> set[str]:
    """Collect SHA256 hashes of all regular files under *root*."""
    if not root.is_dir():
        return set()
    paths = root.rglob("*") if recursive else root.iterdir()
    hashes: set[str] = set()
    for path in paths:
        if path.is_file():
            try:
                hashes.add(file_sha256(path))
            except OSError:
                continue
    return hashes


def assert_no_destructive_path(path: Path) -> None:
    """
    Refuse destructive operations on paths outside staging/tmp directories.

    Raises:
        PermissionError: when *path* is not under an allowed staging/tmp root.
    """
    resolved = Path(path).resolve()
    parts = {part.lower() for part in resolved.parts}
    if parts & _STAGING_DIR_NAMES:
        return
    raise PermissionError(
        f"Destructive operation refused on {resolved!s}: "
        "only staging/tmp paths are permitted."
    )
