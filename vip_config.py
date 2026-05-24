"""Shared VIP pipeline paths, triggers, and subject configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from subject_discovery import resolve_subject_input_folder

PROJECT_ROOT = Path(__file__).resolve().parent
INPUTS_ROOT = PROJECT_ROOT / "inputs"
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
MODELS_DIR = PROJECT_ROOT / "models"

ACTIVE_SUBJECTS: tuple[int, ...] = (1, 2, 3, 4, 5)

VIP_TRIGGER_BY_SUBJECT: dict[int, str] = {
    1: "soggetto_uno_vip",
    2: "soggetto_due_vip",
    3: "soggetto_tre_vip",
    4: "soggetto_quattro_vip",
    5: "soggetto_cinque_vip",
    6: "soggetto_sei_vip",
}

ITALIAN_CARDINAL = {
    1: "uno",
    2: "due",
    3: "tre",
    4: "quattro",
    5: "cinque",
    6: "sei",
}


def vip_trigger_word(subject_num: int) -> str:
    if subject_num in VIP_TRIGGER_BY_SUBJECT:
        return VIP_TRIGGER_BY_SUBJECT[subject_num]
    word = ITALIAN_CARDINAL.get(subject_num, str(subject_num))
    return f"soggetto_{word}_vip"


def vip_export_stem_prefix(subject_num: int) -> str:
    return f"vip_s{subject_num}_"


def vip_dataset_dir(subject_num: int, *, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "inputs" / f"VIP_Dataset_Soggetto{subject_num}"


def scarti_dir(subject_num: int, *, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "inputs" / f"scarti_soggetto{subject_num}"


def vip_stats_path(subject_num: int, *, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "outputs" / f"vip_curation_s{subject_num}_stats.json"


def vip_metadata_json(subject_num: int, *, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "models" / f"lora_soggetto{subject_num}_vip.json"


def vip_metadata_url(subject_num: int, *, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "models" / f"lora_soggetto{subject_num}_vip_url.txt"


def replicate_vip_destination(subject_num: int) -> str:
    return f"umbepapa-collab/flux-lora-soggetto{subject_num}-vip"


def replicate_vip_fallback_destination(subject_num: int) -> str:
    return f"umbepapa-collab/flux-lora-soggetto{subject_num}"


@dataclass(frozen=True)
class SubjectVipConfig:
    subject_num: int
    input_dir: Path
    output_dir: Path
    scarti_dir: Path
    stats_file: Path
    trigger_word: str
    export_stem_prefix: str
    metadata_json: Path
    metadata_url: Path
    replicate_destination: str
    replicate_fallback: str


def get_subject_vip_config(
    subject_num: int,
    *,
    project_root: Path | None = None,
) -> SubjectVipConfig:
    root = project_root or PROJECT_ROOT
    inputs = root / "inputs"
    input_dir = resolve_subject_input_folder(subject_num, inputs)
    return SubjectVipConfig(
        subject_num=subject_num,
        input_dir=input_dir,
        output_dir=vip_dataset_dir(subject_num, project_root=root),
        scarti_dir=scarti_dir(subject_num, project_root=root),
        stats_file=vip_stats_path(subject_num, project_root=root),
        trigger_word=vip_trigger_word(subject_num),
        export_stem_prefix=vip_export_stem_prefix(subject_num),
        metadata_json=vip_metadata_json(subject_num, project_root=root),
        metadata_url=vip_metadata_url(subject_num, project_root=root),
        replicate_destination=replicate_vip_destination(subject_num),
        replicate_fallback=replicate_vip_fallback_destination(subject_num),
    )


def normalize_subject_list(subjects: Iterable[int] | None) -> list[int]:
    if subjects is None:
        return list(ACTIVE_SUBJECTS)
    out: list[int] = []
    for n in subjects:
        if n not in out:
            out.append(int(n))
    return out
