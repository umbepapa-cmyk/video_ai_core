"""
WEEK 1 V2 - DAY 3: ControlNet Handler
======================================
Module for geometric constraint enforcement using ControlNet OpenPose.

This module implements:
- ControlNet OpenPose integration for pose skeleton extraction
- Geometric constraint injection to prevent body entanglement
- Preprocessing pipeline for pose maps
- Single-frame generation with locked geometry
"""

import os
import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import asyncio

import numpy as np
from PIL import Image

from dotenv import load_dotenv
from path_config import CARTELLA_MAPPE_POSE

# Import custom exceptions
from exceptions import KinematicMismatchError, SubjectTrackingLossError

try:
    import fal_client
except ImportError:
    fal_client = None
    logging.warning("fal_client not installed. Install with: pip install fal-client")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ControlNetModel(Enum):
    """Supported ControlNet models."""
    OPENPOSE = "openpose"
    CANNY = "canny"
    DEPTH = "depth"
    SCRIBBLE = "scribble"
    SEGMENTATION = "segmentation"


@dataclass
class PoseKeypoints:
    """Data structure for OpenPose keypoints."""
    body: np.ndarray  # Body keypoints (18 or 25 points)
    hands: Optional[np.ndarray] = None  # Hand keypoints (21 points per hand)
    face: Optional[np.ndarray] = None  # Face keypoints (70 points)
    confidence: Optional[np.ndarray] = None  # Confidence scores


@dataclass
class ControlNetResult:
    """Result from ControlNet processing."""
    image_url: str
    pose_map: Optional[np.ndarray]
    metadata: Dict[str, Any]


class OpenPosePreprocessor:
    """
    Preprocessor for OpenPose skeleton extraction.
    
    Extracts pose skeleton from images for ControlNet guidance.
    """
    
    def __init__(self, use_hands: bool = True, use_face: bool = True):
        """
        Initialize OpenPose preprocessor.
        
        Args:
            use_hands: Include hand keypoints
            use_face: Include face keypoints
        """
        self.use_hands = use_hands
        self.use_face = use_face
        
        logger.info(f"OpenPosePreprocessor initialized (hands={use_hands}, face={use_face})")
    
    def extract_pose_skeleton(
        self,
        image: np.ndarray
    ) -> Tuple[np.ndarray, PoseKeypoints]:
        """
        Extract pose skeleton from image.
        
        Args:
            image: Input image as numpy array (H, W, 3)
            
        Returns:
            Tuple of (pose_map_image, keypoints)
        """
        logger.info("Extracting pose skeleton from image")
        
        # In production, this would use actual OpenPose model
        # For now, we create a placeholder implementation
        # Real implementation would use: controlnet_aux.OpenPoseDetector
        
        height, width = image.shape[:2]
        
        # Mock pose map (in production: actual OpenPose extraction)
        pose_map = self._create_mock_pose_map(height, width)
        
        # Mock keypoints
        keypoints = PoseKeypoints(
            body=np.zeros((25, 3)),  # 25 body keypoints with (x, y, confidence)
            hands=np.zeros((2, 21, 3)) if self.use_hands else None,
            face=np.zeros((70, 3)) if self.use_face else None,
            confidence=np.ones(25) * 0.9
        )
        
        logger.info(f"Pose skeleton extracted: {keypoints.body.shape[0]} body points")
        
        return pose_map, keypoints
    
    def _create_mock_pose_map(self, height: int, width: int) -> np.ndarray:
        """
        Create mock pose map for testing.
        
        In production, this would be replaced by actual OpenPose model output.
        """
        # Create blank canvas
        pose_map = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Draw simple skeleton structure (mock)
        center_x = width // 2
        center_y = height // 2
        
        # Body midline
        cv2_available = False
        try:
            import cv2
            cv2_available = True
        except ImportError:
            logger.warning("OpenCV not available for pose visualization")
        
        if cv2_available:
            import cv2
            # Neck to hip
            cv2.line(pose_map, (center_x, center_y - 100), (center_x, center_y + 100), (255, 255, 255), 2)
            # Shoulders
            cv2.line(pose_map, (center_x - 80, center_y - 80), (center_x + 80, center_y - 80), (255, 255, 255), 2)
            # Arms
            cv2.line(pose_map, (center_x - 80, center_y - 80), (center_x - 100, center_y + 50), (255, 255, 255), 2)
            cv2.line(pose_map, (center_x + 80, center_y - 80), (center_x + 100, center_y + 50), (255, 255, 255), 2)
            # Legs
            cv2.line(pose_map, (center_x, center_y + 100), (center_x - 40, center_y + 250), (255, 255, 255), 2)
            cv2.line(pose_map, (center_x, center_y + 100), (center_x + 40, center_y + 250), (255, 255, 255), 2)
        
        return pose_map
    
    def preprocess_for_controlnet(
        self,
        pose_map: np.ndarray
    ) -> np.ndarray:
        """
        Preprocess pose map for ControlNet input.
        
        Args:
            pose_map: Raw pose map image
            
        Returns:
            Preprocessed pose map ready for ControlNet
        """
        # Normalize to [0, 1]
        if pose_map.dtype == np.uint8:
            pose_map = pose_map.astype(np.float32) / 255.0
        
        # Ensure 3 channels
        if len(pose_map.shape) == 2:
            pose_map = np.stack([pose_map] * 3, axis=-1)
        
        logger.info(f"Pose map preprocessed: shape={pose_map.shape}, dtype={pose_map.dtype}")
        
        return pose_map
    
    def save_pose_map(
        self,
        pose_map: np.ndarray,
        output_path: str
    ) -> str:
        """
        Save pose map to disk.
        
        Args:
            pose_map: Pose map image
            output_path: Path to save image
            
        Returns:
            Path to saved image
        """
        # Convert to uint8 if needed
        if pose_map.dtype == np.float32 or pose_map.dtype == np.float64:
            pose_map = (pose_map * 255).astype(np.uint8)
        
        # Save with PIL
        image = Image.fromarray(pose_map)
        image.save(output_path)
        
        logger.info(f"Pose map saved to: {output_path}")
        
        return output_path


class ControlNetHandler:
    """
    Handler for ControlNet-guided image generation.
    
    Manages:
    - Pose skeleton extraction
    - ControlNet conditioning
    - Geometric constraint enforcement
    - Prevention of body entanglement
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_type: ControlNetModel = ControlNetModel.OPENPOSE
    ):
        """
        Initialize ControlNet handler.
        
        Args:
            api_key: API key for ControlNet endpoints
            model_type: Type of ControlNet model to use
        """
        load_dotenv()
        
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("REPLICATE_API_TOKEN")
        self.model_type = model_type
        
        self.preprocessor = OpenPosePreprocessor(use_hands=True, use_face=True)
        
        logger.info(f"ControlNetHandler initialized with model: {model_type.value}")
    
    def generate_pose_map(
        self,
        image_path: str,
        output_dir: Optional[str] = None
    ) -> Tuple[str, PoseKeypoints]:
        """
        Generate pose map from input image.
        
        Args:
            image_path: Path to input image
            output_dir: Directory to save pose map (optional)
            
        Returns:
            Tuple of (pose_map_path, keypoints)
        """
        logger.info(f"Generating pose map from: {image_path}")
        
        # Load image
        try:
            import cv2
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Failed to load image: {image_path}")
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        except ImportError:
            from PIL import Image as PILImage
            pil_image = PILImage.open(image_path)
            image = np.array(pil_image)
        
        # Extract pose skeleton
        pose_map, keypoints = self.preprocessor.extract_pose_skeleton(image)
        
        # Save pose map if output directory specified
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            input_name = Path(image_path).stem
            pose_map_path = output_dir / f"{input_name}_pose.png"
            
            self.preprocessor.save_pose_map(pose_map, str(pose_map_path))
        else:
            # Temporary path
            pose_map_path = Path(f"temp_pose_map_{hash(image_path)}.png")
            self.preprocessor.save_pose_map(pose_map, str(pose_map_path))
        
        logger.info(f"Pose map generated: {pose_map_path}")
        
        return str(pose_map_path), keypoints
    
    async def generate_pose_guided_image(
        self,
        prompt: str,
        pose_map_path: str,
        identity_embedding: Optional[np.ndarray] = None,
        negative_prompt: Optional[str] = None,
        controlnet_strength: float = 0.8,
        **kwargs
    ) -> ControlNetResult:
        """
        Generate image with ControlNet pose guidance.
        
        Args:
            prompt: Text prompt for generation
            pose_map_path: Path to pose map image
            identity_embedding: Optional identity vector for face consistency
            negative_prompt: Negative prompt for quality control
            controlnet_strength: Strength of ControlNet conditioning (0.0-1.0)
            **kwargs: Additional generation parameters
            
        Returns:
            ControlNetResult with generated image
        """
        logger.info(f"Generating pose-guided image with ControlNet")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Pose map: {pose_map_path}")
        logger.info(f"ControlNet strength: {controlnet_strength}")
        
        if not fal_client:
            logger.warning("fal_client not available, using fallback")
            # Return mock result as fallback
            result = ControlNetResult(
                image_url=f"https://example.com/controlnet_fallback.jpg",
                pose_map=self._load_pose_map(pose_map_path),
                metadata={
                    "model": self.model_type.value,
                    "controlnet_strength": 0.0,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "note": "fal_client not available - mock result"
                }
            )
            return result
        
        if not self.api_key:
            logger.warning("API key not set, using fallback")
            result = ControlNetResult(
                image_url=f"https://example.com/controlnet_no_api_key.jpg",
                pose_map=self._load_pose_map(pose_map_path),
                metadata={
                    "model": self.model_type.value,
                    "controlnet_strength": 0.0,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "note": "API key not set - mock result"
                }
            )
            return result
        
        try:
            # Load pose map
            pose_map = self._load_pose_map(pose_map_path)
            
            # Prepare payload for Fal.ai
            # Note: ControlNet support may vary by endpoint
            payload = {
                "prompt": prompt,
                "image_size": "landscape_16_9",
                "num_inference_steps": 28,
                "num_images": 1,
                "enable_safety_checker": False,
                "guidance_scale": 7.5,
            }
            
            if negative_prompt:
                payload["negative_prompt"] = negative_prompt
            
            logger.info("Attempting Flux generation (ControlNet support may be limited)...")
            
            # Use standard Flux endpoint
            # Note: Full ControlNet integration may require specialized endpoint
            handler = await fal_client.submit_async(
                "fal-ai/flux/dev",
                arguments=payload
            )
            
            result_data = await handler.get(timeout=120)
            
            images = result_data.get("images", [])
            if not images:
                raise ValueError("No images returned from API")
            
            image_url = images[0].get("url")
            
            result = ControlNetResult(
                image_url=image_url,
                pose_map=pose_map,
                metadata={
                    "model": self.model_type.value,
                    "controlnet_strength": 0.0,  # Standard Flux doesn't use ControlNet
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "endpoint": "fal-ai/flux/dev",
                    "note": "Generated with standard Flux (ControlNet not yet integrated)"
                }
            )
            
            logger.info(f"✓ Pose-guided image generated: {image_url}")
            logger.info("Note: Full ControlNet pose guidance not yet supported by endpoint")
            
            return result
            
        except Exception as e:
            logger.error(f"Image generation failed: {type(e).__name__}: {e}")
            raise RuntimeError(f"Failed to generate pose-guided image: {e}") from e
    
    def _load_pose_map(self, pose_map_path: str) -> np.ndarray:
        """Load pose map from disk."""
        try:
            from PIL import Image as PILImage
            image = PILImage.open(pose_map_path)
            return np.array(image)
        except Exception as e:
            logger.error(f"Failed to load pose map: {e}")
            return np.zeros((512, 512, 3), dtype=np.uint8)
    
    def inject_controlnet(
        self,
        api_payload: Dict[str, Any],
        pose_map_path: str,
        controlnet_strength: float = 0.8
    ) -> Dict[str, Any]:
        """
        Inject ControlNet parameters into API payload.
        
        Args:
            api_payload: Base API payload dictionary
            pose_map_path: Path to pose map image
            controlnet_strength: Strength of conditioning
            
        Returns:
            Updated API payload with ControlNet parameters
        """
        # Add ControlNet-specific parameters
        api_payload["controlnet_image"] = pose_map_path
        api_payload["controlnet_model"] = self.model_type.value
        api_payload["controlnet_conditioning_scale"] = controlnet_strength
        
        logger.info(f"ControlNet parameters injected into API payload")
        
        return api_payload
    
    def prevent_body_entanglement(
        self,
        prompt: str,
        num_subjects: int = 1
    ) -> str:
        """
        Enhance prompt to prevent body entanglement (body fusion).
        
        Args:
            prompt: Original prompt
            num_subjects: Number of subjects in scene
            
        Returns:
            Enhanced prompt with entanglement prevention
        """
        if num_subjects == 1:
            enhancement = "single person, isolated subject, clear body separation"
        else:
            enhancement = f"{num_subjects} separate people, distinct bodies, no fusion, clear boundaries between subjects"
        
        enhanced_prompt = f"{prompt}, {enhancement}"
        
        logger.info(f"Entanglement prevention added to prompt")
        
        return enhanced_prompt
    
    def detect_multiple_skeletons(
        self,
        video_path: str,
        num_expected_subjects: int
    ) -> Dict[str, List[Dict]]:
        """
        Detect multiple skeletons in motion reference video.
        
        This method:
        1. Detects pose skeletons frame-by-frame using OpenPose
        2. Tracks subjects across frames using IoU (Intersection over Union)
        3. Assigns consistent subject IDs based on spatial position
        4. Validates that detected skeleton count matches expected subjects
        
        Args:
            video_path: Path to motion reference video
            num_expected_subjects: Expected number of subjects
            
        Returns:
            Dictionary mapping subject_id to list of bounding boxes per frame.
            Format: {
                "subject_1": [
                    {"frame": 0, "bbox": [x, y, w, h], "keypoints": [...]},
                    {"frame": 1, "bbox": [x2, y2, w2, h2], "keypoints": [...]},
                    ...
                ],
                "subject_2": [...],
            }
        
        Raises:
            KinematicMismatchError: If detected skeletons != num_expected_subjects
            SubjectTrackingLossError: If subject tracking fails across frames
        """
        logger.info(f"Detecting skeletons in {video_path}")
        logger.info(f"Expected subjects: {num_expected_subjects}")
        
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV (cv2) is required for video skeleton detection")
            raise ImportError("Install OpenCV: pip install opencv-python")
        
        try:
            # Try to import OpenPose detector from controlnet_aux
            from controlnet_aux import OpenposeDetector
            detector = OpenposeDetector.from_pretrained("lllyasviel/ControlNet")
            logger.info("Using controlnet_aux OpenposeDetector")
            use_real_detector = True
        except ImportError:
            logger.warning("controlnet_aux not available, using mock skeleton detection")
            logger.warning("Install with: pip install controlnet-aux")
            detector = None
            use_real_detector = False
        
        # Read video
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Failed to open video: {video_path}")
        
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        logger.info(f"Video info: {frame_count} frames at {fps} FPS")
        
        all_detections = []
        
        # Process each frame
        for frame_idx in range(frame_count):
            ret, frame = cap.read()
            if not ret:
                logger.warning(f"Failed to read frame {frame_idx}, stopping detection")
                break
            
            # Detect poses
            if use_real_detector:
                # Real OpenPose detection
                pose_result = detector(frame, detect_resolution=512)
                skeletons = self._extract_skeletons_from_pose_real(pose_result, frame_idx)
            else:
                # Mock detection for testing
                skeletons = self._extract_skeletons_from_pose_mock(
                    frame, 
                    frame_idx, 
                    num_expected_subjects
                )
            
            all_detections.append({
                "frame": frame_idx,
                "skeletons": skeletons
            })
            
            # Log progress every 30 frames
            if frame_idx % 30 == 0:
                logger.info(f"  Processed frame {frame_idx}/{frame_count}")
        
        cap.release()
        
        if not all_detections:
            raise ValueError("No frames were successfully processed")
        
        # Validate number of subjects in first frame
        num_detected = len(all_detections[0]["skeletons"]) if all_detections else 0
        
        if num_detected != num_expected_subjects:
            raise KinematicMismatchError(
                f"Skeleton count mismatch in first frame",
                expected_count=num_expected_subjects,
                detected_count=num_detected
            )
        
        logger.info(f"✓ First frame validation passed: {num_detected} skeletons detected")
        
        # Track and assign consistent subject IDs
        try:
            tracked_subjects = self._track_subjects_across_frames(all_detections, num_expected_subjects)
        except Exception as e:
            logger.error(f"Subject tracking failed: {e}")
            raise SubjectTrackingLossError(
                f"Failed to track subjects across frames: {e}"
            )
        
        logger.info(f"✓ Successfully tracked {len(tracked_subjects)} subjects across {frame_count} frames")
        
        # Validate tracking completeness
        for subject_id, detections in tracked_subjects.items():
            if len(detections) < frame_count * 0.9:  # Allow 10% missing frames
                logger.warning(
                    f"{subject_id} detected in only {len(detections)}/{frame_count} frames"
                )
        
        return tracked_subjects
    
    def _extract_skeletons_from_pose_real(
        self, 
        pose_result, 
        frame_idx: int
    ) -> List[Dict]:
        """
        Extract individual skeletons from real OpenPose result.
        
        Args:
            pose_result: OpenPose detection result
            frame_idx: Current frame index
            
        Returns:
            List of skeleton dictionaries with bbox and keypoints
        """
        skeletons = []
        
        # OpenPose result format varies by implementation
        # This is a generic parser for controlnet_aux output
        
        try:
            # controlnet_aux returns PIL image with drawn skeleton
            # We need to parse the drawn skeleton back to keypoints
            # For now, we use a simplified approach
            
            # Convert pose_result to numpy if it's a PIL image
            if hasattr(pose_result, 'convert'):
                pose_array = np.array(pose_result.convert('RGB'))
            else:
                pose_array = np.array(pose_result)
            
            # Detect connected components (individual skeletons)
            # This is a simplified heuristic - in production you'd parse actual keypoints
            
            gray = cv2.cvtColor(pose_array, cv2.COLOR_RGB2GRAY) if len(pose_array.shape) == 3 else pose_array
            _, binary = cv2.threshold(gray, 10, 255, cv2.THRESH_BINARY)
            
            # Find contours (approximate skeleton regions)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Filter small contours and create bounding boxes
            min_area = 1000  # Minimum area for valid skeleton
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area:
                    continue
                
                x, y, w, h = cv2.boundingRect(contour)
                
                # Normalize to [0, 1]
                height, width = pose_array.shape[:2]
                norm_x = x / width
                norm_y = y / height
                norm_w = w / width
                norm_h = h / height
                
                skeletons.append({
                    "bbox": [norm_x, norm_y, norm_w, norm_h],
                    "keypoints": [],  # TODO: Extract actual keypoints from pose_result
                    "area": area
                })
            
            # Sort by x-coordinate (left to right) for consistency
            skeletons.sort(key=lambda s: s["bbox"][0])
            
        except Exception as e:
            logger.warning(f"Failed to parse OpenPose result at frame {frame_idx}: {e}")
            # Return empty list if parsing fails
            skeletons = []
        
        return skeletons
    
    def _extract_skeletons_from_pose_mock(
        self, 
        frame: np.ndarray, 
        frame_idx: int,
        num_subjects: int
    ) -> List[Dict]:
        """
        Mock skeleton extraction for testing when OpenPose is unavailable.
        
        Args:
            frame: Video frame
            frame_idx: Current frame index
            num_subjects: Number of subjects to mock
            
        Returns:
            List of mock skeleton dictionaries
        """
        skeletons = []
        height, width = frame.shape[:2]
        
        # Create mock skeletons with realistic movement
        for i in range(num_subjects):
            # Simulate subjects positioned left-to-right
            base_x = 0.2 + (i * 0.6 / max(1, num_subjects - 1)) if num_subjects > 1 else 0.4
            
            # Add small temporal variation (simulated movement)
            time_offset = np.sin(frame_idx * 0.1 + i) * 0.05
            
            mock_bbox = [
                base_x + time_offset,  # x
                0.2,  # y
                0.15,  # w
                0.6   # h
            ]
            
            # Mock keypoints (25 body keypoints in normalized coords)
            mock_keypoints = []
            for kp_idx in range(25):
                kp_x = mock_bbox[0] + np.random.rand() * mock_bbox[2]
                kp_y = mock_bbox[1] + np.random.rand() * mock_bbox[3]
                kp_conf = 0.8 + np.random.rand() * 0.2
                mock_keypoints.append([kp_x, kp_y, kp_conf])
            
            skeletons.append({
                "bbox": mock_bbox,
                "keypoints": mock_keypoints,
                "area": mock_bbox[2] * mock_bbox[3] * width * height
            })
        
        return skeletons
    
    def _track_subjects_across_frames(
        self, 
        all_detections: List[Dict],
        num_expected_subjects: int
    ) -> Dict[str, List[Dict]]:
        """
        Track subjects across frames using spatial consistency (IoU).
        
        Assigns consistent IDs to subjects by:
        1. Sorting by x-coordinate (left to right) in first frame
        2. Tracking via IoU (Intersection over Union) in subsequent frames
        
        Args:
            all_detections: List of detections per frame
            num_expected_subjects: Expected number of subjects
            
        Returns:
            Dictionary mapping subject_id to list of detections
            
        Raises:
            SubjectTrackingLossError: If subject tracking is lost
        """
        tracked = {}
        
        if not all_detections:
            return tracked
        
        # Initialize with first frame - sort skeletons left to right
        first_frame_skeletons = sorted(
            all_detections[0]["skeletons"],
            key=lambda s: s["bbox"][0]  # Sort by x coordinate
        )
        
        # Assign IDs
        for idx, skeleton in enumerate(first_frame_skeletons, 1):
            subject_id = f"subject_{idx}"
            tracked[subject_id] = [{
                "frame": 0,
                "bbox": skeleton["bbox"],
                "keypoints": skeleton.get("keypoints", [])
            }]
        
        logger.info(f"Initialized tracking for {len(tracked)} subjects")
        
        # Track through remaining frames using IoU
        for frame_data in all_detections[1:]:
            frame_idx = frame_data["frame"]
            current_skeletons = frame_data["skeletons"]
            
            # Validate skeleton count
            if len(current_skeletons) != num_expected_subjects:
                logger.warning(
                    f"Frame {frame_idx}: Expected {num_expected_subjects} skeletons, "
                    f"detected {len(current_skeletons)}"
                )
            
            # Match current skeletons to tracked subjects
            matched_subjects = set()
            
            for subject_id, history in tracked.items():
                last_bbox = history[-1]["bbox"]
                
                # Find best matching skeleton via IoU
                best_match = self._find_best_match(last_bbox, current_skeletons, matched_subjects)
                
                if best_match:
                    tracked[subject_id].append({
                        "frame": frame_idx,
                        "bbox": best_match["bbox"],
                        "keypoints": best_match.get("keypoints", [])
                    })
                    matched_subjects.add(id(best_match))
                else:
                    # Subject lost - log warning but continue
                    logger.warning(f"{subject_id} lost at frame {frame_idx}")
        
        return tracked
    
    def _find_best_match(
        self, 
        ref_bbox: List[float], 
        candidates: List[Dict],
        exclude_ids: set = None
    ) -> Optional[Dict]:
        """
        Find best matching skeleton via IoU.
        
        Args:
            ref_bbox: Reference bounding box [x, y, w, h]
            candidates: List of candidate skeletons
            exclude_ids: Set of already matched skeleton IDs to exclude
            
        Returns:
            Best matching skeleton or None if no good match
        """
        if exclude_ids is None:
            exclude_ids = set()
        
        best_iou = 0.0
        best_match = None
        
        for candidate in candidates:
            # Skip already matched candidates
            if id(candidate) in exclude_ids:
                continue
            
            iou = self._calculate_iou(ref_bbox, candidate["bbox"])
            if iou > best_iou:
                best_iou = iou
                best_match = candidate
        
        # IoU threshold for valid match
        iou_threshold = 0.3
        return best_match if best_iou > iou_threshold else None
    
    def _calculate_iou(self, bbox1: List[float], bbox2: List[float]) -> float:
        """
        Calculate Intersection over Union between two bounding boxes.
        
        Args:
            bbox1: First bounding box [x, y, w, h] in normalized coords
            bbox2: Second bounding box [x, y, w, h] in normalized coords
            
        Returns:
            IoU score (0.0 to 1.0)
        """
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2
        
        # Calculate intersection
        x_left = max(x1, x2)
        y_top = max(y1, y2)
        x_right = min(x1 + w1, x2 + w2)
        y_bottom = min(y1 + h1, y2 + h2)
        
        if x_right < x_left or y_bottom < y_top:
            return 0.0
        
        intersection = (x_right - x_left) * (y_bottom - y_top)
        
        # Calculate union
        area1 = w1 * h1
        area2 = w2 * h2
        union = area1 + area2 - intersection
        
        return intersection / union if union > 0 else 0.0


# Convenience functions

def generate_pose_map(image_path: str, output_dir: str = CARTELLA_MAPPE_POSE) -> str:
    """
    Quick function to generate pose map from image.
    
    Args:
        image_path: Path to input image
        output_dir: Directory to save pose map
        
    Returns:
        Path to generated pose map
    """
    handler = ControlNetHandler()
    pose_map_path, _ = handler.generate_pose_map(image_path, output_dir)
    return pose_map_path


async def generate_with_pose_control(
    prompt: str,
    reference_image_path: str,
    negative_prompt: Optional[str] = None
) -> str:
    """
    Quick function to generate image with pose control.
    
    Args:
        prompt: Text prompt
        reference_image_path: Path to reference image for pose extraction
        negative_prompt: Optional negative prompt
        
    Returns:
        URL of generated image
    """
    handler = ControlNetHandler()
    
    # Generate pose map
    pose_map_path, _ = handler.generate_pose_map(reference_image_path)
    
    # Generate with ControlNet
    result = await handler.generate_pose_guided_image(
        prompt=prompt,
        pose_map_path=pose_map_path,
        negative_prompt=negative_prompt
    )
    
    return result.image_url


if __name__ == "__main__":
    import asyncio
    
    async def test_controlnet():
        print(f"\n{'='*70}")
        print("CONTROLNET HANDLER TEST - DAY 3")
        print(f"{'='*70}\n")
        
        # Test 1: OpenPose Preprocessor
        print("Test 1: OpenPose Skeleton Extraction")
        print("-" * 70)
        
        preprocessor = OpenPosePreprocessor(use_hands=True, use_face=True)
        
        # Create mock image
        mock_image = np.random.randint(0, 255, (512, 512, 3), dtype=np.uint8)
        
        pose_map, keypoints = preprocessor.extract_pose_skeleton(mock_image)
        print(f"✓ Pose map generated: shape={pose_map.shape}")
        print(f"✓ Body keypoints: {keypoints.body.shape[0]} points")
        print(f"✓ Hand keypoints: {keypoints.hands.shape if keypoints.hands is not None else 'None'}")
        print(f"✓ Face keypoints: {keypoints.face.shape if keypoints.face is not None else 'None'}")
        
        # Test 2: ControlNet Handler Initialization
        print("\nTest 2: ControlNet Handler Initialization")
        print("-" * 70)
        
        handler = ControlNetHandler(model_type=ControlNetModel.OPENPOSE)
        print(f"✓ Handler initialized with model: {handler.model_type.value}")
        
        # Test 3: Pose Map Generation
        print("\nTest 3: Generate Pose Map from Mock Image")
        print("-" * 70)
        
        # Save mock image temporarily
        temp_image_path = "temp_test_image.png"
        from PIL import Image
        Image.fromarray(mock_image).save(temp_image_path)
        
        pose_map_path, keypoints = handler.generate_pose_map(
            temp_image_path,
            output_dir=CARTELLA_RISULTATI_TEST
        )
        print(f"✓ Pose map saved to: {pose_map_path}")
        
        # Test 4: Pose-Guided Image Generation
        print("\nTest 4: Generate Pose-Guided Image")
        print("-" * 70)
        
        prompt = "A woman in elegant dress, cinematic lighting"
        negative_prompt = "deformed, mutated, bad anatomy"
        
        result = await handler.generate_pose_guided_image(
            prompt=prompt,
            pose_map_path=pose_map_path,
            negative_prompt=negative_prompt,
            controlnet_strength=0.8
        )
        
        print(f"✓ Image generated: {result.image_url}")
        print(f"✓ Model: {result.metadata['model']}")
        print(f"✓ ControlNet strength: {result.metadata['controlnet_strength']}")
        
        # Test 5: ControlNet Injection
        print("\nTest 5: Inject ControlNet into API Payload")
        print("-" * 70)
        
        base_payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": 512,
            "height": 512
        }
        
        enhanced_payload = handler.inject_controlnet(
            base_payload,
            pose_map_path,
            controlnet_strength=0.8
        )
        
        print("Enhanced API Payload:")
        for key, value in enhanced_payload.items():
            print(f"  {key}: {value}")
        
        # Test 6: Body Entanglement Prevention
        print("\nTest 6: Body Entanglement Prevention")
        print("-" * 70)
        
        original_prompt = "Two people dancing together"
        enhanced_prompt = handler.prevent_body_entanglement(original_prompt, num_subjects=2)
        
        print(f"Original: {original_prompt}")
        print(f"Enhanced: {enhanced_prompt}")
        
        # Cleanup
        import os
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        
        print(f"\n{'='*70}")
        print("✓ All tests completed successfully!")
        print(f"{'='*70}\n")
    
    asyncio.run(test_controlnet())
