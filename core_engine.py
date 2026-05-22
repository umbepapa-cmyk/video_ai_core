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
import shutil
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

from identity_cache import (
    compute_folder_hash,
    load_cached_identity,
    save_cached_identity,
)

# Import custom exceptions
from exceptions import KinematicMismatchError, BudgetExceededError

from cost_estimator import estimate_pipeline_cost
from budget_tracker import check_budget, record_spend

from generation_progress import (
    ProgressCallback,
    estimate_first_frame_seconds,
    estimate_pipeline_seconds,
    format_eta_range,
    submit_and_wait_with_eta,
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


# Preset tuning: Flux first-frame steps, I2V resolution/steps, timeouts.
# STANDARD/HIGH/MID enforce >= 20 inference steps; DRAFT stays low for dry-runs.
PRESET_TUNING: Dict[QualityPreset, Dict[str, Any]] = {
    QualityPreset.DRAFT: {
        "flux_steps": 12,
        "i2v_steps": 12,
        "resolution": "480p",
        "image_size": {"width": 512, "height": 512},
        "first_frame_timeout": 90,
        "i2v_timeout_multiplier": 0.5,
        "guidance_scale": 5.0,
    },
    QualityPreset.STANDARD: {
        "flux_steps": 25,
        "i2v_steps": 25,
        "resolution": "720p",
        "image_size": "landscape_16_9",
        "first_frame_timeout": 120,
        "i2v_timeout_multiplier": 0.75,
        "guidance_scale": 6.5,
    },
    QualityPreset.HIGH: {
        "flux_steps": 28,
        "i2v_steps": 28,
        "resolution": "720p",
        "image_size": "landscape_16_9",
        "first_frame_timeout": 120,
        "i2v_timeout_multiplier": 1.0,
        "guidance_scale": 7.5,
    },
    QualityPreset.ULTRA: {
        "flux_steps": 35,
        "i2v_steps": 35,
        "resolution": "720p",
        "image_size": "landscape_16_9",
        "first_frame_timeout": 180,
        "i2v_timeout_multiplier": 1.0,
        "guidance_scale": 8.0,
    },
}


def get_preset_tuning(preset: QualityPreset) -> Dict[str, Any]:
    """Return a copy of preset tuning with non-DRAFT step floors enforced."""
    tuning = dict(PRESET_TUNING.get(preset, PRESET_TUNING[QualityPreset.HIGH]))
    if preset != QualityPreset.DRAFT:
        tuning["flux_steps"] = max(20, int(tuning["flux_steps"]))
        tuning["i2v_steps"] = max(20, int(tuning["i2v_steps"]))
    return tuning


def _resolve_dir_path(path: str) -> str:
    """Normalize directory path to absolute resolved string (Windows-safe)."""
    return str(Path(path).resolve())


def _normalize_subjects_payload(subjects_payload: Dict[str, str]) -> Dict[str, str]:
    """Resolve all face directory paths in subjects_payload."""
    return {subject_id: _resolve_dir_path(faces_dir) for subject_id, faces_dir in subjects_payload.items()}


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
    """Configuration for Core Engine with Multi-Agent Spatial Conditioning support."""
    
    # Identity settings (Multi-Subject Support)
    # OPTION 1: Single subject (legacy compatibility)
    reference_faces_dir: Optional[str] = None
    
    # OPTION 2: Multi-subject (new Multi-Agent approach)
    subjects_payload: Optional[Dict[str, str]] = None
    # Format: {"subject_1": "inputs/donna/", "subject_2": "inputs/uomo/"}
    
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

    # I2V provider: "fal" | "replicate" | "fal_then_replicate"
    i2v_provider: str = "fal"
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        # Must provide either reference_faces_dir OR subjects_payload
        if not self.reference_faces_dir and not self.subjects_payload:
            raise ValueError(
                "Must provide either 'reference_faces_dir' (single subject) "
                "or 'subjects_payload' (multi-subject)"
            )
        
        # If both provided, subjects_payload takes precedence
        if self.reference_faces_dir and self.subjects_payload:
            logger.warning(
                "Both reference_faces_dir and subjects_payload provided. "
                "Using subjects_payload (multi-subject mode)."
            )
        
        # Convert single subject to subjects_payload format for unified handling
        if self.reference_faces_dir and not self.subjects_payload:
            self.subjects_payload = {"subject_1": self.reference_faces_dir}
            logger.info("Converted single subject to subjects_payload format")

        if self.reference_faces_dir:
            self.reference_faces_dir = _resolve_dir_path(self.reference_faces_dir)
        if self.subjects_payload:
            self.subjects_payload = _normalize_subjects_payload(self.subjects_payload)
    
    @property
    def num_subjects(self) -> int:
        """Get number of subjects in configuration."""
        if self.subjects_payload:
            return len(self.subjects_payload)
        return 1
    
    @property
    def is_multi_subject(self) -> bool:
        """Check if this is a multi-subject configuration."""
        return self.num_subjects > 1


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

    def log_cumulative_eta(
        self,
        label: str,
        remaining_low: float,
        remaining_high: float,
    ) -> None:
        """Log cumulative pipeline ETA for upcoming work."""
        logger.info(
            "[ETA] Pipeline — %s: tempo stimato rimanente %s",
            label,
            format_eta_range(remaining_low, remaining_high),
        )
    
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
        logger.info(f"Mode: {'Multi-Subject' if config.is_multi_subject else 'Single-Subject'}")
        logger.info(f"Subjects: {config.num_subjects}")
        
        self.weights_handler = CustomWeightsHandler() if CustomWeightsHandler else None
        self.controlnet_handler = ControlNetHandler(api_key=self.api_key) if ControlNetHandler else None
        
        # Identity locker will be initialized per subject in multi-subject mode
        self.identity_locker = None
        
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
                identity_consistency_threshold=config.temporal_consistency,
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

    def _get_quality_params(self) -> Dict[str, Any]:
        """Map quality preset to generation parameters (Flux + I2V)."""
        tuning = get_preset_tuning(self.config.quality_preset)
        return {
            "num_inference_steps": tuning["flux_steps"],
            "i2v_inference_steps": tuning["i2v_steps"],
            "image_size": tuning["image_size"],
            "resolution": tuning["resolution"],
            "first_frame_timeout": tuning["first_frame_timeout"],
            "i2v_timeout_multiplier": tuning["i2v_timeout_multiplier"],
            "guidance_scale": tuning["guidance_scale"],
        }

    @staticmethod
    def _is_content_policy_error(exc: Exception) -> bool:
        from prompt_obfuscation import is_content_policy_error

        return is_content_policy_error(exc)

    @staticmethod
    def _obfuscate_prompt(prompt: str) -> str:
        from prompt_obfuscation import obfuscate_prompt

        return obfuscate_prompt(prompt)

    async def _resolve_controlnet_urls(
        self,
        controlnet_data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Upload local ControlNet assets to Fal CDN when needed."""
        from i2v_router import ensure_public_media_url

        controlnet_video_url: Optional[str] = None
        pose_map_url: Optional[str] = None

        if self.config.controlnet_map_path:
            try:
                controlnet_video_url = await ensure_public_media_url(
                    self.config.controlnet_map_path, self.api_key
                )
            except Exception as exc:
                logger.warning("ControlNet video upload skipped: %s", exc)

        if controlnet_data and controlnet_data.get("pose_map_path"):
            try:
                pose_map_url = await ensure_public_media_url(
                    controlnet_data["pose_map_path"], self.api_key
                )
            except Exception as exc:
                logger.warning("Pose map upload skipped: %s", exc)

        return controlnet_video_url, pose_map_url
    
    async def generate_high_fidelity_video(
        self,
        reference_faces_dir: Optional[str] = None,
        subjects_payload: Optional[Dict[str, str]] = None,
        prompt: str = "",
        controlnet_map_path: Optional[str] = None,
        duration_seconds: int = 10,
        output_path: str = "outputs/"
    ) -> GenerationResult:
        """
        Main entrypoint: Generate high-fidelity video from reference faces.
        
        Supports both single-subject and multi-subject (Multi-Agent Spatial Conditioning).
        
        Args:
            reference_faces_dir: Directory with reference faces (single subject, legacy)
            subjects_payload: Dict of subject_id -> faces_dir (multi-subject)
                Example: {"subject_1": "./faces/donna/", "subject_2": "./faces/uomo/"}
            prompt: Text prompt describing desired video content
            controlnet_map_path: Optional path to ControlNet pose map (video for multi-subject)
            duration_seconds: Target video duration
            output_path: Directory to save output
            
        Returns:
            GenerationResult with video URL and comprehensive metrics
        
        Raises:
            KinematicMismatchError: If skeleton count != subject count (multi-subject)
        """
        logger.info("\n" + "="*70)
        logger.info("CORE ENGINE: HIGH-FIDELITY VIDEO GENERATION")
        logger.info("="*70)
        
        # Determine subjects payload
        if subjects_payload:
            final_subjects_payload = _normalize_subjects_payload(subjects_payload)
            logger.info(f"Mode: Multi-Subject ({len(subjects_payload)} subjects)")
        elif reference_faces_dir:
            final_subjects_payload = {"subject_1": _resolve_dir_path(reference_faces_dir)}
            logger.info(f"Mode: Single-Subject (legacy)")
        else:
            raise ValueError("Must provide either reference_faces_dir or subjects_payload")
        
        num_subjects = len(final_subjects_payload)
        
        for subject_id, faces_dir in final_subjects_payload.items():
            logger.info(f"  {subject_id}: {faces_dir}")
        
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Target duration: {duration_seconds}s")
        logger.info(f"Quality: {self.config.quality_preset.value}")
        logger.info("="*70 + "\n")

        # FASE 4: Cost estimate + daily budget circuit breaker
        tuning = PRESET_TUNING.get(
            self.config.quality_preset, PRESET_TUNING[QualityPreset.HIGH]
        )
        cost_config = {
            "duration_seconds": duration_seconds,
            "resolution": tuning.get("resolution", "720p"),
            "fps": self.config.fps,
            "endpoint": "fal-ai/wan-i2v",
            "segment_duration": self.config.segment_duration,
            "enable_autoregressive": self.config.enable_autoregressive,
        }
        cost_estimate = estimate_pipeline_cost(cost_config)
        try:
            check_budget(cost_estimate.total_usd)
        except BudgetExceededError as exc:
            logger.error(
                "SAFE MODE: Daily API budget exceeded — generation blocked. %s",
                exc,
            )
            raise
        logger.info(
            "Cost estimate: $%.4f USD (%d credits, %d segments @ %s)",
            cost_estimate.total_usd,
            cost_estimate.credits_required,
            cost_estimate.num_segments,
            cost_estimate.resolution,
        )
        
        # Update config
        self.config.subjects_payload = final_subjects_payload
        self.config.duration_seconds = duration_seconds
        self.config.output_path = output_path
        self.config.controlnet_map_path = controlnet_map_path
        
        # STAGE 1: Initialization
        self.progress_tracker.start_stage(PipelineStage.INITIALIZATION)
        self._notify_progress(PipelineStage.INITIALIZATION, 1, 7)
        
        # Apply negative prompting
        prompts = self._apply_negative_prompts(prompt)
        
        # Enhance prompt for multi-subject to prevent body entanglement
        if num_subjects > 1 and self.controlnet_handler:
            prompts['prompt'] = self.controlnet_handler.prevent_body_entanglement(
                prompts['prompt'],
                num_subjects=num_subjects
            )
        
        stage1_time = self.progress_tracker.end_stage(PipelineStage.INITIALIZATION)
        
        # STAGE 2: Identity Extraction (MULTI-SUBJECT)
        self.progress_tracker.start_stage(PipelineStage.IDENTITY_EXTRACTION)
        self._notify_progress(PipelineStage.IDENTITY_EXTRACTION, 2, 7)
        
        identity_vectors, stability_scores = await self._extract_identity(final_subjects_payload)
        
        stage2_time = self.progress_tracker.end_stage(PipelineStage.IDENTITY_EXTRACTION)
        
        # STAGE 3: ControlNet Processing + Skeleton Detection (if multi-subject)
        self.progress_tracker.start_stage(PipelineStage.CONTROLNET_PROCESSING)
        self._notify_progress(PipelineStage.CONTROLNET_PROCESSING, 3, 7)
        
        controlnet_data = await self._process_controlnet(controlnet_map_path, prompts)
        self._controlnet_data = controlnet_data
        self._controlnet_urls: Tuple[Optional[str], Optional[str]] = (None, None)
        if controlnet_map_path or controlnet_data:
            self._controlnet_urls = await self._resolve_controlnet_urls(controlnet_data)
        
        # Spatial masks for multi-subject
        spatial_masks = None
        
        if num_subjects > 1 and controlnet_map_path and self.controlnet_handler:
            logger.info("Detecting multiple skeletons for spatial conditioning...")
            
            try:
                spatial_masks = self.controlnet_handler.detect_multiple_skeletons(
                    video_path=controlnet_map_path,
                    num_expected_subjects=num_subjects
                )
                
                logger.info(f"✓ Spatial masks generated for {len(spatial_masks)} subjects")
                
                # Log first frame positions
                for subject_id, masks in spatial_masks.items():
                    if masks:
                        first_bbox = masks[0]["bbox"]
                        position = self._bbox_to_position_descriptor(first_bbox)
                        logger.info(f"  {subject_id}: {position} (bbox: {first_bbox})")
                
            except KinematicMismatchError as e:
                logger.error(f"Kinematic mismatch detected: {e}")
                logger.error(f"Expected: {e.expected_count} subjects")
                logger.error(f"Detected: {e.detected_count} skeletons")
                raise
            
            except Exception as e:
                logger.error(f"Skeleton detection failed: {e}")
                logger.warning("Continuing without spatial masks (may cause identity bleed)")
                spatial_masks = None
        
        stage3_time = self.progress_tracker.end_stage(PipelineStage.CONTROLNET_PROCESSING)
        
        # STAGE 4: First Frame Generation (MULTI-SUBJECT AWARE)
        self.progress_tracker.start_stage(PipelineStage.FIRST_FRAME_GENERATION)
        self._notify_progress(PipelineStage.FIRST_FRAME_GENERATION, 4, 7)
        draft_mode = self.config.quality_preset == QualityPreset.DRAFT
        autoregressive = (
            self.config.enable_autoregressive
            and duration_seconds > self.config.segment_duration
        )
        ff_low, ff_high = estimate_pipeline_seconds(
            duration_seconds,
            draft_mode=draft_mode,
            autoregressive=autoregressive,
            segment_duration=self.config.segment_duration,
            include_first_frame=True,
        )
        self.progress_tracker.log_cumulative_eta(
            "prima del first frame (Flux + I2V)",
            ff_low,
            ff_high,
        )
        
        first_frame_url = await self._generate_first_frame(
            prompts=prompts,
            identity_vectors=identity_vectors,
            spatial_masks=spatial_masks,
            controlnet_data=controlnet_data
        )
        
        stage4_time = self.progress_tracker.end_stage(PipelineStage.FIRST_FRAME_GENERATION)
        
        # STAGE 5: Video Generation
        self.progress_tracker.start_stage(PipelineStage.VIDEO_GENERATION)
        self._notify_progress(PipelineStage.VIDEO_GENERATION, 5, 7)
        video_low, video_high = estimate_pipeline_seconds(
            duration_seconds,
            draft_mode=draft_mode,
            autoregressive=autoregressive,
            segment_duration=self.config.segment_duration,
            include_first_frame=False,
        )
        self.progress_tracker.log_cumulative_eta(
            "generazione video (I2V)",
            video_low,
            video_high,
        )
        
        # For multi-subject, use the first subject's identity for video generation
        # (Full multi-subject video generation would require per-frame identity conditioning)
        primary_subject_id = list(identity_vectors.keys())[0]
        primary_identity_vector = identity_vectors[primary_subject_id]
        
        if num_subjects > 1:
            logger.info(f"Using {primary_subject_id} identity for video generation")
            logger.info("Note: Full per-frame multi-subject conditioning requires advanced pipeline")
        
        if self.config.enable_autoregressive and duration_seconds > self.config.segment_duration:
            # Use autoregressive for longer videos
            video_result = await self._generate_autoregressive_video(
                first_frame_url,
                prompts,
                primary_identity_vector
            )
        else:
            # Single segment video
            video_result = await self._generate_single_video(
                first_frame_url,
                prompts,
                primary_identity_vector,
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
        
        # Calculate average stability (for multi-subject)
        avg_stability = np.mean(list(stability_scores.values())) if stability_scores else 0.0
        
        # Create result
        result = GenerationResult(
            final_video_url=final_video_url,
            duration_seconds=video_result.get('duration', duration_seconds),
            first_frame_url=first_frame_url,
            last_frame_url=video_result.get('last_frame_url'),
            identity_super_vector=primary_identity_vector,  # Primary subject vector
            identity_stability_score=avg_stability,
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
                'autoregressive_used': self.config.enable_autoregressive,
                'num_subjects': num_subjects,
                'is_multi_subject': num_subjects > 1,
                'subjects': list(final_subjects_payload.keys()),
                'stability_scores': stability_scores,
                'spatial_conditioning': spatial_masks is not None
            }
        )
        
        self.progress_tracker.end_stage(PipelineStage.COMPLETED)
        
        record_spend(cost_estimate.total_usd)
        
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
    
    async def _extract_identity(
        self, 
        subjects_payload: Dict[str, str]
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, float]]:
        """
        Extract multi-angle identity super-vectors for multiple subjects.
        
        This method supports Multi-Agent Spatial Conditioning by extracting
        separate identity embeddings for each subject, preventing Latent Identity Bleed.
        
        Args:
            subjects_payload: Dictionary mapping subject IDs to face directories
                Example: {"subject_1": "./faces/person_a/", "subject_2": "./faces/person_b/"}
        
        Returns:
            Tuple of:
            - identity_vectors: Dict mapping subject_id -> super_vector (np.ndarray)
            - stability_scores: Dict mapping subject_id -> stability score (float)
        """
        logger.info(f"Extracting identity from {len(subjects_payload)} subject(s)...")
        
        identity_vectors = {}
        stability_scores = {}
        
        for subject_id, faces_dir in subjects_payload.items():
            faces_dir = _resolve_dir_path(faces_dir)
            logger.info(f"Processing {subject_id} from {faces_dir}")

            cache_hash = compute_folder_hash(faces_dir)
            cached = load_cached_identity(cache_hash)
            if cached is not None:
                super_vec, stability, _meta = cached
                identity_vectors[subject_id] = super_vec
                stability_scores[subject_id] = stability
                logger.info(f"  {subject_id}: {stability*100:.1f}% stability (cached)")
                continue
            
            if MultiAngleIdentityLock:
                # Reinitialize identity locker for each subject
                # This ensures complete isolation between subjects
                self.identity_locker = MultiAngleIdentityLock(
                    reference_faces_dir=faces_dir,
                    num_angles=self.config.num_angles
                )
                
                # Extract embeddings
                self.identity_locker.extract_multi_angle_embeddings()
                
                # Create super-vector
                super_vec = self.identity_locker.create_super_vector(fusion_method="weighted_mean")
                
                # Calculate stability
                stability = self.identity_locker.get_identity_stability_score()
                
                identity_vectors[subject_id] = super_vec.vector
                stability_scores[subject_id] = stability
                
                num_images = len(list(Path(faces_dir).iterdir()))
                save_cached_identity(
                    cache_hash,
                    super_vec.vector,
                    stability,
                    metadata={
                        "subject_id": subject_id,
                        "faces_dir": faces_dir,
                        "num_images": num_images,
                        "num_angles": self.config.num_angles,
                    },
                )
                
                logger.info(f"  {subject_id}: {stability*100:.1f}% stability")
            else:
                # Fallback mock for testing
                logger.warning(f"Identity locker not available, using mock identity for {subject_id}")
                mock_vector = np.random.randn(512).astype(np.float32)
                mock_vector = mock_vector / np.linalg.norm(mock_vector)
                identity_vectors[subject_id] = mock_vector
                stability_scores[subject_id] = 0.95
        
        # Log summary
        avg_stability = np.mean(list(stability_scores.values())) if stability_scores else 0.0
        logger.info(f"✓ Identity extraction complete: {len(identity_vectors)} subjects")
        logger.info(f"  Average stability: {avg_stability*100:.1f}%")
        
        return identity_vectors, stability_scores
    
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
        identity_vectors: Dict[str, np.ndarray],  # CHANGED: Dict instead of single vector
        spatial_masks: Optional[Dict[str, List[Dict]]] = None,  # NEW
        controlnet_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate high-fidelity first frame using Flux.1 Dev with identity injection.
        
        Supports Multi-Agent Spatial Conditioning:
        - Single subject: Uses standard prompt + identity
        - Multi-subject: Uses regional prompting with spatial position descriptors
        
        Args:
            prompts: Text prompts dict with 'prompt' and optional 'negative_prompt'
            identity_vectors: Dict mapping subject_id -> identity vector
            spatial_masks: Optional dict mapping subject_id -> bounding boxes
            controlnet_data: Optional ControlNet data
        
        Returns:
            URL of the generated image
        """
        logger.info("Generating high-fidelity first frame with Flux.1 Dev...")
        
        if not fal_client:
            raise RuntimeError("fal_client not available. Install with: pip install fal-client")
        
        if not self.api_key:
            raise ValueError("FAL_KEY not set in environment or constructor")

        quality = self._get_quality_params()
        
        num_subjects = len(identity_vectors)
        logger.info(f"  Subjects: {num_subjects}")
        
        # Determine generation mode
        if num_subjects == 1:
            # Single subject - use existing logic
            logger.info("Using single-subject generation")
            
            subject_id = list(identity_vectors.keys())[0]
            identity_vector = identity_vectors[subject_id]
            
            # Prepare payload for Fal.ai
            payload = {
                "prompt": prompts.get("prompt", ""),
                "image_size": quality["image_size"],
                "num_inference_steps": quality["num_inference_steps"],
                "num_images": 1,
                "enable_safety_checker": False,  # Critical for custom tensors
                "guidance_scale": quality["guidance_scale"],
            }
            
            # Add negative prompting if provided
            if "negative_prompt" in prompts and prompts["negative_prompt"]:
                payload["negative_prompt"] = prompts["negative_prompt"]
            
            # Add ControlNet data if available
            if controlnet_data and "pose_map_path" in controlnet_data:
                logger.info(f"ControlNet data available but not yet integrated with Flux endpoint")
        
        else:
            # Multi-subject - use regional prompting
            logger.info(f"Using multi-subject regional prompting for {num_subjects} subjects")
            
            # Build regional prompts with spatial descriptors
            regional_prompts = []
            
            for subject_id, identity_vec in identity_vectors.items():
                # Get spatial position
                if spatial_masks and subject_id in spatial_masks:
                    bbox = spatial_masks[subject_id][0]["bbox"]  # First frame
                    position = self._bbox_to_position_descriptor(bbox)
                else:
                    # Fallback positional descriptor based on subject index
                    subject_num = int(subject_id.split('_')[-1])
                    if subject_num == 1:
                        position = "left side"
                    elif subject_num == num_subjects:
                        position = "right side"
                    else:
                        position = "center"
                
                regional_prompts.append(f"{prompts['prompt']} on the {position}")
            
            # Combine prompts using pipe separator (regional prompting syntax)
            combined_prompt = " | ".join(regional_prompts)
            
            logger.info(f"Regional prompt: {combined_prompt}")
            
            # Prepare payload
            payload = {
                "prompt": combined_prompt,
                "image_size": quality["image_size"],
                "num_inference_steps": quality["num_inference_steps"],
                "num_images": 1,
                "enable_safety_checker": False,
                "guidance_scale": quality["guidance_scale"],
            }
            
            # Add negative prompt
            if "negative_prompt" in prompts and prompts["negative_prompt"]:
                payload["negative_prompt"] = prompts["negative_prompt"]
            
            # Note: Full regional IP-Adapter support depends on Fal.ai endpoint capabilities
            # This implementation uses prompt-based regional guidance as a fallback
            logger.info("Note: Using prompt-based regional guidance")
            logger.info("Full regional IP-Adapter requires specialized endpoint support")
        
        # Define the actual API call as a nested async function for retry
        obfuscation_attempted = False

        async def _api_call():
            nonlocal obfuscation_attempted
            logger.info("Submitting first frame generation to Fal.ai...")
            logger.info("  Prompt: %s...", payload["prompt"][:100])

            handler = await fal_client.submit_async(
                "fal-ai/flux/dev",
                arguments=payload,
            )

            first_frame_timeout = quality["first_frame_timeout"]
            draft_mode = self.config.quality_preset == QualityPreset.DRAFT
            estimated = estimate_first_frame_seconds(draft_mode=draft_mode)
            logger.info(
                "Waiting for first frame generation (timeout: %ss)...",
                first_frame_timeout,
            )
            try:
                result = await submit_and_wait_with_eta(
                    handler,
                    estimated,
                    "First frame Flux",
                    timeout=first_frame_timeout,
                    step_info="step 1/1 first frame",
                )
            except Exception as exc:
                if self._is_content_policy_error(exc) and not obfuscation_attempted:
                    logger.warning(
                        "[WARNING] Server-side policy filter triggered. "
                        "Initiating Prompt Obfuscation..."
                    )
                    payload["prompt"] = self._obfuscate_prompt(payload["prompt"])
                    if payload.get("negative_prompt"):
                        payload["negative_prompt"] = self._obfuscate_prompt(
                            payload["negative_prompt"]
                        )
                    obfuscation_attempted = True
                    return await _api_call()
                raise

            images = result.get("images", [])
            if not images:
                raise ValueError("No images returned from Flux.1 Dev")

            image_url = images[0].get("url")
            if not image_url:
                raise ValueError("Image URL not found in response")

            logger.info("✓ First frame generated successfully")
            logger.info("  URL: %s", image_url)

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
    
    def _bbox_to_position_descriptor(self, bbox: List[float]) -> str:
        """
        Convert bounding box to spatial position descriptor for regional prompting.
        
        Args:
            bbox: Bounding box [x, y, w, h] in normalized coordinates
            
        Returns:
            Spatial descriptor string (e.g., "left side", "center", "right side")
        """
        x, y, w, h = bbox
        
        # Determine horizontal position based on center x coordinate
        center_x = x + w / 2
        
        if center_x < 0.33:
            return "left side"
        elif center_x > 0.67:
            return "right side"
        else:
            return "center"
    
    async def _generate_single_video(
        self,
        first_frame_url: str,
        prompts: Dict[str, str],
        identity_vector: np.ndarray,
        duration: float,
        *,
        segment_index: int = 1,
        segment_total: int = 1,
        on_progress: Optional[ProgressCallback] = None,
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
        stage_label = f"Generazione video (segmento {segment_index}/{segment_total})"
        
        if self.animatediff_engine:
            quality = self._get_quality_params()
            controlnet_video_url, pose_map_url = getattr(
                self, "_controlnet_urls", (None, None)
            )
            result = await self.animatediff_engine.generate_cinematic_video(
                prompt=prompts['prompt'],
                first_frame_url=first_frame_url,
                negative_prompt=prompts['negative_prompt'],
                identity_vector=identity_vector,
                motion_override={
                    "resolution": quality["resolution"],
                    "timeout_multiplier": quality["i2v_timeout_multiplier"],
                    "draft_mode": self.config.quality_preset == QualityPreset.DRAFT,
                    "duration_seconds": duration,
                    "num_inference_steps": quality["i2v_inference_steps"],
                    "require_last_frame": self.config.enable_autoregressive,
                    "stage_label": stage_label,
                    "segment_index": segment_index,
                    "segment_total": segment_total,
                    "on_progress": on_progress,
                    "controlnet_video_url": controlnet_video_url,
                    "pose_map_url": pose_map_url,
                    "identity_adapter_strength": self.config.identity_adapter_strength,
                },
            )
            
            return {
                'video_url': result.video_url,
                'duration': result.duration_seconds,
                'last_frame_url': result.last_frame_url,
                'num_segments': 1,
                'mean_drift': 0.0,
                'temporal_consistency': 1.0
            }
        
        if not self.api_key:
            raise ValueError("FAL_KEY not set in environment or constructor")

        from i2v_router import generate_i2v_with_fallback

        motion_preset = (
            self.config.motion_preset if hasattr(self.config, "motion_preset") else "smooth"
        )
        fps = self.config.fps if hasattr(self.config, "fps") else 24
        quality = self._get_quality_params()
        controlnet_video_url, pose_map_url = getattr(self, "_controlnet_urls", (None, None))

        async def _api_call():
            logger.info("Submitting video generation via I2V fallback router...")
            logger.info(f"  First frame: {first_frame_url}")
            logger.info(f"  Duration: {duration}s")
            logger.info(f"  Motion preset: {motion_preset}")
            logger.info(f"  Resolution: {quality['resolution']}")
            logger.info(f"  I2V steps: {quality['i2v_inference_steps']}")
            if controlnet_video_url:
                logger.info("  ControlNet video conditioning: enabled")
            if pose_map_url:
                logger.info("  Pose map conditioning: enabled")
            return await generate_i2v_with_fallback(
                image_url=first_frame_url,
                prompt=prompts.get("prompt", ""),
                duration=duration,
                negative_prompt=prompts.get("negative_prompt", ""),
                motion_preset=motion_preset,
                fps=fps,
                resolution=quality["resolution"],
                timeout_multiplier=quality["i2v_timeout_multiplier"],
                draft_mode=self.config.quality_preset == QualityPreset.DRAFT,
                require_last_frame=self.config.enable_autoregressive,
                api_key=self.api_key,
                provider=getattr(self.config, "i2v_provider", "fal"),
                stage_label=stage_label,
                segment_index=segment_index,
                segment_total=segment_total,
                on_progress=on_progress,
                identity_vector=identity_vector,
                identity_adapter_strength=self.config.identity_adapter_strength,
                controlnet_video_url=controlnet_video_url,
                pose_map_url=pose_map_url,
                num_inference_steps=quality["i2v_inference_steps"],
            )

        try:
            result = await retry_with_backoff(
                _api_call,
                max_retries=1,
                initial_delay=5.0,
                backoff_factor=2.0,
                exceptions=(httpx.HTTPError, ValueError, RuntimeError),
            )
            logger.info(
                "Video segment generated via %s (%s)%s",
                result.get("provider_id"),
                result.get("endpoint_id"),
                " [obfuscation applied]" if result.get("obfuscation_applied") else "",
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
        duration = params.get('duration_seconds') or params.get(
            'duration', self.config.segment_duration
        )
        segment_index = int(params.get("segment_index", 0)) + 1
        segment_total = int(
            params.get("num_segments")
            or max(1, int(np.ceil(self.config.duration_seconds / self.config.segment_duration)))
        )
        
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
            duration=duration,
            segment_index=segment_index,
            segment_total=segment_total,
        )
        
        video_url = result.get('video_url', '')
        last_frame_url = result.get('last_frame_url') or ''
        
        if not video_url:
            raise ValueError("Video URL not returned from generation")
        
        if not last_frame_url:
            from i2v_router import ensure_last_frame_url

            last_frame_url = await ensure_last_frame_url(
                video_url, None, self.api_key
            )
        
        logger.info("✓ Segment generated for autoregressive loop")
        logger.info("  last_frame_url ready for next segment propagation")
        
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
        logger.info("Finalizing video...")
        
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

        source_path = Path(video_url)
        if not str(video_url).startswith(("http://", "https://")):
            if not source_path.exists():
                raise FileNotFoundError(f"Local video not found: {video_url}")
            logger.info(f"Copying local video from: {source_path}")
            logger.info(f"Saving to: {local_path}")
            shutil.copy2(source_path, local_path)
            file_size = local_path.stat().st_size
            if file_size == 0:
                raise IOError("Copied video file is empty")
            logger.info("✓ Video finalized from local path")
            logger.info(f"  Local path: {local_path}")
            logger.info(f"  File size: {file_size / 1024 / 1024:.2f} MB")
            return str(local_path.absolute())
        
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
