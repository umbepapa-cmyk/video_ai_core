#!/usr/bin/env python3
"""
Fase 3.8 — body consistency prompt injection tests.

Run:
    python test_prompt_enhancement.py
"""

from __future__ import annotations

import logging

from prompt_enhancement import (
    BODY_CONSISTENCY_SUFFIX,
    inject_body_consistency_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def test_v2v_suffix_appended():
    prompt = "A dancer spinning in the rain"
    result = inject_body_consistency_prompt(prompt, mode="v2v")
    assert BODY_CONSISTENCY_SUFFIX.strip() in result
    assert result.startswith(prompt)
    logger.info("[OK] V2V suffix appended: ...%s", result[-60:])


def test_v2v_idempotent():
    prompt = inject_body_consistency_prompt("cinematic motion", mode="v2v")
    again = inject_body_consistency_prompt(prompt, mode="v2v")
    assert prompt == again
    logger.info("[OK] V2V suffix not duplicated")


def test_i2v_unchanged():
    prompt = "smooth camera pan"
    result = inject_body_consistency_prompt(prompt, mode="i2v")
    assert result == prompt
    logger.info("[OK] I2V prompt unchanged")


def main() -> None:
    test_v2v_suffix_appended()
    test_v2v_idempotent()
    test_i2v_unchanged()
    logger.info("All prompt enhancement tests passed.")


if __name__ == "__main__":
    main()
