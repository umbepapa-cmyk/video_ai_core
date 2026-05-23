"""
Minimal tests for cost_estimator (Fase 4).

Run: python test_cost_estimator.py
"""

from cost_estimator import (
    calculate_job_cost,
    calculate_credit_price,
    estimate_pipeline_cost,
    CREDIT_MARKUP_MULTIPLIER,
    CREDITS_PER_USD,
)


def test_single_segment_720p_5s():
    cost = calculate_job_cost(
        duration_seconds=5,
        resolution="720p",
        fps=24,
        endpoint="fal-ai/wan-i2v",
        num_segments=1,
        include_first_frame=True,
    )
    # Flux first frame $0.025 + 5s * $0.05/s = $0.275
    assert cost == 0.275, f"Expected 0.275, got {cost}"
    print(f"[OK] 5s 720p single segment: ${cost:.4f} USD")


def test_autoregressive_10s_2_segments():
    estimate = estimate_pipeline_cost({
        "duration_seconds": 10,
        "resolution": "720p",
        "fps": 24,
        "endpoint": "fal-ai/wan-i2v",
        "segment_duration": 5.0,
        "enable_autoregressive": True,
    })
    assert estimate.num_segments == 2
    # First frame $0.025 + 2 × (5s × $0.05) = $0.525
    assert estimate.total_usd == 0.525, f"Expected 0.525, got {estimate.total_usd}"
    print(f"[OK] 10s autoregressive (2 segments): ${estimate.total_usd:.4f} USD")
    print(f"  Breakdown: {estimate.breakdown}")


def test_credit_price():
    api_cost = 0.525
    credits = calculate_credit_price(api_cost, markup=CREDIT_MARKUP_MULTIPLIER)
    expected = __import__("math").ceil(api_cost * 5.0 * CREDITS_PER_USD)
    assert credits == expected == 263
    print(f"[OK] Credit price for ${api_cost}: {credits} credits")
    print(f"  (markup={CREDIT_MARKUP_MULTIPLIER}, credits_per_usd={CREDITS_PER_USD})")


def test_1080p_multiplier():
    cost_720 = calculate_job_cost(5, "720p", 24, "fal-ai/wan-i2v", 1, True)
    cost_1080 = calculate_job_cost(5, "1080p", 24, "fal-ai/wan-i2v", 1, True)
    assert cost_1080 > cost_720
    print(f"[OK] 1080p (${cost_1080:.4f}) > 720p (${cost_720:.4f}) due to megapixel scaling")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("COST ESTIMATOR — Fase 4 Unit Economics")
    print("=" * 60 + "\n")

    test_single_segment_720p_5s()
    test_autoregressive_10s_2_segments()
    test_credit_price()
    test_1080p_multiplier()

    print("\n" + "=" * 60)
    print("All cost estimator tests passed.")
    print("=" * 60 + "\n")
