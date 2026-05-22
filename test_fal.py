"""
Day 1 smoke test: Flux.1 Dev image generation via Fal.ai.
Run from AppVideoAI with venv activated: python test_fal.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import fal_client

PROJECT_DIR = Path(__file__).resolve().parent
PLACEHOLDER = "your_fal_api_key_here"

PROMPT = (
    "A highly detailed portrait of a muscular man, "
    "hyperrealistic skin textures, 8k resolution"
)


def load_api_key() -> str:
    load_dotenv(PROJECT_DIR / ".env")
    key = os.getenv("FAL_KEY", "").strip()
    if not key or key == PLACEHOLDER:
        print(
            "ERROR: FAL_KEY is missing or still the placeholder.\n"
            "Open AppVideoAI/.env and paste your real Fal.ai API key.",
            file=sys.stderr,
        )
        sys.exit(1)
    os.environ["FAL_KEY"] = key
    return key


def on_queue_update(update) -> None:
    if isinstance(update, fal_client.InProgress):
        for log in update.logs:
            print(log["message"])


def main() -> None:
    load_api_key()

    print("Submitting request to fal-ai/flux/dev ...")
    print(f"Prompt: {PROMPT}\n")

    result = fal_client.subscribe(
        "fal-ai/flux/dev",
        arguments={
            "prompt": PROMPT,
            "image_size": "landscape_4_3",
            "num_inference_steps": 28,
            "num_images": 1,
            "enable_safety_checker": False,
        },
        with_logs=True,
        on_queue_update=on_queue_update,
    )

    images = result.get("images") or []
    if not images:
        print("ERROR: No images returned.", file=sys.stderr)
        print(result, file=sys.stderr)
        sys.exit(1)

    image_url = images[0].get("url")
    if not image_url:
        print("ERROR: Image entry has no URL.", file=sys.stderr)
        print(images[0], file=sys.stderr)
        sys.exit(1)

    print("\n--- Success ---")
    print("Generated image URL:")
    print(image_url)


if __name__ == "__main__":
    main()
