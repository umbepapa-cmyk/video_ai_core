"""
PHASE 2 SPRINT 1: Celery Tasks
================================
Async tasks for video generation with progress tracking.

Features:
- VideoGenerationTask base class with auto-retry
- generate_video_task with granular state updates
- Automatic credit refund on failure
- GDPR-compliant ephemeral storage cleanup
- Integration with celebrity blocker and age verification
"""

import os
import logging
import traceback
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from celery import Task
from celery_app import celery_app

# Core modules
from core_engine import generate_high_fidelity_video
from database import (
    consume_credits,
    refund_credits,
    supabase_service_role,
    InsufficientCreditsError
)
from security_module import (
    EphemeralStorage,
    AgeVerifier,
    AgeVerificationError,
    SecurityViolationError
)
from celebrity_blocker import CelebrityBlocker, CelebrityBlockingError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize global instances
age_verifier = AgeVerifier()
celebrity_blocker = CelebrityBlocker()


# ============================================================================
# Base Task Class with Auto-Retry
# ============================================================================

class VideoGenerationTask(Task):
    """
    Base task class with automatic retry and cleanup on failure.
    
    Features:
    - Auto-retry on transient failures (ConnectionError, TimeoutError)
    - Exponential backoff
    - Automatic credit refund on permanent failure
    - GDPR-compliant cleanup
    """
    
    # Retry configuration
    autoretry_for = (ConnectionError, TimeoutError)
    retry_kwargs = {'max_retries': 3, 'countdown': 60}
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max backoff
    retry_jitter = True
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Callback executed on task failure.
        Handles credit refund and cleanup.
        """
        user_id = kwargs.get('user_id')
        credits_consumed = kwargs.get('credits_consumed', 0)
        
        logger.error(f"Task {task_id} failed with exception: {exc}")
        logger.error(f"Traceback: {einfo}")
        
        # Refund credits if consumed
        if user_id and credits_consumed > 0:
            try:
                result = refund_credits(
                    user_id=user_id,
                    amount=credits_consumed,
                    job_id=task_id,
                    reason=f"Task failed: {str(exc)[:200]}"
                )
                logger.info(f"Refunded {credits_consumed} credits to user {user_id}")
            except Exception as refund_error:
                logger.error(f"Failed to refund credits: {refund_error}")
        
        # Update job history to failed state
        try:
            supabase_service_role.table('job_history').update({
                'status': 'failed',
                'error_message': str(exc)[:500],
                'completed_at': datetime.utcnow().isoformat()
            }).eq('job_id', task_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update job history: {db_error}")
        
        # Cleanup ephemeral storage (force cleanup)
        try:
            ephemeral_storage = EphemeralStorage()
            if ephemeral_storage.storage_path and ephemeral_storage.storage_path.exists():
                logger.info(f"Forcing cleanup of: {ephemeral_storage.storage_path}")
                ephemeral_storage._secure_delete()
        except Exception as cleanup_error:
            logger.error(f"Failed to cleanup ephemeral storage: {cleanup_error}")
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Callback executed on task retry."""
        retry_count = self.request.retries
        max_retries = self.max_retries
        logger.warning(f"Task {task_id} retry {retry_count}/{max_retries}: {exc}")


# ============================================================================
# Video Generation Task
# ============================================================================

@celery_app.task(
    bind=True,
    base=VideoGenerationTask,
    name='tasks.generate_video_task',
    queue='video_generation',
    track_started=True
)
def generate_video_task(
    self,
    user_id: str,
    reference_faces_dir: str,
    prompt: str,
    duration_seconds: int = 10,
    controlnet_map_path: Optional[str] = None,
    credits_consumed: int = 0
) -> Dict[str, Any]:
    """
    Celery task for asynchronous video generation.
    
    States:
    - PENDING: Task is queued
    - PROCESSING: Inference in progress (with substages)
    - STITCHING: FFmpeg crossfade application
    - UPLOADING: Uploading to storage
    - SUCCESS: Completed successfully
    - FAILURE: Failed with error
    
    Args:
        user_id: User UUID
        reference_faces_dir: Directory containing reference face images
        prompt: Text prompt for video generation
        duration_seconds: Target video duration
        controlnet_map_path: Optional ControlNet pose map
        credits_consumed: Credits consumed for this job
        
    Returns:
        Dictionary with video_url, duration, and quality metrics
    """
    
    task_id = self.request.id
    logger.info(f"Starting video generation task {task_id} for user {user_id}")
    
    try:
        # ====================================================================
        # STAGE 1: Biometric Extraction & Security Checks
        # ====================================================================
        
        self.update_state(
            state='PROCESSING',
            meta={
                'stage': 'biometric_extraction',
                'progress': 10,
                'message': 'Extracting biometric data from reference images...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        # Setup ephemeral storage
        ephemeral_storage = EphemeralStorage()
        temp_dir = ephemeral_storage.setup()
        logger.info(f"Ephemeral storage initialized: {temp_dir}")
        
        # Verify reference faces directory exists
        ref_faces_path = Path(reference_faces_dir)
        if not ref_faces_path.exists():
            raise ValueError(f"Reference faces directory not found: {reference_faces_dir}")
        
        # Get first frame for security checks
        reference_images = list(ref_faces_path.glob("*.jpg")) + list(ref_faces_path.glob("*.png"))
        if not reference_images:
            raise ValueError(f"No reference images found in {reference_faces_dir}")
        
        first_frame = str(reference_images[0])
        
        # ====================================================================
        # STAGE 2: Age Verification
        # ====================================================================
        
        self.update_state(
            state='PROCESSING',
            meta={
                'stage': 'age_verification',
                'progress': 20,
                'message': 'Verifying age compliance (25+ years)...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info("Performing age verification...")
        is_age_compliant, detected_age = age_verifier.verify_age(
            first_frame,
            min_age_threshold=25
        )
        
        if not is_age_compliant:
            error_msg = f"Age verification failed: detected age {detected_age} < 25 years"
            logger.error(error_msg)
            raise AgeVerificationError(error_msg)
        
        logger.info(f"Age verification passed: {detected_age} years")
        
        # ====================================================================
        # STAGE 3: Celebrity Blocking
        # ====================================================================
        
        self.update_state(
            state='PROCESSING',
            meta={
                'stage': 'celebrity_blocking',
                'progress': 30,
                'message': 'Checking for protected identities...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info("Checking for protected celebrities...")
        is_protected, celebrity_name, similarity_score = celebrity_blocker.check_if_protected(
            first_frame
        )
        
        if is_protected:
            error_msg = f"Protected identity detected: {celebrity_name} (similarity: {similarity_score:.2%})"
            logger.error(error_msg)
            raise CelebrityBlockingError(error_msg)
        
        logger.info("Celebrity check passed - no protected identities detected")
        
        # ====================================================================
        # STAGE 4: Identity Locking
        # ====================================================================
        
        self.update_state(
            state='PROCESSING',
            meta={
                'stage': 'identity_locking',
                'progress': 40,
                'message': 'Locking 3D identity across all angles...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info("Identity locking stage - delegated to core engine")
        
        # ====================================================================
        # STAGE 5: Core Video Generation
        # ====================================================================
        
        self.update_state(
            state='PROCESSING',
            meta={
                'stage': 'core_generation',
                'progress': 50,
                'message': f'Generating {duration_seconds}s video with AI engine...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info(f"Starting core video generation (duration: {duration_seconds}s)")
        
        # Call async core engine (run in event loop)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        generation_result = loop.run_until_complete(
            generate_high_fidelity_video(
                reference_faces_dir=reference_faces_dir,
                prompt=prompt,
                controlnet_map_path=controlnet_map_path,
                duration_seconds=duration_seconds,
                output_path=str(temp_dir)
            )
        )
        
        logger.info("Core video generation completed")
        
        # ====================================================================
        # STAGE 6: Video Stitching & Crossfade
        # ====================================================================
        
        self.update_state(
            state='STITCHING',
            meta={
                'stage': 'stitching',
                'progress': 80,
                'message': 'Applying crossfade and finalizing video...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info("Stitching stage - handled by core engine")
        
        # ====================================================================
        # STAGE 7: Upload to Storage
        # ====================================================================
        
        self.update_state(
            state='UPLOADING',
            meta={
                'stage': 'uploading',
                'progress': 90,
                'message': 'Uploading video to storage...',
                'user_id': user_id,
                'task_id': task_id
            }
        )
        
        logger.info("Uploading video to Supabase storage...")
        
        # Upload video to Supabase Storage
        video_path = generation_result.get('video_path') or generation_result.get('final_video_url')
        video_url = upload_video_to_storage(
            video_path=video_path,
            user_id=user_id,
            job_id=task_id
        )
        
        logger.info(f"Video uploaded successfully: {video_url}")
        
        # ====================================================================
        # STAGE 8: Update Database & Cleanup
        # ====================================================================
        
        # Update job history to completed
        supabase_service_role.table('job_history').update({
            'status': 'completed',
            'video_url': video_url,
            'completed_at': datetime.utcnow().isoformat(),
            'metadata': {
                'duration': generation_result.get('duration', duration_seconds),
                'identity_stability': generation_result.get('identity_stability_score', 0.99),
                'temporal_consistency': generation_result.get('temporal_consistency_score', 0.95)
            }
        }).eq('job_id', task_id).execute()
        
        logger.info(f"Job history updated for task {task_id}")
        
        # Cleanup ephemeral storage
        logger.info("Starting ephemeral storage cleanup...")
        loop.run_until_complete(ephemeral_storage.cleanup_async())
        logger.info("Ephemeral storage cleaned up successfully")
        
        # ====================================================================
        # Return Result
        # ====================================================================
        
        result = {
            'video_url': video_url,
            'duration': generation_result.get('duration', duration_seconds),
            'identity_stability': generation_result.get('identity_stability_score', 0.99),
            'temporal_consistency': generation_result.get('temporal_consistency_score', 0.95),
            'task_id': task_id,
            'user_id': user_id,
            'completed_at': datetime.utcnow().isoformat()
        }
        
        logger.info(f"Task {task_id} completed successfully")
        return result
        
    except (AgeVerificationError, CelebrityBlockingError, SecurityViolationError) as security_error:
        # Security violations should not be retried
        logger.error(f"Security violation in task {task_id}: {security_error}")
        raise security_error
        
    except Exception as e:
        logger.error(f"Task {task_id} failed with error: {e}")
        logger.error(traceback.format_exc())
        raise


# ============================================================================
# Helper Functions
# ============================================================================

def upload_video_to_storage(
    video_path: str,
    user_id: str,
    job_id: str
) -> str:
    """
    Upload video to Supabase Storage.
    
    Args:
        video_path: Local path to generated video
        user_id: User UUID
        job_id: Job UUID
        
    Returns:
        Public URL of uploaded video
    """
    bucket_name = "generated-videos"
    file_name = f"{user_id}/{job_id}.mp4"
    
    try:
        # Ensure bucket exists (create if needed)
        try:
            supabase_service_role.storage.get_bucket(bucket_name)
        except Exception:
            logger.info(f"Creating storage bucket: {bucket_name}")
            supabase_service_role.storage.create_bucket(
                bucket_name,
                options={"public": True}
            )
        
        # Upload file
        with open(video_path, 'rb') as video_file:
            supabase_service_role.storage.from_(bucket_name).upload(
                file_name,
                video_file,
                file_options={"content-type": "video/mp4", "upsert": "true"}
            )
        
        # Get public URL
        public_url = supabase_service_role.storage.from_(bucket_name).get_public_url(file_name)
        
        logger.info(f"Video uploaded to storage: {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"Failed to upload video to storage: {e}")
        raise


# ============================================================================
# Maintenance Tasks
# ============================================================================

@celery_app.task(
    bind=True,
    name='tasks.cleanup_task',
    queue='maintenance'
)
def cleanup_task(self):
    """
    Periodic cleanup task for expired jobs and temporary files.
    Runs every hour via Celery Beat.
    """
    task_id = self.request.id
    logger.info(f"Starting cleanup task {task_id}")
    
    try:
        # Cleanup expired jobs (older than 24 hours with status 'failed')
        from datetime import timedelta
        
        cutoff_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        
        result = supabase_service_role.table('job_history').delete().match({
            'status': 'failed'
        }).lt('created_at', cutoff_time).execute()
        
        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Cleaned up {deleted_count} expired failed jobs")
        
        return {
            'status': 'completed',
            'deleted_jobs': deleted_count,
            'task_id': task_id
        }
        
    except Exception as e:
        logger.error(f"Cleanup task {task_id} failed: {e}")
        raise


# ============================================================================
# Quick Test Task
# ============================================================================

@celery_app.task(
    bind=True,
    name='tasks.quick_task',
    queue='default'
)
def quick_task(self, message: str = "Hello from Celery!"):
    """
    Quick test task for verifying Celery setup.
    
    Usage:
        from tasks import quick_task
        result = quick_task.delay("Test message")
        print(result.get())
    """
    logger.info(f"Quick task executed: {message}")
    return {
        'status': 'ok',
        'message': message,
        'task_id': self.request.id
    }
