#!/usr/bin/env python3
"""Batch sequential LoRA training for subjects 1-5."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_ROOT / "models"
LOG_FILE = PROJECT_ROOT / "lora_batch_training.log"
MIN_IMAGES = 5
MAX_TRAINING_IMAGES = 35
POLL_INTERVAL = 30
DEFAULT_STEPS = 900
DEFAULT_V3_STEPS = 1200
DEFAULT_LORA_RANK = 32
REPLICATE_OWNER = "umbepapa-collab"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

SUBJECTS_BATCH = [1, 2, 3, 5]
SUBJECTS_V3 = [1, 2, 3, 4, 5]

SUBJECT_CONFIG: Dict[int, Dict[str, str]] = {
    1: {"trigger": "soggetto_uno", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto1"},
    2: {"trigger": "soggetto_due", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto2"},
    3: {"trigger": "soggetto_tre", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto3"},
    4: {"trigger": "soggetto_quattro", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto4"},
    5: {"trigger": "soggetto_cinque", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto5"},
    6: {"trigger": "soggetto_sei", "destination": f"{REPLICATE_OWNER}/flux-lora-soggetto6"},
}


def _setup_logging() -> logging.Logger:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("train_all_loras")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


logger = _setup_logging()


def count_images(folder: Path, recursive: bool = True) -> int:
    if not folder.is_dir():
        return 0
    pattern = "**/*" if recursive else "*"
    return sum(
        1
        for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _glob_test_soggetto(subject_num: int) -> Optional[Path]:
    inputs = PROJECT_ROOT / "inputs"
    exact = inputs / f"Test_Soggetto{subject_num}"
    if exact.is_dir() and count_images(exact) > 0:
        return exact
    matches = sorted(inputs.glob(f"Test_Soggetto{subject_num}_*"))
    for candidate in matches:
        if candidate.is_dir() and count_images(candidate) > 0:
            return candidate
    return None


def _run_dataset_automator(subject_num: int, source: Path, trigger: str, out_dataset: Path) -> Path:
    logger.info("[S%d] dataset_automator: %s -> %s", subject_num, source, out_dataset)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "dataset_automator.py"),
        "--input",
        str(source),
        "--subject-name",
        f"soggetto{subject_num}",
        "--trigger-word",
        trigger,
        "--output-dir",
        str(out_dataset),
        "--recursive",
    ]
    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))
    n = count_images(out_dataset)
    if n < MIN_IMAGES:
        raise ValueError(f"Subject {subject_num}: only {n} images after curation")
    return out_dataset.resolve()


def resolve_dataset_folder(
    subject_num: int,
    *,
    version: Optional[str] = None,
    dataset_mode: str = "combined",
) -> Path:
    """Resolve curated dataset path; prefer v3 layout when version is set."""
    cfg = SUBJECT_CONFIG[subject_num]
    trigger = cfg["trigger"]

    if version:
        v3_root = PROJECT_ROOT / "datasets" / f"soggetto{subject_num}_{version}"
        if dataset_mode == "combined":
            tier_total = 0
            tier_parts: list[str] = []
            try:
                from auto_curator import V3_TIER_SUBFOLDERS
            except ImportError:
                V3_TIER_SUBFOLDERS = ("face_front", "face_profile", "body_back", "body_full", "body_partial", "detail_macro")
            for name in V3_TIER_SUBFOLDERS:
                n = count_images(v3_root / name, recursive=False)
                tier_total += n
                if n:
                    tier_parts.append(f"{name}={n}")
            if tier_total >= MIN_IMAGES:
                logger.info(
                    "[S%d] v3 tier dataset: %s (%s total=%d)",
                    subject_num,
                    v3_root,
                    ", ".join(tier_parts),
                    tier_total,
                )
                return v3_root.resolve()
            face_n = count_images(v3_root / "face", recursive=False)
            body_n = count_images(v3_root / "body", recursive=False)
            total = face_n + body_n
            if total >= MIN_IMAGES:
                logger.info(
                    "[S%d] v3 dataset: %s (face=%d body=%d total=%d)",
                    subject_num,
                    v3_root,
                    face_n,
                    body_n,
                    total,
                )
                return v3_root.resolve()
            raise FileNotFoundError(
                f"Subject {subject_num}: v3 dataset insufficient "
                f"(tiers={tier_total}, face={face_n} body={body_n}, need >= {MIN_IMAGES})"
            )
        mode_dir = v3_root / dataset_mode
        n = count_images(mode_dir, recursive=False)
        if n >= MIN_IMAGES:
            logger.info("[S%d] v3 %s dataset: %s (%d images)", subject_num, dataset_mode, mode_dir, n)
            return mode_dir.resolve()
        raise FileNotFoundError(
            f"Subject {subject_num}: v3/{dataset_mode} has only {n} images"
        )

    candidates: List[Path] = []
    test_dir = _glob_test_soggetto(subject_num)
    if test_dir:
        candidates.append(test_dir)
    candidates.append(PROJECT_ROOT / "datasets" / f"soggetto{subject_num}")
    candidates.append(PROJECT_ROOT / "inputs" / f"Soggetto {subject_num}")

    for folder in candidates:
        n = count_images(folder)
        if n >= MIN_IMAGES:
            if n > MAX_TRAINING_IMAGES:
                out_dataset = PROJECT_ROOT / "datasets" / f"soggetto{subject_num}"
                return _run_dataset_automator(subject_num, folder, trigger, out_dataset)
            logger.info("[S%d] Dataset: %s (%d images)", subject_num, folder, n)
            return folder.resolve()

    inputs_folder = PROJECT_ROOT / "inputs" / f"Soggetto {subject_num}"
    if not inputs_folder.is_dir():
        raise FileNotFoundError(f"No input folder for subject {subject_num}: {inputs_folder}")

    out_dataset = PROJECT_ROOT / "datasets" / f"soggetto{subject_num}"
    return _run_dataset_automator(subject_num, inputs_folder, trigger, out_dataset)


def metadata_paths(subject_num: int, *, version: Optional[str] = None) -> Tuple[Path, Path]:
    suffix = f"_{version}" if version else ""
    json_path = MODELS_DIR / f"lora_soggetto{subject_num}{suffix}.json"
    url_path = MODELS_DIR / f"lora_soggetto{subject_num}{suffix}_url.txt"
    return json_path, url_path


def ensure_replicate_destination(destination: str) -> None:
    """Create Replicate model destination if missing (required before training)."""
    if "/" not in destination:
        raise ValueError(f"Invalid destination: {destination}")
    owner, name = destination.split("/", 1)
    import replicate
    import os

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    client = replicate.Client(api_token=token)
    try:
        client.models.get(owner, name)
        logger.info("[DEST] Exists: %s", destination)
        return
    except Exception:
        pass
    logger.info("[DEST] Creating Replicate model: %s", destination)
    client.models.create(
        owner=owner,
        name=name,
        visibility="private",
        hardware="cpu",
        description=f"Flux LoRA destination for {name}",
    )


def should_skip_training(subject_num: int, *, force: bool, version: Optional[str] = None) -> bool:
    if force:
        return False
    json_path, _ = metadata_paths(subject_num, version=version)
    if not json_path.is_file():
        return False
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_trigger = SUBJECT_CONFIG[subject_num]["trigger"]
    status = str(data.get("status", "")).lower()
    trigger = str(data.get("trigger_word", "")).strip()
    if status == "succeeded" and trigger == expected_trigger:
        logger.info(
            "[S%d] Skip training: %s already succeeded with trigger %s",
            subject_num,
            json_path,
            trigger,
        )
        return True
    return False


def save_url_sidecar(metadata: Dict[str, Any], url_path: Path) -> None:
    url = metadata.get("weights_url")
    if not url:
        output = metadata.get("output")
        if isinstance(output, dict):
            url = output.get("weights") or output.get("version")
    if not url:
        urls = metadata.get("urls") or {}
        if isinstance(urls, dict):
            url = urls.get("web")
    if url:
        url_path.write_text(str(url).strip() + "\n", encoding="utf-8")
        logger.info("[TRAINING] URL sidecar: %s", url_path)


def train_subject(
    subject_num: int,
    *,
    force: bool = False,
    version: Optional[str] = None,
    dataset_mode: str = "combined",
) -> Dict[str, Any]:
    cfg = SUBJECT_CONFIG[subject_num]
    json_path, url_path = metadata_paths(subject_num, version=version)

    if should_skip_training(subject_num, force=force, version=version):
        return json.loads(json_path.read_text(encoding="utf-8"))

    dataset_folder = resolve_dataset_folder(
        subject_num, version=version, dataset_mode=dataset_mode
    )
    ensure_replicate_destination(cfg["destination"])

    from train_lora_replicate import (
        DEFAULT_COMBINED_STEPS,
        DEFAULT_LORA_RANK,
        DEFAULT_STEPS,
        build_metadata,
        poll_training,
        prepare_dataset,
        save_metadata,
        start_lora_training,
    )

    is_v3 = version is not None
    steps = DEFAULT_V3_STEPS if is_v3 else DEFAULT_STEPS
    lora_rank = DEFAULT_LORA_RANK if is_v3 and dataset_mode == "combined" else None

    zip_path: Optional[Path] = None
    training = None
    try:
        zip_path = prepare_dataset(
            dataset_folder,
            trigger_word=cfg["trigger"],
            recursive=True,
            dataset_mode=dataset_mode,
        )
        training = start_lora_training(
            zip_path,
            trigger_word=cfg["trigger"],
            destination=cfg["destination"],
            steps=steps,
            lora_rank=lora_rank,
        )
        training_id = getattr(training, "id", None)
        logger.info("[S%d] Replicate training ID: %s", subject_num, training_id)

        training = poll_training(training_id, poll_interval=POLL_INTERVAL)
        metadata = build_metadata(
            training,
            trigger_word=cfg["trigger"],
            destination=cfg["destination"],
            steps=steps,
            dataset_folder=dataset_folder,
        )
        metadata["dataset_mode"] = dataset_mode
        metadata["dataset_version"] = version or "legacy"
        if lora_rank is not None:
            metadata["lora_rank"] = lora_rank
        save_metadata(metadata, json_path)
        save_url_sidecar(metadata, url_path)
        logger.info(
            "[S%d] Complete training_id=%s version=%s",
            subject_num,
            metadata.get("training_id"),
            (metadata.get("output") or {}).get("version")
            if isinstance(metadata.get("output"), dict)
            else metadata.get("replicate_weights"),
        )
        return metadata
    finally:
        if zip_path and zip_path.exists():
            try:
                zip_path.unlink()
            except OSError as exc:
                logger.warning("[S%d] Could not remove temp zip: %s", subject_num, exc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Batch train Flux LoRAs for subjects")
    p.add_argument(
        "--subjects",
        default=None,
        help="Comma-separated subject numbers (default: 1,2,3,5 legacy; 1-5 for v3)",
    )
    p.add_argument("--force", action="store_true", help="Retrain even if metadata matches")
    p.add_argument(
        "--include",
        type=int,
        nargs="*",
        default=None,
        help="Override subject list (e.g. --include 1 2)",
    )
    p.add_argument(
        "--version",
        default=None,
        help="Dataset/model version suffix (es. v3 -> datasets/soggetto{N}_v3, models/lora_soggetto{N}_v3.json)",
    )
    p.add_argument(
        "--dataset-mode",
        choices=("face", "body", "combined"),
        default="combined",
        help="Modalità dataset v3 (default: combined).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.include:
        subjects = args.include
    elif args.subjects:
        subjects = [int(x.strip()) for x in args.subjects.split(",") if x.strip()]
    elif args.version:
        subjects = SUBJECTS_V3
    else:
        subjects = SUBJECTS_BATCH

    results: Dict[int, Dict[str, Any]] = {}
    for num in subjects:
        if num not in SUBJECT_CONFIG:
            logger.error("Unknown subject number: %s", num)
            return 1
        logger.info("========== Subject %d (version=%s) ==========", num, args.version or "legacy")
        try:
            results[num] = train_subject(
                num,
                force=args.force,
                version=args.version,
                dataset_mode=args.dataset_mode,
            )
        except Exception as exc:
            logger.exception("[S%d] Training failed: %s", num, exc)
            return 1

    logger.info("Batch complete for subjects: %s", subjects)
    for num, meta in results.items():
        logger.info(
            "S%d id=%s trigger=%s url_file=%s",
            num,
            meta.get("training_id"),
            meta.get("trigger_word"),
            metadata_paths(num, version=args.version)[1],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
