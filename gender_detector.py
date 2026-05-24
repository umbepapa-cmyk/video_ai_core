"""
Gender detection for VIP subjects — InsightFace vote + gender.json cache.

Used by curators and full-body inference tests to pick correct pronouns/descriptors.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Literal, Optional, Sequence, Union

logger = logging.getLogger(__name__)

GenderLabel = Literal["male", "female", "unknown"]
CERTAINTY_THRESHOLD = 0.7
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class GenderResult:
    gender: GenderLabel
    confidence: float
    votes: Dict[str, int]
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def is_certain(self, threshold: float = CERTAINTY_THRESHOLD) -> bool:
        return self.gender != "unknown" and self.confidence >= threshold


@dataclass(frozen=True)
class GenderPromptDescriptors:
    """Prompt fragments for a single-subject full-body shot."""

    biological: str
    pronoun: str
    physique: str

    @classmethod
    def for_gender(cls, gender: GenderLabel) -> "GenderPromptDescriptors":
        if gender == "male":
            return cls(
                biological="adult biological male",
                pronoun="He",
                physique="natural male physique",
            )
        if gender == "female":
            return cls(
                biological="adult biological female",
                pronoun="She",
                physique="natural female physique",
            )
        return cls(
            biological="adult person",
            pronoun="They",
            physique="natural physique",
        )


def _normalize_gender(value: object) -> Optional[GenderLabel]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"male", "m", "man", "uomo", "maschio"}:
        return "male"
    if normalized in {"female", "f", "woman", "donna", "femmina"}:
        return "female"
    if normalized in {"unknown", "uncertain", "neutral"}:
        return "unknown"
    return None


def load_gender_json(path: Union[str, Path]) -> Optional[GenderResult]:
    """Load cached gender from ``gender.json`` if present."""
    json_path = Path(path)
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read gender.json at %s: %s", json_path, exc)
        return None

    gender = _normalize_gender(data.get("gender"))
    if gender is None:
        return None

    confidence = float(data.get("confidence", 1.0))
    votes = data.get("votes")
    if not isinstance(votes, dict):
        votes = {gender: 1}
    reason = str(data.get("reason", f"loaded from {json_path.name}"))
    return GenderResult(gender=gender, confidence=confidence, votes=votes, reason=reason)


def save_gender_json(path: Union[str, Path], result: GenderResult) -> Path:
    """Persist detection result for reuse by training/inference scripts."""
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Saved gender metadata: %s (%s, confidence=%.2f)", json_path, result.gender, result.confidence)
    return json_path


def _iter_face_images(
    folder: Path,
    *,
    max_images: int = 30,
    recursive: bool = False,
) -> Iterable[Path]:
    if not folder.is_dir():
        return
    if recursive:
        paths = sorted(
            p
            for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
        )
    else:
        paths = sorted(
            p
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
        )
    for path in paths[:max_images]:
        yield path


@lru_cache(maxsize=2)
def _get_face_analyzer_for_size(det_size: tuple[int, int]):
    import cv2
    from insightface.app import FaceAnalysis

    analyzer = FaceAnalysis(name="buffalo_l")
    try:
        import torch

        ctx_id = 0 if torch.cuda.is_available() else -1
    except ImportError:
        ctx_id = -1
    analyzer.prepare(ctx_id=ctx_id, det_size=det_size)
    return analyzer, cv2


def _get_face_analyzer():
    return _get_face_analyzer_for_size((640, 640))


def _read_faces(path: Path):
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return None, None
    for det_size in ((640, 640), (320, 320)):
        analyzer, _cv2 = _get_face_analyzer_for_size(det_size)
        faces = analyzer.get(img)
        if faces:
            return faces, img
    return None, img


def _face_gender_vote(face) -> Optional[tuple[GenderLabel, float]]:
    sex = getattr(face, "sex", None)
    weight = float(getattr(face, "det_score", 0.5) or 0.5)
    weight = max(0.05, min(1.0, weight))
    if sex == "M":
        return "male", weight
    if sex == "F":
        return "female", weight
    gender_attr = getattr(face, "gender", None)
    if gender_attr == 1:
        return "male", weight
    if gender_attr == 0:
        return "female", weight
    return None


def _deepface_gender_vote(image_path: Path) -> Optional[tuple[GenderLabel, float]]:
    try:
        from deepface import DeepFace
    except ImportError:
        return None
    try:
        results = DeepFace.analyze(
            str(image_path),
            actions=["gender"],
            enforce_detection=False,
            silent=True,
        )
    except Exception as exc:
        logger.debug("DeepFace fallito su %s: %s", image_path.name, exc)
        return None
    result = results[0] if isinstance(results, list) and results else results
    gender_map = result.get("gender") if isinstance(result, dict) else None
    if not isinstance(gender_map, dict) or not gender_map:
        return None
    woman = float(gender_map.get("Woman", gender_map.get("Female", 0.0)) or 0.0)
    man = float(gender_map.get("Man", gender_map.get("Male", 0.0)) or 0.0)
    total = woman + man
    if total <= 0:
        return None
    if woman >= man:
        return "female", woman / total
    return "male", man / total


def detect_gender_from_faces(
    image_paths: Sequence[Union[str, Path]],
    *,
    certainty_threshold: float = CERTAINTY_THRESHOLD,
) -> GenderResult:
    """
    Vote male/female across face crops using InsightFace gender/sex attributes.

    Uses ``face.sex`` (M/F) when available, else ``face.gender`` (1=male, 0=female).
    Votes are weighted by face detection confidence (``det_score``).
    """
    votes: Dict[str, float] = {"male": 0.0, "female": 0.0}
    analyzed = 0
    method = "insightface"

    try:
        _get_face_analyzer()
        insightface_ok = True
    except ImportError as exc:
        insightface_ok = False
        logger.warning("InsightFace unavailable: %s", exc)

    for raw_path in image_paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        vote: Optional[tuple[GenderLabel, float]] = None
        if insightface_ok:
            faces, _img = _read_faces(path)
            if faces:
                face = max(
                    faces,
                    key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
                )
                vote = _face_gender_vote(face)
        if vote is None:
            vote = _deepface_gender_vote(path)
            if vote is not None:
                method = "deepface"
        if vote is None:
            continue

        label, weight = vote
        votes[label] = votes.get(label, 0.0) + weight
        analyzed += 1

    total = votes["male"] + votes["female"]
    if total == 0:
        return GenderResult(
            gender="unknown",
            confidence=0.0,
            votes={"male": 0, "female": 0},
            reason=f"no faces detected in {analyzed} analyzed images",
        )

    male_ratio = votes["male"] / total
    female_ratio = votes["female"] / total
    if male_ratio >= female_ratio:
        winner: GenderLabel = "male"
        confidence = male_ratio
        winner_weight = votes["male"]
    else:
        winner = "female"
        confidence = female_ratio
        winner_weight = votes["female"]

    reason = (
        f"{method} weighted vote over {analyzed} images "
        f"({winner}={winner_weight:.2f}/{total:.2f})"
    )
    gender: GenderLabel = winner if confidence >= certainty_threshold else "unknown"
    if gender == "unknown":
        reason = (
            f"{reason}; below certainty threshold {certainty_threshold:.2f} "
            f"({winner}={confidence:.0%})"
        )
    return GenderResult(
        gender=gender,
        confidence=confidence,
        votes={"male": int(round(votes["male"])), "female": int(round(votes["female"]))},
        reason=reason,
    )


def detect_gender_from_folder(
    folder: Union[str, Path],
    *,
    max_images: int = 30,
    certainty_threshold: float = CERTAINTY_THRESHOLD,
    recursive: bool = True,
    subject_label: str = "",
) -> GenderResult:
    folder_path = Path(folder)
    image_paths = list(
        _iter_face_images(folder_path, max_images=max_images, recursive=recursive)
    )
    if not image_paths:
        label = subject_label or str(folder_path)
        return GenderResult(
            gender="unknown",
            confidence=0.0,
            votes={"male": 0, "female": 0},
            reason=f"no images in {label}",
        )
    return detect_gender_from_faces(
        image_paths,
        certainty_threshold=certainty_threshold,
    )


def gender_from_metadata_or_file(
    *,
    gender_json_path: Optional[Union[str, Path]] = None,
    metadata: Optional[dict] = None,
) -> GenderLabel:
    if gender_json_path is not None:
        cached = load_gender_json(gender_json_path)
        if cached and cached.gender in ("male", "female"):
            return cached.gender
    if metadata:
        g = _normalize_gender(metadata.get("subject_gender", metadata.get("gender")))
        if g in ("male", "female"):
            return g
    return "unknown"


def gender_portrait_label(gender: GenderLabel) -> str:
    if gender == "male":
        return "male portrait"
    if gender == "female":
        return "female portrait"
    return "portrait"


def subject_pronoun(gender: GenderLabel, *, form: str = "subject") -> str:
    if gender == "male":
        return {"subject": "He", "object": "him", "possessive": "his"}.get(form, "He")
    if gender == "female":
        return {"subject": "She", "object": "her", "possessive": "her"}.get(form, "She")
    return {"subject": "They", "object": "them", "possessive": "their"}.get(form, "They")


def build_fullbody_prompt(trigger: str, gender: GenderLabel) -> str:
    desc = GenderPromptDescriptors.for_gender(gender)
    if gender == "unknown":
        return (
            f"A full-body photograph of {trigger} standing in a sunlit, modern "
            "minimalist apartment living room. The person is looking at the camera with a relaxed "
            "expression. Wearing casual jeans and a white t-shirt. Natural light coming from "
            "a large window. Photorealistic, 8k resolution, highly detailed."
        )
    return (
        f"A full-body photograph of {trigger}, {desc.biological}, standing in a sunlit, modern "
        f"minimalist apartment living room. {desc.pronoun} is looking at the camera with a relaxed "
        f"expression. Wearing casual jeans and a white t-shirt. Natural light coming from "
        f"a large window. Photorealistic, 8k resolution, highly detailed."
    )


def resolve_gender(
    detected: GenderResult,
    *,
    subject_label: str = "Subject",
    gender_override: Optional[str] = None,
    expected_gender: Optional[str] = None,
    interactive: bool = True,
    dry_run: bool = False,
) -> GenderResult:
    """Apply CLI override, optional prompt when uncertain, and expected-gender guard."""
    if gender_override:
        gender = _normalize_gender(gender_override)
        if gender is None:
            raise ValueError(f"Invalid --gender value: {gender_override!r}")
        result = GenderResult(
            gender=gender,
            confidence=1.0,
            votes={gender: 1},
            reason=f"CLI override --gender={gender}",
        )
    elif detected.is_certain():
        result = detected
    elif interactive and not dry_run:
        print(
            f"\nGenere non determinabile con certezza per {subject_label}. "
            f"(rilevato: {detected.gender}, conf={detected.confidence:.2f})"
        )
        while True:
            answer = input("[M]aschio / [F]emmina / [S]alta: ").strip().lower()
            if answer in {"skip", "s", "salta", ""}:
                result = detected
                break
            gender = _normalize_gender(answer)
            if gender == "male" or answer in {"m", "maschio"}:
                result = GenderResult(
                    gender="male",
                    confidence=1.0,
                    votes={"male": 1, "female": 0},
                    reason=f"confermato dall'utente (male) per {subject_label}",
                )
                break
            if gender == "female" or answer in {"f", "femmina"}:
                result = GenderResult(
                    gender="female",
                    confidence=1.0,
                    votes={"male": 0, "female": 1},
                    reason=f"confermato dall'utente (female) per {subject_label}",
                )
                break
            print("Risposta non valida. Usa M, F o S.")
    else:
        result = detected

    if expected_gender:
        expected = _normalize_gender(expected_gender)
        if expected and result.gender not in {expected, "unknown"}:
            raise ValueError(
                f"Genere rilevato {result.gender!r} non corrisponde a "
                f"expected {expected!r} per {subject_label}"
            )
    return result


def _gender_suffix(subject_gender: GenderLabel) -> str:
    if subject_gender == "male":
        return ", adult male"
    if subject_gender == "female":
        return ", adult female"
    return ""


def build_v3_face_caption(trigger_word: str, subject_gender: GenderLabel) -> str:
    base = (
        f"ohwx {trigger_word}, face portrait, photorealistic, "
        "sharp focus, neutral expression"
    )
    return base + _gender_suffix(subject_gender)


def build_v3_tier_caption(
    tier: str,
    trigger_word: str,
    subject_gender: GenderLabel,
    *,
    detail_hint: str = "",
) -> str:
    """Build gender-aware caption for tier A-F LoRA export."""
    gender = _gender_suffix(subject_gender)
    hint = detail_hint.strip().lower()
    templates: dict[str, str] = {
        "A": (
            f"ohwx {trigger_word}, face portrait, frontal view, photorealistic, "
            f"sharp focus, neutral expression{gender}"
        ),
        "B": (
            f"ohwx {trigger_word}, face portrait, three-quarter profile view, "
            f"photorealistic, sharp focus, natural skin{gender}"
        ),
        "C": (
            f"ohwx {trigger_word}, back view, rear angle, full figure, "
            f"photorealistic, natural skin, anatomically correct{gender}"
        ),
        "D": (
            f"ohwx {trigger_word}, full body portrait, head to toe, standing, "
            f"photorealistic, anatomically correct{gender}"
        ),
        "E": (
            f"ohwx {trigger_word}, partial body portrait, bust waist legs torso, "
            f"photorealistic, natural skin, anatomically correct{gender}"
        ),
        "F": (
            f"ohwx {trigger_word}, macro detail photograph, extreme close-up, "
            f"photorealistic, {hint or 'anatomical detail'}, "
            f"high detail, anatomically correct{gender}"
        ),
    }
    return templates.get(tier, build_v3_face_caption(trigger_word, subject_gender))


def build_vip_face_caption(trigger_word: str, subject_gender: GenderLabel) -> str:
    base = (
        f"ohwx {trigger_word}, VIP face portrait, photorealistic, "
        "sharp focus, frontal, natural skin, high quality"
    )
    if subject_gender == "male":
        return f"{base}, adult male"
    if subject_gender == "female":
        return f"{base}, adult female"
    return base


def outputs_gender_path(subject_num: int) -> Path:
    return Path("outputs") / f"gender_soggetto{subject_num}.json"


def resolve_subject_gender(
    *,
    gender_json: Optional[Union[str, Path]] = None,
    subject_folder: Optional[Union[str, Path]] = None,
    face_paths: Optional[Sequence[Union[str, Path]]] = None,
    override: Optional[str] = None,
    certainty_threshold: float = CERTAINTY_THRESHOLD,
    write_json: bool = False,
) -> GenderResult:
    """
    Resolve subject gender: CLI override > gender.json > InsightFace detection.

    When ``write_json`` is True and detection runs, persists result to ``gender_json``.
    """
    if override:
        gender = _normalize_gender(override)
        if gender is None:
            raise ValueError(f"Invalid --gender value: {override!r}")
        return GenderResult(
            gender=gender,
            confidence=1.0,
            votes={gender: 1},
            reason=f"CLI override --gender={gender}",
        )

    if gender_json is not None:
        cached = load_gender_json(gender_json)
        if cached is not None:
            logger.info(
                "Gender from %s: %s (confidence=%.2f)",
                gender_json,
                cached.gender,
                cached.confidence,
            )
            return cached

    if face_paths:
        result = detect_gender_from_faces(
            face_paths,
            certainty_threshold=certainty_threshold,
        )
    elif subject_folder is not None:
        result = detect_gender_from_folder(
            subject_folder,
            certainty_threshold=certainty_threshold,
        )
    else:
        result = GenderResult(
            gender="unknown",
            confidence=0.0,
            votes={"male": 0, "female": 0},
            reason="no gender source provided",
        )

    if write_json and gender_json is not None and result.gender != "unknown":
        save_gender_json(gender_json, result)

    logger.info(
        "Detected gender=%s confidence=%.2f (%s)",
        result.gender,
        result.confidence,
        result.reason,
    )
    return result


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Rilevamento genere da cartella soggetto")
    parser.add_argument("folder", type=Path, help="Cartella immagini da analizzare")
    parser.add_argument("--subject", default="", help="Etichetta soggetto (es. Soggetto 2)")
    parser.add_argument("--gender", default=None, help="Forza genere male|female")
    parser.add_argument("--expected-gender", default=None, help="Valida genere atteso")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-images", type=int, default=30)
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--save", type=Path, default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    raw = detect_gender_from_folder(
        args.folder,
        max_images=max(1, args.max_images),
        recursive=not args.no_recursive,
        subject_label=args.subject or args.folder.name,
    )
    try:
        resolved = resolve_gender(
            raw,
            subject_label=args.subject or args.folder.name,
            gender_override=args.gender,
            expected_gender=args.expected_gender,
            interactive=not args.non_interactive,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    save_path = args.save
    if save_path is None and not args.dry_run:
        save_path = Path("outputs") / f"gender_{args.folder.name.replace(' ', '_')}.json"
    if save_path and not args.dry_run:
        save_gender_json(save_path, resolved)

    print("\n=== Gender detection ===")
    print(f"Folder: {args.folder}")
    print(f"Gender: {resolved.gender}")
    print(f"Confidence: {resolved.confidence:.2f}")
    print(f"Votes: {resolved.votes}")
    print(f"Reason: {resolved.reason}")
    if save_path and not args.dry_run:
        print(f"Saved: {save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
