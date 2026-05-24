#!/usr/bin/env python3
"""VIP dataset curator (Soggetto 2 defaults)."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from auto_curator import (
    BiometricCurator,
    Candidate,
    _iter_media_files,
    _read_image,
    _sample_video_frames,
)
from gender_detector import build_vip_face_caption, resolve_subject_gender

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SUBJECT_NUM = 2
INPUT_DIR = PROJECT_ROOT / "inputs" / "Soggetto 2"
OUTPUT_DIR = PROJECT_ROOT / "inputs" / "VIP_Dataset_Soggetto2"
SCARTI_DIR = PROJECT_ROOT / "inputs" / "scarti_soggetto2"
STATS_FILE = PROJECT_ROOT / "outputs" / "vip_curation_s2_stats.json"
TRIGGER_WORD = "soggetto_due_vip"
VIP_EXPORT_STEM_PREFIX = "vip_s2_"
GENDER_JSON_NAME = "gender.json"

VIP_TARGET_MIN = 15
VIP_FALLBACK_MIN = 15
VIP_TARGET_MAX = 25
SHARPNESS_THRESHOLD = 80.0


@dataclass
class VIPCurationStats:
    timestamp: str = ""
    cleanup_strategy: str = "none_by_default"
    input_dir: str = ""
    output_dir: str = ""
    scarti_dir: str = ""
    sharpness_threshold: float = SHARPNESS_THRESHOLD
    accepted_total: int = 0
    rejected_total: int = 0
    exported: int = 0
    moved_to_scarti: int = 0
    kept_in_source: int = 0
    relaxed: bool = False
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    exported_files: list[str] = field(default_factory=list)
    fallback_face_front: int = 0
    moved_files: list[str] = field(default_factory=list)
    kept_files: list[str] = field(default_factory=list)


def _face_crop_sharpness(curator: BiometricCurator, image, bbox) -> float:
    x1, y1, x2, y2 = bbox
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0
    return curator.measure_sharpness(crop)


def _evaluate_vip(
    curator: BiometricCurator,
    image,
    *,
    source_label: str,
    source_path: Path,
    image_path: Optional[Path],
    sharpness_threshold: float,
    strict: bool = True,
) -> tuple[Optional[Candidate], str]:
    candidate, reason = curator.evaluate(
        image,
        source_label,
        source_path,
        image_path=image_path,
        strict=strict,
    )
    if candidate is None:
        key = "blur" if "sfocat" in reason.lower() else "no_face"
        if "occlus" in reason.lower() or "landmark" in reason.lower():
            key = "occlusion"
        return None, key

    bbox = curator._face_detection_bbox(image)
    if bbox is None:
        return None, "no_face"
    if _face_crop_sharpness(curator, image, bbox) < sharpness_threshold:
        return None, "blur"

    ok, metrics = curator.check_face_size_and_occlusion(image, strict=strict)
    if not ok or metrics is None:
        return None, "occlusion"

    candidate.score = round(candidate.lora_score + candidate.sharpness * 0.05, 2)
    return candidate, ""


def _select_vip_diverse(candidates: list[Candidate], *, max_count: int) -> list[Candidate]:
    ranked = sorted(candidates, key=lambda c: c.lora_score, reverse=True)
    selected: list[Candidate] = []
    used: set[str] = set()
    for cand in ranked:
        if len(selected) >= max_count:
            break
        key = str(cand.source_path.resolve())
        if key in used:
            continue
        used.add(key)
        selected.append(cand)
    if len(selected) < min(VIP_TARGET_MIN, max_count):
        for cand in ranked:
            if len(selected) >= max_count:
                break
            if cand not in selected:
                selected.append(cand)
    return selected[:max_count]


def _collect_candidates(
    curator: BiometricCurator,
    *,
    input_dir: Path,
    recursive: bool,
    sharpness_threshold: float,
    stats: VIPCurationStats,
    strict: bool = True,
) -> list[Candidate]:
    accepted: list[Candidate] = []
    video_ext = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    for media_path in _iter_media_files(input_dir, recursive):
        if media_path.suffix.lower() in video_ext:
            for frame, label in _sample_video_frames(
                media_path, min_sharpness=sharpness_threshold
            ):
                cand, reason = _evaluate_vip(
                    curator,
                    frame,
                    source_label=label,
                    source_path=media_path,
                    image_path=None,
                    sharpness_threshold=sharpness_threshold,
                
                    strict=strict,
                )
                if cand is None:
                    stats.rejected_total += 1
                    stats.rejection_reasons[reason] = stats.rejection_reasons.get(reason, 0) + 1
                else:
                    stats.accepted_total += 1
                    accepted.append(cand)
            continue
        image = _read_image(media_path)
        if image is None:
            stats.rejected_total += 1
            stats.rejection_reasons["no_face"] = stats.rejection_reasons.get("no_face", 0) + 1
            continue
        cand, reason = _evaluate_vip(
            curator,
            image,
            source_label=media_path.name,
            source_path=media_path,
            image_path=media_path,
            sharpness_threshold=sharpness_threshold,
        
            strict=strict,
        )
        if cand is None:
            stats.rejected_total += 1
            stats.rejection_reasons[reason] = stats.rejection_reasons.get(reason, 0) + 1
        else:
            stats.accepted_total += 1
            accepted.append(cand)
    return accepted




def _face_front_tier_dir(subject_num: int) -> Path:
    return PROJECT_ROOT / "datasets" / f"soggetto{subject_num}_v3" / "face_front"


def _seed_vip_from_face_front_tier(
    *,
    subject_num: int,
    output_dir: Path,
    trigger_word: str,
    subject_gender: str,
    stem_prefix: str,
    need: int,
    max_total: int,
) -> list[Path]:
    """Copy tier-A face_front exports into VIP dataset when biometric curation is sparse."""
    tier_dir = _face_front_tier_dir(subject_num)
    if not tier_dir.is_dir():
        logger.warning("VIP fallback: missing %s", tier_dir)
        return []
    images = sorted(tier_dir.glob("lora_train_*.jpg"))
    if not images:
        logger.warning("VIP fallback: no lora_train_*.jpg in %s", tier_dir)
        return []
    caption = build_vip_face_caption(trigger_word, subject_gender)  # type: ignore[arg-type]
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(output_dir.glob(f"{stem_prefix}*.jpg")))
    exported: list[Path] = []
    for src in images:
        if existing + len(exported) >= max_total:
            break
        if len(exported) >= need:
            break
        idx = existing + len(exported) + 1
        stem = f"{stem_prefix}{idx:03d}"
        out_img = output_dir / f"{stem}.jpg"
        out_txt = output_dir / f"{stem}.txt"
        shutil.copy2(src, out_img)
        out_txt.write_text(caption + "\n", encoding="utf-8")
        exported.append(out_img)
    if exported:
        logger.info(
            "VIP fallback: seeded %d images from %s (subject %d)",
            len(exported),
            tier_dir.name,
            subject_num,
        )
    return exported


def _export_vip_images(
    selected: list[Candidate],
    *,
    output_dir: Path,
    trigger_word: str,
    subject_gender: str,
    stem_prefix: str,
) -> list[Path]:
    import cv2

    output_dir.mkdir(parents=True, exist_ok=True)
    caption = build_vip_face_caption(trigger_word, subject_gender)  # type: ignore[arg-type]
    exported: list[Path] = []
    for index, cand in enumerate(selected, start=1):
        stem = f"{stem_prefix}{index:03d}"
        out_img = output_dir / f"{stem}.jpg"
        out_txt = output_dir / f"{stem}.txt"
        crop = cand.face_crop
        if crop is None:
            crop = cand.processed_image
        if crop is None:
            crop = cand.image
        cv2.imwrite(str(out_img), crop)
        out_txt.write_text(caption + "\n", encoding="utf-8")
        exported.append(out_img)
    gender_path = output_dir / GENDER_JSON_NAME
    if not gender_path.is_file():
        gender_path.write_text(
            json.dumps({"gender": subject_gender, "source": "auto_vip_curator"}, indent=2) + "\n",
            encoding="utf-8",
        )
    return exported


def _relative_to_input(path: Path, input_dir: Path) -> Path:
    try:
        return path.relative_to(input_dir)
    except ValueError:
        return Path(path.name)


def _copy_to_scarti(source: Path, input_dir: Path, scarti_dir: Path) -> Path:
    dest = scarti_dir / _relative_to_input(source, input_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def _move_to_scarti(source: Path, input_dir: Path, scarti_dir: Path) -> Path:
    dest = scarti_dir / _relative_to_input(source, input_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return dest


def run_vip_curation(
    *,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    scarti_dir: Path | None = None,
    stats_file: Path | None = None,
    trigger_word: str | None = None,
    export_stem_prefix: str | None = None,
    dry_run: bool = False,
    recursive: bool = True,
    move_rejects: bool = False,
    copy_rejects_to: Path | None = None,
    sharpness_threshold: float = SHARPNESS_THRESHOLD,
    max_export: int = VIP_TARGET_MAX,
) -> VIPCurationStats:
    input_dir = input_dir or INPUT_DIR
    output_dir = output_dir or OUTPUT_DIR
    scarti_dir = scarti_dir or SCARTI_DIR
    stats_file = stats_file or STATS_FILE
    trigger_word = trigger_word or TRIGGER_WORD
    stem_prefix = export_stem_prefix or VIP_EXPORT_STEM_PREFIX

    stats = VIPCurationStats(
        timestamp=datetime.now(timezone.utc).isoformat(),
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        scarti_dir=str(scarti_dir),
        sharpness_threshold=sharpness_threshold,
        cleanup_strategy="move_rejects" if move_rejects else ("copy_rejects" if copy_rejects_to else "none_by_default"),
    )

    gender_result = resolve_subject_gender(
        gender_json=output_dir / GENDER_JSON_NAME,
        subject_folder=input_dir,
        write_json=True,
    )
    subject_gender = gender_result.gender

    with BiometricCurator(sharpness_threshold=sharpness_threshold) as curator:
        accepted = _collect_candidates(
            curator,
            input_dir=input_dir,
            recursive=recursive,
            sharpness_threshold=sharpness_threshold,
            stats=stats,
        )
        selected = _select_vip_diverse(accepted, max_count=max_export)
        if len(selected) < VIP_TARGET_MIN and accepted:
            stats.relaxed = True
            relaxed_threshold = max(50.0, sharpness_threshold * 0.75)
            logger.warning("Relaxing VIP sharpness threshold to %.1f", relaxed_threshold)
            stats.sharpness_threshold = relaxed_threshold
            stats.accepted_total = 0
            stats.rejected_total = 0
            stats.rejection_reasons = {}
            accepted = _collect_candidates(
                curator,
                input_dir=input_dir,
                recursive=recursive,
                sharpness_threshold=relaxed_threshold,
                stats=stats,
            )
            selected = _select_vip_diverse(accepted, max_count=max_export)
        if not accepted:
            logger.warning("Strict VIP found no candidates; retrying with relaxed biometrics")
            stats.relaxed = True
            stats.accepted_total = 0
            stats.rejected_total = 0
            stats.rejection_reasons = {}
            accepted = _collect_candidates(
                curator,
                input_dir=input_dir,
                recursive=recursive,
                sharpness_threshold=max(45.0, sharpness_threshold * 0.6),
                stats=stats,
                strict=False,
            )
            selected = _select_vip_diverse(accepted, max_count=max_export)

    if (
        not dry_run
        and len(selected) < VIP_FALLBACK_MIN
    ):
        need = max(1, VIP_FALLBACK_MIN - len(selected))
        if selected:
            pre = _export_vip_images(
                selected,
                output_dir=output_dir,
                trigger_word=trigger_word,
                subject_gender=subject_gender,
                stem_prefix=stem_prefix,
            )
            stats.exported = len(pre)
            stats.exported_files = [x.name for x in pre]
        seeded = _seed_vip_from_face_front_tier(
            subject_num=SUBJECT_NUM,
            output_dir=output_dir,
            trigger_word=trigger_word,
            subject_gender=subject_gender,
            stem_prefix=stem_prefix,
            need=need,
            max_total=max_export,
        )
        if seeded:
            stats.fallback_face_front = len(seeded)
            stats.exported = len(list(output_dir.glob(f"{stem_prefix}*.jpg")))
            stats.exported_files = [x.name for x in sorted(output_dir.glob(f"{stem_prefix}*.jpg"))]

    exported_paths: list[Path] = []
    if not dry_run and selected and stats.exported <= 0:
        exported_paths = _export_vip_images(
            selected,
            output_dir=output_dir,
            trigger_word=trigger_word,
            subject_gender=subject_gender,
            stem_prefix=stem_prefix,
        )
        stats.exported = len(exported_paths)
        stats.exported_files = [p.name for p in exported_paths]

    exported_sources = {str(c.source_path.resolve()) for c in selected}
    reject_root = copy_rejects_to or (scarti_dir if move_rejects else None)
    if not dry_run and reject_root is not None and selected:
        for media_path in _iter_media_files(input_dir, recursive):
            key = str(media_path.resolve())
            if key in exported_sources:
                stats.kept_in_source += 1
                stats.kept_files.append(key)
                continue
            if move_rejects:
                dest = _move_to_scarti(media_path, input_dir, reject_root)
            else:
                dest = _copy_to_scarti(media_path, input_dir, reject_root)
            stats.moved_to_scarti += 1
            stats.moved_files.append(str(dest))

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        stats_file.write_text(json.dumps(stats.__dict__, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VIP biometric curator (Soggetto 2 defaults)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--move-rejects", action="store_true")
    parser.add_argument("--copy-rejects-to", type=Path, default=None)
    parser.add_argument("--sharpness-threshold", type=float, default=SHARPNESS_THRESHOLD)
    parser.add_argument("--max-export", type=int, default=VIP_TARGET_MAX)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    stats = run_vip_curation(
        dry_run=args.dry_run,
        recursive=not args.no_recursive,
        move_rejects=args.move_rejects,
        copy_rejects_to=args.copy_rejects_to,
        sharpness_threshold=args.sharpness_threshold,
        max_export=max(1, args.max_export),
    )
    if stats.exported < 5 and not args.dry_run:
        logger.error("No VIP images exported (need >=5)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
