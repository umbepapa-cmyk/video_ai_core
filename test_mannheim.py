#!/usr/bin/env python3
"""
Mannheim — isolated outpainting, restoration & remuxing pipeline.

Local files only (no YouTube / dynamic retriever):
- Identity: inputs/Soggetto 2/ (Subject 2) or fallback inputs/Mannheim/face.jpg
- Guide video: inputs/Mannheim/source_video.mp4 (or source_video.avi)

First-run source (MVI_6705.AVI, 21:34 legacy AVI):
  Copy C:\\Users\\umbep\\Downloads\\MVI_6705.AVI → inputs/Mannheim/source_video.mp4
  OR set MANNHEIM_SOURCE to that path — the script copies into inputs/Mannheim/ on first run.

Usage:
    python test_mannheim.py

Optional env:
    MANNHEIM_SOURCE=C:\\Users\\umbep\\Downloads\\MVI_6705.AVI
    MANNHEIM_SUBJECT_DIR=inputs/Soggetto 2
    MANNHEIM_USE_SUBJECT=2
    MANNHEIM_TARGET_PERSON=left|right|center
    MANNHEIM_CLIP_START=0          (seconds into normalized video)
    MANNHEIM_CLIP_DURATION=12      (trim length for Replicate/V2V; default 12s)
    MANNHEIM_SUBJECT_GENDER=female
    MANNHEIM_MAX_DURATION=10       (V2V duration cap; min with clip length)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("FORCE_REPLICATE", "1")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("test_mannheim.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from canvas_expander import expand_video_canvas_percent
from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
from mannheim_subject import (
    SubjectResolution,
    augment_mannheim_prompt,
    extract_identity_super_vector,
    match_subject_in_video,
    pick_best_swap_reference,
    prepare_subjects_payload,
    resolve_subject_dir,
)
from prompt_enhancement import inject_body_consistency_prompt, inject_outpainting_prompt
from video_enhancer import (
    enhance_old_audio,
    enhance_old_video,
    extract_audio_wav,
    normalize_legacy_video,
    probe_video_metadata,
    remux_video_audio,
    trim_video_segment,
)

INPUT_DIR = Path("inputs/Mannheim")
OUTPUT_DIR = Path("outputs/Mannheim")
TEMP_DIR = OUTPUT_DIR / "temp"
TEMP_FACES_DIR = TEMP_DIR / "faces"

FACE_IMAGE = INPUT_DIR / "face.jpg"
SOURCE_VIDEO_MP4 = INPUT_DIR / "source_video.mp4"
SOURCE_VIDEO_AVI = INPUT_DIR / "source_video.avi"
DEFAULT_DOWNLOADS_AVI = Path(r"C:\Users\umbep\Downloads\MVI_6705.AVI")

NORMALIZED_VIDEO = OUTPUT_DIR / "normalized.mp4"
TRIMMED_VIDEO = OUTPUT_DIR / "trimmed_for_ai.mp4"
ORIGINAL_WAV = OUTPUT_DIR / "original.wav"
ENHANCED_AUDIO_WAV = OUTPUT_DIR / "enhanced_audio.wav"
RESTORED_VIDEO = OUTPUT_DIR / "restored.mp4"
READY_FOR_AI = OUTPUT_DIR / "ready_for_ai.mp4"
VIDEO_SILENT_FINAL = OUTPUT_DIR / "video_silent_final.mp4"
FINAL_OUTPUT = OUTPUT_DIR / "FINAL_MANNHEIM_RESTORED.mp4"

SUBJECT_GENDER = os.getenv("MANNHEIM_SUBJECT_GENDER", "female").strip().lower()
TARGET_PERSON_HINT = os.getenv("MANNHEIM_TARGET_PERSON", "").strip().lower() or None
MAX_DURATION_CAP = float(os.getenv("MANNHEIM_MAX_DURATION", "10"))
CLIP_START_SEC = float(os.getenv("MANNHEIM_CLIP_START", "0"))
CLIP_DURATION_SEC = float(os.getenv("MANNHEIM_CLIP_DURATION", "12"))

MANNHEIM_PROMPT = (
    "Cinematic photorealistic restoration of vintage footage, natural skin texture, "
    "soft film grain, faithful body motion, professional color grading, 8k detail"
)
QUALITY_PRESET = QualityPreset.HIGH
ENABLE_AUTOREGRESSIVE = False


def _log_probe(label: str, meta: dict[str, Any]) -> None:
    logger.info(
        "[MANNHEIM] %s ffprobe: container=%s duration=%.2fs (%s) resolution=%dx%d fps=%.3f audio_ch=%d path=%s",
        label,
        meta.get("container", "?"),
        float(meta.get("duration_sec") or 0),
        _format_duration(float(meta.get("duration_sec") or 0)),
        int(meta.get("width") or 0),
        int(meta.get("height") or 0),
        float(meta.get("fps") or 0),
        int(meta.get("audio_channels") or 0),
        meta.get("path", "?"),
    )


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _copy_source_into_inputs(src: Path) -> Path:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() or ".avi"
    dest = INPUT_DIR / f"source_video{ext}"
    if dest.exists() and dest.stat().st_size > 0:
        logger.info("[MANNHEIM] Input already present: %s", dest.resolve())
        return dest
    logger.info("[MANNHEIM] Copying source → %s", dest.resolve())
    shutil.copy2(src, dest)
    return dest


def _resolve_source_video() -> Path:
    env_source = os.getenv("MANNHEIM_SOURCE", "").strip()
    if env_source:
        src = Path(env_source)
        if not src.exists():
            raise FileNotFoundError(f"MANNHEIM_SOURCE not found: {src}")
        return _copy_source_into_inputs(src)

    if SOURCE_VIDEO_MP4.exists():
        return SOURCE_VIDEO_MP4
    if SOURCE_VIDEO_AVI.exists():
        return SOURCE_VIDEO_AVI

    if DEFAULT_DOWNLOADS_AVI.exists():
        logger.info(
            "[MANNHEIM] No inputs/Mannheim source yet — found %s; copy it here or set MANNHEIM_SOURCE",
            DEFAULT_DOWNLOADS_AVI,
        )
        return _copy_source_into_inputs(DEFAULT_DOWNLOADS_AVI)

    raise FileNotFoundError(
        "Mannheim source video missing. Place one of:\n"
        f"  - {SOURCE_VIDEO_MP4.resolve()}\n"
        f"  - {SOURCE_VIDEO_AVI.resolve()}\n"
        f"  - Copy {DEFAULT_DOWNLOADS_AVI} to inputs/Mannheim/\n"
        "  - Or set MANNHEIM_SOURCE to your AVI/MP4 path"
    )


def _require_inputs(source_video: Path, subject_resolution: SubjectResolution) -> None:
    missing = []
    if not source_video.exists():
        missing.append(str(source_video.resolve()))
    if subject_resolution.source_label == "face.jpg" and not FACE_IMAGE.exists():
        missing.append(str(FACE_IMAGE.resolve()))
    if missing:
        raise FileNotFoundError(
            "Mannheim input files missing. Place the following files and retry:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )


def _clip_duration_for_source(normalized_duration: float) -> float:
    if CLIP_DURATION_SEC <= 0:
        return normalized_duration
    available = max(0.0, normalized_duration - CLIP_START_SEC)
    return min(CLIP_DURATION_SEC, available)


def _prepare_face_reference() -> Dict[str, str]:
    """Legacy helper — prefer prepare_subjects_payload via resolve_subject_dir."""
    subject_dir = resolve_subject_dir()
    resolution = prepare_subjects_payload(
        subject_dir=subject_dir,
        face_image=FACE_IMAGE,
        temp_faces_dir=TEMP_FACES_DIR,
    )
    return resolution.subjects_payload


def _resolve_subject_identity(
    subject_resolution: SubjectResolution,
    video_path: Path,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Match Subject 2 in a two-person clip and pick the best Pass 2 swap reference.

    Returns (face_reference_path, target_position_log).
    """
    if not subject_resolution.subject_dir:
        return None, None

    subject_dir = str(subject_resolution.subject_dir.resolve())
    identity_vector = extract_identity_super_vector(subject_dir)
    if identity_vector is None:
        return None, None

    video_match = match_subject_in_video(
        str(video_path.resolve()),
        identity_vector,
        spatial_hint=TARGET_PERSON_HINT,
    )
    swap_ref = pick_best_swap_reference(subject_dir, identity_vector)

    position_log = None
    if video_match:
        position_log = video_match.position
        if video_match.face_count >= 2:
            logger.info(
                "[MANNHEIM] Two-person clip — face-swap targets Subject 2 on the %s "
                "(similarity=%.3f). Set MANNHEIM_TARGET_PERSON if wrong.",
                video_match.position,
                video_match.similarity,
            )
        else:
            logger.info(
                "[MANNHEIM] Single face matched Subject 2 (sim=%.3f, position=%s)",
                video_match.similarity,
                video_match.position,
            )

    face_ref = str(swap_ref.resolve()) if swap_ref else None
    return face_ref, position_log


def _build_v2v_prompt(*, use_subject2_focus: bool = False) -> str:
    prompt = inject_body_consistency_prompt(MANNHEIM_PROMPT, mode="v2v")
    prompt = inject_outpainting_prompt(prompt, canvas_expanded=True)
    return augment_mannheim_prompt(prompt, use_subject2_focus=use_subject2_focus)


async def _copy_engine_output(source: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    if dest.exists():
        dest.unlink()
    if src.exists():
        shutil.copy2(src, dest)
        return dest.resolve()
    if str(source).startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            resp = await client.get(source)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest.resolve()
    raise FileNotFoundError(f"Engine output not found: {source}")


async def run_v2v_two_pass(
    *,
    ready_video: Path,
    subjects_payload: Dict[str, str],
    duration_seconds: float,
    replicate_token: str,
    face_reference_path: Optional[str] = None,
    use_subject2_focus: bool = False,
) -> Path:
    """Core Engine V2V two-pass (AnimateDiff → face-swap), local motion reference only."""
    if not replicate_token:
        raise RuntimeError(
            "REPLICATE_API_TOKEN required for Mannheim V2V. Set it in .env."
        )

    prompt = _build_v2v_prompt(use_subject2_focus=use_subject2_focus)
    logger.info("[MANNHEIM] V2V prompt: %s", prompt)

    engine_config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=1,
        duration_seconds=duration_seconds,
        output_path=str(TEMP_DIR / "v2v"),
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
        motion_reference_video_path=str(ready_video.resolve()),
        enable_post_i2v_face_swap=True,
        subject_gender=SUBJECT_GENDER,
        i2v_provider="replicate",
        require_reference_face=True,
        face_reference_path=face_reference_path,
    )
    engine = CoreEngine(config=engine_config)
    engine._canvas_expanded = True
    engine._canvas_padding = {
        "padding_top": 0.25,
        "padding_bottom": 0.0,
        "padding_left": 0.0,
        "padding_right": 0.0,
    }

    gen_result = await engine.generate_high_fidelity_video(
        subjects_payload=subjects_payload,
        prompt=prompt,
        duration_seconds=int(duration_seconds),
        output_path=str(TEMP_DIR / "v2v"),
        controlnet_map_path=str(ready_video.resolve()),
    )

    silent_path = await _copy_engine_output(gen_result.final_video_url, VIDEO_SILENT_FINAL)
    logger.info("[MANNHEIM] Silent V2V output: %s", silent_path)
    return silent_path


async def main() -> bool:
    logger.info("")
    logger.info("#" * 70)
    logger.info("# MANNHEIM — Outpainting, Restoration & Remuxing")
    logger.info("#" * 70)

    start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        source_video = _resolve_source_video()
        subject_dir = resolve_subject_dir()
        subject_resolution = prepare_subjects_payload(
            subject_dir=subject_dir,
            face_image=FACE_IMAGE,
            temp_faces_dir=TEMP_FACES_DIR,
        )
        _require_inputs(source_video, subject_resolution)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return False

    subjects_payload = subject_resolution.subjects_payload
    logger.info(
        "[MANNHEIM] Identity source: %s (%s)",
        subject_resolution.source_label,
        subjects_payload["subject_1"],
    )

    try:
        source_meta = probe_video_metadata(source_video)
        _log_probe("Source", source_meta)
    except Exception as exc:
        logger.warning("[MANNHEIM] Source ffprobe failed (%s) — continuing", exc)
        source_meta = {}

    replicate_token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not replicate_token or replicate_token == "your_replicate_api_token_here":
        logger.error(
            "REPLICATE_API_TOKEN missing or placeholder in .env — Mannheim pipeline requires Replicate."
        )
        return False

    # 1. Read source (validated above)
    logger.info("[1/9] Source video: %s", source_video.resolve())

    # 2. Normalize legacy video (AVI 30fps → 24fps, mono preserved, 64-aligned scale)
    logger.info("[2/9] Normalizing legacy video → %s", NORMALIZED_VIDEO)
    await normalize_legacy_video(source_video, NORMALIZED_VIDEO)

    try:
        normalized_meta = probe_video_metadata(NORMALIZED_VIDEO)
        _log_probe("Normalized", normalized_meta)
        normalized_duration = float(normalized_meta.get("duration_sec") or 0)
    except Exception as exc:
        logger.warning("[MANNHEIM] Normalized ffprobe failed (%s)", exc)
        normalized_duration = float(source_meta.get("duration_sec") or CLIP_DURATION_SEC)

    clip_duration = _clip_duration_for_source(normalized_duration)
    if normalized_duration > clip_duration + 1:
        logger.info(
            "[MANNHEIM] Long source (%.1fs) — trimming to clip start=%.1fs duration=%.1fs for Replicate/V2V",
            normalized_duration,
            CLIP_START_SEC,
            clip_duration,
        )
    else:
        logger.info(
            "[MANNHEIM] Clip segment start=%.1fs duration=%.1fs (MANNHEIM_CLIP_* env)",
            CLIP_START_SEC,
            clip_duration,
        )

    # 3. Trim normalized video before AI stages (cost control)
    logger.info("[3/9] Trimming for AI stages → %s", TRIMMED_VIDEO)
    await trim_video_segment(
        NORMALIZED_VIDEO,
        TRIMMED_VIDEO,
        start_sec=CLIP_START_SEC,
        duration_sec=clip_duration,
    )

    duration_seconds = min(clip_duration, MAX_DURATION_CAP)
    logger.info(
        "V2V duration: %.2fs (clip=%.2fs, cap=%.1fs)",
        duration_seconds,
        clip_duration,
        MAX_DURATION_CAP,
    )

    # 4. Extract audio from trimmed clip (not full 21min — saves Replicate cost)
    logger.info(
        "[4/9] Extracting audio from trimmed clip → %s (AI stages use trim only)",
        ORIGINAL_WAV,
    )
    await extract_audio_wav(TRIMMED_VIDEO, ORIGINAL_WAV, mono=True)

    # 5. Enhance audio
    logger.info("[5/9] Enhancing audio via Replicate → %s", ENHANCED_AUDIO_WAV)
    await enhance_old_audio(ORIGINAL_WAV, ENHANCED_AUDIO_WAV, replicate_token)

    # 6. Enhance video (trimmed segment only)
    logger.info("[6/9] Restoring trimmed video via Replicate → %s", RESTORED_VIDEO)
    await enhance_old_video(TRIMMED_VIDEO, RESTORED_VIDEO, replicate_token)

    # 7. Canvas expand (25% top pad)
    logger.info("[7/9] Canvas expand pad_top=25%% → %s", READY_FOR_AI)
    await expand_video_canvas_percent(
        RESTORED_VIDEO,
        READY_FOR_AI,
        pad_top_percent=25,
        pad_bottom_percent=0,
        pad_left_percent=0,
        pad_right_percent=0,
    )

    # 8. Core Engine V2V two-pass
    face_reference_path, _target_position = _resolve_subject_identity(
        subject_resolution,
        READY_FOR_AI,
    )
    logger.info("[8/9] V2V two-pass (Replicate) → %s", VIDEO_SILENT_FINAL)
    await run_v2v_two_pass(
        ready_video=READY_FOR_AI,
        subjects_payload=subjects_payload,
        duration_seconds=duration_seconds,
        replicate_token=replicate_token,
        face_reference_path=face_reference_path,
        use_subject2_focus=subject_resolution.use_subject2_prompt,
    )

    # 9. Remux enhanced audio
    logger.info("[9/9] Remux → %s", FINAL_OUTPUT)
    await remux_video_audio(VIDEO_SILENT_FINAL, ENHANCED_AUDIO_WAV, FINAL_OUTPUT)

    elapsed = time.time() - start
    logger.info("")
    logger.info("=" * 70)
    logger.info("MANNHEIM COMPLETE in %.2fs", elapsed)
    logger.info("Final output: %s (%.2f MB)", FINAL_OUTPUT.resolve(), FINAL_OUTPUT.stat().st_size / 1024 / 1024)
    logger.info("=" * 70)
    return FINAL_OUTPUT.exists() and FINAL_OUTPUT.stat().st_size > 0


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        logger.warning("Mannheim pipeline interrupted (Ctrl+C)")
        sys.exit(130)
    except Exception as exc:
        logger.error("MANNHEIM FAILED: %s: %s", type(exc).__name__, exc)
        import traceback

        logger.error(traceback.format_exc())
        sys.exit(1)
