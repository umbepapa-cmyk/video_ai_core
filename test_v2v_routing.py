#!/usr/bin/env python3

"""

Fase 3.7/3.8 — Dynamic V2V/I2V routing unit tests.



Run:

    python test_v2v_routing.py

"""



from __future__ import annotations



import logging



from i2v_router import I2VContext, resolve_generation_mode

from prompt_enhancement import BODY_CONSISTENCY_SUFFIX

from provider_adapters import (

    prepare_i2v_payload_fal,

    prepare_v2v_payload_fal,

    V2V_IP_ADAPTER_SCALE,

    V2V_MOTION_STRENGTH,

    V2V_POSE_STRENGTH,

)



logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

logger = logging.getLogger(__name__)





def test_v2v_mode_with_motion_path():

    face_url = "https://cdn.example.com/face_crop.jpg"

    full_body_url = "https://cdn.example.com/full_body.jpg"

    ctx = I2VContext(

        image_url="https://cdn.example.com/first_frame.jpg",

        prompt="dancer spinning in rain",

        duration=5.0,

        motion_reference_video_path="/tmp/motion_ref.mp4",

        motion_reference_video_url="https://cdn.example.com/motion.mp4",

        face_reference_url=face_url,

        full_body_reference_url=full_body_url,

        reference_image_url=face_url,

    )

    assert ctx.generation_mode == "v2v"

    assert resolve_generation_mode(ctx) == "v2v"



    payload = prepare_v2v_payload_fal(ctx, "wan-animate-replace")

    assert payload["image_url"] == full_body_url

    assert payload["ip_adapter_image"] == full_body_url

    assert payload["reference_image_url"] == full_body_url

    assert payload["image_prompt"] == full_body_url

    assert payload["face_image"] == face_url

    assert payload["video_url"] == ctx.motion_reference_video_url

    assert payload["control_video"] == ctx.motion_reference_video_url

    assert payload["motion_strength"] == V2V_MOTION_STRENGTH

    assert payload["pose_strength"] == V2V_POSE_STRENGTH

    assert payload["ip_adapter_scale"] == V2V_IP_ADAPTER_SCALE

    assert payload["image_url"] != face_url

    assert BODY_CONSISTENCY_SUFFIX.strip() in payload["prompt"]

    logger.info("[OK] V2V payload uses full_body over face crop")





def test_v2v_single_input_full_body_wins():

    full_body_url = "https://cdn.example.com/full_body_only.jpg"

    ctx = I2VContext(

        image_url="https://cdn.example.com/first_frame.jpg",

        prompt="motion test",

        duration=5.0,

        motion_reference_video_url="https://cdn.example.com/motion.mp4",

        full_body_reference_url=full_body_url,

    )

    payload = prepare_v2v_payload_fal(ctx, "wan-animate-replace")

    assert payload["image_url"] == full_body_url

    assert "face_image" not in payload

    logger.info("[OK] Single-input V2V uses full_body only")





def test_i2v_mode_without_motion():

    ctx = I2VContext(

        image_url="https://cdn.example.com/frame.jpg",

        prompt="cinematic motion",

        duration=5.0,

    )

    assert ctx.generation_mode == "i2v"

    assert resolve_generation_mode(ctx) == "i2v"



    payload = prepare_i2v_payload_fal(ctx, "wan21-i2v")

    assert payload["image_url"] == ctx.image_url

    assert "video_url" not in payload

    assert "control_video" not in payload

    logger.info("[OK] I2V payload keys: %s", sorted(payload.keys()))





def main() -> None:

    test_v2v_mode_with_motion_path()

    test_v2v_single_input_full_body_wins()

    test_i2v_mode_without_motion()

    logger.info("All V2V routing tests passed.")





if __name__ == "__main__":

    main()

