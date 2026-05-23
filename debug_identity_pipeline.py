#!/usr/bin/env python3
"""
Debug script for the identity pipeline (Phase 3.6+).

Steps:
1. Load reference media from inputs/Soggetto 1/
2. Extract InsightFace embeddings and report stability score
3. Generate ONLY the PuLID first frame (no I2V)
4. Print full Fal payload and save output URL for visual inspection

Usage:
    python debug_identity_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

INPUT_BASE = Path(__file__).resolve().parent / "inputs"
SUBJECT_INPUT_DIR = INPUT_BASE / "Soggetto 1"
TEMP_FACES_DIR = Path("tmpfs/debug_identity_faces")
OUTPUT_DIR = Path("outputs/debug_identity")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

FIRST_FRAME_PROMPT = (
    "Extreme close-up macro portrait of the subject's face, concentrated expression, "
    "blurred background, cinematic lighting, photorealistic"
)


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


def prepare_reference_faces() -> str:
    from frame_extractor import extract_and_save_frames_for_identity

    if TEMP_FACES_DIR.exists():
        shutil.rmtree(TEMP_FACES_DIR, ignore_errors=True)
    TEMP_FACES_DIR.mkdir(parents=True, exist_ok=True)

    videos, photos = discover_input_media(SUBJECT_INPUT_DIR)
    logger.info("Discovered %d video(s), %d photo(s)", len(videos), len(photos))

    for video in videos:
        extract_and_save_frames_for_identity(
            str(video),
            str(TEMP_FACES_DIR),
            num_frames=5,
            laplacian_threshold=30.0,
        )

    for i, photo in enumerate(photos):
        dest = TEMP_FACES_DIR / f"photo_{i:03d}{photo.suffix.lower()}"
        shutil.copy2(photo, dest)

    return str(TEMP_FACES_DIR.resolve())


def run_identity_extraction(faces_dir: str) -> Dict[str, Any]:
    from identity_cache import compute_folder_hash, invalidate_cache, load_cached_identity
    from identity_lock_3d import (
        MIN_IDENTITY_STABILITY,
        MultiAngleIdentityLock,
        insightface_available,
        rank_reference_face_images,
        score_reference_image,
    )

    report: Dict[str, Any] = {
        "faces_dir": faces_dir,
        "insightface_available": insightface_available(),
        "ranked_faces": [],
        "stability": None,
        "stability_pct": None,
        "below_threshold": None,
    }

    ranked = rank_reference_face_images(faces_dir, top_n=5, require_face=True)
    for path in ranked:
        score = score_reference_image(str(path))
        report["ranked_faces"].append(
            {
                "path": str(path),
                "sharpness": score.sharpness,
                "face_area": score.face_area,
                "confidence": score.confidence,
            }
        )

    cache_hash = compute_folder_hash(faces_dir)
    cached = load_cached_identity(cache_hash)
    if cached:
        _vec, stability, meta = cached
        if stability < MIN_IDENTITY_STABILITY:
            logger.warning("Invalidating low-stability cache (%.1f%%)", stability * 100)
            invalidate_cache(cache_hash)
        else:
            report["stability"] = stability
            report["stability_pct"] = stability * 100
            report["below_threshold"] = stability < MIN_IDENTITY_STABILITY
            report["cache_hit"] = True
            report["cache_metadata"] = meta
            return report

    locker = MultiAngleIdentityLock(
        reference_faces_dir=faces_dir,
        num_angles=5,
        min_stability=MIN_IDENTITY_STABILITY,
        fail_on_low_stability=False,
    )
    locker.extract_multi_angle_embeddings()
    locker.create_super_vector()
    stability = locker.get_identity_stability_score()
    report["stability"] = stability
    report["stability_pct"] = stability * 100
    report["below_threshold"] = stability < MIN_IDENTITY_STABILITY
    report["cache_hit"] = False
    report["num_embeddings"] = len(locker.embeddings)
    return report


async def generate_pulid_first_frame(faces_dir: str) -> Dict[str, Any]:
    from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
    from provider_adapters import log_payload_debug

    config = CoreEngineConfig(
        subjects_payload={"subject_1": faces_dir},
        quality_preset=QualityPreset.STANDARD,
        require_reference_face=True,
        identity_adapter_strength=0.95,
        output_path=str(OUTPUT_DIR),
    )
    engine = CoreEngine(config=config)

    identity_vectors, stability_scores = await engine._extract_identity(
        {"subject_1": faces_dir}
    )

    prompts = {
        "prompt": "Cinematic tracking shot, photorealistic",
        "first_frame_prompt": FIRST_FRAME_PROMPT,
        "negative_prompt": "deformed, bad anatomy, wrong face",
    }

    from prompt_sanitizer import sanitize_prompt_dict

    prompts = sanitize_prompt_dict(prompts)

    reference_url = await engine._upload_face_reference(faces_dir)
    full_body_path = engine._resolve_full_body_reference({"subject_1": faces_dir})
    full_body_url: str | None = None
    if full_body_path:
        full_body_url = await engine._upload_full_body_reference(full_body_path)
    else:
        full_body_url = reference_url
        logger.warning("No full-body reference — using face crop URL")

    quality = engine._get_quality_params()
    payload: Dict[str, Any] = {
        "prompt": prompts["first_frame_prompt"],
        "reference_image_url": reference_url,
        "id_weight": config.identity_adapter_strength,
        "image_size": quality["image_size"],
        "num_inference_steps": quality["num_inference_steps"],
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": quality["guidance_scale"],
        "negative_prompt": prompts.get("negative_prompt", ""),
    }
    if full_body_url:
        payload["image_prompt"] = full_body_url

    result_info: Dict[str, Any] = {
        "endpoint": "fal-ai/flux-pulid",
        "payload": {k: v for k, v in payload.items()},
        "face_reference_url": reference_url,
        "full_body_reference_url": full_body_url,
        "reference_face_url": reference_url,
        "stability_scores": {k: float(v) for k, v in stability_scores.items()},
    }

    logger.info(
        "[CHARACTER] face_ref=%s full_body_ref=%s mode=debug",
        reference_url[:80],
        (full_body_url or "")[:80],
    )

    log_payload_debug("fal-ai/flux-pulid", payload)
    logger.info("Full PuLID payload:\n%s", json.dumps(result_info["payload"], indent=2))

    if not os.getenv("FAL_KEY"):
        logger.error("FAL_KEY not set — skipping PuLID API call")
        result_info["skipped"] = "FAL_KEY missing"
        return result_info

    first_frame_url = await engine._generate_first_frame(
        prompts=prompts,
        identity_vectors=identity_vectors,
    )
    result_info["first_frame_url"] = first_frame_url
    result_info["skipped"] = False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "debug_identity_report.json"
    report_path.write_text(json.dumps(result_info, indent=2), encoding="utf-8")
    logger.info("Report saved: %s", report_path)
    return result_info


async def main() -> int:
    logger.info("=" * 70)
    logger.info("DEBUG IDENTITY PIPELINE")
    logger.info("=" * 70)

    if not SUBJECT_INPUT_DIR.exists():
        logger.error("Input directory missing: %s", SUBJECT_INPUT_DIR)
        logger.error("Add photos/videos to inputs/Soggetto 1/ and re-run.")
        return 1

    faces_dir = prepare_reference_faces()
    logger.info("Reference faces dir: %s", faces_dir)

    identity_report = run_identity_extraction(faces_dir)
    logger.info("Identity extraction report:")
    logger.info(json.dumps(identity_report, indent=2))

    if identity_report.get("below_threshold"):
        logger.warning(
            "Stability %.1f%% is below 50%% — reference photos may be inconsistent",
            identity_report.get("stability_pct", 0),
        )

    pulid_report = await generate_pulid_first_frame(faces_dir)
    if pulid_report.get("first_frame_url"):
        logger.info("=" * 70)
        logger.info("PuLID FIRST FRAME URL (inspect visually):")
        logger.info("  %s", pulid_report["first_frame_url"])
        logger.info("=" * 70)
    else:
        logger.warning("No first frame generated — check FAL_KEY and logs above")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
