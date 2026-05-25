#!/usr/bin/env python3
"""
Orchestrate LoRA remediation: curation, retrain S4 (and optionally S5), tests.

Logs append to lora_remediation.log. Run in background for long training polls.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_FILE = PROJECT_ROOT / "lora_remediation.log"


def _setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("lora_remediation")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


def _run(cmd: list[str], logger: logging.Logger) -> int:
    logger.info("CMD: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    logger.info("EXIT %d: %s", proc.returncode, cmd[0])
    return proc.returncode


def curate_s4(logger: logging.Logger) -> int:
    input_dir = PROJECT_ROOT / "inputs" / "Soggetto 4"
    output_dir = PROJECT_ROOT / "datasets" / "soggetto4_v2"
    return _run(
        [
            sys.executable,
            "auto_curator.py",
            "--input",
            str(input_dir),
            "--lora-export",
            str(output_dir),
            "--trigger-word",
            "soggetto_quattro",
            "--top-n",
            "20",
            "--caption-suffix",
            "portrait, front facing, natural skin, high quality",
        ],
        logger,
    )


def curate_s5(logger: logging.Logger) -> int:
    input_dir = PROJECT_ROOT / "inputs" / "Soggetto 5"
    output_dir = PROJECT_ROOT / "datasets" / "soggetto5_v2"
    return _run(
        [
            sys.executable,
            "auto_curator.py",
            "--input",
            str(input_dir),
            "--lora-export",
            str(output_dir),
            "--trigger-word",
            "soggetto_cinque",
            "--top-n",
            "20",
            "--caption-suffix",
            "portrait, front facing, natural skin, high quality",
        ],
        logger,
    )


def train_s4(logger: logging.Logger) -> int:
    from train_all_loras import ensure_replicate_destination

    destination = "umbepapa-collab/flux-lora-soggetto4"
    try:
        ensure_replicate_destination("umbepapa-collab/flux-lora-soggetto4-v2")
        destination = "umbepapa-collab/flux-lora-soggetto4-v2"
    except Exception as exc:
        logger.warning(
            "Destination v2 non disponibile (%s); uso %s",
            exc,
            destination,
        )
    return _run(
        [
            sys.executable,
            "train_lora_replicate.py",
            "-i",
            str(PROJECT_ROOT / "datasets" / "soggetto4_v2"),
            "--trigger-word",
            "soggetto_quattro",
            "--destination",
            destination,
            "--output",
            str(PROJECT_ROOT / "models" / "lora_soggetto4_v2.json"),
            "--steps",
            "900",
        ],
        logger,
    )


def train_s5(logger: logging.Logger) -> int:
    return _run(
        [
            sys.executable,
            "train_lora_replicate.py",
            "-i",
            str(PROJECT_ROOT / "datasets" / "soggetto5_v2"),
            "--trigger-word",
            "soggetto_cinque",
            "--destination",
            "umbepapa-collab/flux-lora-soggetto5-v2",
            "--output",
            str(PROJECT_ROOT / "models" / "lora_soggetto5_v2.json"),
            "--steps",
            "900",
        ],
        logger,
    )


def update_registry_s4(logger: logging.Logger) -> None:
    v2 = PROJECT_ROOT / "models" / "lora_soggetto4_v2.json"
    main = PROJECT_ROOT / "models" / "lora_soggetto4.json"
    url_main = PROJECT_ROOT / "models" / "lora_soggetto4_url.txt"
    if not v2.is_file():
        logger.warning("Registry skip: %s missing", v2)
        return
    meta = json.loads(v2.read_text(encoding="utf-8"))
    main.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    weights = meta.get("weights_url") or ""
    if isinstance(meta.get("output"), dict):
        weights = meta["output"].get("weights") or weights
    if weights:
        url_main.write_text(str(weights).strip() + "\n", encoding="utf-8")
    logger.info("Registry aggiornato: %s -> %s", v2.name, main.name)


def run_tests(logger: logging.Logger) -> int:
    rc = _run([sys.executable, "test_lora_soggetto4.py"], logger)
    if rc != 0:
        return rc
    return _run([sys.executable, "test_native_loras.py", "--subject", "4"], logger)


def dataset_stats(path: Path, logger: logging.Logger) -> None:
    if not path.is_dir():
        logger.info("Dataset %s: assente", path)
        return
    jpgs = list(path.glob("lora_train_*.jpg"))
    logger.info("Dataset %s: %d immagini LoRA export", path.name, len(jpgs))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LoRA remediation orchestrator")
    p.add_argument(
        "--phase",
        choices=("all", "curate", "train", "registry", "test", "curate-train-test"),
        default="curate-train-test",
    )
    p.add_argument("--include-s5", action="store_true", help="Also curate/retrain Soggetto 5")
    return p


def main() -> int:
    logger = _setup_logging()
    args = build_parser().parse_args()
    logger.info("=== LoRA remediation start phase=%s ===", args.phase)
    logger.info("Timestamp UTC: %s", datetime.now(timezone.utc).isoformat())

    if args.phase in ("all", "curate", "curate-train-test"):
        rc = curate_s4(logger)
        dataset_stats(PROJECT_ROOT / "datasets" / "soggetto4_v2", logger)
        if rc != 0:
            logger.error("Curazione S4 fallita")
            return rc
        if args.include_s5:
            curate_s5(logger)
            dataset_stats(PROJECT_ROOT / "datasets" / "soggetto5_v2", logger)

    if args.phase in ("all", "train", "curate-train-test"):
        rc = train_s4(logger)
        if rc != 0:
            logger.error("Training S4 fallito")
            return rc
        update_registry_s4(logger)
        if args.include_s5:
            train_s5(logger)

    if args.phase == "registry":
        update_registry_s4(logger)

    if args.phase in ("all", "test", "curate-train-test"):
        return run_tests(logger)

    logger.info("=== LoRA remediation complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
