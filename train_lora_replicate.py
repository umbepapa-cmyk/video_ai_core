#!/usr/bin/env python3
"""
Train a Flux LoRA on Replicate (ostris/flux-dev-lora-trainer) from a local image folder.

Replaces the two-pass face-swap identity path with native LoRA identity injection:
zip curated images + captions, start Replicate training, poll until complete, save metadata.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from dotenv import load_dotenv

from auto_curator import V3_TIER_SUBFOLDERS

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_ROOT / "models"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_CAPTION_SUFFIX = "high quality, cinematic, 4k"
DEFAULT_STEPS = 900
DEFAULT_COMBINED_STEPS = 1200
DEFAULT_LORA_RANK = 32
POLL_INTERVAL_SECONDS = 30
MAX_UPLOAD_IMAGES = 80  # Replicate upload limit for large combined tier zips

# Latest trainer on Replicate (prefix 26dce37a); override via --trainer-version or resolve at runtime.
DEFAULT_TRAINER = "ostris/flux-dev-lora-trainer"
DEFAULT_TRAINER_VERSION = (
    "ostris/flux-dev-lora-trainer:26dce37a"
)


def _require_replicate_token() -> str:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set (add to .env)")
    return token


def _resolve_trainer_version(client: Any, trainer_version: Optional[str]) -> str:
    if trainer_version:
        if ":" in trainer_version:
            return trainer_version
        return f"{DEFAULT_TRAINER}:{trainer_version}"

    try:
        model = client.models.get("ostris", "flux-dev-lora-trainer")
        latest = getattr(model, "latest_version", None)
        if latest and getattr(latest, "id", None):
            version_id = str(latest.id)
            logger.info("[TRAINING] Trainer version risolta via API: %s", version_id[:12])
            return f"{DEFAULT_TRAINER}:{version_id}"
    except Exception as exc:
        logger.warning(
            "[TRAINING] Impossibile risolvere latest version (%s); uso default %s",
            exc,
            DEFAULT_TRAINER_VERSION,
        )

    return DEFAULT_TRAINER_VERSION


def iter_images(folder_path: Path, recursive: bool = False) -> Iterator[Path]:
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Cartella dataset non trovata: {folder_path}")

    pattern = "**/*" if recursive else "*"
    for candidate in sorted(folder_path.glob(pattern)):
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
            yield candidate


def _default_caption(trigger_word: str, caption_suffix: str) -> str:
    suffix = caption_suffix.strip()
    if suffix:
        return f"{trigger_word}, {suffix}"
    return trigger_word


def prepare_dataset(
    folder_path: Path,
    *,
    trigger_word: str,
    caption_suffix: str = DEFAULT_CAPTION_SUFFIX,
    recursive: bool = False,
    dataset_mode: str = "combined",
) -> Path:
    """
    Collect images (+ optional sidecar .txt captions) into a temporary dataset.zip.

    dataset_mode: face | body | combined (merges face+body subfolders for v3).
    Returns path to the zip file (caller should delete when done).
    """
    folder_path = folder_path.resolve()
    mode = dataset_mode.lower().strip()

    if mode == "combined":
        tier_dirs = [folder_path / name for name in V3_TIER_SUBFOLDERS]
        legacy_dirs = [folder_path / "face", folder_path / "body"]
        active_tiers = [d for d in tier_dirs if d.is_dir()]
        if active_tiers:
            merge_root = Path(tempfile.mkdtemp(prefix="lora_merge_"))
            for sub in active_tiers:
                for image_path in iter_images(sub, recursive=False):
                    dest = merge_root / f"{sub.name}_{image_path.name}"
                    shutil.copy2(image_path, dest)
                    caption_path = image_path.with_suffix(".txt")
                    if caption_path.is_file():
                        shutil.copy2(caption_path, dest.with_suffix(".txt"))
            folder_path = merge_root
            recursive = False
        else:
            face_dir = folder_path / "face"
            body_dir = folder_path / "body"
            if face_dir.is_dir() and body_dir.is_dir():
                merge_root = Path(tempfile.mkdtemp(prefix="lora_merge_"))
                for sub in (face_dir, body_dir):
                    for image_path in iter_images(sub, recursive=False):
                        dest = merge_root / f"{sub.name}_{image_path.name}"
                        shutil.copy2(image_path, dest)
                        caption_path = image_path.with_suffix(".txt")
                        if caption_path.is_file():
                            shutil.copy2(caption_path, dest.with_suffix(".txt"))
                folder_path = merge_root
                recursive = False
            else:
                recursive = True

    images = list(iter_images(folder_path, recursive=recursive))
    if mode in ("face", "body"):
        sub = folder_path / mode
        if sub.is_dir():
            images = list(iter_images(sub, recursive=False))
            folder_path = sub

    if not images:
        raise ValueError(f"Nessuna immagine (.jpg/.jpeg/.png) in {folder_path} mode={mode}")

    if len(images) > MAX_UPLOAD_IMAGES:
        logger.warning(
            "[DATASET] %d immagini — subsampling a %d per limite upload Replicate",
            len(images),
            MAX_UPLOAD_IMAGES,
        )
        step = max(1, len(images) // MAX_UPLOAD_IMAGES)
        images = images[::step][:MAX_UPLOAD_IMAGES]

    tmp = tempfile.NamedTemporaryFile(
        prefix="lora_dataset_",
        suffix=".zip",
        delete=False,
    )
    tmp.close()
    zip_path = Path(tmp.name)

    generated_captions = 0
    included_captions = 0

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for image_path in images:
            arcname = image_path.name
            zf.write(image_path, arcname=arcname)

            caption_path = image_path.with_suffix(".txt")
            if caption_path.is_file():
                zf.write(caption_path, arcname=caption_path.name)
                included_captions += 1
            else:
                caption_text = _default_caption(trigger_word, caption_suffix)
                zf.writestr(f"{image_path.stem}.txt", caption_text)
                generated_captions += 1

    logger.info(
        "[DATASET] %d immagini in %s (%d caption esistenti, %d generate)",
        len(images),
        zip_path,
        included_captions,
        generated_captions,
    )
    return zip_path


def start_lora_training(
    zip_path: Path,
    *,
    trigger_word: str,
    destination: str,
    steps: int = DEFAULT_STEPS,
    learning_rate: Optional[float] = None,
    trainer_version: Optional[str] = None,
    lora_rank: Optional[int] = None,
) -> Any:
    """Start Replicate LoRA training; returns the Training resource."""
    import replicate

    token = _require_replicate_token()
    client = replicate.Client(api_token=token)
    version = _resolve_trainer_version(client, trainer_version)

    if not destination or "/" not in destination:
        raise ValueError(
            "destination deve essere owner/model-name (es. myuser/flux-lora-soggetto4)"
        )

    training_input: Dict[str, Any] = {
        "input_images": open(zip_path, "rb"),
        "trigger_word": trigger_word,
        "steps": steps,
    }
    if learning_rate is not None:
        training_input["learning_rate"] = learning_rate
    if lora_rank is not None:
        training_input["lora_rank"] = lora_rank
        logger.info("[TRAINING] lora_rank=%d", lora_rank)

    logger.info(
        "[TRAINING] Avvio addestramento destination=%s steps=%d trigger=%s",
        destination,
        steps,
        trigger_word,
    )

    try:
        training = client.trainings.create(
            version=version,
            input=training_input,
            destination=destination,
        )
    finally:
        file_handle = training_input.get("input_images")
        if hasattr(file_handle, "close"):
            file_handle.close()

    logger.info("[TRAINING] Job creato id=%s url=https://replicate.com/p/%s", training.id, training.id)
    return training


def _training_to_dict(training: Any) -> Dict[str, Any]:
    if hasattr(training, "dict"):
        return training.dict()
    if hasattr(training, "model_dump"):
        return training.model_dump()
    return dict(training)


def _extract_weights_url(training: Any) -> Optional[str]:
    output = getattr(training, "output", None)
    if output is None:
        return None

    if isinstance(output, str) and output.startswith("http"):
        return output

    if isinstance(output, dict):
        for key in ("weights", "weights_url", "lora", "lora_weights", "model", "output"):
            value = output.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                nested = value.get("url") or value.get("weights")
                if isinstance(nested, str) and nested.startswith("http"):
                    return nested

    if isinstance(output, (list, tuple)):
        for item in output:
            if isinstance(item, str) and item.startswith("http") and item.endswith(".safetensors"):
                return item

    return None


def poll_training(
    training_id: str,
    *,
    poll_interval: int = POLL_INTERVAL_SECONDS,
) -> Any:
    """Poll Replicate until training succeeds, fails, or is canceled."""
    import replicate

    token = _require_replicate_token()
    client = replicate.Client(api_token=token)

    while True:
        training = client.trainings.get(training_id)
        status = getattr(training, "status", "unknown")
        logger.info("[TRAINING] Addestramento in corso. Status: %s...", status)

        if status == "succeeded":
            return training
        if status == "failed":
            detail = getattr(training, "error", None) or _training_to_dict(training)
            raise RuntimeError(f"Addestramento LoRA fallito: {detail}")
        if status == "canceled":
            raise RuntimeError(f"Addestramento LoRA annullato (id={training_id})")

        time.sleep(poll_interval)


def build_metadata(
    training: Any,
    *,
    trigger_word: str,
    destination: str,
    steps: int,
    dataset_folder: Path,
) -> Dict[str, Any]:
    training_dict = _training_to_dict(training)
    version_obj = training_dict.get("version")
    if isinstance(version_obj, dict):
        trained_version = version_obj.get("id")
    else:
        trained_version = version_obj

    weights_url = _extract_weights_url(training)
    replicate_weights = None
    if destination and trained_version:
        replicate_weights = f"{destination}:{trained_version}"

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trigger_word": trigger_word,
        "destination": destination,
        "training_id": training_dict.get("id"),
        "status": training_dict.get("status"),
        "steps": steps,
        "dataset_folder": str(dataset_folder.resolve()),
        "weights_url": weights_url,
        "replicate_weights": replicate_weights,
        "trainer": DEFAULT_TRAINER,
        "trainer_version": (
            trained_version
            if isinstance(trained_version, str)
            else training_dict.get("version")
        ),
        "output": training_dict.get("output"),
        "completed_at": training_dict.get("completed_at"),
        "urls": training_dict.get("urls"),
    }


def save_metadata(metadata: Dict[str, Any], output_path: Path) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("[TRAINING] Metadata salvati in %s", output_path)
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Addestra un LoRA Flux su Replicate da una cartella di immagini curate.",
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Cartella con immagini (.jpg/.jpeg/.png) e caption .txt opzionali.",
    )
    parser.add_argument(
        "--trigger-word",
        required=True,
        help="Token trigger univoco (es. soggetto4_token).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path JSON metadata (default: models/lora_<trigger>.json).",
    )
    parser.add_argument(
        "--destination",
        default=os.getenv("REPLICATE_LORA_DESTINATION", "").strip() or None,
        help="Modello Replicate destinazione owner/name (o REPLICATE_LORA_DESTINATION).",
    )
    parser.add_argument(
        "--dataset-mode",
        choices=("face", "body", "combined"),
        default=None,
        help="Modalità dataset v3: face, body, o combined (merge face+body). "
        "Default combined quando la cartella ha sottocartelle face/ e body/.",
    )
    parser.add_argument(
        "--lora-rank",
        type=int,
        default=None,
        help=f"LoRA rank (lora_rank su Replicate). Default {DEFAULT_LORA_RANK} per combined.",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help=f"Training steps (default: {DEFAULT_STEPS} legacy, {DEFAULT_COMBINED_STEPS} combined).",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=None,
        help="Learning rate (default: omesso, usa default endpoint ~4e-4).",
    )
    parser.add_argument(
        "--trainer-version",
        default=None,
        help="Versione trainer (es. ostris/flux-dev-lora-trainer:26dce37a).",
    )
    parser.add_argument(
        "--caption-suffix",
        default=DEFAULT_CAPTION_SUFFIX,
        help="Suffisso caption quando manca il file .txt sidecar.",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Includi immagini nelle sottocartelle.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        help=f"Intervallo polling secondi (default: {POLL_INTERVAL_SECONDS}).",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Solo crea dataset.zip e stampa il path (no training, no costi).",
    )
    return parser


def _default_output_path(trigger_word: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in trigger_word)
    return MODELS_DIR / f"lora_{safe}.json"


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    input_dir = Path(args.input)
    dataset_mode = args.dataset_mode or "combined"
    has_v3_layout = (input_dir / "face").is_dir() and (input_dir / "body").is_dir()
    if args.dataset_mode is None and not has_v3_layout:
        dataset_mode = "combined"

    steps = args.steps
    if steps is None:
        steps = DEFAULT_COMBINED_STEPS if has_v3_layout or dataset_mode == "combined" else DEFAULT_STEPS

    lora_rank = args.lora_rank
    if lora_rank is None and (has_v3_layout or dataset_mode == "combined"):
        lora_rank = DEFAULT_LORA_RANK

    if steps < 100 or steps > 5000:
        raise SystemExit("--steps deve essere tra 100 e 5000")

    output_path = Path(args.output) if args.output else _default_output_path(args.trigger_word)

    zip_path: Optional[Path] = None
    training = None
    keep_zip = bool(args.prepare_only)

    try:
        zip_path = prepare_dataset(
            input_dir,
            trigger_word=args.trigger_word,
            caption_suffix=args.caption_suffix,
            recursive=args.recursive,
            dataset_mode=dataset_mode,
        )

        if args.prepare_only:
            print(zip_path)
            return 0

        if not args.destination:
            raise SystemExit(
                "Specificare --destination owner/model-name oppure REPLICATE_LORA_DESTINATION in .env"
            )

        training = start_lora_training(
            zip_path,
            trigger_word=args.trigger_word,
            destination=args.destination,
            steps=steps,
            learning_rate=args.learning_rate,
            trainer_version=args.trainer_version,
            lora_rank=lora_rank,
        )

        training = poll_training(training.id, poll_interval=args.poll_interval)

        metadata = build_metadata(
            training,
            trigger_word=args.trigger_word,
            destination=args.destination,
            steps=steps,
            dataset_folder=input_dir,
        )
        if lora_rank is not None:
            metadata["lora_rank"] = lora_rank
        metadata["dataset_mode"] = dataset_mode
        save_metadata(metadata, output_path)
        return 0

    except KeyboardInterrupt:
        logger.warning("[TRAINING] Interrotto dall'utente (KeyboardInterrupt)")
        if training is not None:
            training_id = getattr(training, "id", None)
            if training_id:
                logger.warning(
                    "[TRAINING] Il job Replicate %s potrebbe continuare in cloud. "
                    "Annullalo da https://replicate.com/p/%s se necessario.",
                    training_id,
                    training_id,
                )
        return 130

    finally:
        if zip_path and zip_path.exists() and not keep_zip:
            try:
                zip_path.unlink()
                logger.debug("[DATASET] Rimosso zip temporaneo %s", zip_path)
            except OSError as exc:
                logger.warning("[DATASET] Impossibile rimuovere %s: %s", zip_path, exc)


if __name__ == "__main__":
    raise SystemExit(main())
