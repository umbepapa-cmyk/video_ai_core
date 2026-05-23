"""
ETA / countdown tracking for Fal.ai generation steps.

Provides baseline time estimates and console countdown during queue polling.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float, str], None]

# Configurable baseline estimates (seconds): (low, high)
DEFAULT_ESTIMATES: Dict[str, Tuple[float, float]] = {
    "first_frame_flux": (60, 120),
    "i2v_draft_2s": (120, 180),
    "i2v_5s": (180, 360),
    "i2v_10s_2segments": (600, 1200),
}

TICK_INTERVAL_S = 12.0
POLL_INTERVAL_S = 2.0


def format_mmss(seconds: float) -> str:
    """Format seconds as M:SS."""
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def format_eta_range(low: float, high: float) -> str:
    """Human-readable ETA range, collapsing to minutes when large."""
    if high >= 600:
        low_min = max(1, int(round(low / 60)))
        high_min = max(low_min, int(round(high / 60)))
        return f"~{low_min}-{high_min} min"
    if low == high:
        return f"~{format_mmss(low)}"
    return f"~{format_mmss(low)}-{format_mmss(high)}"


def estimate_i2v_seconds(
    duration: float,
    *,
    draft_mode: bool = False,
    segment_index: int = 1,
    segment_total: int = 1,
) -> float:
    """Midpoint baseline estimate for a single I2V job."""
    if draft_mode and duration <= 3.0:
        low, high = DEFAULT_ESTIMATES["i2v_draft_2s"]
    elif segment_total >= 2 and duration >= 7.5:
        low, high = DEFAULT_ESTIMATES["i2v_10s_2segments"]
        per_seg = ((low / segment_total) + (high / segment_total)) / 2
        return per_seg
    elif duration <= 5.5:
        low, high = DEFAULT_ESTIMATES["i2v_5s"]
    else:
        low, high = DEFAULT_ESTIMATES["i2v_10s_2segments"]
        if segment_total > 1:
            return (low + high) / (2 * segment_total)
    return (low + high) / 2


def estimate_first_frame_seconds(*, draft_mode: bool = False) -> float:
    """Midpoint baseline for Flux first-frame generation."""
    low, high = DEFAULT_ESTIMATES["first_frame_flux"]
    if draft_mode:
        low *= 0.75
        high *= 0.75
    return (low + high) / 2


def estimate_pipeline_seconds(
    duration_seconds: float,
    *,
    draft_mode: bool = False,
    autoregressive: bool = False,
    segment_duration: float = 5.0,
    include_first_frame: bool = True,
) -> Tuple[float, float]:
    """Return (low, high) total pipeline ETA in seconds."""
    low = 0.0
    high = 0.0

    if include_first_frame:
        ff_low, ff_high = DEFAULT_ESTIMATES["first_frame_flux"]
        if draft_mode:
            ff_low *= 0.75
            ff_high *= 0.75
        low += ff_low
        high += ff_high

    if autoregressive and duration_seconds > segment_duration:
        num_segments = max(2, math.ceil(duration_seconds / segment_duration))
        if duration_seconds >= 10 and num_segments >= 2:
            i2v_low, i2v_high = DEFAULT_ESTIMATES["i2v_10s_2segments"]
        else:
            per_low, per_high = DEFAULT_ESTIMATES["i2v_5s"]
            i2v_low = per_low * num_segments
            i2v_high = per_high * num_segments
    elif draft_mode and duration_seconds <= 3.0:
        i2v_low, i2v_high = DEFAULT_ESTIMATES["i2v_draft_2s"]
    elif duration_seconds <= 5.5:
        i2v_low, i2v_high = DEFAULT_ESTIMATES["i2v_5s"]
    else:
        i2v_low, i2v_high = DEFAULT_ESTIMATES["i2v_10s_2segments"]

    low += i2v_low
    high += i2v_high
    return low, high


class GenerationProgressTracker:
    """Tracks elapsed/remaining time and logs ETA on a fixed interval."""

    def __init__(
        self,
        estimated_seconds: float,
        label: str,
        *,
        tick_interval: float = TICK_INTERVAL_S,
        on_progress: Optional[ProgressCallback] = None,
        step_info: str = "",
    ):
        self.start = time.time()
        self.estimated = estimated_seconds
        self.label = label
        self.tick_interval = tick_interval
        self.on_progress = on_progress
        self.step_info = step_info
        self._last_tick = 0.0
        self._fal_progress: Optional[float] = None

    def update_fal_status(self, status: Any) -> None:
        """Refine ETA using Fal queue status / logs when available."""
        try:
            import fal_client

            if isinstance(status, fal_client.Queued):
                queue_penalty = min(90.0, max(0, status.position) * 8.0)
                self.estimated += queue_penalty
            elif isinstance(status, fal_client.InProgress) and status.logs:
                for log in status.logs:
                    msg = str(log.get("message", ""))
                    pct = re.search(r"(\d+)\s*%", msg)
                    if pct:
                        self._fal_progress = int(pct.group(1)) / 100.0
                        return
                    step = re.search(r"step\s*(\d+)\s*/\s*(\d+)", msg, re.I)
                    if step:
                        current, total = int(step.group(1)), int(step.group(2))
                        if total > 0:
                            self._fal_progress = current / total
                        return
            elif isinstance(status, fal_client.Completed) and status.metrics:
                infer_time = status.metrics.get("inference_time")
                if isinstance(infer_time, (int, float)) and infer_time > 0:
                    self._fal_progress = 1.0
        except Exception:
            pass

    def remaining_seconds(self) -> float:
        elapsed = time.time() - self.start
        if self._fal_progress is not None and 0.0 < self._fal_progress < 1.0:
            projected_total = elapsed / self._fal_progress
            return max(0.0, projected_total - elapsed)
        return max(0.0, self.estimated - elapsed)

    def tick(self, *, force: bool = False) -> bool:
        """Log ETA if tick interval elapsed. Returns True when a line was logged."""
        now = time.time()
        if not force and self._last_tick and (now - self._last_tick) < self.tick_interval:
            return False

        self._last_tick = now
        elapsed = now - self.start
        remaining = self.remaining_seconds()
        step_suffix = f" ({self.step_info})" if self.step_info else ""
        logger.info(
            "[ETA] %s: ~%s rimanenti (%s trascorsi)%s",
            self.label,
            format_mmss(remaining),
            format_mmss(elapsed),
            step_suffix,
        )
        if self.on_progress:
            self.on_progress(elapsed, remaining, self.label)
        return True


async def submit_and_wait_with_eta(
    handler: Any,
    estimated_seconds: float,
    label: str,
    *,
    timeout: float,
    poll_interval: float = POLL_INTERVAL_S,
    tick_interval: float = TICK_INTERVAL_S,
    on_progress: Optional[ProgressCallback] = None,
    step_info: str = "",
) -> Dict[str, Any]:
    """
    Poll a Fal AsyncRequestHandle until complete, logging ETA periodically.

    Returns the result JSON dict from Fal.
    """
    import fal_client

    tracker = GenerationProgressTracker(
        estimated_seconds,
        label,
        tick_interval=tick_interval,
        on_progress=on_progress,
        step_info=step_info,
    )
    deadline = time.time() + timeout
    tracker.tick(force=True)

    while True:
        if time.time() > deadline:
            raise asyncio.TimeoutError(
                f"{label} timed out after {timeout:.0f}s"
            )

        status = await handler.status(with_logs=True)
        tracker.update_fal_status(status)
        tracker.tick()

        if isinstance(status, fal_client.Completed):
            if status.error:
                raise RuntimeError(
                    f"{label} failed: {status.error_type or 'error'} — {status.error}"
                )
            break

        await asyncio.sleep(poll_interval)

    result = await handler.get()
    if not isinstance(result, dict):
        return {"video": result} if result else {}
    return result
