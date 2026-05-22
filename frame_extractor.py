"""
FASE 1: Frame Extractor Module
==============================
Academic PoC for spatial video analysis using OpenCV.

This module implements:
- Laplacian variance calculation for motion blur detection
- solvePnP for Euler angle computation (Yaw, Pitch, Roll)
- Extraction of exactly 5 frames with diverse spatial coordinates and angles
"""

import cv2
import numpy as np
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Data structure for extracted frame with spatial metadata."""
    frame_number: int
    image: np.ndarray
    timestamp_ms: float
    laplacian_variance: float
    euler_angles: Tuple[float, float, float]  # (yaw, pitch, roll) in degrees
    spatial_coordinates: Tuple[float, float, float]  # (x, y, z) position


class FrameExtractor:
    """
    Extracts frames from video with spatial analysis.
    
    Uses mathematical algorithms for:
    1. Motion blur detection (Laplacian operator)
    2. Camera pose estimation (PnP solver)
    """
    
    def __init__(self, laplacian_threshold: float = 100.0):
        """
        Initialize extractor.
        
        Args:
            laplacian_threshold: Minimum variance to consider frame sharp
        """
        self.laplacian_threshold = laplacian_threshold
        
        # Camera calibration parameters (generic values for PoC)
        self.focal_length = 800.0
        self.camera_matrix = np.array([
            [self.focal_length, 0, 320],
            [0, self.focal_length, 240],
            [0, 0, 1]
        ], dtype=np.float64)
        
        self.dist_coeffs = np.zeros((4, 1))
        
    def calculate_laplacian_variance(self, image: np.ndarray) -> float:
        """
        Calculate variance of Laplacian to detect motion blur.
        
        Higher variance indicates sharper image (less blur).
        
        Args:
            image: Input image (BGR or grayscale)
            
        Returns:
            Laplacian variance value
        """
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
            
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        variance = laplacian.var()
        
        logger.debug(f"Laplacian variance: {variance:.2f}")
        return float(variance)
    
    def estimate_pose(self, image: np.ndarray) -> Optional[Tuple[float, float, float]]:
        """
        Estimate camera pose using solvePnP with feature detection.
        
        Args:
            image: Input image
            
        Returns:
            Euler angles (yaw, pitch, roll) in degrees, or None if estimation fails
        """
        try:
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                gray = image
            
            orb = cv2.ORB_create(nfeatures=100)
            keypoints, descriptors = orb.detectAndCompute(gray, None)
            
            if len(keypoints) < 4:
                logger.warning("Not enough keypoints for pose estimation")
                return None
            
            object_points = np.array([
                [0, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [1, 1, 0]
            ], dtype=np.float64)
            
            image_points = np.array([kp.pt for kp in keypoints[:4]], dtype=np.float64)
            
            success, rvec, tvec = cv2.solvePnP(
                object_points,
                image_points,
                self.camera_matrix,
                self.dist_coeffs,
                flags=cv2.SOLVEPNP_ITERATIVE
            )
            
            if not success:
                return None
            
            rotation_matrix, _ = cv2.Rodrigues(rvec)
            
            yaw = np.arctan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
            pitch = np.arctan2(-rotation_matrix[2, 0], 
                              np.sqrt(rotation_matrix[2, 1]**2 + rotation_matrix[2, 2]**2))
            roll = np.arctan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
            
            yaw_deg = np.degrees(yaw)
            pitch_deg = np.degrees(pitch)
            roll_deg = np.degrees(roll)
            
            logger.debug(f"Estimated pose - Yaw: {yaw_deg:.2f}°, Pitch: {pitch_deg:.2f}°, Roll: {roll_deg:.2f}°")
            
            return (float(yaw_deg), float(pitch_deg), float(roll_deg))
            
        except Exception as e:
            logger.error(f"Pose estimation failed: {e}")
            return None
    
    def extract_spatial_coordinates(self, frame_idx: int, total_frames: int, 
                                   euler_angles: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """
        Calculate spatial coordinates based on frame position and orientation.
        
        Args:
            frame_idx: Current frame index
            total_frames: Total number of frames
            euler_angles: Camera orientation (yaw, pitch, roll)
            
        Returns:
            Spatial coordinates (x, y, z)
        """
        t = frame_idx / max(total_frames - 1, 1)
        
        yaw, pitch, roll = euler_angles
        
        x = t * np.cos(np.radians(yaw)) * 10.0
        y = t * np.sin(np.radians(pitch)) * 10.0
        z = t * np.cos(np.radians(roll)) * 5.0
        
        return (float(x), float(y), float(z))
    
    def select_diverse_frames(self, candidate_frames: List[Frame], target_count: int = 5) -> List[Frame]:
        """
        Select frames with maximum spatial and angular diversity.
        
        Args:
            candidate_frames: List of candidate frames
            target_count: Number of frames to select
            
        Returns:
            List of selected diverse frames
        """
        if len(candidate_frames) <= target_count:
            return candidate_frames
        
        selected = []
        remaining = candidate_frames.copy()
        
        best_frame = max(remaining, key=lambda f: f.laplacian_variance)
        selected.append(best_frame)
        remaining.remove(best_frame)
        
        while len(selected) < target_count and remaining:
            def min_distance_to_selected(frame: Frame) -> float:
                distances = []
                for sel_frame in selected:
                    spatial_dist = np.linalg.norm(
                        np.array(frame.spatial_coordinates) - 
                        np.array(sel_frame.spatial_coordinates)
                    )
                    
                    angular_dist = np.linalg.norm(
                        np.array(frame.euler_angles) - 
                        np.array(sel_frame.euler_angles)
                    )
                    
                    combined_dist = spatial_dist + 0.1 * angular_dist
                    distances.append(combined_dist)
                
                return min(distances)
            
            next_frame = max(remaining, key=min_distance_to_selected)
            selected.append(next_frame)
            remaining.remove(next_frame)
        
        selected.sort(key=lambda f: f.frame_number)
        
        logger.info(f"Selected {len(selected)} diverse frames from {len(candidate_frames)} candidates")
        return selected
    
    def extract_frames(self, video_path: str, num_frames: int = 5) -> List[Frame]:
        """
        Extract exactly N frames with diverse spatial coordinates and angles.
        
        Main entry point for the module.
        
        Args:
            video_path: Path to input video file
            num_frames: Number of frames to extract (default: 5)
            
        Returns:
            List of Frame objects with spatial metadata
            
        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If video cannot be opened or has no frames
        """
        video_path_obj = Path(video_path)
        if not video_path_obj.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        logger.info(f"Opening video: {video_path}")
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise ValueError(f"Cannot open video file: {video_path}")
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        if total_frames == 0:
            raise ValueError("Video has no frames")
        
        logger.info(f"Video info - Total frames: {total_frames}, FPS: {fps:.2f}")
        
        sample_rate = max(1, total_frames // (num_frames * 3))
        
        candidate_frames = []
        frame_idx = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_idx % sample_rate == 0:
                lap_var = self.calculate_laplacian_variance(frame)
                
                if lap_var < self.laplacian_threshold:
                    logger.debug(f"Frame {frame_idx} skipped (blur detected: {lap_var:.2f})")
                    frame_idx += 1
                    continue
                
                euler_angles = self.estimate_pose(frame)
                
                if euler_angles is None:
                    euler_angles = (0.0, 0.0, 0.0)
                
                spatial_coords = self.extract_spatial_coordinates(
                    frame_idx, total_frames, euler_angles
                )
                
                timestamp_ms = (frame_idx / fps) * 1000.0 if fps > 0 else 0.0
                
                frame_obj = Frame(
                    frame_number=frame_idx,
                    image=frame.copy(),
                    timestamp_ms=timestamp_ms,
                    laplacian_variance=lap_var,
                    euler_angles=euler_angles,
                    spatial_coordinates=spatial_coords
                )
                
                candidate_frames.append(frame_obj)
                logger.debug(f"Frame {frame_idx} added as candidate")
            
            frame_idx += 1
        
        cap.release()
        
        if not candidate_frames:
            raise ValueError("No suitable frames found (all frames too blurry)")
        
        logger.info(f"Found {len(candidate_frames)} candidate frames")
        
        selected_frames = self.select_diverse_frames(candidate_frames, num_frames)
        
        logger.info(f"Successfully extracted {len(selected_frames)} frames with spatial metadata")
        
        return selected_frames


def extract_frames(video_path: str, num_frames: int = 5, 
                  laplacian_threshold: float = 100.0) -> List[Frame]:
    """
    Convenience function for frame extraction.
    
    Args:
        video_path: Path to input video file
        num_frames: Number of frames to extract
        laplacian_threshold: Minimum Laplacian variance for sharp frames
        
    Returns:
        List of extracted frames with spatial metadata
    """
    extractor = FrameExtractor(laplacian_threshold=laplacian_threshold)
    return extractor.extract_frames(video_path, num_frames)


def extract_and_save_frames_for_identity(
    video_path: str,
    output_dir: str,
    num_frames: int = 5,
    laplacian_threshold: float = 100.0,
    filename_prefix: str = ""
) -> List[Dict[str, Any]]:
    """
    WEEK 1 V2: Extract frames and save for multi-angle identity locking.
    
    This function extracts frames with diverse angles and saves them to a directory
    for use with the MultiAngleIdentityLock module.
    
    Args:
        video_path: Path to input video file
        output_dir: Directory to save extracted frames
        num_frames: Number of frames to extract (default: 5 for multi-angle)
        laplacian_threshold: Minimum Laplacian variance for sharp frames
        filename_prefix: Optional prefix for saved frame filenames (avoids collisions)
        
    Returns:
        List of frame metadata dictionaries with:
        - path: Path to saved frame image
        - angles: Tuple of (yaw, pitch, roll) in degrees
        - frame_number: Frame index
        - laplacian_variance: Sharpness metric
    """
    import cv2
    from pathlib import Path
    
    # Create output directory (absolute path for cross-module consistency on Windows)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Extracting {num_frames} frames for multi-angle identity lock")
    logger.info(f"Output directory: {output_path}")
    
    # Extract frames
    extractor = FrameExtractor(laplacian_threshold=laplacian_threshold)
    frames = extractor.extract_frames(video_path, num_frames)
    
    # Save frames and collect metadata
    frame_data = []
    
    for i, frame in enumerate(frames):
        # Save frame image
        prefix = f"{filename_prefix}_" if filename_prefix else ""
        frame_filename = f"{prefix}frame_{i:03d}_yaw{frame.euler_angles[0]:.1f}.jpg"
        frame_path = (output_path / frame_filename).resolve()
        
        cv2.imwrite(str(frame_path), frame.image)
        
        # Create metadata
        metadata = {
            'path': str(frame_path.resolve()),
            'angles': frame.euler_angles,
            'frame_number': frame.frame_number,
            'laplacian_variance': frame.laplacian_variance,
            'timestamp_ms': frame.timestamp_ms,
            'spatial_coordinates': frame.spatial_coordinates
        }
        
        frame_data.append(metadata)
        
        logger.info(f"  Saved frame {i+1}: {frame_filename} "
                   f"(Yaw={frame.euler_angles[0]:.1f}°, "
                   f"Pitch={frame.euler_angles[1]:.1f}°, "
                   f"Roll={frame.euler_angles[2]:.1f}°)")
    
    logger.info(f"✓ {len(frame_data)} frames saved for identity locking")
    
    return frame_data


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python frame_extractor.py <video_path>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    
    try:
        frames = extract_frames(video_path)
        
        print(f"\n{'='*60}")
        print(f"EXTRACTED {len(frames)} FRAMES")
        print(f"{'='*60}\n")
        
        for i, frame in enumerate(frames, 1):
            print(f"Frame {i}:")
            print(f"  - Frame number: {frame.frame_number}")
            print(f"  - Timestamp: {frame.timestamp_ms:.2f} ms")
            print(f"  - Laplacian variance: {frame.laplacian_variance:.2f}")
            print(f"  - Euler angles (Y/P/R): {frame.euler_angles[0]:.2f}°, "
                  f"{frame.euler_angles[1]:.2f}°, {frame.euler_angles[2]:.2f}°")
            print(f"  - Spatial coords (x/y/z): {frame.spatial_coordinates[0]:.2f}, "
                  f"{frame.spatial_coordinates[1]:.2f}, {frame.spatial_coordinates[2]:.2f}")
            print()
        
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
