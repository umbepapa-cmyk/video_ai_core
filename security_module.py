"""
FASE 2: Security Module
========================
GDPR-compliant security measures for video processing.

This module implements:
- Ephemeral RAM-based storage (tmpfs on Linux, temp on Windows)
- Face analysis and age verification using DeepFace
- Blocking exception for underage detection (< 25 years)
- Secure asynchronous cleanup with irreversible deletion
"""

import os
import sys
import shutil
import tempfile
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgeVerificationError(Exception):
    """Exception raised when age verification fails."""
    pass


class SecurityViolationError(Exception):
    """Exception raised for security policy violations."""
    pass


class EphemeralStorage:
    """
    Manages ephemeral storage for GDPR compliance.
    
    Uses RAM-based storage when available:
    - Linux: /dev/shm (tmpfs)
    - Windows: system temp directory
    """
    
    def __init__(self, custom_path: Optional[str] = None):
        self.custom_path = custom_path
        self.storage_path: Optional[Path] = None
        self._is_tmpfs = False
    
    def setup(self) -> Path:
        """
        Allocate ephemeral storage location.
        
        Returns:
            Path to ephemeral storage directory
        """
        if self.custom_path:
            storage_path = Path(self.custom_path)
            storage_path.mkdir(parents=True, exist_ok=True)
            self.storage_path = storage_path
            logger.info(f"Using custom ephemeral storage: {storage_path}")
            return storage_path
        
        if sys.platform.startswith('linux'):
            tmpfs_path = Path('/dev/shm')
            if tmpfs_path.exists() and tmpfs_path.is_dir():
                storage_path = tmpfs_path / f'video_synthesis_{os.getpid()}'
                storage_path.mkdir(parents=True, exist_ok=True)
                self._is_tmpfs = True
                logger.info(f"Using tmpfs ephemeral storage: {storage_path}")
                self.storage_path = storage_path
                return storage_path
        
        storage_path = Path(tempfile.mkdtemp(prefix='video_synthesis_'))
        logger.info(f"Using temp ephemeral storage: {storage_path}")
        logger.warning("Not using RAM-based storage - consider tmpfs for better GDPR compliance")
        
        self.storage_path = storage_path
        return storage_path
    
    async def cleanup_async(self) -> None:
        """Asynchronously perform irreversible deletion of ephemeral storage."""
        if not self.storage_path or not self.storage_path.exists():
            logger.warning("No storage path to clean up")
            return
        
        logger.info(f"Starting secure cleanup of: {self.storage_path}")
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._secure_delete)
            logger.info("Secure cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise
    
    def _secure_delete(self) -> None:
        """Perform secure deletion with overwrite."""
        if not self.storage_path:
            return
        
        try:
            # Overwrite files before deletion
            for root, dirs, files in os.walk(self.storage_path):
                for file in files:
                    file_path = Path(root) / file
                    try:
                        size = file_path.stat().st_size
                        
                        with open(file_path, 'wb') as f:
                            f.write(b'\x00' * size)
                        
                        with open(file_path, 'wb') as f:
                            f.write(os.urandom(size))
                        
                    except Exception as e:
                        logger.warning(f"Could not securely overwrite {file_path}: {e}")
            
            shutil.rmtree(self.storage_path, ignore_errors=False)
            logger.info(f"Deleted storage: {self.storage_path}")
            
        except Exception as e:
            logger.error(f"Secure deletion failed: {e}")
            raise
    
    def cleanup_sync(self) -> None:
        """Synchronous cleanup method."""
        if self.storage_path and self.storage_path.exists():
            self._secure_delete()


class AgeVerifier:
    """Face detection and age verification using DeepFace."""
    
    def __init__(self, min_age: int = 25):
        self.min_age = min_age
        self._model_loaded = False
    
    def _ensure_model_loaded(self) -> None:
        """Lazy-load DeepFace models."""
        if self._model_loaded:
            return
        
        try:
            from deepface import DeepFace
            self._deepface = DeepFace
            
            logger.info("Loading DeepFace models...")
            
            dummy_image = np.zeros((224, 224, 3), dtype=np.uint8)
            try:
                self._deepface.analyze(
                    dummy_image,
                    actions=['age'],
                    enforce_detection=False,
                    silent=True
                )
            except:
                pass
            
            self._model_loaded = True
            logger.info("DeepFace models loaded successfully")
            
        except ImportError:
            logger.error("DeepFace not installed. Install with: pip install deepface")
            raise
        except Exception as e:
            logger.warning(f"Model loading warning: {e}")
            self._model_loaded = True
    
    def verify_age(self, image: np.ndarray) -> Tuple[bool, float, str]:
        """
        Verify age from image using face analysis.
        
        Args:
            image: Input image (numpy array, BGR or RGB)
            
        Returns:
            Tuple of (is_compliant, estimated_age, message)
            
        Raises:
            AgeVerificationError: If age is below threshold
            SecurityViolationError: If verification fails critically
        """
        self._ensure_model_loaded()
        
        logger.info(f"Performing age verification (threshold: {self.min_age} years)")
        
        try:
            result = self._deepface.analyze(
                image,
                actions=['age'],
                enforce_detection=True,
                silent=True
            )
            
            if isinstance(result, list):
                if not result:
                    raise SecurityViolationError("No faces detected in image")
                result = result[0]
            
            estimated_age = result.get('age', 0)
            
            logger.info(f"Estimated age: {estimated_age:.1f} years")
            
            if estimated_age < self.min_age:
                message = (
                    f"GDPR VIOLATION: Detected age ({estimated_age:.1f}) "
                    f"below minimum threshold ({self.min_age} years)"
                )
                logger.error(message)
                raise AgeVerificationError(message)
            
            message = f"Age verification passed: {estimated_age:.1f} years (>= {self.min_age})"
            logger.info(message)
            
            return (True, estimated_age, message)
            
        except AgeVerificationError:
            raise
            
        except Exception as e:
            if "no face" in str(e).lower() or "could not find" in str(e).lower():
                raise SecurityViolationError(f"Face detection failed: {e}")
            else:
                logger.error(f"Age verification error: {e}")
                raise SecurityViolationError(f"Age verification failed: {e}")
    
    def verify_age_from_file(self, image_path: str) -> Tuple[bool, float, str]:
        """Verify age from image file."""
        import cv2
        
        if not Path(image_path).exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Could not read image: {image_path}")
        
        return self.verify_age(image)


def setup_ephemeral_storage(custom_path: Optional[str] = None) -> Path:
    """Setup ephemeral storage for GDPR-compliant processing."""
    storage = EphemeralStorage(custom_path)
    return storage.setup()


async def cleanup_storage(storage_path: Path) -> None:
    """Asynchronously cleanup ephemeral storage with secure deletion."""
    storage = EphemeralStorage()
    storage.storage_path = storage_path
    await storage.cleanup_async()


def verify_age(image: np.ndarray, min_age: int = 25) -> Tuple[bool, float, str]:
    """Verify age in image meets minimum threshold."""
    verifier = AgeVerifier(min_age=min_age)
    return verifier.verify_age(image)


# ============================================================================
# WEEK 4 - DAY 28-29: GDPR Compliance Extensions
# ============================================================================

class GDPRComplianceHandler:
    """
    Enhanced GDPR compliance handler with age verification and ephemeral storage.
    
    Features:
    - Age estimation with DeepFace/InsightFace
    - Automatic garbage collection
    - tmpfs/RAM storage preference
    - Art. 9 GDPR compliance (biometric data)
    """
    
    AGE_THRESHOLD = 25  # Safety margin for model MAE
    
    def __init__(self, age_threshold: int = 25):
        """
        Initialize GDPR compliance handler.
        
        Args:
            age_threshold: Minimum age threshold
        """
        self.AGE_THRESHOLD = age_threshold
        self.temp_storage: Optional[EphemeralStorage] = None
        self.age_verifier = AgeVerifier(min_age=age_threshold)
    
    def setup_ephemeral_storage(self, custom_path: Optional[str] = None) -> Path:
        """
        Create ephemeral storage in RAM (tmpfs on Linux, temp on Windows).
        
        Returns:
            Path to ephemeral storage directory
        """
        self.temp_storage = EphemeralStorage(custom_path=custom_path)
        storage_path = self.temp_storage.setup()
        
        logger.info(f"GDPR ephemeral storage setup: {storage_path}")
        
        return storage_path
    
    def verify_age_compliance(
        self,
        image_path: str
    ) -> Tuple[bool, float, str]:
        """
        Verify age compliance using face analysis.
        
        Args:
            image_path: Path to image file
        
        Returns:
            Tuple of (is_compliant, estimated_age, message)
        
        Raises:
            AgeVerificationError: If age below threshold
            SecurityViolationError: If verification fails critically
        """
        try:
            is_compliant, age, message = self.age_verifier.verify_age_from_file(image_path)
            
            logger.info(f"Age verification: {age:.1f} years ({'PASS' if is_compliant else 'FAIL'})")
            
            return is_compliant, age, message
        
        except (AgeVerificationError, SecurityViolationError):
            raise
        except Exception as e:
            logger.error(f"Age verification error: {e}")
            raise SecurityViolationError(f"Age verification failed: {e}")
    
    async def cleanup_ephemeral_data(self, force: bool = False):
        """
        Irreversible deletion of biometric data (GDPR Art. 9 compliance).
        
        Privacy by Design: Data is deleted immediately after processing.
        
        Args:
            force: If True, ignore errors and force cleanup
        """
        if not self.temp_storage:
            logger.warning("No ephemeral storage to cleanup")
            return
        
        try:
            await self.temp_storage.cleanup_async()
            logger.info(f"GDPR cleanup completed: {self.temp_storage.storage_path}")
            self.temp_storage = None
        
        except Exception as e:
            logger.error(f"GDPR cleanup failed: {e}")
            if not force:
                raise
            else:
                logger.warning("Forced cleanup - ignoring errors")
    
    def cleanup_ephemeral_data_sync(self, force: bool = False):
        """
        Synchronous version of cleanup_ephemeral_data.
        
        Args:
            force: If True, ignore errors and force cleanup
        """
        if not self.temp_storage:
            logger.warning("No ephemeral storage to cleanup")
            return
        
        try:
            self.temp_storage.cleanup_sync()
            logger.info(f"GDPR cleanup completed (sync): {self.temp_storage.storage_path}")
            self.temp_storage = None
        
        except Exception as e:
            logger.error(f"GDPR cleanup failed: {e}")
            if not force:
                raise
            else:
                logger.warning("Forced cleanup - ignoring errors")
    
    def get_storage_info(self) -> dict:
        """
        Get information about ephemeral storage.
        
        Returns:
            Storage information dictionary
        """
        if not self.temp_storage:
            return {"status": "not_initialized"}
        
        return {
            "status": "initialized",
            "path": str(self.temp_storage.storage_path),
            "is_tmpfs": self.temp_storage._is_tmpfs,
            "age_threshold": self.AGE_THRESHOLD
        }


# Global GDPR handler instance (Week 4 Day 28-29)
gdpr_handler = GDPRComplianceHandler()


if __name__ == "__main__":
    import sys
    import cv2
    
    if len(sys.argv) < 2:
        print("Usage: python security_module.py <image_path>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    
    async def test_security():
        print(f"\n{'='*60}")
        print("SECURITY MODULE TEST")
        print(f"{'='*60}\n")
        
        print("Test 1: Ephemeral Storage")
        print("-" * 60)
        
        storage = EphemeralStorage()
        storage_path = storage.setup()
        print(f"Created ephemeral storage: {storage_path}")
        print(f"Is tmpfs: {storage._is_tmpfs}")
        
        test_file = storage_path / "test.txt"
        test_file.write_text("Confidential data for GDPR compliance test")
        print(f"Created test file: {test_file}")
        
        await storage.cleanup_async()
        print(f"Storage cleaned up: {not storage_path.exists()}")
        print()
        
        print("Test 2: GDPR Compliance Handler")
        print("-" * 60)
        
        gdpr = GDPRComplianceHandler(age_threshold=25)
        gdpr_storage = gdpr.setup_ephemeral_storage()
        print(f"GDPR storage created: {gdpr_storage}")
        
        storage_info = gdpr.get_storage_info()
        print(f"Storage info: {storage_info}")
        
        await gdpr.cleanup_ephemeral_data(force=True)
        print("GDPR cleanup completed")
        print()
        
        print("Test 3: Age Verification")
        print("-" * 60)
        
        if not Path(image_path).exists():
            print(f"ERROR: Image file not found: {image_path}")
            return
        
        image = cv2.imread(image_path)
        if image is None:
            print(f"ERROR: Could not read image: {image_path}")
            return
        
        try:
            verifier = AgeVerifier(min_age=25)
            is_compliant, estimated_age, message = verifier.verify_age(image)
            
            print(f"Result: {'PASSED' if is_compliant else 'FAILED'}")
            print(f"Estimated age: {estimated_age:.1f} years")
            print(f"Message: {message}")
            
        except AgeVerificationError as e:
            print(f"AGE VERIFICATION FAILED: {e}")
        except SecurityViolationError as e:
            print(f"SECURITY VIOLATION: {e}")
        except Exception as e:
            print(f"ERROR: {e}")
    
    asyncio.run(test_security())
