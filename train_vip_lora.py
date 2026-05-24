#!/usr/bin/env python3
"""Train VIP Flux LoRA for any active subject (Replicate ostris/flux-dev-lora-trainer)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from vip_config import get_subject_vip_config, normalize_subject_list

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _noop_registry(_: dict[str, Any]) -> None:
    logger.info("[REGISTRY] Skipped promote (non-S2 subject)")


def run_training(subject_num: int, *, force: bool = False) -> dict[str, Any]:
    import train_vip_lora_s2 as tv

    cfg = get_subject_vip_config(subject_num)
    tv.DATASET_DIR = cfg.output_dir
    tv.GENDER_JSON = cfg.output_dir / "gender.json"
    tv.VIP_JSON = cfg.metadata_json
    tv.VIP_URL = cfg.metadata_url
    tv.TRIGGER_WORD = cfg.trigger_word
    tv.DESTINATION = cfg.replicate_destination
    tv.FALLBACK_DESTINATION = cfg.replicate_fallback

    if subject_num != 2:
        tv.update_registry = _noop_registry
        tv.LEGACY_JSON = cfg.metadata_json
        tv.LEGACY_URL = cfg.metadata_url

    return tv.run_training(force=force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train VIP LoRA for a subject")
    parser.add_argument("--subject", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata = run_training(args.subject, force=args.force)
    except Exception as exc:
        logger.error("VIP training failed for S%d: %s", args.subject, exc)
        return 1

    cfg = get_subject_vip_config(args.subject)
    print(f"\n=== train_vip_lora S{args.subject} RESULT ===")
    print(f"Status: {metadata.get('status')}")
    print(f"Trigger: {metadata.get('trigger_word')}")
    print(f"Weights URL: {metadata.get('weights_url')}")
    print(f"Metadata: {cfg.metadata_json}")
    return 0 if str(metadata.get("status", "")).lower() == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
