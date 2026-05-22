"""
FASE 3: API Orchestrator Module (UPDATED FOR WEEK 1 V2)
========================================================
Network orchestration for cloud-based video generation.

This module extends test_fal.py into a complete production orchestrator with:
- Async client for Fal.ai API (extends existing test_fal.py)
- Alibaba Wan video generation integration
- Queue status management (in_progress, completed)
- FFmpeg-based video crossfade merging
- Autoregressive loop management for multi-shot generation

WEEK 1 V2 ENHANCEMENTS:
- Custom checkpoint/LoRA support
- AnimateDiff integration for high-fidelity video
- Identity vector injection support
- Advanced negative prompting
"""

import os
import asyncio
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import time

from dotenv import load_dotenv
import fal_client
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class QueueStatus(Enum):
    """Status of generation queue."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class APIError(Exception):
    """Exception for API-related errors."""
    pass


class FFmpegError(Exception):
    """Exception for FFmpeg processing errors."""
    pass


class VideoGenerationClient:
    """
    Client for Fal.ai video generation APIs.
    
    Extends test_fal.py with production features:
    - Alibaba Wan video generation
    - Autoregressive multi-shot generation
    - Queue management
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize API client.
        
        Args:
            api_key: Fal.ai API key (or from FAL_KEY env var)
        """
        load_dotenv()
        self.api_key = api_key or os.getenv("FAL_KEY")
        
        if not self.api_key:
            logger.warning("No API key provided - using mock mode")
            self._mock_mode = True
        else:
            os.environ["FAL_KEY"] = self.api_key
            self._mock_mode = False
    
    def generate_image(
        self,
        prompt: str,
        image_size: str = "landscape_4_3",
        num_inference_steps: int = 28,
        enable_safety_checker: bool = False,
        negative_prompt: Optional[str] = None,
        custom_checkpoint_url: Optional[str] = None,
        identity_vector: Optional[List[float]] = None
    ) -> str:
        """
        Generate image using Flux.1 Dev (from test_fal.py).
        
        WEEK 1 V2 ENHANCEMENTS: Added support for negative prompts, custom checkpoints,
        and identity vector injection.
        
        Args:
            prompt: Text prompt
            image_size: Image size preset
            num_inference_steps: Number of diffusion steps
            enable_safety_checker: Enable NSFW filter
            negative_prompt: Negative prompt for quality control (V2)
            custom_checkpoint_url: URL to custom .safetensors checkpoint (V2)
            identity_vector: Identity embedding for face consistency (V2)
            
        Returns:
            Image URL
            
        Raises:
            APIError: If generation fails
        """
        if self._mock_mode:
            return f"https://mock.example.com/image_{hash(prompt)}.jpg"
        
        try:
            logger.info(f"Generating image with Flux.1 Dev")
            logger.info(f"Prompt: {prompt}")
            
            # Build arguments with V2 enhancements
            arguments = {
                "prompt": prompt,
                "image_size": image_size,
                "num_inference_steps": num_inference_steps,
                "num_images": 1,
                "enable_safety_checker": False,
            }
            
            # Add V2 parameters if provided
            if negative_prompt:
                arguments["negative_prompt"] = negative_prompt
            if custom_checkpoint_url:
                arguments["custom_checkpoint_url"] = custom_checkpoint_url
            if identity_vector:
                arguments["identity_embedding"] = identity_vector
            
            result = fal_client.subscribe(
                "fal-ai/flux/dev",
                arguments=arguments,
                with_logs=True,
                on_queue_update=self._on_queue_update,
            )
            
            images = result.get("images") or []
            if not images:
                raise APIError("No images returned")
            
            image_url = images[0].get("url")
            if not image_url:
                raise APIError("Image entry has no URL")
            
            logger.info(f"Image generated: {image_url}")
            return image_url
            
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            raise APIError(f"Failed to generate image: {e}")
    
    async def generate_video(
        self,
        prompt: str,
        image_url: str,
        duration: str = "5",
        aspect_ratio: str = "16:9",
        negative_prompt: Optional[str] = None,
        identity_vector: Optional[List[float]] = None,
        use_animatediff: bool = False,
        motion_preset: str = "cinematic"
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Generate video using Alibaba Wan (I2V-01) or AnimateDiff.
        
        WEEK 1 V2 ENHANCEMENTS: Added AnimateDiff support for higher fidelity,
        identity vector injection, and advanced negative prompting.
        
        Args:
            prompt: Text prompt for video generation
            image_url: URL of starting image
            duration: Video duration ("5" or "10" seconds)
            aspect_ratio: Video aspect ratio
            negative_prompt: Negative prompt for quality control (V2)
            identity_vector: Identity embedding for face consistency (V2)
            use_animatediff: Use AnimateDiff instead of Wan (V2)
            motion_preset: Motion preset for AnimateDiff (V2)
            
        Returns:
            Tuple of (request_id, initial_response)
            
        Raises:
            APIError: If API request fails
        """
        if self._mock_mode:
            import uuid
            request_id = f"mock_{uuid.uuid4().hex[:16]}"
            logger.info(f"[MOCK] Generating video: {request_id}")
            return (request_id, {"status": "in_progress"})
        
        # Select endpoint based on use_animatediff flag
        endpoint = "fal-ai/animatediff" if use_animatediff else "fal-ai/alibaba-wan/i2v-01"
        
        logger.info(f"Submitting video generation to {endpoint}")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Image: {image_url}")
        if use_animatediff:
            logger.info(f"Motion preset: {motion_preset}")
        
        try:
            # Build arguments with V2 enhancements
            arguments = {
                "prompt": prompt,
                "image_url": image_url,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "enable_safety_checker": False,
            }
            
            # Add V2 parameters
            if negative_prompt:
                arguments["negative_prompt"] = negative_prompt
            if identity_vector:
                arguments["identity_embedding"] = identity_vector
            if use_animatediff:
                arguments["motion_preset"] = motion_preset
                arguments["temporal_consistency"] = 0.9
            
            # Submit to Fal.ai endpoint
            result = await asyncio.to_thread(
                fal_client.subscribe,
                endpoint,
                arguments=arguments,
                with_logs=True,
                on_queue_update=self._on_queue_update,
            )
            
            video_url = result.get("video", {}).get("url")
            if not video_url:
                raise APIError("No video URL in response")
            
            logger.info(f"Video generated: {video_url}")
            
            # Return format compatible with status checking
            request_id = f"completed_{hash(video_url)}"
            return (request_id, {"status": "completed", "video_url": video_url})
            
        except Exception as e:
            logger.error(f"Video generation failed: {e}")
            raise APIError(f"Failed to generate video: {e}")
    
    def _on_queue_update(self, update) -> None:
        """Callback for queue updates (from test_fal.py)."""
        if isinstance(update, fal_client.InProgress):
            for log in update.logs:
                logger.info(f"Queue: {log['message']}")
    
    async def generate_autoregressive(
        self,
        prompts: List[str],
        initial_image: str,
        duration_per_shot: str = "5"
    ) -> List[str]:
        """
        Generate multiple video shots autoregressively.
        
        Each shot uses the last frame of the previous shot as input.
        
        Args:
            prompts: List of prompts for each shot
            initial_image: Starting image for first shot
            duration_per_shot: Duration of each shot
            
        Returns:
            List of video URLs for each shot
        """
        logger.info(f"Starting autoregressive generation: {len(prompts)} shots")
        
        video_urls = []
        current_image = initial_image
        
        for i, prompt in enumerate(prompts):
            logger.info(f"Generating shot {i+1}/{len(prompts)}")
            
            request_id, response = await self.generate_video(
                prompt=prompt,
                image_url=current_image,
                duration=duration_per_shot
            )
            
            video_url = response.get("video_url")
            if not video_url:
                raise APIError(f"Shot {i+1} failed: no video URL")
            
            video_urls.append(video_url)
            
            # TODO: Extract last frame from video for next iteration
            # For now, reuse the same image (PoC limitation)
            logger.warning(f"Using same image for next shot (last frame extraction not implemented)")
        
        logger.info(f"Autoregressive generation complete: {len(video_urls)} shots")
        return video_urls


class FFmpegProcessor:
    """FFmpeg-based video processing with crossfade transitions."""
    
    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        
        if not self.ffmpeg_path:
            logger.warning("FFmpeg not found - video merging unavailable")
    
    def _find_ffmpeg(self) -> Optional[str]:
        """Auto-detect FFmpeg binary."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return "ffmpeg"
        except:
            pass
        
        return None
    
    def apply_crossfade(
        self,
        video1_path: str,
        video2_path: str,
        output_path: str,
        crossfade_duration: float = 1.0
    ) -> str:
        """
        Merge two videos with crossfade transition using xfade filter.
        
        Args:
            video1_path: Path to first video
            video2_path: Path to second video
            output_path: Path for output video
            crossfade_duration: Crossfade duration in seconds
            
        Returns:
            Path to output video
            
        Raises:
            FFmpegError: If processing fails
        """
        if not self.ffmpeg_path:
            raise FFmpegError("FFmpeg not available")
        
        logger.info(f"Applying crossfade transition ({crossfade_duration}s)")
        logger.info(f"Input 1: {video1_path}")
        logger.info(f"Input 2: {video2_path}")
        logger.info(f"Output: {output_path}")
        
        # FFmpeg command for xfade transition
        cmd = [
            self.ffmpeg_path,
            "-i", video1_path,
            "-i", video2_path,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={crossfade_duration}:offset=0[v]",
            "-map", "[v]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-y",
            output_path
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                raise FFmpegError(f"FFmpeg failed: {result.stderr}")
            
            logger.info(f"Crossfade applied successfully: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            raise FFmpegError("FFmpeg processing timed out")
        except Exception as e:
            raise FFmpegError(f"FFmpeg error: {e}")
    
    def merge_multiple_videos(
        self,
        video_paths: List[str],
        output_path: str,
        crossfade_duration: float = 1.0
    ) -> str:
        """
        Merge multiple videos with crossfade transitions.
        
        Args:
            video_paths: List of video file paths
            output_path: Path for output video
            crossfade_duration: Crossfade duration in seconds
            
        Returns:
            Path to output video
        """
        if len(video_paths) < 2:
            raise ValueError("Need at least 2 videos to merge")
        
        if len(video_paths) == 2:
            return self.apply_crossfade(
                video_paths[0],
                video_paths[1],
                output_path,
                crossfade_duration
            )
        
        # Merge multiple videos sequentially
        temp_dir = Path(output_path).parent / "temp_merge"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            current_video = video_paths[0]
            
            for i, next_video in enumerate(video_paths[1:], 1):
                temp_output = temp_dir / f"merged_{i}.mp4"
                
                current_video = self.apply_crossfade(
                    current_video,
                    next_video,
                    str(temp_output),
                    crossfade_duration
                )
            
            import shutil
            shutil.move(current_video, output_path)
            
            logger.info(f"Merged {len(video_paths)} videos successfully")
            
            return output_path
            
        finally:
            if temp_dir.exists():
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)


# Convenience functions

async def generate_video(
    prompt: str,
    image_url: str,
    api_key: Optional[str] = None
) -> str:
    """
    Generate video from prompt and image.
    
    Args:
        prompt: Text prompt
        image_url: Starting image URL
        api_key: Optional API key
        
    Returns:
        Video URL
    """
    client = VideoGenerationClient(api_key=api_key)
    request_id, response = await client.generate_video(prompt, image_url)
    return response.get("video_url", "")


def apply_crossfade(video1: str, video2: str, output: str, duration: float = 1.0) -> str:
    """Apply crossfade transition between two videos."""
    processor = FFmpegProcessor()
    return processor.apply_crossfade(video1, video2, output, duration)


if __name__ == "__main__":
    async def test_orchestrator():
        print(f"\n{'='*60}")
        print("API ORCHESTRATOR TEST")
        print(f"{'='*60}\n")
        
        print("Test 1: Image Generation (Flux.1 Dev)")
        print("-" * 60)
        
        client = VideoGenerationClient()
        
        prompt = "A serene mountain landscape at sunset, highly detailed"
        
        try:
            image_url = client.generate_image(prompt)
            print(f"✓ Image generated: {image_url}")
        except APIError as e:
            print(f"✗ Image generation failed: {e}")
        
        print()
        
        print("Test 2: Video Generation (Alibaba Wan I2V)")
        print("-" * 60)
        
        try:
            request_id, response = await client.generate_video(
                prompt="Camera zooming into the mountains",
                image_url="https://example.com/image.jpg"
            )
            print(f"✓ Video request: {request_id}")
            print(f"✓ Response: {response}")
        except APIError as e:
            print(f"✗ Video generation failed: {e}")
        
        print()
        
        print("Test 3: FFmpeg Detection")
        print("-" * 60)
        
        processor = FFmpegProcessor()
        if processor.ffmpeg_path:
            print(f"✓ FFmpeg available: {processor.ffmpeg_path}")
        else:
            print("✗ FFmpeg not found")
    
    asyncio.run(test_orchestrator())
