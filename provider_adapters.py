"""
Provider adapters — map I2VContext to Fal/Replicate payloads and parse responses.

Shared by i2v_router (Fal cascade) and replicate_i2v_provider (Replicate fallback).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional, Union

from i2v_router import (
    I2VContext,
    I2VResult,
    build_hunyuan_payload,
    build_kling_payload,
    build_luma_payload,
    build_wan21_payload,
    extract_last_frame_url,
    extract_video_url,
    motion_strength_for_preset,
)

logger = logging.getLogger(__name__)

ProviderName = Literal["fal", "replicate"]

# Fase 3.8 — body-aware V2V tuning (lower motion lock, stronger identity).
V2V_POSE_STRENGTH = 0.78
V2V_OUTPAINT_POSE_STRENGTH = 0.70  # Fase 3.14 — more generative freedom in padded zones
V2V_MOTION_STRENGTH = 0.78
V2V_IP_ADAPTER_SCALE = 0.98
V2V_ID_WEIGHT = 0.98

# Backward-compatible alias (was 0.85 in Fase 3.7).
V2V_KINEMATIC_STRENGTH = V2V_MOTION_STRENGTH
V2V_MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100 MB — Fal/Replicate typical limit

I2V_FAL_ENDPOINTS = (
    "hunyuan-i2v",
    "wan21-i2v",
    "kling-v1-i2v",
    "luma-dream-machine",
)

V2V_FAL_ENDPOINTS = (
    "wan-animate-replace",
    "wan-animate-move",
    "wan-motion",
    "hunyuan-v2v",
    "wan22-v2v",
)

DEFAULT_REPLICATE_V2V_MODEL_ORDER = (
    "wan-video/wan-2.2-animate-replace",
    "lucataco/animate-diff",
)

VISUAL_CONDITIONING_KEYS = (
    "reference_image_url",
    "reference_image",
    "reference_image_urls",
    "image_url",
    "ip_adapter_image",
    "ip_adapters",
    "image_prompt",
    "face_image",
)


def has_visual_conditioning(payload: Dict[str, Any]) -> bool:
    """Return True when payload includes a visual identity-conditioning field."""
    for key in VISUAL_CONDITIONING_KEYS:
        value = payload.get(key)
        if value:
            return True
    return False


V2V_VIDEO_KEYS = (
    "video_url",
    "video",
    "control_video",
    "drive_video",
    "control_video_url",
    "reference_video_url",
)


def log_payload_debug(endpoint: str, payload: Dict[str, Any]) -> None:
    """Log payload keys and identity fields (redact long vectors/URLs)."""
    logger.info("[PAYLOAD DEBUG] endpoint=%s keys=%s", endpoint, list(payload.keys()))
    id_vec = payload.get("identity_vector")
    id_vec_repr = (
        f"<vector len={len(id_vec)}>"
        if isinstance(id_vec, list)
        else id_vec
    )

    def _short_url(key: str) -> Optional[str]:
        val = payload.get(key)
        if not val:
            return None
        if isinstance(val, str):
            return val[:80] + "..." if len(val) > 80 else val
        if isinstance(val, list) and val and isinstance(val[0], str):
            first = val[0]
            return first[:80] + "..." if len(first) > 80 else first
        return f"<{type(val).__name__}>"

    video_fields = {
        key: _short_url(key) for key in V2V_VIDEO_KEYS if payload.get(key)
    }
    logger.info(
        "[PAYLOAD DEBUG] identity fields: reference_image=%s ip_adapter=%s "
        "reference_image_urls=%s image_url=%s identity_vector=%s",
        _short_url("reference_image_url") or _short_url("reference_image"),
        _short_url("ip_adapter_image") or payload.get("ip_adapters"),
        _short_url("reference_image_urls"),
        _short_url("image_url"),
        id_vec_repr,
    )
    if video_fields:
        logger.info("[PAYLOAD DEBUG] V2V video fields present: %s", video_fields)


def _v2v_visual_reference(ctx: I2VContext) -> str:
    """Primary V2V character reference — full body wins over face crop."""
    return (
        ctx.full_body_reference_url
        or ctx.reference_image_url
        or ctx.ip_adapter_image
        or ctx.image_url
    )


def _v2v_prompt(ctx: I2VContext) -> str:
    """Build V2V prompt with body consistency + optional outpainting suffix."""
    from prompt_enhancement import inject_body_consistency_prompt, inject_outpainting_prompt

    prompt = inject_body_consistency_prompt(ctx.prompt, mode="v2v")
    return inject_outpainting_prompt(prompt, ctx.canvas_expanded)


def _apply_v2v_tuning(payload: Dict[str, Any], ctx: Optional[I2VContext] = None) -> Dict[str, Any]:
    """Apply Fase 3.8 kinematic + identity tuning for V2V payloads."""
    # Lower ControlNet scale when canvas was expanded so padded regions can be filled (Fase 3.14).
    pose_strength = (
        V2V_OUTPAINT_POSE_STRENGTH
        if ctx and ctx.canvas_expanded
        else V2V_POSE_STRENGTH
    )
    payload["controlnet_conditioning_scale"] = pose_strength
    payload["pose_strength"] = pose_strength
    payload["motion_strength"] = V2V_MOTION_STRENGTH
    payload["strength"] = V2V_MOTION_STRENGTH
    payload["ip_adapter_scale"] = V2V_IP_ADAPTER_SCALE
    payload["image_prompt_strength"] = V2V_IP_ADAPTER_SCALE
    payload["id_weight"] = V2V_ID_WEIGHT
    logger.info(
        "[V2V TUNING] pose=%.2f ip_adapter=%.2f canvas_expanded=%s",
        pose_strength,
        V2V_IP_ADAPTER_SCALE,
        bool(ctx and ctx.canvas_expanded),
    )
    return payload


def _v2v_resolution(ctx: I2VContext) -> str:
    """Resolution for V2V payloads — proportional when canvas was expanded."""
    base = ctx.resolution if ctx.resolution in ("480p", "580p", "720p") else "720p"
    if ctx.draft_mode:
        return "480p"
    if ctx.canvas_expanded and ctx.canvas_padding:
        from canvas_expander import resolution_for_expanded_canvas

        return resolution_for_expanded_canvas(base, ctx.canvas_padding)
    return base


def _apply_v2v_visual_conditioning(payload: Dict[str, Any], ctx: I2VContext) -> Dict[str, Any]:
    """Attach full-body primary + optional face crop for V2V character replacement."""
    full_body_url = _v2v_visual_reference(ctx)
    face_url = ctx.face_reference_url

    payload["image_url"] = full_body_url
    payload["ip_adapter_image"] = full_body_url
    payload["reference_image_url"] = full_body_url
    payload["image_prompt"] = full_body_url
    if face_url and face_url != full_body_url:
        payload["face_image"] = face_url

    if ctx.prompt:
        payload["prompt"] = _v2v_prompt(ctx)

    apply_identity_conditioning(
        payload,
        identity_vector=ctx.identity_vector,
        reference_image_url=full_body_url,
        identity_adapter_strength=V2V_IP_ADAPTER_SCALE,
    )
    payload["ip_adapter_scale"] = V2V_IP_ADAPTER_SCALE
    payload["reference_image_urls"] = [full_body_url]
    return payload


def apply_identity_conditioning(
    payload: Dict[str, Any],
    *,
    identity_vector: Optional[Any] = None,
    reference_image_url: Optional[str] = None,
    identity_adapter_strength: float = 0.95,
) -> Dict[str, Any]:
    """Attach shared identity / IP-Adapter fields to a provider payload."""
    if identity_vector is not None:
        if hasattr(identity_vector, "tolist"):
            payload["identity_vector"] = identity_vector.tolist()
        else:
            payload["identity_vector"] = identity_vector
        payload["identity_adapter_strength"] = identity_adapter_strength
        payload["ip_adapter_scale"] = identity_adapter_strength
        payload["lock_identity_all_frames"] = True
    if reference_image_url:
        payload["reference_image_url"] = reference_image_url
        payload["ip_adapter_image"] = reference_image_url
        payload["reference_image_urls"] = [reference_image_url]
    return payload

FAL_ENDPOINT_BUILDERS: Dict[str, Any] = {
    "hunyuan-i2v": build_hunyuan_payload,
    "fal-ai/hunyuan-video-image-to-video": build_hunyuan_payload,
    "fal-ai/hunyuan-video/image-to-video": build_hunyuan_payload,
    "wan21-i2v": build_wan21_payload,
    "fal-ai/wan-i2v": build_wan21_payload,
    "fal-ai/wan2.1/i2v": build_wan21_payload,
    "kling-v1-i2v": build_kling_payload,
    "fal-ai/kling-video/v1/standard/image-to-video": build_kling_payload,
    "luma-dream-machine": build_luma_payload,
    "fal-ai/luma-dream-machine/image-to-video": build_luma_payload,
}

FAL_ENDPOINT_IDS: Dict[str, str] = {
    "hunyuan-i2v": "fal-ai/hunyuan-video-image-to-video",
    "wan21-i2v": "fal-ai/wan-i2v",
    "kling-v1-i2v": "fal-ai/kling-video/v1/standard/image-to-video",
    "luma-dream-machine": "fal-ai/luma-dream-machine/image-to-video",
    "wan-animate-replace": "fal-ai/wan/v2.2-14b/animate/replace",
    "wan-animate-move": "fal-ai/wan/v2.2-14b/animate/move",
    "wan-motion": "fal-ai/wan-motion",
    "hunyuan-v2v": "fal-ai/hunyuan-video/video-to-video",
    "wan22-v2v": "fal-ai/wan/v2.2-a14b/video-to-video",
}

V2V_FAL_ENDPOINT_BUILDERS: Dict[str, Any] = {}


def _apply_replicate_identity(payload: Dict[str, Any], ctx: I2VContext) -> Dict[str, Any]:
    """Ensure Replicate/Fal payloads retain identity conditioning."""
    ref_url = _v2v_visual_reference(ctx) if ctx.generation_mode == "v2v" else (
        ctx.full_body_reference_url
        or ctx.reference_image_url
        or ctx.ip_adapter_image
        or ctx.image_url
    )
    strength = V2V_IP_ADAPTER_SCALE if ctx.generation_mode == "v2v" else ctx.identity_adapter_strength
    apply_identity_conditioning(
        payload,
        identity_vector=ctx.identity_vector,
        reference_image_url=ref_url,
        identity_adapter_strength=strength,
    )
    if ref_url and "reference_image" not in payload:
        payload["reference_image"] = ref_url
    return payload


def _motion_video_url(ctx: I2VContext) -> str:
    url = ctx.motion_reference_video_url
    if not url:
        raise ValueError(
            "V2V mode requires motion_reference_video_url (upload local path first)"
        )
    return url


def _apply_v2v_motion_fields(payload: Dict[str, Any], ctx: I2VContext) -> Dict[str, Any]:
    """Attach motion-reference video fields with body-aware kinematic lock."""
    motion_url = _motion_video_url(ctx)
    payload["video_url"] = motion_url
    payload["control_video"] = motion_url
    payload["drive_video"] = motion_url
    payload["control_video_url"] = motion_url
    payload["reference_video_url"] = motion_url
    _apply_v2v_tuning(payload, ctx)
    return payload


def _build_wan_animate_replace_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": ctx.prompt,
    }
    _apply_v2v_motion_fields(payload, ctx)
    _apply_v2v_visual_conditioning(payload, ctx)
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    payload["enable_safety_checker"] = False
    return payload


def _build_wan_animate_move_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": ctx.prompt,
    }
    _apply_v2v_motion_fields(payload, ctx)
    _apply_v2v_visual_conditioning(payload, ctx)
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    payload["enable_safety_checker"] = False
    return payload


def _build_wan_motion_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": ctx.prompt,
        "adapt_motion": True,
        "enhance_identity": True,
    }
    _apply_v2v_motion_fields(payload, ctx)
    _apply_v2v_visual_conditioning(payload, ctx)
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return payload


def _build_hunyuan_v2v_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "resolution": _v2v_resolution(ctx),
        "aspect_ratio": "16:9",
        "num_frames": "129",
        "enable_safety_checker": False,
    }
    _apply_v2v_motion_fields(payload, ctx)
    _apply_v2v_visual_conditioning(payload, ctx)
    return payload


def _build_wan22_v2v_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    _apply_v2v_motion_fields(payload, ctx)
    _apply_v2v_visual_conditioning(payload, ctx)
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    payload["enable_safety_checker"] = False
    return payload


V2V_FAL_ENDPOINT_BUILDERS.update(
    {
        "wan-animate-replace": _build_wan_animate_replace_payload,
        "fal-ai/wan/v2.2-14b/animate/replace": _build_wan_animate_replace_payload,
        "wan-animate-move": _build_wan_animate_move_payload,
        "fal-ai/wan/v2.2-14b/animate/move": _build_wan_animate_move_payload,
        "wan-motion": _build_wan_motion_payload,
        "fal-ai/wan-motion": _build_wan_motion_payload,
        "hunyuan-v2v": _build_hunyuan_v2v_payload,
        "fal-ai/hunyuan-video/video-to-video": _build_hunyuan_v2v_payload,
        "wan22-v2v": _build_wan22_v2v_payload,
        "fal-ai/wan/v2.2-a14b/video-to-video": _build_wan22_v2v_payload,
    }
)


def prepare_i2v_payload_fal(ctx: I2VContext, endpoint_id: str) -> Dict[str, Any]:
    """Build Fal I2V payload for the given endpoint id or slug."""
    return prepare_payload_for_provider("fal", ctx, endpoint_id=endpoint_id)


def prepare_v2v_payload_fal(ctx: I2VContext, endpoint_id: str) -> Dict[str, Any]:
    """Build Fal V2V payload for the given endpoint id or slug."""
    key = endpoint_id or V2V_FAL_ENDPOINTS[0]
    builder = V2V_FAL_ENDPOINT_BUILDERS.get(key)
    if builder is None:
        raise ValueError(f"Unknown Fal V2V endpoint_id: {key}")
    payload = builder(ctx)
    _apply_v2v_tuning(payload, ctx)
    return payload


def _replicate_wan_resolution(ctx: I2VContext) -> str:
    """Replicate wan-2.2-animate-replace expects '720' or '480', not '720p'."""
    res = _v2v_resolution(ctx)
    if res.startswith("480"):
        return "480"
    return "720"


def _replicate_wan_animate_replace_payload(ctx: I2VContext) -> Dict[str, Any]:
    motion_url = _motion_video_url(ctx)
    full_body_url = _v2v_visual_reference(ctx)
    payload: Dict[str, Any] = {
        "video": motion_url,
        "character_image": full_body_url,
        "resolution": _replicate_wan_resolution(ctx),
        "merge_audio": True,
    }
    if ctx.prompt:
        payload["prompt"] = _v2v_prompt(ctx)
    if ctx.face_reference_url and ctx.face_reference_url != full_body_url:
        payload["face_image"] = ctx.face_reference_url
    return _apply_replicate_identity(payload, ctx)


def _replicate_animatediff_v2v_payload(ctx: I2VContext) -> Dict[str, Any]:
    motion_url = _motion_video_url(ctx)
    full_body_url = _v2v_visual_reference(ctx)

    payload: Dict[str, Any] = {
        "path": full_body_url,
        "video_path": motion_url,
        "prompt": _v2v_prompt(ctx),
        "n_prompt": ctx.negative_prompt or "",
        "motion_module": "mm_sd_v15_v2.ckpt",
        "guidance_scale": 7.5,
    }
    _apply_v2v_tuning(payload, ctx)
    return _apply_replicate_identity(payload, ctx)


def _replicate_animatediff_vid2vid_payload(ctx: I2VContext) -> Dict[str, Any]:
    """Pass 1 V2V — motion/body via AnimateDiff vid2vid."""
    motion_url = _motion_video_url(ctx)

    payload: Dict[str, Any] = {
        "video": motion_url,
        "prompt": _v2v_prompt(ctx),
        "negative_prompt": ctx.negative_prompt or "",
        "guidance_scale": 7.5,
        "num_inference_steps": 25,
        "strength": V2V_MOTION_STRENGTH,
    }
    _apply_v2v_tuning(payload, ctx)
    return payload


REPLICATE_V2V_MODEL_BUILDERS: Dict[str, Any] = {
    "lucataco/animate-diff-vid2vid": _replicate_animatediff_vid2vid_payload,
    "wan-video/wan-2.2-animate-replace": _replicate_wan_animate_replace_payload,
    "lucataco/animate-diff": _replicate_animatediff_v2v_payload,
}

REPLICATE_V2V_MODEL_TIMEOUTS: Dict[str, int] = {
    "lucataco/animate-diff-vid2vid": 600,
    "wan-video/wan-2.2-animate-replace": 720,
    "lucataco/animate-diff": 600,
}


def prepare_v2v_payload_replicate(ctx: I2VContext, model_id: str) -> Dict[str, Any]:
    """Build Replicate V2V payload for the given model id."""
    mid = model_id or DEFAULT_REPLICATE_V2V_MODEL_ORDER[0]
    builder = REPLICATE_V2V_MODEL_BUILDERS.get(mid)
    if builder is None:
        raise ValueError(f"Unknown Replicate V2V model_id: {mid}")
    payload = builder(ctx)
    _apply_v2v_tuning(payload, ctx)
    return payload


def _replicate_wan22_payload(ctx: I2VContext) -> Dict[str, Any]:
    strength = motion_strength_for_preset(ctx.motion_preset)
    resolution = "480p" if ctx.resolution == "480p" or ctx.draft_mode else "480p"
    if ctx.resolution == "720p" and not ctx.draft_mode:
        resolution = "720p"
    num_frames = max(81, min(121, int(ctx.duration * ctx.fps)))
    payload: Dict[str, Any] = {
        "prompt": ctx.prompt,
        "image": ctx.image_url,
        "num_frames": num_frames,
        "resolution": resolution,
        "frames_per_second": min(24, max(5, ctx.fps)),
        "sample_shift": max(1.0, min(20.0, 3.0 + strength * 9.0)),
        "go_fast": ctx.draft_mode,
        "disable_safety_checker": True,
    }
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return _apply_replicate_identity(payload, ctx)


def _replicate_wan21_wavespeed_payload(ctx: I2VContext) -> Dict[str, Any]:
    strength = motion_strength_for_preset(ctx.motion_preset)
    payload: Dict[str, Any] = {
        "prompt": ctx.prompt,
        "image": ctx.image_url,
        "aspect_ratio": "16:9",
        "sample_guide_scale": max(1.0, min(10.0, 3.0 + strength * 5.0)),
        "sample_shift": max(1, min(10, int(1 + strength * 9))),
        "sample_steps": 20 if ctx.draft_mode else 30,
        "fast_mode": "Fast" if ctx.draft_mode else "Balanced",
        "disable_safety_checker": True,
    }
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return _apply_replicate_identity(payload, ctx)


def _replicate_lucataco_wan_payload(ctx: I2VContext) -> Dict[str, Any]:
    duration = max(1, min(5, int(round(ctx.duration))))
    payload: Dict[str, Any] = {
        "image": ctx.image_url,
        "prompt": ctx.prompt,
        "duration": duration,
        "fps": min(30, max(7, ctx.fps)),
        "guidance_scale": 5.0,
        "num_inference_steps": 20 if ctx.draft_mode else 28,
    }
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return _apply_replicate_identity(payload, ctx)


def _replicate_minimax_payload(ctx: I2VContext) -> Dict[str, Any]:
    full_prompt = ctx.prompt
    if ctx.negative_prompt:
        full_prompt = f"{ctx.prompt}. Avoid: {ctx.negative_prompt}"
    payload = {
        "first_frame_image": ctx.image_url,
        "prompt": full_prompt,
        "prompt_optimizer": True,
    }
    return _apply_replicate_identity(payload, ctx)


REPLICATE_MODEL_BUILDERS: Dict[str, Any] = {
    "wan-video/wan-2.2-i2v-fast": _replicate_wan22_payload,
    "wavespeedai/wan-2.1-i2v-720p": _replicate_wan21_wavespeed_payload,
    "wavespeedai/wan-2.1-i2v-480p": _replicate_wan21_wavespeed_payload,
    "lucataco/wan2.1-i2v-lora": _replicate_lucataco_wan_payload,
    "minimax/video-01": _replicate_minimax_payload,
}

REPLICATE_MODEL_TIMEOUTS: Dict[str, int] = {
    "wan-video/wan-2.2-i2v-fast": 480,
    "wavespeedai/wan-2.1-i2v-720p": 720,
    "wavespeedai/wan-2.1-i2v-480p": 600,
    "lucataco/wan2.1-i2v-lora": 600,
    "minimax/video-01": 600,
}

DEFAULT_REPLICATE_MODEL_ORDER = (
    "wan-video/wan-2.2-i2v-fast",
    "wavespeedai/wan-2.1-i2v-720p",
    "wavespeedai/wan-2.1-i2v-480p",
    "lucataco/wan2.1-i2v-lora",
    "minimax/video-01",
)


def prepare_payload_for_provider(
    provider: ProviderName,
    params: I2VContext,
    *,
    endpoint_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build provider-specific request payload from shared I2VContext.

    Fal requires endpoint_id (spec id or Fal slug). Replicate requires model_id.
    """
    if provider == "fal":
        key = endpoint_id or "hunyuan-i2v"
        builder = FAL_ENDPOINT_BUILDERS.get(key)
        if builder is None:
            raise ValueError(f"Unknown Fal endpoint_id: {key}")
        return builder(params)

    if provider == "replicate":
        mid = model_id or DEFAULT_REPLICATE_MODEL_ORDER[0]
        builder = REPLICATE_MODEL_BUILDERS.get(mid)
        if builder is None:
            raise ValueError(f"Unknown Replicate model_id: {mid}")
        return builder(params)

    raise ValueError(f"Unsupported provider: {provider}")


def _extract_replicate_output_url(output: Any) -> str:
    if output is None:
        raise ValueError("Replicate returned empty output")
    if isinstance(output, str) and output.startswith(("http://", "https://")):
        return output
    if hasattr(output, "url"):
        url_attr = output.url
        resolved = url_attr() if callable(url_attr) else url_attr
        if isinstance(resolved, str) and resolved.startswith(("http://", "https://")):
            return resolved
    if isinstance(output, (list, tuple)) and output:
        return _extract_replicate_output_url(output[0])
    if isinstance(output, dict):
        for key in ("video", "url", "output"):
            if key in output:
                return _extract_replicate_output_url(output[key])
    raise ValueError(f"Unrecognized Replicate output format: {type(output)}")


def parse_response_for_provider(
    provider: ProviderName,
    raw: Any,
    params: I2VContext,
    *,
    endpoint_id: Optional[str] = None,
    model_id: Optional[str] = None,
    obfuscation_applied: bool = False,
) -> I2VResult:
    """Normalize Fal dict or Replicate prediction output into I2VResult."""
    if provider == "fal":
        if not isinstance(raw, dict):
            raw = {"video": raw}
        video_url = extract_video_url(raw)
        if not video_url:
            raise ValueError(
                f"Video URL not found in Fal response from {endpoint_id or 'unknown'}"
            )
        ep = endpoint_id or "fal-ai/hunyuan-video-image-to-video"
        spec_id = ep
        for sid, slug in FAL_ENDPOINT_IDS.items():
            if ep in (sid, slug):
                spec_id = sid
                break
        return I2VResult(
            video_url=video_url,
            last_frame_url=extract_last_frame_url(raw),
            duration=params.duration,
            endpoint_id=ep,
            provider_id=spec_id,
            obfuscation_applied=obfuscation_applied,
        )

    if provider == "replicate":
        video_url = _extract_replicate_output_url(raw)
        mid = model_id or "replicate"
        return I2VResult(
            video_url=video_url,
            last_frame_url=None,
            duration=params.duration,
            endpoint_id=mid,
            provider_id="replicate",
            obfuscation_applied=obfuscation_applied,
        )

    raise ValueError(f"Unsupported provider: {provider}")


def resolve_replicate_image_input(image_url: str) -> Union[str, Any]:
    """Return HTTP URL or open file handle for Replicate image input."""
    from pathlib import Path

    if image_url.startswith(("http://", "https://")):
        return image_url
    local = Path(image_url)
    if not local.exists():
        raise FileNotFoundError(f"Image not found: {image_url}")
    return open(local, "rb")


def prepare_flux_replicate_payload(
    prompt: str,
    *,
    negative_prompt: str = "",
    num_inference_steps: int = 28,
    guidance_scale: float = 7.5,
    aspect_ratio: str = "16:9",
    reference_image_url: Optional[str] = None,
    identity_vector: Optional[Any] = None,
    identity_adapter_strength: float = 0.95,
) -> Dict[str, Any]:
    """Payload for black-forest-labs/flux-dev first-frame fallback on Replicate."""
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "output_format": "webp",
        "disable_safety_checker": True,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    apply_identity_conditioning(
        payload,
        identity_vector=identity_vector,
        reference_image_url=reference_image_url,
        identity_adapter_strength=identity_adapter_strength,
    )
    return payload


def parse_flux_replicate_output(raw: Any) -> str:
    """Extract image URL from Replicate Flux output."""
    url = _extract_replicate_output_url(raw)
    if not url.startswith(("http://", "https://")):
        raise ValueError("Flux Replicate output is not a valid URL")
    return url
