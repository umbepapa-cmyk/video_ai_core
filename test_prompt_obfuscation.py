"""
Unit tests for prompt_obfuscation (Fase 3.4).

Run: python test_prompt_obfuscation.py
"""

from prompt_obfuscation import (
    PROMPT_OBFUSCATION_MAP,
    is_content_policy_error,
    obfuscate_prompt,
)


def test_nude_replaced_case_insensitive():
    original = "woman dancing nude in rain"
    result = obfuscate_prompt(original)
    assert "nude" not in result.lower(), f"Trigger still present: {result!r}"
    assert "anatomical life drawing reference" in result.lower()
    print(f"[OK] nude obfuscated: {result!r}")


def test_rain_dance_prompt():
    original = "A woman dancing nude in the rain on empty city streets at dawn"
    result = obfuscate_prompt(original)
    assert "nude" not in result.lower()
    assert "bare skin" in result.lower() or "anatomical" in result.lower()
    print(f"[OK] rain dance prompt obfuscated (no trigger words)")


def test_case_insensitive():
    for variant in ("NUDE", "Nude", "nUdE"):
        result = obfuscate_prompt(f"figure {variant} study")
        assert "nude" not in result.lower()
    print("[OK] case insensitive replacement")


def test_empty_and_clean_prompts():
    assert obfuscate_prompt("") == ""
    clean = "A woman dancing in the rain at dawn, cinematic light"
    assert obfuscate_prompt(clean) == clean
    print("[OK] empty and clean prompts unchanged")


def test_italian_variants():
    result = obfuscate_prompt("donna nuda sotto la pioggia")
    assert "nuda" not in result.lower()
    print(f"[OK] Italian variant obfuscated: {result!r}")


def test_is_content_policy_error():
    class FakePolicyError(Exception):
        pass

    err = FakePolicyError(
        "[{'type': 'content_policy_violation', "
        "'msg': 'flagged by a content checker.'}]"
    )
    assert is_content_policy_error(err) is True
    assert is_content_policy_error(ValueError("not found")) is False
    print("[OK] is_content_policy_error detection")


def test_mapping_has_common_triggers():
    for key in ("nude", "naked", "nsfw", "nuda", "nudo"):
        assert key in PROMPT_OBFUSCATION_MAP
    print(f"[OK] mapping has {len(PROMPT_OBFUSCATION_MAP)} trigger entries")


if __name__ == "__main__":
    test_nude_replaced_case_insensitive()
    test_rain_dance_prompt()
    test_case_insensitive()
    test_empty_and_clean_prompts()
    test_italian_variants()
    test_is_content_policy_error()
    test_mapping_has_common_triggers()
    print("\nAll prompt obfuscation tests passed.")
