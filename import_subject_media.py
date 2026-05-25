#!/usr/bin/env python3
"""
Primary entry point for importing subject media from external sources.

COPY-ONLY pipeline: external source folders are read-only. Selected media is
copied into ``inputs/Soggetto N/``; optional LoRA exports go to ``datasets/``.
No unlink, rmtree, or move-from-source operations are performed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from auto_curator import (
    SUBJECT_TRIGGER_MAP,
    TierFrame,
    _iter_media_files,
    _subject_dataset_folder,
    _subject_input_folder,
    collect_tier_frames,
    diagnose_media,
    resolve_quality_mode,
    run_lora_export_v3,
)
from gender_detector import (
    GenderResult,
    detect_gender_from_folder,
    outputs_gender_path,
    resolve_gender,
    save_gender_json,
)
from media_import_safety import collect_file_hashes, file_sha256, safe_copy

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUTS_ROOT = PROJECT_ROOT / "inputs"
DEFAULT_DATASET_VERSION = "v3"

SAFETY_GUARD = True
SAFE_LOG_MESSAGE = "[SAFE] copy-only mode — source untouched, no deletions"


@dataclass
class ImportStats:
    source_dir: Path
    subject_id: int
    inputs_dir: Path
    media_scanned: int = 0
    loose_scan_count: int = 0
    quality_mode: str = ""
    tier_counts: dict[str, int] = field(default_factory=dict)
    tier_folder_counts: dict[str, int] = field(default_factory=dict)
    candidates_accepted: int = 0
    copied_to_inputs: int = 0
    skipped_dedup: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    copied_files: list[str] = field(default_factory=list)
    dry_run: bool = False
    merge_inputs: bool = False


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return dest.with_name(f"{dest.stem}_{stamp}{dest.suffix}")


def _copy_media_file(
    source_file: Path,
    dest: Path,
    *,
    dry_run: bool,
    existing_hashes: set[str],
) -> tuple[Optional[Path], bool]:
    """Copy file if SHA256 not already present. Returns (dest, skipped_dedup)."""
    try:
        digest = file_sha256(source_file)
    except OSError as exc:
        logger.warning("[DEDUP] Cannot hash %s: %s", source_file, exc)
        digest = ""

    if digest and digest in existing_hashes:
        logger.info("[DEDUP] Skip (hash match): %s", source_file)
        return None, True

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest)
    if dry_run:
        logger.info("[DRY-RUN] Would copy %s -> %s", source_file, dest)
        if digest:
            existing_hashes.add(digest)
        return dest, False
    safe_copy(source_file, dest)
    if digest:
        existing_hashes.add(digest)
    logger.info("[COPY] %s -> %s", source_file, dest)
    return dest, False


def _relative_under(source_root: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(source_root.resolve())
    except ValueError:
        return Path(path.name)


def _collect_scan_roots(
    source_dir: Path,
    inputs_dir: Path,
    *,
    merge_inputs: bool,
    recursive: bool,
) -> list[tuple[Path, Path]]:
    """Return (scan_root, rel_base) pairs for source + optional inputs merge."""
    roots: list[tuple[Path, Path]] = [(source_dir.resolve(), source_dir.resolve())]
    if merge_inputs and inputs_dir.is_dir():
        roots.append((inputs_dir.resolve(), inputs_dir.resolve()))
    return roots


def _gather_media_files(
    scan_roots: list[tuple[Path, Path]],
    *,
    recursive: bool,
) -> list[Path]:
    seen: set[str] = set()
    media: list[Path] = []
    for scan_root, _ in scan_roots:
        for path in _iter_media_files(scan_root, recursive):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            media.append(path)
    return media


def _sources_from_tiers(tier_frames: list[TierFrame]) -> set[Path]:
    return {f.source_path.resolve() for f in tier_frames}


def _copy_relative_path(source_file: Path, source_dir: Path) -> Path:
    try:
        return source_file.resolve().relative_to(source_dir.resolve())
    except ValueError:
        return Path(source_file.name)


def run_subject_import(
    source_dir: Path,
    *,
    subject_id: int = 2,
    inputs_root: Path = DEFAULT_INPUTS_ROOT,
    copy_to_inputs: bool = True,
    lora_export: bool = False,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    detect_gender: bool = False,
    build_profile: bool = False,
    dry_run: bool = False,
    gender_override: Optional[str] = None,
    expected_gender: Optional[str] = None,
    interactive: bool = True,
    preserve_relative_paths: bool = True,
    recursive: bool = True,
    merge_inputs: bool = False,
    diagnose_only: bool = False,
) -> ImportStats:
    if not SAFETY_GUARD:
        raise RuntimeError("SAFETY_GUARD must remain True for import flows.")

    logger.info(SAFE_LOG_MESSAGE)

    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source folder missing: {source_dir}")

    inputs_dir = _subject_input_folder(subject_id, inputs_root)
    dataset_dir = _subject_dataset_folder(subject_id, dataset_version)
    trigger = SUBJECT_TRIGGER_MAP.get(subject_id, f"soggetto_{subject_id}")

    scan_roots = _collect_scan_roots(
        source_dir, inputs_dir, merge_inputs=merge_inputs, recursive=recursive
    )
    media_files = _gather_media_files(scan_roots, recursive=recursive)
    loose_count = len(media_files)

    stats = ImportStats(
        source_dir=source_dir,
        subject_id=subject_id,
        inputs_dir=inputs_dir,
        media_scanned=loose_count,
        loose_scan_count=loose_count,
        merge_inputs=merge_inputs,
        dry_run=dry_run,
    )

    if not media_files:
        raise RuntimeError(f"No media files found in {source_dir}")

    profile = resolve_quality_mode(loose_count)
    stats.quality_mode = profile.mode

    logger.info(
        "Import Soggetto %d: %d media (mode=%s) source=%s merge_inputs=%s",
        subject_id,
        loose_count,
        profile.mode,
        source_dir,
        merge_inputs,
    )

    if diagnose_only:
        report = diagnose_media(media_files, profile)
        logger.info("\n=== Diagnose Soggetto %d ===", subject_id)
        logger.info("%s", json.dumps(report, indent=2, ensure_ascii=False))
        stats.tier_counts = report.get("tier_pass_final", {})
        stats.candidates_accepted = sum(stats.tier_counts.values())
        return stats

    gender_result: Optional[GenderResult] = None
    subject_gender = "unknown"
    if detect_gender:
        gender_raw = detect_gender_from_folder(
            source_dir,
            recursive=recursive,
            subject_label=f"Soggetto {subject_id}",
        )
        gender_result = resolve_gender(
            gender_raw,
            subject_label=f"Soggetto {subject_id}",
            gender_override=gender_override,
            expected_gender=expected_gender,
            interactive=interactive,
            dry_run=dry_run,
        )
        subject_gender = gender_result.gender
        if not dry_run:
            save_gender_json(outputs_gender_path(subject_id), gender_result)
            save_gender_json(dataset_dir / "gender.json", gender_result)
        else:
            logger.info(
                "[DRY-RUN] Genere: %s (conf=%.2f)",
                subject_gender,
                gender_result.confidence,
            )
    elif gender_override:
        subject_gender = gender_override
        gender_result = GenderResult(
            gender=gender_override,  # type: ignore[arg-type]
            confidence=1.0,
            votes={gender_override: 1},
            reason=f"CLI override --gender={gender_override}",
        )

    tier_frames, tier_counts = collect_tier_frames(
        media_files, profile, apply_caps=False
    )
    stats.candidates_accepted = len(tier_frames)
    stats.tier_counts = tier_counts

    winner_sources = _sources_from_tiers(tier_frames)

    if copy_to_inputs:
        inputs_dir.mkdir(parents=True, exist_ok=True)
        existing_hashes = collect_file_hashes(inputs_dir, recursive=True)
        for source_file in sorted(winner_sources):
            rel = (
                _copy_relative_path(source_file, source_dir)
                if preserve_relative_paths
                else Path(source_file.name)
            )
            dest = inputs_dir / rel
            copied, skipped = _copy_media_file(
                source_file, dest, dry_run=dry_run, existing_hashes=existing_hashes
            )
            if skipped:
                stats.skipped_dedup += 1
            elif copied is not None:
                stats.copied_files.append(str(copied))
                stats.copied_to_inputs += 1

    if lora_export:
        export_input = inputs_dir if copy_to_inputs and stats.copied_to_inputs else source_dir
        if dry_run:
            logger.info(
                "[DRY-RUN] Would export LoRA v3 tiers -> %s tiers=%s",
                dataset_dir,
                tier_counts,
            )
        else:
            export_result = run_lora_export_v3(
                export_input,
                dataset_dir,
                trigger_word=trigger,
                recursive=recursive,
                subject_gender=subject_gender,
                gender_result=gender_result if detect_gender or gender_override else None,
                quality_profile=profile,
                media_files=media_files,
            )
            stats.tier_folder_counts = export_result.tier_counts
            stats.quality_mode = export_result.quality_mode

    if build_profile and (copy_to_inputs or inputs_dir.is_dir()):
        _maybe_build_profile(subject_id, inputs_dir, gender_result, dry_run=dry_run)

    logger.info("\n=== Import Soggetto %d Summary ===", subject_id)
    logger.info("Source (read-only): %s", source_dir)
    logger.info("Quality mode: %s (loose scan=%d)", stats.quality_mode, stats.loose_scan_count)
    logger.info("Tier frame counts: %s", stats.tier_counts)
    logger.info("Copied to inputs: %d (dedup skip=%d) -> %s", stats.copied_to_inputs, stats.skipped_dedup, inputs_dir)
    if stats.tier_folder_counts:
        logger.info("Dataset tier export: %s", stats.tier_folder_counts)
    logger.info(SAFE_LOG_MESSAGE)

    return stats


def _maybe_build_profile(
    subject_id: int,
    inputs_dir: Path,
    gender_result: Optional[GenderResult],
    *,
    dry_run: bool,
) -> None:
    try:
        import subject_profile_builder as spb  # type: ignore[import-not-found]
    except ImportError:
        logger.info("[PROFILE] subject_profile_builder non disponibile — skip")
        return

    profile_path = inputs_dir / "subject_profile.json"
    if dry_run:
        logger.info("[DRY-RUN] Would build subject profile -> %s", profile_path)
        return

    builder = getattr(spb, "build_subject_profile", None) or getattr(
        spb, "build_profile", None
    )
    if builder is None:
        logger.info("[PROFILE] Nessuna funzione build_* in subject_profile_builder")
        return
    profile = builder(inputs_dir, subject_id=subject_id, gender=gender_result)
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    logger.info("[PROFILE] Scritto %s", profile_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Importa materiale LoRA interessante da cartella esterna (READ-ONLY). "
            "Copia i file selezionati in inputs/Soggetto N/ — non sposta mai dalla sorgente."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Percorso cartella sorgente (OneDrive o altro) — non verrà modificata",
    )
    parser.add_argument(
        "--subject",
        "--subject-id",
        type=int,
        default=2,
        dest="subject_id",
        help="ID soggetto (default: 2 -> inputs/Soggetto 2/)",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scansiona sottocartelle sorgente (default True)",
    )
    parser.add_argument(
        "--copy-to-inputs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Copia i vincitori in inputs/Soggetto N/ (default: True)",
    )
    parser.add_argument(
        "--merge-inputs",
        action="store_true",
        help="Opzione B: unisce scan --source + inputs/Soggetto N/ ricorsivo",
    )
    parser.add_argument(
        "--lora-export",
        action="store_true",
        help="Esporta datasets/soggetto{N}_v3/ con tier A-F",
    )
    parser.add_argument(
        "--detect-gender",
        action="store_true",
        help="Rileva genere con gender_detector; chiede conferma se incerto",
    )
    parser.add_argument(
        "--build-profile",
        action="store_true",
        help="Genera subject_profile.json se subject_profile_builder esiste",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra cosa verrebbe copiato senza scrivere file",
    )
    parser.add_argument(
        "--gender",
        choices=("male", "female"),
        default=None,
        help="Forza genere (salta prompt interattivo)",
    )
    parser.add_argument(
        "--expected-gender",
        choices=("male", "female"),
        default=None,
        help="Blocca import se genere rilevato non corrisponde",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non chiedere conferma genere quando incerto",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Solo report diagnostico (sharpness, face count, tier) senza copiare",
    )
    parser.add_argument(
        "--inputs-root",
        type=Path,
        default=DEFAULT_INPUTS_ROOT,
        help=f"Root inputs (default: {DEFAULT_INPUTS_ROOT})",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        stats = run_subject_import(
            args.source,
            subject_id=args.subject_id,
            inputs_root=args.inputs_root,
            copy_to_inputs=args.copy_to_inputs,
            lora_export=args.lora_export,
            detect_gender=args.detect_gender,
            build_profile=args.build_profile,
            dry_run=args.dry_run,
            gender_override=args.gender,
            expected_gender=args.expected_gender,
            interactive=not args.non_interactive,
            recursive=args.recursive,
            merge_inputs=args.merge_inputs,
            diagnose_only=args.diagnose,
        )
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        return 1
    return 0 if stats.copied_to_inputs > 0 or stats.dry_run or stats.candidates_accepted > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
