"""
Prompt sanitization for identity-preserving generation.

When ``subject_gender`` is unset, strips hardcoded gender terms so Flux/PuLID and
I2V models are not steered away from the reference face. When ``subject_gender``
is set, neutral sanitization is skipped and biological sex is forced via explicit
positive/negative injection.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Terms that steer text-to-image models toward a specific gender/appearance.
_GENDER_PATTERNS = [
    (re.compile(r"\bmale\b", re.I), ""),
    (re.compile(r"\bfemale\b", re.I), ""),
    (re.compile(r"\bman\b", re.I), "person"),
    (re.compile(r"\bwoman\b", re.I), "person"),
    (re.compile(r"\bboy\b", re.I), "young person"),
    (re.compile(r"\bgirl\b", re.I), "young person"),
    (re.compile(r"\bhis\b", re.I), "their"),
    (re.compile(r"\bher\b", re.I), "their"),
    (re.compile(r"\bhe\b", re.I), "they"),
    (re.compile(r"\bshe\b", re.I), "they"),
    (re.compile(r"\bhim\b", re.I), "them"),
    (re.compile(r"\bmen\b", re.I), "people"),
    (re.compile(r"\bwomen\b", re.I), "people"),
    (re.compile(r"\bguy\b", re.I), "person"),
    (re.compile(r"\blady\b", re.I), "person"),
    (re.compile(r"\bgentleman\b", re.I), "person"),
]


def sanitize_prompt_gender_neutral(
    text: str,
    *,
    subject_gender: Optional[str] = None,
    field: str = "prompt",
) -> str:
    """Strip conflicting gender terms (neutral mode only)."""
    if not text:
        return text

    cleaned = text
    for pattern, replacement in _GENDER_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+,", ",", cleaned)

    return cleaned


def apply_gender_routing(prompts: Dict[str, str], subject_gender: str) -> Dict[str, str]:
    """Force biological sex via explicit positive/negative prompt injection."""
    gender_key = (subject_gender or "").strip().lower()
    if gender_key not in ("female", "male"):
        return prompts

    out = dict(prompts)

    if gender_key == "female":
        neg_suffix = (
            ", penis, male genitalia, male body, adam's apple, muscular man, "
            "flat chest, boy, man"
        )
        pos_suffix = ", biological female, 1girl, female anatomy, woman"
    else:
        neg_suffix = ", breasts, female genitalia, female body, 1girl, woman"
        pos_suffix = ", biological male, 1boy, male anatomy, man"

    for key in ("prompt", "first_frame_prompt"):
        if key in out and out[key]:
            out[key] = out[key] + pos_suffix

    existing_neg = out.get("negative_prompt", "")
    out["negative_prompt"] = existing_neg + neg_suffix
    return out


def sanitize_prompt_dict(
    prompts: Dict[str, str],
    *,
    subject_gender: Optional[str] = None,
) -> Dict[str, str]:
    """Sanitize prompt fields; skip neutral stripping when subject_gender is set."""
    gender_key = (subject_gender or "").strip().lower()
    if gender_key in ("female", "male"):
        return dict(prompts)

    out = dict(prompts)

    for key in ("prompt", "first_frame_prompt"):
        if key in out and out[key]:
            out[key] = sanitize_prompt_gender_neutral(
                out[key], subject_gender=subject_gender, field=key
            )

    if "negative_prompt" in out and out["negative_prompt"]:
        out["negative_prompt"] = sanitize_prompt_gender_neutral(
            out["negative_prompt"], subject_gender=subject_gender, field="negative_prompt"
        )

    return out
