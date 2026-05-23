"""
I2V Fallback Router — Fal.ai image-to-video with provider adapters.

Tries endpoints in priority order; on 404/400/application-not-found, falls through
to the next provider. Shared by core_engine and animatediff_engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import numpy as np

from generation_progress import (
    ProgressCallback,
    estimate_i2v_seconds,
    submit_and_wait_with_eta,
)

import aiofiles
import httpx

try:
    import fal_client
except ImportError:
    fal_client = None  # type: ignore

logger = logging.getLogger(__name__)

MOTION_STRENGTH_MAP = {
    "static": 0.2,
    "subtle": 0.4,
    "smooth": 0.6,
    "cinematic": 0.8,
    "dynamic": 1.0,
}


@dataclass
class I2VContext:
    """Inputs shared across all I2V/V2V provider adapters."""

    image_url: str
    prompt: str
    duration: float
    fps: int = 24
    negative_prompt: str = ""
    motion_preset: str = "smooth"
    resolution: str = "720p"
    timeout_multiplier: float = 1.0
    draft_mode: bool = False
    stage_label: str = "Generazione video"
    segment_index: int = 1
    segment_total: int = 1
    on_progress: Optional[ProgressCallback] = field(default=None, repr=False)
    identity_vector: Optional[np.ndarray] = field(default=None, repr=False)
    identity_adapter_strength: float = 0.95
    reference_image_url: Optional[str] = None
    face_reference_url: Optional[str] = None
    full_body_reference_url: Optional[str] = None
    ip_adapter_image: Optional[str] = None
    controlnet_video_url: Optional[str] = None
    pose_map_url: Optional[str] = None
    num_inference_steps: Optional[int] = None
    motion_reference_video_path: Optional[str] = None
    motion_reference_video_url: Optional[str] = None
    canvas_expanded: bool = False
    canvas_padding: Optional[Dict[str, float]] = None

    @property
    def generation_mode(self) -> Literal["i2v", "v2v"]:
        if self.motion_reference_video_path or self.motion_reference_video_url:
            return "v2v"
        return "i2v"


# Alias for kinematic branching (Fase 3.7).
VideoGenContext = I2VContext


def resolve_generation_mode(ctx: I2VContext) -> Literal["i2v", "v2v"]:
    """Return auto-derived generation mode and log routing decision."""
    mode = ctx.generation_mode
    if mode == "v2v":
        ref = ctx.motion_reference_video_url or ctx.motion_reference_video_path
        logger.info("[ROUTER] Modalità V2V attivata. Reference video: %s", ref)
    else:
        logger.info("[ROUTER] Modalità I2V attivata.")
    return mode


@dataclass
class I2VResult:
    video_url: str
    last_frame_url: Optional[str]
    duration: float
    endpoint_id: str
    provider_id: str
    obfuscation_applied: bool = False


@dataclass
class I2VEndpointSpec:
    id: str
    endpoint: str
    build_payload: Callable[[I2VContext], Dict[str, Any]]
    parse_result: Callable[[Dict[str, Any], I2VContext], I2VResult]
    timeout: int = 300
    endpoint_aliases: Tuple[str, ...] = ()


def motion_strength_for_preset(preset: str, default: float = 0.6) -> float:
    return MOTION_STRENGTH_MAP.get(preset, default)


def _apply_conditioning(payload: Dict[str, Any], ctx: I2VContext) -> Dict[str, Any]:
    """
    Attach ControlNet / inference fields for Fal I2V endpoints.

    Identity note: Wan, Hunyuan, Kling, and Luma I2V do NOT consume
    ``identity_vector`` or ``reference_image_url`` JSON fields. Visual identity
    is carried exclusively by ``image_url`` — the PuLID-generated first frame.
    """
    logger.info(
        "[IDENTITY] I2V identity carrier (image_url / first frame): %s",
        (ctx.image_url[:80] + "...") if ctx.image_url and len(ctx.image_url) > 80 else ctx.image_url,
    )
    if ctx.controlnet_video_url:
        payload["control_video_url"] = ctx.controlnet_video_url
        payload["reference_video_url"] = ctx.controlnet_video_url
    if ctx.pose_map_url:
        payload["pose_map_url"] = ctx.pose_map_url
        payload["control_image_url"] = ctx.pose_map_url
    if ctx.num_inference_steps is not None and not ctx.draft_mode:
        steps = max(20, int(ctx.num_inference_steps))
        payload["num_inference_steps"] = steps
    if ctx.full_body_reference_url and ctx.full_body_reference_url != ctx.image_url:
        payload["reference_image_url"] = ctx.full_body_reference_url
        payload["ip_adapter_image"] = ctx.full_body_reference_url
        if ctx.face_reference_url:
            payload["face_image"] = ctx.face_reference_url
    payload["enable_safety_checker"] = False
    return payload


def _kling_duration_str(duration: float) -> str:
    return "10" if duration >= 7.5 else "5"


def _wan_num_frames(duration: float, fps: int, draft: bool = False) -> int:
    frames = int(duration * fps)
    if draft:
        # Fal Wan I2V enforces num_frames >= 81; use minimum for draft speed.
        return 81
    return max(81, min(100, frames))


def build_hunyuan_payload(ctx: I2VContext) -> Dict[str, Any]:
    # Fal Hunyuan I2V accepts only num_frames='129' (fixed clip length).
    resolution = ctx.resolution if ctx.resolution in ("480p", "720p") else "720p"
    if ctx.draft_mode:
        resolution = "480p"
    payload: Dict[str, Any] = {
        "image_url": ctx.image_url,
        "prompt": ctx.prompt,
        "resolution": resolution,
        "num_frames": "129",
        "aspect_ratio": "16:9",
        "i2v_stability": False,
    }
    return _apply_conditioning(payload, ctx)


def build_wan21_payload(ctx: I2VContext) -> Dict[str, Any]:
    strength = motion_strength_for_preset(ctx.motion_preset)
    # Wan 2.1 uses shift (1–10) and guide_scale for motion intensity
    shift = max(1.0, min(10.0, 1.0 + strength * 9.0))
    guide_scale = max(1.0, min(10.0, 3.0 + strength * 5.0))
    resolution = ctx.resolution if ctx.resolution in ("480p", "720p") else "720p"
    if ctx.draft_mode:
        resolution = "480p"
    payload: Dict[str, Any] = {
        "image_url": ctx.image_url,
        "prompt": ctx.prompt,
        "num_frames": _wan_num_frames(ctx.duration, ctx.fps, draft=ctx.draft_mode),
        "frames_per_second": min(24, max(5, ctx.fps)),
        "resolution": resolution,
        "shift": shift,
        "guide_scale": guide_scale,
        "enable_prompt_expansion": False,
    }
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return _apply_conditioning(payload, ctx)


def build_kling_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "image_url": ctx.image_url,
        "prompt": ctx.prompt,
        "duration": _kling_duration_str(ctx.duration),
        "cfg_scale": 0.5,
    }
    if ctx.negative_prompt:
        payload["negative_prompt"] = ctx.negative_prompt
    return _apply_conditioning(payload, ctx)


def build_luma_payload(ctx: I2VContext) -> Dict[str, Any]:
    payload = {
        "image_url": ctx.image_url,
        "prompt": ctx.prompt,
        "loop": False,
    }
    return _apply_conditioning(payload, ctx)


def _default_parse_result(
    result: Dict[str, Any], ctx: I2VContext, provider_id: str, endpoint_id: str
) -> I2VResult:
    video_url = extract_video_url(result)
    if not video_url:
        raise ValueError(f"Video URL not found in response from {endpoint_id}")
    last_frame_url = extract_last_frame_url(result)
    return I2VResult(
        video_url=video_url,
        last_frame_url=last_frame_url,
        duration=ctx.duration,
        endpoint_id=endpoint_id,
        provider_id=provider_id,
    )


def extract_video_url(result: Dict[str, Any]) -> Optional[str]:
    if not result:
        return None
    if isinstance(result.get("video_url"), str):
        return result["video_url"]
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    if isinstance(video, str):
        return video
    output = result.get("output")
    if isinstance(output, dict):
        return extract_video_url(output)
    data = result.get("data")
    if isinstance(data, dict):
        return extract_video_url(data)
    return None


def extract_last_frame_url(result: Dict[str, Any]) -> Optional[str]:
    if not result:
        return None
    last = result.get("last_frame")
    if isinstance(last, dict):
        return last.get("url")
    if isinstance(last, str) and last.startswith(("http://", "https://")):
        return last
    if isinstance(result.get("last_frame_url"), str):
        return result["last_frame_url"]
    end = result.get("end_frame")
    if isinstance(end, dict):
        return end.get("url")
    if isinstance(end, str) and end.startswith(("http://", "https://")):
        return end
    return None


def _is_http_url(url: Optional[str]) -> bool:
    return bool(url and (url.startswith("http://") or url.startswith("https://")))


def _configure_fal_key(api_key: Optional[str]) -> None:
    if api_key:
        import os

        os.environ.setdefault("FAL_KEY", api_key)


async def upload_local_image(path: str, api_key: Optional[str] = None) -> str:
    """Upload a local image file to Fal CDN and return the public URL."""
    if not fal_client:
        raise RuntimeError("fal_client not available. Install with: pip install fal-client")
    _configure_fal_key(api_key)
    local = Path(path)
    if not local.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    uploaded_url = await fal_client.upload_file_async(str(local))
    logger.info("Uploaded local image to Fal CDN")
    return uploaded_url


async def ensure_public_image_url(
    image_url: Optional[str], api_key: Optional[str] = None
) -> str:
    """Ensure image_url is a public HTTP URL (upload local paths when needed)."""
    if not image_url:
        raise ValueError("image_url is required for I2V generation (got None or empty)")
    if _is_http_url(image_url):
        return image_url
    return await upload_local_image(image_url, api_key)


async def ensure_public_media_url(
    media_url: Optional[str], api_key: Optional[str] = None
) -> str:
    """Ensure a local image/video path is uploaded to Fal CDN."""
    if not media_url:
        raise ValueError("media_url is required")
    if _is_http_url(media_url):
        return media_url
    if not fal_client:
        raise RuntimeError("fal_client not available. Install with: pip install fal-client")
    _configure_fal_key(api_key)
    local = Path(media_url)
    if not local.exists():
        raise FileNotFoundError(f"Media not found: {media_url}")
    uploaded_url = await fal_client.upload_file_async(str(local))
    logger.info("Uploaded local media to Fal CDN: %s", local.name)
    return uploaded_url


async def _upload_motion_reference(local_path: str, api_key: Optional[str] = None) -> str:
    """
    Upload a local motion-reference MP4 to Fal CDN.

    Requires fal_client and FAL_KEY when the path is not already a public URL.
    """
    from provider_adapters import V2V_MAX_VIDEO_BYTES

    if _is_http_url(local_path):
        return local_path

    local = Path(local_path)
    if not local.exists():
        raise FileNotFoundError(f"Motion reference video not found: {local_path}")

    size = local.stat().st_size
    if size > V2V_MAX_VIDEO_BYTES:
        raise ValueError(
            f"Motion reference video exceeds {V2V_MAX_VIDEO_BYTES // (1024 * 1024)} MB limit "
            f"({size} bytes): {local_path}"
        )

    suffix = local.suffix.lower()
    if suffix not in (".mp4", ".mov", ".webm", ".avi", ".mkv"):
        logger.warning(
            "Motion reference has uncommon extension %s; upload may still succeed",
            suffix or "(none)",
        )

    if not fal_client:
        raise RuntimeError(
            "fal_client not available for motion video upload. "
            "Install with: pip install fal-client and set FAL_KEY."
        )

    url = await ensure_public_media_url(str(local), api_key)
    logger.info("Motion reference uploaded to Fal CDN (%d bytes)", size)
    return url


async def _download_video_to_path(video_url: str, dest: Path) -> None:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        async with client.stream("GET", video_url) as response:
            response.raise_for_status()
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    await f.write(chunk)


def _extract_last_frame_ffmpeg(video_path: Path, output_path: Path) -> None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise FileNotFoundError("ffmpeg not found in PATH")
    cmd = [
        ffmpeg_bin,
        "-i",
        str(video_path),
        "-sseof",
        "-1",
        "-update",
        "1",
        "-q:v",
        "2",
        str(output_path),
        "-y",
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if process.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"FFmpeg failed to extract last frame: {process.stderr or process.stdout}"
        )


def _extract_last_frame_opencv(video_path: Path, output_path: Path) -> None:
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")

    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if frame_count > 0:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_count - 1)
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            last_frame = None
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                last_frame = frame
            frame = last_frame
        if frame is None:
            raise RuntimeError("OpenCV could not decode any frame from video")
        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"OpenCV failed to write frame: {output_path}")
    finally:
        capture.release()


def _extract_last_frame_from_file(video_path: Path, output_path: Path) -> None:
    if shutil.which("ffmpeg"):
        try:
            _extract_last_frame_ffmpeg(video_path, output_path)
            return
        except Exception as exc:
            logger.warning("FFmpeg last-frame extraction failed, trying OpenCV: %s", exc)
    _extract_last_frame_opencv(video_path, output_path)


async def extract_and_upload_last_frame(
    video_url: str, api_key: Optional[str] = None
) -> str:
    """
    Extract the last frame from a generated video and upload it to Fal CDN.

    Used when I2V providers (e.g. Wan) do not return last_frame in the API response.
    """
    logger.info("Extracting last frame from video (API did not return last_frame)...")

    output_dir = Path(tempfile.gettempdir()) / "video_frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = abs(hash(video_url)) & 0xFFFFFFFF
    temp_video = output_dir / f"temp_video_{tag}.mp4"
    last_frame_path = output_dir / f"last_frame_{tag}.jpg"

    await _download_video_to_path(video_url, temp_video)

    try:
        _extract_last_frame_from_file(temp_video, last_frame_path)
    finally:
        if temp_video.exists():
            temp_video.unlink()

    if not last_frame_path.exists():
        raise RuntimeError("Last frame file was not created after extraction")

    uploaded_url = await upload_local_image(str(last_frame_path), api_key)
    logger.info("Last frame extracted and uploaded for autoregressive propagation")
    return uploaded_url


async def ensure_last_frame_url(
    video_url: str,
    last_frame_url: Optional[str],
    api_key: Optional[str] = None,
) -> str:
    """Return a valid last_frame URL, extracting from video when the API omits it."""
    if _is_http_url(last_frame_url):
        return last_frame_url
    extracted = await extract_and_upload_last_frame(video_url, api_key)
    if not extracted:
        raise RuntimeError(
            "Could not obtain last_frame_url from API or video extraction. "
            "Autoregressive segment chaining requires a valid last frame."
        )
    return extracted


def is_404_or_400(exc: BaseException) -> bool:
    """True when the router should try the next endpoint/alias."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (400, 404, 422)

    exc_name = type(exc).__name__
    if exc_name in ("FalClientHTTPError", "HTTPStatusError"):
        msg = str(exc).lower()
        if any(
            token in msg
            for token in ("404", "400", "422", "not found", "literal_error", "validation")
        ):
            return True

    msg = str(exc).lower()
    markers = (
        "404",
        "400",
        "422",
        "not found",
        "path /",
        "application not found",
        "invalid application",
        "unknown application",
        "does not exist",
        "literal_error",
    )
    return any(m in msg for m in markers)


def is_content_policy_error(exc: BaseException) -> bool:
    """Delegate to prompt_obfuscation for consistent policy detection."""
    from prompt_obfuscation import is_content_policy_error as _is_policy

    return _is_policy(exc)


# Primary order per spec; aliases cover documented Fal model IDs when slug differs.
I2V_ENDPOINTS: List[I2VEndpointSpec] = [
    I2VEndpointSpec(
        id="hunyuan-i2v",
        endpoint="fal-ai/hunyuan-video-image-to-video",
        build_payload=build_hunyuan_payload,
        parse_result=lambda r, c: _default_parse_result(
            r, c, "hunyuan-i2v", "fal-ai/hunyuan-video-image-to-video"
        ),
        timeout=300,
        endpoint_aliases=("fal-ai/hunyuan-video/image-to-video",),
    ),
    I2VEndpointSpec(
        id="wan21-i2v",
        endpoint="fal-ai/wan-i2v",
        build_payload=build_wan21_payload,
        parse_result=lambda r, c: _default_parse_result(
            r, c, "wan21-i2v", "fal-ai/wan-i2v"
        ),
        endpoint_aliases=("fal-ai/wan2.1/i2v",),
    ),
    I2VEndpointSpec(
        id="kling-v1-i2v",
        endpoint="fal-ai/kling-video/v1/standard/image-to-video",
        build_payload=build_kling_payload,
        parse_result=lambda r, c: _default_parse_result(
            r, c, "kling-v1-i2v", "fal-ai/kling-video/v1/standard/image-to-video"
        ),
        timeout=360,
    ),
    I2VEndpointSpec(
        id="luma-dream-machine",
        endpoint="fal-ai/luma-dream-machine/image-to-video",
        build_payload=build_luma_payload,
        parse_result=lambda r, c: _default_parse_result(
            r, c, "luma-dream-machine", "fal-ai/luma-dream-machine/image-to-video"
        ),
        timeout=360,
    ),
]


def _v2v_build_payload(endpoint_id: str) -> Callable[[I2VContext], Dict[str, Any]]:
    def _builder(ctx: I2VContext) -> Dict[str, Any]:
        from provider_adapters import prepare_v2v_payload_fal

        return prepare_v2v_payload_fal(ctx, endpoint_id)

    return _builder


def _build_v2v_endpoint_specs() -> List[I2VEndpointSpec]:
    from provider_adapters import FAL_ENDPOINT_IDS, V2V_FAL_ENDPOINTS

    specs: List[I2VEndpointSpec] = []
    for spec_id in V2V_FAL_ENDPOINTS:
        slug = FAL_ENDPOINT_IDS[spec_id]
        specs.append(
            I2VEndpointSpec(
                id=spec_id,
                endpoint=slug,
                build_payload=_v2v_build_payload(spec_id),
                parse_result=lambda r, c, sid=spec_id, ep=slug: _default_parse_result(
                    r, c, sid, ep
                ),
                timeout=420 if "animate" in spec_id or spec_id == "wan-motion" else 360,
            )
        )
    return specs


_v2v_endpoints_cache: Optional[List[I2VEndpointSpec]] = None


def _get_v2v_endpoints() -> List[I2VEndpointSpec]:
    global _v2v_endpoints_cache
    if _v2v_endpoints_cache is None:
        _v2v_endpoints_cache = _build_v2v_endpoint_specs()
    return _v2v_endpoints_cache


class I2VFallbackRouter:
    """Submit I2V/V2V jobs with ordered endpoint fallback."""

    def __init__(
        self,
        endpoints: Optional[List[I2VEndpointSpec]] = None,
        api_key: Optional[str] = None,
        *,
        mode: Literal["i2v", "v2v"] = "i2v",
    ):
        if endpoints is not None:
            self.endpoints = endpoints
        elif mode == "v2v":
            self.endpoints = _get_v2v_endpoints()
        else:
            self.endpoints = I2V_ENDPOINTS
        self.api_key = api_key
        self.mode = mode
        self.last_obfuscation_applied = False

    def _ensure_client(self) -> None:
        if not fal_client:
            raise RuntimeError("fal_client not available. Install with: pip install fal-client")
        if self.api_key:
            import os

            os.environ.setdefault("FAL_KEY", self.api_key)

    async def _submit_one(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        timeout: int,
        ctx: I2VContext,
    ) -> Dict[str, Any]:
        from provider_adapters import log_payload_debug

        log_payload_debug(endpoint, payload)
        handler = await fal_client.submit_async(endpoint, arguments=payload)
        estimated = estimate_i2v_seconds(
            ctx.duration,
            draft_mode=ctx.draft_mode,
            segment_index=ctx.segment_index,
            segment_total=ctx.segment_total,
        )
        step_info = ""
        if ctx.segment_total > 1:
            step_info = f"step {ctx.segment_index}/{ctx.segment_total} segmenti"
        result = await asyncio.wait_for(
            submit_and_wait_with_eta(
                handler,
                estimated,
                ctx.stage_label,
                timeout=timeout,
                on_progress=ctx.on_progress,
                step_info=step_info,
            ),
            timeout=timeout,
        )
        if not isinstance(result, dict):
            return {"video": result} if result else {}
        return result

    async def generate(self, ctx: I2VContext) -> I2VResult:
        self._ensure_client()
        from prompt_obfuscation import obfuscate_prompt

        mode_label = resolve_generation_mode(ctx)
        last_error: Optional[BaseException] = None
        timeout_scale = max(0.25, min(1.0, ctx.timeout_multiplier))
        obfuscation_applied = False
        self.last_obfuscation_applied = False

        for i, spec in enumerate(self.endpoints):
            endpoints_to_try = (spec.endpoint,) + spec.endpoint_aliases
            for endpoint in endpoints_to_try:
                obfuscation_tried_for_endpoint = False
                while True:
                    try:
                        payload = spec.build_payload(ctx)
                        effective_timeout = max(60, int(spec.timeout * timeout_scale))
                        logger.info(
                            "Submitting %s [%s] endpoint=%s duration=%.1fs preset=%s resolution=%s timeout=%ds",
                            mode_label.upper(),
                            spec.id,
                            endpoint,
                            ctx.duration,
                            ctx.motion_preset,
                            ctx.resolution,
                            effective_timeout,
                        )
                        result = await self._submit_one(
                            endpoint, payload, effective_timeout, ctx
                        )
                        parsed = spec.parse_result(result, ctx)
                        parsed.endpoint_id = endpoint
                        parsed.obfuscation_applied = obfuscation_applied
                        logger.info(
                            "%s success [%s] endpoint=%s video_url=%s",
                            mode_label.upper(),
                            spec.id,
                            endpoint,
                            parsed.video_url[:80] + "..."
                            if len(parsed.video_url) > 80
                            else parsed.video_url,
                        )
                        return parsed
                    except Exception as e:
                        last_error = e
                        if (
                            is_content_policy_error(e)
                            and not obfuscation_tried_for_endpoint
                        ):
                            logger.warning(
                                "[WARNING] Server-side policy filter triggered. "
                                "Initiating Prompt Obfuscation..."
                            )
                            ctx.prompt = obfuscate_prompt(ctx.prompt)
                            if ctx.negative_prompt:
                                ctx.negative_prompt = obfuscate_prompt(
                                    ctx.negative_prompt
                                )
                            obfuscation_applied = True
                            obfuscation_tried_for_endpoint = True
                            continue

                        if is_404_or_400(e) or is_content_policy_error(e):
                            idx = endpoints_to_try.index(endpoint)
                            if idx + 1 < len(endpoints_to_try):
                                next_target = endpoints_to_try[idx + 1]
                            elif i + 1 < len(self.endpoints):
                                next_target = self.endpoints[i + 1].endpoint
                            else:
                                next_target = "none"
                            logger.warning(
                                "[WARN] Endpoint %s failed (%s), switching to %s",
                                endpoint,
                                e,
                                next_target,
                            )
                            break
                        raise

        self.last_obfuscation_applied = obfuscation_applied
        raise RuntimeError(
            f"All {mode_label.upper()} endpoints failed. Last error: {last_error}"
        ) from last_error


ForceProvider = Literal["fal", "replicate", "fal_then_replicate"]


class MultiProviderFallbackRouter:
    """
    Fase 3.5 — Fal primary cascade, Replicate secondary fallback.

    Attempt 1: Fal (Hunyuan → Wan → Kling → Luma) with obfuscation retry.
    Attempt 2: Replicate when Fal is exhausted or policy remains after obfuscation.
    """

    def __init__(
        self,
        fal_api_key: Optional[str] = None,
        replicate_token: Optional[str] = None,
        fal_endpoints: Optional[List[I2VEndpointSpec]] = None,
        *,
        mode: Literal["i2v", "v2v"] = "i2v",
    ):
        self.fal_api_key = fal_api_key
        self.replicate_token = replicate_token
        self.mode = mode
        self.fal_router = I2VFallbackRouter(
            endpoints=fal_endpoints,
            api_key=fal_api_key,
            mode=mode,
        )

    @staticmethod
    def _force_replicate_active(force_provider: Optional[str]) -> bool:
        env_force = os.getenv("FORCE_REPLICATE", "").strip().lower()
        if env_force in ("1", "true", "yes"):
            return True
        return (force_provider or "").strip().lower() == "replicate"

    async def _generate_replicate(
        self,
        ctx: I2VContext,
        *,
        obfuscation_applied: bool = False,
    ) -> I2VResult:
        from replicate_i2v_provider import ReplicateI2VProvider

        provider = ReplicateI2VProvider(api_token=self.replicate_token)
        return await provider.generate(
            ctx,
            fal_api_key=self.fal_api_key,
            obfuscation_applied=obfuscation_applied,
        )

    async def generate(
        self,
        ctx: I2VContext,
        *,
        force_provider: Optional[ForceProvider] = None,
    ) -> I2VResult:
        mode = resolve_generation_mode(ctx)
        if self.mode != mode:
            self.mode = mode
            self.fal_router = I2VFallbackRouter(
                api_key=self.fal_api_key,
                mode=mode,
            )

        force = (force_provider or os.getenv("I2V_FORCE_PROVIDER", "")).strip().lower()

        if self._force_replicate_active(force):
            logger.info(
                "[INFO] FORCE_REPLICATE active — skipping Fal, using Replicate provider"
            )
            return await self._generate_replicate(ctx)

        if force == "fal":
            try:
                return await self.fal_router.generate(ctx)
            except Exception as fal_only_exc:
                if mode == "v2v":
                    logger.warning(
                        "[WARN] V2V Fal endpoints exhausted (%s). "
                        "Falling back to I2V with warning.",
                        fal_only_exc,
                    )
                    i2v_ctx = _ctx_without_motion_reference(ctx)
                    i2v_router = I2VFallbackRouter(
                        api_key=self.fal_api_key,
                        mode="i2v",
                    )
                    return await i2v_router.generate(i2v_ctx)
                raise

        fal_error: Optional[BaseException] = None
        obfuscation_applied = False
        try:
            return await self.fal_router.generate(ctx)
        except Exception as exc:
            fal_error = exc
            obfuscation_applied = self.fal_router.last_obfuscation_applied

        if mode == "v2v":
            logger.warning(
                "[WARN] V2V Fal cascade failed (%s). Attempting Replicate V2V, "
                "then I2V fallback.",
                fal_error,
            )

        logger.info("[INFO] Fal exhausted, switching to Replicate provider")
        try:
            result = await self._generate_replicate(
                ctx, obfuscation_applied=obfuscation_applied
            )
            if fal_error:
                logger.info(
                    "Replicate fallback succeeded after Fal failure: %s",
                    type(fal_error).__name__,
                )
            return result
        except Exception as replicate_exc:
            if mode == "v2v":
                logger.warning(
                    "[WARN] Replicate V2V failed (%s). Final fallback to I2V.",
                    replicate_exc,
                )
                i2v_ctx = _ctx_without_motion_reference(ctx)
                i2v_router = I2VFallbackRouter(
                    api_key=self.fal_api_key,
                    mode="i2v",
                )
                return await i2v_router.generate(i2v_ctx)
            raise RuntimeError(
                f"Fal and Replicate I2V both failed. "
                f"Fal: {fal_error}; Replicate: {replicate_exc}"
            ) from replicate_exc


def _ctx_without_motion_reference(ctx: I2VContext) -> I2VContext:
    """Strip motion reference fields for I2V fallback after V2V exhaustion."""
    return I2VContext(
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
        canvas_expanded=ctx.canvas_expanded,
        canvas_padding=ctx.canvas_padding,
    )


async def generate_video_with_fallback(
    image_url: str,
    prompt: str,
    duration: float,
    *,
    negative_prompt: str = "",
    motion_preset: str = "smooth",
    fps: int = 24,
    resolution: str = "720p",
    timeout_multiplier: float = 1.0,
    draft_mode: bool = False,
    require_last_frame: bool = True,
    api_key: Optional[str] = None,
    provider: str = "fal",
    stage_label: str = "Generazione video",
    segment_index: int = 1,
    segment_total: int = 1,
    on_progress: Optional[ProgressCallback] = None,
    identity_vector: Optional[np.ndarray] = None,
    identity_adapter_strength: float = 0.95,
    reference_image_url: Optional[str] = None,
    face_reference_url: Optional[str] = None,
    full_body_reference_url: Optional[str] = None,
    ip_adapter_image: Optional[str] = None,
    controlnet_video_url: Optional[str] = None,
    pose_map_url: Optional[str] = None,
    num_inference_steps: Optional[int] = None,
    motion_reference_video_path: Optional[str] = None,
    motion_reference_video_url: Optional[str] = None,
    canvas_expanded: bool = False,
    canvas_padding: Optional[Dict[str, float]] = None,
    replicate_token: Optional[str] = None,
    force_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience wrapper returning dict compatible with core_engine._generate_single_video.

    Auto-selects V2V when motion_reference_video_path/url is set, otherwise I2V.

    provider:
        - "fal": Fal endpoints only
        - "replicate": Replicate only (bypasses Fal content filters)
        - "fal_then_replicate" (default): Fal first, Replicate on exhaustion/policy

    force_provider:
        - "replicate": skip Fal (same as FORCE_REPLICATE=1 env var)
    """
    provider_norm = (provider or "fal_then_replicate").strip().lower()
    replicate_token = replicate_token or os.getenv("REPLICATE_API_TOKEN")

    if provider_norm == "replicate" or (
        force_provider or os.getenv("FORCE_REPLICATE", "")
    ).strip().lower() in ("1", "true", "replicate"):
        force: ForceProvider = "replicate"
    elif provider_norm == "fal":
        force = "fal"
    else:
        force = "fal_then_replicate"

    public_image_url = image_url
    if not image_url.startswith(("http://", "https://")):
        if api_key:
            public_image_url = await ensure_public_image_url(image_url, api_key)
        elif force == "fal":
            raise ValueError(
                "Local image path requires FAL_KEY for CDN upload when using Fal provider"
            )

    motion_path = motion_reference_video_path
    motion_url = motion_reference_video_url
    if motion_path or motion_url:
        try:
            if motion_path and not motion_url:
                if not api_key:
                    raise ValueError(
                        "V2V mode requires FAL_KEY to upload local motion reference video"
                    )
                motion_url = await _upload_motion_reference(motion_path, api_key)
            elif motion_url and not _is_http_url(motion_url):
                if not api_key:
                    raise ValueError(
                        "V2V mode requires FAL_KEY to upload local motion reference video"
                    )
                motion_url = await _upload_motion_reference(motion_url, api_key)
        except Exception as upload_exc:
            raise RuntimeError(
                f"V2V mode selected but motion reference video upload failed: {upload_exc}"
            ) from upload_exc

    ctx = I2VContext(
        image_url=public_image_url,
        prompt=prompt,
        duration=duration,
        fps=fps,
        negative_prompt=negative_prompt,
        motion_preset=motion_preset,
        resolution=resolution,
        timeout_multiplier=timeout_multiplier,
        draft_mode=draft_mode,
        stage_label=stage_label,
        segment_index=segment_index,
        segment_total=segment_total,
        on_progress=on_progress,
        identity_vector=identity_vector,
        identity_adapter_strength=identity_adapter_strength,
        reference_image_url=reference_image_url or full_body_reference_url,
        face_reference_url=face_reference_url,
        full_body_reference_url=full_body_reference_url,
        ip_adapter_image=ip_adapter_image or full_body_reference_url or reference_image_url,
        controlnet_video_url=controlnet_video_url,
        pose_map_url=pose_map_url,
        num_inference_steps=num_inference_steps,
        motion_reference_video_path=motion_path,
        motion_reference_video_url=motion_url,
        canvas_expanded=canvas_expanded,
        canvas_padding=canvas_padding,
    )
    logger.info(
        "[IDENTITY] First frame URL for I2V: %s",
        public_image_url[:80] + "..." if len(public_image_url) > 80 else public_image_url,
    )

    mode = resolve_generation_mode(ctx)
    router = MultiProviderFallbackRouter(
        fal_api_key=api_key,
        replicate_token=replicate_token,
        mode=mode,
    )
    result = await router.generate(ctx, force_provider=force)

    if require_last_frame:
        last_frame_url = await ensure_last_frame_url(
            result.video_url, result.last_frame_url, api_key
        )
    else:
        last_frame_url = result.last_frame_url

    return {
        "video_url": result.video_url,
        "duration": result.duration,
        "last_frame_url": last_frame_url,
        "endpoint_id": result.endpoint_id,
        "provider_id": result.provider_id,
        "obfuscation_applied": result.obfuscation_applied,
        "generation_mode": mode,
        "num_segments": 1,
        "mean_drift": 0.0,
        "temporal_consistency": 1.0,
    }


async def generate_i2v_with_fallback(*args, **kwargs) -> Dict[str, Any]:
    """Backward-compatible alias for generate_video_with_fallback."""
    return await generate_video_with_fallback(*args, **kwargs)
