#!/usr/bin/env python3
"""
Forest scene v2 — full-body 9:16, negative prompts, gender-aware discovery, InsightFace logging.

Improvements over test_all_subjects_forest.py:
- Dynamic subject discovery from inputs/ (uomo/donna folder names)
- 9:16 aspect ratio for full-body framing
- Structured prompt + strong negative prompts
- Single-person prefix enforcement
- Post-generation InsightFace similarity vs reference face.jpg (logged, never silent)
- Higher inference steps (40)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import httpx
from dotenv import load_dotenv

from custom_weights_handler import NegativePromptMatrix
from identity_validator import V3_SIMILARITY_THRESHOLD, compute_face_similarity
from provider_adapters import (
    DEFAULT_REALISM_LORA_SCALE,
    _resolve_lora_weights_url,
    load_lora_metadata,
    resolve_lora_weights_for_fal,
)
from subject_discovery import (
    discover_subject_inputs,
    resolve_reference_face,
    resolve_subject_gender,
    resolve_subject_input_folder,
)

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_ROOT / "models"
INPUTS_DIR = PROJECT_ROOT / "inputs"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LOG_FILE = PROJECT_ROOT / "test_forest_v2.log"

FLUX_ENDPOINT = "fal-ai/flux/dev"
REALISM_LORA: Dict[str, Any] = {
    "path": "https://huggingface.co/XLabs-AI/flux-RealismLora/resolve/main/lora.safetensors",
    "scale": DEFAULT_REALISM_LORA_SCALE,
}
SUBJECT_LORA_SCALE = 1.0
FLUX_STEPS = 40
FLUX_GUIDANCE = 7.5
FLUX_IMAGE_SIZE = "portrait_9_16"
REPLICATE_ASPECT_RATIO = "9:16"
GENERATION_TIMEOUT = 300
DOWNLOAD_TIMEOUT = 120.0
FALLBACK_FLUX_MODEL = "black-forest-labs/flux-dev-lora"

TRIGGERS = {
    1: "soggetto_uno",
    2: "soggetto_due",
    3: "soggetto_tre",
    4: "soggetto_quattro",
    5: "soggetto_cinque",
    6: "soggetto_sei",
}

FOREST_SCENE = (
    "figura intera head-to-toe, nudo, viso rivolto verso la telecamera, corpo a tre quarti, "
    "anatomia realistica dettagliata, espressione leggermente arrabbiata ma di buon umore. "
    "Bosco al tramonto, luce calda dorata filtrata tra gli alberi, profondità cinematografica, "
    "fotorealistico, 8k, pelle naturale."
)

FOREST_EXTRA_NEGATIVES = [
    "clothed",
    "clothing",
    "underwear",
    "bikini",
    "multiple people",
    "two people",
    "crowd",
    "duplicate person",
    "cropped",
    "cut off feet",
    "cut off head",
    "partial body",
    "close-up only",
    "portrait only",
    "daytime flat light",
    "harsh midday sun",
    "overcast grey sky",
    "profile view only",
    "side profile",
    "back turned",
    "looking away",
    "sunglasses",
    "hat",
    "mask",
    "text",
    "watermark",
    "cartoon",
    "anime",
    "3d render",
    "plastic skin",
]


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("test_forest_v2")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


logger = _setup_logging()


@dataclass
class RunResult:
    subject_num: int
    status: str
    output_path: Optional[Path] = None
    reason: str = ""
    metadata_path: Optional[Path] = None
    provider: str = ""
    image_url: str = ""
    input_folder: str = ""
    gender: Optional[str] = None
    identity_similarity: float = 0.0
    identity_threshold: float = V3_SIMILARITY_THRESHOLD
    identity_pass: bool = False
    identity_notes: str = ""
    prompt: str = ""
    negative_prompt: str = ""


def _build_negative_prompt() -> str:
    base = NegativePromptMatrix.get_image_negatives(custom_negatives=FOREST_EXTRA_NEGATIVES)
    return base


def _gender_prefix(gender: Optional[str]) -> str:
    if gender == "female":
        return "Una sola persona, una donna adulta, "
    if gender == "male":
        return "Una sola persona, un uomo adulto, "
    return "Una sola persona, "


def _build_prompt(trigger: str, gender: Optional[str]) -> str:
    return f"{_gender_prefix(gender)}{trigger}, {FOREST_SCENE}"


def _load_fal_key() -> Optional[str]:
    key = os.getenv("FAL_KEY", "").strip()
    if not key or key == "your_fal_api_key_here":
        return None
    os.environ["FAL_KEY"] = key
    return key


def _load_replicate_token() -> Optional[str]:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    return token or None


def _metadata_path(subject_num: int) -> Optional[Path]:
    v3 = MODELS_DIR / f"lora_soggetto{subject_num}_v3.json"
    base = MODELS_DIR / f"lora_soggetto{subject_num}.json"
    if v3.is_file():
        return v3
    if base.is_file():
        return base
    return None


def _resolve_model_ref(metadata: Dict[str, Any]) -> Optional[str]:
    output = metadata.get("output")
    if isinstance(output, dict):
        version_ref = output.get("version")
        if isinstance(version_ref, str) and ":" in version_ref and "/" in version_ref:
            return version_ref
    destination = str(metadata.get("destination", "")).strip()
    trainer_version = str(metadata.get("trainer_version", "")).strip()
    if destination and trainer_version:
        return f"{destination}:{trainer_version}"
    return None


def _resolve_fal_lora_path(metadata: Dict[str, Any], meta_path: Path) -> str:
    model_ref = _resolve_model_ref(metadata)
    if model_ref:
        return resolve_lora_weights_for_fal(model_ref)
    weights = _resolve_lora_weights_url(metadata)
    if not weights:
        url_path = meta_path.with_name(meta_path.stem + "_url.txt")
        if url_path.is_file():
            weights = url_path.read_text(encoding="utf-8").strip()
    if not weights:
        raise RuntimeError("weights_url assente nei metadata")
    return resolve_lora_weights_for_fal(weights)


def _build_fal_payload(lora_path: str, prompt: str, negative_prompt: str) -> Dict[str, Any]:
    loras = [dict(REALISM_LORA), {"path": lora_path, "scale": SUBJECT_LORA_SCALE}]
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "image_size": FLUX_IMAGE_SIZE,
        "num_inference_steps": FLUX_STEPS,
        "num_images": 1,
        "enable_safety_checker": False,
        "guidance_scale": FLUX_GUIDANCE,
        "loras": loras,
    }
    return payload


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


async def _download_image(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(8192):
                    await f.write(chunk)
    size = dest.stat().st_size
    if size == 0:
        raise RuntimeError(f"Empty download: {dest}")
    return size


async def _generate_fal(payload: Dict[str, Any]) -> str:
    import fal_client

    handler = await fal_client.submit_async(FLUX_ENDPOINT, arguments=payload)
    try:
        result = await asyncio.wait_for(handler.get(), timeout=GENERATION_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"Flux timeout after {GENERATION_TIMEOUT}s") from exc
    images = result.get("images") or []
    if not images:
        raise RuntimeError(f"No images from Flux: {result!r}")
    url = images[0].get("url")
    if not url:
        raise RuntimeError(f"Missing image URL: {images[0]!r}")
    return str(url)


def _generate_replicate(model_ref: str, prompt: str, negative_prompt: str, *, token: str) -> str:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "num_inference_steps": FLUX_STEPS,
        "guidance_scale": FLUX_GUIDANCE,
        "output_format": "jpg",
        "aspect_ratio": REPLICATE_ASPECT_RATIO,
    }
    output = client.run(model_ref, input=payload)
    url = _extract_image_url(output)
    if not url:
        raise RuntimeError(f"Replicate trained model returned no URL: {output!r}")
    return url


def _generate_replicate_fallback(
    weights_url: str, prompt: str, negative_prompt: str, *, token: str
) -> str:
    import replicate

    client = replicate.Client(api_token=token)
    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "num_inference_steps": FLUX_STEPS,
        "guidance_scale": FLUX_GUIDANCE,
        "output_format": "jpg",
        "aspect_ratio": REPLICATE_ASPECT_RATIO,
        "hf_lora": weights_url,
        "lora_scale": SUBJECT_LORA_SCALE,
    }
    output = client.run(FALLBACK_FLUX_MODEL, input=payload)
    url = _extract_image_url(output)
    if not url:
        raise RuntimeError(f"Replicate fallback returned no URL: {output!r}")
    return url


def _evaluate_identity(
    output_path: Path,
    subject_num: int,
    *,
    threshold: float,
) -> tuple[float, bool, str]:
    ref_face = resolve_reference_face(subject_num, INPUTS_DIR)
    if ref_face is None:
        msg = f"Nessun face.jpg in {resolve_subject_input_folder(subject_num, INPUTS_DIR)}"
        logger.warning("[IDENTITY] S%d: %s — gate non applicabile", subject_num, msg)
        return 0.0, False, msg

    try:
        score = compute_face_similarity(output_path, ref_face)
    except RuntimeError as exc:
        msg = f"InsightFace non disponibile: {exc}"
        logger.error("[IDENTITY] S%d: %s", subject_num, msg)
        return 0.0, False, msg
    except Exception as exc:
        msg = f"Errore InsightFace: {exc}"
        logger.error("[IDENTITY] S%d: %s", subject_num, msg)
        return 0.0, False, msg

    passed = score >= threshold
    if score == 0.0:
        note = "volto non rilevato in output o reference"
    elif passed:
        note = "similarity sopra soglia"
    else:
        note = "similarity SOTTO soglia — identità non verificata"

    logger.info(
        "[IDENTITY] S%d similarity=%.3f threshold=%.3f pass=%s ref=%s (%s)",
        subject_num,
        score,
        threshold,
        passed,
        ref_face,
        note,
    )
    return score, passed, note


def _subjects_to_run(explicit: Optional[List[int]], discovered: dict[int, Path]) -> List[int]:
    if explicit:
        return sorted(set(explicit))
    # Prefer subjects with both LoRA metadata and input folder; fall back to LoRA-only.
    with_lora = [n for n in range(1, 7) if _metadata_path(n) is not None]
    with_both = sorted(n for n in with_lora if n in discovered)
    return with_both if with_both else sorted(with_lora)


async def _run_subject(subject_num: int, *, similarity_threshold: float) -> RunResult:
    input_folder = resolve_subject_input_folder(subject_num, INPUTS_DIR)
    meta_path = _metadata_path(subject_num)
    negative_prompt = _build_negative_prompt()

    if meta_path is None:
        msg = "LoRA non addestrata: metadata JSON assente (v3 e base)"
        logger.warning("[SKIP] Soggetto %s: %s", subject_num, msg)
        return RunResult(subject_num, "SKIP", reason=msg, input_folder=str(input_folder))

    try:
        metadata = load_lora_metadata(meta_path)
    except Exception as exc:
        msg = f"metadata invalida: {exc}"
        logger.warning("[SKIP] Soggetto %s: %s", subject_num, msg)
        return RunResult(
            subject_num, "SKIP", reason=msg, metadata_path=meta_path, input_folder=str(input_folder)
        )

    status = str(metadata.get("status", "")).lower()
    if status and status != "succeeded":
        msg = f"training incomplete (status={status})"
        logger.warning("[SKIP] Soggetto %s: %s", subject_num, msg)
        return RunResult(
            subject_num,
            "SKIP",
            reason=msg,
            metadata_path=meta_path,
            input_folder=str(input_folder),
        )

    trigger = str(metadata.get("trigger_word") or TRIGGERS.get(subject_num, f"soggetto_{subject_num}"))
    gender = resolve_subject_gender(
        subject_num,
        inputs_root=INPUTS_DIR,
        metadata=metadata,
        project_root=PROJECT_ROOT,
    )
    prompt = _build_prompt(trigger, gender)
    out_path = OUTPUTS_DIR / f"test_forest_v2_soggetto{subject_num}.jpg"

    replicate_token = _load_replicate_token()
    fal_key = _load_fal_key()
    model_ref = _resolve_model_ref(metadata)
    weights_url = _resolve_lora_weights_url(metadata)
    if not weights_url:
        url_path = meta_path.with_name(meta_path.stem + "_url.txt")
        if url_path.is_file():
            weights_url = url_path.read_text(encoding="utf-8").strip()

    logger.info(
        "[RUN] Soggetto %s input=%s meta=%s gender=%s trigger=%s steps=%s aspect=%s",
        subject_num,
        input_folder.name,
        meta_path.name,
        gender or "unknown",
        trigger,
        FLUX_STEPS,
        REPLICATE_ASPECT_RATIO,
    )
    logger.info("[RUN] Prompt: %s", prompt)
    logger.info("[RUN] Negative (first 200): %s...", negative_prompt[:200])

    errors: list[str] = []
    image_url = ""
    provider = ""

    if replicate_token and model_ref:
        try:
            image_url = await asyncio.to_thread(
                _generate_replicate, model_ref, prompt, negative_prompt, token=replicate_token
            )
            provider = "replicate"
        except Exception as exc:
            errors.append(f"replicate-trained: {exc}")
            logger.error("[ERROR/replicate-trained] Soggetto %s: %s", subject_num, exc)

    if not image_url and replicate_token and weights_url:
        try:
            image_url = await asyncio.to_thread(
                _generate_replicate_fallback,
                weights_url,
                prompt,
                negative_prompt,
                token=replicate_token,
            )
            provider = "replicate-fallback"
        except Exception as exc:
            errors.append(f"replicate-fallback: {exc}")
            logger.error("[ERROR/replicate-fallback] Soggetto %s: %s", subject_num, exc)

    if not image_url and fal_key:
        try:
            lora_path = _resolve_fal_lora_path(metadata, meta_path)
            payload = _build_fal_payload(lora_path, prompt, negative_prompt)
            image_url = await _generate_fal(payload)
            provider = "fal"
        except Exception as exc:
            errors.append(f"fal: {exc}")
            logger.error("[ERROR/fal] Soggetto %s: %s", subject_num, exc)
    elif not image_url and not fal_key:
        errors.append("fal: FAL_KEY assente")

    if not image_url:
        if not replicate_token:
            errors.append("replicate: REPLICATE_API_TOKEN assente")
        msg = "; ".join(errors) if errors else "nessun provider disponibile"
        return RunResult(
            subject_num,
            "ERROR",
            reason=msg,
            metadata_path=meta_path,
            input_folder=str(input_folder),
            gender=gender,
            prompt=prompt,
            negative_prompt=negative_prompt,
            identity_threshold=similarity_threshold,
        )

    size = await _download_image(image_url, out_path)
    logger.info("[OK/%s] Soggetto %s -> %s (%d bytes)", provider, subject_num, out_path, size)

    similarity, identity_pass, identity_notes = _evaluate_identity(
        out_path, subject_num, threshold=similarity_threshold
    )

    final_status = "OK"
    if ref_has_face := resolve_reference_face(subject_num, INPUTS_DIR) is not None:
        if similarity == 0.0:
            final_status = "IDENTITY_FAIL"
        elif not identity_pass:
            final_status = "IDENTITY_LOW"

    result = RunResult(
        subject_num,
        final_status,
        output_path=out_path,
        metadata_path=meta_path,
        provider=provider,
        image_url=image_url,
        input_folder=str(input_folder),
        gender=gender,
        identity_similarity=similarity,
        identity_threshold=similarity_threshold,
        identity_pass=identity_pass,
        identity_notes=identity_notes,
        prompt=prompt,
        negative_prompt=negative_prompt,
    )

    summary_path = OUTPUTS_DIR / f"test_forest_v2_soggetto{subject_num}.json"
    summary_path.write_text(
        json.dumps(
            {
                "subject_num": subject_num,
                "status": final_status,
                "provider": provider,
                "input_folder": input_folder.name,
                "gender": gender,
                "trigger": trigger,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "identity_similarity": similarity,
                "identity_threshold": similarity_threshold,
                "identity_pass": identity_pass,
                "identity_notes": identity_notes,
                "output_path": str(out_path),
                "image_url": image_url,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return result


async def main_async(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Forest test v2 — full-body + identity logging")
    parser.add_argument(
        "--subjects",
        type=int,
        nargs="*",
        help="Subject numbers to run (default: discovered + LoRA)",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=V3_SIMILARITY_THRESHOLD,
        help=f"InsightFace cosine threshold (default {V3_SIMILARITY_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    discovered = discover_subject_inputs(INPUTS_DIR)
    logger.info("=== test_forest_v2 — subject discovery ===")
    for num, path in sorted(discovered.items()):
        logger.info("  S%d -> %s", num, path.name)
    if not discovered:
        logger.warning("Nessuna cartella soggetto in %s", INPUTS_DIR)

    subjects = _subjects_to_run(args.subjects, discovered)
    logger.info("Soggetti in esecuzione: %s", subjects)

    results: list[RunResult] = []
    for n in subjects:
        results.append(await _run_subject(n, similarity_threshold=args.similarity_threshold))

    logger.info("=== RIEPILOGO test_forest_v2 ===")
    for r in results:
        line = f"Soggetto {r.subject_num}: {r.status}"
        if r.provider:
            line += f" provider={r.provider}"
        if r.input_folder:
            line += f" input={Path(r.input_folder).name}"
        if r.gender:
            line += f" gender={r.gender}"
        if r.metadata_path:
            line += f" meta={r.metadata_path.name}"
        if r.output_path:
            line += f" -> {r.output_path}"
        if r.identity_similarity or r.identity_notes:
            line += (
                f" identity={r.identity_similarity:.3f}"
                f" (threshold={r.identity_threshold:.2f}, pass={r.identity_pass})"
            )
        if r.reason:
            line += f" ({r.reason})"
        logger.info(line)

    if any(r.status == "ERROR" for r in results):
        return 1
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        logger.error("Test abortito: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
