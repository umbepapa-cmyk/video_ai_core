"""
Prompt enhancement helpers — Fase 3.8 body consistency injection.
"""

from __future__ import annotations

BODY_CONSISTENCY_SUFFIX = (
    ", maintaining the EXACT body type, gender, skin tone, and clothing style "
    "of the provided reference image. The subject's physical proportions must "
    "perfectly match the reference image."
)

OUTPAINTING_SUFFIX = (
    ", full body visible, complete head, generative fill outpainting, "
    "seamless background, centered subject"
)


def inject_outpainting_prompt(prompt: str, canvas_expanded: bool = False) -> str:
    """Append outpainting suffix when canvas expansion was applied (Fase 3.14)."""
    if canvas_expanded and OUTPAINTING_SUFFIX.strip() not in prompt:
        return prompt.rstrip(". ") + OUTPAINTING_SUFFIX
    return prompt


def inject_body_consistency_prompt(prompt: str, mode: str = "v2v") -> str:
    """Append body-consistency suffix for V2V when not already present."""
    if mode == "v2v" and BODY_CONSISTENCY_SUFFIX.strip() not in prompt:
        return prompt.rstrip(". ") + BODY_CONSISTENCY_SUFFIX
    return prompt
