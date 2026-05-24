#!/usr/bin/env python3
"""Generic VIP full-body forest/nude test (9:16) with identity gate."""

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

from custom_weights_handler import NegativePromptMatrix
from gender_detector import GenderPromptDescriptors, resolve_subject_gender
from identity_validator import DEFAULT_SIMILARITY_THRESHOLD, assert_identity_gate
from provider_adapters import (
    DEFAULT_REALISM_LORA_SCALE,
    FLUX_REALISM_LORA,
    resolve_lora_weights_for_fal,
)
from subject_discovery import resolve_reference_face, resolve_subject_input_folder
from test_forest_v2 import FOREST_SCENE, _build_negative_prompt, _gender_prefix
from vip_config import INPUTS_ROOT, PROJECT_ROOT, get_subject_vip_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

FLUX_ENDPOINT = "fal-ai/flux/dev"
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"
FLUX_STEPS = 40
FLUX_GUIDANCE = 7.5
VIP_LORA_SCALE = 1.0


def _load_metadata(path: Path) -> Dict[str, Any]:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if str(metadata.get("status", "")).lower() not in ("", "succeeded"):
        raise RuntimeError(f"Training status: {metadata.get('status')}")
    return metadata


def _load_weights_url(url_path: Path, metadata_path: Path) -> str:
    if url_path.is_file():
        url = url_path.read_text(encoding="utf-8").strip()
        if url.startswith("http"):
            return url
    metadata = _load_metadata(metadata_path)
    url = metadata.get("weights_url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    output = metadata.get("output")
    if isinstance(output, dict):
        w = output.get("weights")
        if isinstance(w, str) and w.startswith("http"):
            return w
    raise FileNotFoundError("Missing VIP weights URL")


def _build_prompt(trigger: str, gender: Optional[str]) -> str:
    prefix = _gender_prefix(gender)
    return f"{prefix}{trigger}, {FOREST_SCENE}"


def _build_fal_payload(lora_path: str, prompt: str, negative_prompt: str) -> Dict[str, Any]:
    realism = dict(FLUX_REALISM_LORA)
    realism["scale"] = DEFAULT_REALISM_LORA_SCALE
    return {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "image_size": "portrait_16_9",
        "num_inference_steps": FLUX_STEPS,
        "guidance_scale": FLUX_GUIDANCE,
        "num_images": 1,
        "enable_safety_checker": False,
        "loras": [realism, {"path": lora_path, "scale": VIP_LORA_SCALE}],
    }


async def _generate_fal(payload: Dict[str, Any]) -> str:
    import fal_client

    key = os.getenv("FAL_KEY", "").strip()
    if not key:
        raise RuntimeError("FAL_KEY missing")
    os.environ["FAL_KEY"] = key
    handler = await fal_client.submit_async(FLUX_ENDPOINT, arguments=payload)
    result = await asyncio.wait_for(handler.get(), timeout=600)
    images = result.get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"Fal returned no image: {result!r}")
    return str(images[0]["url"])


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VIP full-body forest test")
    parser.add_argument("--subject", type=int, required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    args = parser.parse_args(argv)

    cfg = get_subject_vip_config(args.subject)
    output = PROJECT_ROOT / "outputs" / f"test_vip_s{args.subject}_fullbody_forest.jpg"
    metadata = _load_metadata(cfg.metadata_json)
    weights_url = _load_weights_url(cfg.metadata_url, cfg.metadata_json)
    lora_path = resolve_lora_weights_for_fal(weights_url)

    folder = resolve_subject_input_folder(args.subject, INPUTS_ROOT)
    gender_result = resolve_subject_gender(subject_folder=folder, gender_json=cfg.output_dir / "gender.json")
    gender = gender_result.gender if gender_result.gender in ("male", "female") else None

    prompt = _build_prompt(cfg.trigger_word, gender)
    negative = _build_negative_prompt()
    payload = _build_fal_payload(lora_path, prompt, negative)

    logger.info("Generating VIP full-body forest for S%d", args.subject)
    image_url = asyncio.run(_generate_fal(payload))
    _download(image_url, output)

    ref = resolve_reference_face(args.subject, INPUTS_ROOT)
    if ref is None:
        vip_faces = sorted(cfg.output_dir.glob(f"{cfg.export_stem_prefix}*.jpg"))
        ref = vip_faces[0] if vip_faces else None
    if ref is None:
        raise FileNotFoundError("No reference face for identity gate")

    score = assert_identity_gate(output, ref, threshold=args.threshold, label=f"vip_fullbody_s{args.subject}")
    print(f"\n=== test_vip_fullbody S{args.subject} ===")
    print(f"InsightFace similarity: {score:.3f}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    except Exception as exc:
        logger.error("test_vip_fullbody failed: %s", exc)
        raise SystemExit(1)
