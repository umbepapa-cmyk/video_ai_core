"""
FASE 4: Unit Economics — Fal.ai cost estimation and credit pricing.

Documented Fal.ai endpoint costs (USD). Update ENDPOINT_COSTS when pricing changes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

# Fal.ai endpoint costs (USD) — documented estimates, update as needed.
ENDPOINT_COSTS: Dict[str, Dict[str, Any]] = {
    "fal-ai/flux/dev": {"type": "image", "cost_per_image": 0.025},
    "fal-ai/hunyuan-video-image-to-video": {"type": "video", "cost_per_second": 0.08},
    "fal-ai/wan-i2v": {"type": "video", "cost_per_second": 0.05},
    "fal-ai/kling-video/v1/standard/image-to-video": {"type": "video", "cost_per_second": 0.06},
    "fal-ai/luma-dream-machine/image-to-video": {"type": "video", "cost_per_second": 0.04},
}

FLUX_ENDPOINT = "fal-ai/flux/dev"
DEFAULT_I2V_ENDPOINT = "fal-ai/wan-i2v"

# Megapixels per frame by resolution (16:9 landscape).
RESOLUTION_MEGAPIXELS: Dict[str, float] = {
    "480p": 0.41,
    "720p": 0.92,
    "1080p": 2.07,
}

BASELINE_MP = RESOLUTION_MEGAPIXELS["720p"]

CREDITS_PER_USD = float(os.getenv("CREDITS_PER_USD", "100"))
CREDIT_MARKUP_MULTIPLIER = float(os.getenv("CREDIT_MARKUP_MULTIPLIER", "5.0"))


def _megapixel_factor(resolution: str) -> float:
    mp = RESOLUTION_MEGAPIXELS.get(resolution, RESOLUTION_MEGAPIXELS["720p"])
    return mp / BASELINE_MP


def _resolve_endpoint(endpoint: Optional[str]) -> str:
    if endpoint and endpoint in ENDPOINT_COSTS:
        return endpoint
    return DEFAULT_I2V_ENDPOINT


def calculate_job_cost(
    duration_seconds: float,
    resolution: str,
    fps: int,
    endpoint: str,
    num_segments: int = 1,
    include_first_frame: bool = True,
) -> float:
    """
    Estimate USD cost for a single generation job.

    Video endpoints bill per second scaled by megapixel factor (720p baseline).
    Autoregressive jobs sum first-frame (Flux) + per-segment video costs.
    """
    del fps  # reserved for future frame-based pricing

    endpoint = _resolve_endpoint(endpoint)
    mp_factor = _megapixel_factor(resolution)
    total = 0.0

    if include_first_frame:
        flux = ENDPOINT_COSTS[FLUX_ENDPOINT]
        total += flux["cost_per_image"] * mp_factor

    video_spec = ENDPOINT_COSTS.get(endpoint, ENDPOINT_COSTS[DEFAULT_I2V_ENDPOINT])
    if video_spec["type"] == "video":
        segment_duration = duration_seconds / max(num_segments, 1)
        per_segment = (
            video_spec["cost_per_second"] * segment_duration * mp_factor
        )
        total += per_segment * max(num_segments, 1)

    return round(total, 4)


def calculate_credit_price(
    api_cost_usd: float,
    markup: Optional[float] = None,
) -> int:
    """
    Convert API cost (USD) to user-facing credits.

    credits = ceil(api_cost_usd * markup * CREDITS_PER_USD)
    Default markup 5.0 with CREDITS_PER_USD=100 → ~80% gross margin.
    """
    markup = markup if markup is not None else CREDIT_MARKUP_MULTIPLIER
    return math.ceil(api_cost_usd * markup * CREDITS_PER_USD)


@dataclass
class JobCostEstimate:
    """Pipeline cost breakdown."""

    total_usd: float
    first_frame_usd: float
    video_usd: float
    num_segments: int
    duration_seconds: float
    resolution: str
    endpoint: str
    credits_required: int
    breakdown: Dict[str, float] = field(default_factory=dict)


def _compute_num_segments(config: Dict[str, Any]) -> int:
    duration = float(config.get("duration_seconds", 10))
    segment_duration = float(config.get("segment_duration", 5.0))
    enable_autoregressive = config.get("enable_autoregressive", True)

    if enable_autoregressive and duration > segment_duration:
        return max(1, math.ceil(duration / segment_duration))
    return 1


def estimate_pipeline_cost(config: Dict[str, Any]) -> JobCostEstimate:
    """
    Estimate full pipeline cost from a generation config dict.

    Expected keys: duration_seconds, resolution, fps, endpoint (optional),
    segment_duration, enable_autoregressive, quality_preset (optional).
    """
    duration = float(config.get("duration_seconds", 10))
    resolution = config.get("resolution", "720p")
    fps = int(config.get("fps", 24))
    endpoint = _resolve_endpoint(config.get("endpoint"))
    num_segments = int(config.get("num_segments", _compute_num_segments(config)))
    include_first_frame = config.get("include_first_frame", True)

    mp_factor = _megapixel_factor(resolution)

    first_frame_usd = 0.0
    if include_first_frame:
        first_frame_usd = round(
            ENDPOINT_COSTS[FLUX_ENDPOINT]["cost_per_image"] * mp_factor, 4
        )

    video_spec = ENDPOINT_COSTS.get(endpoint, ENDPOINT_COSTS[DEFAULT_I2V_ENDPOINT])
    segment_duration = duration / max(num_segments, 1)
    per_segment_usd = (
        video_spec["cost_per_second"] * segment_duration * mp_factor
    )
    video_usd = round(per_segment_usd * num_segments, 4)
    total_usd = round(first_frame_usd + video_usd, 4)
    credits = calculate_credit_price(total_usd)

    breakdown = {
        "first_frame": first_frame_usd,
        "video_segments": video_usd,
        "per_segment_usd": round(per_segment_usd, 4),
    }

    return JobCostEstimate(
        total_usd=total_usd,
        first_frame_usd=first_frame_usd,
        video_usd=video_usd,
        num_segments=num_segments,
        duration_seconds=duration,
        resolution=resolution,
        endpoint=endpoint,
        credits_required=credits,
        breakdown=breakdown,
    )
