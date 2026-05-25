"""Prompt obfuscation fallback for provider content filters."""

from __future__ import annotations


def is_content_policy_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    markers = (
        "content policy",
        "safety",
        "nsfw",
        "violat",
        "blocked",
        "moderation",
        "inappropriate",
    )
    return any(m in msg for m in markers)


def obfuscate_prompt(prompt: str) -> str:
    if not prompt:
        return prompt
    replacements = {
        "nude": "unclothed artistic figure",
        "naked": "unclothed artistic figure",
        "kiss on the lips": "close romantic embrace",
    }
    out = prompt
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    return out
