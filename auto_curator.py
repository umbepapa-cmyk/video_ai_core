"""
Fase 3.17: Automated Data Curator (Biometric Filter).

Standalone utility to filter and rank face/body reference images from raw
input folders using OpenCV sharpness checks and MediaPipe face detection/mesh.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from gender_detector import (
    GenderResult,
    build_v3_face_caption,
    build_v3_tier_caption,
    detect_gender_from_folder,
    outputs_gender_path,
    resolve_gender,
    save_gender_json,
)

logger = logging.getLogger(__name__)

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import mediapipe as mp
except ImportError:
    mp = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv"}
SKIP_MEDIA_NAMES = {"face.jpg", "curated_fullbody_candidate.jpg"}

# MediaPipe Face Mesh landmark indices (critical visibility points).
_LEFT_EYE = 33
_RIGHT_EYE = 263
_NOSE_TIP = 1
_MOUTH_LEFT = 61
_MOUTH_RIGHT = 291
_CRITICAL_LANDMARKS = (_LEFT_EYE, _RIGHT_EYE, _NOSE_TIP, _MOUTH_LEFT, _MOUTH_RIGHT)

MIN_FACE_AREA_RATIO = 0.04
FULL_BODY_MAX_FACE_RATIO = 0.15
SMALL_FACE_CROP_RATIO = 0.15
LORA_FACE_SWEET_MIN = 0.08
LORA_FACE_SWEET_MAX = 0.40
DEFAULT_SHARPNESS_THRESHOLD = 24.0  # legacy default (20% lower than prior 30.0)
V3_SHARPNESS_THRESHOLD = 150.0
V3_MAX_YAW = 25.0
V3_MAX_PITCH = 20.0
V3_LANDMARK_VISIBILITY_MIN = 0.75
V3_TARGET_MIN = 25
V3_TARGET_MAX = 35
V3_DIVERSITY_HIST_THRESHOLD = 0.92
DEFAULT_TOP_N = 5
DEFAULT_LORA_TOP_N = 20
DEFAULT_MIN_SHORT_SIDE = 768
DEFAULT_INPUTS_ROOT = Path("inputs")
DATASETS_ROOT = Path("datasets")
VIDEO_SAMPLE_FPS = 1.0
VIDEO_SCENE_CHANGE_HIST = 0.78
# Anti-runaway for corrupted/infinite video loops only — NOT a user export cap.
MAX_FRAMES_PER_VIDEO_SAFETY = 500
SHARPNESS_NORMALIZE_SHORT = 720
TIER_EXPORT_CAPS: dict[str, int] = {
    "A": 50,
    "B": 30,
    "C": 150,
    "D": 80,
    "E": 40,
    "F": 40,
}
DEFAULT_LORA_CAPTION_SUFFIX = "portrait, front facing, natural skin, high quality"
BRIGHTNESS_MIN = 45.0
BRIGHTNESS_MAX = 210.0

SUBJECT_TRIGGER_MAP: dict[int, str] = {
    1: "soggetto_uno",
    2: "soggetto_due",
    3: "soggetto_tre",
    4: "soggetto_quattro",
    5: "soggetto_cinque",
    6: "soggetto_sei",
}

V3_FACE_CAPTION = (
    "ohwx {trigger}, face portrait, photorealistic, sharp focus, neutral expression"
)
V3_BODY_CAPTION = (
    "ohwx {trigger}, full body portrait, photorealistic, {trigger} standing, "
    "anatomically correct"
)

ALL_SUBJECTS = list(range(1, 6))

# --- Tier system A-F (v3 adaptive export) ---
TIER_NAMES = ("A", "B", "C", "D", "E", "F")
TIER_FOLDERS: dict[str, str] = {
    "A": "face_front",
    "B": "face_profile",
    "C": "body_back",
    "D": "body_full",
    "E": "body_partial",
    "F": "detail_macro",
}
V3_TIER_SUBFOLDERS = tuple(TIER_FOLDERS.values())
SCAN_EXCLUDE_DIR_PREFIXES = ("scarti_", "Test_Soggetto", "VIP_Dataset_", "Mannheim")
SCAN_EXCLUDE_DIR_NAMES = frozenset({"datasets", "Mannheim", "__pycache__"})
PROFILE_YAW_MIN = 18.0
PROFILE_YAW_MAX = 55.0
PARTIAL_FACE_MIN = 0.12
PARTIAL_FACE_MAX = 0.55
DETAIL_MACRO_MAX_FACE = 0.08


@dataclass(frozen=True)
class QualityProfile:
    mode: str
    sharp_ab: float
    sharp_cf: float
    max_yaw_front: float
    profile_yaw_min: float
    relax_tiers_cf: bool
    warn_low: bool


@dataclass
class TierFrame:
    tier: str
    source_label: str
    source_path: Path
    image_path: Optional[Path]
    image: "np.ndarray"
    sharpness: float
    roi_sharpness: float
    face_count: int
    face_area_ratio: float
    yaw: float
    pitch: float
    symmetry: float = 55.0
    brightness: float = 128.0
    lora_score: float = 0.0
    detail_hint: str = ""
    processed_image: Optional["np.ndarray"] = None


def resolve_quality_mode(loose_count: int) -> QualityProfile:
    """Pick adaptive quality mode from loose media scan count.

    More source files → lower thresholds (more permissive on body/macro tiers).
    """
    if loose_count >= 200:
        return QualityProfile("archive", 70.0, 35.0, 28.0, 18.0, True, False)
    if loose_count >= 100:
        return QualityProfile("relaxed", 65.0, 30.0, 32.0, 16.0, True, False)
    if loose_count >= 30:
        return QualityProfile("standard", 60.0, 25.0, 35.0, 14.0, True, False)
    return QualityProfile("best-effort", 50.0, 20.0, 40.0, 12.0, True, loose_count < 10)


def _path_excluded_from_scan(path: Path) -> bool:
    for part in path.parts:
        lower = part.lower()
        if lower in {n.lower() for n in SCAN_EXCLUDE_DIR_NAMES}:
            return True
        for prefix in SCAN_EXCLUDE_DIR_PREFIXES:
            if part.startswith(prefix) or lower.startswith(prefix.lower()):
                return True
    return False


def _infer_detail_hint(image: "np.ndarray", face_count: int) -> str:
    """Heuristic body-part hint for tier F captions."""
    h, w = image.shape[:2]
    if h <= 0 or w <= 0:
        return "skin texture detail"
    if face_count == 1:
        return "eye macro detail"
    aspect = w / float(h)
    if aspect > 1.25:
        return "legs and feet detail"
    if aspect < 0.75:
        return "torso and hips detail"
    cy = h // 2
    lower = image[cy:, :]
    upper = image[:cy, :]
    lower_var = float(cv2.Laplacian(cv2.cvtColor(lower, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    upper_var = float(cv2.Laplacian(cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    if lower_var >= upper_var * 1.15:
        return "buttocks and legs macro detail"
    return "torso macro detail"

# Source folders whose best face.jpg should also be mirrored to test targets.
TEST_FOLDER_MAPPINGS: dict[str, str] = {
    "Soggetto 4": "Test_Soggetto4_Donna",
    "Soggetto 5": "Test_Soggetto5_Uomo",
}


@dataclass
class FaceMetrics:
    face_area_ratio: float
    symmetry_score: float
    yaw: float = 0.0
    pitch: float = 0.0
    brightness: float = 128.0


@dataclass
class Candidate:
    source_label: str
    source_path: Path
    image_path: Optional[Path]
    image: "np.ndarray"
    sharpness: float
    symmetry: float
    face_area_ratio: float
    score: float
    yaw: float = 0.0
    pitch: float = 0.0
    brightness: float = 128.0
    lora_score: float = 0.0
    processed_image: Optional["np.ndarray"] = None
    face_crop: Optional["np.ndarray"] = None
    body_crop: Optional["np.ndarray"] = None


@dataclass
class CurationResult:
    folder: Path
    best: Optional[Candidate]
    accepted_count: int
    used_fallback: bool


def _ensure_mediapipe_solutions() -> None:
    """Fail fast with a pinned-version hint when mp.solutions is unavailable."""
    if mp is None:
        return
    if not hasattr(mp, "solutions"):
        logger.error(
            "MediaPipe install incompatible (mp.solutions missing). "
            "Install pinned build: pip install 'mediapipe>=0.10.0,<0.10.31'"
        )
        sys.exit(1)


class BiometricCurator:
    """Filter and score images/frames using biometric quality heuristics."""

    def __init__(
        self,
        sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
        min_face_area_ratio: float = MIN_FACE_AREA_RATIO,
    ) -> None:
        _ensure_mediapipe_solutions()
        self.sharpness_threshold = sharpness_threshold
        self.min_face_area_ratio = min_face_area_ratio
        self._face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=0.5,
        )
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._insightface_app: Any = None
        self._insightface_failed = False
        self._insightface_cache: dict[int, Optional[tuple[int, int, int, int]]] = {}

    def close(self) -> None:
        self._face_detection.close()
        self._face_mesh.close()

    def __enter__(self) -> "BiometricCurator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _to_rgb(image: "np.ndarray") -> "np.ndarray":
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    @staticmethod
    def _gray(image: "np.ndarray") -> "np.ndarray":
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _measure_brightness(image: "np.ndarray") -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(gray.mean())

    @staticmethod
    def _estimate_head_pose(
        landmarks: Any, width: int, height: int
    ) -> tuple[float, float]:
        """Approximate yaw/pitch in degrees from MediaPipe 468 landmarks."""
        left_eye = landmarks[_LEFT_EYE]
        right_eye = landmarks[_RIGHT_EYE]
        nose = landmarks[_NOSE_TIP]
        chin_idx = 152
        _ = landmarks[chin_idx]

        eye_mid_x = (left_eye.x + right_eye.x) / 2.0
        eye_mid_y = (left_eye.y + right_eye.y) / 2.0
        yaw = (nose.x - eye_mid_x) * 120.0
        pitch = (nose.y - eye_mid_y) * 80.0
        return float(yaw), float(pitch)

    def _bbox_from_relative(
        self, rel_bbox: Any, width: int, height: int
    ) -> Optional[tuple[int, int, int, int]]:
        x1 = max(0, int(rel_bbox.xmin * width))
        y1 = max(0, int(rel_bbox.ymin * height))
        x2 = min(width, int((rel_bbox.xmin + rel_bbox.width) * width))
        y2 = min(height, int((rel_bbox.ymin + rel_bbox.height) * height))
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    def _insightface_largest_bbox(
        self, image: "np.ndarray"
    ) -> Optional[tuple[int, int, int, int]]:
        cache_key = id(image)
        if cache_key in self._insightface_cache:
            return self._insightface_cache[cache_key]
        if self._insightface_failed:
            self._insightface_cache[cache_key] = None
            return None
        try:
            if self._insightface_app is None:
                from insightface.app import FaceAnalysis

                app = FaceAnalysis(name="buffalo_l")
                app.prepare(ctx_id=-1, det_size=(640, 640))
                self._insightface_app = app
            faces = self._insightface_app.get(image)
            if not faces:
                self._insightface_cache[cache_key] = None
                return None
            best = max(
                faces,
                key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            )
            x1, y1, x2, y2 = (int(v) for v in best.bbox)
            h, w = image.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                self._insightface_cache[cache_key] = None
                return None
            bbox = (x1, y1, x2, y2)
            self._insightface_cache[cache_key] = bbox
            return bbox
        except Exception:
            self._insightface_failed = True
            self._insightface_cache[cache_key] = None
            return None

    def _face_detection_bbox(
        self, image: "np.ndarray"
    ) -> Optional[tuple[int, int, int, int]]:
        results = self._face_detection.process(self._to_rgb(image))
        detections = results.detections or []
        h, w = image.shape[:2]
        if not detections:
            return self._insightface_largest_bbox(image)
        if len(detections) == 1:
            return self._bbox_from_relative(
                detections[0].location_data.relative_bounding_box, w, h
            )
        best_det = max(
            detections,
            key=lambda d: d.location_data.relative_bounding_box.width
            * d.location_data.relative_bounding_box.height,
        )
        bbox = self._bbox_from_relative(
            best_det.location_data.relative_bounding_box, w, h
        )
        return bbox or self._insightface_largest_bbox(image)

    @staticmethod
    def smart_crop_for_lora(
        image: "np.ndarray",
        bbox: tuple[int, int, int, int],
        face_area_ratio: float,
    ) -> "np.ndarray":
        """Crop around face with padding, or upper-body center crop for small faces."""
        h, w = image.shape[:2]
        x1, y1, x2, y2 = bbox
        fw, fh = x2 - x1, y2 - y1

        if face_area_ratio < SMALL_FACE_CROP_RATIO:
            pad = 1.75
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            half_w = int(max(fw, fh) * pad)
            half_h = int(max(fw, fh) * pad * 1.15)
            nx1 = max(0, cx - half_w)
            ny1 = max(0, cy - int(half_h * 0.55))
            nx2 = min(w, cx + half_w)
            ny2 = min(h, cy + int(half_h * 0.85))
            crop = image[ny1:ny2, nx1:nx2]
            if crop.size > 0:
                return crop

        pad_x = int(fw * 0.65)
        pad_y_top = int(fh * 0.55)
        pad_y_bot = int(fh * 0.75)
        nx1 = max(0, x1 - pad_x)
        ny1 = max(0, y1 - pad_y_top)
        nx2 = min(w, x2 + pad_x)
        ny2 = min(h, y2 + pad_y_bot)
        crop = image[ny1:ny2, nx1:nx2]
        return crop if crop.size > 0 else image

    @staticmethod
    def crop_face_tight(
        image: "np.ndarray",
        bbox: tuple[int, int, int, int],
    ) -> "np.ndarray":
        """Tight face crop for identity LoRA training."""
        h, w = image.shape[:2]
        x1, y1, x2, y2 = bbox
        fw, fh = x2 - x1, y2 - y1
        pad_x = int(fw * 0.25)
        pad_y = int(fh * 0.30)
        nx1 = max(0, x1 - pad_x)
        ny1 = max(0, y1 - pad_y)
        nx2 = min(w, x2 + pad_x)
        ny2 = min(h, y2 + pad_y)
        crop = image[ny1:ny2, nx1:nx2]
        return crop if crop.size > 0 else image

    @staticmethod
    def crop_body_three_quarter(
        image: "np.ndarray",
        bbox: tuple[int, int, int, int],
    ) -> "np.ndarray":
        """3/4 crop: head, bust, hips/waist."""
        h, w = image.shape[:2]
        x1, y1, x2, y2 = bbox
        fw, fh = x2 - x1, y2 - y1
        cx = (x1 + x2) // 2
        half_w = int(max(fw, fh) * 1.35)
        ny1 = max(0, y1 - int(fh * 0.45))
        ny2 = min(h, y2 + int(fh * 3.2))
        nx1 = max(0, cx - half_w)
        nx2 = min(w, cx + half_w)
        crop = image[ny1:ny2, nx1:nx2]
        return crop if crop.size > 0 else image

    @staticmethod
    def crop_full_frame(image: "np.ndarray") -> "np.ndarray":
        """Normalized full frame (no crop)."""
        return image

    def prepare_crop_for_mode(
        self,
        image: "np.ndarray",
        mode: str,
        face_area_ratio: float,
        min_short_side: int = DEFAULT_MIN_SHORT_SIDE,
    ) -> "np.ndarray":
        bbox = self._face_detection_bbox(image)
        if bbox is None:
            processed = image
        elif mode == "face":
            processed = self.crop_face_tight(image, bbox)
        elif mode == "body":
            processed = self.crop_body_three_quarter(image, bbox)
        else:
            processed = self.crop_full_frame(image)
        return self.upscale_if_needed(processed, min_short_side=min_short_side)

    @staticmethod
    def upscale_if_needed(
        image: "np.ndarray", min_short_side: int = DEFAULT_MIN_SHORT_SIDE
    ) -> "np.ndarray":
        """Upscale with PIL LANCZOS when short side is below target."""
        h, w = image.shape[:2]
        short = min(h, w)
        if short >= min_short_side:
            return image
        scale = min_short_side / float(short)
        new_w = max(min_short_side, int(round(w * scale)))
        new_h = max(min_short_side, int(round(h * scale)))
        try:
            from PIL import Image
        except ImportError:
            return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        resized = pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return cv2.cvtColor(np.array(resized), cv2.COLOR_RGB2BGR)

    @staticmethod
    def compute_lora_score(
        sharpness: float,
        symmetry: float,
        face_area_ratio: float,
        yaw: float,
        pitch: float,
        brightness: float,
    ) -> float:
        sharpness_norm = min(100.0, sharpness / 1.2)
        frontal_bonus = max(0.0, 25.0 - abs(yaw) * 1.8 - abs(pitch) * 1.2)
        area_bonus = 0.0
        if LORA_FACE_SWEET_MIN <= face_area_ratio <= LORA_FACE_SWEET_MAX:
            center = (LORA_FACE_SWEET_MIN + LORA_FACE_SWEET_MAX) / 2.0
            area_bonus = 20.0 - abs(face_area_ratio - center) * 120.0
            area_bonus = max(0.0, min(20.0, area_bonus))
        exposure_penalty = 0.0
        if brightness < BRIGHTNESS_MIN or brightness > BRIGHTNESS_MAX:
            exposure_penalty = 15.0
        blur_penalty = 10.0 if sharpness < DEFAULT_SHARPNESS_THRESHOLD else 0.0
        raw = (
            sharpness_norm * 0.35
            + symmetry * 0.30
            + frontal_bonus
            + area_bonus
            - exposure_penalty
            - blur_penalty
        )
        return round(max(0.0, min(100.0, raw)), 1)

    def prepare_lora_image(
        self,
        image: "np.ndarray",
        face_area_ratio: float,
        min_short_side: int = DEFAULT_MIN_SHORT_SIDE,
    ) -> "np.ndarray":
        bbox = self._face_detection_bbox(image)
        if bbox is None:
            processed = image
        else:
            processed = self.smart_crop_for_lora(image, bbox, face_area_ratio)
        return self.upscale_if_needed(processed, min_short_side=min_short_side)

    def check_single_face(self, image: "np.ndarray") -> bool:
        """Return True only when exactly one face is detected."""
        results = self._face_detection.process(self._to_rgb(image))
        detections = results.detections or []
        return len(detections) == 1

    def check_sharpness(self, image: "np.ndarray", threshold: Optional[float] = None) -> bool:
        """Return True when Laplacian variance meets the blur threshold."""
        limit = self.sharpness_threshold if threshold is None else threshold
        gray = self._gray(image)
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return variance >= limit

    def measure_sharpness(
        self, image: "np.ndarray", *, normalize: bool = True
    ) -> float:
        if normalize:
            image = self._normalize_for_sharpness(image)
        gray = self._gray(image)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def _normalize_for_sharpness(image: "np.ndarray") -> "np.ndarray":
        h, w = image.shape[:2]
        short = min(h, w)
        if short <= SHARPNESS_NORMALIZE_SHORT:
            return image
        scale = SHARPNESS_NORMALIZE_SHORT / float(short)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def measure_center_sharpness(self, image: "np.ndarray") -> float:
        h, w = image.shape[:2]
        ch, cw = max(1, int(h * 0.6)), max(1, int(w * 0.6))
        y1 = max(0, (h - ch) // 2)
        x1 = max(0, (w - cw) // 2)
        crop = image[y1 : y1 + ch, x1 : x1 + cw]
        if crop.size == 0:
            return 0.0
        return self.measure_sharpness(crop, normalize=True)

    def measure_roi_sharpness(
        self, image: "np.ndarray", bbox: Optional[tuple[int, int, int, int]] = None
    ) -> float:
        """Laplacian variance on ROI (full frame or bbox) for tier F."""
        h, w = image.shape[:2]
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            roi = image[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
            if roi.size > 0:
                return self.measure_sharpness(roi, normalize=True)
        return self.measure_sharpness(image, normalize=True)

    def count_faces(self, image: "np.ndarray") -> int:
        results = self._face_detection.process(self._to_rgb(image))
        detections = results.detections or []
        if detections:
            return 1
        return 1 if self._insightface_largest_bbox(image) is not None else 0

    def analyze_frame(
        self, image: "np.ndarray"
    ) -> tuple[int, float, float, float, float, float, Optional[tuple[int, int, int, int]]]:
        """Return face_count, area_ratio, yaw, pitch, symmetry, brightness, bbox."""
        bbox = self._face_detection_bbox(image)
        results = self._face_detection.process(self._to_rgb(image))
        detections = results.detections or []
        if detections:
            face_count = 1
        elif bbox is not None:
            face_count = 1
        else:
            face_count = 0
        if face_count != 1:
            brightness = self._measure_brightness(image)
            return face_count, 0.0, 0.0, 0.0, 55.0, brightness, None
        height, width = image.shape[:2]
        area_ratio = 0.0
        yaw = pitch = 0.0
        symmetry = 55.0
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            area_ratio = ((x2 - x1) * (y2 - y1)) / float(height * width)

        mesh = self._face_mesh.process(self._to_rgb(image))
        if mesh.multi_face_landmarks:
            landmarks = mesh.multi_face_landmarks[0].landmark
            xs = [lm.x * width for lm in landmarks]
            ys = [lm.y * height for lm in landmarks]
            fw = max(xs) - min(xs)
            fh = max(ys) - min(ys)
            if fw > 1 and fh > 1:
                area_ratio = max(area_ratio, (fw * fh) / float(height * width))
            yaw, pitch = self._estimate_head_pose(landmarks, width, height)
            left_eye = landmarks[_LEFT_EYE]
            right_eye = landmarks[_RIGHT_EYE]
            nose = landmarks[_NOSE_TIP]
            mid_x = (left_eye.x + right_eye.x) / 2.0
            eye_asymmetry = abs(left_eye.x - mid_x) - abs(right_eye.x - mid_x)
            nose_offset = abs(nose.x - mid_x)
            symmetry = max(
                0.0,
                100.0 - (abs(eye_asymmetry) * 400.0 + nose_offset * 250.0),
            )

        brightness = self._measure_brightness(image)
        return face_count, area_ratio, yaw, pitch, symmetry, brightness, bbox

    def classify_tier(
        self,
        image: "np.ndarray",
        profile: QualityProfile,
    ) -> Optional[TierFrame]:
        """Classify a frame into tier A-F using adaptive thresholds."""
        sharpness = self.measure_sharpness(image)
        (
            face_count,
            area_ratio,
            yaw,
            pitch,
            symmetry,
            brightness,
            bbox,
        ) = self.analyze_frame(image)
        roi_sharpness = self.measure_roi_sharpness(image, bbox)
        center_sharpness = self.measure_center_sharpness(image)
        effective_cf = max(sharpness, roi_sharpness, center_sharpness)
        lora_score = self.compute_lora_score(
            sharpness, symmetry, area_ratio, yaw, pitch, brightness
        )

        tier: Optional[str] = None
        detail_hint = ""

        if face_count == 1:
            if (
                abs(yaw) <= profile.max_yaw_front
                and abs(pitch) <= V3_MAX_PITCH
                and effective_cf >= profile.sharp_ab
            ):
                tier = "A"
            elif (
                profile.profile_yaw_min <= abs(yaw) <= PROFILE_YAW_MAX
                and effective_cf >= profile.sharp_ab
            ):
                tier = "B"
            elif PARTIAL_FACE_MIN <= area_ratio <= PARTIAL_FACE_MAX and effective_cf >= profile.sharp_cf:
                tier = "E"
            elif area_ratio <= FULL_BODY_MAX_FACE_RATIO and effective_cf >= profile.sharp_cf:
                tier = "D"

        if tier is None and face_count == 0:
            if effective_cf >= profile.sharp_cf:
                tier = "C"
            elif roi_sharpness >= profile.sharp_cf * 0.85 or center_sharpness >= profile.sharp_cf * 0.85:
                tier = "F"
                detail_hint = _infer_detail_hint(image, face_count)

        if tier is None and face_count == 1 and area_ratio <= DETAIL_MACRO_MAX_FACE:
            if roi_sharpness >= profile.sharp_cf * 0.85 or center_sharpness >= profile.sharp_cf * 0.85:
                tier = "F"
                detail_hint = _infer_detail_hint(image, face_count)

        if tier is None and profile.mode == "best-effort":
            if face_count == 1 and effective_cf >= profile.sharp_ab * 0.6:
                tier = "A" if abs(yaw) <= profile.max_yaw_front else "B"
            elif face_count == 0 and effective_cf >= profile.sharp_cf * 0.6:
                tier = "C"
            elif effective_cf >= profile.sharp_cf * 0.5:
                tier = "F"
                detail_hint = _infer_detail_hint(image, face_count)
            elif face_count <= 1:
                tier = "D" if area_ratio <= FULL_BODY_MAX_FACE_RATIO else "E"

        if tier is None:
            return None

        if not profile.relax_tiers_cf and tier in {"C", "D", "E", "F"}:
            if effective_cf < profile.sharp_cf * 0.85:
                return None

        processed = image
        if face_count == 1 and bbox is not None:
            if tier in {"A", "B"}:
                processed = self.crop_face_tight(image, bbox)
            elif tier == "E":
                processed = self.crop_body_three_quarter(image, bbox)
            elif tier == "D":
                processed = self.smart_crop_for_lora(image, bbox, area_ratio)
        elif tier == "F" and bbox is not None:
            h, w = image.shape[:2]
            x1, y1, x2, y2 = bbox
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            half = max(x2 - x1, y2 - y1)
            nx1 = max(0, cx - half)
            ny1 = max(0, cy - half)
            nx2 = min(w, cx + half)
            ny2 = min(h, cy + half)
            crop = image[ny1:ny2, nx1:nx2]
            if crop.size > 0:
                processed = crop
        else:
            processed = self.crop_full_frame(image)

        if tier == "F" and not detail_hint:
            detail_hint = _infer_detail_hint(image, face_count)

        return TierFrame(
            tier=tier,
            source_label="",
            source_path=Path("."),
            image_path=None,
            image=image,
            sharpness=sharpness,
            roi_sharpness=roi_sharpness,
            face_count=face_count,
            face_area_ratio=area_ratio,
            yaw=yaw,
            pitch=pitch,
            symmetry=symmetry,
            brightness=brightness,
            lora_score=lora_score,
            detail_hint=detail_hint,
            processed_image=processed,
        )

    def check_face_size_and_occlusion(
        self,
        image: "np.ndarray",
        *,
        strict: bool = False,
    ) -> tuple[bool, Optional[FaceMetrics]]:
        """
        Validate face size and critical landmark visibility.

        Rejects tiny faces, occluded landmarks (phone/mirror), and extreme profiles.
        When strict=True (v3), applies tighter yaw/pitch and visibility thresholds.
        """
        height, width = image.shape[:2]
        image_area = float(height * width)
        if image_area <= 0:
            return False, None

        results = self._face_mesh.process(self._to_rgb(image))
        if not results.multi_face_landmarks:
            return False, None

        landmarks = results.multi_face_landmarks[0].landmark
        xs = [lm.x * width for lm in landmarks]
        ys = [lm.y * height for lm in landmarks]

        face_width = max(xs) - min(xs)
        face_height = max(ys) - min(ys)
        if face_height <= 1.0 or face_width <= 1.0:
            return False, None

        face_area_ratio = (face_width * face_height) / image_area
        if face_area_ratio < self.min_face_area_ratio:
            return False, None

        aspect = face_width / face_height
        if aspect < 0.35:
            return False, None

        visibility_min = V3_LANDMARK_VISIBILITY_MIN if strict else 0.0
        for index in _CRITICAL_LANDMARKS:
            lm = landmarks[index]
            visibility = float(getattr(lm, "visibility", 1.0))
            if visibility < visibility_min:
                return False, None
            if not (0.02 <= lm.x <= 0.98 and 0.02 <= lm.y <= 0.98):
                return False, None
            if abs(lm.z) > 0.08:
                return False, None

        left_eye = landmarks[_LEFT_EYE]
        right_eye = landmarks[_RIGHT_EYE]
        eye_distance = abs(left_eye.x - right_eye.x) * width
        min_expected = max(8.0, face_width * 0.18)
        if eye_distance < min_expected:
            return False, None

        nose = landmarks[_NOSE_TIP]
        mid_x = (left_eye.x + right_eye.x) / 2.0
        eye_asymmetry = abs(left_eye.x - mid_x) - abs(right_eye.x - mid_x)
        nose_offset = abs(nose.x - mid_x)
        symmetry = max(
            0.0,
            100.0 - (abs(eye_asymmetry) * 400.0 + nose_offset * 250.0),
        )

        mouth_left = landmarks[_MOUTH_LEFT]
        mouth_right = landmarks[_MOUTH_RIGHT]
        mouth_width = abs(mouth_right.x - mouth_left.x) * width
        if mouth_width < face_width * 0.25:
            return False, None

        yaw, pitch = self._estimate_head_pose(landmarks, width, height)
        max_yaw = V3_MAX_YAW if strict else 35.0
        max_pitch = V3_MAX_PITCH if strict else 30.0
        if abs(yaw) > max_yaw or abs(pitch) > max_pitch:
            return False, None

        brightness = self._measure_brightness(image)
        if brightness < BRIGHTNESS_MIN - 15 or brightness > BRIGHTNESS_MAX + 15:
            return False, None

        return True, FaceMetrics(
            face_area_ratio=face_area_ratio,
            symmetry_score=symmetry,
            yaw=yaw,
            pitch=pitch,
            brightness=brightness,
        )

    def evaluate(
        self,
        image: "np.ndarray",
        source_label: str,
        source_path: Path,
        image_path: Optional[Path] = None,
        *,
        strict: bool = False,
    ) -> tuple[Optional[Candidate], str]:
        """Run all filters; return candidate on success or rejection reason."""
        if not self.check_single_face(image):
            detections = self._face_detection.process(self._to_rgb(image)).detections or []
            if len(detections) == 0:
                return None, "Nessuna faccia rilevata"
            return None, "Troppe facce rilevate"

        sharpness_limit = V3_SHARPNESS_THRESHOLD if strict else self.sharpness_threshold
        sharpness = self.measure_sharpness(image)
        if sharpness < sharpness_limit:
            return None, "Immagine sfocata"

        ok, metrics = self.check_face_size_and_occlusion(image, strict=strict)
        if not ok or metrics is None:
            if metrics is None:
                mesh = self._face_mesh.process(self._to_rgb(image))
                if not mesh.multi_face_landmarks:
                    return None, "Landmark facciali non rilevati"
                height, width = image.shape[:2]
                landmarks = mesh.multi_face_landmarks[0].landmark
                xs = [lm.x * width for lm in landmarks]
                ys = [lm.y * height for lm in landmarks]
                face_area_ratio = ((max(xs) - min(xs)) * (max(ys) - min(ys))) / float(height * width)
                if face_area_ratio < self.min_face_area_ratio:
                    return None, "Volto troppo piccolo"
                aspect = (max(xs) - min(xs)) / max(1.0, (max(ys) - min(ys)))
                if aspect < 0.35:
                    return None, "Profilo estremo rilevato"
                if strict:
                    yaw, pitch = self._estimate_head_pose(landmarks, width, height)
                    if abs(yaw) > V3_MAX_YAW or abs(pitch) > V3_MAX_PITCH:
                        return None, f"Pose non frontale (yaw={yaw:.1f}, pitch={pitch:.1f})"
            return None, "Landmark critici mancanti o occlusi"

        score = self.compute_score(sharpness, metrics.symmetry_score)
        lora_score = self.compute_lora_score(
            sharpness,
            metrics.symmetry_score,
            metrics.face_area_ratio,
            metrics.yaw,
            metrics.pitch,
            metrics.brightness,
        )
        processed = self.prepare_lora_image(image, metrics.face_area_ratio)
        face_crop = self.prepare_crop_for_mode(image, "face", metrics.face_area_ratio)
        body_crop = self.prepare_crop_for_mode(image, "body", metrics.face_area_ratio)
        candidate = Candidate(
            source_label=source_label,
            source_path=source_path,
            image_path=image_path,
            image=image,
            sharpness=sharpness,
            symmetry=metrics.symmetry_score,
            face_area_ratio=metrics.face_area_ratio,
            score=score,
            yaw=metrics.yaw,
            pitch=metrics.pitch,
            brightness=metrics.brightness,
            lora_score=lora_score,
            processed_image=processed,
        )
        candidate.face_crop = face_crop
        candidate.body_crop = body_crop
        return candidate, ""

    @staticmethod
    def compute_score(sharpness: float, symmetry: float) -> float:
        sharpness_norm = min(100.0, (sharpness / 100.0) * 100.0)
        return round(sharpness_norm * 0.5 + symmetry * 0.5, 1)


def _require_dependencies() -> None:
    missing: list[str] = []
    if cv2 is None:
        missing.append("opencv-python")
    if mp is None:
        missing.append("mediapipe>=0.10.0,<0.10.31")
    if np is None:
        missing.append("numpy")
    if missing:
        packages = ", ".join(missing)
        logger.error(
            "Dipendenze mancanti: %s. Installa con: pip install %s",
            packages,
            " ".join(missing),
        )
        sys.exit(1)
    _ensure_mediapipe_solutions()


def _iter_media_files(input_dir: Path, recursive: bool) -> Iterator[Path]:
    if recursive:
        paths = sorted(input_dir.rglob("*"))
    else:
        paths = sorted(input_dir.iterdir())

    for path in paths:
        if not path.is_file():
            continue
        if _path_excluded_from_scan(path):
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
            name_lower = path.name.lower()
            if name_lower in SKIP_MEDIA_NAMES or name_lower.startswith("curated_face_"):
                continue
            yield path


def _read_image(path: Path) -> Optional["np.ndarray"]:
    image = cv2.imread(str(path))
    if image is None:
        logger.warning("Impossibile leggere immagine: %s", path)
    return image


def _video_frame_sharpness(frame: "np.ndarray") -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    short = min(h, w)
    if short > SHARPNESS_NORMALIZE_SHORT:
        scale = SHARPNESS_NORMALIZE_SHORT / float(short)
        gray = cv2.resize(
            gray,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _sample_video_frames_diverse(
    path: Path,
    *,
    min_sharpness: float = 0.0,
    diversity_threshold: float = V3_DIVERSITY_HIST_THRESHOLD,
) -> Iterator[tuple["np.ndarray", str]]:
    """Yield every clean, non-redundant frame from a video (~1 fps + scene cuts).

    Dedup is similarity-only (histogram correlation vs already-kept frames from
    this video). There is no per-video count cap; MAX_FRAMES_PER_VIDEO_SAFETY
    stops only corrupted/infinite decode loops.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        logger.warning("Impossibile aprire video: %s", path)
        return

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 24.0)
    if fps <= 0:
        fps = 24.0
    step = max(1, int(round(fps / VIDEO_SAMPLE_FPS)))
    frame_index = 0
    scanned = 0
    kept = 0
    accepted_frames: list["np.ndarray"] = []
    last_sampled_frame: Optional["np.ndarray"] = None
    hit_safety = False

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            take = frame_index % step == 0
            if not take and last_sampled_frame is not None:
                if _histogram_correlation(frame, last_sampled_frame) < VIDEO_SCENE_CHANGE_HIST:
                    take = True
            if take:
                scanned += 1
                if scanned > MAX_FRAMES_PER_VIDEO_SAFETY:
                    hit_safety = True
                    logger.warning(
                        "Video %s: safety cap %d candidati — possibile file corrotto",
                        path.name,
                        MAX_FRAMES_PER_VIDEO_SAFETY,
                    )
                    break
                second = frame_index / fps
                label = f"{path.name} @ {second:.0f}s"
                sharpness = _video_frame_sharpness(frame)
                if sharpness >= min_sharpness:
                    too_similar = any(
                        _histogram_correlation(frame, existing) >= diversity_threshold
                        for existing in accepted_frames
                    )
                    if not too_similar:
                        accepted_frames.append(frame)
                        kept += 1
                        yield frame, label
                last_sampled_frame = frame
            frame_index += 1
    finally:
        capture.release()

    if scanned == 0:
        logger.warning("Nessun frame campionato da video: %s", path)
    else:
        logger.debug(
            "Video %s: %d candidati @~%.0ffps, %d puliti+diversi%s",
            path.name,
            scanned,
            VIDEO_SAMPLE_FPS,
            kept,
            " (safety cap)" if hit_safety else "",
        )


def _sample_video_frames(
    path: Path,
    *,
    min_sharpness: float = 0.0,
    diversity_threshold: float = V3_DIVERSITY_HIST_THRESHOLD,
) -> Iterator[tuple["np.ndarray", str]]:
    """Extract all clean, non-redundant frames from a video."""
    yield from _sample_video_frames_diverse(
        path,
        min_sharpness=min_sharpness,
        diversity_threshold=diversity_threshold,
    )


def _save_candidate_image(candidate: Candidate, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), candidate.image)


def _copy_source(candidate: Candidate, output_path: Path) -> None:
    """Write curated output via copy2 or cv2 — never move/delete the source file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if candidate.image_path is not None and candidate.image_path.exists():
        if candidate.image_path.resolve() == output_path.resolve():
            return
        shutil.copy2(candidate.image_path, output_path)
    else:
        _save_candidate_image(candidate, output_path)


def _best_effort_candidates(
    curator: "BiometricCurator", media_files: list[Path], limit: int = 25
) -> list[Candidate]:
    """Collect ranked single-face frames when strict biometric filters reject most."""
    ranked: list[tuple[float, Candidate]] = []

    for media_path in media_files:
        suffix = media_path.suffix.lower()
        frames: list[tuple] = []
        if suffix in IMAGE_EXTENSIONS:
            image = _read_image(media_path)
            if image is not None:
                frames.append((image, media_path.name, media_path))
        elif suffix in VIDEO_EXTENSIONS:
            for frame, label in _sample_video_frames(media_path):
                frames.append((frame, label, None))

        for image, label, image_path in frames:
            det_results = curator._face_detection.process(curator._to_rgb(image)).detections or []
            if len(det_results) != 1:
                continue
            sharpness = curator.measure_sharpness(image)
            bbox = det_results[0].location_data.relative_bounding_box
            area_ratio = float(bbox.width * bbox.height)
            brightness = curator._measure_brightness(image)
            processed = curator.prepare_lora_image(image, area_ratio)
            lora_score = curator.compute_lora_score(
                sharpness, 55.0, area_ratio, 0.0, 0.0, brightness
            )
            rank = lora_score * (0.6 + area_ratio)
            candidate = Candidate(
                source_label=label,
                source_path=media_path,
                image_path=image_path,
                image=image,
                sharpness=sharpness,
                symmetry=55.0,
                face_area_ratio=area_ratio,
                score=round(curator.compute_score(sharpness, 55.0), 1),
                brightness=brightness,
                lora_score=lora_score,
                processed_image=processed,
            )
            ranked.append((rank, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    out: list[Candidate] = []
    for _, candidate in ranked:
        key = candidate.source_label
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def _best_effort_candidate(
    curator: "BiometricCurator", media_files: list[Path]
) -> Optional[Candidate]:
    """Pick the best single-face frame when strict biometric filters reject all."""
    candidates = _best_effort_candidates(curator, media_files, limit=1)
    return candidates[0] if candidates else None


def _write_face_jpg(candidate: Candidate, face_path: Path) -> None:
    """Save a tight face crop for Fal face-swap reference."""
    image = candidate.image
    with mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.5
    ) as detector:
        results = detector.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        detections = results.detections or []
    if detections:
        h, w = image.shape[:2]
        bbox = detections[0].location_data.relative_bounding_box
        x1 = max(0, int(bbox.xmin * w))
        y1 = max(0, int(bbox.ymin * h))
        x2 = min(w, int((bbox.xmin + bbox.width) * w))
        y2 = min(h, int((bbox.ymin + bbox.height) * h))
        pad_x = int((x2 - x1) * 0.35)
        pad_y = int((y2 - y1) * 0.45)
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(w, x2 + pad_x)
        y2 = min(h, y2 + pad_y)
        crop = image[y1:y2, x1:x2]
        if crop.size > 0:
            face_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(face_path), crop)
            logger.info("[OUTPUT] face.jpg (crop) -> %s", face_path)
            return
    _copy_source(candidate, face_path)
    logger.info("[OUTPUT] face.jpg -> %s", face_path)


def run_curation(
    input_dir: Path,
    output_dir: Optional[Path] = None,
    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    recursive: bool = True,
) -> CurationResult:
    """Curate one folder; write face.jpg (and optional curated outputs) to output_dir."""
    _require_dependencies()

    if not input_dir.exists():
        logger.error("Directory di input non trovata: %s", input_dir)
        return CurationResult(folder=input_dir, best=None, accepted_count=0, used_fallback=False)

    target_dir = output_dir or input_dir
    media_files = list(_iter_media_files(input_dir, recursive))
    if not media_files:
        logger.warning("Nessun file immagine/video trovato in: %s", input_dir)
        return CurationResult(folder=target_dir, best=None, accepted_count=0, used_fallback=False)

    target_dir.mkdir(parents=True, exist_ok=True)
    accepted: list[Candidate] = []
    used_fallback = False

    with BiometricCurator(sharpness_threshold=sharpness_threshold) as curator:
        for media_path in media_files:
            suffix = media_path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                image = _read_image(media_path)
                if image is None:
                    continue
                candidate, reason = curator.evaluate(
                    image,
                    source_label=media_path.name,
                    source_path=media_path,
                    image_path=media_path,
                )
                if candidate is None:
                    logger.debug("[SCARTATA] %s - Motivo: %s", media_path.name, reason)
                    continue
                logger.info("[ACCETTATA] %s - Score: %.1f", media_path.name, candidate.score)
                accepted.append(candidate)
                continue

            for frame, label in _sample_video_frames(
                media_path, min_sharpness=sharpness_threshold
            ):
                candidate, reason = curator.evaluate(
                    frame,
                    source_label=label,
                    source_path=media_path,
                    image_path=None,
                )
                if candidate is None:
                    logger.debug("[SCARTATA] %s - Motivo: %s", label, reason)
                    continue
                logger.info("[ACCETTATA] %s - Score: %.1f", label, candidate.score)
                accepted.append(candidate)

        if not accepted:
            fallback = _best_effort_candidate(curator, media_files)
            if fallback is None:
                logger.warning("Nessun candidato accettato dopo i filtri biometrici: %s", input_dir)
                return CurationResult(folder=target_dir, best=None, accepted_count=0, used_fallback=False)
            logger.warning(
                "[FALLBACK] Nessun candidato strict; uso best-effort: %s (score %.1f)",
                fallback.source_label,
                fallback.score,
            )
            accepted = [fallback]
            used_fallback = True

    face_ranked = sorted(accepted, key=lambda c: c.score, reverse=True)
    best = face_ranked[0]
    for index, candidate in enumerate(face_ranked[:top_n], start=1):
        output_name = f"curated_face_{index}.jpg"
        output_path = target_dir / output_name
        _copy_source(candidate, output_path)
        logger.info("[OUTPUT] %s -> %s", output_name, output_path)
        if index == 1:
            _write_face_jpg(candidate, target_dir / "face.jpg")

    fullbody_candidates = [
        c
        for c in accepted
        if c.face_area_ratio < FULL_BODY_MAX_FACE_RATIO and c.sharpness >= sharpness_threshold
    ]
    if fullbody_candidates:
        best_fullbody = max(fullbody_candidates, key=lambda c: c.sharpness)
        fullbody_path = target_dir / "curated_fullbody_candidate.jpg"
        _copy_source(best_fullbody, fullbody_path)
        logger.info("[OUTPUT] curated_fullbody_candidate.jpg -> %s", fullbody_path)

    logger.info(
        "Curazione %s: %d accettate, face score=%.1f, fallback=%s",
        target_dir.name,
        len(accepted),
        best.score,
        "si" if used_fallback else "no",
    )
    return CurationResult(
        folder=target_dir,
        best=best,
        accepted_count=len(accepted),
        used_fallback=used_fallback,
    )


@dataclass
class LoRAExportResult:
    output_dir: Path
    exported_count: int
    used_fallback: bool
    top_score: float


@dataclass
class LoRAExportV3Result:
    output_dir: Path
    face_count: int
    body_count: int
    accepted_total: int
    insufficient_frontal: bool
    message: str = ""
    subject_gender: str = "unknown"
    gender_confidence: float = 0.0
    quality_mode: str = ""
    tier_counts: dict[str, int] = field(default_factory=dict)
    loose_scan_count: int = 0


def _save_lora_export(
    candidate: Candidate,
    output_dir: Path,
    index: int,
    trigger_word: str,
    caption_suffix: str,
    min_short_side: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"lora_train_{index:03d}"
    image_path = output_dir / f"{stem}.jpg"
    caption_path = output_dir / f"{stem}.txt"

    image = candidate.processed_image
    if image is None:
        image = candidate.image
    with BiometricCurator() as curator:
        image = curator.upscale_if_needed(image, min_short_side=min_short_side)

    cv2.imwrite(str(image_path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    caption = f"{trigger_word}, {caption_suffix}".strip().rstrip(",")
    caption_path.write_text(caption + "\n", encoding="utf-8")
    logger.info(
        "[LORA_EXPORT] %s score=%.1f lora=%.1f src=%s",
        stem,
        candidate.score,
        candidate.lora_score,
        candidate.source_label,
    )


def run_lora_export(
    input_dir: Path,
    output_dir: Path,
    *,
    trigger_word: str,
    top_n: int = DEFAULT_LORA_TOP_N,
    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
    min_short_side: int = DEFAULT_MIN_SHORT_SIDE,
    caption_suffix: str = DEFAULT_LORA_CAPTION_SUFFIX,
    recursive: bool = True,
    min_lora_score: float = 20.0,
) -> LoRAExportResult:
    """Curate, crop, upscale, and export top-N LoRA training pairs."""
    _require_dependencies()

    if not input_dir.exists():
        logger.error("Directory di input non trovata: %s", input_dir)
        return LoRAExportResult(output_dir, 0, False, 0.0)

    media_files = list(_iter_media_files(input_dir, recursive))
    if not media_files:
        logger.warning("Nessun file immagine/video trovato in: %s", input_dir)
        return LoRAExportResult(output_dir, 0, False, 0.0)

    accepted: list[Candidate] = []
    used_fallback = False

    with BiometricCurator(sharpness_threshold=sharpness_threshold) as curator:
        for media_path in media_files:
            suffix = media_path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                image = _read_image(media_path)
                if image is None:
                    continue
                candidate, reason = curator.evaluate(
                    image,
                    source_label=media_path.name,
                    source_path=media_path,
                    image_path=media_path,
                )
                if candidate is None:
                    logger.debug("[SCARTATA] %s - %s", media_path.name, reason)
                    continue
                logger.info(
                    "[ACCETTATA] %s - lora_score=%.1f",
                    media_path.name,
                    candidate.lora_score,
                )
                accepted.append(candidate)
                continue

            for frame, label in _sample_video_frames(
                media_path, min_sharpness=sharpness_threshold
            ):
                candidate, reason = curator.evaluate(
                    frame,
                    source_label=label,
                    source_path=media_path,
                    image_path=None,
                )
                if candidate is None:
                    logger.debug("[SCARTATA] %s - %s", label, reason)
                    continue
                accepted.append(candidate)

        if not accepted:
            relaxed = _best_effort_candidates(curator, media_files, limit=top_n * 2)
            if not relaxed:
                logger.warning("LoRA export: nessun candidato in %s", input_dir)
                return LoRAExportResult(output_dir, 0, False, 0.0)
            logger.warning(
                "[FALLBACK] LoRA export: %d candidati best-effort (filtri strict: 0)",
                len(relaxed),
            )
            accepted = relaxed
            used_fallback = True
        elif len(accepted) < max(5, top_n // 2):
            before = len(accepted)
            extra = _best_effort_candidates(curator, media_files, limit=top_n * 2)
            existing = {c.source_label for c in accepted}
            for candidate in extra:
                if candidate.source_label in existing:
                    continue
                accepted.append(candidate)
                existing.add(candidate.source_label)
                if len(accepted) >= top_n:
                    break
            if len(accepted) > before:
                used_fallback = True
                logger.warning(
                    "[FALLBACK] LoRA export integrato con best-effort -> %d candidati totali",
                    len(accepted),
                )

    ranked = sorted(accepted, key=lambda c: c.lora_score, reverse=True)
    export_list = [c for c in ranked if c.lora_score >= min_lora_score][:top_n]
    if not export_list and ranked:
        export_list = ranked[: min(top_n, max(3, len(ranked) // 2))]
        logger.warning(
            "LoRA export: nessuna immagine sopra score %.1f; export top %d comunque",
            min_lora_score,
            len(export_list),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for index, candidate in enumerate(export_list, start=1):
        _save_lora_export(
            candidate,
            output_dir,
            index,
            trigger_word,
            caption_suffix,
            min_short_side,
        )

    top_score = export_list[0].lora_score if export_list else 0.0
    logger.info(
        "LoRA export %s: %d/%d accettate -> %d esportate (top lora_score=%.1f fallback=%s)",
        input_dir.name,
        len(accepted),
        len(media_files),
        len(export_list),
        top_score,
        "si" if used_fallback else "no",
    )
    return LoRAExportResult(
        output_dir=output_dir,
        exported_count=len(export_list),
        used_fallback=used_fallback,
        top_score=top_score,
    )


def _histogram_correlation(img_a: "np.ndarray", img_b: "np.ndarray") -> float:
    """Return histogram correlation in [0,1]; higher = more similar."""
    size = (64, 64)
    a = cv2.resize(img_a, size)
    b = cv2.resize(img_b, size)
    corr = 0.0
    for channel in range(3):
        hist_a = cv2.calcHist([a], [channel], None, [32], [0, 256])
        hist_b = cv2.calcHist([b], [channel], None, [32], [0, 256])
        cv2.normalize(hist_a, hist_a)
        cv2.normalize(hist_b, hist_b)
        corr += float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
    return corr / 3.0


def _select_diverse_candidates(
    ranked: list[Candidate],
    *,
    target_min: int = V3_TARGET_MIN,
    target_max: int = V3_TARGET_MAX,
    diversity_threshold: float = V3_DIVERSITY_HIST_THRESHOLD,
) -> list[Candidate]:
    """Pick diverse top candidates up to target_max."""
    selected: list[Candidate] = []
    for candidate in ranked:
        if len(selected) >= target_max:
            break
        too_similar = False
        compare_img = candidate.face_crop or candidate.processed_image or candidate.image
        for existing in selected:
            ref_img = existing.face_crop or existing.processed_image or existing.image
            if _histogram_correlation(compare_img, ref_img) >= diversity_threshold:
                too_similar = True
                break
        if too_similar:
            continue
        selected.append(candidate)
    return selected


def _save_v3_mode_export(
    candidate: Candidate,
    output_dir: Path,
    index: int,
    mode: str,
    trigger_word: str,
    min_short_side: int,
    *,
    subject_gender: str = "unknown",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"lora_train_{index:03d}"
    image_path = output_dir / f"{stem}.jpg"
    caption_path = output_dir / f"{stem}.txt"

    if mode == "face":
        image = candidate.face_crop or candidate.processed_image or candidate.image
        caption = build_v3_face_caption(trigger_word, subject_gender)  # type: ignore[arg-type]
    else:
        image = candidate.body_crop or candidate.processed_image or candidate.image
        caption = V3_BODY_CAPTION.format(trigger=trigger_word)

    with BiometricCurator() as curator:
        image = curator.upscale_if_needed(image, min_short_side=min_short_side)

    cv2.imwrite(str(image_path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    caption_path.write_text(caption + "\n", encoding="utf-8")
    logger.info(
        "[LORA_V3/%s] %s score=%.1f lora=%.1f src=%s",
        mode,
        stem,
        candidate.score,
        candidate.lora_score,
        candidate.source_label,
    )


def dedupe_tier_frames(frames: list[TierFrame]) -> list[TierFrame]:
    """Keep the highest-scoring frame per (source file, tier)."""
    best: dict[tuple[str, str], TierFrame] = {}
    for frame in frames:
        key = (str(frame.source_path.resolve()), frame.tier)
        prev = best.get(key)
        if prev is None or frame.lora_score > prev.lora_score:
            best[key] = frame
    return list(best.values())


def apply_tier_caps(
    frames: list[TierFrame],
    caps: Optional[dict[str, int]] = None,
) -> list[TierFrame]:
    limits = caps or TIER_EXPORT_CAPS
    by_tier: dict[str, list[TierFrame]] = {t: [] for t in TIER_NAMES}
    for frame in sorted(frames, key=lambda f: f.lora_score, reverse=True):
        limit = limits.get(frame.tier, 999999)
        if len(by_tier[frame.tier]) < limit:
            by_tier[frame.tier].append(frame)
    selected: list[TierFrame] = []
    for tier in TIER_NAMES:
        selected.extend(by_tier[tier])
    return selected


def finalize_tier_frames(
    frames: list[TierFrame],
    *,
    apply_caps: bool = True,
) -> list[TierFrame]:
    deduped = dedupe_tier_frames(frames)
    if apply_caps:
        return apply_tier_caps(deduped)
    return deduped


def diagnose_media(
    media_files: list[Path],
    profile: Optional[QualityProfile] = None,
) -> dict[str, Any]:
    """Scan media and report why frames pass or fail tier classification."""
    _require_dependencies()
    prof = profile or resolve_quality_mode(len(media_files))
    face_hist = {"0": 0, "1": 0, "2+": 0}
    tier_pass_raw: dict[str, int] = {t: 0 for t in TIER_NAMES}
    reject_reasons: dict[str, int] = {}
    sharp_samples: list[float] = []
    accepted_frames: list[TierFrame] = []

    with BiometricCurator(sharpness_threshold=prof.sharp_ab) as curator:
        for media_path in media_files:
            suffix = media_path.suffix.lower()
            frame_iter: list[tuple["np.ndarray", str]] = []
            if suffix in IMAGE_EXTENSIONS:
                image = _read_image(media_path)
                if image is not None:
                    frame_iter.append((image, media_path.name))
            elif suffix in VIDEO_EXTENSIONS:
                frame_iter = list(
                    _sample_video_frames(
                        media_path, min_sharpness=prof.sharp_cf * 0.5
                    )
                )

            for image, _label in frame_iter:
                fc = curator.count_faces(image)
                bucket = "1" if fc == 1 else ("0" if fc == 0 else "2+")
                face_hist[bucket] = face_hist.get(bucket, 0) + 1
                sharp = curator.measure_sharpness(image)
                sharp_samples.append(sharp)
                classified = curator.classify_tier(image, prof)
                if classified is not None:
                    classified.source_path = media_path
                    accepted_frames.append(classified)
                    tier_pass_raw[classified.tier] = tier_pass_raw.get(classified.tier, 0) + 1
                else:
                    bbox = curator._face_detection_bbox(image)
                    roi = curator.measure_roi_sharpness(image, bbox)
                    center = curator.measure_center_sharpness(image)
                    eff = max(sharp, roi, center)
                    if fc == 0 and eff < prof.sharp_cf:
                        reason = "no_face_low_sharpness"
                    elif fc == 1 and eff < prof.sharp_ab:
                        reason = "face_low_sharpness"
                    else:
                        reason = "pose_or_tier_rules"
                    reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

    sharp_samples.sort()
    n = len(sharp_samples)
    pct = lambda p: sharp_samples[int(n * p)] if n else 0.0
    finalized = finalize_tier_frames(accepted_frames)
    tier_pass_final = {t: 0 for t in TIER_NAMES}
    for frame in finalized:
        tier_pass_final[frame.tier] = tier_pass_final.get(frame.tier, 0) + 1

    return {
        "quality_mode": prof.mode,
        "sharp_ab": prof.sharp_ab,
        "sharp_cf": prof.sharp_cf,
        "media_count": len(media_files),
        "frames_scanned": n,
        "face_histogram": face_hist,
        "tier_pass_raw": tier_pass_raw,
        "tier_pass_final": tier_pass_final,
        "reject_reasons": reject_reasons,
        "sharpness_p25": pct(0.25),
        "sharpness_p50": pct(0.50),
        "sharpness_p75": pct(0.75),
    }


def collect_tier_frames(
    media_files: list[Path],
    profile: QualityProfile,
    *,
    apply_caps: bool = True,
) -> tuple[list[TierFrame], dict[str, int]]:
    """Scan media and classify frames into tiers A-F."""
    tier_frames: list[TierFrame] = []
    raw_counts: dict[str, int] = {t: 0 for t in TIER_NAMES}

    with BiometricCurator(sharpness_threshold=profile.sharp_ab) as curator:
        for media_path in media_files:
            suffix = media_path.suffix.lower()
            frames: list[tuple["np.ndarray", str, Optional[Path]]] = []
            if suffix in IMAGE_EXTENSIONS:
                image = _read_image(media_path)
                if image is not None:
                    frames.append((image, media_path.name, media_path))
            elif suffix in VIDEO_EXTENSIONS:
                for frame, label in _sample_video_frames(
                    media_path, min_sharpness=profile.sharp_cf * 0.5
                ):
                    frames.append((frame, label, None))

            for image, label, image_path in frames:
                classified = curator.classify_tier(image, profile)
                if classified is None:
                    continue
                classified.source_label = label
                classified.source_path = media_path
                classified.image_path = image_path
                tier_frames.append(classified)
                raw_counts[classified.tier] = raw_counts.get(classified.tier, 0) + 1

    finalized = finalize_tier_frames(tier_frames, apply_caps=apply_caps)
    final_counts = {t: 0 for t in TIER_NAMES}
    for frame in finalized:
        final_counts[frame.tier] = final_counts.get(frame.tier, 0) + 1
    logger.info(
        "[V3] Tier frames raw=%d final=%d (dedup/cap raw=%s final=%s)",
        len(tier_frames),
        len(finalized),
        raw_counts,
        final_counts,
    )
    return finalized, final_counts


def _save_tier_export(
    frame: TierFrame,
    output_dir: Path,
    index: int,
    trigger_word: str,
    subject_gender: str,
    min_short_side: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"lora_train_{index:03d}"
    image_path = output_dir / f"{stem}.jpg"
    caption_path = output_dir / f"{stem}.txt"

    image = frame.processed_image if frame.processed_image is not None else frame.image
    image = np.ascontiguousarray(image)
    with BiometricCurator() as curator:
        image = curator.upscale_if_needed(image, min_short_side=min_short_side)

    cv2.imwrite(str(image_path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    caption = build_v3_tier_caption(
        frame.tier,
        trigger_word,
        subject_gender,  # type: ignore[arg-type]
        detail_hint=frame.detail_hint,
    )
    caption_path.write_text(caption + "\n", encoding="utf-8")
    logger.info(
        "[LORA_V3/tier-%s] %s sharp=%.1f roi=%.1f src=%s",
        frame.tier,
        stem,
        frame.sharpness,
        frame.roi_sharpness,
        frame.source_label,
    )


def run_lora_export_v3(
    input_dir: Path,
    output_dir: Path,
    *,
    trigger_word: str,
    min_short_side: int = DEFAULT_MIN_SHORT_SIDE,
    recursive: bool = True,
    subject_gender: str = "unknown",
    gender_result: Optional[GenderResult] = None,
    quality_profile: Optional[QualityProfile] = None,
    media_files: Optional[list[Path]] = None,
) -> LoRAExportV3Result:
    """Adaptive v3 export: tier subfolders A-F under datasets/soggetto{N}_v3/."""
    _require_dependencies()

    if not input_dir.exists():
        logger.error("Directory di input non trovata: %s", input_dir)
        return LoRAExportV3Result(
            output_dir, 0, 0, 0, True, "input mancante", subject_gender, 0.0
        )

    scanned = media_files if media_files is not None else list(_iter_media_files(input_dir, recursive))
    loose_count = len(scanned)
    if not scanned:
        logger.warning("Nessun file immagine/video trovato in: %s", input_dir)
        return LoRAExportV3Result(
            output_dir, 0, 0, 0, True, "nessun media", subject_gender, 0.0
        )

    profile = quality_profile or resolve_quality_mode(loose_count)
    logger.info(
        "[V3] Modalità adattiva: %s (loose_scan=%d, sharp_AB=%.0f sharp_CF=%.0f)",
        profile.mode,
        loose_count,
        profile.sharp_ab,
        profile.sharp_cf,
    )
    if profile.warn_low:
        logger.warning(
            "[V3] Materiale molto scarso (%d file) — best-effort con warning",
            loose_count,
        )

    tier_frames, tier_counts = collect_tier_frames(scanned, profile)

    tier_dirs: dict[str, Path] = {}
    for tier, folder_name in TIER_FOLDERS.items():
        tier_dir = output_dir / folder_name
        tier_dir.mkdir(parents=True, exist_ok=True)
        for old in tier_dir.glob("*.jpg"):
            old.unlink(missing_ok=True)
        for old in tier_dir.glob("*.txt"):
            old.unlink(missing_ok=True)
        tier_dirs[tier] = tier_dir

    per_tier_index: dict[str, int] = {t: 0 for t in TIER_NAMES}
    for frame in sorted(tier_frames, key=lambda f: f.lora_score, reverse=True):
        per_tier_index[frame.tier] = per_tier_index.get(frame.tier, 0) + 1
        _save_tier_export(
            frame,
            tier_dirs[frame.tier],
            per_tier_index[frame.tier],
            trigger_word,
            subject_gender,
            min_short_side,
        )

    if gender_result is not None:
        save_gender_json(output_dir / "gender.json", gender_result)
        subject_num = _subject_num_from_folder(input_dir.name)
        if subject_num:
            save_gender_json(outputs_gender_path(subject_num), gender_result)

    exported_counts = {
        tier: len(list(tier_dirs[tier].glob("*.jpg"))) for tier in TIER_NAMES
    }
    face_count = exported_counts.get("A", 0) + exported_counts.get("B", 0)
    body_count = sum(exported_counts.get(t, 0) for t in ("C", "D", "E", "F"))
    total_exported = sum(exported_counts.values())
    insufficient = face_count < min(5, V3_TARGET_MIN) and body_count < 20

    message = ""
    if insufficient:
        message = (
            f"INSUFFICIENTE: face={face_count} body={body_count} total={total_exported} "
            f"(target face>={V3_TARGET_MIN}) per {input_dir.name}"
        )
        logger.warning("[V3] %s tiers=%s", message, exported_counts)
    else:
        logger.info(
            "LoRA v3 tier export %s: mode=%s face=%d body=%d total=%d tiers=%s",
            input_dir.name,
            profile.mode,
            face_count,
            body_count,
            total_exported,
            exported_counts,
        )

    meta_path = output_dir / "tier_export_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "quality_mode": profile.mode,
                "loose_scan_count": loose_count,
                "tier_counts": exported_counts,
                "accepted_frames": len(tier_frames),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return LoRAExportV3Result(
        output_dir=output_dir,
        face_count=face_count,
        body_count=body_count,
        accepted_total=len(tier_frames),
        insufficient_frontal=insufficient,
        message=message,
        subject_gender=subject_gender,
        gender_confidence=gender_result.confidence if gender_result else 0.0,
        quality_mode=profile.mode,
        tier_counts=exported_counts,
        loose_scan_count=loose_count,
    )


def _subject_num_from_folder(folder_name: str) -> int:
    digits = "".join(ch for ch in folder_name if ch.isdigit())
    if digits:
        return int(digits)
    return 0


def _subject_input_folder(subject_num: int, inputs_root: Path) -> Path:
    from subject_discovery import resolve_subject_input_folder

    return resolve_subject_input_folder(subject_num, inputs_root)


def _subject_dataset_folder(subject_num: int, version: str) -> Path:
    return DATASETS_ROOT / f"soggetto{subject_num}_{version}"


def run_batch_lora_export_v3(
    inputs_root: Path = DEFAULT_INPUTS_ROOT,
    *,
    dataset_version: str = "v3",
    min_short_side: int = DEFAULT_MIN_SHORT_SIDE,
    recursive: bool = True,
    detect_gender: bool = False,
    gender_override: Optional[str] = None,
    interactive: bool = True,
    dry_run: bool = False,
) -> dict[int, LoRAExportV3Result]:
    """Export v3 datasets for Soggetto 1-5."""
    results: dict[int, LoRAExportV3Result] = {}
    for num in ALL_SUBJECTS:
        trigger = SUBJECT_TRIGGER_MAP[num]
        input_dir = _subject_input_folder(num, inputs_root)
        output_dir = _subject_dataset_folder(num, dataset_version)
        logger.info("=== LoRA v3 export Soggetto %d (%s) ===", num, trigger)
        if not input_dir.is_dir():
            logger.error("[S%d] Cartella input assente: %s", num, input_dir)
            results[num] = LoRAExportV3Result(
                output_dir, 0, 0, 0, True, f"input assente: {input_dir}"
            )
            continue

        gender_result: Optional[GenderResult] = None
        subject_gender = "unknown"
        if detect_gender:
            raw = detect_gender_from_folder(
                input_dir,
                recursive=recursive,
                subject_label=f"Soggetto {num}",
            )
            try:
                gender_result = resolve_gender(
                    raw,
                    subject_label=f"Soggetto {num}",
                    gender_override=gender_override,
                    interactive=interactive,
                    dry_run=dry_run,
                )
            except ValueError as exc:
                logger.error("[S%d] Genere: %s — export saltato", num, exc)
                results[num] = LoRAExportV3Result(
                    output_dir,
                    0,
                    0,
                    0,
                    True,
                    str(exc),
                    raw.gender,
                    raw.confidence,
                )
                continue
            subject_gender = gender_result.gender
            if dry_run:
                logger.info(
                    "[DRY-RUN][S%d] Genere: %s (conf=%.2f)",
                    num,
                    subject_gender,
                    gender_result.confidence,
                )
            elif not gender_result.is_certain() and subject_gender == "unknown":
                logger.warning(
                    "[S%d] Genere incerto — caption neutre; usa --gender per forzare",
                    num,
                )

        if detect_gender and dry_run:
            results[num] = LoRAExportV3Result(
                output_dir,
                0,
                0,
                0,
                False,
                "dry-run: solo rilevamento genere",
                subject_gender,
                gender_result.confidence if gender_result else 0.0,
            )
            continue

        results[num] = run_lora_export_v3(
            input_dir,
            output_dir,
            trigger_word=trigger,
            min_short_side=min_short_side,
            recursive=recursive,
            subject_gender=subject_gender,
            gender_result=gender_result if detect_gender and not dry_run else None,
        )
    return results


def _discover_input_subfolders(inputs_root: Path) -> list[Path]:
    if not inputs_root.is_dir():
        return []
    mirror_targets = set(TEST_FOLDER_MAPPINGS.values())
    return sorted(
        path
        for path in inputs_root.iterdir()
        if path.is_dir() and path.name not in mirror_targets
    )


def _mirror_test_faces(inputs_root: Path, results: dict[str, CurationResult]) -> None:
    """COPY best face.jpg from mapped source folders into Test_Soggetto* targets.

    SAFETY: uses shutil.copy2 only — never move or delete source files.
    """
    for source_name, target_name in TEST_FOLDER_MAPPINGS.items():
        source_dir = inputs_root / source_name
        target_dir = inputs_root / target_name
        source_face = source_dir / "face.jpg"
        if not source_face.is_file():
            result = results.get(str(source_dir))
            if result and result.best is not None:
                target_dir.mkdir(parents=True, exist_ok=True)
                _write_face_jpg(result.best, source_face)
        if source_face.is_file():
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_face, target_dir / "face.jpg")
            logger.info("[MIRROR] %s -> %s/face.jpg", source_name, target_name)


def run_batch_curation(
    inputs_root: Path,
    sharpness_threshold: float,
    top_n: int,
    recursive: bool,
) -> int:
    subfolders = _discover_input_subfolders(inputs_root)
    if not subfolders:
        logger.error("Nessuna sottocartella trovata in: %s", inputs_root)
        return 1

    logger.info("Batch curation: %d cartelle in %s", len(subfolders), inputs_root)
    results: dict[str, CurationResult] = {}
    success_count = 0

    for folder in subfolders:
        logger.info("--- Curazione: %s ---", folder.name)
        result = run_curation(
            input_dir=folder,
            output_dir=folder,
            sharpness_threshold=sharpness_threshold,
            top_n=top_n,
            recursive=recursive,
        )
        results[str(folder)] = result
        if result.best is not None:
            success_count += 1

    _mirror_test_faces(inputs_root, results)

    logger.info(
        "Batch completato: %d/%d cartelle con face.jpg",
        success_count,
        len(subfolders),
    )
    return 0 if success_count > 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Automated Data Curator (Biometric Filter): filtra e seleziona "
            "le migliori immagini di riferimento facciale/corporea."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Cartella singola da curare (es. inputs/Soggetto 4/). "
        "Se omesso, scansiona tutte le sottocartelle di --inputs-root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Cartella destinazione per modalità singola (default: stessa di --input).",
    )
    parser.add_argument(
        "--inputs-root",
        type=Path,
        default=DEFAULT_INPUTS_ROOT,
        help=f"Root batch scan (default: {DEFAULT_INPUTS_ROOT})",
    )
    parser.add_argument(
        "--sharpness-threshold",
        type=float,
        default=DEFAULT_SHARPNESS_THRESHOLD,
        help=f"Soglia varianza Laplacian (default: {DEFAULT_SHARPNESS_THRESHOLD})",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Numero massimo di face curated (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scansiona ricorsivamente ogni cartella input (default: True)",
    )
    parser.add_argument(
        "--lora-export",
        nargs="?",
        const="AUTO",
        default=None,
        help="Esporta dataset LoRA. Con --all-subjects e --dataset-version v3 esporta "
        "face/ e body/. Altrimenti specificare path destinazione (legacy).",
    )
    parser.add_argument(
        "--all-subjects",
        action="store_true",
        help="Processa Soggetto 1-5 da inputs/Soggetto N/ (con --lora-export --dataset-version v3).",
    )
    parser.add_argument(
        "--dataset-version",
        default=None,
        help="Versione dataset (es. v3 -> datasets/soggetto{N}_v3/).",
    )
    parser.add_argument(
        "--trigger-word",
        default="",
        help="Trigger word per caption LoRA export legacy (richiesto con --lora-export PATH).",
    )
    parser.add_argument(
        "--caption-suffix",
        default=DEFAULT_LORA_CAPTION_SUFFIX,
        help="Suffisso caption dopo trigger word per LoRA export.",
    )
    parser.add_argument(
        "--min-resolution",
        type=int,
        default=DEFAULT_MIN_SHORT_SIDE,
        help=f"Lato corto minimo dopo upscale (default: {DEFAULT_MIN_SHORT_SIDE}).",
    )
    parser.add_argument(
        "--min-lora-score",
        type=float,
        default=35.0,
        help="Score minimo LoRA per export (default: 35).",
    )
    parser.add_argument(
        "--detect-gender",
        action="store_true",
        help="Rileva genere all'inizio di ogni soggetto (batch v3); chiede conferma se incerto.",
    )
    parser.add_argument(
        "--gender",
        choices=("male", "female"),
        default=None,
        help="Forza genere per tutti i soggetti (salta prompt).",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non chiedere conferma genere quando incerto.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Con --detect-gender: mostra rilevamento senza scrivere dataset.",
    )
    parser.add_argument(
        "--import-from",
        type=Path,
        default=None,
        help="Importa da cartella esterna (delega a import_subject_media.py)",
    )
    parser.add_argument(
        "--subject",
        "--subject-id",
        type=int,
        default=2,
        dest="subject_id",
        help="ID soggetto per --import-from (default: 2)",
    )
    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="Con --import-from: copia solo, non sposta dalla sorgente (default implicito)",
    )
    parser.add_argument(
        "--move-rejects",
        action="store_true",
        help="Con --import-from: opt-in distruttivo per spostare scarti dalla sorgente",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.import_from is not None:
        from import_subject_media import run_subject_import

        try:
            stats = run_subject_import(
                args.import_from,
                subject_id=args.subject_id,
                inputs_root=args.inputs_root,
                copy_to_inputs=True,
                lora_export=args.lora_export is not None,
                detect_gender=args.detect_gender,
                dry_run=args.dry_run,
                min_sharpness=max(args.sharpness_threshold, V3_SHARPNESS_THRESHOLD),
                top_n_face=max(1, args.top_n),
                top_n_body=max(1, args.top_n),
                move_rejects=args.move_rejects and not args.copy_only,
                gender_override=args.gender,
                interactive=not args.non_interactive,
            )
        except Exception as exc:
            logger.error("Import-from failed: %s", exc)
            return 1
        return 0 if stats.copied_to_inputs > 0 or args.dry_run else 1

    if args.lora_export is not None:
        if args.all_subjects and args.dataset_version:
            results = run_batch_lora_export_v3(
                inputs_root=args.inputs_root,
                dataset_version=args.dataset_version,
                min_short_side=max(512, args.min_resolution),
                recursive=args.recursive,
                detect_gender=args.detect_gender,
                gender_override=args.gender,
                interactive=not args.non_interactive,
                dry_run=args.dry_run,
            )
            ok_count = sum(
                1 for r in results.values() if r.face_count >= V3_TARGET_MIN
            )
            print("\n=== LoRA v3 export summary ===")
            for num in sorted(results):
                r = results[num]
                status = "OK" if r.face_count >= V3_TARGET_MIN else "INSUFFICIENTE"
                print(
                    f"Soggetto {num}: face={r.face_count} body={r.body_count} "
                    f"accettate={r.accepted_total} gender={r.subject_gender} "
                    f"conf={r.gender_confidence:.2f} [{status}]"
                )
                if r.message:
                    print(f"  -> {r.message}")
            return 0 if ok_count > 0 else 1

        if not args.input:
            parser.error("--lora-export richiede --input (legacy) oppure --all-subjects")
        if not args.trigger_word:
            parser.error("--lora-export legacy richiede --trigger-word")
        export_path = Path(args.lora_export) if args.lora_export != "AUTO" else None
        if export_path is None:
            parser.error("Specificare path destinazione per --lora-export legacy")
        result = run_lora_export(
            input_dir=args.input,
            output_dir=export_path,
            trigger_word=args.trigger_word,
            top_n=max(1, args.top_n),
            sharpness_threshold=args.sharpness_threshold,
            min_short_side=max(512, args.min_resolution),
            caption_suffix=args.caption_suffix,
            recursive=args.recursive,
            min_lora_score=args.min_lora_score,
        )
        return 0 if result.exported_count > 0 else 1

    if args.input is not None:
        result = run_curation(
            input_dir=args.input,
            output_dir=args.output,
            sharpness_threshold=args.sharpness_threshold,
            top_n=max(1, args.top_n),
            recursive=args.recursive,
        )
        if args.input.name in TEST_FOLDER_MAPPINGS:
            inputs_root = args.input.parent
            _mirror_test_faces(inputs_root, {str(args.input): result})
        return 0 if result.best is not None else 1

    return run_batch_curation(
        inputs_root=args.inputs_root,
        sharpness_threshold=args.sharpness_threshold,
        top_n=max(1, args.top_n),
        recursive=args.recursive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
