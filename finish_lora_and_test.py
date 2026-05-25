#!/usr/bin/env python3
"""Poll in-flight LoRA training, finish metadata, train soggetto5, then run static tests."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SOGGETTO4_TRAINING_ID = "4jahspvpfhrmr0cyas0a9yvr78"
DEST4 = "umbepapa-collab/flux-lora-soggetto4"
DEST5 = "umbepapa-collab/flux-lora-soggetto5"
META4 = PROJECT_ROOT / "models" / "lora_soggetto4.json"
META5 = PROJECT_ROOT / "models" / "lora_soggetto5.json"
DATASET4 = PROJECT_ROOT / "datasets" / "soggetto4"
DATASET5 = PROJECT_ROOT / "datasets" / "soggetto5"


def _finish_training(training_id: str, *, trigger: str, destination: str, output: Path, dataset: Path) -> None:
    import replicate
    from train_lora_replicate import build_metadata, poll_training, save_metadata

    import os

    client = replicate.Client(api_token=os.environ["REPLICATE_API_TOKEN"])
    training = client.trainings.get(training_id)
    status = getattr(training, "status", "unknown")
    logger.info("[PIPELINE] %s status=%s", training_id, status)
    if status != "succeeded":
        training = poll_training(training_id, poll_interval=30)

    metadata = build_metadata(
        training,
        trigger_word=trigger,
        destination=destination,
        steps=900,
        dataset_folder=dataset,
    )
    save_metadata(metadata, output)
    logger.info("[PIPELINE] Saved %s weights_url=%s", output, metadata.get("weights_url"))


def _train_subject(*, input_dir: Path, trigger: str, destination: str, output: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "train_lora_replicate.py"),
        "--input",
        str(input_dir),
        "--trigger-word",
        trigger,
        "--destination",
        destination,
        "--output",
        str(output),
        "--steps",
        "900",
    ]
    logger.info("[PIPELINE] Starting training: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    if META4.is_file():
        logger.info("[PIPELINE] %s already exists — skipping soggetto4", META4)
    else:
        logger.info("[PIPELINE] Finishing soggetto4 training %s", SOGGETTO4_TRAINING_ID)
        _finish_training(
            SOGGETTO4_TRAINING_ID,
            trigger="soggetto_quattro",
            destination=DEST4,
            output=META4,
            dataset=DATASET4,
        )

    if META5.is_file():
        logger.info("[PIPELINE] %s already exists — skipping soggetto5", META5)
    else:
        _train_subject(
            input_dir=DATASET5,
            trigger="soggetto_cinque",
            destination=DEST5,
            output=META5,
        )

    logger.info("[PIPELINE] Running static LoRA anatomy batch")
    subprocess.run([sys.executable, str(PROJECT_ROOT / "run_static_lora_anatomy_batch.py")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
