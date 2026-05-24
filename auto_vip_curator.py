#!/usr/bin/env python3
"""Generic VIP curator wrapper: patches auto_vip_curator_s2 module paths per --subject."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import auto_vip_curator_s2 as curator_mod
from vip_config import get_subject_vip_config, normalize_subject_list


def _apply_subject(subject_num: int) -> None:
    cfg = get_subject_vip_config(subject_num)
    curator_mod.SUBJECT_NUM = subject_num
    curator_mod.INPUT_DIR = cfg.input_dir
    curator_mod.OUTPUT_DIR = cfg.output_dir
    curator_mod.SCARTI_DIR = cfg.scarti_dir
    curator_mod.STATS_FILE = cfg.stats_file
    curator_mod.TRIGGER_WORD = cfg.trigger_word
    curator_mod.VIP_EXPORT_STEM_PREFIX = cfg.export_stem_prefix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VIP curator for any subject")
    parser.add_argument("--subject", type=int, required=True, help="Subject number (1-6)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--move-rejects", action="store_true")
    parser.add_argument("--copy-rejects-to", type=Path, default=None)
    parser.add_argument("--sharpness-threshold", type=float, default=curator_mod.SHARPNESS_THRESHOLD)
    parser.add_argument("--max-export", type=int, default=curator_mod.VIP_TARGET_MAX)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _apply_subject(args.subject)
    return curator_mod.main(
        [
            *( ["--dry-run"] if args.dry_run else [] ),
            *( ["--no-recursive"] if args.no_recursive else [] ),
            *( ["--move-rejects"] if args.move_rejects else [] ),
            *( ["--copy-rejects-to", str(args.copy_rejects_to)] if args.copy_rejects_to else [] ),
            "--sharpness-threshold",
            str(args.sharpness_threshold),
            "--max-export",
            str(args.max_export),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
