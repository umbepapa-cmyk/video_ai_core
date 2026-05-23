#!/usr/bin/env python3
"""
Fase 3.5 — Multi-Provider Fallback Router tests.

Run:
    python test_multi_provider_fallback.py

Live Replicate test (requires REPLICATE_API_TOKEN in .env):
    FORCE_REPLICATE=1 python test_multi_provider_fallback.py --live

Mock tests always run; live test skipped when token is missing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from i2v_router import (
    I2VContext,
    I2VResult,
    MultiProviderFallbackRouter,
    generate_i2v_with_fallback,
)
from provider_adapters import (
    prepare_payload_for_provider,
    prepare_flux_replicate_payload,
    parse_response_for_provider,
)


def test_prepare_fal_payload():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="cinematic motion",
        duration=5.0,
        resolution="720p",
    )
    payload = prepare_payload_for_provider("fal", ctx, endpoint_id="wan21-i2v")
    assert payload["image_url"] == ctx.image_url
    assert payload["prompt"] == ctx.prompt
    assert "num_frames" in payload
    logger.info("[OK] Fal Wan payload: num_frames=%s", payload["num_frames"])


def test_prepare_replicate_payload():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="cinematic motion",
        duration=5.0,
        fps=24,
        resolution="720p",
    )
    payload = prepare_payload_for_provider(
        "replicate", ctx, model_id="wan-video/wan-2.2-i2v-fast"
    )
    assert payload["image"] == ctx.image_url
    assert payload["prompt"] == ctx.prompt
    assert payload["disable_safety_checker"] is True
    logger.info("[OK] Replicate Wan2.2 payload keys: %s", sorted(payload.keys()))


def test_parse_fal_response():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="test",
        duration=5.0,
    )
    raw = {"video": {"url": "https://cdn.example.com/out.mp4"}}
    result = parse_response_for_provider(
        "fal", raw, ctx, endpoint_id="fal-ai/wan-i2v"
    )
    assert result.video_url == "https://cdn.example.com/out.mp4"
    assert result.provider_id == "wan21-i2v"
    logger.info("[OK] parse Fal response")


def test_parse_replicate_response():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="test",
        duration=5.0,
    )
    result = parse_response_for_provider(
        "replicate",
        "https://replicate.delivery/out.mp4",
        ctx,
        model_id="wavespeedai/wan-2.1-i2v-720p",
    )
    assert result.provider_id == "replicate"
    assert result.video_url.endswith(".mp4")
    logger.info("[OK] parse Replicate response")


def test_flux_replicate_payload():
    payload = prepare_flux_replicate_payload(
        "portrait in rain",
        negative_prompt="blur",
        num_inference_steps=25,
    )
    assert payload["disable_safety_checker"] is True
    assert payload["prompt"] == "portrait in rain"
    logger.info("[OK] Flux Replicate payload")


async def test_mock_fal_failure_triggers_replicate():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="test prompt",
        duration=5.0,
    )
    replicate_result = I2VResult(
        video_url="https://replicate.delivery/video.mp4",
        last_frame_url=None,
        duration=5.0,
        endpoint_id="wan-video/wan-2.2-i2v-fast",
        provider_id="replicate",
    )

    router = MultiProviderFallbackRouter(
        fal_api_key="test-fal-key",
        replicate_token="test-replicate-token",
    )

    with patch.object(
        router.fal_router, "generate", new_callable=AsyncMock
    ) as mock_fal:
        mock_fal.side_effect = RuntimeError("All I2V endpoints failed")
        router.fal_router.last_obfuscation_applied = True

        with patch.object(
            router, "_generate_replicate", new_callable=AsyncMock
        ) as mock_rep:
            mock_rep.return_value = replicate_result
            result = await router.generate(ctx)

    mock_fal.assert_awaited_once()
    mock_rep.assert_awaited_once()
    assert result.provider_id == "replicate"
    logger.info("[OK] Fal exhaustion triggers Replicate fallback")


async def test_force_replicate_skips_fal():
    ctx = I2VContext(
        image_url="https://example.com/frame.jpg",
        prompt="test",
        duration=5.0,
    )
    replicate_result = I2VResult(
        video_url="https://replicate.delivery/forced.mp4",
        last_frame_url=None,
        duration=5.0,
        endpoint_id="wavespeedai/wan-2.1-i2v-720p",
        provider_id="replicate",
    )

    router = MultiProviderFallbackRouter(
        fal_api_key="test-fal-key",
        replicate_token="test-replicate-token",
    )

    with patch.object(
        router.fal_router, "generate", new_callable=AsyncMock
    ) as mock_fal:
        with patch.object(
            router, "_generate_replicate", new_callable=AsyncMock
        ) as mock_rep:
            mock_rep.return_value = replicate_result
            result = await router.generate(ctx, force_provider="replicate")

    mock_fal.assert_not_awaited()
    mock_rep.assert_awaited_once()
    assert result.video_url.endswith("forced.mp4")
    logger.info("[OK] force_provider=replicate skips Fal")


async def test_generate_wrapper_force_replicate_env():
    prev = os.environ.get("FORCE_REPLICATE")
    os.environ["FORCE_REPLICATE"] = "1"
    try:
        with patch(
            "i2v_router.MultiProviderFallbackRouter.generate",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = I2VResult(
                video_url="https://replicate.delivery/wrap.mp4",
                last_frame_url=None,
                duration=5.0,
                endpoint_id="wan-video/wan-2.2-i2v-fast",
                provider_id="replicate",
            )
            with patch(
                "i2v_router.ensure_last_frame_url",
                new_callable=AsyncMock,
                return_value="https://cdn.example.com/last.jpg",
            ):
                result = await generate_i2v_with_fallback(
                    image_url="https://example.com/frame.jpg",
                    prompt="test",
                    duration=5.0,
                    require_last_frame=False,
                    replicate_token="test-token",
                )

        assert result["provider_id"] == "replicate"
        call_kwargs = mock_gen.call_args
        assert call_kwargs.kwargs.get("force_provider") == "replicate"
        logger.info("[OK] FORCE_REPLICATE=1 sets replicate force path")
    finally:
        if prev is None:
            os.environ.pop("FORCE_REPLICATE", None)
        else:
            os.environ["FORCE_REPLICATE"] = prev


async def test_live_replicate_optional():
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token or token == "your_replicate_api_token_here":
        logger.warning(
            "[SKIP] Live Replicate test — set REPLICATE_API_TOKEN in .env to run"
        )
        return

    logger.info("Running live Replicate-only I2V (FORCE_REPLICATE=1)...")
    os.environ["FORCE_REPLICATE"] = "1"

    # Minimal public test image (Replicate requires reachable URL or local file)
    test_image = "https://replicate.delivery/pbxt/JYzF8KqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqKqK/test.png"
    # Use a well-known public image instead
    test_image = "https://picsum.photos/seed/appvideoai/1280/720"

    try:
        result = await generate_i2v_with_fallback(
            image_url=test_image,
            prompt="gentle camera pan, cinematic lighting, subtle motion",
            duration=3.0,
            negative_prompt="blur, distortion",
            resolution="480p",
            draft_mode=True,
            require_last_frame=False,
            replicate_token=token,
            provider="replicate",
        )
        logger.info(
            "[OK] Live Replicate I2V: provider=%s endpoint=%s video=%s",
            result.get("provider_id"),
            result.get("endpoint_id"),
            (result.get("video_url") or "")[:80],
        )
    except Exception as exc:
        logger.error("Live Replicate test failed: %s", exc)
        raise


async def main() -> bool:
    run_live = "--live" in sys.argv

    test_prepare_fal_payload()
    test_prepare_replicate_payload()
    test_parse_fal_response()
    test_parse_replicate_response()
    test_flux_replicate_payload()
    await test_mock_fal_failure_triggers_replicate()
    await test_force_replicate_skips_fal()
    await test_generate_wrapper_force_replicate_env()

    if run_live:
        await test_live_replicate_optional()
    else:
        token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        if token and token != "your_replicate_api_token_here":
            logger.info("REPLICATE_API_TOKEN present — run with --live for integration test")
        else:
            logger.info("Code ready — REPLICATE_API_TOKEN needed for live test")

    logger.info("\nAll multi-provider fallback tests passed.")
    return True


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
