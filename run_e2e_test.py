#!/usr/bin/env python3
"""
End-to-End Kinematic Integration Test
=====================================

Valida l'intero flusso architetturale:
1. Ingestione biometrica (frame extraction da video + foto in inputs/Soggetto 1/)
2. Orchestrazione video (core_engine + dynamic retriever opzionale)
3. Teardown GDPR-compliant

Usage:
    python run_e2e_test.py
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
import sys
from typing import Dict, Any, List, Optional, Tuple

from dotenv import load_dotenv

from frame_extractor import extract_and_save_frames_for_identity
from generation_progress import estimate_pipeline_seconds, format_eta_range
from core_engine import CoreEngine, CoreEngineConfig, QualityPreset, generate_high_fidelity_video

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('e2e_test.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

INPUT_BASE = Path(__file__).resolve().parent / "inputs"
INPUT_DIR = INPUT_BASE / "Soggetto 1"
TEMP_FACES_DIR = Path("tmpfs/test_faces")
OUTPUT_DIR = Path("outputs/e2e_test")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def safe_rmtree(path: Path, max_retries: int = 3) -> None:
    """Remove directory tree with retry for Windows file locks."""
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


E2E_FIRST_FRAME_PROMPT = (
    "Extreme close-up macro portrait of the athlete's face looking concentrated before a dive, "
    "sweat on forehead, blurred background, cinematic lighting, photorealistic"
)
E2E_VIDEO_PROMPT = (
    "Cinematic sports broadcast tracking shot. Camera dynamically pulls back from close-up to "
    "reveal full body leaping off platform, executing a flawless triple somersault dive into "
    "the bright blue pool. Photorealistic, 8k resolution, dynamic lighting, professional TV "
    "broadcast style, ultra-detailed."
)
E2E_MOTION_KEYWORD = "olympic diver concentration and platform dive"
E2E_DURATION_SECONDS = 10
E2E_SEGMENT_DURATION = 5.0
E2E_ENABLE_AUTOREGRESSIVE = True
FRAMES_PER_VIDEO = 5
LAPLACIAN_THRESHOLD = 30.0

# Stats for final report
_test_stats: Dict[str, Any] = {
    "videos_found": 0,
    "photos_found": 0,
    "frames_extracted": 0,
    "photos_copied": 0,
    "motion_reference_path": None,
}


def discover_input_media(input_dir: Path) -> Tuple[List[Path], List[Path]]:
    """Scan subject directory recursively for videos and photos."""
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
    """Copy photo references into the unified reference directory."""
    copied = 0
    for photo in photos:
        prefix = _sanitize_prefix(photo.parent.name) if photo.parent != INPUT_DIR else "root"
        dest_name = f"photo_{prefix}_{photo.stem}{photo.suffix.lower()}"
        dest_path = output_dir / dest_name

        if dest_path.exists() and dest_path.stat().st_size == photo.stat().st_size:
            logger.info(f"  Foto già presente: {dest_name}")
            copied += 1
            continue

        shutil.copy2(photo, dest_path)
        logger.info(f"  Copiata foto: {photo.name} -> {dest_name}")
        copied += 1

    return copied


def check_prerequisites() -> bool:
    """Verify FAL_KEY, fal-client, and FFmpeg availability."""
    logger.info("Verifica prerequisiti...")

    fal_key = os.getenv("FAL_KEY", "")
    if not fal_key or fal_key.strip() in ("", "your_fal_api_key_here"):
        logger.warning("FAL_KEY non configurata o placeholder in .env — la Fase 2 probabilmente fallirà")
    else:
        logger.info("✓ FAL_KEY configurata")

    try:
        import fal_client  # noqa: F401
        logger.info("✓ fal-client installato")
    except ImportError:
        logger.error("fal-client mancante — eseguire: pip install -r requirements.txt")
        return False

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    if ffmpeg_ok:
        logger.info("✓ FFmpeg disponibile")
    else:
        logger.warning("FFmpeg non trovato nel PATH — alcune operazioni video potrebbero fallire")

    return True


async def setup_phase() -> bool:
    """Prepare directories and verify input material exists."""
    logger.info("=" * 70)
    logger.info("FASE SETUP - Preparazione ambiente test")
    logger.info("=" * 70)

    start_time = time.time()

    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    videos, photos = discover_input_media(INPUT_DIR)
    _test_stats["videos_found"] = len(videos)
    _test_stats["photos_found"] = len(photos)

    logger.info(f"Scansione {INPUT_DIR}/:")
    logger.info(f"  Video trovati: {len(videos)}")
    for v in videos:
        logger.info(f"    - {v.relative_to(INPUT_DIR)} ({v.stat().st_size / 1024 / 1024:.2f} MB)")
    logger.info(f"  Foto trovate: {len(photos)}")
    for p in photos:
        logger.info(f"    - {p.relative_to(INPUT_DIR)}")

    if not videos and not photos:
        logger.error("ERRORE: Nessun video o foto trovato in %s", INPUT_DIR)
        logger.info("Caricare materiale in inputs/Soggetto 1/ (*.jpg, *.avi)")
        return False

    if not check_prerequisites():
        return False

    if TEMP_FACES_DIR.exists():
        safe_rmtree(TEMP_FACES_DIR)
    TEMP_FACES_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"✓ Directory reference creata: {TEMP_FACES_DIR.resolve()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"✓ Directory output creata: {OUTPUT_DIR}")

    elapsed = time.time() - start_time
    logger.info(f"Setup completato in {elapsed:.2f}s")
    return True


async def fase1_ingestione_biometrica() -> bool:
    """Extract frames from all videos and copy all photos into reference dir."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 1 - INGESTIONE BIOMETRICA (SOLO SOGGETTO 1)")
    logger.info("=" * 70)

    start_time = time.time()
    videos, photos = discover_input_media(INPUT_DIR)
    total_frames = 0

    try:
        for video in videos:
            prefix = _sanitize_prefix(video.stem)
            logger.info(f"Estrazione frame da: {video.relative_to(INPUT_DIR)}")
            logger.info(f"  Prefix: {prefix} | Target: {FRAMES_PER_VIDEO} frame")

            try:
                frame_data = extract_and_save_frames_for_identity(
                    video_path=str(video.resolve()),
                    output_dir=get_reference_faces_dir(),
                    num_frames=FRAMES_PER_VIDEO,
                    laplacian_threshold=LAPLACIAN_THRESHOLD,
                    filename_prefix=prefix,
                )
            except ValueError as e:
                logger.warning(f"  ⚠ Estrazione fallita per {video.name}: {e}")
                continue

            total_frames += len(frame_data)
            logger.info(f"  ✓ Estratti {len(frame_data)} frame da {video.name}")
            for i, data in enumerate(frame_data, 1):
                angles = data["angles"]
                logger.info(
                    f"    Frame {i}: {Path(data['path']).name} "
                    f"(Yaw={angles[0]:.1f}°, Pitch={angles[1]:.1f}°, Roll={angles[2]:.1f}°)"
                )

        if photos:
            logger.info(f"Copia {len(photos)} foto di riferimento in {TEMP_FACES_DIR}")
            copied = copy_photos_to_reference(photos, TEMP_FACES_DIR)
            _test_stats["photos_copied"] = copied
            logger.info(f"✓ {copied} foto disponibili come reference aggiuntive")

        reference_files = [
            p for p in TEMP_FACES_DIR.iterdir() if p.is_file()
        ]
        _test_stats["frames_extracted"] = total_frames

        if not reference_files:
            logger.error("ERRORE: Nessun file di reference generato")
            return False

        logger.info("")
        logger.info(f"Reference set unificato (subject_1): {len(reference_files)} file totali")
        logger.info(f"  - Frame estratti da video: {total_frames}")
        logger.info(f"  - Foto copiate: {_test_stats['photos_copied']}")

        elapsed = time.time() - start_time
        logger.info(f"Fase 1 completata in {elapsed:.2f}s")
        return True

    except FileNotFoundError as e:
        logger.error(f"ERRORE Fase 1: File non trovato - {e}")
        return False
    except ValueError as e:
        logger.error(f"ERRORE Fase 1: Video non valido o corrotto - {e}")
        return False
    except Exception as e:
        logger.error(f"ERRORE Fase 1: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def _resolve_motion_reference(motion_keyword: str) -> Optional[str]:
    """Try to retrieve motion reference via Dynamic Retriever (optional)."""
    try:
        from dynamic_retriever import retrieve_motion_reference

        logger.info(f"Dynamic Retriever: ricerca motion reference per '{motion_keyword}'")
        motion_path = await retrieve_motion_reference(motion_keyword, max_duration=E2E_DURATION_SECONDS)
        logger.info(f"✓ Motion reference: {motion_path}")
        return motion_path
    except Exception as e:
        logger.warning(f"Dynamic Retriever non disponibile o fallito: {e}")
        logger.info("Proseguo senza motion reference (solo prompt testuale)")
        return None


async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    """Trigger orchestrator with unified reference set."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 2 - ORCHESTRAZIONE VIDEO")
    logger.info("=" * 70)

    start_time = time.time()

    try:
        subjects_payload = {"subject_1": get_reference_faces_dir()}

        logger.info("Parametri generazione:")
        logger.info(f"  First-frame prompt: {E2E_FIRST_FRAME_PROMPT}")
        logger.info(f"  Video prompt: {E2E_VIDEO_PROMPT}")
        logger.info(f"  Motion keyword: {E2E_MOTION_KEYWORD}")
        logger.info(f"  Duration: {E2E_DURATION_SECONDS}s")
        logger.info(f"  Subjects payload: {subjects_payload}")
        logger.info("")

        controlnet_map_path = await _resolve_motion_reference(E2E_MOTION_KEYWORD)
        _test_stats["motion_reference_path"] = controlnet_map_path

        logger.info("Config Core Engine (autoregressivo):")
        logger.info(f"  enable_autoregressive: {E2E_ENABLE_AUTOREGRESSIVE}")
        logger.info(f"  segment_duration: {E2E_SEGMENT_DURATION}s")
        expected_segments = int(E2E_DURATION_SECONDS / E2E_SEGMENT_DURATION)
        if E2E_DURATION_SECONDS > E2E_SEGMENT_DURATION:
            expected_segments = max(2, -(-E2E_DURATION_SECONDS // E2E_SEGMENT_DURATION))
        logger.info(f"  segmenti attesi (~): {expected_segments}")
        eta_low, eta_high = estimate_pipeline_seconds(
            E2E_DURATION_SECONDS,
            draft_mode=False,
            autoregressive=E2E_ENABLE_AUTOREGRESSIVE,
            segment_duration=E2E_SEGMENT_DURATION,
        )
        logger.info(f"  Tempo stimato totale: {format_eta_range(eta_low, eta_high)}")
        logger.info("")
        logger.info("Avvio generazione video...")
        logger.info("(Countdown [ETA] visibile ogni ~12s durante Flux/I2V)")
        logger.info("")

        engine_config = CoreEngineConfig(
            reference_faces_dir=get_reference_faces_dir(),
            num_angles=5,
            duration_seconds=E2E_DURATION_SECONDS,
            output_path=str(OUTPUT_DIR),
            controlnet_map_path=controlnet_map_path,
            quality_preset=QualityPreset.HIGH,
            enable_autoregressive=E2E_ENABLE_AUTOREGRESSIVE,
            segment_duration=E2E_SEGMENT_DURATION,
            identity_adapter_strength=0.95,
        )
        engine = CoreEngine(config=engine_config)
        gen_result = await engine.generate_high_fidelity_video(
            reference_faces_dir=get_reference_faces_dir(),
            prompt=E2E_VIDEO_PROMPT,
            first_frame_prompt=E2E_FIRST_FRAME_PROMPT,
            controlnet_map_path=controlnet_map_path,
            duration_seconds=E2E_DURATION_SECONDS,
            output_path=str(OUTPUT_DIR),
        )
        result = {
            "video_url": gen_result.final_video_url,
            "duration": gen_result.duration_seconds,
            "identity_stability": gen_result.identity_stability_score,
            "temporal_consistency": gen_result.temporal_consistency_score,
            "generation_time": gen_result.total_generation_time,
            "num_segments": gen_result.num_segments,
            "autoregressive_used": gen_result.metadata.get("autoregressive_used", False),
        }

        logger.info("")
        logger.info("V Video generato con successo!")
        logger.info("")
        logger.info("Risultati generazione:")
        logger.info(f"  Video URL/Path: {result.get('video_url', 'N/A')}")
        logger.info(f"  Duration: {result.get('duration', 0)}s")
        logger.info(f"  Num segments: {result.get('num_segments', 'N/A')}")
        logger.info(f"  Autoregressive: {result.get('autoregressive_used', False)}")
        logger.info(f"  Identity stability: {result.get('identity_stability', 0) * 100:.1f}%")
        logger.info(f"  Temporal consistency: {result.get('temporal_consistency', 0) * 100:.1f}%")
        logger.info(f"  Generation time: {result.get('generation_time', 0):.2f}s")

        elapsed = time.time() - start_time
        logger.info(f"Fase 2 completata in {elapsed:.2f}s")
        return result

    except RuntimeError as e:
        logger.error(f"ERRORE Fase 2: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
    except Exception as e:
        logger.error(f"ERRORE Fase 2: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> bool:
    """GDPR-compliant cleanup."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("FASE 3 - TEARDOWN GDPR-COMPLIANT")
    logger.info("=" * 70)

    start_time = time.time()

    try:
        if result:
            logger.info("Risultato finale:")
            logger.info(f"  Video URL/Path: {result.get('video_url', 'N/A')}")
            logger.info(f"  Duration: {result.get('duration', 0)}s")

        logger.info(f"Rimozione dati biometrici: {TEMP_FACES_DIR}")

        if TEMP_FACES_DIR.exists():
            num_files = len(list(TEMP_FACES_DIR.glob("*")))
            logger.info(f"  File da rimuovere: {num_files}")
            safe_rmtree(TEMP_FACES_DIR)
            logger.info("✓ Dati biometrici rimossi (GDPR compliance)")

        elapsed = time.time() - start_time
        logger.info(f"Fase 3 completata in {elapsed:.2f}s")
        return True

    except Exception as e:
        logger.error(f"ERRORE Fase 3: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def print_final_report(result: Optional[Dict[str, Any]], total_elapsed: float) -> None:
    """Print structured E2E report."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("REPORT E2E")
    logger.info("=" * 70)
    logger.info(f"Video processati:     {_test_stats['videos_found']}")
    logger.info(f"Foto processate:      {_test_stats['photos_found']} (copiate: {_test_stats['photos_copied']})")
    logger.info(f"Frame estratti:       {_test_stats['frames_extracted']}")
    logger.info(f"Motion reference:     {_test_stats['motion_reference_path'] or 'N/A'}")
    logger.info(f"Tempo totale:         {total_elapsed:.2f}s")

    if result:
        video_path = result.get("video_url", "N/A")
        logger.info(f"Esito:                PASSED")
        logger.info(f"Segmenti:             {result.get('num_segments', 'N/A')}")
        logger.info(f"Autoregressivo:       {result.get('autoregressive_used', False)}")
        logger.info(f"Output video:         {video_path}")
    else:
        logger.info("Esito:                FAILED")
        logger.info("Output video:         N/A")


async def main() -> bool:
    """Main E2E test runner."""
    logger.info("")
    logger.info("#" * 70)
    logger.info("# END-TO-END KINEMATIC INTEGRATION TEST")
    logger.info("#" * 70)
    logger.info("")

    total_start = time.time()

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

    logger.info("")
    if result:
        logger.info("✓✓✓ Test PASSED ✓✓✓")
        return True

    logger.error("✗✗✗ Test FAILED ✗✗✗")
    return False


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.warning("Test interrotto dall'utente (Ctrl+C)")
        if TEMP_FACES_DIR.exists():
            safe_rmtree(TEMP_FACES_DIR)
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERRORE CRITICO: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
