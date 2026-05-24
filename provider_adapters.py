"""Fal.ai provider adapters for face swap and related media operations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent

FAL_FACE_SWAP_IMAGE_ENDPOINT = "fal-ai/face-swap"
FAL_FACE_SWAP_VIDEO_ENDPOINT = "fal-ai/pixverse/swap"

FLUX_REALISM_LORA: Dict[str, Any] = {
    "path": "https://huggingface.co/XLabs-AI/flux-RealismLora/resolve/main/lora.safetensors",
    "scale": 0.6,
}

DEFAULT_LORA_IDENTITY_SCALE = 1.0
DEFAULT_REALISM_LORA_SCALE = 0.6

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"}


def _configure_fal_key(api_key: Optional[str] = None) -> None:
    if api_key:
        os.environ.setdefault("FAL_KEY", api_key)


def _is_video_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS)


def _extract_image_url(result: Dict[str, Any]) -> Optional[str]:
    image = result.get("image")
    if isinstance(image, dict):
        url = image.get("url")
        if url and str(url).startswith("http"):
            return str(url)
    return None


def _extract_video_url(result: Dict[str, Any]) -> Optional[str]:
    video = result.get("video")
    if isinstance(video, dict):
        url = video.get("url")
        if url and str(url).startswith("http"):
            return str(url)
    if isinstance(video, str) and video.startswith("http"):
        return video
    return None


def _looks_like_no_face_swap(base_url: str, output_url: str) -> bool:
    """Heuristic: fal-ai/face-swap returns base image when no face is detected."""
    if not output_url:
        return True
    if base_url == output_url:
        return True
    base_name = urlparse(base_url).path.rsplit("/", 1)[-1]
    out_name = urlparse(output_url).path.rsplit("/", 1)[-1]
    return bool(base_name and base_name == out_name)


async def apply_fal_face_swap(
    *,
    image_or_video_url: str,
    face_image_url: str,
    target_face_index: Optional[int] = None,
    require_face: bool = True,
    timeout: int = 600,
    api_key: Optional[str] = None,
) -> str:
    """
    Swap a reference face onto an image or video via Fal.ai.

    Images use ``fal-ai/face-swap`` (base_image_url + swap_image_url).
    Videos use ``fal-ai/pixverse/swap`` (Fal video endpoint; ``fal-ai/face-swap``
    accepts images only).

    Raises IdentityConditioningError on API failure or when no face swap occurs.
    """
    from exceptions import IdentityConditioningError

    if not require_face:
        raise ValueError(
            "require_face=False is no longer supported; face swap must succeed or raise"
        )

    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError(
            "fal_client not available. Install with: pip install fal-client"
        ) from exc

    token = (api_key or os.getenv("FAL_KEY", "")).strip()
    if not token:
        raise IdentityConditioningError("FAL_KEY not set for Fal face swap")

    _configure_fal_key(token)
    is_video = _is_video_url(image_or_video_url)

    if is_video:
        endpoint = FAL_FACE_SWAP_VIDEO_ENDPOINT
        payload: Dict[str, Any] = {
            "video_url": image_or_video_url,
            "image_url": face_image_url,
        }
        if target_face_index is not None:
            payload["keyframe_id"] = target_face_index
    else:
        endpoint = FAL_FACE_SWAP_IMAGE_ENDPOINT
        payload = {
            "base_image_url": image_or_video_url,
            "swap_image_url": face_image_url,
        }

    logger.info(
        "[FACE_SWAP] Fal %s target_face_index=%s require_face=%s",
        endpoint,
        target_face_index,
        require_face,
    )

    try:
        handler = await fal_client.submit_async(endpoint, arguments=payload)
        result = await asyncio.wait_for(handler.get(), timeout=timeout)
    except Exception as exc:
        raise IdentityConditioningError(
            f"Fal face swap failed ({endpoint}): {exc}",
            target_face_index=target_face_index,
        ) from exc

    if not isinstance(result, dict):
        raise IdentityConditioningError(
            f"Unexpected Fal face swap response type: {type(result)!r}",
            target_face_index=target_face_index,
        )

    if is_video:
        output_url = _extract_video_url(result)
        if not output_url:
            raise IdentityConditioningError(
                f"No video URL in Fal face swap response: {result!r}",
                target_face_index=target_face_index,
            )
        if _looks_like_no_face_swap(image_or_video_url, output_url):
            raise IdentityConditioningError(
                "Fal video face swap returned unchanged input (no face detected)",
                target_face_index=target_face_index,
            )
        return output_url

    output_url = _extract_image_url(result)
    if not output_url:
        raise IdentityConditioningError(
            f"No image URL in Fal face swap response: {result!r}",
            target_face_index=target_face_index,
        )

    if _looks_like_no_face_swap(image_or_video_url, output_url):
        raise IdentityConditioningError(
            "Fal face swap returned unchanged base image (no face detected)",
            target_face_index=target_face_index,
        )

    logger.info("Fal face swap succeeded via %s", endpoint)
    return output_url


def load_lora_metadata(metadata_path: Union[str, Path]) -> Dict[str, Any]:
    """Load LoRA training metadata JSON produced by train_lora_replicate.py."""
    path = Path(metadata_path)
    if not path.is_file():
        raise FileNotFoundError(f"LoRA metadata not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid LoRA metadata (expected object): {path}")
    return data


def _resolve_lora_weights_url(metadata: Dict[str, Any]) -> Optional[str]:
    output = metadata.get("output")
    if isinstance(output, dict):
        nested_weights = output.get("weights")
        if isinstance(nested_weights, str) and nested_weights.startswith("http"):
            return nested_weights
        version_ref = output.get("version")
        if isinstance(version_ref, str) and "/" in version_ref and ":" in version_ref:
            return version_ref

    for key in ("weights_url", "replicate_weights"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            if key == "replicate_weights" and value.count(":") == 1:
                owner_model, version_id = value.split(":", 1)
                if owner_model.count("/") != 1:
                    continue
                # Skip trainer-version slugs mistakenly stored here
                if version_id.startswith("26dce37a"):
                    continue
            return value.strip()

    if isinstance(output, str) and output.startswith("http"):
        return output
    return None


_LORA_FAL_CACHE: Dict[str, str] = {}


def _download_to_temp(url: str, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    path = Path(tmp.name)
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def _extract_safetensors_from_tar(tar_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as archive:
        members = [
            m for m in archive.getmembers()
            if m.isfile() and m.name.lower().endswith(".safetensors")
        ]
        if not members:
            raise ValueError(f"Nessun .safetensors in {tar_path}")
        member = max(members, key=lambda m: m.size)
        archive.extract(member, path=dest_dir)
        extracted = dest_dir / member.name
        if not extracted.is_file():
            matches = list(dest_dir.rglob("*.safetensors"))
            if not matches:
                raise ValueError(f"Estrazione tar fallita: {tar_path}")
            extracted = matches[0]
    return extracted


def _upload_local_to_fal(local_path: Path) -> str:
    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal_client required for LoRA upload") from exc

    token = os.getenv("FAL_KEY", "").strip()
    if token:
        os.environ.setdefault("FAL_KEY", token)

    uploaded = fal_client.upload_file(str(local_path))
    if isinstance(uploaded, str):
        return uploaded
    if isinstance(uploaded, dict):
        url = uploaded.get("url")
        if url:
            return str(url)
    raise RuntimeError(f"Unexpected fal upload response: {uploaded!r}")


def resolve_lora_weights_for_fal(
    weights_url: str,
    *,
    cache_dir: Optional[Union[str, Path]] = None,
) -> str:
    """
    Return a Fal-compatible LoRA URL.

    - HTTP .safetensors: use directly (or upload if local path)
    - Replicate trained_model.tar: download, extract safetensors, upload to Fal CDN
    - owner/model:version replicate ref: pass through (Fal accepts)
    """
    if not weights_url or not str(weights_url).strip():
        raise ValueError("weights_url is required")

    url = str(weights_url).strip()
    if url in _LORA_FAL_CACHE:
        return _LORA_FAL_CACHE[url]

    local = Path(url)
    if local.is_file():
        if local.suffix.lower() == ".safetensors":
            resolved = _upload_local_to_fal(local)
            _LORA_FAL_CACHE[url] = resolved
            return resolved
        if local.suffix.lower() == ".tar":
            cache_root = Path(cache_dir) if cache_dir else PROJECT_ROOT / "models" / "lora_cache"
            cache_root.mkdir(parents=True, exist_ok=True)
            extracted = _extract_safetensors_from_tar(local, cache_root / local.stem)
            resolved = _upload_local_to_fal(extracted)
            _LORA_FAL_CACHE[url] = resolved
            return resolved

    if url.count(":") == 1 and "/" in url and not url.startswith("http"):
        _LORA_FAL_CACHE[url] = url
        return url

    if url.endswith(".safetensors") and url.startswith("http"):
        _LORA_FAL_CACHE[url] = url
        return url

    if url.startswith("http") and (".tar" in url or url.endswith(".tar")):
        cache_root = Path(cache_dir) if cache_dir else PROJECT_ROOT / "models" / "lora_cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        tar_path = _download_to_temp(url, ".tar")
        try:
            extracted = _extract_safetensors_from_tar(tar_path, cache_root / "extracted")
            resolved = _upload_local_to_fal(extracted)
        finally:
            try:
                tar_path.unlink(missing_ok=True)
            except OSError:
                pass
        _LORA_FAL_CACHE[url] = resolved
        logger.info("[LORA] Risolto tar -> Fal CDN: %s", resolved[:80])
        return resolved

    if url.startswith("http"):
        _LORA_FAL_CACHE[url] = url
        return url

    raise ValueError(f"Unsupported LoRA weights URL: {url!r}")


def build_flux_loras_array(
    metadata_paths: Sequence[Union[str, Path]],
    *,
    identity_scale: float = DEFAULT_LORA_IDENTITY_SCALE,
    include_realism_lora: bool = True,
    realism_scale: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Build fal-ai/flux/dev ``loras`` payload from subject metadata JSON files.

    Each metadata file must contain ``weights_url`` (HTTP safetensors) or
    ``replicate_weights`` (owner/model:version — Fal accepts both forms).
    """
    loras: List[Dict[str, Any]] = []

    if include_realism_lora:
        realism = dict(FLUX_REALISM_LORA)
        realism["scale"] = realism_scale if realism_scale is not None else DEFAULT_REALISM_LORA_SCALE
        loras.append(realism)

    for metadata_path in metadata_paths:
        metadata = load_lora_metadata(metadata_path)
        weights_url = _resolve_lora_weights_url(metadata)
        if not weights_url:
            trigger = metadata.get("trigger_word", metadata_path)
            raise ValueError(
                f"No weights URL in LoRA metadata for {trigger!r} ({metadata_path})"
            )
        fal_path = resolve_lora_weights_for_fal(weights_url)
        loras.append({"path": fal_path, "scale": identity_scale})
        logger.info(
            "[LORA] Loaded identity LoRA trigger=%s scale=%.2f path=%s",
            metadata.get("trigger_word"),
            identity_scale,
            fal_path[:80],
        )

    return loras


def build_flux_loras_from_config(
    lora_metadata_paths: Optional[Dict[str, str]],
    *,
    identity_scale: float = DEFAULT_LORA_IDENTITY_SCALE,
    include_realism_lora: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """Return Flux loras array when config maps subject_id -> metadata JSON path."""
    if not lora_metadata_paths:
        return None
    paths = [p for p in lora_metadata_paths.values() if p]
    if not paths:
        return None
    return build_flux_loras_array(
        paths,
        identity_scale=identity_scale,
        include_realism_lora=include_realism_lora,
    )
