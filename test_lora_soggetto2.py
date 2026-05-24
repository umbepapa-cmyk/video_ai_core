#!/usr/bin/env python3
"""
VIP LoRA inference test for Soggetto 2.

Uses models/lora_soggetto2_vip.json (+ URL sidecar) from train_vip_lora_s2.py.
Tries Replicate trained model first, then Fal fal-ai/flux/dev with LoRA URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from identity_validator import DEFAULT_SIMILARITY_THRESHOLD, compute_face_similarity
from provider_adapters import resolve_lora_weights_for_fal

PROJECT_ROOT = Path(__file__).resolve().parent
WEIGHTS_URL_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip_url.txt"
METADATA_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip.json"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "test_vip_soggetto2.jpg"
REFERENCE_FACE = PROJECT_ROOT / "inputs" / "Soggetto 2" / "face.jpg"
VIP_DATASET = PROJECT_ROOT / "inputs" / "VIP_Dataset_Soggetto2"

PROMPT = (
    "Extreme close-up macro portrait photo of soggetto_due_vip, looking straight "
    "and directly at the camera. Highly detailed face, photorealistic, 8k resolution, "
    "studio lighting."
)
NUM_INFERENCE_STEPS = 28
GUIDANCE_SCALE = 3.5
OUTPUT_FORMAT = "jpg"
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"
FLUX_ENDPOINT = "fal-ai/flux/dev"


def _require_replicate_token() -> str:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set (add to .env)")
    return token


def _load_weights_url() -> str:
    if WEIGHTS_URL_FILE.is_file():
        url = WEIGHTS_URL_FILE.read_text(encoding="utf-8").strip()
        if url.startswith("http"):
            logger.info("Loaded weights URL from %s", WEIGHTS_URL_FILE.name)
            return url
    if METADATA_FILE.is_file():
        metadata = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        url = metadata.get("weights_url")
        if isinstance(url, str) and url.startswith("http"):
            return url
        output = metadata.get("output")
        if isinstance(output, dict):
            w = output.get("weights")
            if isinstance(w, str) and w.startswith("http"):
                return w
    raise FileNotFoundError(f"No weights URL in {WEIGHTS_URL_FILE} or {METADATA_FILE}")


def _load_metadata() -> Dict[str, Any]:
    if not METADATA_FILE.is_file():
        raise FileNotFoundError(f"LoRA metadata missing: {METADATA_FILE}")
    metadata = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    status = str(metadata.get("status", "")).lower()
    if status and status != "succeeded":
        raise RuntimeError(f"LoRA training not complete (status={status!r})")
    return metadata


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
    return None


def _run_trained_model(model_ref: str, *, token: str) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": PROMPT,
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "guidance_scale": GUIDANCE_SCALE,
        "output_format": OUTPUT_FORMAT,
    }
    logger.info("Replicate trained model: %s", model_ref)
    return client.run(model_ref, input=payload)


def _run_fallback_flux(weights_url: str, *, token: str) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": PROMPT,
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "guidance_scale": GUIDANCE_SCALE,
        "output_format": OUTPUT_FORMAT,
        "hf_lora": weights_url,
        "lora_scale": 1.0,
    }
    logger.warning("Fallback Replicate %s with hf_lora", FALLBACK_FLUX_MODEL)
    return client.run(FALLBACK_FLUX_MODEL, input=payload)


async def _run_fal_flux(weights_url: str) -> str:
    import fal_client

    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key or fal_key == "your_fal_api_key_here":
        raise RuntimeError("FAL_KEY missing for Fal fallback")
    os.environ["FAL_KEY"] = fal_key

    lora_path = resolve_lora_weights_for_fal(weights_url)
    payload = {
        "prompt": PROMPT,
        "image_size": "portrait_4_3",
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": 7.5,
        "loras": [{"path": lora_path, "scale": 1.0}],
    }
    logger.info("Fal %s with LoRA %s...", FLUX_ENDPOINT, lora_path[:80])
    handler = await fal_client.submit_async(FLUX_ENDPOINT, arguments=payload)
    result = await asyncio.wait_for(handler.get(), timeout=180)
    images = result.get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"Fal returned no image: {result!r}")
    return str(images[0]["url"])


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


def _resolve_reference_face() -> Optional[Path]:
    if REFERENCE_FACE.is_file():
        return REFERENCE_FACE
    vip_faces = sorted(VIP_DATASET.glob("vip_s2_*.jpg"))
    if vip_faces:
        logger.info("Using VIP dataset reference: %s", vip_faces[0])
        return vip_faces[0]
    return None


def main() -> int:
    weights_url = _load_weights_url()
    metadata = _load_metadata()
    model_ref = _resolve_model_ref(metadata)
    token = _require_replicate_token()

    image_url: Optional[str] = None
    last_error: Optional[BaseException] = None

    try:
        output = _run_trained_model(model_ref, token=token)
        image_url = _extract_image_url(output)
        if not image_url:
            raise RuntimeError(f"Trained model returned no image URL: {output!r}")
    except Exception as exc:
        last_error = exc
        logger.error("Trained model failed: %s", exc)
        try:
            output = _run_fallback_flux(weights_url, token=token)
            image_url = _extract_image_url(output)
            if not image_url:
                raise RuntimeError(f"Replicate fallback returned no URL: {output!r}")
        except Exception as rep_exc:
            logger.error("Replicate fallback failed: %s", rep_exc)
            try:
                image_url = asyncio.run(_run_fal_flux(weights_url))
            except Exception as fal_exc:
                raise RuntimeError(
                    f"All inference paths failed. Trained: {last_error}; "
                    f"Replicate: {rep_exc}; Fal: {fal_exc}"
                ) from fal_exc

    assert image_url is not None
    file_size = _download_image(image_url, OUTPUT_PATH)

    similarity = 0.0
    ref_face = _resolve_reference_face()
    if ref_face is not None:
        try:
            similarity = compute_face_similarity(OUTPUT_PATH, ref_face)
            logger.info(
                "[InsightFace] similarity=%.3f threshold=%.3f ref=%s",
                similarity,
                DEFAULT_SIMILARITY_THRESHOLD,
                ref_face,
            )
        except Exception as exc:
            logger.warning("[InsightFace] gate unavailable: %s", exc)
    else:
        logger.warning("No reference face for InsightFace gate")

    print("\n=== test_lora_soggetto2 VIP RESULT ===")
    print(f"Model: {model_ref}")
    print(f"Trigger: {metadata.get('trigger_word')}")
    print(f"Weights URL: {weights_url[:100]}...")
    print(f"InsightFace similarity: {similarity:.3f}")
    print(f"Output: {OUTPUT_PATH} ({file_size} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.error("test_lora_soggetto2 failed: %s", exc)
        raise SystemExit(1)
