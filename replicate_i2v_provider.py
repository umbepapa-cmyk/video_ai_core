"""
Replicate I2V provider — cloud image-to-video fallback (Fase 3.5).

Used when Fal I2V endpoints are exhausted or content_policy persists after
obfuscation. Invoked by MultiProviderFallbackRouter in i2v_router.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from provider_adapters import (
    DEFAULT_REPLICATE_MODEL_ORDER,
    REPLICATE_MODEL_TIMEOUTS,
    parse_response_for_provider,
    prepare_payload_for_provider,
    resolve_replicate_image_input,
)

from i2v_router import I2VContext, ensure_public_image_url

try:
    import replicate
except ImportError:
    replicate = None  # type: ignore

logger = logging.getLogger(__name__)


def _is_replicate_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "throttled" in msg


async def _replicate_run_with_rate_limit_retry(
    model_id: str,
    model_input: Dict[str, Any],
    *,
    timeout: int,
    max_rate_retries: int = 3,
) -> Any:
    """Run Replicate model; backoff on 429 throttling."""
    last_exc: Optional[BaseException] = None
    for attempt in range(max_rate_retries + 1):
        try:
            return await asyncio.wait_for(
                replicate.async_run(model_id, input=model_input),
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            if _is_replicate_rate_limit(exc) and attempt < max_rate_retries:
                wait_s = 12 * (attempt + 1)
                logger.warning(
                    "[REPLICATE] Rate limited on %s — retry %d/%d in %ds",
                    model_id,
                    attempt + 1,
                    max_rate_retries,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue
            raise
    raise last_exc  # type: ignore[misc]


@dataclass(frozen=True)
class ReplicateModelSpec:
    model_id: str
    timeout: int = 600


def _require_replicate_token(explicit: Optional[str] = None) -> str:
    token = (explicit or os.getenv("REPLICATE_API_TOKEN", "")).strip()
    if not token or token in ("", "your_replicate_api_token_here"):
        raise RuntimeError(
            "REPLICATE_API_TOKEN not configured. "
            "Set it in .env — token from https://replicate.com/account/api-tokens"
        )
    return token


def _model_specs(
    models: Optional[List[ReplicateModelSpec]] = None,
    resolution: str = "720p",
    draft_mode: bool = False,
) -> List[ReplicateModelSpec]:
    if models:
        return models
    order = list(DEFAULT_REPLICATE_MODEL_ORDER)
    if resolution == "480p" or draft_mode:
        preferred = "wavespeedai/wan-2.1-i2v-480p"
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
    return [
        ReplicateModelSpec(
            model_id=mid,
            timeout=REPLICATE_MODEL_TIMEOUTS.get(mid, 600),
        )
        for mid in order
    ]


class ReplicateI2VProvider:
    """Run Replicate I2V models with ordered fallback."""

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token

    def _ensure_client(self) -> str:
        if replicate is None:
            raise RuntimeError(
                "replicate package not installed. Run: pip install replicate>=0.25.0"
            )
        token = _require_replicate_token(self.api_token)
        os.environ.setdefault("REPLICATE_API_TOKEN", token)
        return token

    async def generate(
        self,
        ctx: I2VContext,
        *,
        fal_api_key: Optional[str] = None,
        models: Optional[List[ReplicateModelSpec]] = None,
        obfuscation_applied: bool = False,
    ) -> "I2VResult":
        from i2v_router import I2VResult  # avoid circular at module level

        self._ensure_client()
        ctx = I2VContext(
            image_url=await ensure_public_image_url(ctx.image_url, fal_api_key),
            prompt=ctx.prompt,
            duration=ctx.duration,
            fps=ctx.fps,
            negative_prompt=ctx.negative_prompt,
            motion_preset=ctx.motion_preset,
            resolution=ctx.resolution,
            timeout_multiplier=ctx.timeout_multiplier,
            draft_mode=ctx.draft_mode,
            stage_label=ctx.stage_label,
            segment_index=ctx.segment_index,
            segment_total=ctx.segment_total,
            on_progress=ctx.on_progress,
            identity_vector=ctx.identity_vector,
            identity_adapter_strength=ctx.identity_adapter_strength,
            reference_image_url=ctx.reference_image_url,
            face_reference_url=ctx.face_reference_url,
            full_body_reference_url=ctx.full_body_reference_url,
            ip_adapter_image=ctx.ip_adapter_image,
            controlnet_video_url=ctx.controlnet_video_url,
            pose_map_url=ctx.pose_map_url,
            num_inference_steps=ctx.num_inference_steps,
            motion_reference_video_path=ctx.motion_reference_video_path,
            motion_reference_video_url=ctx.motion_reference_video_url,
            canvas_expanded=getattr(ctx, "canvas_expanded", False),
            canvas_padding=getattr(ctx, "canvas_padding", None),
        )

        if ctx.generation_mode == "v2v":
            return await self._generate_v2v(
                ctx,
                fal_api_key=fal_api_key,
                obfuscation_applied=obfuscation_applied,
            )

        specs = _model_specs(models, ctx.resolution, ctx.draft_mode)
        last_error: Optional[BaseException] = None
        timeout_scale = max(0.25, min(1.0, ctx.timeout_multiplier))

        for spec in specs:
            effective_timeout = max(120, int(spec.timeout * timeout_scale))
            file_handle = None
            try:
                image_input = resolve_replicate_image_input(ctx.image_url)
                if hasattr(image_input, "read") and not isinstance(image_input, str):
                    file_handle = image_input
                    ctx_with_image = I2VContext(
                        image_url=image_input,
                        prompt=ctx.prompt,
                        duration=ctx.duration,
                        fps=ctx.fps,
                        negative_prompt=ctx.negative_prompt,
                        motion_preset=ctx.motion_preset,
                        resolution=ctx.resolution,
                        timeout_multiplier=ctx.timeout_multiplier,
                        draft_mode=ctx.draft_mode,
                        stage_label=ctx.stage_label,
                        segment_index=ctx.segment_index,
                        segment_total=ctx.segment_total,
                        on_progress=ctx.on_progress,
                        identity_vector=ctx.identity_vector,
                        identity_adapter_strength=ctx.identity_adapter_strength,
                        reference_image_url=ctx.reference_image_url,
                        face_reference_url=ctx.face_reference_url,
                        full_body_reference_url=ctx.full_body_reference_url,
                        ip_adapter_image=ctx.ip_adapter_image,
                        controlnet_video_url=ctx.controlnet_video_url,
                        pose_map_url=ctx.pose_map_url,
                        num_inference_steps=ctx.num_inference_steps,
                        motion_reference_video_path=ctx.motion_reference_video_path,
                        motion_reference_video_url=ctx.motion_reference_video_url,
                        canvas_expanded=getattr(ctx, "canvas_expanded", False),
                        canvas_padding=getattr(ctx, "canvas_padding", None),
                    )
                else:
                    ctx_with_image = ctx

                model_input = prepare_payload_for_provider(
                    "replicate",
                    ctx_with_image,
                    model_id=spec.model_id,
                )
                if "image" in model_input and file_handle is not None:
                    model_input["image"] = file_handle
                if "first_frame_image" in model_input and file_handle is not None:
                    model_input["first_frame_image"] = file_handle

                from provider_adapters import log_payload_debug

                log_payload_debug(spec.model_id, model_input)
                logger.info(
                    "Replicate I2V [%s] duration=%.1fs resolution=%s timeout=%ds",
                    spec.model_id,
                    ctx.duration,
                    ctx.resolution,
                    effective_timeout,
                )
                output = await _replicate_run_with_rate_limit_retry(
                    spec.model_id,
                    model_input,
                    timeout=effective_timeout,
                )
                parsed = parse_response_for_provider(
                    "replicate",
                    output,
                    ctx,
                    model_id=spec.model_id,
                    obfuscation_applied=obfuscation_applied,
                )
                logger.info(
                    "Replicate I2V success [%s] video_url=%s",
                    spec.model_id,
                    parsed.video_url[:80] + "..."
                    if len(parsed.video_url) > 80
                    else parsed.video_url,
                )
                return parsed
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[WARN] Replicate model %s failed (%s), trying next...",
                    spec.model_id,
                    exc,
                )
                continue
            finally:
                if file_handle is not None:
                    try:
                        file_handle.close()
                    except Exception:
                        pass

        raise RuntimeError(
            f"All Replicate I2V models failed. Last error: {last_error}"
        ) from last_error

    async def _generate_v2v(
        self,
        ctx: I2VContext,
        *,
        fal_api_key: Optional[str] = None,
        obfuscation_applied: bool = False,
    ) -> "I2VResult":
        from i2v_router import I2VResult, _upload_motion_reference

        from provider_adapters import (
            DEFAULT_REPLICATE_V2V_MODEL_ORDER,
            REPLICATE_V2V_MODEL_TIMEOUTS,
            log_payload_debug,
            prepare_v2v_payload_replicate,
        )

        if ctx.motion_reference_video_url:
            motion_url = ctx.motion_reference_video_url
            if not (
                isinstance(motion_url, str)
                and motion_url.startswith(("http://", "https://"))
            ):
                motion_url = await _upload_motion_reference(motion_url, fal_api_key)
        elif ctx.motion_reference_video_path:
            motion_url = await _upload_motion_reference(
                ctx.motion_reference_video_path, fal_api_key
            )
        else:
            raise ValueError("V2V Replicate requires a motion reference video")

        ctx = I2VContext(
            image_url=ctx.image_url,
            prompt=ctx.prompt,
            duration=ctx.duration,
            fps=ctx.fps,
            negative_prompt=ctx.negative_prompt,
            motion_preset=ctx.motion_preset,
            resolution=ctx.resolution,
            timeout_multiplier=ctx.timeout_multiplier,
            draft_mode=ctx.draft_mode,
            stage_label=ctx.stage_label,
            segment_index=ctx.segment_index,
            segment_total=ctx.segment_total,
            on_progress=ctx.on_progress,
            identity_vector=ctx.identity_vector,
            identity_adapter_strength=ctx.identity_adapter_strength,
            reference_image_url=ctx.reference_image_url,
            face_reference_url=ctx.face_reference_url,
            full_body_reference_url=ctx.full_body_reference_url,
            ip_adapter_image=ctx.ip_adapter_image,
            controlnet_video_url=ctx.controlnet_video_url,
            pose_map_url=ctx.pose_map_url,
            num_inference_steps=ctx.num_inference_steps,
            motion_reference_video_path=ctx.motion_reference_video_path,
            motion_reference_video_url=motion_url,
            canvas_expanded=getattr(ctx, "canvas_expanded", False),
            canvas_padding=getattr(ctx, "canvas_padding", None),
        )

        last_error: Optional[BaseException] = None
        timeout_scale = max(0.25, min(1.0, ctx.timeout_multiplier))

        for model_id in DEFAULT_REPLICATE_V2V_MODEL_ORDER:
            effective_timeout = max(
                120, int(REPLICATE_V2V_MODEL_TIMEOUTS.get(model_id, 600) * timeout_scale)
            )
            try:
                model_input = prepare_v2v_payload_replicate(ctx, model_id)
                log_payload_debug(model_id, model_input)
                logger.info(
                    "Replicate V2V [%s] duration=%.1fs resolution=%s timeout=%ds",
                    model_id,
                    ctx.duration,
                    ctx.resolution,
                    effective_timeout,
                )
                output = await _replicate_run_with_rate_limit_retry(
                    model_id,
                    model_input,
                    timeout=effective_timeout,
                )
                parsed = parse_response_for_provider(
                    "replicate",
                    output,
                    ctx,
                    model_id=model_id,
                    obfuscation_applied=obfuscation_applied,
                )
                pass1_video_url = parsed.video_url
                logger.info(f"[PASS 1 COMPLETO] URL Generato: {pass1_video_url}")
                return await self._apply_v2v_pass2_face_swap(
                    parsed,
                    ctx,
                    fal_api_key=fal_api_key,
                    pass1_model_id=model_id,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[WARN] Replicate V2V model %s failed (%s), trying next...",
                    model_id,
                    exc,
                )
                if _is_replicate_rate_limit(exc):
                    await asyncio.sleep(12)
                continue

        raise RuntimeError(
            f"All Replicate V2V models failed. Last error: {last_error}"
        ) from last_error

    async def _apply_v2v_pass2_face_swap(
        self,
        pass1_result: "I2VResult",
        ctx: I2VContext,
        *,
        fal_api_key: Optional[str] = None,
        pass1_model_id: str = "",
    ) -> "I2VResult":
        """Pass 2 — mandatory face swap onto Pass 1 V2V output."""
        from exceptions import IdentityConditioningError
        from i2v_router import I2VResult, ensure_public_image_url

        face_ref = ctx.face_reference_url or ctx.reference_image_url
        if not face_ref:
            raise IdentityConditioningError(
                "[PASS 2 CRITICAL] No face reference URL in context — "
                "cannot run V2V face-swap Pass 2"
            )

        logger.info("[INIZIO PASS 2] Invio a Face-Swap API...")
        face_url = await ensure_public_image_url(face_ref, fal_api_key)
        swapped_url = await apply_v2v_face_swap_pass2(
            pass1_result.video_url,
            face_url,
            replicate_token=self.api_token,
        )
        logger.info("[PASS 2 COMPLETO] URL=%s", swapped_url)
        return I2VResult(
            video_url=swapped_url,
            last_frame_url=pass1_result.last_frame_url,
            duration=pass1_result.duration,
            endpoint_id=f"{pass1_model_id}+face-swap",
            provider_id=pass1_result.provider_id,
            obfuscation_applied=pass1_result.obfuscation_applied,
        )


async def generate_i2v_replicate(
    image_url: str,
    prompt: str,
    duration: float,
    *,
    negative_prompt: str = "",
    resolution: str = "720p",
    timeout_multiplier: float = 1.0,
    fal_api_key: Optional[str] = None,
    replicate_token: Optional[str] = None,
    models: Optional[List[ReplicateModelSpec]] = None,
    draft_mode: bool = False,
    fps: int = 24,
    motion_preset: str = "smooth",
) -> Dict[str, Any]:
    """
    Generate I2V video via Replicate (async_run pattern).

    Returns dict compatible with i2v_router.generate_i2v_with_fallback.
    """
    ctx = I2VContext(
        image_url=image_url,
        prompt=prompt,
        duration=duration,
        fps=fps,
        negative_prompt=negative_prompt,
        motion_preset=motion_preset,
        resolution=resolution,
        timeout_multiplier=timeout_multiplier,
        draft_mode=draft_mode,
    )
    provider = ReplicateI2VProvider(api_token=replicate_token)
    result = await provider.generate(ctx, fal_api_key=fal_api_key, models=models)
    return {
        "video_url": result.video_url,
        "duration": result.duration,
        "last_frame_url": result.last_frame_url,
        "endpoint_id": result.endpoint_id,
        "provider_id": result.provider_id,
        "obfuscation_applied": result.obfuscation_applied,
        "num_segments": 1,
        "mean_drift": 0.0,
        "temporal_consistency": 1.0,
    }


async def generate_first_frame_replicate(
    prompt: str,
    *,
    negative_prompt: str = "",
    num_inference_steps: int = 28,
    guidance_scale: float = 7.5,
    replicate_token: Optional[str] = None,
    timeout: int = 180,
    reference_image_url: Optional[str] = None,
    identity_vector: Optional[Any] = None,
    identity_adapter_strength: float = 0.95,
) -> str:
    """Optional Flux first-frame fallback via Replicate (black-forest-labs/flux-dev)."""
    if replicate is None:
        raise RuntimeError("replicate package not installed")

    from provider_adapters import log_payload_debug, parse_flux_replicate_output, prepare_flux_replicate_payload

    token = _require_replicate_token(replicate_token)
    os.environ.setdefault("REPLICATE_API_TOKEN", token)

    payload = prepare_flux_replicate_payload(
        prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        reference_image_url=reference_image_url,
        identity_vector=identity_vector,
        identity_adapter_strength=identity_adapter_strength,
    )
    log_payload_debug("black-forest-labs/flux-dev", payload)
    logger.info("Replicate Flux first-frame fallback (black-forest-labs/flux-dev)...")
    output = await asyncio.wait_for(
        replicate.async_run("black-forest-labs/flux-dev", input=payload),
        timeout=timeout,
    )
    image_url = parse_flux_replicate_output(output)
    logger.info("Replicate Flux first-frame success: %s", image_url[:80])
    return image_url



# Pass 2 V2V face-swap models — verified payload schemas only.
V2V_PASS2_FACE_SWAP_SCHEMAS = (
    ("fofr/face-swap-video", ("swap_image", "target_video")),
    ("lucataco/facefusion", ("source_image", "target_video")),
)


def _parse_face_swap_output(output: Any) -> Optional[str]:
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list) and output:
        url = str(output[0])
        if url.startswith("http"):
            return url
    if isinstance(output, dict):
        for key in ("video", "output", "result", "url"):
            val = output.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


async def apply_v2v_face_swap_pass2(
    video_url: str,
    reference_face_url: str,
    *,
    replicate_token: Optional[str] = None,
    timeout: int = 600,
) -> str:
    """
    Mandatory Pass 2 face-swap for two-pass V2V pipeline.

    Raises IdentityConditioningError if all models fail — no graceful fallback.
    """
    from exceptions import IdentityConditioningError

    if replicate is None:
        raise IdentityConditioningError(
            "[PASS 2 CRITICAL] replicate package not installed — cannot run face-swap"
        )

    token = _require_replicate_token(replicate_token)
    os.environ.setdefault("REPLICATE_API_TOKEN", token)

    last_error: Optional[BaseException] = None

    for model, keys in V2V_PASS2_FACE_SWAP_SCHEMAS:
        key_a, key_b = keys
        payload = {key_a: reference_face_url, key_b: video_url}
        try:
            logger.info(
                "[PASS 2] face-swap via %s (keys=%s)...",
                model,
                list(payload.keys()),
            )
            output = await _replicate_run_with_rate_limit_retry(
                model,
                payload,
                timeout=timeout,
            )
            result_url = _parse_face_swap_output(output)
            if result_url:
                logger.info("[PASS 2] face-swap success via %s", model)
                return result_url
            last_error = RuntimeError(f"{model} returned no video URL")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Pass 2 face swap %s failed with verified schema %s: %s",
                model,
                list(payload.keys()),
                exc,
            )

    raise IdentityConditioningError(
        f"[PASS 2 CRITICAL] All V2V face-swap models failed. Last error: {last_error}"
    ) from last_error


async def apply_replicate_face_swap(
    video_url: str,
    reference_face_url: str,
    *,
    replicate_token: Optional[str] = None,
    timeout: int = 600,
) -> str:
    """
    Mandatory post-I2V face swap using Replicate.

    Raises IdentityConditioningError on failure — no graceful fallback.
    """
    from exceptions import IdentityConditioningError

    if replicate is None:
        raise IdentityConditioningError(
            "[IDENTITY] replicate package not installed — face-swap required"
        )

    token = _require_replicate_token(replicate_token)
    os.environ.setdefault("REPLICATE_API_TOKEN", token)

    last_error: Optional[BaseException] = None
    for model, keys in V2V_PASS2_FACE_SWAP_SCHEMAS:
        key_a, key_b = keys
        payload = {key_a: reference_face_url, key_b: video_url}
        try:
            logger.info("[IDENTITY] Post-I2V face swap via %s...", model)
            output = await _replicate_run_with_rate_limit_retry(
                model,
                payload,
                timeout=timeout,
            )
            result_url = _parse_face_swap_output(output)
            if result_url:
                logger.info("[IDENTITY] Face swap output: %s", result_url[:80])
                return result_url
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Face swap %s failed with verified schema %s: %s",
                model,
                list(payload.keys()),
                exc,
            )

    raise IdentityConditioningError(
        f"[IDENTITY] Post-I2V face swap failed. Last error: {last_error}"
    ) from last_error
