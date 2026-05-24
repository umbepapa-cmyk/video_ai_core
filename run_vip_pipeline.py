#!/usr/bin/env python3
"""Curate -> train -> closeup -> fullbody VIP pipeline per subject."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from subject_discovery import resolve_reference_face
from vip_config import ACTIVE_SUBJECTS, INPUTS_ROOT, PROJECT_ROOT, get_subject_vip_config, normalize_subject_list

LOG_FILE = PROJECT_ROOT / "vip_pipeline_run.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def _run(cmd: list[str], *, label: str) -> int:
    logger.info("=== %s ===", label)
    logger.info("Command: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


def _ensure_face_reference(subject: int) -> None:
    ref = resolve_reference_face(subject, INPUTS_ROOT)
    if ref is not None:
        return
    cfg = get_subject_vip_config(subject)
    vip_faces = sorted(cfg.output_dir.glob(f"{cfg.export_stem_prefix}*.jpg"))
    if not vip_faces:
        raise FileNotFoundError(f"S{subject}: no face.jpg and no VIP crops")
    dest = cfg.input_dir / "face.jpg"
    shutil.copy2(vip_faces[0], dest)
    logger.info("S%d: copied VIP reference -> %s", subject, dest)


def run_subject(
    subject: int,
    *,
    dry_run: bool,
    skip_train: bool,
    skip_closeup: bool,
    skip_fullbody: bool,
    force_train: bool,
    move_rejects: bool,
) -> int:
    py = sys.executable
    curator_cmd = [py, "auto_vip_curator.py", "--subject", str(subject)]
    if dry_run:
        curator_cmd.append("--dry-run")
    if move_rejects:
        curator_cmd.append("--move-rejects")

    rc = _run(curator_cmd, label=f"S{subject} VIP curation")
    if rc != 0:
        return rc
    if dry_run:
        return 0

    _ensure_face_reference(subject)

    if not skip_train:
        train_cmd = [py, "train_vip_lora.py", "--subject", str(subject)]
        if force_train:
            train_cmd.append("--force")
        rc = _run(train_cmd, label=f"S{subject} VIP train")
        if rc != 0:
            return rc

    if not skip_closeup:
        rc = _run([py, "test_vip_closeup.py", "--subject", str(subject)], label=f"S{subject} closeup")
        if rc != 0:
            return rc

    if not skip_fullbody:
        rc = _run([py, "test_vip_fullbody.py", "--subject", str(subject)], label=f"S{subject} fullbody")
        if rc != 0:
            return rc

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VIP pipeline for subjects")
    parser.add_argument("--subjects", type=int, nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--skip-closeup", action="store_true")
    parser.add_argument("--skip-fullbody", action="store_true")
    parser.add_argument("--force-train", action="store_true")
    parser.add_argument("--move-rejects", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    subjects = normalize_subject_list(args.subjects)
    logger.info("VIP pipeline subjects: %s", subjects)
    for subject in subjects:
        rc = run_subject(
            subject,
            dry_run=args.dry_run,
            skip_train=args.skip_train,
            skip_closeup=args.skip_closeup,
            skip_fullbody=args.skip_fullbody,
            force_train=args.force_train,
            move_rejects=args.move_rejects,
        )
        if rc != 0:
            logger.error("Pipeline failed for S%d (exit %d)", subject, rc)
            return rc
    logger.info("VIP pipeline complete for %s", subjects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
