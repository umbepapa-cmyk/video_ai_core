"""
Dataset Automator Pro — prepare LoRA training datasets from raw media.

Recursively scans ``inputs/`` for videos (and optionally images), extracts
quality-filtered frames, and writes JPEG + caption sidecars to ``dataset_lora/``.

Integration hooks:
  - Output folder ``dataset_lora/`` can be fed directly to LoRA training pipelines.
  - For curated face references, use ``auto_curator.py`` (``BiometricCurator``)
    which writes ranked faces to ``inputs/Test_Commerciale/``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

VIDEO_EXTENSIONS = {".avi", ".mp4", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_INPUT = "inputs"
DEFAULT_OUTPUT = "dataset_ready"
DEFAULT_FRAME_INTERVAL = 60
DEFAULT_TRIGGER_WORD = "soggetto"
CAPTION_SUFFIX = "high quality, cinematic, 4k"

LAPLACIAN_THRESHOLD = 80.0
BRIGHTNESS_MIN = 50
BRIGHTNESS_MAX = 200


def _require_opencv() -> None:
    if cv2 is None or np is None:
        logger.error(
            "Dipendenze mancanti: opencv-python, numpy. "
            "Installa con: pip install opencv-python numpy"
        )
        sys.exit(1)


def _iter_videos(input_dir: Path) -> Iterator[Path]:
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            yield path


def _iter_images(input_dir: Path) -> Iterator[Path]:
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


class DatasetAutomator:
    """Scan inputs recursively, extract frames from videos, prepare LoRA dataset."""

    def __init__(
        self,
        input_folder: str | Path = DEFAULT_INPUT,
        output_folder: str | Path = DEFAULT_OUTPUT,
        frame_interval: int = DEFAULT_FRAME_INTERVAL,
        use_biometric_filter: bool = False,
        include_images: bool = False,
    ) -> None:
        self.input_folder = Path(input_folder)
        self.output_folder = Path(output_folder)
        self.frame_interval = max(1, frame_interval)
        self.use_biometric_filter = use_biometric_filter
        self.include_images = include_images
        self._saved_count = 0
        self._rejected_count = 0

    @staticmethod
    def is_high_quality(frame: "np.ndarray") -> bool:
        """Return True when Laplacian variance > 80 and mean brightness is 50–200."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        return laplacian_var > LAPLACIAN_THRESHOLD and BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX

    def _make_caption(self, trigger_word: str) -> str:
        return f"{trigger_word}, {CAPTION_SUFFIX}"

    def _write_sample(
        self,
        frame: "np.ndarray",
        stem: str,
        trigger_word: str,
    ) -> None:
        jpg_path = self.output_folder / f"{stem}.jpg"
        txt_path = self.output_folder / f"{stem}.txt"
        cv2.imwrite(str(jpg_path), frame)
        txt_path.write_text(self._make_caption(trigger_word), encoding="utf-8")
        self._saved_count += 1
        logger.info("[OK] %s", jpg_path.name)

    def _passes_biometric(
        self,
        frame: "np.ndarray",
        source_label: str,
        source_path: Path,
        curator: object,
    ) -> bool:
        candidate, reason = curator.evaluate(  # type: ignore[attr-defined]
            frame,
            source_label=source_label,
            source_path=source_path,
            image_path=None,
        )
        if candidate is None:
            self._rejected_count += 1
            logger.info("[SCARTATA] %s - Motivo: %s", source_label, reason)
            return False
        return True

    def _process_frame(
        self,
        frame: "np.ndarray",
        stem: str,
        source_label: str,
        source_path: Path,
        trigger_word: str,
        curator: Optional[object],
    ) -> None:
        if not self.is_high_quality(frame):
            self._rejected_count += 1
            logger.info("[SCARTATA] %s - Motivo: qualità insufficiente", source_label)
            return

        if curator is not None and not self._passes_biometric(
            frame, source_label, source_path, curator
        ):
            return

        self._write_sample(frame, stem, trigger_word)

    def _extract_video_frames(
        self,
        video_path: Path,
        trigger_word: str,
        curator: Optional[object],
    ) -> None:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning("[!] Video corrotto o illeggibile, skip: %s", video_path)
            return

        frame_index = 0
        saved_from_video = 0

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % self.frame_interval == 0:
                    stem = f"{video_path.stem}_f{frame_index:06d}"
                    label = f"{video_path.name} frame {frame_index}"
                    self._process_frame(
                        frame, stem, label, video_path, trigger_word, curator
                    )
                    saved_from_video += 1
                frame_index += 1
        finally:
            capture.release()

        logger.info(
            "[+] %s: campionati %d frame (ogni %d)",
            video_path.name,
            saved_from_video,
            self.frame_interval,
        )

    def _run_media(
        self,
        videos: list[Path],
        images: list[Path],
        trigger_word: str,
        curator: Optional[object],
    ) -> None:
        for video_path in videos:
            logger.info("[*] Elaborazione video: %s", video_path)
            self._extract_video_frames(video_path, trigger_word, curator)

        for image_path in images:
            logger.info("[*] Elaborazione immagine: %s", image_path)
            self._process_image(image_path, trigger_word, curator)

    def _process_image(
        self,
        image_path: Path,
        trigger_word: str,
        curator: Optional[object],
    ) -> None:
        frame = cv2.imread(str(image_path))
        if frame is None:
            logger.warning("[!] Immagine corrotta o illeggibile, skip: %s", image_path)
            return

        rel = image_path.relative_to(self.input_folder)
        stem = str(rel.with_suffix("")).replace("\\", "_").replace("/", "_")
        self._process_frame(
            frame, stem, image_path.name, image_path, trigger_word, curator
        )

    def run(self, trigger_word: str = DEFAULT_TRIGGER_WORD) -> int:
        """Scan input folder and write quality-filtered dataset samples."""
        _require_opencv()

        if not self.input_folder.exists():
            logger.error("Directory di input non trovata: %s", self.input_folder)
            return 1

        videos = list(_iter_videos(self.input_folder))
        images = list(_iter_images(self.input_folder)) if self.include_images else []

        if not videos and not images:
            logger.warning(
                "Nessun file video%s trovato in: %s",
                "/immagine" if self.include_images else "",
                self.input_folder,
            )
            return 1

        self.output_folder.mkdir(parents=True, exist_ok=True)
        self._saved_count = 0
        self._rejected_count = 0

        logger.info("[*] Input:  %s", self.input_folder.resolve())
        logger.info("[*] Output: %s", self.output_folder.resolve())
        logger.info("[*] Trigger word: %s", trigger_word)
        logger.info("[*] Frame interval: ogni %d frame", self.frame_interval)
        if self.use_biometric_filter:
            logger.info("[*] Filtro biometrico: attivo (BiometricCurator)")
        if self.include_images:
            logger.info("[*] Include immagini statiche: sì")

        if self.use_biometric_filter:
            from auto_curator import BiometricCurator

            with BiometricCurator(sharpness_threshold=LAPLACIAN_THRESHOLD) as curator:
                self._run_media(videos, images, trigger_word, curator)
        else:
            self._run_media(videos, images, trigger_word, None)

        if self._saved_count == 0:
            logger.warning("Nessun campione salvato dopo i filtri qualità.")
            return 1

        logger.info(
            "[*] Completato: %d salvati, %d scartati → %s",
            self._saved_count,
            self._rejected_count,
            self.output_folder.resolve(),
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dataset Automator Pro: estrae frame da video (e opzionalmente immagini) "
            "e prepara un dataset LoRA con caption sidecar."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(DEFAULT_INPUT),
        help=f"Cartella sorgente ricorsiva (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"Cartella destinazione dataset (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--trigger-word",
        default=DEFAULT_TRIGGER_WORD,
        help=f"Parola trigger per le caption (default: {DEFAULT_TRIGGER_WORD})",
    )
    parser.add_argument(
        "--frame-interval",
        type=int,
        default=DEFAULT_FRAME_INTERVAL,
        help=f"Estrai ogni N-esimo frame (default: {DEFAULT_FRAME_INTERVAL})",
    )
    parser.add_argument(
        "--use-biometric-filter",
        action="store_true",
        default=False,
        help=(
            "Applica BiometricCurator (single face, sharpness, occlusion) "
            "prima di salvare. Disattivato di default per velocità; "
            "attivalo per qualità facciale superiore."
        ),
    )
    parser.add_argument(
        "--include-images",
        action="store_true",
        default=False,
        help="Includi anche immagini statiche (.jpg, .png, …) oltre ai video.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    automator = DatasetAutomator(
        input_folder=args.input,
        output_folder=args.output,
        frame_interval=args.frame_interval,
        use_biometric_filter=args.use_biometric_filter,
        include_images=args.include_images,
    )
    return automator.run(trigger_word=args.trigger_word)


if __name__ == "__main__":
    raise SystemExit(main())
