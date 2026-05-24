"""
InsightFace-based identity validation gate for LoRA inference tests.

Used by test_lora_soggetto4.py and test_native_loras.py to reject generations
whose face embedding similarity to the reference falls below threshold.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.45
V3_SIMILARITY_THRESHOLD = 0.65
DEFAULT_BODY_CONSISTENCY_THRESHOLD = 0.55


@lru_cache(maxsize=1)
def _get_face_analyzer():
    try:
        import cv2
        from insightface.app import FaceAnalysis
    except ImportError as exc:
        raise RuntimeError(
            "InsightFace non installato. Installa con: pip install insightface onnxruntime"
        ) from exc

    analyzer = FaceAnalysis(name="buffalo_l")
    try:
        import torch

        ctx_id = 0 if torch.cuda.is_available() else -1
    except ImportError:
        ctx_id = -1
    analyzer.prepare(ctx_id=ctx_id, det_size=(640, 640))
    return analyzer, cv2


def _largest_face_embedding(image_path: Union[str, Path]):
    analyzer, cv2 = _get_face_analyzer()
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"Impossibile leggere immagine: {image_path}")

    faces = analyzer.get(img)
    if not faces:
        return None

    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    emb = getattr(face, "normed_embedding", None)
    if emb is None:
        emb = getattr(face, "embedding", None)
    return emb


def compute_face_similarity(
    generated_path: Union[str, Path],
    reference_path: Union[str, Path],
) -> float:
    """
    Cosine similarity between largest faces in generated vs reference images.

    Returns 0.0 when either image has no detectable face.
    """
    import numpy as np

    gen_emb = _largest_face_embedding(generated_path)
    ref_emb = _largest_face_embedding(reference_path)
    if gen_emb is None or ref_emb is None:
        logger.warning(
            "Face similarity: missing embedding (gen=%s ref=%s)",
            gen_emb is not None,
            ref_emb is not None,
        )
        return 0.0

    gen_norm = gen_emb / np.linalg.norm(gen_emb)
    ref_norm = ref_emb / np.linalg.norm(ref_emb)
    return float(np.dot(gen_norm, ref_norm))


def assert_identity_gate(
    generated_path: Union[str, Path],
    reference_path: Union[str, Path],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    label: str = "identity",
) -> float:
    """Raise AssertionError if similarity is below threshold."""
    score = compute_face_similarity(generated_path, reference_path)
    logger.info(
        "[IDENTITY_GATE] %s similarity=%.3f threshold=%.3f ref=%s gen=%s",
        label,
        score,
        threshold,
        reference_path,
        generated_path,
    )
    if score < threshold:
        raise AssertionError(
            f"Identity gate FAILED ({label}): similarity {score:.3f} < {threshold:.3f}"
        )
    return score


def _extract_body_proportions(image_path: Union[str, Path]) -> Optional[tuple[float, float]]:
    """
    Extract shoulder-width / hip-width ratio and torso aspect from MediaPipe Pose.
    Returns (shoulder_hip_ratio, torso_aspect) or None.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return None

    img = cv2.imread(str(image_path))
    if img is None:
        return None

    h, w = img.shape[:2]
    with mp.solutions.pose.Pose(
        static_image_mode=True,
        model_complexity=1,
        min_detection_confidence=0.5,
    ) as pose:
        results = pose.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if not results.pose_landmarks:
            return None
        lm = results.pose_landmarks.landmark

        def pt(idx: int) -> tuple[float, float]:
            p = lm[idx]
            return p.x * w, p.y * h

        ls, rs = pt(11), pt(12)
        lh, rh = pt(23), pt(24)
        shoulder_w = abs(ls[0] - rs[0])
        hip_w = max(1.0, abs(lh[0] - rh[0]))
        shoulder_y = (ls[1] + rs[1]) / 2.0
        hip_y = (lh[1] + rh[1]) / 2.0
        torso_h = max(1.0, hip_y - shoulder_y)
        ratio = shoulder_w / hip_w
        aspect = torso_h / max(1.0, shoulder_w)
        return float(ratio), float(aspect)


def compute_body_consistency_score(
    generated_path: Union[str, Path],
    reference_path: Union[str, Path],
) -> float:
    """
    Compare shoulder/hip proportions between reference and generated images.
    Returns score in [0,1]; higher = more consistent body proportions.
    """
    ref = _extract_body_proportions(reference_path)
    gen = _extract_body_proportions(generated_path)
    if ref is None or gen is None:
        logger.warning(
            "Body consistency: missing pose (ref=%s gen=%s)",
            ref is not None,
            gen is not None,
        )
        return 0.0

    ref_ratio, ref_aspect = ref
    gen_ratio, gen_aspect = gen
    ratio_diff = abs(ref_ratio - gen_ratio) / max(ref_ratio, 0.01)
    aspect_diff = abs(ref_aspect - gen_aspect) / max(ref_aspect, 0.01)
    score = max(0.0, 1.0 - (ratio_diff * 0.6 + aspect_diff * 0.4))
    return round(score, 3)


def assert_body_consistency_gate(
    generated_path: Union[str, Path],
    reference_path: Union[str, Path],
    *,
    threshold: float = DEFAULT_BODY_CONSISTENCY_THRESHOLD,
    label: str = "body",
) -> float:
    """Log body consistency; does not raise (informational gate for v3 tests)."""
    score = compute_body_consistency_score(generated_path, reference_path)
    logger.info(
        "[BODY_GATE] %s body_score=%.3f threshold=%.3f ref=%s gen=%s",
        label,
        score,
        threshold,
        reference_path,
        generated_path,
    )
    return score
