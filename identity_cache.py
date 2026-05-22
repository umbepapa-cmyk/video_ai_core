"""
Identity vector cache — O(1) lookup for multi-angle super-vectors.

Caches InsightFace extraction results keyed by MD5 hash of reference face inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

CACHE_ROOT = Path(".cache/identity_vectors")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _iter_face_files(faces_dir: Path) -> List[Path]:
    if not faces_dir.exists():
        return []
    files = [
        p
        for p in faces_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def compute_folder_hash(
    faces_dir: str,
    file_paths: Optional[List[Union[str, Path]]] = None,
) -> str:
    """
    Compute MD5 hash from sorted filenames, sizes, and mtimes.

    Args:
        faces_dir: Directory containing reference face images.
        file_paths: Optional explicit file list instead of scanning the directory.
    """
    if file_paths:
        paths = sorted(
            (Path(p).resolve() for p in file_paths),
            key=lambda p: str(p).lower(),
        )
    else:
        paths = _iter_face_files(Path(faces_dir).resolve())

    hasher = hashlib.md5()
    for path in paths:
        if not path.is_file():
            continue
        stat = path.stat()
        hasher.update(path.name.encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(int(stat.st_mtime)).encode("utf-8"))

    digest = hasher.hexdigest()
    logger.debug("Identity cache hash %s for %d file(s)", digest, len(paths))
    return digest


def _cache_path(cache_hash: str) -> Path:
    return CACHE_ROOT / f"{cache_hash}.npz"


def load_cached_identity(
    cache_hash: str,
) -> Optional[Tuple[np.ndarray, float, Dict[str, Any]]]:
    """
    Load cached super-vector, stability score, and metadata.

    Returns:
        (super_vector, stability_score, metadata) or None on cache miss.
    """
    path = _cache_path(cache_hash)
    if not path.exists():
        return None

    try:
        data = np.load(path, allow_pickle=False)
        vector = data["super_vector"]
        stability = float(data["stability_score"])
        metadata: Dict[str, Any] = {}
        if "metadata_json" in data:
            metadata = json.loads(str(data["metadata_json"]))
            logger.info(
                "Identity cache HIT (%s): %d images, saved %s",
                cache_hash[:8],
                metadata.get("num_images", "?"),
                metadata.get("timestamp", "?"),
            )
        else:
            logger.info("Identity cache HIT (%s)", cache_hash[:8])
        return vector, stability, metadata
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Corrupt identity cache %s: %s — ignoring", path, exc)
        return None


def save_cached_identity(
    cache_hash: str,
    vector: np.ndarray,
    stability: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """Persist super-vector and stability to .cache/identity_vectors/{hash}.npz."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_hash)

    meta = dict(metadata or {})
    meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    np.savez_compressed(
        path,
        super_vector=np.asarray(vector, dtype=np.float32),
        stability_score=np.float64(stability),
        metadata_json=np.array(json.dumps(meta), dtype="U"),
    )
    logger.info(
        "Identity cache SAVE (%s): stability=%.1f%%, files=%s",
        cache_hash[:8],
        stability * 100,
        meta.get("num_images", "?"),
    )
    return path


def invalidate_cache(cache_hash: str) -> bool:
    """Remove a single cache entry. Returns True if a file was deleted."""
    path = _cache_path(cache_hash)
    if path.exists():
        path.unlink()
        return True
    return False
