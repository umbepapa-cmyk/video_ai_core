"""
WEEK 3 - DAY 16: Upload Validator
===================================
Secure file upload validation for video and image files.

Features:
- MIME type validation
- File size limits (prevent DoS)
- Extension whitelist
- Magic number verification
- Frame extraction preview
"""

import os
import mimetypes
from pathlib import Path
from typing import Optional, Tuple, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Exception raised for validation errors."""
    pass


class UploadValidator:
    """
    Secure file upload validator.
    
    Validates:
    - MIME type
    - File extension
    - File size
    - Magic numbers (file header)
    """
    
    # File type configurations
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    
    VIDEO_MIMES = {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm"
    }
    
    IMAGE_MIMES = {
        "image/jpeg",
        "image/png",
        "image/webp"
    }
    
    # Magic numbers (file signatures) for verification
    MAGIC_NUMBERS = {
        # Video formats
        b"\x00\x00\x00\x18ftypmp42": "video/mp4",  # MP4
        b"\x00\x00\x00\x14ftypisom": "video/mp4",  # MP4 ISO
        b"\x00\x00\x00\x20ftypmp42": "video/mp4",  # MP4 v2
        b"\x1a\x45\xdf\xa3": "video/x-matroska",   # MKV
        
        # Image formats
        b"\xff\xd8\xff": "image/jpeg",              # JPEG
        b"\x89PNG\r\n\x1a\n": "image/png",          # PNG
        b"RIFF": "image/webp",                       # WebP (partial)
    }
    
    def __init__(
        self,
        max_video_size_mb: int = 50,
        max_image_size_mb: int = 10
    ):
        """
        Initialize validator.
        
        Args:
            max_video_size_mb: Maximum video file size in MB
            max_image_size_mb: Maximum image file size in MB
        """
        self.max_video_size_bytes = max_video_size_mb * 1024 * 1024
        self.max_image_size_bytes = max_image_size_mb * 1024 * 1024
    
    def validate_upload(
        self,
        file_path: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        expected_type: str = "video"
    ) -> Tuple[bool, str]:
        """
        Validate uploaded file.
        
        Args:
            file_path: Path to file (for file system)
            file_bytes: File content as bytes (for Streamlit UploadedFile)
            filename: Original filename
            expected_type: "video" or "image"
        
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        try:
            # Determine file source
            if file_path:
                if not os.path.exists(file_path):
                    return (False, "File not found")
                
                file_size = os.path.getsize(file_path)
                
                with open(file_path, "rb") as f:
                    header = f.read(32)
                
                if not filename:
                    filename = os.path.basename(file_path)
            
            elif file_bytes:
                file_size = len(file_bytes)
                header = file_bytes[:32]
                
                if not filename:
                    return (False, "Filename required for bytes validation")
            
            else:
                return (False, "No file provided")
            
            # 1. Validate file extension
            file_ext = Path(filename).suffix.lower()
            
            if expected_type == "video":
                if file_ext not in self.VIDEO_EXTENSIONS:
                    return (
                        False,
                        f"Invalid video extension: {file_ext}. "
                        f"Allowed: {', '.join(self.VIDEO_EXTENSIONS)}"
                    )
            elif expected_type == "image":
                if file_ext not in self.IMAGE_EXTENSIONS:
                    return (
                        False,
                        f"Invalid image extension: {file_ext}. "
                        f"Allowed: {', '.join(self.IMAGE_EXTENSIONS)}"
                    )
            else:
                return (False, f"Unknown expected type: {expected_type}")
            
            # 2. Validate file size
            if expected_type == "video":
                max_size = self.max_video_size_bytes
                max_size_mb = self.max_video_size_bytes / (1024 * 1024)
            else:
                max_size = self.max_image_size_bytes
                max_size_mb = self.max_image_size_bytes / (1024 * 1024)
            
            if file_size > max_size:
                return (
                    False,
                    f"File too large: {file_size / (1024*1024):.1f}MB "
                    f"(max: {max_size_mb:.0f}MB)"
                )
            
            if file_size == 0:
                return (False, "File is empty")
            
            # 3. Validate MIME type (from extension)
            mime_type, _ = mimetypes.guess_type(filename)
            
            if mime_type:
                if expected_type == "video" and mime_type not in self.VIDEO_MIMES:
                    return (False, f"Invalid video MIME type: {mime_type}")
                elif expected_type == "image" and mime_type not in self.IMAGE_MIMES:
                    return (False, f"Invalid image MIME type: {mime_type}")
            
            # 4. Validate magic numbers (file signature)
            is_valid_magic = self._validate_magic_number(header, expected_type)
            
            if not is_valid_magic:
                return (
                    False,
                    f"File header validation failed. "
                    f"File may be corrupted or not a valid {expected_type}."
                )
            
            # All checks passed
            logger.info(
                f"✓ Validation passed: {filename} "
                f"({file_size / (1024*1024):.2f}MB, {expected_type})"
            )
            
            return (True, "Validation successful")
        
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return (False, f"Validation error: {str(e)}")
    
    def _validate_magic_number(self, header: bytes, expected_type: str) -> bool:
        """
        Validate file using magic numbers (file signature).
        
        Args:
            header: First bytes of file
            expected_type: "video" or "image"
        
        Returns:
            True if valid, False otherwise
        """
        if not header or len(header) < 4:
            return False
        
        # Check against known magic numbers
        for magic, file_type in self.MAGIC_NUMBERS.items():
            if header.startswith(magic):
                # Verify type matches expectation
                if expected_type == "video" and file_type.startswith("video/"):
                    return True
                elif expected_type == "image" and file_type.startswith("image/"):
                    return True
        
        # Special case: MP4 variants (check for 'ftyp' at offset 4)
        if expected_type == "video" and len(header) >= 12:
            if b"ftyp" in header[4:12]:
                return True
        
        # Special case: WebP (check for WEBP after RIFF)
        if expected_type == "image" and header.startswith(b"RIFF"):
            if len(header) >= 12 and b"WEBP" in header[8:12]:
                return True
        
        return False
    
    def validate_multiple(
        self,
        files: List[Tuple[str, bytes]],
        expected_type: str = "image"
    ) -> Tuple[bool, List[str]]:
        """
        Validate multiple files (for reference faces upload).
        
        Args:
            files: List of (filename, bytes) tuples
            expected_type: "video" or "image"
        
        Returns:
            Tuple of (all_valid: bool, error_messages: List[str])
        """
        errors = []
        
        for filename, file_bytes in files:
            is_valid, error = self.validate_upload(
                file_bytes=file_bytes,
                filename=filename,
                expected_type=expected_type
            )
            
            if not is_valid:
                errors.append(f"{filename}: {error}")
        
        return (len(errors) == 0, errors)


# Convenience functions for direct use

def validate_video_upload(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    max_size_mb: int = 50
) -> Tuple[bool, str]:
    """
    Validate video file upload.
    
    Args:
        file_path: Path to video file
        file_bytes: Video content as bytes
        filename: Original filename
        max_size_mb: Maximum file size in MB
    
    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    validator = UploadValidator(max_video_size_mb=max_size_mb)
    return validator.validate_upload(
        file_path=file_path,
        file_bytes=file_bytes,
        filename=filename,
        expected_type="video"
    )


def validate_image_upload(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    max_size_mb: int = 10
) -> Tuple[bool, str]:
    """
    Validate image file upload.
    
    Args:
        file_path: Path to image file
        file_bytes: Image content as bytes
        filename: Original filename
        max_size_mb: Maximum file size in MB
    
    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    validator = UploadValidator(max_image_size_mb=max_size_mb)
    return validator.validate_upload(
        file_path=file_path,
        file_bytes=file_bytes,
        filename=filename,
        expected_type="image"
    )


def validate_reference_faces(
    files: List[Tuple[str, bytes]],
    max_size_mb: int = 10
) -> Tuple[bool, List[str]]:
    """
    Validate multiple reference face images.
    
    Args:
        files: List of (filename, bytes) tuples
        max_size_mb: Maximum file size per image in MB
    
    Returns:
        Tuple of (all_valid: bool, error_messages: List[str])
    """
    validator = UploadValidator(max_image_size_mb=max_size_mb)
    return validator.validate_multiple(files, expected_type="image")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("UPLOAD VALIDATOR - DAY 16")
    print(f"{'='*60}\n")
    
    print("Features:")
    print("✓ MIME type validation")
    print("✓ File extension whitelist")
    print("✓ Size limits (50MB video, 10MB image)")
    print("✓ Magic number verification")
    print()
    
    print("Usage example:")
    print("-" * 60)
    print("""
from upload_validator import validate_video_upload, validate_image_upload

# Validate video (Streamlit UploadedFile)
is_valid, error = validate_video_upload(
    file_bytes=uploaded_file.getvalue(),
    filename=uploaded_file.name
)

# Validate image
is_valid, error = validate_image_upload(
    file_path="/path/to/image.jpg"
)

# Validate multiple reference faces
from upload_validator import validate_reference_faces

files = [(f.name, f.getvalue()) for f in uploaded_files]
all_valid, errors = validate_reference_faces(files)
    """)
