#!/usr/bin/env python3
"""
VIP LoRA full-body inference test for Soggetto 2 (head to toe).

Default mode: **nude** full-body anatomy validation (explicit anatomical details).
Use ``--clothed`` for jeans/t-shirt variant (see test_vip_fullbody_s2.py).

Uses models/lora_soggetto2_vip.json (+ URL sidecar) from train_vip_lora_s2.py.
Nude mode uses Fal first (Replicate blocks NSFW) with Realism LoRA (0.6) + VIP LoRA (1.0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
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

from gender_detector import GenderPromptDescriptors, resolve_subject_gender
from identity_validator import (
    DEFAULT_SIMILARITY_THRESHOLD,
    assert_body_consistency_gate,
    compute_face_similarity,
)
from provider_adapters import (
    DEFAULT_REALISM_LORA_SCALE,
    FLUX_REALISM_LORA,
    resolve_lora_weights_for_fal,
)

PROJECT_ROOT = Path(__file__).resolve().parent
WEIGHTS_URL_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip_url.txt"
METADATA_FILE = PROJECT_ROOT / "models" / "lora_soggetto2_vip.json"
OUTPUT_NUDE = PROJECT_ROOT / "outputs" / "test_vip_soggetto2_fullbody_nude.jpg"
OUTPUT_CLOTHED = PROJECT_ROOT / "outputs" / "test_vip_soggetto2_fullbody.jpg"
REFERENCE_FACE = PROJECT_ROOT / "inputs" / "Soggetto 2" / "face.jpg"
REFERENCE_BODY = PROJECT_ROOT / "inputs" / "Soggetto 2" / "curated_fullbody_candidate.jpg"
VIP_DATASET = PROJECT_ROOT / "inputs" / "VIP_Dataset_Soggetto2"
SUBJECT_FOLDER = PROJECT_ROOT / "inputs" / "Soggetto 2"
GENDER_JSON = VIP_DATASET / "gender.json"

NUM_INFERENCE_STEPS = 28
GUIDANCE_SCALE = 3.5
FAL_GUIDANCE_SCALE = 7.5
OUTPUT_FORMAT = "jpg"
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"
FLUX_ENDPOINT = "fal-ai/flux/dev"
VIP_LORA_SCALE = 1.0
REALISM_LORA_SCALE = DEFAULT_REALISM_LORA_SCALE
FAL_TIMEOUT_SEC = 600


def _build_nude_prompt(descriptors: GenderPromptDescriptors) -> str:
    return (
        "Cinematic 8k photorealistic full-body wide shot of soggetto_due_vip, "
        f"{descriptors.biological}, standing upright in minimalist white seamless "
        "studio, completely naked with full explicit realistic anatomical details, "
        "natural stance, entire body visible head to toe, face toward camera, "
        f"soft even studio lighting, sharp focus, highly detailed natural skin "
        f"texture, {descriptors.physique}, masterpiece photorealistic"
    )


def _build_clothed_prompt(descriptors: GenderPromptDescriptors) -> str:
    return (
        "A full-body photograph of soggetto_due_vip standing in a sunlit, modern "
        f"minimalist apartment living room. {descriptors.pronoun} is looking at the "
        "camera with a relaxed expression. Wearing casual jeans and a white t-shirt. "
        "Natural light coming from a large window. Photorealistic, 8k resolution, "
        "highly detailed, entire body visible from head to toe."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VIP Soggetto 2 full-body inference (default: nude anatomy validation)."
    )
    parser.add_argument(
        "--clothed",
        action="store_true",
        help="Generate clothed full-body (jeans/t-shirt) instead of nude default.",
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
        help="Custom output path (default: nude or clothed preset under outputs/).",
    )
    return parser.parse_args()


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


def _aspect_ratio(*, nude: bool) -> str:
    return "16:9" if nude else "3:4"


def _flux_image_size(*, nude: bool) -> str:
    return "landscape_16_9" if nude else "portrait_4_3"


def _resolve_vip_lora_for_fal(metadata: Dict[str, Any], weights_url: str) -> str:
    """Prefer Replicate model ref for Fal (avoids tar download + CDN upload)."""
    ref = metadata.get("replicate_weights")
    if isinstance(ref, str) and ":" in ref and "/" in ref:
        logger.info("Fal VIP LoRA via Replicate ref: %s", ref)
        return ref
    output = metadata.get("output")
    if isinstance(output, dict):
        version_ref = output.get("version")
        if isinstance(version_ref, str) and ":" in version_ref:
            logger.info("Fal VIP LoRA via output.version: %s", version_ref)
            return version_ref
    return resolve_lora_weights_for_fal(weights_url)


def _build_fal_loras(metadata: Dict[str, Any], weights_url: str, *, nude: bool) -> List[Dict[str, Any]]:
    lora_path = _resolve_vip_lora_for_fal(metadata, weights_url)
    loras: List[Dict[str, Any]] = []
    if nude:
        realism = dict(FLUX_REALISM_LORA)
        realism["scale"] = REALISM_LORA_SCALE
        loras.append(realism)
    loras.append({"path": lora_path, "scale": VIP_LORA_SCALE})
    return loras


def _run_trained_model(
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
    logger.info("Replicate trained model: %s", model_ref)
    return client.run(model_ref, input=payload)


def _run_fallback_flux(
    weights_url: str,
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
        "hf_lora": weights_url,
        "lora_scale": VIP_LORA_SCALE,
    }
    logger.warning("Fallback Replicate %s with hf_lora", FALLBACK_FLUX_MODEL)
    return client.run(FALLBACK_FLUX_MODEL, input=payload)


async def _run_fal_flux(
    metadata: Dict[str, Any],
    weights_url: str,
    *,
    prompt: str,
    nude: bool,
) -> str:
    import fal_client

    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key or fal_key == "your_fal_api_key_here":
        raise RuntimeError("FAL_KEY missing for Fal inference")
    os.environ["FAL_KEY"] = fal_key

    loras = _build_fal_loras(metadata, weights_url, nude=nude)
    payload = {
        "prompt": prompt,
        "image_size": _flux_image_size(nude=nude),
        "num_inference_steps": NUM_INFERENCE_STEPS,
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": FAL_GUIDANCE_SCALE,
        "loras": loras,
    }
    logger.info(
        "Fal %s | nude=%s | loras=%d | image_size=%s",
        FLUX_ENDPOINT,
        nude,
        len(loras),
        payload["image_size"],
    )
    handler = await fal_client.submit_async(FLUX_ENDPOINT, arguments=payload)
    result = await asyncio.wait_for(handler.get(), timeout=FAL_TIMEOUT_SEC)
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


def _resolve_reference_body() -> Optional[Path]:
    if REFERENCE_BODY.is_file():
        return REFERENCE_BODY
    return None


def _run_inference(
    *,
    nude: bool,
    prompt: str,
    metadata: Dict[str, Any],
    weights_url: str,
    model_ref: str,
    token: str,
) -> tuple[str, str]:
    fal_exc: Optional[BaseException] = None
    last_error: Optional[BaseException] = None
    rep_exc: Optional[BaseException] = None

    if nude:
        try:
            image_url = asyncio.run(
                _run_fal_flux(metadata, weights_url, prompt=prompt, nude=True)
            )
            return image_url, f"fal:{FLUX_ENDPOINT}"
        except Exception as exc:
            fal_exc = exc
            logger.error("Fal nude path failed: %s", exc)

    try:
        output = _run_trained_model(model_ref, token=token, prompt=prompt, nude=nude)
        image_url = _extract_image_url(output)
        if not image_url:
            raise RuntimeError(f"Trained model returned no image URL: {output!r}")
        return image_url, f"replicate_trained:{model_ref}"
    except Exception as exc:
        last_error = exc
        logger.error("Trained model failed: %s", exc)

    try:
        output = _run_fallback_flux(weights_url, token=token, prompt=prompt, nude=nude)
        image_url = _extract_image_url(output)
        if not image_url:
            raise RuntimeError(f"Replicate fallback returned no URL: {output!r}")
        return image_url, f"replicate_fallback:{FALLBACK_FLUX_MODEL}"
    except Exception as exc:
        rep_exc = exc
        logger.error("Replicate fallback failed: %s", exc)

    if not nude:
        try:
            image_url = asyncio.run(
                _run_fal_flux(metadata, weights_url, prompt=prompt, nude=False)
            )
            return image_url, f"fal:{FLUX_ENDPOINT}"
        except Exception as exc:
            fal_exc = exc

    if nude:
        raise RuntimeError(
            f"Nude generation failed (Replicate blocks NSFW). Fal: {fal_exc}; "
            f"Replicate trained: {last_error}; Replicate fallback: {rep_exc}"
        )
    raise RuntimeError(
        f"All inference paths failed. Trained: {last_error}; "
        f"Replicate: {rep_exc}; Fal: {fal_exc}"
    )


def main() -> int:
    args = _parse_args()
    nude = not args.clothed
    output_path = args.output or (OUTPUT_NUDE if nude else OUTPUT_CLOTHED)

    gender_result = resolve_subject_gender(
        gender_json=GENDER_JSON,
        subject_folder=SUBJECT_FOLDER,
        override=args.gender,
    )
    descriptors = GenderPromptDescriptors.for_gender(gender_result.gender)
    prompt = _build_nude_prompt(descriptors) if nude else _build_clothed_prompt(descriptors)

    logger.info("Mode: %s", "nude" if nude else "clothed")
    logger.info(
        "Gender: %s (confidence=%.2f, %s)",
        gender_result.gender,
        gender_result.confidence,
        gender_result.reason,
    )
    logger.info("Prompt: %s", prompt)

    weights_url = _load_weights_url()
    metadata = _load_metadata()
    model_ref = _resolve_model_ref(metadata)
    token = _require_replicate_token()

    image_url, provider = _run_inference(
        nude=nude,
        prompt=prompt,
        metadata=metadata,
        weights_url=weights_url,
        model_ref=model_ref,
        token=token,
    )

    file_size = _download_image(image_url, output_path)

    similarity = 0.0
    ref_face = _resolve_reference_face()
    if ref_face is not None:
        try:
            similarity = compute_face_similarity(output_path, ref_face)
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

    body_score = 0.0
    ref_body = _resolve_reference_body()
    if ref_body is not None:
        body_score = assert_body_consistency_gate(
            output_path,
            ref_body,
            label="soggetto2_vip_fullbody",
        )
    else:
        logger.warning("No full-body reference at %s", REFERENCE_BODY)

    print("\n=== test_vip_soggetto2_fullbody RESULT ===")
    print(f"Mode: {'nude' if nude else 'clothed'}")
    print(f"Gender: {gender_result.gender} (confidence={gender_result.confidence:.2f})")
    print(f"Provider: {provider}")
    print(f"Prompt: {prompt}")
    print(f"Model: {model_ref}")
    print(f"Trigger: {metadata.get('trigger_word')}")
    print(f"Weights URL: {weights_url[:100]}...")
    print(f"InsightFace similarity: {similarity:.3f}")
    print(f"Body consistency score: {body_score:.3f}")
    print(f"Output: {output_path} ({file_size} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.error("test_vip_soggetto2_fullbody failed: %s", exc)
        raise SystemExit(1)
