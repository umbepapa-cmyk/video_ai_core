#!/usr/bin/env python3
"""
Master runner: VIP Soggetto 2 pipeline (curate -> train -> test).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_TEST = PROJECT_ROOT / "outputs" / "test_vip_soggetto2.jpg"
STATS_FILE = PROJECT_ROOT / "outputs" / "vip_curation_s2_stats.json"
VIP_JSON = PROJECT_ROOT / "models" / "lora_soggetto2_vip.json"


def _run(cmd: list[str], *, label: str) -> int:
    logger.info("=== %s ===", label)
    logger.info("Command: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        logger.error("%s failed with exit code %d", label, result.returncode)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="VIP Soggetto 2 full pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Curator dry-run only")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--skip-test", action="store_true")
    parser.add_argument("--force-train", action="store_true")
    args = parser.parse_args()

    py = sys.executable
    curator_cmd = [py, "auto_vip_curator_s2.py"]
    if args.dry_run:
        curator_cmd.append("--dry-run")

    rc = _run(curator_cmd, label="Task 1: VIP Curation")
    if rc != 0:
        return rc
    if args.dry_run:
        logger.info("Dry-run complete — stopping before train/test")
        return 0

    if STATS_FILE.is_file():
        stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        logger.info(
            "Curation stats: accepted=%s exported=%s moved=%s relaxed=%s",
            stats.get("accepted_total"),
            stats.get("exported"),
            stats.get("moved_to_scarti"),
            stats.get("relaxed"),
        )

    if not args.skip_train:
        train_cmd = [py, "train_vip_lora_s2.py"]
        if args.force_train:
            train_cmd.append("--force")
        rc = _run(train_cmd, label="Task 2: VIP LoRA Training")
        if rc != 0:
            return rc

    if not args.skip_test:
        rc = _run([py, "test_lora_soggetto2.py"], label="Task 3: VIP LoRA Test")
        if rc != 0:
            return rc

    if OUTPUT_TEST.is_file():
        size = OUTPUT_TEST.stat().st_size
        logger.info("Pipeline complete: %s (%d bytes)", OUTPUT_TEST, size)
        return 0

    logger.error("Pipeline finished but %s not found", OUTPUT_TEST)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
