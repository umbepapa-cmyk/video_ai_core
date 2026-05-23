"""
Mannheim pipeline — Subject 2 identity resolution and two-person disambiguation.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SUBJECT2_DIR = Path("inputs/Soggetto 2")

SUBJECT2_PROMPT_SUFFIX = (
    ", replace only the person matching the reference identity (Subject 2); "
    "preserve the other person's appearance and position in frame unchanged"
)


@dataclass
class SubjectResolution:
    subject_dir: Optional[Path]
    subjects_payload: Dict[str, str]
    source_label: str
    use_subject2_prompt: bool


@dataclass
class VideoFaceMatch:
    bbox: Tuple[int, int, int, int]
    similarity: float
    position: str
    face_count: int


def resolve_subject_dir() -> Optional[Path]:
    """Resolve Mannheim identity source directory from env or defaults."""
    explicit = os.getenv("MANNHEIM_SUBJECT_DIR", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"MANNHEIM_SUBJECT_DIR not found: {path}")
        return path.resolve()

    if os.getenv("MANNHEIM_USE_SUBJECT", "").strip() == "2":
        if DEFAULT_SUBJECT2_DIR.exists():
            return DEFAULT_SUBJECT2_DIR.resolve()
        raise FileNotFoundError(
            f"MANNHEIM_USE_SUBJECT=2 but directory missing: {DEFAULT_SUBJECT2_DIR}"
        )

    if DEFAULT_SUBJECT2_DIR.exists():
        return DEFAULT_SUBJECT2_DIR.resolve()
    return None


def prepare_subjects_payload(
    *,
    subject_dir: Optional[Path],
    face_image: Path,
    temp_faces_dir: Path,
) -> SubjectResolution:
    """Build subjects_payload from Soggetto 2 or face.jpg fallback."""
    if subject_dir and subject_dir.exists():
        logger.info(
            "[MANNHEIM] Targeting Subject 2 from %s",
            subject_dir.resolve(),
        )
        use_subject2 = (
            subject_dir.resolve() == DEFAULT_SUBJECT2_DIR.resolve()
            or os.getenv("MANNHEIM_USE_SUBJECT", "").strip() == "2"
            or bool(os.getenv("MANNHEIM_SUBJECT_DIR", "").strip())
        )
        return SubjectResolution(
            subject_dir=subject_dir,
            subjects_payload={"subject_1": str(subject_dir.resolve())},
            source_label="soggetto_2",
            use_subject2_prompt=use_subject2,
        )

    if not face_image.exists():
        raise FileNotFoundError(
            "Mannheim identity missing. Provide one of:\n"
            f"  - {DEFAULT_SUBJECT2_DIR.resolve()}\n"
            f"  - {face_image.resolve()}\n"
            "  - MANNHEIM_SUBJECT_DIR or MANNHEIM_USE_SUBJECT=2"
        )

    temp_faces_dir.mkdir(parents=True, exist_ok=True)
    for old in temp_faces_dir.glob("*"):
        if old.is_file():
            old.unlink()
    dest = temp_faces_dir / "face.jpg"
    shutil.copy2(face_image, dest)
    logger.info("[MANNHEIM] Using face.jpg fallback: %s", face_image.resolve())
    return SubjectResolution(
        subject_dir=None,
        subjects_payload={"subject_1": str(temp_faces_dir.resolve())},
        source_label="face.jpg",
        use_subject2_prompt=False,
    )


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a_flat = np.asarray(a, dtype=np.float32).flatten()
    b_flat = np.asarray(b, dtype=np.float32).flatten()
    norm_a = np.linalg.norm(a_flat)
    norm_b = np.linalg.norm(b_flat)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_flat / norm_a, b_flat / norm_b))


def _bbox_to_position(bbox: Tuple[float, float, float, float], frame_width: int) -> str:
    x1, _, x2, _ = bbox
    center_x = (x1 + x2) / 2
    third = frame_width / 3
    if center_x < third:
        return "left"
    if center_x > 2 * third:
        return "right"
    return "center"


def _spatial_hint_x_range(hint: str, frame_width: int) -> Tuple[float, float]:
    hint = hint.strip().lower()
    third = frame_width / 3
    if hint == "left":
        return 0.0, third * 1.5
    if hint == "right":
        return third * 1.5, float(frame_width)
    return third * 0.5, third * 2.5


def match_subject_in_video(
    video_path: str,
    identity_vector: np.ndarray,
    *,
    spatial_hint: Optional[str] = None,
    max_frames: int = 3,
) -> Optional[VideoFaceMatch]:
    """
    Compare Subject 2 super-vector to faces in early video frames.

    When two or more faces appear, logs each candidate and returns the best
    identity match (optionally biased by MANNHEIM_TARGET_PERSON=left|right|center).
    """
    from identity_lock_3d import insightface_available

    if not insightface_available():
        logger.warning("[MANNHEIM] InsightFace unavailable — skipping video face match")
        return None

    import cv2
    from identity_lock_3d import _get_insightface_analyzer

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning("[MANNHEIM] Cannot open video for face match: %s", video_path)
        return None

    analyzer = _get_insightface_analyzer()
    best_match: Optional[VideoFaceMatch] = None

    try:
        for frame_idx in range(max_frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            height, width = frame.shape[:2]
            faces = analyzer.get(frame)
            if not faces:
                continue

            scored: List[Tuple[float, Tuple[int, int, int, int], str]] = []
            for face in faces:
                embedding = np.asarray(face.normed_embedding, dtype=np.float32)
                similarity = cosine_similarity(embedding, identity_vector)
                x1, y1, x2, y2 = (int(v) for v in face.bbox)
                position = _bbox_to_position((x1, y1, x2, y2), width)
                scored.append((similarity, (x1, y1, x2 - x1, y2 - y1), position))

            scored.sort(key=lambda item: item[0], reverse=True)

            if spatial_hint and len(scored) >= 2:
                x_min, x_max = _spatial_hint_x_range(spatial_hint, width)
                hinted = [
                    item
                    for item in scored
                    if x_min <= item[1][0] + item[1][2] / 2 <= x_max
                ]
                if hinted:
                    rest = [item for item in scored if item not in hinted]
                    scored = hinted + rest
                    logger.info(
                        "[MANNHEIM] Applied spatial hint %r — preferring faces in x=[%.0f, %.0f]",
                        spatial_hint,
                        x_min,
                        x_max,
                    )

            top_sim, top_bbox, top_pos = scored[0]
            best_match = VideoFaceMatch(
                bbox=top_bbox,
                similarity=top_sim,
                position=top_pos,
                face_count=len(faces),
            )
            logger.info(
                "[MANNHEIM] Video face match frame=%d faces=%d best_sim=%.3f position=%s",
                frame_idx,
                len(faces),
                top_sim,
                top_pos,
            )
            if len(faces) >= 2:
                for idx, (sim, bbox, pos) in enumerate(scored):
                    logger.info(
                        "  face[%d] sim=%.3f position=%s bbox=%s",
                        idx,
                        sim,
                        pos,
                        bbox,
                    )
            break
    finally:
        cap.release()

    return best_match


def extract_identity_super_vector(subject_dir: str) -> Optional[np.ndarray]:
    """Load or compute Subject 2 super-vector for video face matching."""
    from identity_cache import compute_folder_hash, load_cached_identity
    from identity_lock_3d import MultiAngleIdentityLock

    cache_hash = compute_folder_hash(subject_dir)
    cached = load_cached_identity(cache_hash)
    if cached is not None:
        return cached[0]

    try:
        locker = MultiAngleIdentityLock(
            reference_faces_dir=subject_dir,
            num_angles=1,
            fail_on_low_stability=False,
        )
        locker.extract_multi_angle_embeddings()
        super_vector = locker.create_super_vector()
        return super_vector.vector
    except Exception as exc:
        logger.warning("[MANNHEIM] Identity vector extraction failed: %s", exc)
        return None


def pick_best_swap_reference(
    subject_dir: str,
    identity_vector: Optional[np.ndarray] = None,
) -> Optional[Path]:
    """
    Pick the best reference photo for Pass 2 face-swap.

    Prefers the reference whose embedding best matches the Subject 2 super-vector.
    """
    from identity_lock_3d import (
        FaceEmbeddingExtractor,
        rank_reference_face_images,
        score_reference_image,
        select_best_full_body_image,
    )

    try:
        select_best_full_body_image([subject_dir], faces_dir=subject_dir)
    except ValueError as exc:
        logger.debug("[MANNHEIM] Full-body preview skipped: %s", exc)

    ranked = rank_reference_face_images(subject_dir, top_n=10, require_face=False)
    if not ranked:
        return None

    if identity_vector is not None and len(ranked) > 1:
        try:
            extractor = FaceEmbeddingExtractor()
            best_path: Optional[Path] = None
            best_sim = -1.0
            for candidate in ranked:
                try:
                    embedding = extractor.extract_embedding(str(candidate))
                    sim = cosine_similarity(embedding, identity_vector)
                    if sim > best_sim:
                        best_sim = sim
                        best_path = candidate
                except ValueError:
                    continue
            if best_path is not None:
                score = score_reference_image(str(best_path))
                logger.info(
                    "[MANNHEIM] Pass 2 swap reference: %s (identity_sim=%.3f sharpness=%.1f)",
                    best_path.name,
                    best_sim,
                    score.sharpness,
                )
                return best_path
        except RuntimeError as exc:
            logger.debug("[MANNHEIM] Embedding-based swap pick skipped: %s", exc)

    best = ranked[0]
    logger.info("[MANNHEIM] Pass 2 swap reference (ranked): %s", best.name)
    return best


def augment_mannheim_prompt(base_prompt: str, *, use_subject2_focus: bool) -> str:
    if use_subject2_focus and SUBJECT2_PROMPT_SUFFIX.strip() not in base_prompt:
        return base_prompt.rstrip(". ") + SUBJECT2_PROMPT_SUFFIX
    return base_prompt
