"""
WEEK 1 V2 - Multi-Agent Spatial Conditioning: Custom Exceptions
================================================================
Custom exceptions for multi-subject video generation pipeline.

This module defines specialized exceptions for handling multi-agent
scenarios and kinematic validation errors.
"""


class KinematicMismatchError(Exception):
    """
    Raised when the number of subjects loaded differs from 
    the number of skeletons detected in the motion reference video.
    
    This error indicates a critical mismatch in multi-subject video generation:
    - Expected: Number of subjects provided in subjects_payload
    - Detected: Number of skeletons found in motion reference video
    
    Example scenarios:
    - User provides 2 subjects but video contains only 1 skeleton
    - User provides 1 subject but video contains 2 skeletons (duo choreography)
    - Skeleton detection fails or becomes inconsistent across frames
    
    Attributes:
        message: Human-readable error description
        expected_count: Number of subjects expected (from subjects_payload)
        detected_count: Number of skeletons detected in video
    """
    
    def __init__(
        self, 
        message: str,
        expected_count: int = None,
        detected_count: int = None
    ):
        """
        Initialize KinematicMismatchError.
        
        Args:
            message: Detailed error message
            expected_count: Number of expected subjects
            detected_count: Number of detected skeletons
        """
        super().__init__(message)
        self.expected_count = expected_count
        self.detected_count = detected_count
        
    def __str__(self) -> str:
        """Return formatted error string."""
        base_msg = super().__str__()
        
        if self.expected_count is not None and self.detected_count is not None:
            return (
                f"{base_msg}\n"
                f"Expected subjects: {self.expected_count}\n"
                f"Detected skeletons: {self.detected_count}"
            )
        
        return base_msg


class IdentityBleedError(Exception):
    """
    Raised when identity features from one subject leak into another
    subject's generation (Latent Identity Bleed).
    
    This occurs when spatial conditioning fails and identity embeddings
    from subject A appear in subject B's region, causing face swapping
    or feature mixing in multi-subject scenarios.
    
    Attributes:
        message: Human-readable error description
        subject_ids: List of affected subject IDs
        similarity_score: Optional cross-subject identity similarity score
    """
    
    def __init__(
        self,
        message: str,
        subject_ids: list = None,
        similarity_score: float = None
    ):
        """
        Initialize IdentityBleedError.
        
        Args:
            message: Detailed error message
            subject_ids: IDs of subjects with bleeding identities
            similarity_score: Measured identity cross-contamination score (0-1)
        """
        super().__init__(message)
        self.subject_ids = subject_ids or []
        self.similarity_score = similarity_score


class SubjectTrackingLossError(Exception):
    """
    Raised when subject tracking fails across frames in motion reference video.
    
    This occurs when the spatial consistency tracking (IoU-based) loses
    a subject between frames, indicating motion is too fast, occlusion
    occurred, or subject left frame.
    
    Attributes:
        message: Human-readable error description
        lost_subject_id: ID of the subject that was lost
        last_known_frame: Last frame index where subject was detected
        total_frames: Total number of frames in video
    """
    
    def __init__(
        self,
        message: str,
        lost_subject_id: str = None,
        last_known_frame: int = None,
        total_frames: int = None
    ):
        """
        Initialize SubjectTrackingLossError.
        
        Args:
            message: Detailed error message
            lost_subject_id: ID of lost subject
            last_known_frame: Frame index where subject was last seen
            total_frames: Total frame count in video
        """
        super().__init__(message)
        self.lost_subject_id = lost_subject_id
        self.last_known_frame = last_known_frame
        self.total_frames = total_frames
        
    def __str__(self) -> str:
        """Return formatted error string."""
        base_msg = super().__str__()
        
        if self.lost_subject_id and self.last_known_frame is not None:
            return (
                f"{base_msg}\n"
                f"Lost subject: {self.lost_subject_id}\n"
                f"Last seen at frame: {self.last_known_frame}/{self.total_frames}"
            )
        
        return base_msg


class SpatialMaskingError(Exception):
    """
    Raised when spatial mask generation or application fails.
    
    This occurs when bounding box extraction, mask creation, or
    regional prompting pipeline encounters an error.
    
    Attributes:
        message: Human-readable error description
        subject_id: ID of subject with masking failure
        frame_index: Frame where masking failed
    """
    
    def __init__(
        self,
        message: str,
        subject_id: str = None,
        frame_index: int = None
    ):
        """
        Initialize SpatialMaskingError.
        
        Args:
            message: Detailed error message
            subject_id: ID of affected subject
            frame_index: Frame index where error occurred
        """
        super().__init__(message)
        self.subject_id = subject_id
        self.frame_index = frame_index
