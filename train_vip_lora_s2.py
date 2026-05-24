#!/usr/bin/env python3
"""
Train VIP Flux LoRA for Soggetto 2 on Replicate (ostris/flux-dev-lora-trainer).

Input: inputs/VIP_Dataset_Soggetto2/
Output: models/lora_soggetto2_vip.json + models/lora_soggetto2_vip_url.txt
Registry: backs up legacy metadata then promotes VIP to active soggetto_2 slot.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from gender_detector import (
    gender_from_metadata_or_file,
    gender_portrait_label,
    load_gender_json,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_ROOT / "models"
DATASET_DIR = PROJECT_ROOT / "inputs" / "VIP_Dataset_Soggetto2"
GENDER_JSON = DATASET_DIR / "gender.json"
VIP_JSON = MODELS_DIR / "lora_soggetto2_vip.json"
VIP_URL = MODELS_DIR / "lora_soggetto2_vip_url.txt"
LEGACY_JSON = MODELS_DIR / "lora_soggetto2.json"
LEGACY_URL = MODELS_DIR / "lora_soggetto2_url.txt"
LEGACY_BACKUP_JSON = MODELS_DIR / "lora_soggetto2_legacy.json"
LEGACY_BACKUP_URL = MODELS_DIR / "lora_soggetto2_legacy_url.txt"

TRIGGER_WORD = "soggetto_due_vip"
DESTINATION = "umbepapa-collab/flux-lora-soggetto2-vip"
FALLBACK_DESTINATION = "umbepapa-collab/flux-lora-soggetto2"
STEPS = 1200
LORA_RANK = 32
AUTOCAPTION = True
POLL_INTERVAL = 30


def _load_subject_gender() -> str:
    cached = load_gender_json(GENDER_JSON)
    if cached and cached.gender in ("male", "female"):
        return cached.gender
    return gender_from_metadata_or_file(gender_json_path=GENDER_JSON)


def _caption_suffix_for_gender(gender: str) -> str:
    label = gender_portrait_label(gender)  # type: ignore[arg-type]
    return f"{label}, photorealistic, sharp focus"


def _check_trainer_schema() -> dict[str, bool]:
    """Probe Replicate trainer version schema for optional inputs."""
    supported = {"lora_rank": False, "autocaption": False}
    try:
        import replicate

        token = __import__("os").getenv("REPLICATE_API_TOKEN", "").strip()
        if not token:
            return supported
        client = replicate.Client(api_token=token)
        model = client.models.get("ostris", "flux-dev-lora-trainer")
        latest = getattr(model, "latest_version", None)
        openapi = getattr(latest, "openapi_schema", None) if latest else None
        if not openapi:
            return supported
        props = (
            openapi.get("components", {})
            .get("schemas", {})
            .get("Input", {})
            .get("properties", {})
        )
        supported["lora_rank"] = "lora_rank" in props
        supported["autocaption"] = "autocaption" in props
        logger.info(
            "[SCHEMA] Trainer inputs: lora_rank=%s autocaption=%s",
            supported["lora_rank"],
            supported["autocaption"],
        )
    except Exception as exc:
        logger.warning("[SCHEMA] Could not probe trainer schema: %s", exc)
    return supported


def ensure_replicate_destination(destination: str) -> str:
    """Create destination model if missing; return effective destination path."""
    if "/" not in destination:
        raise ValueError(f"Invalid destination: {destination}")
    owner, name = destination.split("/", 1)
    import os
    import replicate

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    client = replicate.Client(api_token=token)
    try:
        client.models.get(owner, name)
        logger.info("[DEST] Exists: %s", destination)
        return destination
    except Exception:
        pass
    logger.info("[DEST] Creating Replicate model: %s", destination)
    try:
        client.models.create(
            owner=owner,
            name=name,
            visibility="private",
            hardware="cpu",
            description="Flux VIP LoRA for soggetto_due_vip",
        )
        return destination
    except Exception as exc:
        err = str(exc).lower()
        if "model limit" in err or "403" in err:
            logger.warning(
                "[DEST] Cannot create %s (%s) — falling back to %s",
                destination,
                exc,
                FALLBACK_DESTINATION,
            )
            client.models.get(*FALLBACK_DESTINATION.split("/", 1))
            return FALLBACK_DESTINATION
        raise


def save_url_sidecar(metadata: Dict[str, Any], url_path: Path) -> None:
    from train_lora_replicate import _extract_weights_url

    url = metadata.get("weights_url") or _extract_weights_url(
        type("T", (), {"output": metadata.get("output")})()
    )
    if not url and isinstance(metadata.get("output"), dict):
        url = metadata["output"].get("weights")
    if url:
        url_path.write_text(str(url).strip() + "\n", encoding="utf-8")
        logger.info("[TRAINING] URL sidecar: %s", url_path)


def update_registry(vip_metadata: Dict[str, Any]) -> None:
    """Backup legacy soggetto2 metadata, promote VIP to active registry files."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if LEGACY_JSON.is_file() and not LEGACY_BACKUP_JSON.is_file():
        shutil.copy2(LEGACY_JSON, LEGACY_BACKUP_JSON)
        logger.info("[REGISTRY] Backed up %s -> %s", LEGACY_JSON.name, LEGACY_BACKUP_JSON.name)
    if LEGACY_URL.is_file() and not LEGACY_BACKUP_URL.is_file():
        shutil.copy2(LEGACY_URL, LEGACY_BACKUP_URL)
        logger.info("[REGISTRY] Backed up %s -> %s", LEGACY_URL.name, LEGACY_BACKUP_URL.name)

    LEGACY_JSON.write_text(
        json.dumps(vip_metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    save_url_sidecar(vip_metadata, LEGACY_URL)
    logger.info("[REGISTRY] Promoted VIP metadata -> %s (legacy backed up)", LEGACY_JSON.name)

    try:
        import update_lora_registry

        update_lora_registry.main()
        logger.info("[REGISTRY] custom_weights_handler.py synced")
    except Exception as exc:
        logger.warning("[REGISTRY] update_lora_registry skipped: %s", exc)


def start_vip_training(
    zip_path: Path,
    *,
    destination: str,
    schema: dict[str, bool],
) -> Any:
    import replicate
    from train_lora_replicate import _require_replicate_token, _resolve_trainer_version

    token = _require_replicate_token()
    client = replicate.Client(api_token=token)
    version = _resolve_trainer_version(client, None)

    training_input: Dict[str, Any] = {
        "input_images": open(zip_path, "rb"),
        "trigger_word": TRIGGER_WORD,
        "steps": STEPS,
        "lora_rank": LORA_RANK,
        "autocaption": AUTOCAPTION,
    }
    if schema.get("lora_rank"):
        logger.info("[TRAINING] schema confirms lora_rank")
    else:
        logger.warning("[TRAINING] lora_rank not in schema probe — passing anyway")
    if schema.get("autocaption"):
        logger.info("[TRAINING] schema confirms autocaption=%s", AUTOCAPTION)
    else:
        logger.warning("[TRAINING] autocaption not in schema probe — passing anyway")
    logger.info("[TRAINING] lora_rank=%d autocaption=%s", LORA_RANK, AUTOCAPTION)

    logger.info(
        "[TRAINING] Starting VIP LoRA destination=%s steps=%d trigger=%s",
        destination,
        STEPS,
        TRIGGER_WORD,
    )
    try:
        training = client.trainings.create(
            version=version,
            input=training_input,
            destination=destination,
        )
    finally:
        fh = training_input.get("input_images")
        if hasattr(fh, "close"):
            fh.close()

    logger.info(
        "[TRAINING] Job id=%s url=https://replicate.com/p/%s",
        training.id,
        training.id,
    )
    return training


def run_training(*, force: bool = False) -> Dict[str, Any]:
    from train_lora_replicate import (
        build_metadata,
        poll_training,
        prepare_dataset,
        save_metadata,
    )

    if not DATASET_DIR.is_dir():
        raise FileNotFoundError(f"VIP dataset missing: {DATASET_DIR}")

    images = list(DATASET_DIR.glob("*.jpg"))
    if len(images) < 5:
        raise RuntimeError(f"VIP dataset too small: {len(images)} images in {DATASET_DIR}")

    if VIP_JSON.is_file() and not force:
        try:
            existing = json.loads(VIP_JSON.read_text(encoding="utf-8"))
            if (
                str(existing.get("status", "")).lower() == "succeeded"
                and str(existing.get("trigger_word", "")) == TRIGGER_WORD
            ):
                logger.info("[TRAINING] Skip: existing VIP training succeeded (%s)", VIP_JSON)
                return existing
        except (OSError, json.JSONDecodeError):
            pass

    schema = _check_trainer_schema()
    effective_dest = ensure_replicate_destination(DESTINATION)

    subject_gender = _load_subject_gender()
    caption_suffix = _caption_suffix_for_gender(subject_gender)
    logger.info("[TRAINING] subject_gender=%s caption_suffix=%s", subject_gender, caption_suffix)

    zip_path = prepare_dataset(
        DATASET_DIR,
        trigger_word=TRIGGER_WORD,
        caption_suffix=caption_suffix,
        recursive=False,
        dataset_mode="combined",
    )

    training = start_vip_training(zip_path, destination=effective_dest, schema=schema)
    training = poll_training(training.id, poll_interval=POLL_INTERVAL)

    metadata = build_metadata(
        training,
        trigger_word=TRIGGER_WORD,
        destination=effective_dest,
        steps=STEPS,
        dataset_folder=DATASET_DIR,
    )
    metadata["requested_destination"] = DESTINATION
    metadata["lora_rank"] = LORA_RANK if schema.get("lora_rank") else None
    metadata["autocaption"] = AUTOCAPTION if schema.get("autocaption") else False
    metadata["vip"] = True
    metadata["subject_gender"] = subject_gender
    metadata["legacy_backup"] = str(LEGACY_BACKUP_JSON.name)

    save_metadata(metadata, VIP_JSON)
    save_url_sidecar(metadata, VIP_URL)
    update_registry(metadata)

    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train VIP LoRA for Soggetto 2")
    parser.add_argument("--force", action="store_true", help="Retrain even if VIP metadata exists")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata = run_training(force=args.force)
    except Exception as exc:
        logger.error("VIP training failed: %s", exc)
        return 1

    print("\n=== train_vip_lora_s2 RESULT ===")
    print(f"Training ID: {metadata.get('training_id')}")
    print(f"Status: {metadata.get('status')}")
    print(f"Weights URL: {metadata.get('weights_url')}")
    print(f"Metadata: {VIP_JSON}")
    print(f"URL sidecar: {VIP_URL}")
    return 0 if str(metadata.get("status", "")).lower() == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
