#!/usr/bin/env python3
"""
Fase 3.14 — canvas expansion / outpainting pre-processor tests.

Run:
    python test_canvas_expander.py
    pytest test_canvas_expander.py -v
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

try:
    import pytest
except ImportError:
    pytest = None  # type: ignore

from canvas_expander import (
    PADDING_KEYS,
    _empty_padding,
    detect_required_padding,
    expand_video_canvas,
    resolution_for_expanded_canvas,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def test_detect_required_padding_returns_expected_keys(tmp_path: Path):
    missing = tmp_path / "missing.mp4"
    padding = detect_required_padding(missing)
    assert set(padding.keys()) == set(PADDING_KEYS)
    assert all(isinstance(v, float) for v in padding.values())
    logger.info("[OK] detect_required_padding keys: %s", padding)


def test_empty_padding_dict():
    padding = _empty_padding()
    assert padding == {k: 0.0 for k in PADDING_KEYS}


def test_resolution_for_expanded_canvas():
    base = resolution_for_expanded_canvas("720p", _empty_padding())
    assert base == "720p"
    expanded = resolution_for_expanded_canvas(
        "720p",
        {
            "padding_top": 0.20,
            "padding_bottom": 0.20,
            "padding_left": 0.0,
            "padding_right": 0.0,
        },
    )
    assert expanded in ("480p", "580p", "720p")
    logger.info("[OK] expanded resolution label: %s", expanded)


async def _run_expand_no_padding():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "clip.mp4"
        src.write_bytes(b"not-a-real-video")
        result = await expand_video_canvas(src)
        assert result == src


async def _run_expand_skip_ffmpeg():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "clip.mp4"
        src.write_bytes(b"fake")
        original_which = shutil.which

        def _fake_which(name):
            if name == "ffmpeg":
                return None
            return original_which(name)

        shutil.which = _fake_which  # type: ignore
        try:
            result = await expand_video_canvas(src, padding_top=0.20)
            assert result == src
        finally:
            shutil.which = original_which  # type: ignore


if pytest is not None:

    @pytest.mark.asyncio
    async def test_expand_video_canvas_no_padding_returns_original(tmp_path: Path):
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"not-a-real-video")
        result = await expand_video_canvas(src)
        assert result == src

    @pytest.mark.asyncio
    async def test_expand_video_canvas_skips_without_ffmpeg(tmp_path: Path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"fake")
        result = await expand_video_canvas(src, padding_top=0.20)
        assert result == src
        logger.info("[OK] graceful degradation without ffmpeg")


def main() -> None:
    import asyncio

    test_detect_required_padding_returns_expected_keys(Path("."))
    test_empty_padding_dict()
    test_resolution_for_expanded_canvas()
    asyncio.run(_run_expand_no_padding())
    asyncio.run(_run_expand_skip_ffmpeg())
    logger.info("All canvas expander smoke tests passed.")


if __name__ == "__main__":
    main()
