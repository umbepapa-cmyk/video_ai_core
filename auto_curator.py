"""
Fase 3.17: Automated Data Curator (Biometric Filter).

Standalone utility to filter and rank face/body reference images from raw
input folders using OpenCV sharpness checks and MediaPipe face detection/mesh.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

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

# MediaPipe Face Mesh landmark indices (critical visibility points).
_LEFT_EYE = 33
_RIGHT_EYE = 263
_NOSE_TIP = 1
_MOUTH_LEFT = 61
_MOUTH_RIGHT = 291
_CRITICAL_LANDMARKS = (_LEFT_EYE, _RIGHT_EYE, _NOSE_TIP, _MOUTH_LEFT, _MOUTH_RIGHT)

MIN_FACE_AREA_RATIO = 0.10
FULL_BODY_MAX_FACE_RATIO = 0.15
DEFAULT_SHARPNESS_THRESHOLD = 30.0
DEFAULT_TOP_N = 5


@dataclass
class FaceMetrics:
    face_area_ratio: float
    symmetry_score: float


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


class BiometricCurator:
    """Filter and score images/frames using biometric quality heuristics."""

    def __init__(
        self,
        sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
        min_face_area_ratio: float = MIN_FACE_AREA_RATIO,
    ) -> None:
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

    def measure_sharpness(self, image: "np.ndarray") -> float:
        gray = self._gray(image)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def check_face_size_and_occlusion(self, image: "np.ndarray") -> tuple[bool, Optional[FaceMetrics]]:
        """
        Validate face size and critical landmark visibility.

        Rejects tiny faces, occluded landmarks (phone/mirror), and extreme profiles.
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

        for index in _CRITICAL_LANDMARKS:
            lm = landmarks[index]
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

        return True, FaceMetrics(face_area_ratio=face_area_ratio, symmetry_score=symmetry)

    def evaluate(
        self,
        image: "np.ndarray",
        source_label: str,
        source_path: Path,
        image_path: Optional[Path] = None,
    ) -> tuple[Optional[Candidate], str]:
        """Run all filters; return candidate on success or rejection reason."""
        if not self.check_single_face(image):
            detections = self._face_detection.process(self._to_rgb(image)).detections or []
            if len(detections) == 0:
                return None, "Nessuna faccia rilevata"
            return None, "Troppe facce rilevate"

        sharpness = self.measure_sharpness(image)
        if not self.check_sharpness(image):
            return None, "Immagine sfocata"

        ok, metrics = self.check_face_size_and_occlusion(image)
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
            return None, "Landmark critici mancanti o occlusi"

        score = self.compute_score(sharpness, metrics.symmetry_score)
        return (
            Candidate(
                source_label=source_label,
                source_path=source_path,
                image_path=image_path,
                image=image,
                sharpness=sharpness,
                symmetry=metrics.symmetry_score,
                face_area_ratio=metrics.face_area_ratio,
                score=score,
            ),
            "",
        )

    @staticmethod
    def compute_score(sharpness: float, symmetry: float) -> float:
        sharpness_norm = min(100.0, (sharpness / 100.0) * 100.0)
        return round(sharpness_norm * 0.5 + symmetry * 0.5, 1)


def _require_dependencies() -> None:
    missing: list[str] = []
    if cv2 is None:
        missing.append("opencv-python")
    if mp is None:
        missing.append("mediapipe")
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


def _iter_media_files(input_dir: Path, recursive: bool) -> Iterator[Path]:
    if recursive:
        paths = sorted(input_dir.rglob("*"))
    else:
        paths = sorted(input_dir.iterdir())

    for path in paths:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
            yield path


def _read_image(path: Path) -> Optional["np.ndarray"]:
    image = cv2.imread(str(path))
    if image is None:
        logger.warning("Impossibile leggere immagine: %s", path)
    return image


def _sample_video_frames(path: Path) -> Iterator[tuple["np.ndarray", str]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        logger.warning("Impossibile aprire video: %s", path)
        return

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 24.0)
    if fps <= 0:
        fps = 24.0
    step = max(1, int(round(fps)))
    frame_index = 0
    sampled = 0

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % step == 0:
                second = frame_index / fps
                label = f"{path.name} @ {second:.0f}s"
                yield frame, label
                sampled += 1
            frame_index += 1
    finally:
        capture.release()

    if sampled == 0:
        logger.warning("Nessun frame campionato da video: %s", path)


def _save_candidate_image(candidate: Candidate, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), candidate.image)


def _copy_source(candidate: Candidate, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if candidate.image_path is not None and candidate.image_path.exists():
        shutil.copy2(candidate.image_path, output_path)
    else:
        _save_candidate_image(candidate, output_path)


def run_curation(
    input_dir: Path,
    output_dir: Path,
    sharpness_threshold: float,
    top_n: int,
    recursive: bool,
) -> int:
    _require_dependencies()

    if not input_dir.exists():
        logger.error("Directory di input non trovata: %s", input_dir)
        return 1

    media_files = list(_iter_media_files(input_dir, recursive))
    if not media_files:
        logger.warning("Nessun file immagine/video trovato in: %s", input_dir)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    accepted: list[Candidate] = []

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
                    logger.info("[SCARTATA] %s - Motivo: %s", media_path.name, reason)
                    continue
                logger.info("[ACCETTATA] %s - Score: %.1f", media_path.name, candidate.score)
                accepted.append(candidate)
                continue

            for frame, label in _sample_video_frames(media_path):
                candidate, reason = curator.evaluate(
                    frame,
                    source_label=label,
                    source_path=media_path,
                    image_path=None,
                )
                if candidate is None:
                    logger.info("[SCARTATA] %s - Motivo: %s", label, reason)
                    continue
                logger.info("[ACCETTATA] %s - Score: %.1f", label, candidate.score)
                accepted.append(candidate)

    if not accepted:
        logger.warning("Nessun candidato accettato dopo i filtri biometrici.")
        return 1

    face_ranked = sorted(accepted, key=lambda c: c.score, reverse=True)
    for index, candidate in enumerate(face_ranked[:top_n], start=1):
        output_name = f"curated_face_{index}.jpg"
        output_path = output_dir / output_name
        _copy_source(candidate, output_path)
        logger.info("[OUTPUT] %s copiata", output_name)

    fullbody_candidates = [
        c
        for c in accepted
        if c.face_area_ratio < FULL_BODY_MAX_FACE_RATIO and c.sharpness >= sharpness_threshold
    ]
    if fullbody_candidates:
        best_fullbody = max(fullbody_candidates, key=lambda c: c.sharpness)
        fullbody_path = output_dir / "curated_fullbody_candidate.jpg"
        _copy_source(best_fullbody, fullbody_path)
        logger.info("[OUTPUT] curated_fullbody_candidate.jpg copiata")

    logger.info(
        "Curazione completata: %d accettate, %d face output, fullbody=%s",
        len(accepted),
        min(top_n, len(face_ranked)),
        "si" if fullbody_candidates else "no",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Automated Data Curator (Biometric Filter): filtra e seleziona "
            "le migliori immagini di riferimento facciale/corporea."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Cartella sorgente (es. inputs/Soggetto_Grezzo/)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Cartella destinazione (es. inputs/Test_Commerciale/)",
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
        help="Scansiona ricorsivamente la cartella input (default: True)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_curation(
        input_dir=args.input,
        output_dir=args.output,
        sharpness_threshold=args.sharpness_threshold,
        top_n=max(1, args.top_n),
        recursive=args.recursive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
