#!/usr/bin/env python3
"""
Advanced Biometric Curator — dataset curation with MediaPipe face filters.

Filters: single face, minimum bbox size (10%), blur/exposure, temporal sampling for video.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator, Optional, Tuple

try:
    import cv2
    import mediapipe as mp
except ImportError:
    print("Dipendenze mancanti. Installa con: pip install mediapipe opencv-python")
    sys.exit(1)

# --- Filter thresholds (aligned with auto_curator philosophy, standalone) ---
MIN_DETECTION_CONFIDENCE = 0.7
MIN_FACE_FRACTION = 0.10
LAPLACIAN_THRESHOLD = 80.0
BRIGHTNESS_MIN = 50.0
BRIGHTNESS_MAX = 200.0
DEFAULT_FPS = 30.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}


class AdvancedBiometricCurator:
    """Curate images and video frames using biometric quality filters."""

    def __init__(
        self,
        output_dir: Path,
        trigger_word: str,
        caption_suffix: str = "high quality, cinematic, 4k",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.trigger_word = trigger_word
        self.caption_suffix = caption_suffix
        self._counter = 0
        self._mp_face = mp.solutions.face_detection
        self._detector = self._mp_face.FaceDetection(
            min_detection_confidence=MIN_DETECTION_CONFIDENCE
        )

    def close(self) -> None:
        self._detector.close()

    def __enter__(self) -> "AdvancedBiometricCurator":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Quality metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _laplacian_variance(image) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def _brightness_mean(image) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        return float(gray.mean())

    @staticmethod
    def _face_crop(frame, detection) -> Tuple[object, int, int, int, int]:
        h, w = frame.shape[:2]
        bbox = detection.location_data.relative_bounding_box
        x = max(0, int(bbox.xmin * w))
        y = max(0, int(bbox.ymin * h))
        bw = max(1, int(bbox.width * w))
        bh = max(1, int(bbox.height * h))
        x2 = min(w, x + bw)
        y2 = min(h, y + bh)
        crop = frame[y:y2, x:x2]
        return crop, x, y, x2 - x, y2 - y

    def _evaluate_frame(
        self, frame, source_label: str
    ) -> Tuple[Optional[object], Optional[str], float]:
        """
        Run filters on a BGR frame.

        Returns:
            (frame, None, score) on success
            (None, reject_reason, 0.0) on rejection
        """
        if frame is None or frame.size == 0:
            return None, "Frame non valido", 0.0

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._detector.process(rgb)
        detections = results.detections or []

        n_faces = len(detections)
        if n_faces == 0:
            return None, "0 volti", 0.0
        if n_faces > 1:
            return None, f"Troppe facce ({n_faces})", 0.0

        detection = detections[0]
        frame_h, frame_w = frame.shape[:2]
        bbox = detection.location_data.relative_bounding_box
        face_w = bbox.width * frame_w
        face_h = bbox.height * frame_h

        min_w = MIN_FACE_FRACTION * frame_w
        min_h = MIN_FACE_FRACTION * frame_h
        if face_w < min_w or face_h < min_h:
            return None, "Faccia troppo piccola", 0.0

        crop, _, _, _, _ = self._face_crop(frame, detection)
        if crop.size == 0:
            return None, "Faccia troppo piccola", 0.0

        sharpness = self._laplacian_variance(crop)
        if sharpness <= LAPLACIAN_THRESHOLD:
            return None, "Sfocatura", sharpness

        brightness = self._brightness_mean(crop)
        if brightness <= BRIGHTNESS_MIN or brightness >= BRIGHTNESS_MAX:
            return None, "Esposizione", sharpness

        return frame, None, sharpness

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_curated(self, frame, score: float) -> str:
        self._counter += 1
        stem = f"{self.trigger_word}_{self._counter:04d}"
        image_path = self.output_dir / f"{stem}.jpg"
        caption_path = self.output_dir / f"{stem}.txt"

        cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        caption_path.write_text(
            f"{self.trigger_word}, {self.caption_suffix}",
            encoding="utf-8",
        )
        print(f"[OK] salvato curated {stem}.jpg (score={score:.1f})")
        return stem

    def _reject(self, source_label: str, reason: str) -> None:
        print(f"[SCARTO] {source_label}: {reason}")

    # ------------------------------------------------------------------
    # Input handlers
    # ------------------------------------------------------------------

    def process_image(self, image_path: Path) -> None:
        frame = cv2.imread(str(image_path))
        if frame is None:
            self._reject(image_path.name, "Impossibile leggere immagine")
            return

        accepted, reason, score = self._evaluate_frame(frame, image_path.name)
        if accepted is None:
            self._reject(image_path.name, reason or "Scartato")
            return

        self._save_curated(accepted, score)

    def process_video(self, video_path: Path) -> None:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            self._reject(video_path.name, "Impossibile aprire video")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps is None or fps <= 0:
            fps = DEFAULT_FPS
        skip = max(1, int(round(fps)))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % skip == 0:
                label = f"Frame {frame_idx}"
                accepted, reason, score = self._evaluate_frame(frame, label)
                if accepted is None:
                    self._reject(label, reason or "Scartato")
                else:
                    self._save_curated(accepted, score)

            frame_idx += 1

        cap.release()

    def process_path(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            self.process_image(path)
        elif suffix in VIDEO_EXTENSIONS:
            self.process_video(path)
        else:
            print(f"[SKIP] {path.name}: formato non supportato")

    @staticmethod
    def iter_input_files(input_path: Path, recursive: bool) -> Iterator[Path]:
        if input_path.is_file():
            yield input_path
            return

        if not input_path.is_dir():
            raise FileNotFoundError(f"Input non trovato: {input_path}")

        pattern = "**/*" if recursive else "*"
        for candidate in sorted(input_path.glob(pattern)):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                yield candidate

    def run(self, input_path: Path, recursive: bool = False) -> int:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files = list(self.iter_input_files(input_path, recursive))
        if not files:
            print(f"[WARN] Nessun file immagine/video trovato in {input_path}")
            return 0

        print(f"[INFO] Output: {self.output_dir}")
        print(f"[INFO] Elaborazione di {len(files)} file...")

        for file_path in files:
            self.process_path(file_path)

        print(f"[INFO] Completato: {self._counter} frame salvati in {self.output_dir}")
        return self._counter


# Backward compatibility
DatasetAutomator = AdvancedBiometricCurator


def _default_output_dir(subject_name: str) -> Path:
    return Path("inputs") / f"Curated_Dataset_{subject_name}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advanced Biometric Curator — filtra dataset per training LoRA/dreambooth.",
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="File singolo o directory di immagini/video.",
    )
    parser.add_argument(
        "--subject-name",
        required=True,
        help="Nome soggetto (es. Soggetto4) — usato per la cartella di output.",
    )
    parser.add_argument(
        "--trigger-word",
        required=True,
        help="Trigger word per caption e nomi file (es. soggetto_quattro).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory output (default: inputs/Curated_Dataset_{subject_name}).",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Scansiona ricorsivamente le sottocartelle.",
    )
    parser.add_argument(
        "--caption-suffix",
        default="high quality, cinematic, 4k",
        help="Testo caption dopo la trigger word.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.subject_name)

    with AdvancedBiometricCurator(
        output_dir=output_dir,
        trigger_word=args.trigger_word,
        caption_suffix=args.caption_suffix,
    ) as curator:
        saved = curator.run(input_path, recursive=args.recursive)

    return 0 if saved >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
