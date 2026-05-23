"""
Legacy video/audio restoration helpers for Mannheim pipeline.

FFmpeg normalization, audio extraction, Replicate enhancement, and remux.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

try:
    import ffmpeg
except ImportError:
    ffmpeg = None  # type: ignore

try:
    import replicate
except ImportError:
    replicate = None  # type: ignore

logger = logging.getLogger(__name__)

AUDIO_ENHANCE_MODELS = (
    "cjwbw/resemble-enhance",
    "replicate/all-in-one-audio",
)
VIDEO_ENHANCE_MODELS = (
    "lucataco/real-esrgan-video",
    "cjwbw/video-restoration",
)


def _ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError("ffmpeg not found in PATH")
    return path


def _run_subprocess(cmd: list[str], *, timeout: int = 3600) -> None:
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if process.returncode != 0:
        raise RuntimeError(process.stderr or process.stdout or "ffmpeg command failed")


def _ceil_to_multiple(value: int, multiple: int = 64) -> int:
    if value <= 0:
        return multiple
    return ((value + multiple - 1) // multiple) * multiple


def _ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FileNotFoundError("ffprobe not found in PATH")
    return path


def _parse_frame_rate(value: str) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            denominator = float(den)
            if denominator <= 0:
                return 0.0
            return float(num) / denominator
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_video_metadata(input_path: Path) -> dict[str, Any]:
    """Return ffprobe metadata: duration, width, height, fps, audio_channels, container."""
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Input video not found: {src}")

    cmd = [
        _ffprobe_bin(),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(src),
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if process.returncode != 0:
        raise RuntimeError(process.stderr or process.stdout or "ffprobe failed")

    payload = json.loads(process.stdout or "{}")
    video_stream = next(
        (s for s in payload.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (s for s in payload.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )
    fmt = payload.get("format", {})

    duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_frame_rate(
        str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "")
    )
    audio_channels = int(audio_stream.get("channels") or 0)
    container = (fmt.get("format_name") or src.suffix.lstrip(".") or "unknown").split(",")[0]

    return {
        "path": str(src.resolve()),
        "container": container,
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "audio_channels": audio_channels,
    }


def _normalize_video_filter() -> str:
    """30→24 fps conversion plus 64-aligned scale (640×480 → 640×512)."""
    return "fps=24,scale=ceil(iw/64)*64:ceil(ih/64)*64"


def _normalize_legacy_video_sync(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    src = Path(input_path)
    suffix = src.suffix.lower()
    if suffix == ".avi":
        logger.info("[MANNHEIM] Normalizing legacy AVI input: %s", src.name)

    video_vf = _normalize_video_filter()
    has_audio = probe_video_metadata(src).get("audio_channels", 0) > 0

    if ffmpeg is not None:
        try:
            inp = ffmpeg.input(str(src))
            video = inp.video.filter("fps", fps=24).filter(
                "scale", "ceil(iw/64)*64", "ceil(ih/64)*64"
            )
            output_kwargs: dict[str, Any] = {
                "vcodec": "libx264",
                "pix_fmt": "yuv420p",
                "r": 24,
                "movflags": "faststart",
            }
            if has_audio:
                stream = ffmpeg.output(
                    video,
                    inp.audio,
                    str(output_path),
                    acodec="aac",
                    ac=1,
                    **output_kwargs,
                )
            else:
                stream = ffmpeg.output(video, str(output_path), **output_kwargs)
            ffmpeg.run(
                ffmpeg.overwrite_output(stream),
                capture_stdout=True,
                capture_stderr=True,
                quiet=True,
            )
        except Exception as exc:
            logger.warning("ffmpeg-python normalize failed (%s) — falling back to subprocess", exc)
        else:
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path

    ffmpeg_bin = _ffmpeg_bin()
    cmd = [
        ffmpeg_bin,
        "-i",
        str(src),
        "-vf",
        video_vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "24",
        "-movflags",
        "faststart",
    ]
    if has_audio:
        cmd.extend(["-c:a", "aac", "-ac", "1"])
    cmd.extend([str(output_path), "-y"])
    _run_subprocess(cmd)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"normalize_legacy_video produced empty output: {output_path}")
    return output_path


def _trim_video_segment_sync(
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if duration_sec <= 0:
        raise ValueError(f"trim duration must be positive, got {duration_sec}")
    if start_sec < 0:
        raise ValueError(f"trim start must be non-negative, got {start_sec}")

    ffmpeg_bin = _ffmpeg_bin()
    cmd = [
        ffmpeg_bin,
        "-ss",
        str(start_sec),
        "-i",
        str(input_path),
        "-t",
        str(duration_sec),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "24",
        "-c:a",
        "aac",
        "-ac",
        "1",
        "-movflags",
        "faststart",
        str(output_path),
        "-y",
    ]
    _run_subprocess(cmd)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"trim_video_segment produced empty output: {output_path}")
    return output_path


async def trim_video_segment(
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
) -> Path:
    """Trim a segment from normalized video (used before Replicate / V2V stages)."""
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Input video not found: {src}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            _trim_video_segment_sync,
            src,
            Path(output_path),
            float(start_sec),
            float(duration_sec),
        ),
    )


async def normalize_legacy_video(input_path: Path, output_path: Path) -> Path:
    """Normalize legacy video to H.264/yuv420p/24fps with 64-aligned dimensions."""
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Input video not found: {src}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_normalize_legacy_video_sync, src, Path(output_path)),
    )


def _extract_audio_wav_sync(video_path: Path, output_wav: Path, *, mono: bool = True) -> Path:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = _ffmpeg_bin()
    channels = "1" if mono else "2"
    cmd = [
        ffmpeg_bin,
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        channels,
        str(output_wav),
        "-y",
    ]
    _run_subprocess(cmd)
    if not output_wav.exists() or output_wav.stat().st_size == 0:
        raise RuntimeError(f"extract_audio_wav produced empty output: {output_wav}")
    return output_wav


async def extract_audio_wav(video_path: Path, output_wav: Path, *, mono: bool = True) -> Path:
    """Extract audio track from video as 44.1kHz WAV (mono by default for legacy sources)."""
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {src}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_extract_audio_wav_sync, src, Path(output_wav), mono=mono),
    )


def _require_replicate(replicate_token: str) -> str:
    token = (replicate_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
    if not token or token == "your_replicate_api_token_here":
        raise RuntimeError(
            "Replicate unavailable: REPLICATE_API_TOKEN not configured. "
            "Set it in .env for audio/video enhancement."
        )
    if replicate is None:
        raise RuntimeError(
            "Replicate unavailable: replicate package not installed. "
            "Run: pip install replicate>=0.25.0"
        )
    os.environ.setdefault("REPLICATE_API_TOKEN", token)
    return token


def _extract_replicate_url(output: Any) -> Optional[str]:
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list) and output:
        url = str(output[0])
        if url.startswith("http"):
            return url
    if isinstance(output, dict):
        for key in ("output", "video", "audio", "result", "url", "enhanced_audio"):
            val = output.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


async def _download_url(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"Download produced empty file: {dest}")
    return dest


async def _run_replicate_model(
    model_id: str,
    model_input: dict[str, Any],
    *,
    replicate_token: str,
    timeout: int = 900,
) -> Any:
    _require_replicate(replicate_token)
    return await asyncio.wait_for(
        replicate.async_run(model_id, input=model_input),
        timeout=timeout,
    )


async def enhance_old_audio(
    wav_path: Path,
    output_path: Path,
    replicate_token: str,
) -> Path:
    """Enhance legacy audio via Replicate (resemble-enhance or fallback)."""
    src = Path(wav_path)
    if not src.exists():
        raise FileNotFoundError(f"WAV not found: {src}")
    _require_replicate(replicate_token)

    last_error: Optional[BaseException] = None
    for model_id in AUDIO_ENHANCE_MODELS:
        try:
            logger.info("[MANNHEIM] Audio enhancement via %s", model_id)
            if model_id == "cjwbw/resemble-enhance":
                payload = {"input_audio": open(src, "rb")}
            else:
                payload = {"audio": open(src, "rb")}
            try:
                output = await _run_replicate_model(
                    model_id,
                    payload,
                    replicate_token=replicate_token,
                )
            finally:
                for fh in payload.values():
                    if hasattr(fh, "close"):
                        fh.close()

            result_url = _extract_replicate_url(output)
            if not result_url:
                raise RuntimeError(f"{model_id} returned no downloadable URL")

            out = Path(output_path)
            suffix = Path(urlparse(result_url).path).suffix or out.suffix or ".wav"
            if out.suffix.lower() != suffix.lower():
                out = out.with_suffix(suffix)
            await _download_url(result_url, out)
            logger.info("[MANNHEIM] Enhanced audio saved: %s", out)
            return out
        except Exception as exc:
            last_error = exc
            logger.warning("[MANNHEIM] Audio model %s failed: %s", model_id, exc)

    raise RuntimeError(
        f"Replicate audio enhancement failed for all models. Last error: {last_error}"
    ) from last_error


async def enhance_old_video(
    video_path: Path,
    output_path: Path,
    replicate_token: str,
) -> Path:
    """Restore legacy video via Replicate (real-esrgan-video or video-restoration)."""
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {src}")
    _require_replicate(replicate_token)

    last_error: Optional[BaseException] = None
    for model_id in VIDEO_ENHANCE_MODELS:
        try:
            logger.info("[MANNHEIM] Video restoration via %s", model_id)
            if model_id == "lucataco/real-esrgan-video":
                payload = {"video": open(src, "rb"), "scale": 2}
            else:
                payload = {"video": open(src, "rb")}
            try:
                output = await _run_replicate_model(
                    model_id,
                    payload,
                    replicate_token=replicate_token,
                    timeout=1800,
                )
            finally:
                for fh in payload.values():
                    if hasattr(fh, "close"):
                        fh.close()

            result_url = _extract_replicate_url(output)
            if not result_url:
                raise RuntimeError(f"{model_id} returned no downloadable URL")

            out = Path(output_path)
            suffix = Path(urlparse(result_url).path).suffix or ".mp4"
            if out.suffix.lower() != suffix.lower():
                out = out.with_suffix(suffix)
            await _download_url(result_url, out)
            logger.info("[MANNHEIM] Restored video saved: %s", out)
            return out
        except Exception as exc:
            last_error = exc
            logger.warning("[MANNHEIM] Video model %s failed: %s", model_id, exc)

    raise RuntimeError(
        f"Replicate video restoration failed for all models. Last error: {last_error}"
    ) from last_error


def _remux_video_audio_sync(
    video_path: Path,
    audio_wav: Path,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = _ffmpeg_bin()
    cmd = [
        ffmpeg_bin,
        "-i",
        str(video_path),
        "-i",
        str(audio_wav),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "faststart",
        str(output_path),
        "-y",
    ]
    _run_subprocess(cmd)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"remux_video_audio produced empty output: {output_path}")
    return output_path


async def remux_video_audio(
    video_path: Path,
    audio_wav: Path,
    output_path: Path,
) -> Path:
    """Remux silent/processed video with enhanced WAV audio (video copy, AAC audio)."""
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not Path(audio_wav).exists():
        raise FileNotFoundError(f"Audio not found: {audio_wav}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            _remux_video_audio_sync,
            Path(video_path),
            Path(audio_wav),
            Path(output_path),
        ),
    )
