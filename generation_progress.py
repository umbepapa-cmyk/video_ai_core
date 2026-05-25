"""ETA helpers and Fal queue polling with progress callbacks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float, float], None]


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def format_eta_range(low: float, high: float) -> str:
    return f"~{_fmt_seconds(low)}-{_fmt_seconds(high)}"


def estimate_first_frame_seconds(*, draft_mode: bool = False) -> float:
    return 90.0 if draft_mode else 150.0


def estimate_i2v_seconds(
    duration: float,
    *,
    draft_mode: bool = False,
    segment_index: int = 1,
    segment_total: int = 1,
) -> float:
    base = 180.0 if draft_mode else 300.0
    return base + max(0.0, duration - 5.0) * 20.0


def estimate_pipeline_seconds(
    duration_seconds: float,
    *,
    draft_mode: bool = False,
    autoregressive: bool = False,
    segment_duration: float = 5.0,
    include_first_frame: bool = True,
) -> Tuple[float, float]:
    segments = max(1, int(duration_seconds / segment_duration)) if autoregressive else 1
    first = estimate_first_frame_seconds(draft_mode=draft_mode) if include_first_frame else 0.0
    i2v = estimate_i2v_seconds(duration_seconds / segments, draft_mode=draft_mode) * segments
    low = first + i2v * 0.8
    high = first + i2v * 1.4
    return low, high


async def submit_and_wait_with_eta(
    handler: Any,
    estimated_seconds: float,
    stage_label: str,
    *,
    timeout: float = 600.0,
    on_progress: Optional[ProgressCallback] = None,
    step_info: str = "",
    poll_interval: float = 12.0,
) -> Any:
    start = time.time()

    async def _eta_loop() -> None:
        while True:
            await asyncio.sleep(poll_interval)
            elapsed = time.time() - start
            remaining = max(0.0, estimated_seconds - elapsed)
            suffix = f" ({step_info})" if step_info else ""
            logger.info(
                "[ETA] %s%s: ~%s rimanenti (%s trascorsi)",
                stage_label,
                suffix,
                _fmt_seconds(remaining),
                _fmt_seconds(elapsed),
            )
            if on_progress:
                on_progress(stage_label, elapsed, remaining)

    eta_task = asyncio.create_task(_eta_loop())
    try:
        return await asyncio.wait_for(handler.get(), timeout=timeout)
    finally:
        eta_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await eta_task
