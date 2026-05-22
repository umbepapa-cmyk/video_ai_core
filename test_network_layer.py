"""
Network Layer Implementation Test
==================================
Test script per verificare che tutte le implementazioni reali funzionino correttamente.

IMPORTANTE: Questo test richiede FAL_KEY valida in .env
"""

import asyncio
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

async def test_imports():
    """Test 1: Verify all imports work."""
    logger.info("\n" + "="*70)
    logger.info("TEST 1: Verify Imports")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
        logger.info("✓ core_engine imports successful")
        
        from animatediff_engine import AnimateDiffEngine, AnimateDiffConfig, MotionPreset
        logger.info("✓ animatediff_engine imports successful")
        
        import fal_client
        logger.info("✓ fal_client available")
        
        import httpx
        logger.info("✓ httpx available")
        
        import aiofiles
        logger.info("✓ aiofiles available")
        
        return True
    except Exception as e:
        logger.error(f"✗ Import failed: {e}")
        return False


async def test_api_key():
    """Test 2: Verify API key is set."""
    logger.info("\n" + "="*70)
    logger.info("TEST 2: Verify API Key")
    logger.info("="*70)
    
    fal_key = os.getenv("FAL_KEY")
    
    if not fal_key:
        logger.error("✗ FAL_KEY not set in .env")
        logger.error("  Please set FAL_KEY in .env file")
        return False
    
    if fal_key == "your_fal_api_key_here":
        logger.error("✗ FAL_KEY is placeholder value")
        logger.error("  Please replace with real API key from https://fal.ai/dashboard/keys")
        return False
    
    logger.info(f"✓ FAL_KEY is set: {fal_key[:10]}...{fal_key[-10:]}")
    return True


async def test_retry_logic():
    """Test 3: Verify retry logic function."""
    logger.info("\n" + "="*70)
    logger.info("TEST 3: Verify Retry Logic")
    logger.info("="*70)
    
    from core_engine import retry_with_backoff
    from animatediff_engine import retry_with_backoff as retry_anim
    
    # Test successful call
    async def success_func():
        return "success"
    
    result = await retry_with_backoff(success_func, max_retries=3)
    if result == "success":
        logger.info("✓ Retry logic works for successful calls")
    else:
        logger.error("✗ Retry logic failed for successful calls")
        return False
    
    # Test retry on failure
    attempt_count = [0]
    
    async def fail_then_succeed():
        attempt_count[0] += 1
        if attempt_count[0] < 2:
            raise ValueError("Intentional failure")
        return "success after retry"
    
    result = await retry_with_backoff(
        fail_then_succeed,
        max_retries=3,
        initial_delay=0.1,
        exceptions=(ValueError,)
    )
    
    if result == "success after retry" and attempt_count[0] == 2:
        logger.info(f"✓ Retry logic works with failures (attempts: {attempt_count[0]})")
    else:
        logger.error("✗ Retry logic failed with failures")
        return False
    
    return True


async def test_core_engine_initialization():
    """Test 4: Verify CoreEngine initialization."""
    logger.info("\n" + "="*70)
    logger.info("TEST 4: CoreEngine Initialization")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine, CoreEngineConfig, QualityPreset
        
        config = CoreEngineConfig(
            reference_faces_dir="./test_faces",
            num_angles=5,
            duration_seconds=10.0,
            quality_preset=QualityPreset.HIGH,
            output_path="./test_output"
        )
        
        engine = CoreEngine(config=config)
        
        logger.info("✓ CoreEngine initialized successfully")
        logger.info(f"  API Key: {'Set' if engine.api_key else 'Missing'}")
        logger.info(f"  Output path: {config.output_path}")
        logger.info(f"  Quality: {config.quality_preset.value}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ CoreEngine initialization failed: {e}")
        return False


async def test_animatediff_initialization():
    """Test 5: Verify AnimateDiff initialization."""
    logger.info("\n" + "="*70)
    logger.info("TEST 5: AnimateDiff Initialization")
    logger.info("="*70)
    
    try:
        from animatediff_engine import AnimateDiffEngine, AnimateDiffConfig, MotionPreset
        
        config = AnimateDiffConfig(
            duration_seconds=5.0,
            fps=24,
            motion_preset=MotionPreset.CINEMATIC
        )
        
        engine = AnimateDiffEngine(default_config=config)
        
        logger.info("✓ AnimateDiffEngine initialized successfully")
        logger.info(f"  API Key: {'Set' if engine.api_key else 'Missing'}")
        logger.info(f"  Duration: {config.duration_seconds}s")
        logger.info(f"  Motion: {config.motion_preset.value}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ AnimateDiffEngine initialization failed: {e}")
        return False


async def test_method_signatures():
    """Test 6: Verify all methods have correct signatures."""
    logger.info("\n" + "="*70)
    logger.info("TEST 6: Method Signatures")
    logger.info("="*70)
    
    try:
        from core_engine import CoreEngine
        from animatediff_engine import AnimateDiffEngine
        import inspect
        
        # Check CoreEngine methods
        core_methods = {
            '_generate_first_frame': ['prompts', 'identity_vector', 'controlnet_data'],
            '_generate_single_video': ['first_frame_url', 'prompts', 'identity_vector', 'duration'],
            '_finalize_video': ['video_result', 'output_path'],
        }
        
        for method_name, expected_params in core_methods.items():
            method = getattr(CoreEngine, method_name)
            sig = inspect.signature(method)
            params = [p for p in sig.parameters.keys() if p != 'self']
            
            if all(ep in params for ep in expected_params):
                logger.info(f"✓ CoreEngine.{method_name} signature correct")
            else:
                logger.error(f"✗ CoreEngine.{method_name} signature mismatch")
                logger.error(f"  Expected params: {expected_params}")
                logger.error(f"  Actual params: {params}")
                return False
        
        # Check AnimateDiff methods
        anim_methods = {
            'extract_last_frame': ['video_url', 'output_path'],
            '_call_animatediff_api': ['payload'],
        }
        
        for method_name, expected_params in anim_methods.items():
            method = getattr(AnimateDiffEngine, method_name)
            sig = inspect.signature(method)
            params = [p for p in sig.parameters.keys() if p != 'self']
            
            if all(ep in params for ep in expected_params):
                logger.info(f"✓ AnimateDiffEngine.{method_name} signature correct")
            else:
                logger.error(f"✗ AnimateDiffEngine.{method_name} signature mismatch")
                logger.error(f"  Expected params: {expected_params}")
                logger.error(f"  Actual params: {params}")
                return False
        
        # Verify extract_last_frame is async
        if inspect.iscoroutinefunction(AnimateDiffEngine.extract_last_frame):
            logger.info("✓ AnimateDiffEngine.extract_last_frame is async")
        else:
            logger.error("✗ AnimateDiffEngine.extract_last_frame is not async")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Method signature check failed: {e}")
        return False


async def test_ffmpeg_availability():
    """Test 7: Verify FFmpeg is available for extract_last_frame."""
    logger.info("\n" + "="*70)
    logger.info("TEST 7: FFmpeg Availability")
    logger.info("="*70)
    
    import subprocess
    
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"✓ FFmpeg is available: {version_line}")
            return True
        else:
            logger.error("✗ FFmpeg command failed")
            return False
            
    except FileNotFoundError:
        logger.error("✗ FFmpeg not found in PATH")
        logger.error("  Install FFmpeg:")
        logger.error("    - Linux: apt install ffmpeg")
        logger.error("    - macOS: brew install ffmpeg")
        logger.error("    - Windows: Download from ffmpeg.org")
        return False
    except Exception as e:
        logger.error(f"✗ FFmpeg check failed: {e}")
        return False


async def main():
    """Run all tests."""
    logger.info("\n" + "="*80)
    logger.info(" NETWORK LAYER IMPLEMENTATION - TEST SUITE")
    logger.info("="*80)
    logger.info("\nThis test suite verifies that all network layer implementations")
    logger.info("are correctly configured (but does NOT make actual API calls)")
    logger.info("="*80)
    
    tests = [
        ("Imports", test_imports),
        ("API Key", test_api_key),
        ("Retry Logic", test_retry_logic),
        ("CoreEngine Init", test_core_engine_initialization),
        ("AnimateDiff Init", test_animatediff_initialization),
        ("Method Signatures", test_method_signatures),
        ("FFmpeg", test_ffmpeg_availability),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"\n✗ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
    
    # Print summary
    logger.info("\n" + "="*80)
    logger.info(" TEST SUMMARY")
    logger.info("="*80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {test_name}")
    
    logger.info("="*80)
    logger.info(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("\n✅ All tests PASSED! Network layer implementation is ready.")
        logger.info("\nNext steps:")
        logger.info("  1. Test with real API calls: python core_engine.py")
        logger.info("  2. Monitor API usage at https://fal.ai/dashboard")
        logger.info("  3. Check logs for any issues")
    else:
        logger.info(f"\n❌ {total - passed} test(s) FAILED. Please fix issues before proceeding.")
    
    logger.info("="*80 + "\n")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
