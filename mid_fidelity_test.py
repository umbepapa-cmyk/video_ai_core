#!/usr/bin/env python3
"""
Mid-Fidelity E2E Test — balance speed vs identity/kinematic validation.

Between fast_e2e_test (DRAFT dry-run) and run_e2e_test (10s autoregressive HIGH):
- duration_seconds: 5.0 (single segment, no autoregressive)
- quality_preset: STANDARD (720p, Flux 25 steps, I2V 25 steps)
- enable_autoregressive: False
- Scans inputs/Soggetto 1/ only
- Identity cache active
- Dynamic Retriever + ControlNet via motion keyword

Usage:
    python mid_fidelity_test.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from core_engine import CoreEngine, CoreEngineConfig, QualityPreset, get_preset_tuning
from frame_extractor import extract_and_save_frames_for_identity
from generation_progress import estimate_pipeline_seconds, format_eta_range
from identity_cache import compute_folder_hash, invalidate_cache, load_cached_identity

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("mid_fidelity_test.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

INPUT_BASE = Path(__file__).resolve().parent / "inputs"
SUBJECT_INPUT_DIR = INPUT_BASE / "Soggetto 1"
TEMP_FACES_DIR = Path("tmpfs/mid_fidelity_faces")
OUTPUT_DIR = Path("outputs/mid_fidelity_test")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

MID_FIRST_FRAME_PROMPT = (
    "Extreme close-up macro portrait of the athlete's face looking concentrated before a dive, "
    "blurred background, cinematic lighting, photorealistic"
)
MID_VIDEO_PROMPT = (
    "Cinematic sports broadcast tracking shot. Camera dynamically pulls back from close-up to "
    "reveal full body leaping off platform, triple somersault dive into pool. "
    "Photorealistic, 4k, TV broadcast style."
)
MOTION_KEYWORD = "olympic diver platform somersault"
DURATION_SECONDS = 5.0
ENABLE_AUTOREGRESSIVE = False
QUALITY_PRESET = QualityPreset.STANDARD
FRAMES_PER_VIDEO = 5
LAPLACIAN_THRESHOLD = 30.0

PARALLEL_TEST_SCRIPTS = ("run_e2e_test.py", "test_subject2_rain_dance.py")
WAIT_PARALLEL_MAX_S = 300

_test_stats: Dict[str, Any] = {
    "videos_found": 0,
    "photos_found": 0,
    "frames_extracted": 0,
    "photos_copied": 0,
    "motion_reference_path": None,
    "cache_hash": None,
    "identity_cache_miss_s": None,
    "identity_cache_hit_s": None,
    "fal_errors": [],
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


def discover_input_media(input_dir: Path) -> Tuple[List[Path], List[Path]]:
    videos: List[Path] = []
    photos: List[Path] = []
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


def copy_photos_to_reference(photos: List[Path], output_dir: Path) -> int:
    copied = 0
    for photo in photos:
        prefix = _sanitize_prefix(photo.parent.name) if photo.parent != SUBJECT_INPUT_DIR else "root"
        dest_name = f"photo_{prefix}_{photo.stem}{photo.suffix.lower()}"
        dest_path = output_dir / dest_name
        if dest_path.exists() and dest_path.stat().st_size == photo.stat().st_size:
            copied += 1
            continue
        shutil.copy2(photo, dest_path)
        copied += 1
    return copied


def _python_processes_running(scripts: Tuple[str, ...]) -> List[str]:
    """Return script names still running as python processes."""
    active: List[str] = []
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
                "| Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
        for script in scripts:
            if script in output:
                active.append(script)
    except Exception as exc:
        logger.warning("Impossibile verificare processi paralleli: %s", exc)
    return active


async def wait_for_parallel_tests(max_wait_s: int = WAIT_PARALLEL_MAX_S) -> None:
    """Wait until run_e2e_test / test_subject2 finish (max 5 min)."""
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        active = _python_processes_running(PARALLEL_TEST_SCRIPTS)
        if not active:
            logger.info("Nessun test parallelo attivo — avvio mid_fidelity_test")
            return
        logger.info(
            "Test paralleli in corso (%s) — attesa 15s (max %ds)...",
            ", ".join(active),
            max_wait_s,
        )
        await asyncio.sleep(15)
    active = _python_processes_running(PARALLEL_TEST_SCRIPTS)
    if active:
        logger.warning(
            "Timeout attesa (%ds) — test ancora attivi: %s. Avvio comunque.",
            max_wait_s,
            ", ".join(active),
        )


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
    logger.info("MID-FIDELITY — SETUP (Soggetto 1)")
    logger.info("=" * 70)

    if not SUBJECT_INPUT_DIR.exists():
        logger.error("Directory non trovata: %s", SUBJECT_INPUT_DIR)
        return False

    videos, photos = discover_input_media(SUBJECT_INPUT_DIR)
    _test_stats["videos_found"] = len(videos)
    _test_stats["photos_found"] = len(photos)

    logger.info("Scansione %s/:", SUBJECT_INPUT_DIR)
    logger.info("  Video: %d | Foto: %d", len(videos), len(photos))
    for v in videos:
        logger.info("    - %s", v.relative_to(SUBJECT_INPUT_DIR))
    for p in photos:
        logger.info("    - %s", p.relative_to(SUBJECT_INPUT_DIR))

    if not videos and not photos:
        logger.error("Nessun media in %s", SUBJECT_INPUT_DIR)
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
    logger.info("FASE 1 — INGESTIONE BIOMETRICA (Soggetto 1)")
    logger.info("=" * 70)

    start_time = time.time()
    videos, photos = discover_input_media(SUBJECT_INPUT_DIR)
    total_frames = 0

    for video in videos:
        prefix = _sanitize_prefix(video.stem)
        logger.info("Estrazione frame da: %s", video.name)
        try:
            frame_data = extract_and_save_frames_for_identity(
                video_path=str(video),
                output_dir=get_reference_faces_dir(),
                num_frames=FRAMES_PER_VIDEO,
                laplacian_threshold=LAPLACIAN_THRESHOLD,
                filename_prefix=prefix,
            )
            total_frames += len(frame_data)
            logger.info("  ✓ %d frame estratti", len(frame_data))
        except ValueError as e:
            logger.warning("  ⚠ %s: %s", video.name, e)

    if photos:
        copied = copy_photos_to_reference(photos, TEMP_FACES_DIR)
        _test_stats["photos_copied"] = copied
        logger.info("✓ %d foto disponibili come reference", copied)

    reference_files = [p for p in TEMP_FACES_DIR.iterdir() if p.is_file()]
    _test_stats["frames_extracted"] = total_frames
    if not reference_files:
        logger.error("Nessun file reference generato")
        return False

    cache_hash = compute_folder_hash(get_reference_faces_dir())
    _test_stats["cache_hash"] = cache_hash
    logger.info("Identity cache hash: %s (%d file)", cache_hash, len(reference_files))

    elapsed = time.time() - start_time
    logger.info("Fase 1 completata in %.2fs", elapsed)
    return True


async def benchmark_identity_cache() -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("BENCHMARK — IDENTITY CACHE (miss vs hit)")
    logger.info("=" * 70)

    subjects = {"subject_1": get_reference_faces_dir()}
    cache_hash = compute_folder_hash(get_reference_faces_dir())
    _test_stats["cache_hash"] = cache_hash

    config = CoreEngineConfig(
        reference_faces_dir=get_reference_faces_dir(),
        num_angles=5,
        duration_seconds=DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
    )
    engine = CoreEngine(config=config)

    invalidate_cache(cache_hash)
    t0 = time.perf_counter()
    await engine._extract_identity(subjects)
    miss_s = time.perf_counter() - t0
    _test_stats["identity_cache_miss_s"] = miss_s
    logger.info("Cache MISS (InsightFace): %.3fs", miss_s)

    if load_cached_identity(cache_hash) is None:
        logger.warning("Cache non popolata dopo miss — hit benchmark skipped")
        return

    t1 = time.perf_counter()
    await engine._extract_identity(subjects)
    hit_s = time.perf_counter() - t1
    _test_stats["identity_cache_hit_s"] = hit_s
    logger.info("Cache HIT (skip InsightFace): %.3fs", hit_s)
    if hit_s > 0:
        logger.info("Speedup: %.1fx", miss_s / hit_s)


async def _resolve_motion_reference(motion_keyword: str) -> Optional[str]:
    try:
        from dynamic_retriever import retrieve_motion_reference

        logger.info("Dynamic Retriever: '%s'", motion_keyword)
        motion_path = await retrieve_motion_reference(
            motion_keyword, max_duration=int(DURATION_SECONDS)
        )
        logger.info("✓ Motion reference: %s", motion_path)
        return motion_path
    except Exception as e:
        logger.warning("Dynamic Retriever non disponibile: %s", e)
        logger.info("Proseguo senza motion reference (solo prompt testuale)")
        return None


async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 2 — ORCHESTRAZIONE MID-FIDELITY (cloud Fal.ai)")
    logger.info("=" * 70)

    start_time = time.time()
    subjects_payload = {"subject_1": get_reference_faces_dir()}
    tuning = get_preset_tuning(QUALITY_PRESET)

    logger.info("Parametri mid-fidelity:")
    logger.info("  first_frame_prompt: %s", MID_FIRST_FRAME_PROMPT)
    logger.info("  video_prompt: %s", MID_VIDEO_PROMPT)
    logger.info("  motion_keyword: %s", MOTION_KEYWORD)
    logger.info("  duration_seconds: %.1f", DURATION_SECONDS)
    logger.info("  quality_preset: %s", QUALITY_PRESET.name)
    logger.info("  enable_autoregressive: %s", ENABLE_AUTOREGRESSIVE)
    logger.info(
        "  Flux: %d steps @ %s | I2V: %s (%d steps)",
        tuning["flux_steps"],
        tuning["image_size"],
        tuning["resolution"],
        tuning["i2v_steps"],
    )
    logger.info("  enable_safety_checker: False (policy uncensored)")

    controlnet_map_path = await _resolve_motion_reference(MOTION_KEYWORD)
    _test_stats["motion_reference_path"] = controlnet_map_path

    eta_low, eta_high = estimate_pipeline_seconds(
        DURATION_SECONDS,
        draft_mode=False,
        autoregressive=ENABLE_AUTOREGRESSIVE,
    )
    logger.info("  Tempo stimato totale: %s", format_eta_range(eta_low, eta_high))
    logger.info("  Stima con cache hit: ~3-5 min")
    logger.info("  Countdown [ETA] ogni ~12s durante Flux/I2V")
    logger.info("")

    engine_config = CoreEngineConfig(
        reference_faces_dir=get_reference_faces_dir(),
        num_angles=5,
        duration_seconds=DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        controlnet_map_path=controlnet_map_path,
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
        identity_adapter_strength=0.95,
    )
    engine = CoreEngine(config=engine_config)

    try:
        gen_result = await engine.generate_high_fidelity_video(
            subjects_payload=subjects_payload,
            prompt=MID_VIDEO_PROMPT,
            first_frame_prompt=MID_FIRST_FRAME_PROMPT,
            controlnet_map_path=controlnet_map_path,
            duration_seconds=int(DURATION_SECONDS),
            output_path=str(OUTPUT_DIR),
        )
        result = {
            "video_url": gen_result.final_video_url,
            "duration": gen_result.duration_seconds,
            "identity_stability": gen_result.identity_stability_score,
            "temporal_consistency": gen_result.temporal_consistency_score,
            "generation_time": gen_result.total_generation_time,
            "num_segments": gen_result.num_segments,
        }
        elapsed = time.time() - start_time
        logger.info("Fase 2 completata in %.2fs", elapsed)
        return result

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        _test_stats["fal_errors"].append(err_msg)
        logger.error("ERRORE Fase 2: %s", err_msg)
        import traceback
        logger.error(traceback.format_exc())
        return None


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 3 — TEARDOWN")
    logger.info("=" * 70)
    if result:
        logger.info("Video output: %s", result.get("video_url", "N/A"))
    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
        logger.info("✓ Dati biometrici temporanei rimossi")


def print_final_report(result: Optional[Dict[str, Any]], total_elapsed: float) -> None:
    tuning = get_preset_tuning(QUALITY_PRESET)
    logger.info("")
    logger.info("=" * 70)
    logger.info("REPORT MID-FIDELITY TEST")
    logger.info("=" * 70)
    logger.info("Materiale (Soggetto 1):")
    logger.info("  Video input:        %d", _test_stats["videos_found"])
    logger.info("  Foto input:         %d (copiate: %d)", _test_stats["photos_found"], _test_stats["photos_copied"])
    logger.info("  Frame estratti:     %d", _test_stats["frames_extracted"])
    logger.info("  Cache hash:         %s", _test_stats["cache_hash"])
    miss = _test_stats["identity_cache_miss_s"]
    hit = _test_stats["identity_cache_hit_s"]
    if miss is not None:
        logger.info("  Identity MISS:      %.3fs", miss)
    if hit is not None:
        logger.info("  Identity HIT:       %.3fs", hit)
    logger.info("  Motion reference:   %s", _test_stats["motion_reference_path"] or "N/A")
    logger.info("Preset tuning applicato:")
    logger.info("  flux_steps:         %d", tuning["flux_steps"])
    logger.info("  i2v_steps:          %d", tuning["i2v_steps"])
    logger.info("  resolution:         %s", tuning["resolution"])
    logger.info("Tempo totale:         %.2fs", total_elapsed)
    if _test_stats["fal_errors"]:
        logger.info("Errori Fal:")
        for err in _test_stats["fal_errors"]:
            logger.info("  - %s", err)
    if result:
        logger.info("Esito:                PASSED")
        logger.info("Output video:         %s", result.get("video_url", "N/A"))
        logger.info("Generation time:      %.2fs", result.get("generation_time", 0))
        logger.info("Identity stability:   %.1f%%", result.get("identity_stability", 0) * 100)
    else:
        logger.info("Esito:                FAILED")
        logger.info("Output video:         N/A")


async def main() -> bool:
    logger.info("")
    logger.info("#" * 70)
    logger.info("# MID-FIDELITY E2E TEST (Soggetto 1 — diver, 5s STANDARD)")
    logger.info("#" * 70)

    await wait_for_parallel_tests()

    total_start = time.time()

    if not await setup_phase():
        print_final_report(None, time.time() - total_start)
        return False
    if not await fase1_ingestione_biometrica():
        await fase3_teardown(None)
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
