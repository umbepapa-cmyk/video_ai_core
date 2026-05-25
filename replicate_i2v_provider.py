"""Replicate image-to-video provider with model fallback chain."""



from __future__ import annotations



import asyncio

import logging

import os

from typing import Any, Dict, List, Optional



logger = logging.getLogger(__name__)



REPLICATE_MODELS: List[str] = [

    "wan-video/wan-2.2-i2v-fast",

    "wavespeedai/wan-2.1-i2v-720p",

    "wavespeedai/wan-2.1-i2v-480p",

    "lucataco/wan2.1-i2v-lora",

    "minimax/video-01",

]



FACE_SWAP_MODELS: List[str] = [

    "fofr/face-swap-video",

    "lucataco/facefusion",

]





def _num_frames(duration: float, fps: int = 24) -> int:

    return max(81, min(120, int(duration * fps)))





def _build_input(

    *,

    image_url: str,

    prompt: str,

    duration: float,

    negative_prompt: str,

    resolution: str,

) -> Dict[str, Any]:

    return {

        "prompt": prompt,

        "image": image_url,

        "num_frames": _num_frames(duration),

        "resolution": resolution if resolution in ("480p", "720p") else "720p",

        "frames_per_second": 24,

        "sample_shift": 5,

        "go_fast": True,

        "disable_safety_checker": True,

        "negative_prompt": negative_prompt or "",

    }





def _extract_video_url(output: Any) -> Optional[str]:

    video_url = output

    if isinstance(output, (list, tuple)) and output:

        video_url = output[0]

    if hasattr(output, "url"):

        video_url = output.url

    if video_url and str(video_url).startswith("http"):

        return str(video_url)

    return None





def _face_swap_payload_variants(

    model: str,

    *,

    video_url: str,

    face_url: str,

    target_face_index: Optional[int],

) -> List[Dict[str, Any]]:

    """Build payload schema variants for a given Replicate face-swap model."""

    variants: List[Dict[str, Any]] = []



    if "face-swap-video" in model or model.startswith("fofr/"):

        bases = [

            {"swap_image": face_url, "target_video": video_url},

            {"face_image": face_url, "video": video_url},

            {"source_image": face_url, "target_video": video_url},

        ]

    elif "facefusion" in model:

        bases = [

            {"source_image": face_url, "target_video": video_url},

            {"swap_image": face_url, "target_video": video_url},

        ]

    else:

        bases = [{"swap_image": face_url, "target_video": video_url}]



    for base in bases:

        variants.append(dict(base))

        if target_face_index is not None:

            indexed = dict(base)

            indexed["target_face_index"] = target_face_index

            variants.append(indexed)

            variants.append({**indexed, "face_index": target_face_index})

            variants.append({**indexed, "input_faces_index": target_face_index})

            if "facefusion" in model:

                variants.append({**indexed, "face_num": target_face_index})



    seen: set = set()

    unique: List[Dict[str, Any]] = []

    for payload in variants:

        key = tuple(sorted(payload.items()))

        if key not in seen:

            seen.add(key)

            unique.append(payload)

    return unique





async def apply_replicate_face_swap(

    *,

    video_url: str,

    face_url: str,

    target_face_index: Optional[int] = None,

    require_face: bool = True,

    timeout: int = 600,

) -> str:

    """

    Swap a reference face onto a target video via Replicate (legacy, not hot path).



    Raises IdentityConditioningError on any failure or missing output.

    """

    from exceptions import IdentityConditioningError



    import replicate



    if not require_face:

        raise ValueError("require_face=False is no longer supported; face swap must succeed")



    token = os.getenv("REPLICATE_API_TOKEN", "").strip()

    if not token:

        raise IdentityConditioningError("REPLICATE_API_TOKEN not set for face swap")



    client = replicate.Client(api_token=token)

    last_error: Optional[BaseException] = None



    for model in FACE_SWAP_MODELS:

        payloads = _face_swap_payload_variants(

            model,

            video_url=video_url,

            face_url=face_url,

            target_face_index=target_face_index,

        )

        for payload in payloads:

            try:

                logger.info(

                    "Replicate face swap [%s] target_face_index=%s keys=%s",

                    model,

                    target_face_index,

                    sorted(payload.keys()),

                )

                output = await asyncio.wait_for(

                    client.async_run(f"{model}", input=payload),

                    timeout=timeout,

                )

                result_url = _extract_video_url(output)

                if result_url:

                    logger.info("Face swap succeeded via %s", model)

                    return result_url

                last_error = RuntimeError(f"Unexpected output for {model}: {output!r}")

            except Exception as exc:

                last_error = exc

                logger.warning(

                    "[FACE_SWAP] %s failed (keys=%s): %s",

                    model,

                    sorted(payload.keys()),

                    exc,

                )



    raise IdentityConditioningError(

        f"All face-swap models/variants failed. Last error: {last_error}",

        target_face_index=target_face_index,

    )





async def apply_v2v_face_swap_pass2(

    *,

    video_url: str,

    face_url: str,

    target_face_index: Optional[int] = None,

    require_face: bool = True,

    subjects_payload: Optional[Dict[str, str]] = None,

    face_reference_urls: Optional[Dict[str, str]] = None,

    subject_face_index_map: Optional[Dict[str, int]] = None,

    core_engine: Optional[Any] = None,

    api_key: Optional[str] = None,

) -> str:

    """

    V2V pass-2 face swap: single-subject call or sequential multi-subject chain.



    Delegates to Fal.ai via provider_adapters (commercial path).

    """

    from provider_adapters import apply_fal_face_swap

    if not require_face:
        raise ValueError(
            "require_face=False is no longer supported; face swap must succeed or raise"
        )

    if core_engine is not None and getattr(core_engine.config, "is_multi_subject", False):

        return await core_engine._apply_sequential_multi_subject_face_swap(video_url)



    if subjects_payload and len(subjects_payload) > 1 and face_reference_urls:

        current_url = video_url

        for ordinal, subject_id in enumerate(sorted(subjects_payload.keys())):

            ref_url = face_reference_urls.get(subject_id)

            if not ref_url:

                raise RuntimeError(f"Missing face reference URL for {subject_id}")

            idx = (

                subject_face_index_map.get(subject_id, ordinal)

                if subject_face_index_map

                else ordinal

            )

            current_url = await apply_fal_face_swap(

                image_or_video_url=current_url,

                face_image_url=ref_url,

                target_face_index=idx,

                require_face=require_face,

                api_key=api_key,

            )

        return current_url



    return await apply_fal_face_swap(

        image_or_video_url=video_url,

        face_image_url=face_url,

        target_face_index=target_face_index if target_face_index is not None else 0,

        require_face=require_face,

        api_key=api_key,

    )





async def _apply_v2v_pass2_face_swap(

    *,

    video_url: str,

    face_url: str,

    target_face_index: Optional[int] = None,

    require_face: bool = True,

    subjects_payload: Optional[Dict[str, str]] = None,

    face_reference_urls: Optional[Dict[str, str]] = None,

    subject_face_index_map: Optional[Dict[str, int]] = None,

    core_engine: Optional[Any] = None,

    api_key: Optional[str] = None,

) -> str:

    """Alias for V2V two-pass pipeline pass-2 face swap."""

    return await apply_v2v_face_swap_pass2(

        video_url=video_url,

        face_url=face_url,

        target_face_index=target_face_index,

        require_face=require_face,

        subjects_payload=subjects_payload,

        face_reference_urls=face_reference_urls,

        subject_face_index_map=subject_face_index_map,

        core_engine=core_engine,

        api_key=api_key,

    )





async def generate_i2v_replicate(

    *,

    image_url: str,

    prompt: str,

    duration: float,

    negative_prompt: str = "",

    resolution: str = "720p",

    timeout_multiplier: float = 1.0,

) -> Dict[str, Any]:

    import replicate



    token = os.getenv("REPLICATE_API_TOKEN", "").strip()

    if not token:

        raise RuntimeError("REPLICATE_API_TOKEN not set")



    client = replicate.Client(api_token=token)

    payload = _build_input(

        image_url=image_url,

        prompt=prompt,

        duration=duration,

        negative_prompt=negative_prompt,

        resolution=resolution,

    )

    timeout = max(120, int(480 * max(0.5, timeout_multiplier)))

    last_error: Optional[BaseException] = None



    for model in REPLICATE_MODELS:

        try:

            logger.info(

                "Replicate I2V [%s] duration=%.1fs resolution=%s timeout=%ss",

                model,

                duration,

                resolution,

                timeout,

            )

            output = await asyncio.wait_for(

                client.async_run(f"{model}", input=payload),

                timeout=timeout,

            )

            video_url = _extract_video_url(output)

            if not video_url:

                raise RuntimeError(f"Unexpected Replicate output for {model}: {output!r}")

            return {

                "video_url": video_url,

                "duration": duration,

                "last_frame_url": None,

                "endpoint_id": model,

                "provider_id": "replicate",

                "obfuscation_applied": False,

            }

        except Exception as exc:

            last_error = exc

            logger.warning("[WARN] Replicate model %s failed (%s), trying next...", model, exc)



    raise RuntimeError(f"All Replicate I2V models failed. Last error: {last_error}")

