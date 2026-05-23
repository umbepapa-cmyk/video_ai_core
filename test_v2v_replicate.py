#!/usr/bin/env python3
"""
Fase 3.9 — Integration Test Finale V2V Full-Body via Replicate.

Validates the full kinematic pipeline:
1. Full-body subject selection from inputs/Soggetto 1|2 (original photos, not tmpfs crops)
2. Dynamic Retriever → V2V branch (olympic platform dive somersault)
3. Replicate-only video generation (FORCE_REPLICATE / i2v_provider=replicate)
4. Body-consistency prompt injection (Phase 3.8)

Usage:
    python test_v2v_replicate.py

Optional env:
    V2V_SUBJECT_DIR=inputs/Soggetto 1
    V2V_FULL_BODY_IMAGE=path/to/photo.jpg
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# Force Replicate before router import paths resolve provider order.
os.environ.setdefault("FORCE_REPLICATE", "1")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("test_v2v_replicate.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
from frame_extractor import extract_and_save_frames_for_identity
from generation_progress import estimate_pipeline_seconds, format_eta_range
from identity_cache import compute_folder_hash, invalidate_cache, load_cached_identity
from identity_lock_3d import score_reference_image, select_best_full_body_image
from prompt_enhancement import inject_body_consistency_prompt

INPUT_BASE = Path(__file__).resolve().parent / "inputs"
SUBJECT_CANDIDATE_DIRS = (
    INPUT_BASE / "Soggetto 1",
    INPUT_BASE / "Soggetto 2",
)
TEMP_IDENTITY_DIR = Path("tmpfs/v2v_replicate_faces")
OUTPUT_DIR = Path("outputs/v2v_replicate_test")
FINAL_OUTPUT = Path("outputs/final_v2v_test.mp4")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

MOTION_KEYWORD = "olympic platform dive somersault"
VIDEO_PROMPT = (
    "Cinematic wide shot, tracking the athlete diving gracefully into the blue pool, "
    "photorealistic, 8k, professional broadcast."
)
FIRST_FRAME_PROMPT = (
    "Extreme close-up macro portrait of the athlete's face looking concentrated before a dive, "
    "sweat on forehead, blurred background, cinematic lighting, photorealistic"
)
DURATION_SECONDS = 5
SEGMENT_DURATION = 5.0
ENABLE_AUTOREGRESSIVE = True
QUALITY_PRESET = QualityPreset.HIGH
IDENTITY_ADAPTER_STRENGTH = 0.98
FRAMES_PER_VIDEO = 5
LAPLACIAN_THRESHOLD = 30.0

_test_stats: Dict[str, Any] = {
    "subject_dir": None,
    "full_body_image": None,
    "videos_found": 0,
    "photos_found": 0,
    "frames_extracted": 0,
    "motion_reference_path": None,
    "motion_reference_cache": None,
    "cache_hash": None,
    "v2v_mode_logged": False,
    "replicate_model": None,
    "final_prompt": None,
    "identity_cache_miss_s": None,
    "identity_cache_hit_s": None,
    "generation_errors": [],
}


class _V2VDebugCapture(logging.Handler):
    """Capture router / retriever / prompt lines for explicit test reporting."""

    _V2V_RE = re.compile(r"\[ROUTER\].*V2V", re.I)
    _REPLICATE_RE = re.compile(r"Replicate V2V \[([^\]]+)\]", re.I)
    _MOTION_RE = re.compile(
        r"(Motion reference (?:retrieved|path resolved)|Dynamic Retriever|motion reference):\s*(.+)",
        re.I,
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
                _test_stats["motion_reference_cache"] = path_hint


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


def _is_placeholder_token(value: str, placeholders: Tuple[str, ...]) -> bool:
    v = (value or "").strip()
    return not v or v in placeholders


def check_prerequisites() -> bool:
    """Fail fast on missing REPLICATE_API_TOKEN; warn on FAL_KEY / ffmpeg."""
    logger.info("Verifica prerequisiti Fase 3.9...")

    replicate_token = os.getenv("REPLICATE_API_TOKEN", "")
    if _is_placeholder_token(
        replicate_token,
        ("", "your_replicate_api_token_here"),
    ):
        logger.error(
            "REPLICATE_API_TOKEN mancante o placeholder in .env — "
            "test V2V Replicate non eseguibile."
        )
        return False
    logger.info("[OK] REPLICATE_API_TOKEN configurato")

    fal_key = os.getenv("FAL_KEY", "")
    if _is_placeholder_token(fal_key, ("", "your_fal_api_key_here")):
        logger.error(
            "FAL_KEY mancante — richiesto per PuLID first-frame e upload motion reference V2V."
        )
        return False
    logger.info("[OK] FAL_KEY configurata (first-frame PuLID + motion CDN upload)")

    try:
        import fal_client  # noqa: F401
        logger.info("[OK] fal-client installato")
    except ImportError:
        logger.error("fal-client mancante — pip install fal-client")
        return False

    try:
        import replicate  # noqa: F401
        logger.info("[OK] replicate installato")
    except ImportError:
        logger.error("replicate mancante — pip install replicate")
        return False

    if shutil.which("ffmpeg"):
        logger.info("[OK] FFmpeg disponibile (Dynamic Retriever fallback)")
    else:
        logger.warning("FFmpeg non nel PATH — Dynamic Retriever può usare fallback limitato")

    return True


def _fast_pick_largest_face_photo(
    subject_dir: Path,
    *,
    max_candidates: int = 15,
) -> Optional[Path]:
    """
    Scan only the largest photos (by bytes) for a detectable face — fast startup.
    """
    _videos, photos = discover_input_media(subject_dir)
    if not photos:
        return None
    ranked = sorted(photos, key=lambda p: p.stat().st_size, reverse=True)
    for photo in ranked[:max_candidates]:
        if score_reference_image(str(photo)).has_face:
            return photo.resolve()
    return ranked[0].resolve()


def resolve_full_body_subject() -> Tuple[Path, Path]:
    """
    Pick best full-body subject directory and reference photo.

    Prefers inputs/Soggetto 1|2; largest original with detectable person wins.
    """
    override_image = os.getenv("V2V_FULL_BODY_IMAGE", "").strip()
    if override_image:
        img = Path(override_image).resolve()
        if not img.is_file():
            raise FileNotFoundError(f"V2V_FULL_BODY_IMAGE not found: {img}")
        subject_dir = img.parent
        logger.info("Using user-specified full-body image: %s", img)
        return subject_dir, img

    override_dir = os.getenv("V2V_SUBJECT_DIR", "").strip()
    if override_dir:
        subject_dir = Path(override_dir).resolve()
        if not subject_dir.is_dir():
            raise FileNotFoundError(f"V2V_SUBJECT_DIR not found: {subject_dir}")
        photo = _fast_pick_largest_face_photo(subject_dir) or select_best_full_body_image(
            [str(subject_dir)]
        )
        return subject_dir, Path(photo).resolve()

    best_global: Optional[Tuple[int, Path, Path]] = None
    for subject_dir in SUBJECT_CANDIDATE_DIRS:
        if not subject_dir.exists():
            continue
        photo = _fast_pick_largest_face_photo(subject_dir)
        if photo is None:
            continue
        size = photo.stat().st_size
        if best_global is None or size > best_global[0]:
            best_global = (size, subject_dir.resolve(), photo)

    if best_global:
        _, subject_dir, photo = best_global
        logger.info(
            "Selected subject dir: %s | full-body photo: %s (%.2f MB)",
            subject_dir,
            photo.name,
            photo.stat().st_size / 1024 / 1024,
        )
        return subject_dir, photo

    raise FileNotFoundError(
        "Nessun soggetto trovato in inputs/Soggetto 1 o inputs/Soggetto 2"
    )


def log_final_prompt_preview(raw_prompt: str) -> str:
    """Log the exact V2V prompt sent after body-consistency injection."""
    final_prompt = inject_body_consistency_prompt(raw_prompt, mode="v2v")
    _test_stats["final_prompt"] = final_prompt
    logger.info("=" * 70)
    logger.info("[TEST] Final complete prompt (V2V + body consistency suffix):")
    logger.info("%s", final_prompt)
    logger.info("=" * 70)
    return final_prompt


async def setup_phase(subject_dir: Path) -> bool:
    logger.info("=" * 70)
    logger.info("FASE SETUP — V2V Replicate full-body test")
    logger.info("=" * 70)

    videos, photos = discover_input_media(subject_dir)
    _test_stats["videos_found"] = len(videos)
    _test_stats["photos_found"] = len(photos)
    _test_stats["subject_dir"] = str(subject_dir)

    logger.info("Subject directory: %s", subject_dir)
    logger.info("  Video: %d | Foto: %d", len(videos), len(photos))

    if not videos and not photos:
        logger.error("Nessun media nel subject dir")
        return False

    if not check_prerequisites():
        return False

    if TEMP_IDENTITY_DIR.exists():
        safe_rmtree(TEMP_IDENTITY_DIR)
    TEMP_IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    return True


async def fase1_ingestione_biometrica(subject_dir: Path) -> bool:
    """
    Extract video frames to tmpfs for supplementary identity angles.

    subjects_payload points at original inputs/ (full-body), NOT tmpfs crops.
    """
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 1 — INGESTIONE (identity tmpfs + full-body originals)")
    logger.info("=" * 70)

    start = time.time()
    videos, photos = discover_input_media(subject_dir)
    total_frames = 0

    for video in videos:
        prefix = _sanitize_prefix(video.stem)
        logger.info("Estrazione frame (tmpfs) da: %s", video.name)
        try:
            frame_data = extract_and_save_frames_for_identity(
                video_path=str(video.resolve()),
                output_dir=str(TEMP_IDENTITY_DIR.resolve()),
                num_frames=FRAMES_PER_VIDEO,
                laplacian_threshold=LAPLACIAN_THRESHOLD,
                filename_prefix=prefix,
            )
            total_frames += len(frame_data)
            logger.info("  [OK] %d frame in tmpfs (non usati come full-body ref)", len(frame_data))
        except ValueError as exc:
            logger.warning("  ⚠ %s: %s", video.name, exc)

    _test_stats["frames_extracted"] = total_frames
    logger.info(
        "Full-body V2V reference: originals in %s (NOT tmpfs face crops)",
        subject_dir,
    )

    cache_hash = compute_folder_hash(str(subject_dir.resolve()))
    _test_stats["cache_hash"] = cache_hash
    logger.info("Identity cache hash (subject dir): %s", cache_hash)

    logger.info("Fase 1 completata in %.2fs", time.time() - start)
    return True


async def benchmark_identity_cache(subjects_payload: Dict[str, str]) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("BENCHMARK — IDENTITY CACHE")
    logger.info("=" * 70)

    cache_hash = compute_folder_hash(subjects_payload["subject_1"])
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=5,
        duration_seconds=DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
        segment_duration=SEGMENT_DURATION,
        identity_adapter_strength=IDENTITY_ADAPTER_STRENGTH,
        motion_keyword=MOTION_KEYWORD,
        i2v_provider="replicate",
        subject_gender="female",
    )
    engine = CoreEngine(config=config)

    invalidate_cache(cache_hash)
    t0 = time.perf_counter()
    await engine._extract_identity(subjects_payload)
    miss_s = time.perf_counter() - t0
    _test_stats["identity_cache_miss_s"] = miss_s
    logger.info("Cache MISS (InsightFace): %.3fs", miss_s)

    if load_cached_identity(cache_hash) is None:
        logger.warning("Cache non popolata — skip hit benchmark")
        return

    t1 = time.perf_counter()
    await engine._extract_identity(subjects_payload)
    hit_s = time.perf_counter() - t1
    _test_stats["identity_cache_hit_s"] = hit_s
    logger.info("Cache HIT: %.3fs (speedup %.1fx)", hit_s, miss_s / max(hit_s, 1e-6))


async def fase2_orchestrazione_v2v(
    subjects_payload: Dict[str, str],
    full_body_image: Path,
) -> Optional[Dict[str, Any]]:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 2 — ORCHESTRAZIONE V2V (Replicate-only, 15s autoregressive)")
    logger.info("=" * 70)

    start = time.time()
    _test_stats["full_body_image"] = str(full_body_image)

    log_final_prompt_preview(VIDEO_PROMPT)

    logger.info("Parametri generazione:")
    logger.info("  motion_keyword: %s", MOTION_KEYWORD)
    logger.info("  duration_seconds: %d", DURATION_SECONDS)
    logger.info("  enable_autoregressive: %s (segment_duration=%.1fs)", ENABLE_AUTOREGRESSIVE, SEGMENT_DURATION)
    logger.info("  quality_preset: %s", QUALITY_PRESET.name)
    logger.info("  identity_adapter_strength: %.2f", IDENTITY_ADAPTER_STRENGTH)
    logger.info("  i2v_provider: replicate | FORCE_REPLICATE=1")
    logger.info("  subjects_payload: %s", subjects_payload)
    logger.info("  full_body_image (original): %s", full_body_image.name)

    eta_low, eta_high = estimate_pipeline_seconds(
        DURATION_SECONDS,
        draft_mode=False,
        autoregressive=ENABLE_AUTOREGRESSIVE,
        segment_duration=SEGMENT_DURATION,
    )
    logger.info("  Tempo stimato pipeline: %s", format_eta_range(eta_low, eta_high))
    logger.info("  Countdown [ETA] ogni ~12s durante Flux/Replicate V2V")
    logger.info("")

    engine_config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=5,
        duration_seconds=DURATION_SECONDS,
        output_path=str(OUTPUT_DIR),
        quality_preset=QUALITY_PRESET,
        enable_autoregressive=ENABLE_AUTOREGRESSIVE,
        segment_duration=SEGMENT_DURATION,
        identity_adapter_strength=IDENTITY_ADAPTER_STRENGTH,
        motion_keyword=MOTION_KEYWORD,
        i2v_provider="replicate",
        subject_gender="female",
    )
    engine = CoreEngine(config=engine_config)

    try:
        gen_result = await engine.generate_high_fidelity_video(
            subjects_payload=subjects_payload,
            prompt=VIDEO_PROMPT,
            first_frame_prompt=FIRST_FRAME_PROMPT,
            motion_keyword=MOTION_KEYWORD,
            duration_seconds=DURATION_SECONDS,
            output_path=str(OUTPUT_DIR),
        )
        result = {
            "video_path": gen_result.final_video_url,
            "duration": gen_result.duration_seconds,
            "identity_stability": gen_result.identity_stability_score,
            "temporal_consistency": gen_result.temporal_consistency_score,
            "generation_time": gen_result.total_generation_time,
            "num_segments": gen_result.num_segments,
            "autoregressive_used": gen_result.metadata.get("autoregressive_used", False),
        }
        motion_path = getattr(engine, "_motion_reference_video_path", None)
        _test_stats["motion_reference_path"] = motion_path
        if motion_path:
            logger.info("[TEST] Downloaded / cached motion video path: %s", motion_path)
            if Path(motion_path).exists():
                logger.info(
                    "[TEST] Motion file size: %.2f MB",
                    Path(motion_path).stat().st_size / 1024 / 1024,
                )

        local_out = await _copy_to_final_output(gen_result.final_video_url)
        result["final_output"] = str(local_out)
        logger.info("Fase 2 completata in %.2fs", time.time() - start)
        return result

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        _test_stats["generation_errors"].append(err)
        logger.error("ERRORE Fase 2: %s", err)
        import traceback
        logger.error(traceback.format_exc())
        return None


async def _copy_to_final_output(source: str) -> Path:
    """Copy engine output to outputs/final_v2v_test.mp4."""
    src = Path(source)
    if FINAL_OUTPUT.exists():
        FINAL_OUTPUT.unlink()
    if src.exists():
        shutil.copy2(src, FINAL_OUTPUT)
        logger.info("[OK] Output copiato in %s", FINAL_OUTPUT.resolve())
        return FINAL_OUTPUT.resolve()
    if str(source).startswith(("http://", "https://")):
        import httpx

        logger.info("Download URL → %s", FINAL_OUTPUT)
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(source)
            resp.raise_for_status()
            FINAL_OUTPUT.write_bytes(resp.content)
        logger.info("[OK] Download completato: %s", FINAL_OUTPUT.resolve())
        return FINAL_OUTPUT.resolve()
    raise FileNotFoundError(f"Output video non trovato: {source}")


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 3 — TEARDOWN GDPR")
    logger.info("=" * 70)
    if result:
        logger.info("Final output: %s", result.get("final_output", "N/A"))
    if TEMP_IDENTITY_DIR.exists():
        safe_rmtree(TEMP_IDENTITY_DIR)
        logger.info("[OK] tmpfs identity frames rimossi")


def print_final_report(result: Optional[Dict[str, Any]], total_elapsed: float) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info("REPORT — FASE 3.9 V2V REPLICATE")
    logger.info("=" * 70)
    logger.info("Subject dir:          %s", _test_stats["subject_dir"])
    logger.info("Full-body image:      %s", _test_stats["full_body_image"])
    logger.info("Video / foto input:   %d / %d", _test_stats["videos_found"], _test_stats["photos_found"])
    logger.info("Frame tmpfs estratti: %d", _test_stats["frames_extracted"])
    logger.info("Motion reference:     %s", _test_stats["motion_reference_path"] or "N/A")
    logger.info("V2V router logged:    %s", _test_stats["v2v_mode_logged"])
    logger.info("Replicate model:      %s", _test_stats["replicate_model"] or "N/A")
    logger.info("Cache hash:           %s", _test_stats["cache_hash"])
    if _test_stats["identity_cache_miss_s"] is not None:
        logger.info("Identity MISS:        %.3fs", _test_stats["identity_cache_miss_s"])
    if _test_stats["identity_cache_hit_s"] is not None:
        logger.info("Identity HIT:         %.3fs", _test_stats["identity_cache_hit_s"])
    logger.info("Tempo totale:         %.2fs", total_elapsed)

    if _test_stats["generation_errors"]:
        for err in _test_stats["generation_errors"]:
            logger.info("Errore: %s", err)

    if result and FINAL_OUTPUT.exists():
        size_mb = FINAL_OUTPUT.stat().st_size / 1024 / 1024
        logger.info("Esito:                PASSED")
        logger.info("Output:               %s (%.2f MB)", FINAL_OUTPUT.resolve(), size_mb)
        logger.info("Segmenti:             %s", result.get("num_segments", "N/A"))
        logger.info("Autoregressivo:       %s", result.get("autoregressive_used", False))
        logger.info("Generation API time:  %.2fs", result.get("generation_time", 0))
    else:
        logger.info("Esito:                FAILED")
        logger.info("Output:               N/A")


async def main() -> bool:
    logger.info("")
    logger.info("#" * 70)
    logger.info("# FASE 3.9 — V2V FULL-BODY INTEGRATION TEST (REPLICATE)")
    logger.info("#" * 70)

    _attach_debug_capture()

    total_start = time.time()

    try:
        subject_dir, full_body_image = resolve_full_body_subject()
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        print_final_report(None, time.time() - total_start)
        return False

    subjects_payload = {"subject_1": str(subject_dir.resolve())}

    if not await setup_phase(subject_dir):
        print_final_report(None, time.time() - total_start)
        return False

    if not await fase1_ingestione_biometrica(subject_dir):
        await fase3_teardown(None)
        print_final_report(None, time.time() - total_start)
        return False

    await benchmark_identity_cache(subjects_payload)

    result = await fase2_orchestrazione_v2v(subjects_payload, full_body_image)
    await fase3_teardown(result)

    total_elapsed = time.time() - total_start
    print_final_report(result, total_elapsed)
    return result is not None and FINAL_OUTPUT.exists() and FINAL_OUTPUT.stat().st_size > 0


def replicate_token_ready() -> bool:
    """Run pipeline only when REPLICATE_API_TOKEN is configured."""
    rep = os.getenv("REPLICATE_API_TOKEN", "")
    return not _is_placeholder_token(rep, ("", "your_replicate_api_token_here"))


if __name__ == "__main__":
    if not replicate_token_ready():
        logger.warning(
            "Script pronto — REPLICATE_API_TOKEN non configurato in .env. "
            "Imposta il token, poi: python test_v2v_replicate.py"
        )
        sys.exit(0)

    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.warning("Test interrotto (Ctrl+C)")
        if TEMP_IDENTITY_DIR.exists():
            safe_rmtree(TEMP_IDENTITY_DIR)
        sys.exit(130)
    except Exception as exc:
        logger.error("ERRORE CRITICO: %s: %s", type(exc).__name__, exc)
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
