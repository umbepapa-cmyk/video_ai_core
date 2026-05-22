#!/usr/bin/env python3
"""
Fast E2E Draft Test — identity cache + minimal Fal.ai cloud pipeline.

Draft parameters for quick validation (~2-5 min vs 10-25 min full E2E):
- duration_seconds: 2.0
- quality_preset: DRAFT (Flux 12 steps @ 512², I2V 480p)
- enable_autoregressive: False
- Scans inputs/ like run_e2e_test.py

Usage:
    python fast_e2e_test.py
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

from dotenv import load_dotenv

from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
from frame_extractor import extract_and_save_frames_for_identity
from generation_progress import estimate_pipeline_seconds, format_eta_range
from identity_cache import (
    compute_folder_hash,
    invalidate_cache,
    load_cached_identity,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("fast_e2e_test.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

INPUT_DIR = Path("inputs")
TEMP_FACES_DIR = Path("tmpfs/fast_test_faces")
OUTPUT_DIR = Path("outputs/fast_e2e_test")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Draft parameters
FAST_PROMPT = "A person jumping, draft quality"
FAST_DURATION_SECONDS = 2.0
FAST_ENABLE_AUTOREGRESSIVE = False
FRAMES_PER_VIDEO = 3
LAPLACIAN_THRESHOLD = 30.0

_test_stats: Dict[str, Any] = {
    "videos_found": 0,
    "photos_found": 0,
    "frames_extracted": 0,
    "photos_copied": 0,
    "identity_cache_miss_s": None,
    "identity_cache_hit_s": None,
    "cache_hash": None,
}


def safe_rmtree(path: Path, max_retries: int = 3) -> None:
    if not path.exists():
        return
    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                shutil.rmtree(path, ignore_errors=True)


def get_reference_faces_dir() -> str:
    """Absolute resolved path to temp reference faces (Windows-safe)."""
    return str(TEMP_FACES_DIR.resolve())


def discover_input_media(input_dir: Path) -> Tuple[list[Path], list[Path]]:
    videos: list[Path] = []
    photos: list[Path] = []
    if not input_dir.exists():
        return videos, photos
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            videos.append(path)
        elif ext in PHOTO_EXTENSIONS:
            photos.append(path)
    return videos, photos


def _sanitize_prefix(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return safe.strip("_") or "media"


def copy_photos_to_reference(photos: list[Path], output_dir: Path) -> int:
    copied = 0
    for photo in photos:
        prefix = _sanitize_prefix(photo.parent.name) if photo.parent != INPUT_DIR else "root"
        dest_name = f"photo_{prefix}_{photo.stem}{photo.suffix.lower()}"
        dest_path = output_dir / dest_name
        if dest_path.exists() and dest_path.stat().st_size == photo.stat().st_size:
            copied += 1
            continue
        shutil.copy2(photo, dest_path)
        copied += 1
    return copied


def check_prerequisites() -> bool:
    fal_key = os.getenv("FAL_KEY", "")
    if not fal_key or fal_key.strip() in ("", "your_fal_api_key_here"):
        logger.warning("FAL_KEY non configurata — Fase 2 cloud fallirà")
    else:
        logger.info("✓ FAL_KEY configurata")
    try:
        import fal_client  # noqa: F401
        logger.info("✓ fal-client installato")
    except ImportError:
        logger.error("fal-client mancante — pip install fal-client")
        return False
    return True


async def setup_phase() -> bool:
    logger.info("=" * 70)
    logger.info("FAST E2E — SETUP")
    logger.info("=" * 70)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    videos, photos = discover_input_media(INPUT_DIR)
    _test_stats["videos_found"] = len(videos)
    _test_stats["photos_found"] = len(photos)

    if not videos and not photos:
        logger.error("Nessun media in inputs/")
        return False
    if not check_prerequisites():
        return False

    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
    TEMP_FACES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return True


async def fase1_ingestione_biometrica() -> bool:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 1 — INGESTIONE BIOMETRICA (draft)")
    logger.info("=" * 70)

    start_time = time.time()
    videos, photos = discover_input_media(INPUT_DIR)
    total_frames = 0

    for video in videos:
        prefix = _sanitize_prefix(video.stem)
        try:
            frame_data = extract_and_save_frames_for_identity(
                video_path=str(video),
                output_dir=get_reference_faces_dir(),
                num_frames=FRAMES_PER_VIDEO,
                laplacian_threshold=LAPLACIAN_THRESHOLD,
                filename_prefix=prefix,
            )
            total_frames += len(frame_data)
            logger.info(f"  ✓ {len(frame_data)} frame da {video.name}")
        except ValueError as e:
            logger.warning(f"  ⚠ {video.name}: {e}")

    if photos:
        copied = copy_photos_to_reference(photos, TEMP_FACES_DIR)
        _test_stats["photos_copied"] = copied

    reference_files = [p for p in TEMP_FACES_DIR.iterdir() if p.is_file()]
    _test_stats["frames_extracted"] = total_frames
    if not reference_files:
        logger.error("Nessun file reference generato")
        return False

    elapsed = time.time() - start_time
    logger.info(f"Ingestione completata: {len(reference_files)} file in {elapsed:.2f}s")
    return True


async def benchmark_identity_cache() -> None:
    """Measure identity extraction: cache miss vs hit."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("BENCHMARK — IDENTITY CACHE (miss vs hit)")
    logger.info("=" * 70)

    subjects = {"subject_1": get_reference_faces_dir()}
    cache_hash = compute_folder_hash(get_reference_faces_dir())
    _test_stats["cache_hash"] = cache_hash
    logger.info(f"Cache hash: {cache_hash}")

    config = CoreEngineConfig(
        reference_faces_dir=get_reference_faces_dir(),
        num_angles=5,
        duration_seconds=FAST_DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QualityPreset.DRAFT,
        enable_autoregressive=FAST_ENABLE_AUTOREGRESSIVE,
    )
    engine = CoreEngine(config=config)

    invalidate_cache(cache_hash)
    t0 = time.perf_counter()
    await engine._extract_identity(subjects)
    miss_s = time.perf_counter() - t0
    _test_stats["identity_cache_miss_s"] = miss_s
    logger.info(f"Cache MISS (InsightFace): {miss_s:.3f}s")

    cached = load_cached_identity(cache_hash)
    if cached is None:
        logger.warning("Cache non popolata dopo miss — hit benchmark skipped")
        return

    t1 = time.perf_counter()
    await engine._extract_identity(subjects)
    hit_s = time.perf_counter() - t1
    _test_stats["identity_cache_hit_s"] = hit_s
    logger.info(f"Cache HIT (skip InsightFace): {hit_s:.3f}s")
    if hit_s > 0:
        logger.info(f"Speedup: {miss_s / hit_s:.1f}x")


async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 2 — ORCHESTRAZIONE DRAFT (cloud Fal.ai)")
    logger.info("=" * 70)

    start_time = time.time()
    subjects_payload = {"subject_1": get_reference_faces_dir()}

    logger.info("Parametri draft:")
    logger.info(f"  prompt: {FAST_PROMPT}")
    logger.info(f"  duration_seconds: {FAST_DURATION_SECONDS}")
    logger.info(f"  quality_preset: DRAFT")
    logger.info(f"  enable_autoregressive: {FAST_ENABLE_AUTOREGRESSIVE}")
    logger.info(f"  Flux: 12 steps @ 512x512, I2V: 480p")
    eta_low, eta_high = estimate_pipeline_seconds(
        FAST_DURATION_SECONDS,
        draft_mode=True,
        autoregressive=FAST_ENABLE_AUTOREGRESSIVE,
    )
    logger.info(f"  Tempo stimato totale: {format_eta_range(eta_low, eta_high)}")
    logger.info("  Countdown [ETA] visibile ogni ~12s durante generazione")
    logger.info("")

    engine_config = CoreEngineConfig(
        reference_faces_dir=get_reference_faces_dir(),
        num_angles=5,
        duration_seconds=FAST_DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QualityPreset.DRAFT,
        enable_autoregressive=FAST_ENABLE_AUTOREGRESSIVE,
    )
    engine = CoreEngine(config=engine_config)

    try:
        gen_result = await engine.generate_high_fidelity_video(
            subjects_payload=subjects_payload,
            prompt=FAST_PROMPT,
            duration_seconds=int(FAST_DURATION_SECONDS),
            output_path=str(OUTPUT_DIR),
        )

        result = {
            "video_url": gen_result.final_video_url,
            "duration": gen_result.duration_seconds,
            "identity_stability": gen_result.identity_stability_score,
            "generation_time": gen_result.total_generation_time,
            "num_segments": gen_result.num_segments,
        }

        elapsed = time.time() - start_time
        logger.info(f"Fase 2 completata in {elapsed:.2f}s")
        return result

    except Exception as e:
        logger.error(f"ERRORE Fase 2: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 3 — TEARDOWN")
    logger.info("=" * 70)
    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
        logger.info("✓ Dati biometrici temporanei rimossi")
    if result:
        logger.info(f"Video output: {result.get('video_url', 'N/A')}")


def print_final_report(result: Optional[Dict[str, Any]], total_elapsed: float) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("REPORT FAST E2E DRAFT")
    logger.info("=" * 70)
    logger.info(f"Video input:          {_test_stats['videos_found']}")
    logger.info(f"Foto input:           {_test_stats['photos_found']}")
    logger.info(f"Frame estratti:       {_test_stats['frames_extracted']}")
    logger.info(f"Cache hash:           {_test_stats['cache_hash']}")
    miss = _test_stats["identity_cache_miss_s"]
    hit = _test_stats["identity_cache_hit_s"]
    if miss is not None:
        logger.info(f"Identity cache MISS:  {miss:.3f}s")
    if hit is not None:
        logger.info(f"Identity cache HIT:   {hit:.3f}s")
    logger.info(f"Tempo totale:         {total_elapsed:.2f}s")
    full_low, full_high = 10 * 60, 25 * 60
    saved_low = max(0, full_low - total_elapsed)
    saved_high = max(0, full_high - total_elapsed)
    logger.info(
        f"Stima vs run_e2e_test (~10-25 min): risparmio ~{saved_low:.0f}-{saved_high:.0f}s"
    )
    if result:
        logger.info(f"Esito:                PASSED")
        logger.info(f"Output video:         {result.get('video_url', 'N/A')}")
        logger.info(f"Generation time:      {result.get('generation_time', 0):.2f}s")
    else:
        logger.info("Esito:                FAILED")


async def main() -> bool:
    logger.info("")
    logger.info("#" * 70)
    logger.info("# FAST E2E DRAFT TEST (identity cache + Fal.ai cloud)")
    logger.info("#" * 70)

    total_start = time.time()

    if not await setup_phase():
        print_final_report(None, time.time() - total_start)
        return False

    if not await fase1_ingestione_biometrica():
        print_final_report(None, time.time() - total_start)
        return False

    await benchmark_identity_cache()

    result = await fase2_orchestrazione()
    await fase3_teardown(result)

    total_elapsed = time.time() - total_start
    print_final_report(result, total_elapsed)
    return result is not None


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        if TEMP_FACES_DIR.exists():
            safe_rmtree(TEMP_FACES_DIR)
        sys.exit(130)
