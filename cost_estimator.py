"""Lightweight pipeline cost estimation for budget checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class PipelineCostEstimate:
    total_usd: float
    credits_required: int
    num_segments: int
    resolution: str


def estimate_pipeline_cost(config: Dict[str, Any]) -> PipelineCostEstimate:
    duration = float(config.get("duration_seconds", 5))
    segment_duration = float(config.get("segment_duration", 5) or 5)
    autoregressive = bool(config.get("enable_autoregressive", False))
    num_segments = max(1, int(duration / segment_duration)) if autoregressive else 1
    resolution = str(config.get("resolution", "720p"))
    per_segment = 0.275 if resolution == "720p" else 0.15
    total = round(per_segment * num_segments, 4)
    credits = int(total * 500)
    return PipelineCostEstimate(
        total_usd=total,
        credits_required=credits,
        num_segments=num_segments,
        resolution=resolution,
    )
