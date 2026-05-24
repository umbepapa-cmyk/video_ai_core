#!/usr/bin/env python3
"""Run forest v3 tests then VIP pipeline for active subjects."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from vip_config import ACTIVE_SUBJECTS, PROJECT_ROOT, normalize_subject_list

FOREST_LOG = PROJECT_ROOT / "test_forest_v2_run.log"
VIP_LOG = PROJECT_ROOT / "vip_pipeline_run.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _run(cmd: list[str]) -> int:
    logger.info("Command: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forest v2 + VIP quality pipeline")
    parser.add_argument("--subjects", type=int, nargs="*", default=list(ACTIVE_SUBJECTS))
    parser.add_argument("--skip-forest", action="store_true")
    parser.add_argument("--skip-vip", action="store_true")
    parser.add_argument("--skip-train", action="store_true", help="Pass-through to run_vip_pipeline")
    args = parser.parse_args(argv)
    subjects = normalize_subject_list(args.subjects)
    py = sys.executable

    if not args.skip_forest:
        forest_cmd = [py, "test_forest_v2.py", "--subjects", *[str(s) for s in subjects]]
        with open(FOREST_LOG, "a", encoding="utf-8") as fh:
            fh.write("\n--- run_quality_pipeline forest ---\n")
        rc = _run(forest_cmd)
        if rc != 0:
            return rc

    if not args.skip_vip:
        vip_cmd = [
            py,
            "run_vip_pipeline.py",
            "--subjects",
            *[str(s) for s in subjects],
        ]
        if args.skip_train:
            vip_cmd.append("--skip-train")
        rc = _run(vip_cmd)
        if rc != 0:
            return rc

    logger.info("Quality pipeline finished (logs: %s, %s)", FOREST_LOG.name, VIP_LOG.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
