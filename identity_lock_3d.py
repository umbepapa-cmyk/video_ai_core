"""
WEEK 1 V2 - DAY 4: Multi-Angle Identity Lock (PuLID)
=====================================================
Module for 3D identity locking using multi-angle face embeddings.

Uses InsightFace ArcFace when available; falls back to OpenCV Haar for face
detection only (embeddings require InsightFace).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from path_config import CARTELLA_VOLTI_RIFERIMENTO_TEST_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MIN_IDENTITY_STABILITY = 0.50
DEFAULT_TOP_FACE_CANDIDATES = 5

_insightface_analyzer = None
_insightface_unavailable_logged = False
_haar_cascade: Optional[cv2.CascadeClassifier] = None


def _get_haar_cascade() -> cv2.CascadeClassifier:
    global _haar_cascade
    if _haar_cascade is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _haar_cascade = cv2.CascadeClassifier(cascade_path)
    return _haar_cascade


def _get_insightface_analyzer():
    """Lazy-init InsightFace FaceAnalysis (buffalo_l)."""
    global _insightface_analyzer, _insightface_unavailable_logged
    if _insightface_analyzer is not None:
        return _insightface_analyzer
    try:
        from insightface.app import FaceAnalysis

        analyzer = FaceAnalysis(name="buffalo_l")
        ctx_id = -1
        try:
            import torch

            if torch.cuda.is_available():
                ctx_id = 0
        except ImportError:
            pass
        analyzer.prepare(ctx_id=ctx_id, det_size=(640, 640))
        _insightface_analyzer = analyzer
        logger.info("InsightFace initialized for identity extraction (ctx_id=%s)", ctx_id)
        return analyzer
    except ImportError:
        if not _insightface_unavailable_logged:
            logger.warning(
                "InsightFace not installed — identity embeddings unavailable. "
                "Install: pip install insightface onnxruntime"
            )
            _insightface_unavailable_logged = True
        return None
    except Exception as exc:
        if not _insightface_unavailable_logged:
            logger.warning("InsightFace init failed: %s", exc)
            _insightface_unavailable_logged = True
        return None


def insightface_available() -> bool:
    return _get_insightface_analyzer() is not None


def _iter_face_files(faces_dir: Path) -> List[Path]:
    if not faces_dir.exists():
        return []
    files = [
        p
        for p in faces_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def _image_sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


@dataclass
class ReferenceFaceScore:
    path: Path
    sharpness: float
    face_area: int
    confidence: float
    has_face: bool


def score_reference_image(image_path: str) -> ReferenceFaceScore:
    """Score a reference image for face presence, size, and sharpness."""
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return ReferenceFaceScore(path, 0.0, 0, 0.0, False)

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = _image_sharpness(gray)

    analyzer = _get_insightface_analyzer()
    if analyzer is not None:
        faces = analyzer.get(image)
        if faces:
            best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            x1, y1, x2, y2 = best.bbox
            area = int(max(0, x2 - x1) * max(0, y2 - y1))
            conf = float(getattr(best, "det_score", 0.9))
            return ReferenceFaceScore(path, sharpness, area, conf, True)
        return ReferenceFaceScore(path, sharpness, 0, 0.0, False)

    cascade = _get_haar_cascade()
    detected = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
    if len(detected) == 0:
        return ReferenceFaceScore(path, sharpness, 0, 0.0, False)

    x, y, w, h = max(detected, key=lambda b: b[2] * b[3])
    return ReferenceFaceScore(path, sharpness, int(w * h), 0.75, True)


def _is_frame_crop(path: Path) -> bool:
    """True for video-extracted frame crops (not original user photos)."""
    name = path.name.lower()
    return name.startswith("frame_") or name.startswith("crop_")


@dataclass
class FullBodyScore:
    path: Path
    score: float
    face_area: int
    image_area: int
    is_original: bool


def score_full_body_image(image_path: str) -> FullBodyScore:
    """
    Score an image for full-body reference suitability.

    Prefers uncropped originals where the face occupies a moderate fraction
    of the frame (typical full-body / three-quarter shots).
    """
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return FullBodyScore(path, 0.0, 0, 0, not _is_frame_crop(path))

    height, width = image.shape[:2]
    image_area = height * width
    face_score = score_reference_image(str(path))
    is_original = not _is_frame_crop(path)

    if not face_score.has_face:
        body_score = float(image_area) * (2.0 if is_original else 0.5)
        return FullBodyScore(path, body_score, 0, image_area, is_original)

    face_ratio = face_score.face_area / max(image_area, 1)
    # Full-body shots: large canvas, face is a smaller fraction of the frame.
    body_score = float(image_area) * (1.0 - min(face_ratio * 4.0, 0.85))
    if is_original:
        body_score *= 2.5
    return FullBodyScore(
        path, body_score, face_score.face_area, image_area, is_original
    )


def select_best_full_body_image(
    search_dirs: List[str],
    *,
    faces_dir: Optional[str] = None,
) -> Path:
    """
    Pick the best full-body reference from search directories.

    Prefers original photos from inputs/ (not frame_*.jpg crops).
    """
    seen: set[str] = set()
    candidates: List[FullBodyScore] = []

    for directory in search_dirs:
        dir_path = Path(directory)
        if not dir_path.exists():
            continue
        for image_path in _iter_face_files(dir_path):
            resolved = str(image_path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(score_full_body_image(resolved))

    if not candidates and faces_dir:
        for image_path in _iter_face_files(Path(faces_dir)):
            candidates.append(score_full_body_image(str(image_path)))

    if not candidates:
        raise ValueError(
            f"No reference images found for full-body selection in {search_dirs}"
        )

    originals = [c for c in candidates if c.is_original and c.score > 0]
    pool = originals if originals else candidates
    pool.sort(key=lambda c: c.score, reverse=True)
    best = pool[0]
    logger.info(
        "Selected full-body reference: %s (score=%.0f, original=%s, face_area=%d)",
        best.path.name,
        best.score,
        best.is_original,
        best.face_area,
    )
    return best.path


def rank_reference_face_images(
    faces_dir: str,
    *,
    top_n: int = DEFAULT_TOP_FACE_CANDIDATES,
    require_face: bool = True,
) -> List[Path]:
    """
    Return up to ``top_n`` reference images with detectable faces, ranked by
    sharpness then face area.
    """
    directory = Path(faces_dir)
    scored: List[ReferenceFaceScore] = []
    for image_path in _iter_face_files(directory):
        score = score_reference_image(str(image_path))
        if require_face and not score.has_face:
            logger.debug("Skipping %s — no detectable face", image_path.name)
            continue
        scored.append(score)

    if not scored and require_face:
        raise ValueError(
            f"No reference images with detectable faces in {faces_dir}. "
            "Use clear front-facing photos with visible faces."
        )

    scored.sort(key=lambda s: (s.sharpness, s.face_area), reverse=True)
    selected = scored[:top_n]
    logger.info(
        "Selected %d/%d reference face images (top sharpness=%.1f)",
        len(selected),
        len(scored),
        selected[0].sharpness if selected else 0.0,
    )
    return [s.path for s in selected]


@dataclass
class FaceEmbedding:
    embedding: np.ndarray
    angle: Tuple[float, float, float]
    frame_number: int
    confidence: float
    source_image_path: str


@dataclass
class IdentitySuperVector:
    vector: np.ndarray
    source_embeddings: List[FaceEmbedding]
    fusion_method: str
    num_angles: int
    mean_confidence: float


class FaceEmbeddingExtractor:
    """Extract identity-preserving face embeddings via InsightFace ArcFace."""

    def __init__(self, model_name: str = "arcface", embedding_dim: int = 512):
        self.model_name = model_name
        self.embedding_dim = embedding_dim
        if not insightface_available():
            raise RuntimeError(
                "InsightFace is required for real identity embeddings. "
                "Install: pip install insightface onnxruntime"
            )
        logger.info("FaceEmbeddingExtractor initialized with %s", model_name)

    def extract_embedding(
        self,
        image_path: str,
        face_bbox: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        analyzer = _get_insightface_analyzer()
        if analyzer is None:
            raise RuntimeError("InsightFace analyzer not available")

        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Cannot read image: {image_path}")

        faces = analyzer.get(image)
        if not faces:
            raise ValueError(f"No face detected in {image_path}")

        if face_bbox is not None:
            x, y, w, h = face_bbox
            cx, cy = x + w / 2, y + h / 2

            def _dist(face) -> float:
                fx1, fy1, fx2, fy2 = face.bbox
                fcx, fcy = (fx1 + fx2) / 2, (fy1 + fy2) / 2
                return (fcx - cx) ** 2 + (fcy - cy) ** 2

            face = min(faces, key=_dist)
        else:
            face = max(
                faces,
                key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            )

        embedding = np.asarray(face.normed_embedding, dtype=np.float32)
        if embedding.shape[0] != self.embedding_dim:
            logger.warning(
                "Unexpected embedding dim %d (expected %d)",
                embedding.shape[0],
                self.embedding_dim,
            )
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        logger.debug("Embedding extracted from %s (norm=%.4f)", image_path, norm)
        return embedding

    def batch_extract_embeddings(self, image_paths: List[str]) -> List[np.ndarray]:
        embeddings = []
        for path in image_paths:
            embeddings.append(self.extract_embedding(path))
        return embeddings


class MultiAngleIdentityLock:
    """Multi-angle identity locking with real InsightFace embeddings."""

    def __init__(
        self,
        reference_faces_dir: str,
        num_angles: int = 5,
        embedding_model: str = "arcface",
        min_stability: float = MIN_IDENTITY_STABILITY,
        fail_on_low_stability: bool = False,
    ):
        self.reference_faces_dir = Path(reference_faces_dir).resolve()
        self.num_angles = num_angles
        self.min_stability = min_stability
        self.fail_on_low_stability = fail_on_low_stability

        self.extractor = FaceEmbeddingExtractor(model_name=embedding_model)
        self.embeddings: List[FaceEmbedding] = []
        self.super_vector: Optional[IdentitySuperVector] = None

        logger.info(
            "MultiAngleIdentityLock initialized (%d angles, dir=%s)",
            num_angles,
            self.reference_faces_dir,
        )

    def extract_multi_angle_embeddings(
        self,
        frame_data: Optional[List[Dict[str, Any]]] = None,
    ) -> List[FaceEmbedding]:
        logger.info("Extracting multi-angle embeddings (InsightFace)")

        if frame_data is None:
            frame_data = self._scan_reference_directory()

        if not frame_data:
            raise ValueError(
                f"No usable reference faces in {self.reference_faces_dir}. "
                "Ensure photos contain visible, front-facing faces."
            )

        if len(frame_data) < self.num_angles:
            logger.warning(
                "Found only %d face images, expected %d angles",
                len(frame_data),
                self.num_angles,
            )

        embeddings: List[FaceEmbedding] = []
        for i, frame_info in enumerate(frame_data):
            if len(embeddings) >= self.num_angles:
                break
            image_path = frame_info.get("path", "")
            angles = frame_info.get("angles", (0.0, 0.0, 0.0))
            frame_number = frame_info.get("frame_number", i)
            confidence = float(frame_info.get("confidence", 0.9))

            try:
                embedding_vector = self.extractor.extract_embedding(image_path)
            except ValueError as exc:
                logger.warning("Skipping %s: %s", Path(image_path).name, exc)
                continue

            embeddings.append(
                FaceEmbedding(
                    embedding=embedding_vector,
                    angle=angles,
                    frame_number=frame_number,
                    confidence=confidence,
                    source_image_path=image_path,
                )
            )
            logger.info(
                "  Angle %d/%d: %s conf=%.2f",
                len(embeddings),
                self.num_angles,
                Path(image_path).name,
                confidence,
            )

        if not embeddings:
            raise ValueError(
                f"No usable face embeddings from {self.reference_faces_dir}. "
                "Use clear front-facing photos with visible faces."
            )

        self.embeddings = embeddings
        return embeddings

    def _scan_reference_directory(self) -> List[Dict[str, Any]]:
        ranked_paths = rank_reference_face_images(
            str(self.reference_faces_dir),
            top_n=max(self.num_angles * 3, DEFAULT_TOP_FACE_CANDIDATES),
            require_face=True,
        )
        frame_data = []
        for i, image_path in enumerate(ranked_paths):
            score = score_reference_image(str(image_path))
            frame_data.append(
                {
                    "path": str(image_path.resolve()),
                    "angles": (i * 15.0, 0.0, 0.0),
                    "frame_number": i,
                    "confidence": score.confidence,
                }
            )
        logger.info("Found %d face-validated reference images", len(frame_data))
        return frame_data

    def create_super_vector(
        self,
        fusion_method: str = "weighted_mean",
        angle_weights: Optional[List[float]] = None,
    ) -> IdentitySuperVector:
        if not self.embeddings:
            raise ValueError("No embeddings extracted. Call extract_multi_angle_embeddings() first.")

        embedding_vectors = [emb.embedding for emb in self.embeddings]
        if fusion_method == "weighted_mean":
            super_vec = self._weighted_mean_fusion(embedding_vectors, angle_weights)
        elif fusion_method == "concat":
            super_vec = self._concatenation_fusion(embedding_vectors)
        elif fusion_method == "attention":
            super_vec = self._attention_fusion(embedding_vectors)
        else:
            raise ValueError(f"Unknown fusion method: {fusion_method}")

        super_vec = super_vec / np.linalg.norm(super_vec)
        mean_confidence = float(np.mean([emb.confidence for emb in self.embeddings]))
        self.super_vector = IdentitySuperVector(
            vector=super_vec,
            source_embeddings=self.embeddings,
            fusion_method=fusion_method,
            num_angles=len(self.embeddings),
            mean_confidence=mean_confidence,
        )
        logger.info(
            "Super-vector: shape=%s confidence=%.3f",
            super_vec.shape,
            mean_confidence,
        )
        return self.super_vector

    def _weighted_mean_fusion(
        self,
        embeddings: List[np.ndarray],
        weights: Optional[List[float]] = None,
    ) -> np.ndarray:
        if weights is None:
            weights = [1.0 / len(embeddings)] * len(embeddings)
        weights_arr = np.array(weights, dtype=np.float32)
        weights_arr = weights_arr / weights_arr.sum()
        fused = np.zeros_like(embeddings[0])
        for emb, weight in zip(embeddings, weights_arr):
            fused += emb * weight
        return fused

    def _concatenation_fusion(self, embeddings: List[np.ndarray]) -> np.ndarray:
        return np.concatenate(embeddings, axis=0)

    def _attention_fusion(self, embeddings: List[np.ndarray]) -> np.ndarray:
        confidences = np.array(
            [max(emb.confidence, 0.01) for emb in self.embeddings],
            dtype=np.float32,
        )
        weights = confidences / confidences.sum()
        fused = np.zeros_like(embeddings[0])
        for emb, weight in zip(embeddings, weights):
            fused += emb * weight
        return fused

    def lock_identity_3d(
        self,
        api_payload: Dict[str, Any],
        adapter_strength: float = 0.9,
    ) -> Dict[str, Any]:
        """
        Attach identity metadata to payload.

        Note: Fal Wan/Hunyuan I2V endpoints ignore ``identity_vector`` JSON.
        Visual identity for video MUST come from the PuLID first frame (``image_url``).
        """
        if self.super_vector is None:
            raise ValueError("No super-vector created. Call create_super_vector() first.")

        api_payload["identity_vector"] = self.super_vector.vector.tolist()
        api_payload["identity_adapter_strength"] = adapter_strength
        api_payload["identity_fusion_method"] = self.super_vector.fusion_method
        api_payload["identity_num_angles"] = self.super_vector.num_angles
        logger.info("Identity metadata attached (strength=%s)", adapter_strength)
        return api_payload

    def save_super_vector(self, output_path: str) -> str:
        if self.super_vector is None:
            raise ValueError("No super-vector to save")
        np.save(output_path, self.super_vector.vector)
        return output_path

    def load_super_vector(self, input_path: str) -> IdentitySuperVector:
        vector = np.load(input_path)
        self.super_vector = IdentitySuperVector(
            vector=vector,
            source_embeddings=[],
            fusion_method="loaded",
            num_angles=0,
            mean_confidence=1.0,
        )
        return self.super_vector

    def get_identity_stability_score(self) -> float:
        if len(self.embeddings) < 2:
            return 1.0

        similarities = []
        for i in range(len(self.embeddings)):
            for j in range(i + 1, len(self.embeddings)):
                sim = float(
                    np.dot(self.embeddings[i].embedding, self.embeddings[j].embedding)
                )
                similarities.append(sim)

        stability = float(np.mean(similarities))
        logger.info("Identity stability score: %.4f (%.1f%%)", stability, stability * 100)

        if stability < self.min_stability:
            msg = (
                f"Identity stability {stability * 100:.1f}% is below minimum "
                f"{self.min_stability * 100:.0f}%. Reference photos may be inconsistent "
                "or low quality."
            )
            if self.fail_on_low_stability:
                raise ValueError(msg)
            logger.warning("[IDENTITY] %s", msg)

        return stability


def extract_identity_from_directory(
    reference_dir: str,
    num_angles: int = 5,
    *,
    min_stability: float = MIN_IDENTITY_STABILITY,
    fail_on_low_stability: bool = False,
) -> IdentitySuperVector:
    locker = MultiAngleIdentityLock(
        reference_dir,
        num_angles=num_angles,
        min_stability=min_stability,
        fail_on_low_stability=fail_on_low_stability,
    )
    locker.extract_multi_angle_embeddings()
    locker.create_super_vector()
    locker.get_identity_stability_score()
    return locker.super_vector  # type: ignore[return-value]


def lock_identity_in_payload(
    api_payload: Dict[str, Any],
    reference_dir: str,
    adapter_strength: float = 0.9,
) -> Dict[str, Any]:
    locker = MultiAngleIdentityLock(reference_dir)
    locker.extract_multi_angle_embeddings()
    locker.create_super_vector()
    return locker.lock_identity_3d(api_payload, adapter_strength)
