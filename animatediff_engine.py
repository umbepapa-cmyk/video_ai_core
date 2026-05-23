"""
WEEK 1 V2 - DAY 5: AnimateDiff Video Pipeline
==============================================
Module for high-fidelity video generation using AnimateDiff.

This module implements:
- I2V (Image-to-Video) pipeline with AnimateDiff
- Strong temporal conditioning for anatomical stability
- High-fidelity frame t=0 initialization
- Identity adapter weight locking across all frames
- Complex cinematic motion with identity preservation
"""

import os
import logging
from typing import Optional, Dict, Any, List, Tuple, Callable
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import asyncio

import numpy as np

from dotenv import load_dotenv

try:
    import fal_client
except ImportError:
    fal_client = None
    logging.warning("fal_client not installed. Install with: pip install fal-client")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple = (Exception,)
) -> Any:
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        backoff_factor: Multiplier for each retry
        exceptions: Tuple of exceptions to catch and retry
        
    Returns:
        Result from successful function call
        
    Raises:
        Last exception if all retries fail
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await func()
        except exceptions as e:
            last_exception = e
            if attempt < max_retries - 1:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                logger.info(f"Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay *= backoff_factor
            else:
                logger.error(f"All {max_retries} attempts failed")
    
    raise last_exception


class MotionPreset(Enum):
    """Preset motion patterns for AnimateDiff."""
    STATIC = "static"
    SUBTLE = "subtle"
    SMOOTH = "smooth"
    CINEMATIC = "cinematic"
    DYNAMIC = "dynamic"
    CUSTOM = "custom"


@dataclass
class AnimateDiffConfig:
    """Configuration for AnimateDiff generation."""
    duration_seconds: float = 5.0
    fps: int = 24
    motion_preset: MotionPreset = MotionPreset.CINEMATIC
    motion_scale: float = 1.0
    temporal_consistency: float = 0.9
    identity_adapter_strength: float = 0.95
    noise_schedule: str = "cosine"
    num_inference_steps: int = 25


@dataclass
class VideoGenerationResult:
    """Result from AnimateDiff video generation."""
    video_url: str
    duration_seconds: float
    fps: int
    num_frames: int
    first_frame_url: Optional[str]
    last_frame_url: Optional[str]
    metadata: Dict[str, Any]


class TemporalConsistencyController:
    """
    Controller for maintaining temporal consistency in video generation.
    
    Ensures:
    - Identity preservation across all frames
    - Smooth motion without flickering
    - Anatomical stability during movement
    """
    
    def __init__(self, consistency_strength: float = 0.9):
        """
        Initialize temporal consistency controller.
        
        Args:
            consistency_strength: Strength of consistency enforcement (0.0-1.0)
        """
        self.consistency_strength = consistency_strength
        
        logger.info(f"TemporalConsistencyController initialized (strength={consistency_strength})")
    
    def apply_temporal_conditioning(
        self,
        frame_embeddings: List[np.ndarray],
        identity_vector: Optional[np.ndarray] = None
    ) -> List[np.ndarray]:
        """
        Apply temporal conditioning to frame embeddings.
        
        Args:
            frame_embeddings: List of frame embedding vectors
            identity_vector: Optional identity super-vector for locking
            
        Returns:
            Conditioned frame embeddings
        """
        logger.info(f"Applying temporal conditioning to {len(frame_embeddings)} frames")
        
        conditioned = []
        
        for i, embedding in enumerate(frame_embeddings):
            # Apply identity lock if provided
            if identity_vector is not None:
                # Blend embedding with identity vector
                alpha = self.consistency_strength
                conditioned_emb = alpha * identity_vector + (1 - alpha) * embedding
                conditioned_emb = conditioned_emb / np.linalg.norm(conditioned_emb)
            else:
                conditioned_emb = embedding
            
            conditioned.append(conditioned_emb)
        
        logger.info("Temporal conditioning applied")
        
        return conditioned
    
    def calculate_temporal_loss(
        self,
        frame_embeddings: List[np.ndarray]
    ) -> float:
        """
        Calculate temporal consistency loss between consecutive frames.
        
        Args:
            frame_embeddings: List of frame embeddings
            
        Returns:
            Average temporal loss (lower is better)
        """
        if len(frame_embeddings) < 2:
            return 0.0
        
        losses = []
        
        for i in range(len(frame_embeddings) - 1):
            # Cosine distance between consecutive frames
            similarity = np.dot(frame_embeddings[i], frame_embeddings[i + 1])
            loss = 1.0 - similarity
            losses.append(loss)
        
        avg_loss = np.mean(losses)
        
        logger.debug(f"Temporal consistency loss: {avg_loss:.4f}")
        
        return float(avg_loss)


class AnimateDiffEngine:
    """
    High-fidelity video generation engine using AnimateDiff.
    
    Features:
    - Image-to-Video generation with temporal consistency
    - Identity preservation across all frames
    - Cinematic motion with anatomical stability
    - Support for custom motion patterns
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        default_config: Optional[AnimateDiffConfig] = None
    ):
        """
        Initialize AnimateDiff engine.
        
        Args:
            api_key: API key for AnimateDiff endpoints
            default_config: Default configuration for generation
        """
        load_dotenv()
        
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("REPLICATE_API_TOKEN")
        self.config = default_config or AnimateDiffConfig()
        
        self.consistency_controller = TemporalConsistencyController(
            consistency_strength=self.config.temporal_consistency
        )
        
        logger.info("AnimateDiffEngine initialized")
        logger.info(f"  Duration: {self.config.duration_seconds}s @ {self.config.fps} fps")
        logger.info(f"  Motion: {self.config.motion_preset.value} (scale={self.config.motion_scale})")
    
    def init_animatediff(
        self,
        first_frame_url: str,
        identity_vector: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """
        Initialize AnimateDiff pipeline with first frame.
        
        Args:
            first_frame_url: URL of high-fidelity first frame (t=0)
            identity_vector: Optional identity super-vector for locking
            
        Returns:
            Initialization payload
        """
        logger.info("Initializing AnimateDiff pipeline")
        logger.info(f"  First frame: {first_frame_url}")
        
        payload = {
            "first_frame_url": first_frame_url,
            "duration_seconds": self.config.duration_seconds,
            "fps": self.config.fps,
            "num_frames": int(self.config.duration_seconds * self.config.fps),
            "motion_preset": self.config.motion_preset.value,
            "motion_scale": self.config.motion_scale,
            "temporal_consistency": self.config.temporal_consistency,
            "noise_schedule": self.config.noise_schedule,
            "num_inference_steps": self.config.num_inference_steps
        }
        
        # Add identity locking if provided
        if identity_vector is not None:
            payload["identity_vector"] = identity_vector.tolist()
            payload["identity_adapter_strength"] = self.config.identity_adapter_strength
            payload["lock_identity_all_frames"] = True
            
            logger.info(f"  Identity locked across all {payload['num_frames']} frames")
        
        logger.info(f"AnimateDiff initialized: {payload['num_frames']} frames")
        
        return payload
    
    async def generate_cinematic_video(
        self,
        prompt: str,
        first_frame_url: str,
        negative_prompt: Optional[str] = None,
        identity_vector: Optional[np.ndarray] = None,
        motion_override: Optional[Dict[str, Any]] = None,
        config_override: Optional[AnimateDiffConfig] = None
    ) -> VideoGenerationResult:
        """
        Generate cinematic video with AnimateDiff.
        
        Args:
            prompt: Text prompt for video generation
            first_frame_url: URL of starting frame (high fidelity)
            negative_prompt: Negative prompt for quality control
            identity_vector: Identity super-vector for face consistency
            motion_override: Optional motion parameter overrides
            config_override: Optional config override for this generation
            
        Returns:
            VideoGenerationResult with video URL and metadata
        """
        logger.info("="*70)
        logger.info("GENERATING CINEMATIC VIDEO WITH ANIMATEDIFF")
        logger.info("="*70)
        
        # Use override config if provided
        config = config_override or self.config
        
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Duration: {config.duration_seconds}s @ {config.fps} fps")
        logger.info(f"Motion: {config.motion_preset.value}")
        
        # Initialize AnimateDiff
        init_payload = self.init_animatediff(first_frame_url, identity_vector)
        
        # Build generation payload
        payload = {
            **init_payload,
            "prompt": prompt,
            "negative_prompt": negative_prompt or self._get_default_negative_prompt(),
        }
        
        # Apply motion overrides if provided
        if motion_override:
            payload.update(motion_override)
            logger.info(f"Applied motion overrides: {motion_override}")
        
        # Call AnimateDiff API
        video_url, metadata = await self._call_animatediff_api(payload)
        
        # Calculate number of frames
        num_frames = int(config.duration_seconds * config.fps)
        
        # Create result
        result = VideoGenerationResult(
            video_url=video_url,
            duration_seconds=config.duration_seconds,
            fps=config.fps,
            num_frames=num_frames,
            first_frame_url=first_frame_url,
            last_frame_url=metadata.get("last_frame_url"),
            metadata=metadata
        )
        
        logger.info(f"Video generation complete!")
        logger.info(f"  URL: {video_url}")
        logger.info(f"  Frames: {num_frames}")
        logger.info(f"  Duration: {config.duration_seconds}s")
        logger.info("="*70)
        
        return result
    
    async def _call_animatediff_api(
        self,
        payload: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Call AnimateDiff/Wan I2V API endpoint for video generation.
        
        Args:
            payload: Generation payload
            
        Returns:
            Tuple of (video_url, metadata)
        """
        logger.info("Calling I2V fallback router for AnimateDiff-style generation...")

        if not self.api_key:
            raise ValueError("FAL_KEY not set in environment")

        from i2v_router import generate_i2v_with_fallback, motion_strength_for_preset

        motion_preset = payload.get("motion_preset", "cinematic")
        duration_seconds = float(payload.get("duration_seconds", 5))
        fps = int(payload.get("fps", 24))
        resolution = payload.get("resolution", "720p")
        timeout_multiplier = float(payload.get("timeout_multiplier", 1.0))
        draft_mode = bool(payload.get("draft_mode", resolution == "480p"))
        require_last_frame = bool(payload.get("require_last_frame", True))
        motion_strength = motion_strength_for_preset(
            motion_preset, payload.get("motion_scale", 0.6)
        )
        stage_label = payload.get("stage_label", "Generazione video")
        segment_index = int(payload.get("segment_index", 1))
        segment_total = int(payload.get("segment_total", 1))
        on_progress = payload.get("on_progress")

        async def _api_call():
            import time

            start_time = time.time()
            logger.info("Submitting via I2V fallback router...")
            logger.info(f"  Image: {payload.get('first_frame_url')}")
            logger.info(f"  Duration: {duration_seconds}s")
            logger.info(f"  Motion: {motion_preset} (strength: {motion_strength})")
            logger.info(f"  Resolution: {resolution}")

            id_raw = payload.get("identity_vector")
            identity_arr: Optional[np.ndarray] = None
            if id_raw is not None:
                identity_arr = (
                    id_raw
                    if isinstance(id_raw, np.ndarray)
                    else np.asarray(id_raw, dtype=np.float32)
                )

            result = await generate_i2v_with_fallback(
                image_url=payload.get("first_frame_url"),
                prompt=payload.get("prompt", ""),
                duration=duration_seconds,
                negative_prompt=payload.get("negative_prompt", ""),
                motion_preset=motion_preset,
                fps=fps,
                resolution=resolution,
                timeout_multiplier=timeout_multiplier,
                draft_mode=draft_mode,
                require_last_frame=require_last_frame,
                api_key=self.api_key,
                stage_label=stage_label,
                segment_index=segment_index,
                segment_total=segment_total,
                on_progress=on_progress,
                identity_vector=identity_arr,
                identity_adapter_strength=float(
                    payload.get("identity_adapter_strength", 0.95)
                ),
                controlnet_video_url=payload.get("controlnet_video_url"),
                pose_map_url=payload.get("pose_map_url"),
                num_inference_steps=payload.get("num_inference_steps"),
                motion_reference_video_path=payload.get("motion_reference_video_path"),
                provider=payload.get("i2v_provider")
                or payload.get("provider", "fal_then_replicate"),
                force_provider=payload.get("force_provider"),
                replicate_token=payload.get("replicate_token"),
                reference_image_url=payload.get("reference_image_url"),
                face_reference_url=payload.get("face_reference_url"),
                full_body_reference_url=payload.get("full_body_reference_url"),
                ip_adapter_image=payload.get("ip_adapter_image"),
            )

            generation_time = time.time() - start_time
            video_url = result["video_url"]
            last_frame_url = result.get("last_frame_url")
            if not last_frame_url and require_last_frame:
                from i2v_router import ensure_last_frame_url

                last_frame_url = await ensure_last_frame_url(
                    video_url, None, self.api_key
                )
            metadata = {
                "model": result.get("provider_id", "i2v-fallback"),
                "endpoint": result.get("endpoint_id"),
                "motion_preset": motion_preset,
                "motion_strength": motion_strength,
                "temporal_consistency": payload.get("temporal_consistency"),
                "identity_locked": "identity_vector" in payload,
                "last_frame_url": last_frame_url,
                "generation_time_seconds": generation_time,
                "resolution": resolution,
                "fps": fps,
                "duration": duration_seconds,
            }

            logger.info(f"Video generation complete ({generation_time:.1f}s)")
            logger.info(f"  Video URL: {video_url}")
            if last_frame_url:
                logger.info(f"  Last frame: {last_frame_url}")

            return video_url, metadata

        try:
            import httpx

            return await retry_with_backoff(
                _api_call,
                max_retries=1,
                initial_delay=5.0,
                backoff_factor=2.0,
                exceptions=(httpx.HTTPError, ValueError, RuntimeError),
            )
        except Exception as e:
            logger.error(f"AnimateDiff API call failed after all retries: {type(e).__name__}: {e}")
            raise RuntimeError(f"AnimateDiff API call failed: {e}") from e
    
    def _get_default_negative_prompt(self) -> str:
        """Get default negative prompt for video generation."""
        return (
            "blurry, out of focus, low quality, flickering, unstable, "
            "morphing, warping, changing identity, inconsistent, "
            "deformed, mutated, bad anatomy, extra limbs, "
            "motion blur, frame jump, jittering"
        )
    
    async def extract_last_frame(
        self,
        video_url: str,
        output_path: Optional[str] = None
    ) -> str:
        """
        Extract last frame from generated video using real download and FFmpeg.
        
        Args:
            video_url: URL of video
            output_path: Optional path to save frame
            
        Returns:
            Path to extracted last frame
        """
        import subprocess
        import tempfile
        from pathlib import Path
        import httpx
        import aiofiles
        
        logger.info(f"Extracting last frame from: {video_url}")
        
        try:
            # Create temp directory if output_path not specified
            if output_path:
                output_dir = Path(output_path)
                output_dir.mkdir(parents=True, exist_ok=True)
            else:
                output_dir = Path(tempfile.gettempdir()) / "video_frames"
                output_dir.mkdir(parents=True, exist_ok=True)
            
            # Download video to temporary location
            temp_video = output_dir / f"temp_video_{hash(video_url)}.mp4"
            
            logger.info(f"Downloading video for frame extraction...")
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", video_url) as response:
                    response.raise_for_status()
                    
                    async with aiofiles.open(temp_video, 'wb') as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            await f.write(chunk)
            
            # Extract last frame using FFmpeg
            last_frame_path = output_dir / f"last_frame_{hash(video_url)}.jpg"
            
            logger.info(f"Extracting last frame with FFmpeg...")
            
            # FFmpeg command to extract last frame
            # -sseof -1: Seek to 1 second before end
            # -frames:v 1: Extract only 1 frame
            cmd = [
                "ffmpeg",
                "-i", str(temp_video),
                "-sseof", "-1",  # Seek to end of file minus 1 second
                "-update", "1",
                "-q:v", "2",  # High quality
                str(last_frame_path),
                "-y"  # Overwrite output file
            ]
            
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if process.returncode != 0:
                logger.error(f"FFmpeg error: {process.stderr}")
                raise RuntimeError(f"FFmpeg failed to extract frame: {process.stderr}")
            
            # Clean up temp video
            if temp_video.exists():
                temp_video.unlink()
                logger.debug(f"Cleaned up temporary video: {temp_video}")
            
            # Verify frame was extracted
            if not last_frame_path.exists():
                raise IOError(f"Last frame not found: {last_frame_path}")
            
            logger.info(f"✓ Last frame extracted: {last_frame_path}")
            
            return str(last_frame_path.absolute())
            
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg extraction timed out after 30s")
            raise RuntimeError("Frame extraction timed out")
        except Exception as e:
            logger.error(f"Frame extraction failed: {type(e).__name__}: {e}")
            raise RuntimeError(f"Failed to extract last frame: {e}") from e
    
    def set_motion_preset(
        self,
        preset: MotionPreset,
        scale: float = 1.0
    ) -> None:
        """
        Set motion preset for generation.
        
        Args:
            preset: Motion preset to use
            scale: Motion scale multiplier
        """
        self.config.motion_preset = preset
        self.config.motion_scale = scale
        
        logger.info(f"Motion preset set: {preset.value} (scale={scale})")
    
    def lock_adapter_weights(
        self,
        identity_vector: np.ndarray,
        strength: float = 0.95
    ) -> Dict[str, Any]:
        """
        Lock adapter weights for identity preservation.
        
        Args:
            identity_vector: Identity super-vector
            strength: Adapter strength (0.0-1.0)
            
        Returns:
            Locking parameters
        """
        self.config.identity_adapter_strength = strength
        
        locking_params = {
            "identity_vector": identity_vector.tolist(),
            "adapter_strength": strength,
            "lock_all_frames": True,
            "temporal_consistency": self.config.temporal_consistency
        }
        
        logger.info(f"Adapter weights locked (strength={strength})")
        
        return locking_params


# Convenience functions

async def generate_video_from_image(
    image_url: str,
    prompt: str,
    duration_seconds: float = 5.0,
    fps: int = 24,
    identity_vector: Optional[np.ndarray] = None
) -> str:
    """
    Quick function to generate video from image.
    
    Args:
        image_url: Starting image URL
        prompt: Text prompt
        duration_seconds: Video duration
        fps: Frames per second
        identity_vector: Optional identity vector
        
    Returns:
        Video URL
    """
    config = AnimateDiffConfig(
        duration_seconds=duration_seconds,
        fps=fps
    )
    
    engine = AnimateDiffEngine(default_config=config)
    
    result = await engine.generate_cinematic_video(
        prompt=prompt,
        first_frame_url=image_url,
        identity_vector=identity_vector
    )
    
    return result.video_url


async def generate_stable_cinematic_video(
    first_frame_url: str,
    prompt: str,
    identity_vector: np.ndarray,
    duration: float = 5.0
) -> VideoGenerationResult:
    """
    Generate video with maximum identity stability.
    
    Args:
        first_frame_url: High-fidelity starting frame
        prompt: Motion/scene description
        identity_vector: Identity super-vector
        duration: Video duration in seconds
        
    Returns:
        VideoGenerationResult
    """
    config = AnimateDiffConfig(
        duration_seconds=duration,
        fps=24,
        motion_preset=MotionPreset.CINEMATIC,
        temporal_consistency=0.95,
        identity_adapter_strength=0.98
    )
    
    engine = AnimateDiffEngine(default_config=config)
    
    return await engine.generate_cinematic_video(
        prompt=prompt,
        first_frame_url=first_frame_url,
        identity_vector=identity_vector
    )


if __name__ == "__main__":
    import asyncio
    
    async def test_animatediff():
        print(f"\n{'='*70}")
        print("ANIMATEDIFF ENGINE TEST - DAY 5")
        print(f"{'='*70}\n")
        
        # Test 1: Engine Initialization
        print("Test 1: Engine Initialization")
        print("-" * 70)
        
        config = AnimateDiffConfig(
            duration_seconds=5.0,
            fps=24,
            motion_preset=MotionPreset.CINEMATIC,
            temporal_consistency=0.9,
            identity_adapter_strength=0.95
        )
        
        engine = AnimateDiffEngine(default_config=config)
        print(f"✓ Engine initialized")
        print(f"  Duration: {config.duration_seconds}s")
        print(f"  FPS: {config.fps}")
        print(f"  Frames: {int(config.duration_seconds * config.fps)}")
        print(f"  Motion: {config.motion_preset.value}")
        
        # Test 2: AnimateDiff Initialization
        print("\nTest 2: Initialize AnimateDiff Pipeline")
        print("-" * 70)
        
        first_frame_url = "https://example.com/first_frame.jpg"
        identity_vector = np.random.randn(512).astype(np.float32)
        identity_vector = identity_vector / np.linalg.norm(identity_vector)
        
        init_payload = engine.init_animatediff(first_frame_url, identity_vector)
        
        print("Initialization Payload:")
        for key, value in init_payload.items():
            if key == "identity_vector":
                print(f"  {key}: [{len(value)} elements]")
            else:
                print(f"  {key}: {value}")
        
        # Test 3: Generate Cinematic Video
        print("\nTest 3: Generate Cinematic Video")
        print("-" * 70)
        
        prompt = "A woman gracefully dancing, elegant movements, cinematic lighting"
        negative_prompt = "flickering, unstable, changing face, deformed"
        
        result = await engine.generate_cinematic_video(
            prompt=prompt,
            first_frame_url=first_frame_url,
            negative_prompt=negative_prompt,
            identity_vector=identity_vector
        )
        
        print(f"✓ Video generated: {result.video_url}")
        print(f"  Duration: {result.duration_seconds}s")
        print(f"  FPS: {result.fps}")
        print(f"  Frames: {result.num_frames}")
        print(f"  First frame: {result.first_frame_url}")
        print(f"  Last frame: {result.last_frame_url}")
        
        # Test 4: Temporal Consistency Controller
        print("\nTest 4: Temporal Consistency Controller")
        print("-" * 70)
        
        controller = TemporalConsistencyController(consistency_strength=0.9)
        
        # Create mock frame embeddings
        num_frames = 10
        frame_embeddings = [
            np.random.randn(512).astype(np.float32) for _ in range(num_frames)
        ]
        frame_embeddings = [emb / np.linalg.norm(emb) for emb in frame_embeddings]
        
        # Apply conditioning
        conditioned = controller.apply_temporal_conditioning(
            frame_embeddings,
            identity_vector=identity_vector
        )
        
        print(f"✓ Conditioned {len(conditioned)} frame embeddings")
        
        # Calculate temporal loss
        loss_before = controller.calculate_temporal_loss(frame_embeddings)
        loss_after = controller.calculate_temporal_loss(conditioned)
        
        print(f"  Temporal loss before conditioning: {loss_before:.4f}")
        print(f"  Temporal loss after conditioning: {loss_after:.4f}")
        print(f"  Improvement: {((loss_before - loss_after) / loss_before * 100):.1f}%")
        
        # Test 5: Motion Presets
        print("\nTest 5: Motion Presets")
        print("-" * 70)
        
        for preset in MotionPreset:
            engine.set_motion_preset(preset, scale=1.2)
            print(f"✓ {preset.value}: scale={engine.config.motion_scale}")
        
        # Test 6: Adapter Weight Locking
        print("\nTest 6: Lock Adapter Weights")
        print("-" * 70)
        
        locking_params = engine.lock_adapter_weights(
            identity_vector,
            strength=0.98
        )
        
        print("Locking Parameters:")
        for key, value in locking_params.items():
            if key == "identity_vector":
                print(f"  {key}: [{len(value)} elements]")
            else:
                print(f"  {key}: {value}")
        
        # Test 7: Extract Last Frame
        print("\nTest 7: Extract Last Frame")
        print("-" * 70)
        
        last_frame = await engine.extract_last_frame(result.video_url)
        print(f"✓ Last frame extracted: {last_frame}")
        
        print(f"\n{'='*70}")
        print("✓ All tests completed successfully!")
        print(f"{'='*70}\n")
    
    asyncio.run(test_animatediff())
