#!/usr/bin/env python3
"""Generic VIP LoRA close-up test with InsightFace identity gate."""

from __future__ import annotations

import argparse
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

from identity_validator import DEFAULT_SIMILARITY_THRESHOLD, assert_identity_gate
from provider_adapters import resolve_lora_weights_for_fal
from subject_discovery import resolve_reference_face
from vip_config import INPUTS_ROOT, PROJECT_ROOT, get_subject_vip_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

NUM_INFERENCE_STEPS = 28
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"
FLUX_ENDPOINT = "fal-ai/flux/dev"

CLOSEUP_PROMPT = (
    "Extreme close-up macro portrait photo of {trigger}, looking straight "
    "and directly at the camera. Highly detailed face, photorealistic, 8k resolution, "
    "studio lighting."
)


def _require_replicate_token() -> str:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    return token


def _load_weights_url(path: Path, metadata_path: Path) -> str:
    if path.is_file():
        url = path.read_text(encoding="utf-8").strip()
        if url.startswith("http"):
            return url
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    url = metadata.get("weights_url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    output = metadata.get("output")
    if isinstance(output, dict):
        w = output.get("weights")
        if isinstance(w, str) and w.startswith("http"):
            return w
    raise FileNotFoundError(f"No weights URL in {path} or {metadata_path}")


def _load_metadata(metadata_path: Path) -> Dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
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
    raise ValueError("Could not resolve Replicate model reference")


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


def _run_trained_model(model_ref: str, *, token: str, prompt: str) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    return client.run(
        model_ref,
        input={
            "prompt": prompt,
            "num_inference_steps": NUM_INFERENCE_STEPS,
            "guidance_scale": 3.5,
            "output_format": "jpg",
        },
    )


def _run_fallback_flux(weights_url: str, *, token: str, prompt: str) -> Any:
    import replicate

    client = replicate.Client(api_token=token)
    return client.run(
        FALLBACK_FLUX_MODEL,
        input={
            "prompt": prompt,
            "hf_lora": weights_url,
            "num_inference_steps": NUM_INFERENCE_STEPS,
            "guidance_scale": 3.5,
            "output_format": "jpg",
        },
    )


async def _run_fal_flux(weights_url: str, prompt: str) -> str:
    import fal_client

    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key:
        raise RuntimeError("FAL_KEY missing")
    os.environ["FAL_KEY"] = fal_key
    lora_path = resolve_lora_weights_for_fal(weights_url)
    payload = {
        "prompt": prompt,
        "image_size": "portrait_4_3",
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": 7.5,
        "loras": [{"path": lora_path, "scale": 1.0}],
    }
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
    return dest.stat().st_size


def _resolve_reference(subject: int, vip_dataset: Path) -> Path:
    ref = resolve_reference_face(subject, INPUTS_ROOT)
    if ref is not None:
        return ref
    cfg = get_subject_vip_config(subject)
    vip_faces = sorted(vip_dataset.glob(f"{cfg.export_stem_prefix}*.jpg"))
    if vip_faces:
        return vip_faces[0]
    raise FileNotFoundError(f"No face.jpg or VIP crop for subject {subject}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VIP close-up LoRA test")
    parser.add_argument("--subject", type=int, default=2)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    args = parser.parse_args(argv)

    cfg = get_subject_vip_config(args.subject)
    output_path = PROJECT_ROOT / "outputs" / f"test_vip_s{args.subject}_closeup.jpg"
    prompt = CLOSEUP_PROMPT.format(trigger=cfg.trigger_word)

    weights_url = _load_weights_url(cfg.metadata_url, cfg.metadata_json)
    metadata = _load_metadata(cfg.metadata_json)
    model_ref = _resolve_model_ref(metadata)
    token = _require_replicate_token()

    image_url: Optional[str] = None
    try:
        output = _run_trained_model(model_ref, token=token, prompt=prompt)
        image_url = _extract_image_url(output)
        if not image_url:
            raise RuntimeError("Trained model returned no URL")
    except Exception as exc:
        logger.error("Trained model failed: %s", exc)
        try:
            output = _run_fallback_flux(weights_url, token=token, prompt=prompt)
            image_url = _extract_image_url(output)
            if not image_url:
                raise RuntimeError("Replicate fallback returned no URL")
        except Exception as rep_exc:
            logger.error("Replicate fallback failed: %s", rep_exc)
            image_url = asyncio.run(_run_fal_flux(weights_url, prompt))

    size = _download_image(image_url, output_path)
    ref_face = _resolve_reference(args.subject, cfg.output_dir)
    score = assert_identity_gate(
        output_path,
        ref_face,
        threshold=args.threshold,
        label=f"vip_closeup_s{args.subject}",
    )

    print(f"\n=== test_vip_closeup S{args.subject} ===")
    print(f"InsightFace similarity: {score:.3f}")
    print(f"Output: {output_path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    except Exception as exc:
        logger.error("test_vip_closeup failed: %s", exc)
        raise SystemExit(1)
