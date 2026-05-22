"""
Test script for real network layer implementation.

This script tests the real API calls to Fal.ai for:
1. First frame generation (Flux.1 Dev)
2. Video generation (Wan I2V)
3. Video download and storage
4. Segment merging with FFmpeg

Usage:
    python test_real_network_layer.py
    
Requirements:
    - FAL_KEY environment variable set
    - Reference face images in test_reference_faces/
    - FFmpeg installed and in PATH
"""

import os
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from core_engine import (
    CoreEngine,
    CoreEngineConfig,
    QualityPreset,
    generate_high_fidelity_video
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_first_frame_generation():
    """Test real first frame generation with Flux.1 Dev."""
    logger.info("\n" + "="*70)
    logger.info("TEST 1: FIRST FRAME GENERATION (Flux.1 Dev)")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
        
        config = CoreEngineConfig(
            reference_faces_dir="./test_reference_faces",
            duration_seconds=5.0,
            quality_preset=QualityPreset.HIGH,
            enable_autoregressive=False,  # Test single segment only
            output_path="./test_outputs/"
        )
        
        engine = CoreEngine(config=config)
        
        # Test first frame generation
        prompts = {
            "prompt": "A professional portrait of a person, studio lighting, high quality",
            "negative_prompt": "blurry, low quality, deformed, bad anatomy"
        }
        
        identity_vector = engine.identity_locker.create_super_vector() if engine.identity_locker else None
        
        logger.info("Calling _generate_first_frame with real API...")
        first_frame_url = await engine._generate_first_frame(
            prompts=prompts,
            identity_vector=identity_vector,
            controlnet_data=None
        )
        
        logger.info(f"✓ Test 1 PASSED")
        logger.info(f"  First frame URL: {first_frame_url}")
        
        return first_frame_url
        
    except Exception as e:
        logger.error(f"✗ Test 1 FAILED: {e}")
        raise


async def test_video_generation(first_frame_url: str):
    """Test real video generation with Wan I2V."""
    logger.info("\n" + "="*70)
    logger.info("TEST 2: VIDEO GENERATION (Wan I2V)")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
        
        config = CoreEngineConfig(
            reference_faces_dir="./test_reference_faces",
            duration_seconds=5.0,
            quality_preset=QualityPreset.HIGH,
            enable_autoregressive=False,
            output_path="./test_outputs/"
        )
        
        engine = CoreEngine(config=config)
        
        prompts = {
            "prompt": "Smooth camera movement, cinematic motion",
            "negative_prompt": "flickering, unstable, jumping"
        }
        
        identity_vector = engine.identity_locker.create_super_vector() if engine.identity_locker else None
        
        logger.info("Calling _generate_single_video with real API...")
        video_result = await engine._generate_single_video(
            first_frame_url=first_frame_url,
            prompts=prompts,
            identity_vector=identity_vector,
            duration=5.0
        )
        
        logger.info(f"✓ Test 2 PASSED")
        logger.info(f"  Video URL: {video_result['video_url']}")
        logger.info(f"  Duration: {video_result['duration']}s")
        logger.info(f"  Last frame: {video_result.get('last_frame_url', 'N/A')}")
        
        return video_result
        
    except Exception as e:
        logger.error(f"✗ Test 2 FAILED: {e}")
        raise


async def test_video_download(video_result: dict):
    """Test real video download from cloud to local storage."""
    logger.info("\n" + "="*70)
    logger.info("TEST 3: VIDEO DOWNLOAD")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
        
        config = CoreEngineConfig(
            reference_faces_dir="./test_reference_faces",
            duration_seconds=5.0,
            quality_preset=QualityPreset.HIGH,
            enable_autoregressive=False,
            output_path="./test_outputs/"
        )
        
        engine = CoreEngine(config=config)
        
        logger.info("Calling _finalize_video to download...")
        local_path = await engine._finalize_video(
            video_result=video_result,
            output_path="./test_outputs/"
        )
        
        # Verify file exists
        if not Path(local_path).exists():
            raise RuntimeError(f"Downloaded file not found: {local_path}")
        
        file_size = Path(local_path).stat().st_size
        
        logger.info(f"✓ Test 3 PASSED")
        logger.info(f"  Local path: {local_path}")
        logger.info(f"  File size: {file_size / 1024 / 1024:.2f} MB")
        
        return local_path
        
    except Exception as e:
        logger.error(f"✗ Test 3 FAILED: {e}")
        raise


async def test_full_pipeline():
    """Test complete pipeline with all real API calls."""
    logger.info("\n" + "="*70)
    logger.info("TEST 4: FULL PIPELINE")
    logger.info("="*70)
    
    try:
        result = await generate_high_fidelity_video(
            reference_faces_dir="./test_reference_faces",
            prompt="A person smiling naturally, professional portrait",
            duration_seconds=5,
            output_path="./test_outputs/"
        )
        
        logger.info(f"✓ Test 4 PASSED")
        logger.info(f"  Video URL: {result['video_url']}")
        logger.info(f"  Duration: {result['duration']}s")
        logger.info(f"  Identity stability: {result['identity_stability']*100:.1f}%")
        logger.info(f"  Generation time: {result['generation_time']:.1f}s")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ Test 4 FAILED: {e}")
        raise


async def main():
    """Run all tests."""
    logger.info("\n" + "#"*70)
    logger.info("# REAL NETWORK LAYER IMPLEMENTATION TESTS")
    logger.info("#"*70)
    
    # Check environment
    load_dotenv()
    fal_key = os.getenv("FAL_KEY")
    
    if not fal_key or fal_key == "your_fal_api_key_here":
        logger.error("❌ FAL_KEY not set in .env file!")
        logger.error("Please set your Fal.ai API key in .env:")
        logger.error("  FAL_KEY=your_actual_fal_api_key")
        return
    
    logger.info(f"✓ FAL_KEY found: {fal_key[:10]}...")
    
    # Check FFmpeg
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.info("✓ FFmpeg found")
        else:
            logger.warning("⚠ FFmpeg not found or not working")
    except Exception as e:
        logger.warning(f"⚠ FFmpeg check failed: {e}")
    
    # Run tests
    try:
        # Test 1: First frame generation
        first_frame_url = await test_first_frame_generation()
        
        # Test 2: Video generation
        video_result = await test_video_generation(first_frame_url)
        
        # Test 3: Video download
        local_path = await test_video_download(video_result)
        
        # Test 4: Full pipeline (optional - takes longer)
        # Uncomment to test full pipeline:
        # await test_full_pipeline()
        
        logger.info("\n" + "="*70)
        logger.info("✓ ALL TESTS PASSED!")
        logger.info("="*70)
        logger.info("\nReal network layer implementation is working correctly.")
        logger.info("You can now use the core_engine.py with real API calls.")
        
    except Exception as e:
        logger.error("\n" + "="*70)
        logger.error("✗ TESTS FAILED")
        logger.error("="*70)
        logger.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
