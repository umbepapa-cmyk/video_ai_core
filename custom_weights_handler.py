"""
WEEK 1 V2 - DAY 1-2: Custom Weights Handler
============================================
Module for managing custom checkpoints and negative prompting for high-fidelity generation.

This module implements:
- Custom .safetensors checkpoint loading via API
- Aggressive negative prompting system
- Integration with Replicate/Fal.ai custom checkpoint endpoints
- Anatomical integrity preservation through negative prompts
"""

import os
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CheckpointType(Enum):
    """Types of custom checkpoints supported."""
    SAFETENSORS = "safetensors"
    CKPT = "ckpt"
    LORA = "lora"
    EMBEDDINGS = "embeddings"


@dataclass
class CustomCheckpoint:
    """Data structure for custom checkpoint configuration."""
    name: str
    path: str
    checkpoint_type: CheckpointType
    weight_strength: float = 1.0
    metadata: Optional[Dict[str, Any]] = None


class NegativePromptMatrix:
    """
    Comprehensive negative prompting system for anatomical stability.
    
    Prevents common AI generation issues:
    - Body entanglement (fusion of bodies)
    - Anatomical drift (changing features)
    - Structural deformation
    - Flickering and temporal inconsistency
    """
    
    # Core negative prompts for anatomical integrity
    ANATOMICAL_NEGATIVES = [
        "deformed", "mutated", "disfigured", "distorted",
        "extra limbs", "extra fingers", "extra arms", "extra legs",
        "missing limbs", "missing fingers", "missing body parts",
        "fused fingers", "fused limbs", "merged bodies",
        "bad anatomy", "wrong anatomy", "incorrect anatomy",
        "poorly drawn", "bad proportions", "gross proportions",
        "asymmetric", "unnatural pose", "contorted"
    ]
    
    # Visual quality negatives
    QUALITY_NEGATIVES = [
        "blurry", "out of focus", "soft focus", "motion blur",
        "low quality", "low resolution", "jpeg artifacts",
        "noise", "grainy", "pixelated", "compressed",
        "oversaturated", "undersaturated", "overexposed", "underexposed"
    ]
    
    # Temporal consistency negatives (for video)
    TEMPORAL_NEGATIVES = [
        "flickering", "flashing", "unstable", "jittering",
        "morphing", "warping", "shifting features", "changing identity",
        "inconsistent", "discontinuous", "frame jump"
    ]
    
    # Compositional negatives
    COMPOSITIONAL_NEGATIVES = [
        "out of frame", "cropped", "cut off", "incomplete",
        "duplicate", "cloned", "repeated", "multiple copies",
        "watermark", "text", "signature", "logo"
    ]
    
    # Face-specific negatives (for identity preservation)
    FACIAL_NEGATIVES = [
        "wrong face", "different face", "changing face",
        "multiple faces", "merged faces", "deformed face",
        "bad eyes", "cross-eyed", "asymmetric eyes",
        "wrong expression", "unnatural expression"
    ]
    
    @classmethod
    def get_comprehensive_negatives(
        cls,
        include_anatomical: bool = True,
        include_quality: bool = True,
        include_temporal: bool = True,
        include_compositional: bool = True,
        include_facial: bool = True,
        custom_negatives: Optional[List[str]] = None
    ) -> str:
        """
        Generate comprehensive negative prompt string.
        
        Args:
            include_anatomical: Include anatomical integrity negatives
            include_quality: Include visual quality negatives
            include_temporal: Include temporal consistency negatives
            include_compositional: Include compositional negatives
            include_facial: Include facial/identity negatives
            custom_negatives: Additional custom negative prompts
            
        Returns:
            Comma-separated string of negative prompts
        """
        negatives = []
        
        if include_anatomical:
            negatives.extend(cls.ANATOMICAL_NEGATIVES)
        
        if include_quality:
            negatives.extend(cls.QUALITY_NEGATIVES)
        
        if include_temporal:
            negatives.extend(cls.TEMPORAL_NEGATIVES)
        
        if include_compositional:
            negatives.extend(cls.COMPOSITIONAL_NEGATIVES)
        
        if include_facial:
            negatives.extend(cls.FACIAL_NEGATIVES)
        
        if custom_negatives:
            negatives.extend(custom_negatives)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_negatives = []
        for neg in negatives:
            if neg.lower() not in seen:
                seen.add(neg.lower())
                unique_negatives.append(neg)
        
        result = ", ".join(unique_negatives)
        logger.info(f"Generated negative prompt with {len(unique_negatives)} terms")
        
        return result
    
    @classmethod
    def get_video_negatives(cls, custom_negatives: Optional[List[str]] = None) -> str:
        """
        Get negative prompts optimized for video generation.
        
        Focuses on temporal consistency and identity preservation.
        """
        return cls.get_comprehensive_negatives(
            include_anatomical=True,
            include_quality=True,
            include_temporal=True,
            include_compositional=True,
            include_facial=True,
            custom_negatives=custom_negatives
        )
    
    @classmethod
    def get_image_negatives(cls, custom_negatives: Optional[List[str]] = None) -> str:
        """
        Get negative prompts optimized for image generation.
        
        Focuses on anatomical integrity and quality.
        """
        return cls.get_comprehensive_negatives(
            include_anatomical=True,
            include_quality=True,
            include_temporal=False,  # Not needed for single images
            include_compositional=True,
            include_facial=True,
            custom_negatives=custom_negatives
        )


class CustomWeightsHandler:
    """
    Handler for custom model weights and checkpoints.
    
    Manages:
    - Loading custom .safetensors checkpoints
    - LoRA weight injection
    - Custom embeddings
    - Integration with API providers (Replicate, Fal.ai)
    """
    
    def __init__(
        self,
        checkpoint_dir: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        """
        Initialize custom weights handler.
        
        Args:
            checkpoint_dir: Directory containing custom checkpoints
            api_key: API key for custom checkpoint endpoints
        """
        load_dotenv()
        
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else Path("./custom_checkpoints")
        self.checkpoint_dir.mkdir(exist_ok=True)
        
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("REPLICATE_API_TOKEN")
        self.checkpoints: Dict[str, CustomCheckpoint] = {}
        
        logger.info(f"CustomWeightsHandler initialized with checkpoint_dir: {self.checkpoint_dir}")
    
    def register_checkpoint(
        self,
        name: str,
        path: str,
        checkpoint_type: CheckpointType = CheckpointType.SAFETENSORS,
        weight_strength: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> CustomCheckpoint:
        """
        Register a custom checkpoint for use.
        
        Args:
            name: Unique identifier for checkpoint
            path: Path to checkpoint file or URL
            checkpoint_type: Type of checkpoint
            weight_strength: Strength of checkpoint influence (0.0-1.0)
            metadata: Additional metadata
            
        Returns:
            CustomCheckpoint object
        """
        checkpoint = CustomCheckpoint(
            name=name,
            path=path,
            checkpoint_type=checkpoint_type,
            weight_strength=weight_strength,
            metadata=metadata or {}
        )
        
        self.checkpoints[name] = checkpoint
        logger.info(f"Registered checkpoint: {name} ({checkpoint_type.value}) at {path}")
        
        return checkpoint
    
    def load_custom_checkpoint(
        self,
        checkpoint_name: str
    ) -> Optional[CustomCheckpoint]:
        """
        Load a registered custom checkpoint.
        
        Args:
            checkpoint_name: Name of checkpoint to load
            
        Returns:
            CustomCheckpoint object or None if not found
        """
        if checkpoint_name not in self.checkpoints:
            logger.warning(f"Checkpoint not found: {checkpoint_name}")
            return None
        
        checkpoint = self.checkpoints[checkpoint_name]
        logger.info(f"Loading checkpoint: {checkpoint_name}")
        
        # Validate checkpoint exists
        if not checkpoint.path.startswith("http"):
            checkpoint_path = Path(checkpoint.path)
            if not checkpoint_path.exists():
                logger.error(f"Checkpoint file not found: {checkpoint.path}")
                return None
        
        return checkpoint
    
    def apply_negative_prompts(
        self,
        positive_prompt: str,
        mode: str = "video",
        custom_negatives: Optional[List[str]] = None,
        negative_strength: float = 1.5
    ) -> Dict[str, str]:
        """
        Apply comprehensive negative prompting to generation parameters.
        
        Args:
            positive_prompt: Original positive text prompt
            mode: Generation mode ("video" or "image")
            custom_negatives: Additional custom negative prompts
            negative_strength: CFG strength for negative prompts
            
        Returns:
            Dict with 'prompt' and 'negative_prompt' keys
        """
        if mode == "video":
            negative_prompt = NegativePromptMatrix.get_video_negatives(custom_negatives)
        else:
            negative_prompt = NegativePromptMatrix.get_image_negatives(custom_negatives)
        
        logger.info(f"Applied negative prompts (strength={negative_strength})")
        logger.debug(f"Positive: {positive_prompt}")
        logger.debug(f"Negative: {negative_prompt[:200]}...")
        
        return {
            "prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "negative_strength": negative_strength
        }
    
    def prepare_api_payload(
        self,
        prompt: str,
        checkpoint_name: Optional[str] = None,
        mode: str = "video",
        custom_negatives: Optional[List[str]] = None,
        additional_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Prepare complete API payload with custom weights and negative prompts.
        
        Args:
            prompt: Text prompt for generation
            checkpoint_name: Name of custom checkpoint to use
            mode: Generation mode ("video" or "image")
            custom_negatives: Custom negative prompts
            additional_params: Additional API parameters
            
        Returns:
            Complete API payload dictionary
        """
        # Start with base prompting
        prompts = self.apply_negative_prompts(
            prompt,
            mode=mode,
            custom_negatives=custom_negatives
        )
        
        payload = {
            "prompt": prompts["prompt"],
            "negative_prompt": prompts["negative_prompt"],
        }
        
        # Add custom checkpoint if specified
        if checkpoint_name:
            checkpoint = self.load_custom_checkpoint(checkpoint_name)
            if checkpoint:
                payload["custom_checkpoint_url"] = checkpoint.path
                payload["checkpoint_weight"] = checkpoint.weight_strength
                
                logger.info(f"Using custom checkpoint: {checkpoint_name}")
        
        # Merge additional parameters
        if additional_params:
            payload.update(additional_params)
        
        logger.info("API payload prepared with custom weights and negative prompts")
        
        return payload
    
    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """
        List all registered checkpoints.
        
        Returns:
            List of checkpoint information dictionaries
        """
        return [
            {
                "name": cp.name,
                "type": cp.checkpoint_type.value,
                "path": cp.path,
                "weight_strength": cp.weight_strength,
                "metadata": cp.metadata
            }
            for cp in self.checkpoints.values()
        ]


# Convenience functions

def apply_negative_prompts(
    prompt: str,
    mode: str = "video",
    custom_negatives: Optional[List[str]] = None
) -> Dict[str, str]:
    """
    Quick function to apply negative prompts to a prompt.
    
    Args:
        prompt: Positive text prompt
        mode: "video" or "image"
        custom_negatives: Optional custom negatives
        
    Returns:
        Dict with prompt and negative_prompt
    """
    handler = CustomWeightsHandler()
    return handler.apply_negative_prompts(prompt, mode, custom_negatives)


def load_custom_checkpoint(
    checkpoint_path: str,
    checkpoint_name: str = "custom",
    weight_strength: float = 1.0
) -> CustomCheckpoint:
    """
    Quick function to load a custom checkpoint.
    
    Args:
        checkpoint_path: Path to checkpoint file
        checkpoint_name: Name for the checkpoint
        weight_strength: Weight strength (0.0-1.0)
        
    Returns:
        CustomCheckpoint object
    """
    handler = CustomWeightsHandler()
    return handler.register_checkpoint(
        name=checkpoint_name,
        path=checkpoint_path,
        weight_strength=weight_strength
    )


if __name__ == "__main__":
    print(f"\n{'='*70}")
    print("CUSTOM WEIGHTS HANDLER TEST - DAY 1-2")
    print(f"{'='*70}\n")
    
    # Test 1: Negative Prompt Matrix
    print("Test 1: Generate Comprehensive Negative Prompts")
    print("-" * 70)
    
    video_negatives = NegativePromptMatrix.get_video_negatives()
    print(f"Video Negatives ({len(video_negatives.split(','))} terms):")
    print(f"{video_negatives[:200]}...\n")
    
    image_negatives = NegativePromptMatrix.get_image_negatives()
    print(f"Image Negatives ({len(image_negatives.split(','))} terms):")
    print(f"{image_negatives[:200]}...\n")
    
    # Test 2: Custom Checkpoint Registration
    print("Test 2: Register Custom Checkpoints")
    print("-" * 70)
    
    handler = CustomWeightsHandler()
    
    # Register example checkpoints
    checkpoint1 = handler.register_checkpoint(
        name="realism_xl",
        path="https://example.com/realism_xl_v2.safetensors",
        checkpoint_type=CheckpointType.SAFETENSORS,
        weight_strength=0.8,
        metadata={"version": "2.0", "focus": "photorealism"}
    )
    print(f"✓ Registered: {checkpoint1.name}")
    
    checkpoint2 = handler.register_checkpoint(
        name="anatomy_lora",
        path="./custom_checkpoints/anatomy_lora.safetensors",
        checkpoint_type=CheckpointType.LORA,
        weight_strength=0.6,
        metadata={"focus": "anatomical accuracy"}
    )
    print(f"✓ Registered: {checkpoint2.name}")
    
    # Test 3: Apply Negative Prompts
    print("\nTest 3: Apply Negative Prompts to Generation")
    print("-" * 70)
    
    prompt = "A woman dancing gracefully, cinematic lighting, 4K"
    result = handler.apply_negative_prompts(
        prompt,
        mode="video",
        custom_negatives=["cartoonish", "animated"]
    )
    
    print(f"Original Prompt: {result['prompt']}")
    print(f"Negative Prompt: {result['negative_prompt'][:150]}...")
    print(f"Negative Strength: {result['negative_strength']}")
    
    # Test 4: Prepare API Payload
    print("\nTest 4: Prepare Complete API Payload")
    print("-" * 70)
    
    payload = handler.prepare_api_payload(
        prompt="A woman in elegant dress, high quality",
        checkpoint_name="realism_xl",
        mode="video",
        additional_params={
            "duration": "10",
            "aspect_ratio": "16:9",
            "fps": 24
        }
    )
    
    print("API Payload:")
    for key, value in payload.items():
        if key == "negative_prompt":
            print(f"  {key}: {str(value)[:100]}...")
        else:
            print(f"  {key}: {value}")
    
    # Test 5: List Registered Checkpoints
    print("\nTest 5: List All Registered Checkpoints")
    print("-" * 70)
    
    checkpoints = handler.list_checkpoints()
    print(f"Total checkpoints: {len(checkpoints)}")
    
    for cp in checkpoints:
        print(f"\n✓ {cp['name']} ({cp['type']})")
        print(f"  Path: {cp['path']}")
        print(f"  Weight: {cp['weight_strength']}")
        print(f"  Metadata: {cp['metadata']}")
    
    print(f"\n{'='*70}")
    print("✓ All tests completed successfully!")
    print(f"{'='*70}\n")
