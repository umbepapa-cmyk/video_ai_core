"""
Discover subject input folders under ``inputs/``.

Supports legacy names (``Soggetto N``) and gender-suffixed folders
(``Soggetto N - donna``, ``Soggetto N - uomo``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from gender_detector import _normalize_gender, load_gender_json

# Mirrors auto_curator.SCAN_EXCLUDE_DIR_PREFIXES / names used for non-subject folders.
_EXCLUDE_PREFIXES = ("scarti_", "Test_Soggetto", "VIP_Dataset_", "Mannheim")
_EXCLUDE_NAMES = frozenset({"datasets", "Mannheim", "__pycache__"})

_SUBJECT_FOLDER_RE = re.compile(
    r"^Soggetto\s+(\d+)(?:\s*[-–—]\s*(.+))?$",
    re.IGNORECASE,
)


def _gender_from_suffix(suffix: str) -> Optional[str]:
    normalized = suffix.strip().lower()
    if not normalized:
        return None
    # Common typo in user folder names.
    if "dpnna" in normalized:
        return "female"
    for token in re.split(r"[\s_\-]+", normalized):
        g = _normalize_gender(token)
        if g in ("male", "female"):
            return g
    if "uomo" in normalized:
        return "male"
    if "donna" in normalized:
        return "female"
    return None


def gender_from_folder_name(folder_name: str) -> Optional[str]:
    """Extract male/female from folder name suffix (e.g. ``Soggetto 1 - donna``)."""
    match = _SUBJECT_FOLDER_RE.match(folder_name.strip())
    if not match:
        return None
    suffix = match.group(2)
    if not suffix:
        return None
    return _gender_from_suffix(suffix)


def _is_subject_folder(path: Path) -> bool:
    name = path.name
    if name in _EXCLUDE_NAMES:
        return False
    if any(name.startswith(prefix) for prefix in _EXCLUDE_PREFIXES):
        return False
    return _SUBJECT_FOLDER_RE.match(name) is not None


def discover_subject_inputs(inputs_root: Path) -> dict[int, Path]:
    """
    Map subject number -> input folder path.

    When multiple folders match the same number, prefer the shortest name
    (legacy ``Soggetto N`` over ``Soggetto N - donna``).
    """
    found: dict[int, list[Path]] = {}
    if not inputs_root.is_dir():
        return {}

    for path in sorted(inputs_root.iterdir()):
        if not path.is_dir() or not _is_subject_folder(path):
            continue
        match = _SUBJECT_FOLDER_RE.match(path.name.strip())
        if not match:
            continue
        num = int(match.group(1))
        found.setdefault(num, []).append(path)

    return {
        num: sorted(paths, key=lambda p: (len(p.name), p.name))[0]
        for num, paths in found.items()
    }


def resolve_subject_input_folder(subject_num: int, inputs_root: Path) -> Path:
    """
    Resolve input folder for a subject number.

    Returns the discovered path or the legacy expected path (may not exist).
    """
    discovered = discover_subject_inputs(inputs_root)
    if subject_num in discovered:
        return discovered[subject_num]
    return inputs_root / f"Soggetto {subject_num}"


def resolve_reference_face(subject_num: int, inputs_root: Path) -> Optional[Path]:
    """Return ``face.jpg`` from the subject input folder if present."""
    folder = resolve_subject_input_folder(subject_num, inputs_root)
    face = folder / "face.jpg"
    return face if face.is_file() else None


def resolve_subject_gender(
    subject_num: int,
    *,
    inputs_root: Path,
    metadata: Optional[dict] = None,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """
    Resolve gender: folder name suffix > gender.json in dataset > metadata.
    """
    folder = resolve_subject_input_folder(subject_num, inputs_root)
    from_name = gender_from_folder_name(folder.name)
    if from_name in ("male", "female"):
        return from_name

    root = project_root or inputs_root.parent
    for suffix in ("_v3", ""):
        gender_path = root / "datasets" / f"soggetto{subject_num}{suffix}" / "gender.json"
        cached = load_gender_json(gender_path)
        if cached and cached.gender in ("male", "female"):
            return cached.gender

    if metadata:
        meta_g = str(metadata.get("subject_gender", metadata.get("gender", ""))).lower()
        if meta_g in ("male", "female"):
            return meta_g

    return None
