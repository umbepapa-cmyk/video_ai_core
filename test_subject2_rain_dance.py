#!/usr/bin/env python3
"""
Parallel test — Soggetto 2: rain dance at dawn (5s, single segment).

Isolated from run_e2e_test.py:
- Scans only inputs/Soggetto 2/
- Separate temp/output paths
- Identity cache via CoreEngine
- enable_safety_checker=False on all Fal calls (core_engine default)

Usage:
    python test_subject2_rain_dance.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("FORCE_REPLICATE", "1")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("subject2_rain_dance.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
from frame_extractor import extract_and_save_frames_for_identity
from generation_progress import estimate_pipeline_seconds, format_eta_range
from identity_cache import compute_folder_hash
from identity_lock_3d import select_best_full_body_image
from prompt_enhancement import inject_body_consistency_prompt

SUBJECT_INPUT_DIR = Path("inputs/Soggetto 2")
TEMP_FACES_DIR = Path("tmpfs/subject2_rain_faces")
OUTPUT_DIR = Path("outputs/subject2_rain_dance")
FINAL_OUTPUT = OUTPUT_DIR / "final_video.mp4"
EASY_OUTPUT = Path("outputs/subject2_turin_rain/final_video.mp4")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

RAIN_DANCE_PROMPT = (
    "A woman dancing nude in the rain on the empty streets of Turin at dawn, cinematic golden "
    "hour light, rain droplets on skin, elegant urban architecture, graceful fluid movement, "
    "photorealistic, 8k, professional cinematography, moody atmospheric"
)
MOTION_KEYWORD = "woman dancing rain city street dawn"
DURATION_SECONDS = 5
ENABLE_AUTOREGRESSIVE = False
QUALITY_PRESET = QualityPreset.HIGH
FRAMES_PER_VIDEO = 5
LAPLACIAN_THRESHOLD = 30.0

_test_stats: Dict[str, Any] = {
    "videos_found": 0,
    "photos_found": 0,
    "frames_extracted": 0,
    "photos_copied": 0,
    "motion_reference_path": None,
    "cache_hash": None,
    "i2v_provider": "replicate",
    "full_body_image": None,
    "v2v_mode_logged": False,
    "replicate_model": None,
    "final_prompt": None,
    "generation_errors": [],
}


class _V2VDebugCapture(logging.Handler):
    import re as _re
    _V2V_RE = _re.compile(r"\[ROUTER\].*V2V", _re.I)
    _REPLICATE_RE = _re.compile(r"Replicate V2V \[([^\]]+)\]", _re.I)
    _MOTION_RE = _re.compile(
        r"(Motion reference (?:retrieved|path resolved)|Dynamic Retriever|motion reference):\s*(.+)",
        _re.I,
    )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if self._V2V_RE.search(msg):
            _test_stats["v2v_mode_logged"] = True
            logger.info("[TEST-CAPTURE] %s", msg)
        m = self._REPLICATE_RE.search(msg)
        if m:
            _test_stats["replicate_model"] = m.group(1)
            logger.info("[TEST-CAPTURE] Replicate model assigned: %s", m.group(1))
        m2 = self._MOTION_RE.search(msg)
        if m2:
            path_hint = m2.group(2).strip()
            if path_hint and not path_hint.startswith("http"):
                _test_stats["motion_reference_path"] = path_hint


def _attach_debug_capture() -> _V2VDebugCapture:
    handler = _V2VDebugCapture()
    handler.setLevel(logging.DEBUG)
    for name in (
        "i2v_router",
        "core_engine",
        "dynamic_retriever",
        "replicate_i2v_provider",
        "provider_adapters",
        "__main__",
    ):
        logging.getLogger(name).addHandler(handler)
    logging.getLogger().addHandler(handler)
    return handler


def log_final_prompt_preview(raw_prompt: str) -> None:
    final_prompt = inject_body_consistency_prompt(raw_prompt, mode="v2v")
    _test_stats["final_prompt"] = final_prompt
    logger.info("=" * 70)
    logger.info("[TEST] Final complete prompt (V2V + body consistency suffix):")
    logger.info("%s", final_prompt)
    logger.info("=" * 70)


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
                logger.warning("Teardown used ignore_errors for %s", path)


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
        prefix = _sanitize_prefix(photo.parent.name)
        dest_name = f"photo_{prefix}_{photo.stem}{photo.suffix.lower()}"
        dest_path = output_dir / dest_name
        if dest_path.exists() and dest_path.stat().st_size == photo.stat().st_size:
            copied += 1
            continue
        shutil.copy2(photo, dest_path)
        copied += 1
    return copied


def check_prerequisites() -> bool:
    logger.info("Verifica prerequisiti...")
    fal_key = os.getenv("FAL_KEY", "")
    if not fal_key or fal_key.strip() in ("", "your_fal_api_key_here"):
        logger.warning("FAL_KEY non configurata — first-frame Flux fallirà")
    else:
        logger.info("✓ FAL_KEY configurata (first-frame Flux)")

    replicate_token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not replicate_token or replicate_token == "your_replicate_api_token_here":
        logger.warning(
            "REPLICATE_API_TOKEN non configurato — fallback Replicate disabilitato "
            "(Fal + obfuscation resta attivo)"
        )
    else:
        logger.info("✓ REPLICATE_API_TOKEN configurato (fallback Replicate)")

    try:
        import replicate  # noqa: F401
        logger.info("✓ replicate installato (fallback opzionale)")
    except ImportError:
        logger.warning("replicate non installato — fallback Replicate disabilitato")

    try:
        import fal_client  # noqa: F401
        logger.info("✓ fal-client installato")
    except ImportError:
        logger.error("fal-client mancante — pip install fal-client")
        return False
    if shutil.which("ffmpeg"):
        logger.info("✓ FFmpeg disponibile")
    else:
        logger.warning("FFmpeg non trovato nel PATH")
    return True


async def setup_phase() -> bool:
    logger.info("=" * 70)
    logger.info("SETUP — Soggetto 2 rain dance (parallelo, isolato da e2e)")
    logger.info("=" * 70)

    if not SUBJECT_INPUT_DIR.exists():
        logger.error("Directory non trovata: %s", SUBJECT_INPUT_DIR)
        return False

    videos, photos = discover_input_media(SUBJECT_INPUT_DIR)
    _test_stats["videos_found"] = len(videos)
    _test_stats["photos_found"] = len(photos)

    logger.info("Scansione %s/:", SUBJECT_INPUT_DIR)
    logger.info("  Video: %d", len(videos))
    for v in videos:
        logger.info("    - %s (%.2f MB)", v.name, v.stat().st_size / 1024 / 1024)
    logger.info("  Foto: %d", len(photos))
    for p in photos[:10]:
        logger.info("    - %s", p.name)
    if len(photos) > 10:
        logger.info("    ... e altre %d foto", len(photos) - 10)

    if not videos and not photos:
        logger.error("Nessun media in %s", SUBJECT_INPUT_DIR)
        return False
    if not check_prerequisites():
        return False

    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
    TEMP_FACES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("✓ Reference dir: %s", TEMP_FACES_DIR)
    logger.info("✓ Output dir: %s", OUTPUT_DIR)
    return True


async def fase1_ingestione_biometrica() -> bool:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 1 — INGESTIONE BIOMETRICA (solo Soggetto 2)")
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
        logger.info("Copia %d foto in %s", len(photos), TEMP_FACES_DIR)
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


async def _resolve_motion_reference(motion_keyword: str) -> Optional[str]:
    try:
        from dynamic_retriever import retrieve_motion_reference

        logger.info("Dynamic Retriever: '%s'", motion_keyword)
        motion_path = await retrieve_motion_reference(motion_keyword, max_duration=DURATION_SECONDS)
        logger.info("✓ Motion reference: %s", motion_path)
        return motion_path
    except Exception as e:
        logger.warning("Dynamic Retriever non disponibile: %s", e)
        logger.info("Proseguo senza motion reference")
        return None


async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 2 — GENERAZIONE VIDEO (5s, HIGH, no autoregressive)")
    logger.info("=" * 70)

    start_time = time.time()
    subjects_payload = {"subject_1": get_reference_faces_dir()}

    logger.info("Parametri:")
    logger.info("  Prompt: %s", RAIN_DANCE_PROMPT)
    logger.info("  Motion keyword: %s", MOTION_KEYWORD)
    logger.info("  Duration: %ds", DURATION_SECONDS)
    logger.info("  Quality: %s", QUALITY_PRESET.name)
    logger.info("  enable_autoregressive: %s", ENABLE_AUTOREGRESSIVE)
    logger.info("  enable_safety_checker: False (policy uncensored)")
    logger.info("  i2v_provider: replicate | FORCE_REPLICATE=1")
    logger.info("  V2V: motion via Dynamic Retriever when available")
    logger.info("  subjects_payload: %s", subjects_payload)

    try:
        fb = select_best_full_body_image([str(SUBJECT_INPUT_DIR.resolve())], faces_dir=get_reference_faces_dir())
        _test_stats["full_body_image"] = str(fb.resolve())
        logger.info("  full_body_image (Soggetto 2 original): %s", fb.name)
    except ValueError as exc:
        logger.warning("  full_body selection preview failed: %s", exc)

    log_final_prompt_preview(RAIN_DANCE_PROMPT)

    eta_low, eta_high = estimate_pipeline_seconds(
        DURATION_SECONDS,
        draft_mode=False,
        autoregressive=ENABLE_AUTOREGRESSIVE,
    )
    logger.info("  Tempo stimato: %s", format_eta_range(eta_low, eta_high))
    logger.info("  Countdown [ETA] ogni ~12s durante Flux/I2V")
    logger.info("")

    engine_config = CoreEngineConfig(
        reference_faces_dir=get_reference_faces_dir(),
        num_angles=5,
        duration_seconds=DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
        motion_keyword=MOTION_KEYWORD,
        i2v_provider="replicate",
    )
    engine = CoreEngine(config=engine_config)

    try:
        gen_result = await engine.generate_high_fidelity_video(
            subjects_payload=subjects_payload,
            prompt=RAIN_DANCE_PROMPT,
            motion_keyword=MOTION_KEYWORD,
            duration_seconds=DURATION_SECONDS,
            output_path=str(OUTPUT_DIR),
        )
        motion_path = getattr(engine, "_motion_reference_video_path", None)
        if motion_path:
            _test_stats["motion_reference_path"] = motion_path
            logger.info("[TEST] Motion reference path: %s", motion_path)
        local_out = await _copy_to_final_output(gen_result.final_video_url)
        result = {
            "video_url": gen_result.final_video_url,
            "final_output": str(local_out),
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
        _test_stats["generation_errors"].append(err_msg)
        logger.error("ERRORE Fase 2: %s", err_msg)
        import traceback
        logger.error(traceback.format_exc())
        return None


async def _copy_to_final_output(source: str) -> Path:
    """Copy engine output to final_video.mp4 (+ easy path)."""
    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    EASY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    if FINAL_OUTPUT.exists():
        FINAL_OUTPUT.unlink()
    if src.exists():
        shutil.copy2(src, FINAL_OUTPUT)
    elif str(source).startswith(("http://", "https://")):
        import httpx
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(source)
            resp.raise_for_status()
            FINAL_OUTPUT.write_bytes(resp.content)
    else:
        raise FileNotFoundError(f"Output video non trovato: {source}")
    if EASY_OUTPUT.exists():
        EASY_OUTPUT.unlink()
    shutil.copy2(FINAL_OUTPUT, EASY_OUTPUT)
    logger.info("[OK] Output: %s", FINAL_OUTPUT.resolve())
    logger.info("[OK] Copia: %s", EASY_OUTPUT.resolve())
    return FINAL_OUTPUT.resolve()


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 3 — TEARDOWN")
    logger.info("=" * 70)
    if result:
        logger.info("Video: %s", result.get("video_url", "N/A"))
    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
        logger.info("✓ Dati biometrici temporanei rimossi: %s", TEMP_FACES_DIR)


def print_final_report(result: Optional[Dict[str, Any]], total_elapsed: float) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("REPORT — SOGGETTO 2 RAIN DANCE")
    logger.info("=" * 70)
    logger.info("Materiale processato:")
    logger.info("  Video input:        %d", _test_stats["videos_found"])
    logger.info("  Foto input:         %d (copiate: %d)", _test_stats["photos_found"], _test_stats["photos_copied"])
    logger.info("  Frame estratti:     %d", _test_stats["frames_extracted"])
    logger.info("  Cache hash:         %s", _test_stats["cache_hash"])
    logger.info("  Full-body image:    %s", _test_stats.get("full_body_image") or "N/A")
    logger.info("  Motion reference:   %s", _test_stats["motion_reference_path"] or "N/A")
    logger.info("  V2V mode logged:    %s", _test_stats.get("v2v_mode_logged"))
    logger.info("  Replicate model:    %s", _test_stats.get("replicate_model") or "N/A")
    logger.info("  I2V provider:       %s", _test_stats["i2v_provider"])
    if _test_stats.get("final_prompt"):
        logger.info("  Final prompt:       %s", _test_stats["final_prompt"])
    logger.info("Tempo totale:         %.2fs", total_elapsed)
    if _test_stats["generation_errors"]:
        logger.info("Errori generazione:")
        for err in _test_stats["generation_errors"]:
            logger.info("  - %s", err)
    if result and FINAL_OUTPUT.exists() and FINAL_OUTPUT.stat().st_size > 0:
        logger.info("Esito:                PASSED")
        logger.info("Output video:         %s", result.get("final_output", result.get("video_url", "N/A")))
        logger.info("Generation time:      %.2fs", result.get("generation_time", 0))
        logger.info("Identity stability:   %.1f%%", result.get("identity_stability", 0) * 100)
    else:
        logger.info("Esito:                FAILED")
        logger.info("Output video:         N/A")


async def main() -> bool:
    logger.info("")
    logger.info("#" * 70)
    logger.info("# SOGGETTO 2 — RAIN DANCE TEST (parallelo, 5s)")
    logger.info("#" * 70)

    total_start = time.time()

    _attach_debug_capture()

    if not await setup_phase():
        print_final_report(None, time.time() - total_start)
        return False
    if not await fase1_ingestione_biometrica():
        await fase3_teardown(None)
        print_final_report(None, time.time() - total_start)
        return False

    result = await fase2_orchestrazione()
    await fase3_teardown(result)

    total_elapsed = time.time() - total_start
    print_final_report(result, total_elapsed)
    return result is not None and FINAL_OUTPUT.exists() and FINAL_OUTPUT.stat().st_size > 0


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.warning("Test interrotto (Ctrl+C)")
        if TEMP_FACES_DIR.exists():
            safe_rmtree(TEMP_FACES_DIR)
        sys.exit(130)
    except Exception as e:
        logger.error("ERRORE CRITICO: %s: %s", type(e).__name__, e)
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
