"""
Canvas expansion pre-processor for V2V generative outpainting (Fase 3.14).

Adds padding around motion-reference video so AnimateDiff / ControlNet can
generatively fill cropped anatomy (head, limbs) while preserving kinematics.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import ffmpeg
except ImportError:
    ffmpeg = None  # type: ignore

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore

try:
    import mediapipe as mp
except ImportError:
    mp = None  # type: ignore

CACHE_DIR = Path(".cache/canvas_expanded")
DEFAULT_PADDING_FRACTION = 0.20
MARGIN_THRESHOLD = 0.05

PADDING_KEYS = (
    "padding_top",
    "padding_bottom",
    "padding_left",
    "padding_right",
)

# MediaPipe Pose landmark indices (subset used for margin detection).
_NOSE = 0
_LEFT_EYE = 2
_RIGHT_EYE = 5
_LEFT_ANKLE = 27
_RIGHT_ANKLE = 28
_LEFT_WRIST = 15
_RIGHT_WRIST = 16
_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12

_RESOLUTION_DIMS = {
    "480p": (854, 480),
    "580p": (1024, 580),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


def _empty_padding() -> dict[str, float]:
    return {key: 0.0 for key in PADDING_KEYS}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_video(video_path: Path) -> tuple[int, int, float, int]:
    """Return (width, height, fps, frame_count)."""
    if cv2 is None:
        raise RuntimeError("opencv-python required for video probe")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 24.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return width, height, fps, frame_count
    finally:
        capture.release()


def _sample_frame_indices(frame_count: int, sample_every: int = 15) -> list[int]:
    if frame_count <= 0:
        return [0]
    indices = {0, frame_count // 2, max(frame_count - 1, 0)}
    step = max(1, sample_every)
    indices.update(range(0, frame_count, step))
    return sorted(indices)


def detect_required_padding(video_path: Path) -> dict[str, float]:
    """
    Autodetect padding fractions via MediaPipe Pose on sampled frames.

    Head landmarks within top 5% → padding_top=0.20.
    Ankles/wrists/shoulders within 5% of frame edges → side/bottom padding.
    """
    padding = _empty_padding()
    path = Path(video_path)
    if not path.exists():
        logger.warning("Canvas autodetect skipped — video not found: %s", path)
        return padding

    if mp is None:
        logger.warning(
            "mediapipe not installed — skipping canvas padding autodetect. "
            "Install with: pip install mediapipe"
        )
        return padding

    if cv2 is None:
        logger.warning("opencv-python not installed — skipping canvas autodetect")
        return padding

    try:
        _, height, _, frame_count = _probe_video(path)
    except Exception as exc:
        logger.warning("Canvas autodetect probe failed: %s", exc)
        return padding

    if height <= 0:
        return padding

    pose = mp.solutions.pose.Pose(
        static_image_mode=True,
        model_complexity=1,
        enable_segmentation=False,
        min_detection_confidence=0.5,
    )

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        logger.warning("Canvas autodetect — cannot open video: %s", path)
        return padding

    head_near_top = False
    feet_near_bottom = False
    limbs_near_left = False
    limbs_near_right = False

    try:
        for idx in _sample_frame_indices(frame_count):
            capture.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = pose.process(rgb)
            if not result.pose_landmarks:
                continue

            lm = result.pose_landmarks.landmark
            head_y = min(lm[_NOSE].y, lm[_LEFT_EYE].y, lm[_RIGHT_EYE].y)
            if head_y < MARGIN_THRESHOLD:
                head_near_top = True

            bottom_y = max(
                lm[_LEFT_ANKLE].y,
                lm[_RIGHT_ANKLE].y,
                lm[_LEFT_WRIST].y,
                lm[_RIGHT_WRIST].y,
            )
            if bottom_y > (1.0 - MARGIN_THRESHOLD):
                feet_near_bottom = True

            left_x = min(
                lm[_LEFT_WRIST].x,
                lm[_LEFT_ANKLE].x,
                lm[_LEFT_SHOULDER].x,
            )
            right_x = max(
                lm[_RIGHT_WRIST].x,
                lm[_RIGHT_ANKLE].x,
                lm[_RIGHT_SHOULDER].x,
            )
            if left_x < MARGIN_THRESHOLD:
                limbs_near_left = True
            if right_x > (1.0 - MARGIN_THRESHOLD):
                limbs_near_right = True
    finally:
        capture.release()
        pose.close()

    if head_near_top:
        padding["padding_top"] = DEFAULT_PADDING_FRACTION
    if feet_near_bottom:
        padding["padding_bottom"] = DEFAULT_PADDING_FRACTION
    if limbs_near_left:
        padding["padding_left"] = DEFAULT_PADDING_FRACTION
    if limbs_near_right:
        padding["padding_right"] = DEFAULT_PADDING_FRACTION

    if any(v > 0 for v in padding.values()):
        logger.info(
            "[CANVAS] Autodetected padding for %s: top=%.2f bottom=%.2f "
            "left=%.2f right=%.2f",
            path.name,
            padding["padding_top"],
            padding["padding_bottom"],
            padding["padding_left"],
            padding["padding_right"],
        )
    else:
        logger.debug("[CANVAS] No padding required for %s", path.name)

    return padding


def compute_expanded_dimensions(
    width: int,
    height: int,
    padding_top: float = 0.0,
    padding_bottom: float = 0.0,
    padding_left: float = 0.0,
    padding_right: float = 0.0,
) -> tuple[int, int]:
    """Return (new_width, new_height) after applying fractional padding."""
    pad_left_px = int(width * padding_left)
    pad_right_px = int(width * padding_right)
    pad_top_px = int(height * padding_top)
    pad_bottom_px = int(height * padding_bottom)
    return width + pad_left_px + pad_right_px, height + pad_top_px + pad_bottom_px


def resolution_for_expanded_canvas(
    base_resolution: str,
    padding: dict[str, float],
    *,
    max_height: int = 720,
) -> str:
    """
    Pick a V2V resolution label proportional to expanded canvas, capped at 720p.

    Maintains aspect ratio of the expanded frame relative to the base preset.
    """
    base_w, base_h = _RESOLUTION_DIMS.get(base_resolution, _RESOLUTION_DIMS["720p"])
    expanded_w, expanded_h = compute_expanded_dimensions(
        base_w,
        base_h,
        padding.get("padding_top", 0.0),
        padding.get("padding_bottom", 0.0),
        padding.get("padding_left", 0.0),
        padding.get("padding_right", 0.0),
    )
    if expanded_h <= 480:
        return "480p"
    scale = min(1.0, max_height / expanded_h)
    scaled_h = expanded_h * scale
    if scaled_h <= 520:
        return "480p"
    if scaled_h <= 620:
        return "580p"
    return "720p"


def _expand_with_ffmpeg_python(
    video_path: Path,
    output_path: Path,
    *,
    pad_left: int,
    pad_right: int,
    pad_top: int,
    pad_bottom: int,
    background_color: str,
) -> None:
    assert ffmpeg is not None
    width, height, fps, _ = _probe_video(video_path)
    new_w = width + pad_left + pad_right
    new_h = height + pad_top + pad_bottom
    x_off = pad_left
    y_off = pad_top
    color = "black" if background_color == "black" else background_color

    stream = ffmpeg.input(str(video_path))
    stream = ffmpeg.filter(
        stream,
        "pad",
        new_w,
        new_h,
        x_off,
        y_off,
        color=color,
    )
    stream = ffmpeg.output(
        stream,
        str(output_path),
        vcodec="libx264",
        preset="medium",
        crf=23,
        pix_fmt="yuv420p",
        r=fps,
        movflags="faststart",
    )
    stream = ffmpeg.overwrite_output(stream)
    ffmpeg.run(stream, capture_stdout=True, capture_stderr=True, quiet=True)


def _expand_with_subprocess(
    video_path: Path,
    output_path: Path,
    *,
    pad_left: int,
    pad_right: int,
    pad_top: int,
    pad_bottom: int,
    background_color: str,
) -> None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise FileNotFoundError("ffmpeg not found in PATH")

    width, height, fps, _ = _probe_video(video_path)
    new_w = width + pad_left + pad_right
    new_h = height + pad_top + pad_bottom
    x_off = pad_left
    y_off = pad_top
    color = "black" if background_color in ("black", "blur") else background_color
    vf = f"pad={new_w}:{new_h}:{x_off}:{y_off}:color={color}"

    cmd = [
        ffmpeg_bin,
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-movflags",
        "faststart",
        "-an",
        str(output_path),
        "-y",
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if process.returncode != 0:
        raise RuntimeError(process.stderr or process.stdout or "ffmpeg pad failed")


def _expand_video_sync(
    video_path: Path,
    output_path: Path,
    *,
    padding_top: float,
    padding_bottom: float,
    padding_left: float,
    padding_right: float,
    background_color: str,
) -> Path:
    width, height, _, _ = _probe_video(video_path)
    pad_left = int(width * padding_left)
    pad_right = int(width * padding_right)
    pad_top = int(height * padding_top)
    pad_bottom = int(height * padding_bottom)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if ffmpeg is not None and background_color != "blur":
        _expand_with_ffmpeg_python(
            video_path,
            output_path,
            pad_left=pad_left,
            pad_right=pad_right,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
            background_color=background_color,
        )
    else:
        _expand_with_subprocess(
            video_path,
            output_path,
            pad_left=pad_left,
            pad_right=pad_right,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
            background_color=background_color,
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Canvas expansion produced empty output: {output_path}")

    logger.info(
        "[CANVAS] Expanded %s → %s (%dx%d + pads → output %s)",
        video_path.name,
        output_path.name,
        width,
        height,
        output_path,
    )
    return output_path


def _ceil_to_multiple(value: int, multiple: int = 64) -> int:
    if value <= 0:
        return multiple
    return ((value + multiple - 1) // multiple) * multiple


def _round_dimensions_to_64(width: int, height: int) -> tuple[int, int]:
    """Ensure width/height are divisible by 64 (round up)."""
    return _ceil_to_multiple(width, 64), _ceil_to_multiple(height, 64)


def _align_video_dimensions_sync(video_path: Path, output_path: Path) -> Path:
    """Scale/pad video so output dimensions are divisible by 64."""
    width, height, fps, _ = _probe_video(video_path)
    target_w, target_h = _round_dimensions_to_64(width, height)
    if target_w == width and target_h == height:
        if video_path.resolve() != output_path.resolve():
            shutil.copy2(video_path, output_path)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise FileNotFoundError("ffmpeg not found in PATH")
    cmd = [
        ffmpeg_bin,
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-an",
        str(output_path),
        "-y",
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if process.returncode != 0:
        raise RuntimeError(process.stderr or process.stdout or "dimension align failed")
    return output_path


async def expand_video_canvas_percent(
    input_path: str | Path,
    output_path: str | Path,
    *,
    pad_top_percent: float = 25,
    pad_bottom_percent: float = 0,
    pad_left_percent: float = 0,
    pad_right_percent: float = 0,
    background_color: str = "black",
) -> Path:
    """
    Expand canvas using percentage-based padding (e.g. pad_top_percent=25 → 25% of height).

    Black padding, centered original, output dimensions rounded up to multiples of 64.
    """
    src = Path(input_path)
    dst = Path(output_path)
    if not src.exists():
        raise FileNotFoundError(f"Input video not found: {src}")

    expanded = await expand_video_canvas(
        src,
        padding_top=pad_top_percent / 100.0,
        padding_bottom=pad_bottom_percent / 100.0,
        padding_left=pad_left_percent / 100.0,
        padding_right=pad_right_percent / 100.0,
        output_path=dst,
        background_color=background_color,
    )

    if expanded.resolve() == src.resolve():
        raise RuntimeError(
            f"Canvas expansion did not produce output for {src} — check ffmpeg availability"
        )

    aligned = dst.with_name(f"{dst.stem}_aligned64{dst.suffix or '.mp4'}")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        partial(_align_video_dimensions_sync, expanded, aligned),
    )
    if aligned.exists() and aligned.stat().st_size > 0:
        if dst.exists():
            dst.unlink()
        aligned.replace(dst)
        logger.info("[CANVAS] Percent expand → %s (64-aligned)", dst)
        return dst
    return result


async def expand_video_canvas(
    video_path: str | Path,
    padding_top: float = 0.0,
    padding_bottom: float = 0.0,
    padding_left: float = 0.0,
    padding_right: float = 0.0,
    output_path: Optional[Path] = None,
    background_color: str = "black",
) -> Path:
    """
    Add margins around *video_path*, keeping the original clip centered.

    Returns path to expanded video, or the original path when no padding is
    needed or ffmpeg is unavailable (graceful degradation).
    """
    src = Path(video_path)
    if not src.exists():
        logger.warning("[CANVAS] Video not found — returning path unchanged: %s", src)
        return src

    if not any(v > 0 for v in (padding_top, padding_bottom, padding_left, padding_right)):
        return src

    if not _ffmpeg_available():
        logger.warning(
            "[CANVAS] ffmpeg not in PATH — skipping canvas expansion for %s",
            src.name,
        )
        return src

    if output_path is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        output_path = CACHE_DIR / f"{src.stem}_expanded{src.suffix or '.mp4'}"

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None,
            partial(
                _expand_video_sync,
                src,
                output_path,
                padding_top=padding_top,
                padding_bottom=padding_bottom,
                padding_left=padding_left,
                padding_right=padding_right,
                background_color=background_color,
            ),
        )
    except Exception as exc:
        logger.warning(
            "[CANVAS] Expansion failed (%s) — using original video: %s",
            exc,
            src,
        )
        return src
