#!/usr/bin/env python3
"""
VIP LoRA full-body **clothed** inference test for Soggetto 2 (Gemini-style variant).

Default mode: jeans + white t-shirt in a sunlit apartment (original Gemini prompt).
Use ``--nude`` for explicit anatomical full-body validation (Realism LoRA + VIP LoRA).

Loads VIP weights from models/lora_soggetto2_vip.json (+ URL sidecar) and
LoRAManager registry. Primary provider: fal-ai/flux/dev; fallback: Replicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from custom_weights_handler import LoRAManager
from gender_detector import GenderPromptDescriptors, resolve_subject_gender
from provider_adapters import (
    DEFAULT_REALISM_LORA_SCALE,
    FLUX_REALISM_LORA,
    resolve_lora_weights_for_fal,
)

PROJECT_ROOT = Path(__file__).resolve().parent
WEIGHTS_URL_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip_url.txt"
METADATA_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip.json"
OUTPUT_CLOTHED = PROJECT_ROOT / "outputs" / "test_vip_fullbody_s2.jpg"
OUTPUT_NUDE = PROJECT_ROOT / "outputs" / "test_vip_fullbody_s2_nude.jpg"
VIP_DATASET = PROJECT_ROOT / "inputs" / "VIP_Dataset_Soggetto2"
SUBJECT_FOLDER = PROJECT_ROOT / "inputs" / "Soggetto 2"
GENDER_JSON = VIP_DATASET / "gender.json"
REFERENCE_FACE = SUBJECT_FOLDER / "face.jpg"

NUM_INFERENCE_STEPS = 28
GUIDANCE_SCALE = 7.5
LORA_SCALE = 0.95
VIP_LORA_SCALE_NUDE = 1.0
REALISM_LORA_SCALE = DEFAULT_REALISM_LORA_SCALE
OUTPUT_FORMAT = "jpg"
FLUX_ENDPOINT = "fal-ai/flux/dev"
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"
FAL_MAX_RETRIES = 3
FAL_RETRY_DELAY_SEC = 15


def _build_clothed_prompt(descriptors: GenderPromptDescriptors) -> str:
    return (
        "A full-body photograph of soggetto_due_vip standing in a sunlit, modern "
        f"minimalist apartment living room. {descriptors.pronoun} is looking at the "
        "camera with a relaxed expression. Wearing casual jeans and a white t-shirt. "
        "Natural light coming from a large window. Photorealistic, 8k resolution, "
        "highly detailed."
    )


def _build_nude_prompt(descriptors: GenderPromptDescriptors) -> str:
    return (
        "Cinematic 8k photorealistic full-body wide shot of soggetto_due_vip, "
        f"{descriptors.biological}, standing upright in minimalist white seamless "
        "studio, completely naked with full explicit realistic anatomical details, "
        "natural stance, entire body visible head to toe, face toward camera, "
        "soft even studio lighting, masterpiece photorealistic"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VIP Soggetto 2 clothed full-body test (Gemini variant); use --nude for anatomy validation."
    )
    parser.add_argument(
        "--nude",
        action="store_true",
        help="Generate nude full-body with explicit anatomical details.",
    )
    parser.add_argument(
        "--gender",
        choices=["male", "female"],
        default=None,
        help="Override gender detection for prompt descriptors.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Custom output path.",
    )
    return parser.parse_args()


def _load_metadata() -> Dict[str, Any]:
    if not METADATA_FILE.is_file():
        raise FileNotFoundError(f"LoRA metadata missing: {METADATA_FILE}")
    metadata = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    status = str(metadata.get("status", "")).lower()
    if status and status != "succeeded":
        raise RuntimeError(f"LoRA training not complete (status={status!r})")
    return metadata


def _load_weights_url(metadata: Dict[str, Any]) -> str:
    if WEIGHTS_URL_FILE.is_file():
        url = WEIGHTS_URL_FILE.read_text(encoding="utf-8").strip()
        if url.startswith("http"):
            logger.info("Loaded weights URL from %s", WEIGHTS_URL_FILE.name)
            return url

    url = metadata.get("weights_url")
    if isinstance(url, str) and url.startswith("http"):
        return url

    output = metadata.get("output")
    if isinstance(output, dict):
        w = output.get("weights")
        if isinstance(w, str) and w.startswith("http"):
            return w

    manager = LoRAManager(version="vip")
    cfg = manager.get("soggetto_2")
    if cfg and cfg.lora_path_or_id:
        logger.info("Loaded weights from LoRAManager registry (soggetto_2 vip)")
        return cfg.lora_path_or_id

    raise FileNotFoundError(
        f"No weights URL in {WEIGHTS_URL_FILE}, {METADATA_FILE}, or LoRAManager"
    )


def _resolve_model_ref(metadata: Dict[str, Any]) -> str:
    output = metadata.get("output")
    if isinstance(output, dict):
        version_ref = output.get("version")
        if isinstance(version_ref, str) and ":" in version_ref:
            return version_ref
    destination = str(metadata.get("destination", "")).strip()
    trainer_version = str(metadata.get("trainer_version", "")).strip()
    if destination and trainer_version:
        return f"{destination}:{trainer_version}"
    raise ValueError("Could not resolve Replicate model reference from metadata")


def _extract_image_url(output: Any) -> Optional[str]:
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list):
        for item in output:
            url = _extract_image_url(item)
            if url:
                return url
    if hasattr(output, "url"):
        url = getattr(output, "url", None)
        if isinstance(url, str) and url.startswith("http"):
            return url
    if isinstance(output, dict):
        for key in ("url", "image", "output"):
            url = _extract_image_url(output.get(key))
            if url:
                return url
    return None


def _flux_image_size(*, nude: bool) -> str:
    return "landscape_16_9" if nude else "portrait_4_3"


def _aspect_ratio(*, nude: bool) -> str:
    return "16:9" if nude else "3:4"


def _build_fal_loras(weights_url: str, *, nude: bool, lora_scale: float) -> List[Dict[str, Any]]:
    lora_path = resolve_lora_weights_for_fal(weights_url)
    loras: List[Dict[str, Any]] = []
    if nude:
        realism = dict(FLUX_REALISM_LORA)
        realism["scale"] = REALISM_LORA_SCALE
        loras.append(realism)
        loras.append({"path": lora_path, "scale": VIP_LORA_SCALE_NUDE})
    else:
        loras.append({"path": lora_path, "scale": lora_scale})
    return loras


async def _run_fal_flux(
    weights_url: str,
    *,
    prompt: str,
    nude: bool,
    lora_scale: float,
) -> str:
    import fal_client

    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key or fal_key == "your_fal_api_key_here":
        raise RuntimeError("FAL_KEY missing for Fal inference")
    os.environ["FAL_KEY"] = fal_key

    loras = _build_fal_loras(weights_url, nude=nude, lora_scale=lora_scale)
    payload = {
        "prompt": prompt,
        "image_size": _flux_image_size(nude=nude),
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": GUIDANCE_SCALE,
        "loras": loras,
    }
    logger.info(
        "Fal %s | nude=%s | loras=%d | scale=%.2f | path=%s...",
        FLUX_ENDPOINT,
        nude,
        len(loras),
        loras[-1]["scale"],
        loras[-1]["path"][:80],
    )

    last_exc: Optional[BaseException] = None
    for attempt in range(1, FAL_MAX_RETRIES + 1):
        try:
            handler = await fal_client.submit_async(FLUX_ENDPOINT, arguments=payload)
            result = await asyncio.wait_for(handler.get(), timeout=180)
            images = result.get("images") or []
            if not images or not images[0].get("url"):
                raise RuntimeError(f"Fal returned no image: {result!r}")
            return str(images[0]["url"])
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            retryable = any(
                token in err
                for token in ("rate limit", "429", "503", "timeout", "temporarily")
            )
            if retryable and attempt < FAL_MAX_RETRIES:
                logger.warning(
                    "Fal attempt %d/%d failed (%s); retry in %ds",
                    attempt,
                    FAL_MAX_RETRIES,
                    exc,
                    FAL_RETRY_DELAY_SEC,
                )
                await asyncio.sleep(FAL_RETRY_DELAY_SEC)
                continue
            raise
    raise RuntimeError(f"Fal failed after {FAL_MAX_RETRIES} attempts: {last_exc}")


def _run_replicate_trained(
    model_ref: str,
    *,
    token: str,
    prompt: str,
    nude: bool,
) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": prompt,
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "guidance_scale": GUIDANCE_SCALE,
        "output_format": OUTPUT_FORMAT,
        "aspect_ratio": _aspect_ratio(nude=nude),
    }
    logger.info("Replicate trained model fallback: %s", model_ref)
    return client.run(model_ref, input=payload)


def _run_replicate_flux_lora(
    weights_url: str,
    *,
    token: str,
    prompt: str,
    lora_scale: float,
    nude: bool,
) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": prompt,
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "guidance_scale": GUIDANCE_SCALE,
        "output_format": OUTPUT_FORMAT,
        "aspect_ratio": _aspect_ratio(nude=nude),
        "hf_lora": weights_url,
        "lora_scale": lora_scale,
    }
    logger.warning("Replicate fallback %s with hf_lora", FALLBACK_FLUX_MODEL)
    return client.run(FALLBACK_FLUX_MODEL, input=payload)


def _download_image(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
    size = dest.stat().st_size
    if size == 0:
        raise RuntimeError(f"Download produced empty file: {dest}")
    logger.info("Saved %s (%d bytes)", dest, size)
    return size


def main() -> int:
    args = _parse_args()
    nude = args.nude
    output_path = args.output or (OUTPUT_NUDE if nude else OUTPUT_CLOTHED)

    gender_result = resolve_subject_gender(
        gender_json=GENDER_JSON,
        subject_folder=SUBJECT_FOLDER,
        face_paths=[REFERENCE_FACE] if REFERENCE_FACE.is_file() else None,
        override=args.gender,
    )
    descriptors = GenderPromptDescriptors.for_gender(gender_result.gender)
    prompt = _build_nude_prompt(descriptors) if nude else _build_clothed_prompt(descriptors)
    effective_lora_scale = VIP_LORA_SCALE_NUDE if nude else LORA_SCALE

    metadata = _load_metadata()
    weights_url = _load_weights_url(metadata)

    logger.info("Mode: %s (Gemini clothed variant unless --nude)", "nude" if nude else "clothed")
    logger.info("Gender: %s (confidence=%.2f, %s)", gender_result.gender, gender_result.confidence, gender_result.reason)
    logger.info("Prompt: %s", prompt)
    logger.info("Weights URL: %s", weights_url)

    image_url: Optional[str] = None
    provider = "unknown"
    lora_path_used = ""
    fal_exc: Optional[BaseException] = None

    try:
        image_url = asyncio.run(
            _run_fal_flux(
                weights_url,
                prompt=prompt,
                nude=nude,
                lora_scale=effective_lora_scale,
            )
        )
        lora_path_used = resolve_lora_weights_for_fal(weights_url)
        provider = f"fal:{FLUX_ENDPOINT}"
    except Exception as exc:
        fal_exc = exc
        logger.error("Fal primary path failed: %s", exc)

        token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                f"Fal failed ({fal_exc}) and REPLICATE_API_TOKEN not set for fallback"
            ) from exc

        model_ref = _resolve_model_ref(metadata)
        rep_exc: Optional[BaseException] = None
        try:
            output = _run_replicate_trained(
                model_ref,
                token=token,
                prompt=prompt,
                nude=nude,
            )
            image_url = _extract_image_url(output)
            if not image_url:
                raise RuntimeError(f"Trained model returned no image URL: {output!r}")
            provider = f"replicate_trained:{model_ref}"
            lora_path_used = weights_url
        except Exception as exc2:
            rep_exc = exc2
            logger.error("Replicate trained model failed: %s", exc2)
            output = _run_replicate_flux_lora(
                weights_url,
                token=token,
                prompt=prompt,
                lora_scale=effective_lora_scale,
                nude=nude,
            )
            image_url = _extract_image_url(output)
            if not image_url:
                raise RuntimeError(
                    f"All paths failed. Fal: {fal_exc}; Replicate trained: {rep_exc}; "
                    f"Replicate flux-lora returned: {output!r}"
                ) from exc2
            provider = f"replicate_fallback:{FALLBACK_FLUX_MODEL}"
            lora_path_used = weights_url

    assert image_url is not None
    file_size = _download_image(image_url, output_path)

    print("\n=== test_vip_fullbody_s2 RESULT ===")
    print(f"Mode: {'nude' if nude else 'clothed (Gemini variant)'}")
    print(f"Gender: {gender_result.gender} (confidence={gender_result.confidence:.2f})")
    print(f"Provider: {provider}")
    print(f"Prompt: {prompt}")
    print(f"LoRA scale: {effective_lora_scale}")
    print(f"LoRA URL/ref: {lora_path_used}")
    print(f"Output: {output_path} ({file_size} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.error("test_vip_fullbody_s2 failed: %s", exc)
        raise SystemExit(1)
