"""
FASE 5: Main Integration Module
=================================
FastAPI backend server integrating all PoC modules.

This module orchestrates all 5 phases:
1. Frame extraction with spatial analysis (frame_extractor.py)
2. Security verification and ephemeral storage (security_module.py)
3. Video generation via Fal.ai APIs (api_orchestrator.py)
4. Transactional credit checks (database.py)
5. Complete pipeline integration
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional
import uuid
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
import cv2
import uuid as uuid_module

# Import PoC modules
from frame_extractor import FrameExtractor
from security_module import (
    EphemeralStorage,
    AgeVerifier,
    AgeVerificationError,
    SecurityViolationError
)
from api_orchestrator import VideoGenerationClient, FFmpegProcessor
from database import (
    SupabaseClient,
    CreditManager,
    InsufficientCreditsError,
    DatabaseError
)
# Week 4 - Day 22: Payment Gateway
from payment_handler import PaymentHandler, PaymentGatewayError
# Week 4 - Day 27: Celebrity Blocker
from celebrity_blocker import CelebrityBlocker, CelebrityBlockerError
# Week 4 - Day 30: Monitoring
from monitoring import (
    metrics,
    health_checker,
    alert_manager,
    init_sentry_monitoring
)

# Week 4 imports
from payment_handler import (
    PaymentHandler,
    PaymentProvider,
    PaymentError,
    InvalidSignatureError,
    WebhookValidator
)

# Week 4 Day 27: Celebrity blocker
try:
    from celebrity_blocker import CelebrityBlocker, CelebrityBlockingError
    celebrity_blocker = CelebrityBlocker()
    logger.info("Celebrity blocker initialized")
except ImportError as e:
    logger.warning(f"Celebrity blocker not available: {e}")
    celebrity_blocker = None

# Week 4 Day 30: Monitoring
from monitoring import init_monitoring, metrics, Environment, capture_exception

# Phase 2 Sprint 1: Celery integration
from celery.result import AsyncResult
from celery_app import celery_app
from tasks import generate_video_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    # API
    fal_key: Optional[str] = None
    
    # Database
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    
    # Security
    min_age_threshold: int = 25
    ephemeral_storage_path: Optional[str] = None
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    # Processing
    max_video_size_mb: int = 500
    num_frames_to_extract: int = 5
    laplacian_variance_threshold: float = 100.0
    
    class Config:
        env_file = ".env"
        case_sensitive = False


app = FastAPI(
    title="Video Synthesis Research PoC",
    description="Academic PoC integrating spatial analysis, GDPR security, and video generation",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = Settings()

# Initialize Week 4 monitoring
init_monitoring(
    environment=Environment(os.getenv("ENVIRONMENT", "development")),
    sentry_dsn=os.getenv("SENTRY_DSN")
)
logger.info("Monitoring initialized")

# Initialize celebrity blocker (Week 4 Day 27)
celebrity_blocker = CelebrityBlocker(
    embeddings_db_path=os.getenv("CELEBRITY_DB_PATH", "celebrity_embeddings.pkl"),
    threshold=float(os.getenv("CELEBRITY_THRESHOLD", "0.85"))
)
logger.info("Celebrity blocker initialized")

# Initialize monitoring (Week 4 Day 30)
init_sentry_monitoring(
    environment=os.getenv("ENVIRONMENT", "production"),
    traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1"))
)
logger.info("Monitoring initialized")


class GenerationRequest(BaseModel):
    """Request model for video generation."""
    user_id: str = Field(..., description="User identifier")
    prompt: str = Field(..., description="Text prompt for video generation")
    credits_required: int = Field(default=10, description="Credits required")


class GenerationResponse(BaseModel):
    """Response model for video generation."""
    success: bool
    job_id: str
    message: str
    credits_remaining: Optional[int] = None
    frames_extracted: Optional[int] = None


class GenerationRequestV2(BaseModel):
    """Request model for V2 async video generation (Week 3 Day 18)."""
    user_email: str = Field(..., description="User email identifier")
    prompt: str = Field(..., description="Text prompt for video generation")
    duration_seconds: int = Field(default=5, ge=3, le=10, description="Video duration")
    credits_required: int = Field(default=10, ge=1, description="Credits required")


class JobStatusResponse(BaseModel):
    """Response model for job status."""
    job_id: str
    status: str
    progress: int
    result_url: Optional[str] = None
    error: Optional[str] = None


jobs_state = {}


@app.get("/")
async def root():
    """Root endpoint - API info."""
    return {
        "name": "Video Synthesis Research PoC",
        "version": "0.2.0 (Week 3 V2)",
        "status": "running",
        "endpoints": {
            "generate_v1": "/api/generate",
            "generate_v2_async": "/api/v1/generate-video",
            "job_status_v1": "/api/status/{job_id}",
            "job_status_v2": "/api/v1/jobs/{job_id}",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint (Week 4 Day 30 - Enhanced).
    
    Runs system health checks and returns detailed status.
    """
    health_results = health_checker.run_checks()
    
    return {
        "status": health_results["status"],
        "timestamp": health_results["timestamp"],
        "checks": health_results["checks"]
    }


@app.get("/metrics")
async def get_metrics():
    """
    Metrics endpoint (Week 4 Day 30).
    
    Returns application metrics for monitoring.
    """
    return metrics.get_metrics()


@app.post("/api/generate", response_model=GenerationResponse)
async def generate_video(
    background_tasks: BackgroundTasks,
    user_id: str = Form(...),
    prompt: str = Form(...),
    video: UploadFile = File(...),
    credits_required: int = Form(default=10)
):
    """
    Main endpoint: Generate video from uploaded video and prompt.
    
    Pipeline:
    1. Validate credits (Fase 4)
    2. Setup ephemeral storage (Fase 2)
    3. Verify age compliance (Fase 2)
    4. Extract frames with spatial analysis (Fase 1)
    5. Generate video via API (Fase 3)
    6. Cleanup (Fase 2)
    """
    job_id = str(uuid.uuid4())
    
    logger.info(f"[{job_id}] New generation request from user {user_id}")
    logger.info(f"[{job_id}] Prompt: {prompt}")
    
    jobs_state[job_id] = {
        "status": "initializing",
        "progress": 0,
        "user_id": user_id,
        "prompt": prompt
    }
    
    try:
        # FASE 4: Check and decrement credits
        logger.info(f"[{job_id}] Phase 1: Credit verification")
        jobs_state[job_id]["status"] = "checking_credits"
        jobs_state[job_id]["progress"] = 10
        
        try:
            credit_manager = CreditManager()
            success, credits_info = credit_manager.check_and_decrement(
                user_id,
                credits_required
            )
            
            if not success:
                return GenerationResponse(
                    success=False,
                    job_id=job_id,
                    message=f"Insufficient credits. Available: {credits_info.credits}, Required: {credits_required}",
                    credits_remaining=credits_info.credits
                )
            
            logger.info(f"[{job_id}] Credits decremented: {credits_info.credits} remaining")
            
        except DatabaseError as e:
            logger.error(f"[{job_id}] Database error: {e}")
            raise HTTPException(status_code=500, detail="Database error")
        
        # FASE 2: Setup ephemeral storage
        logger.info(f"[{job_id}] Phase 2: Ephemeral storage setup")
        jobs_state[job_id]["status"] = "setup_storage"
        jobs_state[job_id]["progress"] = 20
        
        storage = EphemeralStorage(custom_path=settings.ephemeral_storage_path)
        storage_path = storage.setup()
        
        # Save uploaded video
        video_path = storage_path / f"input_{job_id}.mp4"
        
        video_bytes = await video.read()
        size_mb = len(video_bytes) / (1024 * 1024)
        
        if size_mb > settings.max_video_size_mb:
            await storage.cleanup_async()
            raise HTTPException(
                status_code=400,
                detail=f"Video too large: {size_mb:.1f}MB (max: {settings.max_video_size_mb}MB)"
            )
        
        with open(video_path, "wb") as f:
            f.write(video_bytes)
        
        logger.info(f"[{job_id}] Video saved: {video_path} ({size_mb:.1f}MB)")
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_job, job_id, storage)
        
        # Start background processing
        background_tasks.add_task(
            process_video_generation,
            job_id,
            str(video_path),
            prompt,
            storage,
            settings
        )
        
        return GenerationResponse(
            success=True,
            job_id=job_id,
            message="Video generation started",
            credits_remaining=credits_info.credits
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{job_id}] Error: {e}")
        jobs_state[job_id]["status"] = "failed"
        jobs_state[job_id]["error"] = str(e)
        
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get status of video generation job (V1 - backward compatibility)."""
    
    if job_id not in jobs_state:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_state[job_id]
    
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        result_url=job.get("result_url"),
        error=job.get("error")
    )


# ============================================================
# WEEK 3 V2 - DAY 18: Async API Endpoints
# ============================================================

@app.post("/api/v1/generate-video", status_code=202)
async def generate_video_async(
    background_tasks: BackgroundTasks,
    user_email: str = Form(...),
    prompt: str = Form(...),
    duration_seconds: int = Form(default=5),
    credits_required: int = Form(default=10),
    video: Optional[UploadFile] = File(None)
):
    """
    V2 Async endpoint: Submit video generation request and get job_id immediately.
    
    Week 3 Day 18 Implementation:
    - Returns 202 Accepted immediately with job_id
    - Processing happens in background
    - Client polls /api/v1/jobs/{job_id} for status
    
    Returns:
        202 Accepted with job_id
    """
    job_id = str(uuid_module.uuid4())
    
    logger.info(f"[{job_id}] V2 async request from {user_email}")
    logger.info(f"[{job_id}] Prompt: {prompt[:100]}...")
    
    # Initialize job state
    jobs_state[job_id] = {
        "status": "pending",
        "progress": 0,
        "user_email": user_email,
        "prompt": prompt,
        "duration_seconds": duration_seconds,
        "created_at": datetime.utcnow().isoformat()
    }
    
    # For Week 3, we accept the request immediately
    # Week 4 will implement Celery for true async processing
    
    try:
        # Setup ephemeral storage
        storage = EphemeralStorage(custom_path=settings.ephemeral_storage_path)
        storage_path = storage.setup()
        
        video_path = None
        
        if video:
            # Save uploaded video
            video_path = storage_path / f"input_{job_id}.mp4"
            
            video_bytes = await video.read()
            size_mb = len(video_bytes) / (1024 * 1024)
            
            if size_mb > settings.max_video_size_mb:
                jobs_state[job_id]["status"] = "failed"
                jobs_state[job_id]["error"] = f"Video too large: {size_mb:.1f}MB"
                
                return {
                    "job_id": job_id,
                    "status": "accepted",
                    "message": "Job accepted but will fail - video too large",
                    "poll_url": f"/api/v1/jobs/{job_id}"
                }
            
            with open(video_path, "wb") as f:
                f.write(video_bytes)
            
            logger.info(f"[{job_id}] Video saved: {size_mb:.1f}MB")
        
        # Schedule background processing
        background_tasks.add_task(
            process_video_generation,
            job_id,
            str(video_path) if video_path else None,
            prompt,
            storage,
            settings
        )
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_job, job_id, storage)
        
        return {
            "job_id": job_id,
            "status": "accepted",
            "message": "Video generation job accepted and queued",
            "poll_url": f"/api/v1/jobs/{job_id}",
            "estimated_duration_seconds": duration_seconds * 10  # Rough estimate
        }
        
    except Exception as e:
        logger.error(f"[{job_id}] Error accepting job: {e}")
        jobs_state[job_id]["status"] = "failed"
        jobs_state[job_id]["error"] = str(e)
        
        return {
            "job_id": job_id,
            "status": "failed",
            "message": f"Failed to accept job: {e}"
        }


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status_v2(job_id: str):
    """
    V2 endpoint: Get detailed job status for polling.
    
    Week 3 Day 18 Implementation:
    - Returns current job state
    - Progress percentage (0-100)
    - Result URL when completed
    - Error message if failed
    
    Client should poll this endpoint every 2-5 seconds until:
    - status == "completed" (success)
    - status == "failed" (error)
    """
    if job_id not in jobs_state:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    
    job = jobs_state[job_id]
    
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        result_url=job.get("result_url"),
        error=job.get("error")
    )


async def process_video_generation(
    job_id: str,
    video_path: str,
    prompt: str,
    storage: EphemeralStorage,
    settings: Settings
):
    """Background task: Process complete video generation pipeline."""
    try:
        # FASE 1: Extract frames with spatial analysis
        logger.info(f"[{job_id}] Phase 3: Frame extraction")
        jobs_state[job_id]["status"] = "extracting_frames"
        jobs_state[job_id]["progress"] = 30
        
        extractor = FrameExtractor(
            laplacian_threshold=settings.laplacian_variance_threshold
        )
        
        frames = extractor.extract_frames(
            video_path,
            num_frames=settings.num_frames_to_extract
        )
        
        logger.info(f"[{job_id}] Extracted {len(frames)} frames with spatial metadata")
        jobs_state[job_id]["frames_extracted"] = len(frames)
        
        # FASE 2: Age verification on first frame
        logger.info(f"[{job_id}] Phase 4: Age verification")
        jobs_state[job_id]["status"] = "verifying_age"
        jobs_state[job_id]["progress"] = 50
        
        age_verifier = AgeVerifier(min_age=settings.min_age_threshold)
        
        try:
            is_compliant, estimated_age, message = age_verifier.verify_age(
                frames[0].image
            )
            logger.info(f"[{job_id}] Age verification passed: {estimated_age:.1f} years")
            
        except (AgeVerificationError, SecurityViolationError) as e:
            logger.error(f"[{job_id}] Security violation: {e}")
            jobs_state[job_id]["status"] = "failed"
            jobs_state[job_id]["error"] = f"Security violation: {e}"
            jobs_state[job_id]["progress"] = 100
            return
        
        # Save extracted frames
        frame_paths = []
        for i, frame in enumerate(frames):
            frame_path = Path(storage.storage_path) / f"frame_{job_id}_{i}.jpg"
            cv2.imwrite(str(frame_path), frame.image)
            frame_paths.append(str(frame_path))
        
        logger.info(f"[{job_id}] Saved {len(frame_paths)} frames")
        
        # FASE 3: Generate video via API
        logger.info(f"[{job_id}] Phase 5: Video generation")
        jobs_state[job_id]["status"] = "generating_video"
        jobs_state[job_id]["progress"] = 70
        
        api_client = VideoGenerationClient(api_key=settings.fal_key)
        
        # First generate image from prompt
        image_url = api_client.generate_image(prompt)
        logger.info(f"[{job_id}] Generated initial image: {image_url}")
        
        # Then generate video from image
        request_id, response = await api_client.generate_video(
            prompt=prompt,
            image_url=image_url
        )
        
        video_url = response.get("video_url", "")
        
        logger.info(f"[{job_id}] Video generated: {video_url}")
        
        # Mark as completed
        jobs_state[job_id]["status"] = "completed"
        jobs_state[job_id]["progress"] = 100
        jobs_state[job_id]["result_url"] = video_url
        
        logger.info(f"[{job_id}] Job completed successfully")
        
    except Exception as e:
        logger.error(f"[{job_id}] Processing error: {e}")
        jobs_state[job_id]["status"] = "failed"
        jobs_state[job_id]["error"] = str(e)
        jobs_state[job_id]["progress"] = 100


async def cleanup_job(job_id: str, storage: EphemeralStorage):
    """Cleanup job resources after completion."""
    await asyncio.sleep(3600)  # Wait 1 hour
    
    logger.info(f"[{job_id}] Starting cleanup")
    
    try:
        await storage.cleanup_async()
        logger.info(f"[{job_id}] Storage cleaned up")
    except Exception as e:
        logger.error(f"[{job_id}] Cleanup error: {e}")
    
    # Remove from state after 24h
    await asyncio.sleep(86400 - 3600)
    if job_id in jobs_state:
        del jobs_state[job_id]


# ============================================================================
# WEEK 3 - DAY 18: V2 ASYNC ENDPOINTS
# ============================================================================

class VideoGenerationRequestV2(BaseModel):
    """V2 Request model with enhanced fields."""
    user_email: str = Field(..., description="User email (for auth)")
    prompt: str = Field(..., description="Generation prompt")
    duration_seconds: int = Field(default=5, ge=3, le=10, description="Video duration")
    credits_required: int = Field(default=10, description="Credits to consume")
    reference_faces_dir: Optional[str] = Field(None, description="Path to reference faces")


@app.post("/api/v1/generate-video", status_code=202)
async def generate_video_v2_celery(
    user_email: str = Form(...),
    prompt: str = Form(...),
    duration_seconds: int = Form(default=5),
    quality_preset: str = Form(default="high"),
    video: Optional[UploadFile] = File(None),
    controlnet_map: Optional[UploadFile] = File(None)
):
    """
    PHASE 2 SPRINT 1: Async Video Generation with Celery.
    
    Submits video generation job to Celery queue and returns 202 Accepted.
    Client polls /api/v1/jobs/{job_id} for status.
    
    Pipeline:
    1. Validate input
    2. Calculate and consume credits
    3. Process uploaded files (video/controlnet)
    4. Submit Celery task
    5. Return job_id immediately
    
    Args:
        user_email: User email (for auth and credits)
        prompt: Text prompt for video generation
        duration_seconds: Video duration (3-10s)
        quality_preset: Quality preset (draft/standard/high/ultra)
        video: Optional reference video file
        controlnet_map: Optional ControlNet pose map
    
    Returns:
        202 Accepted with job_id and polling URL
    """
    job_id = str(uuid.uuid4())
    
    logger.info(f"[{job_id}] PHASE 2 - Celery generation request from {user_email}")
    
    # ============================================================
    # Step 1: Validation
    # ============================================================
    
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    
    if quality_preset not in ['draft', 'standard', 'high', 'ultra']:
        raise HTTPException(status_code=400, detail="Invalid quality preset")
    
    # ============================================================
    # Step 2: Calculate Credits Cost
    # ============================================================
    
    # Base cost: 10 credits per second
    base_cost = duration_seconds * 10
    
    # Quality multipliers
    quality_multipliers = {
        'draft': 0.5,
        'standard': 1.0,
        'high': 1.5,
        'ultra': 2.5
    }
    
    credits_cost = int(base_cost * quality_multipliers[quality_preset])
    
    logger.info(f"[{job_id}] Credits cost: {credits_cost} ({duration_seconds}s @ {quality_preset})")
    
    # ============================================================
    # Step 3: Consume Credits Atomically
    # ============================================================
    
    try:
        credit_manager = CreditManager()
        
        # Check and decrement credits
        success, result = credit_manager.check_and_decrement(
            user_email=user_email,
            credits=credits_cost
        )
        
        if not success:
            raise HTTPException(
                status_code=402,  # Payment Required
                detail=f"Insufficient credits. Required: {credits_cost}, Available: {result.get('available', 0)}"
            )
        
        logger.info(f"[{job_id}] Credits consumed: {credits_cost}, Remaining: {result.get('remaining', 0)}")
        
    except InsufficientCreditsError as e:
        logger.error(f"[{job_id}] Credit check failed: {e}")
        raise HTTPException(status_code=402, detail=str(e))
    
    except Exception as e:
        logger.error(f"[{job_id}] Credit system error: {e}")
        raise HTTPException(status_code=500, detail="Credit system unavailable")
    
    # ============================================================
    # Step 4: Process Uploaded Files
    # ============================================================
    
    # Setup ephemeral storage
    storage = EphemeralStorage(custom_path=settings.ephemeral_storage_path)
    storage_path = storage.setup()
    
    reference_faces_dir = None
    
    # Process reference video
    if video:
        video_path = storage_path / f"input_{job_id}.mp4"
        
        video_bytes = await video.read()
        size_mb = len(video_bytes) / (1024 * 1024)
        
        if size_mb > settings.max_video_size_mb:
            await storage.cleanup_async()
            raise HTTPException(
                status_code=400,
                detail=f"Video too large: {size_mb:.1f}MB (max: {settings.max_video_size_mb}MB)"
            )
        
        with open(video_path, "wb") as f:
            f.write(video_bytes)
        
        logger.info(f"[{job_id}] Video saved: {size_mb:.1f}MB")
        
        # Extract frames from video
        from frame_extractor import FrameExtractor
        
        frame_extractor = FrameExtractor(
            num_frames=settings.num_frames_to_extract,
            laplacian_threshold=settings.laplacian_variance_threshold
        )
        
        frames_dir = storage_path / f"frames_{job_id}"
        frames_dir.mkdir(exist_ok=True)
        
        extracted_frames = frame_extractor.extract_from_video(str(video_path), str(frames_dir))
        reference_faces_dir = str(frames_dir)
        
        logger.info(f"[{job_id}] Extracted {len(extracted_frames)} frames to {reference_faces_dir}")
    
    # Process ControlNet map
    controlnet_map_path = None
    if controlnet_map:
        controlnet_path = storage_path / f"controlnet_{job_id}.png"
        
        controlnet_bytes = await controlnet_map.read()
        with open(controlnet_path, "wb") as f:
            f.write(controlnet_bytes)
        
        controlnet_map_path = str(controlnet_path)
        logger.info(f"[{job_id}] ControlNet map saved: {controlnet_map_path}")
    
    # ============================================================
    # Step 5: Create Job Record in Database
    # ============================================================
    
    try:
        supabase = SupabaseClient()
        
        supabase.client.table('job_history').insert({
            'job_id': job_id,
            'user_id': user_email,  # TODO: Use actual user_id from auth
            'prompt': prompt,
            'duration_seconds': duration_seconds,
            'quality_preset': quality_preset,
            'credits_consumed': credits_cost,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        logger.info(f"[{job_id}] Job record created in database")
        
    except Exception as e:
        logger.error(f"[{job_id}] Failed to create job record: {e}")
        # Refund credits on failure
        try:
            credit_manager.refund_credits(user_email, credits_cost, "Job creation failed")
        except Exception as refund_error:
            logger.error(f"[{job_id}] Refund failed: {refund_error}")
        
        raise HTTPException(status_code=500, detail="Failed to create job record")
    
    # ============================================================
    # Step 6: Submit Celery Task
    # ============================================================
    
    try:
        task = generate_video_task.apply_async(
            kwargs={
                'user_id': user_email,  # TODO: Use actual user_id from auth
                'reference_faces_dir': reference_faces_dir or "./default_faces",  # TODO: Handle missing video
                'prompt': prompt,
                'duration_seconds': float(duration_seconds),
                'controlnet_map_path': controlnet_map_path,
                'credits_consumed': credits_cost,
                'quality_preset': quality_preset
            },
            task_id=job_id,
            queue='video_generation'
        )
        
        logger.info(f"[{job_id}] Celery task submitted: {task.id}")
        
    except Exception as e:
        logger.error(f"[{job_id}] Failed to submit Celery task: {e}")
        
        # Update job status
        try:
            supabase.client.table('job_history').update({
                'status': 'failed',
                'error_message': f"Task submission failed: {str(e)}"
            }).eq('job_id', job_id).execute()
        except:
            pass
        
        # Refund credits
        try:
            credit_manager.refund_credits(user_email, credits_cost, "Task submission failed")
        except Exception as refund_error:
            logger.error(f"[{job_id}] Refund failed: {refund_error}")
        
        raise HTTPException(status_code=500, detail="Failed to submit video generation task")
    
    # ============================================================
    # Step 7: Return 202 Accepted
    # ============================================================
    
    return {
        "job_id": job_id,
        "status": "accepted",
        "message": "Video generation job submitted to queue",
        "poll_url": f"/api/v1/jobs/{job_id}",
        "credits_consumed": credits_cost,
        "estimated_completion_time": f"{duration_seconds * 2}s"  # Rough estimate
    }


@app.get("/api/v1/jobs/{job_id}")
async def get_job_status_celery(job_id: str, user_email: str = Header(None, alias="X-User-Email")):
    """
    PHASE 2 SPRINT 1: Get Celery job status for polling.
    
    Returns job state from Celery backend (Redis) with detailed progress.
    
    States:
    - PENDING: Task in queue, not started yet
    - PROCESSING: Biometric extraction, identity lock, generation
    - GENERATING: Core video generation in progress
    - STITCHING: FFmpeg crossfade application
    - UPLOADING: Uploading to storage
    - SUCCESS: Completed successfully
    - FAILURE: Error occurred
    - RETRY: Automatic retry in progress
    
    Args:
        job_id: Job UUID
        user_email: User email for auth (from header)
    
    Returns:
        Job status with progress, stage, and result/error
    """
    # ============================================================
    # Step 1: Get Task Result from Celery
    # ============================================================
    
    task = AsyncResult(job_id, app=celery_app)
    
    # ============================================================
    # Step 2: Verify Ownership (check job_history)
    # ============================================================
    
    try:
        supabase = SupabaseClient()
        
        job_record = supabase.client.table('job_history') \
            .select('*') \
            .eq('job_id', job_id) \
            .single() \
            .execute()
        
        if not job_record.data:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Optional: Verify user ownership
        # if user_email and job_record.data.get('user_id') != user_email:
        #     raise HTTPException(status_code=403, detail="Access denied")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch job record: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    
    # ============================================================
    # Step 3: Build Response Based on Celery State
    # ============================================================
    
    response = {
        "job_id": job_id,
        "state": task.state,
        "created_at": job_record.data.get('created_at'),
        "prompt": job_record.data.get('prompt'),
        "duration_seconds": job_record.data.get('duration_seconds')
    }
    
    # Map Celery state to user-friendly status
    if task.state == 'PENDING':
        response['status'] = 'pending'
        response['message'] = 'Job waiting in queue...'
        response['progress'] = 0
        response['stage'] = 'queued'
    
    elif task.state == 'PROCESSING':
        # Get progress from task meta
        info = task.info or {}
        response['status'] = 'processing'
        response['stage'] = info.get('stage', 'unknown')
        response['progress'] = info.get('progress', 0)
        response['message'] = info.get('message', 'Processing...')
        response['started_at'] = info.get('started_at')
    
    elif task.state == 'GENERATING':
        info = task.info or {}
        response['status'] = 'generating'
        response['stage'] = info.get('stage', 'core_generation')
        response['progress'] = info.get('progress', 50)
        response['message'] = info.get('message', 'Generating video frames...')
    
    elif task.state == 'STITCHING':
        info = task.info or {}
        response['status'] = 'stitching'
        response['stage'] = 'stitching'
        response['progress'] = info.get('progress', 80)
        response['message'] = info.get('message', 'Applying crossfade transitions...')
    
    elif task.state == 'UPLOADING':
        info = task.info or {}
        response['status'] = 'uploading'
        response['stage'] = 'uploading'
        response['progress'] = info.get('progress', 90)
        response['message'] = info.get('message', 'Uploading to storage...')
    
    elif task.state == 'SUCCESS':
        result = task.result or {}
        response['status'] = 'completed'
        response['progress'] = 100
        response['stage'] = 'completed'
        response['message'] = 'Video generated successfully!'
        response['video_url'] = result.get('video_url')
        response['completed_at'] = result.get('completed_at')
        response['metadata'] = {
            'duration': result.get('duration'),
            'identity_stability': result.get('identity_stability'),
            'temporal_consistency': result.get('temporal_consistency')
        }
    
    elif task.state == 'FAILURE':
        error_info = str(task.info) if task.info else "Unknown error"
        response['status'] = 'failed'
        response['progress'] = 0
        response['stage'] = 'failed'
        response['error'] = error_info
        response['message'] = f'Generation failed: {error_info[:100]}'
        response['completed_at'] = datetime.utcnow().isoformat()
    
    elif task.state == 'RETRY':
        info = task.info or {}
        response['status'] = 'retrying'
        response['stage'] = 'retry'
        response['progress'] = 0
        response['message'] = f"Retrying after error: {info.get('exc', 'unknown')}"
        response['retry_count'] = info.get('retries', 0)
    
    else:
        # Unknown state
        response['status'] = 'unknown'
        response['stage'] = task.state.lower()
        response['progress'] = 0
        response['message'] = f'Task in state: {task.state}'
    
    # ============================================================
    # Step 4: Add Monitoring Metrics
    # ============================================================
    
    try:
        # Track job status in metrics
        metrics.track_job_status(job_id, response['status'])
    except Exception as e:
        logger.warning(f"Failed to track metrics: {e}")
    
    return response


# ============================================================
# WEEK 4 - DAY 22: Payment Gateway Webhook Endpoints
# ============================================================

@app.get("/health")
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.
    
    Returns:
        Health status with timestamp and version
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0-week4",
        "service": "AppVideoAI"
    }


@app.post("/webhooks/ccbill")
async def ccbill_webhook(request: Request):
    """
    CCBill webhook endpoint for payment processing.
    
    CCBill sends POST notifications when payments are completed.
    Signature verification via X-CCBill-Signature header.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = request.headers.get("X-CCBill-Signature", "")
        
        # Initialize CCBill handler
        handler = PaymentHandler(PaymentProvider.CCBILL)
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("CCBill webhook: Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Validate payload schema
        if not WebhookValidator.validate_payload_schema(data, PaymentProvider.CCBILL):
            raise HTTPException(status_code=400, detail="Invalid payload schema")
        
        # Process payment
        result = handler.process_payment_notification(data)
        
        logger.info(f"CCBill payment processed: {result['transaction_id']}")
        
        return {
            "status": "success",
            "result": result
        }
        
    except PaymentError as e:
        logger.error(f"CCBill payment processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    except Exception as e:
        logger.error(f"CCBill webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/webhooks/segpay")
async def segpay_webhook(request: Request):
    """
    Segpay webhook endpoint for payment processing.
    
    Segpay sends POST notifications when payments are completed.
    Signature verification via X-Segpay-Signature header.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = request.headers.get("X-Segpay-Signature", "")
        
        # Initialize Segpay handler
        handler = PaymentHandler(PaymentProvider.SEGPAY)
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("Segpay webhook: Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Validate payload schema
        if not WebhookValidator.validate_payload_schema(data, PaymentProvider.SEGPAY):
            raise HTTPException(status_code=400, detail="Invalid payload schema")
        
        # Process payment
        result = handler.process_payment_notification(data)
        
        logger.info(f"Segpay payment processed: {result['transaction_id']}")
        
        return {
            "status": "success",
            "result": result
        }
        
    except PaymentError as e:
        logger.error(f"Segpay payment processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    except Exception as e:
        logger.error(f"Segpay webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/webhooks/epoch")
async def epoch_webhook(request: Request):
    """
    Epoch webhook endpoint for payment processing.
    
    Epoch sends POST notifications when payments are completed.
    Signature verification via X-Epoch-Signature header.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = request.headers.get("X-Epoch-Signature", "")
        
        # Initialize Epoch handler
        handler = PaymentHandler(PaymentProvider.EPOCH)
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("Epoch webhook: Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Validate payload schema
        if not WebhookValidator.validate_payload_schema(data, PaymentProvider.EPOCH):
            raise HTTPException(status_code=400, detail="Invalid payload schema")
        
        # Process payment
        result = handler.process_payment_notification(data)
        
        logger.info(f"Epoch payment processed: {result['transaction_id']}")
        
        return {
            "status": "success",
            "result": result
        }
        
    except PaymentError as e:
        logger.error(f"Epoch payment processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    except Exception as e:
        logger.error(f"Epoch webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# WEEK 4 - DAY 22: Payment Gateway Webhooks
# ============================================================================

@app.post("/webhooks/ccbill")
async def ccbill_webhook(request: Request, x_ccbill_signature: Optional[str] = Header(None)):
    """
    CCBill webhook endpoint for payment notifications.
    
    Validates signature and processes payment to credit user account.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = x_ccbill_signature or request.headers.get("X-CCBill-Signature", "")
        
        logger.info(f"CCBill webhook received (signature: {signature[:20]}...)")
        
        # Initialize handler
        handler = PaymentHandler("ccbill")
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("Invalid CCBill webhook signature")
            handler.log_webhook_event("payment", {}, False, "Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Process payment
        try:
            result = handler.process_payment_notification(data)
            handler.log_webhook_event("payment", data, True)
            
            logger.info(f"CCBill payment processed: {result['transaction_id']}")
            
            return {
                "status": "success",
                "result": result
            }
            
        except PaymentGatewayError as e:
            logger.error(f"Payment processing failed: {e}")
            handler.log_webhook_event("payment", data, False, str(e))
            raise HTTPException(status_code=400, detail=str(e))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CCBill webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/webhooks/segpay")
async def segpay_webhook(request: Request, x_segpay_signature: Optional[str] = Header(None)):
    """
    Segpay webhook endpoint for payment notifications.
    
    Validates signature and processes payment to credit user account.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = x_segpay_signature or request.headers.get("X-Segpay-Signature", "")
        
        logger.info(f"Segpay webhook received (signature: {signature[:20]}...)")
        
        # Initialize handler
        handler = PaymentHandler("segpay")
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("Invalid Segpay webhook signature")
            handler.log_webhook_event("payment", {}, False, "Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Process payment
        try:
            result = handler.process_payment_notification(data)
            handler.log_webhook_event("payment", data, True)
            
            logger.info(f"Segpay payment processed: {result['transaction_id']}")
            
            return {
                "status": "success",
                "result": result
            }
            
        except PaymentGatewayError as e:
            logger.error(f"Payment processing failed: {e}")
            handler.log_webhook_event("payment", data, False, str(e))
            raise HTTPException(status_code=400, detail=str(e))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Segpay webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/webhooks/epoch")
async def epoch_webhook(request: Request, x_epoch_signature: Optional[str] = Header(None)):
    """
    Epoch webhook endpoint for payment notifications.
    
    Validates signature and processes payment to credit user account.
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        signature = x_epoch_signature or request.headers.get("X-Epoch-Signature", "")
        
        logger.info(f"Epoch webhook received (signature: {signature[:20]}...)")
        
        # Initialize handler
        handler = PaymentHandler("epoch")
        
        # Verify signature
        if not handler.verify_webhook_signature(body, signature):
            logger.warning("Invalid Epoch webhook signature")
            handler.log_webhook_event("payment", {}, False, "Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse JSON payload
        data = await request.json()
        
        # Process payment
        try:
            result = handler.process_payment_notification(data)
            handler.log_webhook_event("payment", data, True)
            
            logger.info(f"Epoch payment processed: {result['transaction_id']}")
            
            return {
                "status": "success",
                "result": result
            }
            
        except PaymentGatewayError as e:
            logger.error(f"Payment processing failed: {e}")
            handler.log_webhook_event("payment", data, False, str(e))
            raise HTTPException(status_code=400, detail=str(e))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Epoch webhook error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# PHASE 2 SPRINT 1: Celery-Based Async Endpoints
# ============================================================================

class VideoGenerationRequestV2(BaseModel):
    """Request model for V2 video generation (Celery-based)."""
    reference_faces_dir: str = Field(..., description="Directory containing reference face images")
    prompt: str = Field(..., description="Text prompt for video generation")
    duration_seconds: int = Field(default=10, ge=5, le=60, description="Video duration in seconds")
    controlnet_map_path: Optional[str] = Field(None, description="Optional ControlNet pose map path")


@app.post("/api/v2/generate-video", status_code=202)
async def generate_video_celery(
    request: VideoGenerationRequestV2,
    user_id: str = Header(..., alias="X-User-ID")
):
    """
    Phase 2 Sprint 1: Submit video generation job to Celery.
    
    This endpoint replaces synchronous processing with async task queue.
    Returns 202 Accepted immediately with job_id.
    Client should poll /api/v2/jobs/{job_id} for status.
    
    Architecture:
        [FastAPI] → (202 + job_id)
            ↓
        [Redis Queue]
            ↓
        [Celery Workers] → GPU APIs
    
    Args:
        request: VideoGenerationRequestV2 with generation parameters
        user_id: User UUID from X-User-ID header
        
    Returns:
        202 Accepted with job_id and status
    """
    try:
        # Calculate credits cost
        credits_cost = calculate_credits_cost(request.duration_seconds)
        
        logger.info(f"V2 Celery request from user {user_id}")
        logger.info(f"Credits required: {credits_cost} for {request.duration_seconds}s video")
        
        # Consume credits atomically
        try:
            from database import consume_credits
            job_id = str(uuid.uuid4())
            
            result = consume_credits(
                user_id=user_id,
                amount=credits_cost,
                job_id=job_id
            )
            
            logger.info(f"Credits consumed: {credits_cost} (remaining: {result['new_balance']})")
            
        except InsufficientCreditsError as e:
            logger.warning(f"Insufficient credits for user {user_id}: {e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "insufficient_credits",
                    "message": str(e),
                    "required": credits_cost
                }
            )
        
        # Create job history entry
        from database import supabase_service_role
        
        supabase_service_role.table('job_history').insert({
            'job_id': job_id,
            'user_id': user_id,
            'prompt': request.prompt,
            'duration_seconds': request.duration_seconds,
            'credits_consumed': credits_cost,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        logger.info(f"Job history created for {job_id}")
        
        # Submit task to Celery queue
        task = generate_video_task.apply_async(
            kwargs={
                'user_id': user_id,
                'reference_faces_dir': request.reference_faces_dir,
                'prompt': request.prompt,
                'duration_seconds': request.duration_seconds,
                'controlnet_map_path': request.controlnet_map_path,
                'credits_consumed': credits_cost
            },
            task_id=job_id,
            queue='video_generation'
        )
        
        logger.info(f"Task submitted to Celery: {task.id}")
        
        # Track metrics
        metrics.track_video_generation(
            user_id=user_id,
            duration_seconds=request.duration_seconds,
            credits_used=credits_cost
        )
        
        return {
            "job_id": task.id,
            "status": "accepted",
            "message": "Job submitted to queue",
            "queue": "video_generation",
            "estimated_time_seconds": request.duration_seconds * 5,  # Rough estimate
            "poll_url": f"/api/v2/jobs/{task.id}"
        }
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Failed to submit Celery task: {e}")
        logger.error(traceback.format_exc())
        
        # Capture exception in monitoring
        capture_exception(e)
        
        raise HTTPException(
            status_code=500,
            detail={
                "error": "submission_failed",
                "message": str(e)
            }
        )


@app.get("/api/v2/jobs/{job_id}")
async def get_job_status_celery(
    job_id: str,
    user_id: str = Header(..., alias="X-User-ID")
):
    """
    Phase 2 Sprint 1: Poll job status from Celery.
    
    Returns granular progress information including:
    - State: PENDING, PROCESSING, STITCHING, UPLOADING, SUCCESS, FAILURE
    - Progress: 0-100%
    - Stage: Current processing stage
    - Result: Video URL and metrics (when completed)
    
    Args:
        job_id: Job UUID
        user_id: User UUID from X-User-ID header
        
    Returns:
        Job status with progress and result
    """
    try:
        # Verify ownership via job_history
        from database import supabase_service_role
        
        job_record = supabase_service_role.table('job_history') \
            .select('*') \
            .eq('job_id', job_id) \
            .eq('user_id', user_id) \
            .single() \
            .execute()
        
        if not job_record.data:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "job_not_found",
                    "message": f"Job {job_id} not found or access denied"
                }
            )
        
        # Get task result from Celery
        task = AsyncResult(job_id, app=celery_app)
        
        # Build response based on state
        response = {
            "job_id": job_id,
            "state": task.state,
            "created_at": job_record.data['created_at'],
            "user_id": user_id
        }
        
        # State-specific information
        if task.state == 'PENDING':
            response.update({
                'message': 'Job is queued and waiting for worker...',
                'progress': 0,
                'stage': 'queued'
            })
        
        elif task.state in ['PROCESSING', 'STITCHING', 'UPLOADING']:
            info = task.info or {}
            response.update({
                'stage': info.get('stage', 'unknown'),
                'progress': info.get('progress', 0),
                'message': info.get('message', 'Processing...'),
                'task_id': info.get('task_id')
            })
        
        elif task.state == 'SUCCESS':
            result = task.result
            response.update({
                'progress': 100,
                'stage': 'completed',
                'message': 'Video generated successfully',
                'result': {
                    'video_url': result.get('video_url'),
                    'duration': result.get('duration'),
                    'identity_stability': result.get('identity_stability'),
                    'temporal_consistency': result.get('temporal_consistency'),
                    'completed_at': result.get('completed_at')
                }
            })
            
            # Update metrics
            metrics.track_video_completed(
                user_id=user_id,
                job_id=job_id,
                success=True
            )
        
        elif task.state == 'FAILURE':
            error_info = str(task.info) if task.info else 'Unknown error'
            response.update({
                'progress': 0,
                'stage': 'failed',
                'message': 'Generation failed',
                'error': error_info
            })
            
            # Update metrics
            metrics.track_video_completed(
                user_id=user_id,
                job_id=job_id,
                success=False
            )
        
        elif task.state == 'RETRY':
            response.update({
                'progress': 0,
                'stage': 'retrying',
                'message': 'Task failed, retrying...',
                'retry_count': task.info.get('retries', 0) if task.info else 0
            })
        
        else:
            # Unknown state
            response.update({
                'progress': 0,
                'stage': 'unknown',
                'message': f'Unknown state: {task.state}'
            })
        
        return response
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Failed to get job status for {job_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "status_check_failed",
                "message": str(e)
            }
        )


def calculate_credits_cost(duration_seconds: int) -> int:
    """
    Calculate credits cost based on video duration.
    
    Pricing model:
    - Base: 10 credits per second
    - Long video discount: 15% off for videos > 30s
    
    Args:
        duration_seconds: Video duration in seconds
        
    Returns:
        Credits cost
    """
    base_cost = duration_seconds * 10
    
    # Apply discount for longer videos
    if duration_seconds > 30:
        discount = 0.15
        base_cost = int(base_cost * (1 - discount))
    
    return base_cost


if __name__ == "__main__":
    import uvicorn
    import traceback
    
    logger.info("=" * 70)
    logger.info("Starting AppVideoAI Server - Phase 2 Sprint 1")
    logger.info("=" * 70)
    logger.info(f"Host: {settings.host}")
    logger.info(f"Port: {settings.port}")
    logger.info(f"Debug: {settings.debug}")
    logger.info("")
    logger.info("Endpoints:")
    logger.info("  V1 (Legacy):     /api/v1/generate-video")
    logger.info("  V2 (Celery):     /api/v2/generate-video")
    logger.info("  Job Status V2:   /api/v2/jobs/{job_id}")
    logger.info("  Health:          /health")
    logger.info("  Metrics:         /metrics")
    logger.info("  Payment Webhooks: /webhooks/ccbill, /webhooks/segpay, /webhooks/epoch")
    logger.info("")
    logger.info("Celery Queue: video_generation")
    logger.info("Redis URL: " + os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    logger.info("=" * 70)
    
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
