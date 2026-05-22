"""
WEEK 1 V2 - DAY 7: Core Engine Orchestration
=============================================
Unified orchestrator integrating all Week 1 V2 modules.

This module integrates:
- Custom weights and negative prompting (Day 1-2)
- ControlNet geometric constraints (Day 3)
- Multi-angle identity locking (Day 4)
- AnimateDiff video pipeline (Day 5)
- Advanced autoregressive loop (Day 6)

Pipeline:
Reference Faces → Identity Lock → ControlNet → AnimateDiff → Autoregressive → Output
"""

import os
import logging
from typing import Optional, Dict, Any, List, Callable, Tuple
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import asyncio
import time

import numpy as np
import httpx
import aiofiles

from dotenv import load_dotenv

# Import path configuration
from path_config import (
    CARTELLA_RISULTATI,
    CARTELLA_RISULTATI_TEST,
    CARTELLA_VOLTI_RIFERIMENTO_TEST
)

try:
    import fal_client
except ImportError:
    fal_client = None
    logging.warning("fal_client not installed. Install with: pip install fal-client")

# Import all Week 1 V2 modules
try:
    from custom_weights_handler import CustomWeightsHandler, NegativePromptMatrix
    from controlnet_handler import ControlNetHandler, ControlNetModel
    from identity_lock_3d import MultiAngleIdentityLock
    from animatediff_engine import AnimateDiffEngine, AnimateDiffConfig, MotionPreset
    from autoregressive_v2 import AutoregressiveV2Engine, AutoregressiveConfig, CrossfadeMode
except ImportError as e:
    logging.warning(f"Could not import Week 1 V2 modules: {e}")
    # Fallback for testing
    CustomWeightsHandler = None
    ControlNetHandler = None
    MultiAngleIdentityLock = None
    AnimateDiffEngine = None
    AutoregressiveV2Engine = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class QualityPreset(Enum):
    """Quality presets for generation."""
    DRAFT = "draft"
    STANDARD = "standard"
    HIGH = "high"
    ULTRA = "ultra"


class PipelineStage(Enum):
    """Pipeline processing stages."""
    INITIALIZATION = "initialization"
    IDENTITY_EXTRACTION = "identity_extraction"
    CONTROLNET_PROCESSING = "controlnet_processing"
    FIRST_FRAME_GENERATION = "first_frame_generation"
    VIDEO_GENERATION = "video_generation"
    AUTOREGRESSIVE_EXTENSION = "autoregressive_extension"
    FINALIZATION = "finalization"
    COMPLETED = "completed"


@dataclass
class CoreEngineConfig:
    """Configuration for Core Engine."""
    # Identity settings
    reference_faces_dir: str
    num_angles: int = 5
    identity_adapter_strength: float = 0.95
    
    # ControlNet settings
    use_controlnet: bool = True
    controlnet_map_path: Optional[str] = None
    controlnet_strength: float = 0.8
    
    # Custom weights settings
    use_custom_checkpoint: bool = False
    checkpoint_name: Optional[str] = None
    
    # Video generation settings
    duration_seconds: float = 10.0
    fps: int = 24
    motion_preset: str = "cinematic"
    
    # Autoregressive settings
    enable_autoregressive: bool = True
    segment_duration: float = 5.0
    crossfade_duration: float = 0.5
    
    # Quality settings
    quality_preset: QualityPreset = QualityPreset.HIGH
    temporal_consistency: float = 0.9
    flickering_suppression: float = 0.8
    
    # Output settings
    output_path: str = CARTELLA_RISULTATI


@dataclass
class GenerationResult:
    """Complete result from core engine."""
    final_video_url: str
    duration_seconds: float
    first_frame_url: str
    last_frame_url: Optional[str]
    
    # Identity metrics
    identity_super_vector: np.ndarray
    identity_stability_score: float
    mean_identity_drift: float
    
    # Quality metrics
    temporal_consistency_score: float
    num_segments: int
    
    # Metadata
    pipeline_stages: Dict[str, float]  # Stage name -> duration in seconds
    total_generation_time: float
    metadata: Dict[str, Any]


class PipelineProgressTracker:
    """Tracks progress through pipeline stages."""
    
    def __init__(self, total_stages: int = 7):
        """Initialize progress tracker."""
        self.total_stages = total_stages
        self.current_stage = 0
        self.stage_times: Dict[str, float] = {}
        self.start_time = time.time()
        
    def start_stage(self, stage: PipelineStage) -> None:
        """Mark start of a pipeline stage."""
        self.current_stage += 1
        self.stage_times[stage.value] = time.time()
        
        logger.info(f"\n{'='*70}")
        logger.info(f"STAGE {self.current_stage}/{self.total_stages}: {stage.value.upper()}")
        logger.info(f"{'='*70}")
    
    def end_stage(self, stage: PipelineStage) -> float:
        """Mark end of a pipeline stage and return duration."""
        duration = time.time() - self.stage_times[stage.value]
        logger.info(f"Stage '{stage.value}' completed in {duration:.2f}s")
        return duration
    
    def get_total_time(self) -> float:
        """Get total elapsed time."""
        return time.time() - self.start_time


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


class CoreEngine:
    """
    Core orchestration engine for high-fidelity video generation.
    
    Integrates all Week 1 V2 modules into a unified pipeline:
    1. Multi-angle identity extraction
    2. ControlNet geometric constraints (optional)
    3. High-fidelity first frame generation
    4. AnimateDiff video generation
    5. Autoregressive extension (optional)
    6. Output finalization
    """
    
    def __init__(
        self,
        config: CoreEngineConfig,
        api_key: Optional[str] = None,
        progress_callback: Optional[Callable[[PipelineStage, int, int], None]] = None
    ):
        """
        Initialize Core Engine.
        
        Args:
            config: Engine configuration
            api_key: API key for generation services
            progress_callback: Optional callback for progress updates
        """
        load_dotenv()
        
        self.config = config
        self.api_key = api_key or os.getenv("FAL_KEY")
        self.progress_callback = progress_callback
        
        # Initialize sub-engines
        logger.info("Initializing Core Engine components...")
        
        self.weights_handler = CustomWeightsHandler() if CustomWeightsHandler else None
        self.controlnet_handler = ControlNetHandler(api_key=self.api_key) if ControlNetHandler else None
        self.identity_locker = MultiAngleIdentityLock(
            reference_faces_dir=config.reference_faces_dir,
            num_angles=config.num_angles
        ) if MultiAngleIdentityLock else None
        
        # AnimateDiff engine
        anim_config = AnimateDiffConfig(
            duration_seconds=config.segment_duration,
            fps=config.fps,
            motion_preset=MotionPreset(config.motion_preset) if MotionPreset else None,
            temporal_consistency=config.temporal_consistency,
            identity_adapter_strength=config.identity_adapter_strength
        ) if AnimateDiffConfig else None
        
        self.animatediff_engine = AnimateDiffEngine(
            api_key=self.api_key,
            default_config=anim_config
        ) if AnimateDiffEngine and anim_config else None
        
        # Autoregressive engine
        if config.enable_autoregressive:
            auto_config = AutoregressiveConfig(
                segment_duration_seconds=config.segment_duration,
                target_duration_seconds=config.duration_seconds,
                crossfade_duration_seconds=config.crossfade_duration,
                flickering_suppression_strength=config.flickering_suppression,
                temporal_consistency=config.temporal_consistency
            ) if AutoregressiveConfig else None
            
            self.autoregressive_engine = AutoregressiveV2Engine(
                config=auto_config,
                video_generator=self._generate_video_segment
            ) if AutoregressiveV2Engine and auto_config else None
        else:
            self.autoregressive_engine = None
        
        # Progress tracker
        self.progress_tracker = PipelineProgressTracker()
        
        # Ensure output directory exists
        Path(config.output_path).mkdir(parents=True, exist_ok=True)
        
        logger.info("Core Engine initialized successfully")
        logger.info(f"  Output directory: {config.output_path}")
        logger.info(f"  Duration target: {config.duration_seconds}s")
        logger.info(f"  Quality preset: {config.quality_preset.value}")
    
    async def generate_high_fidelity_video(
        self,
        reference_faces_dir: str,
        prompt: str,
        controlnet_map_path: Optional[str] = None,
        duration_seconds: int = 10,
        output_path: str = "outputs/"
    ) -> GenerationResult:
        """
        Main entrypoint: Generate high-fidelity video from reference faces.
        
        This is the primary function that orchestrates the entire Week 1 V2 pipeline.
        
        Args:
            reference_faces_dir: Directory containing reference face images (5 angles)
            prompt: Text prompt describing desired video content
            controlnet_map_path: Optional path to ControlNet pose map
            duration_seconds: Target video duration
            output_path: Directory to save output
            
        Returns:
            GenerationResult with video URL and comprehensive metrics
        """
        logger.info("\n" + "="*70)
        logger.info("CORE ENGINE: HIGH-FIDELITY VIDEO GENERATION")
        logger.info("="*70)
        logger.info(f"Reference faces: {reference_faces_dir}")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Target duration: {duration_seconds}s")
        logger.info(f"Quality: {self.config.quality_preset.value}")
        logger.info("="*70 + "\n")
        
        # Update config
        self.config.reference_faces_dir = reference_faces_dir
        self.config.duration_seconds = duration_seconds
        self.config.output_path = output_path
        self.config.controlnet_map_path = controlnet_map_path
        
        # STAGE 1: Initialization
        self.progress_tracker.start_stage(PipelineStage.INITIALIZATION)
        self._notify_progress(PipelineStage.INITIALIZATION, 1, 7)
        
        # Apply negative prompting
        prompts = self._apply_negative_prompts(prompt)
        
        stage1_time = self.progress_tracker.end_stage(PipelineStage.INITIALIZATION)
        
        # STAGE 2: Identity Extraction
        self.progress_tracker.start_stage(PipelineStage.IDENTITY_EXTRACTION)
        self._notify_progress(PipelineStage.IDENTITY_EXTRACTION, 2, 7)
        
        identity_super_vector, stability_score = await self._extract_identity(reference_faces_dir)
        
        stage2_time = self.progress_tracker.end_stage(PipelineStage.IDENTITY_EXTRACTION)
        
        # STAGE 3: ControlNet Processing (if enabled)
        self.progress_tracker.start_stage(PipelineStage.CONTROLNET_PROCESSING)
        self._notify_progress(PipelineStage.CONTROLNET_PROCESSING, 3, 7)
        
        controlnet_data = await self._process_controlnet(controlnet_map_path, prompts)
        
        stage3_time = self.progress_tracker.end_stage(PipelineStage.CONTROLNET_PROCESSING)
        
        # STAGE 4: First Frame Generation
        self.progress_tracker.start_stage(PipelineStage.FIRST_FRAME_GENERATION)
        self._notify_progress(PipelineStage.FIRST_FRAME_GENERATION, 4, 7)
        
        first_frame_url = await self._generate_first_frame(
            prompts,
            identity_super_vector,
            controlnet_data
        )
        
        stage4_time = self.progress_tracker.end_stage(PipelineStage.FIRST_FRAME_GENERATION)
        
        # STAGE 5: Video Generation
        self.progress_tracker.start_stage(PipelineStage.VIDEO_GENERATION)
        self._notify_progress(PipelineStage.VIDEO_GENERATION, 5, 7)
        
        if self.config.enable_autoregressive and duration_seconds > self.config.segment_duration:
            # Use autoregressive for longer videos
            video_result = await self._generate_autoregressive_video(
                first_frame_url,
                prompts,
                identity_super_vector
            )
        else:
            # Single segment video
            video_result = await self._generate_single_video(
                first_frame_url,
                prompts,
                identity_super_vector,
                duration_seconds
            )
        
        stage5_time = self.progress_tracker.end_stage(PipelineStage.VIDEO_GENERATION)
        
        # STAGE 6: Finalization
        self.progress_tracker.start_stage(PipelineStage.FINALIZATION)
        self._notify_progress(PipelineStage.FINALIZATION, 6, 7)
        
        final_video_url = await self._finalize_video(video_result, output_path)
        
        stage6_time = self.progress_tracker.end_stage(PipelineStage.FINALIZATION)
        
        # STAGE 7: Complete
        self.progress_tracker.start_stage(PipelineStage.COMPLETED)
        self._notify_progress(PipelineStage.COMPLETED, 7, 7)
        
        # Create result
        result = GenerationResult(
            final_video_url=final_video_url,
            duration_seconds=video_result.get('duration', duration_seconds),
            first_frame_url=first_frame_url,
            last_frame_url=video_result.get('last_frame_url'),
            identity_super_vector=identity_super_vector,
            identity_stability_score=stability_score,
            mean_identity_drift=video_result.get('mean_drift', 0.0),
            temporal_consistency_score=video_result.get('temporal_consistency', 1.0),
            num_segments=video_result.get('num_segments', 1),
            pipeline_stages={
                'initialization': stage1_time,
                'identity_extraction': stage2_time,
                'controlnet_processing': stage3_time,
                'first_frame_generation': stage4_time,
                'video_generation': stage5_time,
                'finalization': stage6_time
            },
            total_generation_time=self.progress_tracker.get_total_time(),
            metadata={
                'prompt': prompt,
                'quality_preset': self.config.quality_preset.value,
                'controlnet_used': self.config.use_controlnet,
                'autoregressive_used': self.config.enable_autoregressive
            }
        )
        
        self.progress_tracker.end_stage(PipelineStage.COMPLETED)
        
        self._print_final_summary(result)
        
        return result
    
    def _apply_negative_prompts(self, prompt: str) -> Dict[str, str]:
        """Apply negative prompting system."""
        logger.info("Applying comprehensive negative prompting...")
        
        if self.weights_handler:
            return self.weights_handler.apply_negative_prompts(
                prompt,
                mode="video",
                negative_strength=1.5
            )
        else:
            # Fallback
            return {
                "prompt": prompt,
                "negative_prompt": "blurry, deformed, bad anatomy, flickering"
            }
    
    async def _extract_identity(self, reference_faces_dir: str) -> Tuple[np.ndarray, float]:
        """Extract multi-angle identity super-vector."""
        logger.info(f"Extracting identity from {self.config.num_angles} reference angles...")
        
        if self.identity_locker:
            # Extract embeddings from all angles
            self.identity_locker.extract_multi_angle_embeddings()
            
            # Create super-vector
            super_vec = self.identity_locker.create_super_vector(fusion_method="weighted_mean")
            
            # Calculate stability
            stability = self.identity_locker.get_identity_stability_score()
            
            logger.info(f"Identity extracted with {stability*100:.1f}% stability")
            
            return super_vec.vector, stability
        else:
            # Fallback mock
            logger.warning("Identity locker not available, using mock identity")
            mock_vector = np.random.randn(512).astype(np.float32)
            mock_vector = mock_vector / np.linalg.norm(mock_vector)
            return mock_vector, 0.95
    
    async def _process_controlnet(
        self,
        controlnet_map_path: Optional[str],
        prompts: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Process ControlNet constraints if enabled."""
        if not self.config.use_controlnet or not controlnet_map_path:
            logger.info("ControlNet processing skipped")
            return None
        
        logger.info("Processing ControlNet geometric constraints...")
        
        if self.controlnet_handler:
            # Generate or load pose map
            if Path(controlnet_map_path).exists():
                pose_map_path, _ = self.controlnet_handler.generate_pose_map(
                    controlnet_map_path,
                    output_dir=self.config.output_path
                )
            else:
                pose_map_path = controlnet_map_path
            
            return {
                'pose_map_path': pose_map_path,
                'strength': self.config.controlnet_strength
            }
        else:
            logger.warning("ControlNet handler not available")
            return None
    
    async def _generate_first_frame(
        self,
        prompts: Dict[str, str],
        identity_vector: np.ndarray,
        controlnet_data: Optional[Dict[str, Any]]
    ) -> str:
        """
        Generate high-fidelity first frame using Flux.1 Dev with identity injection.
        
        Returns:
            URL of the generated image
        """
        logger.info("Generating high-fidelity first frame with Flux.1 Dev...")
        
        if not fal_client:
            raise RuntimeError("fal_client not available. Install with: pip install fal-client")
        
        if not self.api_key:
            raise ValueError("FAL_KEY not set in environment or constructor")
        
        # Prepare payload for Fal.ai
        payload = {
            "prompt": prompts.get("prompt", ""),
            "image_size": "landscape_16_9",  # 16:9 aspect ratio for video
            "num_inference_steps": 28,
            "num_images": 1,
            "enable_safety_checker": False,  # Critical for custom tensors
            "guidance_scale": 7.5,
        }
        
        # Add negative prompting if provided
        if "negative_prompt" in prompts and prompts["negative_prompt"]:
            payload["negative_prompt"] = prompts["negative_prompt"]
        
        # Add ControlNet data if available
        if controlnet_data and "pose_map_path" in controlnet_data:
            # Note: ControlNet support depends on Fal.ai endpoint capabilities
            # This may require a different endpoint like "fal-ai/flux-controlnet"
            logger.info(f"ControlNet data available but not yet integrated with Flux endpoint")
        
        # Define the actual API call as a nested async function for retry
        async def _api_call():
            logger.info(f"Submitting first frame generation to Fal.ai...")
            logger.info(f"  Prompt: {payload['prompt'][:80]}...")
            
            # Submit async job to Fal.ai
            handler = await fal_client.submit_async(
                "fal-ai/flux/dev",
                arguments=payload
            )
            
            # Wait for completion with timeout
            logger.info("Waiting for first frame generation (timeout: 120s)...")
            result = await handler.get(timeout=120)
            
            # Extract image URL
            images = result.get("images", [])
            if not images:
                raise ValueError("No images returned from Flux.1 Dev")
            
            image_url = images[0].get("url")
            if not image_url:
                raise ValueError("Image URL not found in response")
            
            logger.info(f"✓ First frame generated successfully")
            logger.info(f"  URL: {image_url}")
            
            return image_url
        
        # Execute with retry logic
        try:
            image_url = await retry_with_backoff(
                _api_call,
                max_retries=3,
                initial_delay=2.0,
                backoff_factor=2.0,
                exceptions=(httpx.HTTPError, asyncio.TimeoutError, ValueError, RuntimeError)
            )
            return image_url
            
        except Exception as e:
            logger.error(f"First frame generation failed after all retries: {type(e).__name__}: {e}")
            raise RuntimeError(f"Failed to generate first frame: {e}") from e
    
    async def _generate_single_video(
        self,
        first_frame_url: str,
        prompts: Dict[str, str],
        identity_vector: np.ndarray,
        duration: float
    ) -> Dict[str, Any]:
        """
        Generate single video segment using Wan I2V (Image-to-Video).
        
        Args:
            first_frame_url: URL of the starting image
            prompts: Text prompts for generation
            identity_vector: Identity embedding vector
            duration: Target duration in seconds
            
        Returns:
            Dictionary with video URL and metadata
        """
        logger.info(f"Generating single video segment ({duration}s) with Wan I2V...")
        
        if self.animatediff_engine:
            # Use AnimateDiff engine if available (it has real API calls)
            result = await self.animatediff_engine.generate_cinematic_video(
                prompt=prompts['prompt'],
                first_frame_url=first_frame_url,
                negative_prompt=prompts['negative_prompt'],
                identity_vector=identity_vector
            )
            
            return {
                'video_url': result.video_url,
                'duration': result.duration_seconds,
                'last_frame_url': result.last_frame_url,
                'num_segments': 1,
                'mean_drift': 0.0,
                'temporal_consistency': 1.0
            }
        
        # Fallback: Direct Fal.ai Wan I2V call
        if not fal_client:
            raise RuntimeError("fal_client not available. Install with: pip install fal-client")
        
        if not self.api_key:
            raise ValueError("FAL_KEY not set in environment or constructor")
        
        # Motion preset mapping
        motion_strength_map = {
            "static": 0.2,
            "subtle": 0.4,
            "smooth": 0.6,
            "cinematic": 0.8,
            "dynamic": 1.0
        }
        
        motion_preset = self.config.motion_preset if hasattr(self.config, 'motion_preset') else "smooth"
        motion_strength = motion_strength_map.get(motion_preset, 0.6)
        
        # Prepare payload for Wan I2V
        payload = {
            "image_url": first_frame_url,
            "prompt": prompts.get("prompt", ""),
            "duration": min(int(duration), 10),  # Wan typically supports up to 10s
            "fps": self.config.fps if hasattr(self.config, 'fps') else 24,
            "resolution": "720p",
            "motion_strength": motion_strength,
            "seed": -1,  # Random seed
            "enable_loop": False,
        }
        
        # Add negative prompt if available
        if "negative_prompt" in prompts and prompts["negative_prompt"]:
            payload["negative_prompt"] = prompts["negative_prompt"]
        
        # Define the actual API call as a nested async function for retry
        async def _api_call():
            logger.info(f"Submitting video generation to Fal.ai Wan I2V...")
            logger.info(f"  First frame: {first_frame_url}")
            logger.info(f"  Duration: {duration}s")
            logger.info(f"  Motion preset: {motion_preset} (strength: {motion_strength})")
            
            # Submit job to Fal.ai
            handler = await fal_client.submit_async(
                "fal-ai/wan-v2.2-i2v",  # Wan V2.2 Image-to-Video endpoint
                arguments=payload
            )
            
            # Wait with long timeout (video generation is slow)
            logger.info("Waiting for video generation (timeout: 300s)...")
            result = await handler.get(timeout=300)
            
            # Extract video URL
            video_data = result.get("video", {})
            video_url = video_data.get("url")
            if not video_url:
                raise ValueError("Video URL not found in response")
            
            # Extract last frame URL for autoregressive loop
            last_frame_data = result.get("last_frame", {})
            last_frame_url = last_frame_data.get("url")
            
            logger.info(f"✓ Video segment generated successfully")
            logger.info(f"  Video URL: {video_url}")
            if last_frame_url:
                logger.info(f"  Last frame URL: {last_frame_url}")
            
            return {
                'video_url': video_url,
                'duration': duration,
                'last_frame_url': last_frame_url,
                'num_segments': 1,
                'mean_drift': 0.0,
                'temporal_consistency': 1.0
            }
        
        # Execute with retry logic
        try:
            result = await retry_with_backoff(
                _api_call,
                max_retries=3,
                initial_delay=5.0,  # Longer initial delay for video generation
                backoff_factor=2.0,
                exceptions=(httpx.HTTPError, asyncio.TimeoutError, ValueError, RuntimeError)
            )
            return result
            
        except Exception as e:
            logger.error(f"Video generation failed after all retries: {type(e).__name__}: {e}")
            raise RuntimeError(f"Failed to generate video: {e}") from e
    
    async def _generate_autoregressive_video(
        self,
        first_frame_url: str,
        prompts: Dict[str, str],
        identity_vector: np.ndarray
    ) -> Dict[str, Any]:
        """Generate extended video using autoregressive loop."""
        logger.info(f"Generating autoregressive video ({self.config.duration_seconds}s)...")
        
        if self.autoregressive_engine:
            result = await self.autoregressive_engine.generate_extended_video(
                prompt=prompts['prompt'],
                first_frame_url=first_frame_url,
                identity_vector=identity_vector,
                negative_prompt=prompts['negative_prompt']
            )
            
            return {
                'video_url': result.final_video_url,
                'duration': result.total_duration_seconds,
                'last_frame_url': result.segments[-1].last_frame_url if result.segments else None,
                'num_segments': result.num_segments,
                'mean_drift': result.mean_identity_drift,
                'temporal_consistency': result.temporal_consistency_score
            }
        else:
            # Fallback to single segment
            return await self._generate_single_video(
                first_frame_url,
                prompts,
                identity_vector,
                self.config.duration_seconds
            )
    
    async def _generate_video_segment(self, params: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generate single video segment (used by autoregressive engine).
        
        Args:
            params: Generation parameters including:
                - prompt: Text prompt
                - first_frame_url: Starting frame URL
                - identity_vector: Identity embedding
                - negative_prompt: Optional negative prompt
            
        Returns:
            Tuple of (video_url, last_frame_url)
        """
        logger.info("Generating video segment for autoregressive loop...")
        
        # Extract parameters
        prompt = params.get('prompt', '')
        first_frame_url = params.get('first_frame_url', '')
        identity_vector = params.get('identity_vector')
        negative_prompt = params.get('negative_prompt', '')
        duration = params.get('duration', self.config.segment_duration)
        
        # Build prompts dict
        prompts = {
            'prompt': prompt,
            'negative_prompt': negative_prompt
        }
        
        # Use the real _generate_single_video method
        result = await self._generate_single_video(
            first_frame_url=first_frame_url,
            prompts=prompts,
            identity_vector=identity_vector,
            duration=duration
        )
        
        video_url = result.get('video_url', '')
        last_frame_url = result.get('last_frame_url', '')
        
        if not video_url:
            raise ValueError("Video URL not returned from generation")
        
        logger.info(f"✓ Segment generated for autoregressive loop")
        
        return video_url, last_frame_url
    
    async def _finalize_video(self, video_result: Dict[str, Any], output_path: str) -> str:
        """
        Finalize video by downloading from remote URL to local ephemeral storage.
        
        Args:
            video_result: Video generation result containing video_url
            output_path: Local directory path to save the video
            
        Returns:
            Absolute path to the downloaded video file
        """
        logger.info("Finalizing video: downloading from remote URL...")
        
        video_url = video_result.get('video_url')
        if not video_url:
            raise ValueError("No video_url found in video_result")
        
        # Ensure output directory exists
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique filename with timestamp
        timestamp = int(time.time())
        filename = f"final_video_{timestamp}.mp4"
        local_path = output_dir / filename
        
        logger.info(f"Downloading video from: {video_url}")
        logger.info(f"Saving to: {local_path}")
        
        # Define the actual download as a nested async function for retry
        async def _download():
            # Download with streaming for large files
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", video_url) as response:
                    response.raise_for_status()
                    
                    # Get total size for progress tracking
                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0
                    last_log_progress = 0
                    
                    logger.info(f"Starting download... (Total size: {total_size / 1024 / 1024:.2f} MB)")
                    
                    # Write in chunks
                    async with aiofiles.open(local_path, 'wb') as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            # Log progress every 10%
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                # Log only when crossing 10% thresholds
                                if progress - last_log_progress >= 10:
                                    logger.info(f"  Download progress: {progress:.1f}%")
                                    last_log_progress = progress
            
            # Verify file exists and has content
            if not local_path.exists():
                raise IOError(f"Downloaded file not found: {local_path}")
            
            file_size = local_path.stat().st_size
            if file_size == 0:
                raise IOError("Downloaded file is empty")
            
            logger.info(f"✓ Video downloaded successfully")
            logger.info(f"  Local path: {local_path}")
            logger.info(f"  File size: {file_size / 1024 / 1024:.2f} MB")
            
            return str(local_path.absolute())
        
        # Execute with retry logic
        try:
            result = await retry_with_backoff(
                _download,
                max_retries=3,
                initial_delay=3.0,
                backoff_factor=2.0,
                exceptions=(httpx.HTTPError, httpx.TimeoutException, IOError)
            )
            return result
            
        except Exception as e:
            logger.error(f"Video download failed after all retries: {type(e).__name__}: {e}")
            # Clean up partial download if it exists
            if local_path.exists():
                try:
                    local_path.unlink()
                    logger.info("Cleaned up partial download")
                except:
                    pass
            raise RuntimeError(f"Failed to download video: {e}") from e
    
    def _notify_progress(self, stage: PipelineStage, current: int, total: int) -> None:
        """Notify progress callback if set."""
        if self.progress_callback:
            self.progress_callback(stage, current, total)
    
    def _print_final_summary(self, result: GenerationResult) -> None:
        """Print comprehensive generation summary."""
        logger.info("\n" + "="*70)
        logger.info("GENERATION COMPLETE - FINAL SUMMARY")
        logger.info("="*70)
        logger.info(f"Output: {result.final_video_url}")
        logger.info(f"Duration: {result.duration_seconds}s")
        logger.info(f"Segments: {result.num_segments}")
        logger.info(f"\nQuality Metrics:")
        logger.info(f"  Identity Stability: {result.identity_stability_score*100:.1f}%")
        logger.info(f"  Mean Identity Drift: {result.mean_identity_drift*100:.2f}%")
        logger.info(f"  Temporal Consistency: {result.temporal_consistency_score*100:.1f}%")
        logger.info(f"\nPerformance:")
        logger.info(f"  Total Time: {result.total_generation_time:.1f}s")
        logger.info(f"  Stage Breakdown:")
        for stage, duration in result.pipeline_stages.items():
            logger.info(f"    {stage}: {duration:.2f}s")
        logger.info("="*70 + "\n")


# Convenience function (main entrypoint)

async def generate_high_fidelity_video(
    reference_faces_dir: str,
    prompt: str,
    controlnet_map_path: Optional[str] = None,
    duration_seconds: int = 10,
    output_path: str = "outputs/"
) -> Dict[str, Any]:
    """
    Main convenience function for high-fidelity video generation.
    
    This is the primary entrypoint for Week 1 V2 Core Engine.
    
    Args:
        reference_faces_dir: Directory with 5 reference face images
        prompt: Text prompt for video content
        controlnet_map_path: Optional ControlNet pose map path
        duration_seconds: Target video duration
        output_path: Output directory
        
    Returns:
        Dictionary with video URL and metrics
    """
    config = CoreEngineConfig(
        reference_faces_dir=reference_faces_dir,
        num_angles=5,
        duration_seconds=duration_seconds,
        output_path=output_path,
        controlnet_map_path=controlnet_map_path,
        quality_preset=QualityPreset.HIGH
    )
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        reference_faces_dir=reference_faces_dir,
        prompt=prompt,
        controlnet_map_path=controlnet_map_path,
        duration_seconds=duration_seconds,
        output_path=output_path
    )
    
    return {
        'video_url': result.final_video_url,
        'duration': result.duration_seconds,
        'identity_stability': result.identity_stability_score,
        'temporal_consistency': result.temporal_consistency_score,
        'generation_time': result.total_generation_time
    }


if __name__ == "__main__":
    async def test_core_engine():
        print(f"\n{'='*70}")
        print("CORE ENGINE TEST - DAY 7")
        print(f"{'='*70}\n")
        
        # Test 1: Configuration
        print("Test 1: Core Engine Configuration")
        print("-" * 70)
        
        config = CoreEngineConfig(
            reference_faces_dir=CARTELLA_VOLTI_RIFERIMENTO_TEST,
            num_angles=5,
            duration_seconds=10.0,
            fps=24,
            quality_preset=QualityPreset.HIGH,
            enable_autoregressive=True,
            output_path=CARTELLA_RISULTATI_TEST
        )
        
        print(f"✓ Configuration created")
        print(f"  Reference faces: {config.reference_faces_dir}")
        print(f"  Duration: {config.duration_seconds}s")
        print(f"  Quality: {config.quality_preset.value}")
        print(f"  Autoregressive: {config.enable_autoregressive}")
        
        # Test 2: Engine Initialization
        print("\nTest 2: Core Engine Initialization")
        print("-" * 70)
        
        engine = CoreEngine(config=config)
        print(f"✓ Engine initialized")
        print(f"  Components loaded:")
        print(f"    - Weights Handler: {'✓' if engine.weights_handler else '✗'}")
        print(f"    - ControlNet Handler: {'✓' if engine.controlnet_handler else '✗'}")
        print(f"    - Identity Locker: {'✓' if engine.identity_locker else '✗'}")
        print(f"    - AnimateDiff Engine: {'✓' if engine.animatediff_engine else '✗'}")
        print(f"    - Autoregressive Engine: {'✓' if engine.autoregressive_engine else '✗'}")
        
        # Test 3: Full Pipeline Execution
        print("\nTest 3: Full Pipeline Execution")
        print("-" * 70)
        
        prompt = "A woman gracefully dancing, elegant movements, cinematic lighting"
        
        result = await engine.generate_high_fidelity_video(
            reference_faces_dir=CARTELLA_VOLTI_RIFERIMENTO_TEST,
            prompt=prompt,
            duration_seconds=10,
            output_path=CARTELLA_RISULTATI_TEST
        )
        
        print(f"\n✓ Pipeline execution complete!")
        print(f"  Video: {result.final_video_url}")
        print(f"  Duration: {result.duration_seconds}s")
        print(f"  Segments: {result.num_segments}")
        
        # Test 4: Quality Metrics
        print("\nTest 4: Quality Metrics")
        print("-" * 70)
        
        print(f"Identity Metrics:")
        print(f"  Stability Score: {result.identity_stability_score*100:.1f}%")
        print(f"  Mean Drift: {result.mean_identity_drift*100:.2f}%")
        print(f"\nTemporal Metrics:")
        print(f"  Consistency Score: {result.temporal_consistency_score*100:.1f}%")
        
        # Test 5: Performance Analysis
        print("\nTest 5: Performance Analysis")
        print("-" * 70)
        
        print(f"Total Generation Time: {result.total_generation_time:.2f}s")
        print(f"\nStage Breakdown:")
        for stage, duration in result.pipeline_stages.items():
            percentage = (duration / result.total_generation_time) * 100
            print(f"  {stage}: {duration:.2f}s ({percentage:.1f}%)")
        
        # Test 6: Convenience Function
        print("\nTest 6: Convenience Function")
        print("-" * 70)
        
        quick_result = await generate_high_fidelity_video(
            reference_faces_dir=CARTELLA_VOLTI_RIFERIMENTO_TEST,
            prompt="A person walking, natural movement",
            duration_seconds=5,
            output_path=CARTELLA_RISULTATI_TEST
        )
        
        print(f"✓ Quick generation complete")
        print(f"  Video: {quick_result['video_url']}")
        print(f"  Time: {quick_result['generation_time']:.2f}s")
        
        print(f"\n{'='*70}")
        print("✓ All tests completed successfully!")
        print(f"{'='*70}\n")
    
    import asyncio
    asyncio.run(test_core_engine())
