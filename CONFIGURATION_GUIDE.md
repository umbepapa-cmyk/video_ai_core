# Configuration Guide - Real Network Layer

## Quick Start

### 1. Set up Fal.ai API Key

Get your API key from https://fal.ai/dashboard/keys

Update `.env`:
```env
FAL_KEY=your_actual_fal_api_key_here
```

### 2. Verify FFmpeg Installation

```bash
# Check FFmpeg is installed
ffmpeg -version

# If not installed:
# Ubuntu/Debian: sudo apt install ffmpeg
# Windows: download from https://ffmpeg.org/download.html
# Mac: brew install ffmpeg
```

### 3. Test Configuration

```bash
python test_real_network_layer.py
```

---

## Environment Variables

### Required

```env
# Fal.ai API key (REQUIRED for video generation)
FAL_KEY=your_fal_api_key_here
```

### Optional

```env
# Supabase (for database integration)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here

# Security
MIN_AGE_THRESHOLD=25

# Storage
EPHEMERAL_STORAGE_PATH=/tmp/video_gen

# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false

# Processing
MAX_VIDEO_SIZE_MB=500
NUM_FRAMES_TO_EXTRACT=5
```

---

## Configuration Presets

### 1. Development Configuration

Fast iteration with lower quality:

```python
from core_engine import CoreEngineConfig, QualityPreset

config = CoreEngineConfig(
    reference_faces_dir="./test_faces",
    duration_seconds=5.0,          # Short videos
    quality_preset=QualityPreset.DRAFT,
    enable_autoregressive=False,   # Single segment
    motion_preset="smooth",
    fps=24,
    output_path="./dev_outputs/"
)
```

**Characteristics:**
- 5 second videos
- Single segment (no autoregressive)
- Faster generation (~2 minutes)
- Lower cost (~$0.18 per video)

---

### 2. Production Configuration

High quality for end users:

```python
config = CoreEngineConfig(
    reference_faces_dir="./user_faces",
    duration_seconds=10.0,         # Medium videos
    quality_preset=QualityPreset.HIGH,
    enable_autoregressive=True,    # Multiple segments
    segment_duration=5.0,
    crossfade_duration=0.5,
    motion_preset="cinematic",
    temporal_consistency=0.9,
    identity_adapter_strength=0.95,
    fps=24,
    output_path="./outputs/"
)
```

**Characteristics:**
- 10 second videos
- Autoregressive (2x 5s segments)
- High quality
- ~5 minutes generation time
- Cost: ~$0.33 per video

---

### 3. Ultra Quality Configuration

Maximum quality for premium content:

```python
config = CoreEngineConfig(
    reference_faces_dir="./premium_faces",
    duration_seconds=20.0,         # Longer videos
    quality_preset=QualityPreset.ULTRA,
    enable_autoregressive=True,
    segment_duration=5.0,
    crossfade_duration=1.0,        # Longer crossfade
    motion_preset="cinematic",
    temporal_consistency=0.95,     # Higher consistency
    identity_adapter_strength=0.98,
    flickering_suppression=0.9,
    fps=30,                        # Higher FPS
    output_path="./ultra_outputs/"
)
```

**Characteristics:**
- 20 second videos
- 4x segments with crossfades
- Ultra high quality
- ~15 minutes generation time
- Cost: ~$1.05 per video

---

### 4. Batch Processing Configuration

For processing multiple videos:

```python
import asyncio

async def batch_generate(user_requests: list):
    """Generate videos for multiple users in parallel."""
    
    tasks = []
    for user_id, prompt in user_requests:
        config = CoreEngineConfig(
            reference_faces_dir=f"./faces/{user_id}",
            duration_seconds=5.0,
            quality_preset=QualityPreset.STANDARD,
            enable_autoregressive=False,
            output_path=f"./outputs/{user_id}/"
        )
        
        engine = CoreEngine(config=config)
        
        task = engine.generate_high_fidelity_video(
            reference_faces_dir=f"./faces/{user_id}",
            prompt=prompt,
            duration_seconds=5
        )
        
        tasks.append(task)
    
    # Run all in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return results

# Usage
results = await batch_generate([
    ("user_123", "Smiling naturally"),
    ("user_456", "Talking to camera"),
    ("user_789", "Waving hello")
])
```

---

## Motion Presets

Configure motion intensity:

```python
config = CoreEngineConfig(
    motion_preset="cinematic",  # Options below
    # ...
)
```

### Available Presets

| Preset | Strength | Use Case | Example |
|--------|----------|----------|---------|
| `static` | 0.2 | Minimal motion | Portrait photos |
| `subtle` | 0.4 | Slight movement | Breathing, blinking |
| `smooth` | 0.6 | Natural motion | Talking, nodding |
| `cinematic` | 0.8 | Dramatic motion | Dancing, walking |
| `dynamic` | 1.0 | High energy | Sports, action |

---

## Timeout Configuration

Adjust timeouts based on your needs:

```python
# In core_engine.py, line ~540
handler = await fal_client.submit_async(
    "fal-ai/flux/dev",
    arguments=payload
)
result = await handler.get(timeout=120)  # Adjust this

# For video generation, line ~590
result = await handler.get(timeout=300)  # Adjust this
```

### Recommended Timeouts

| Operation | Default | Recommended Range | Notes |
|-----------|---------|-------------------|-------|
| First frame | 120s | 90-180s | Depends on resolution |
| Video 5s | 300s | 240-360s | Depends on motion |
| Video 10s | 300s | 300-480s | May need more time |
| Download | 120s | 60-180s | Depends on connection |

---

## Error Handling Configuration

### Enable Retry Logic

Add retry wrapper to API calls:

```python
from core_engine import retry_with_backoff

# Wrap API calls with retry
result = await retry_with_backoff(
    func=lambda: fal_client.submit_async(...),
    max_retries=3,
    initial_delay=1.0,
    backoff_factor=2.0,
    exceptions=(asyncio.TimeoutError, httpx.HTTPError)
)
```

### Custom Error Callbacks

```python
def on_error(error: Exception, attempt: int):
    """Called on each retry attempt."""
    logger.warning(f"Attempt {attempt} failed: {error}")
    # Send notification, update database, etc.

config.error_callback = on_error
```

---

## Progress Tracking Configuration

### Add Progress Callback

```python
def progress_callback(stage, current, total):
    """Called on each pipeline stage."""
    print(f"Progress: Stage {stage.value} ({current}/{total})")
    # Update database, websocket, etc.

engine = CoreEngine(
    config=config,
    progress_callback=progress_callback
)
```

### Integrate with WebSockets

```python
from fastapi import WebSocket

async def generate_with_websocket(
    websocket: WebSocket,
    prompt: str
):
    """Generate video with real-time progress updates."""
    
    def progress_callback(stage, current, total):
        asyncio.create_task(
            websocket.send_json({
                "type": "progress",
                "stage": stage.value,
                "current": current,
                "total": total
            })
        )
    
    engine = CoreEngine(
        config=config,
        progress_callback=progress_callback
    )
    
    result = await engine.generate_high_fidelity_video(
        reference_faces_dir="./faces",
        prompt=prompt,
        duration_seconds=5
    )
    
    await websocket.send_json({
        "type": "complete",
        "video_url": result.final_video_url
    })
```

---

## Storage Configuration

### Ephemeral Storage

Configure temporary storage location:

```python
import tempfile

config = CoreEngineConfig(
    output_path=tempfile.mkdtemp(prefix="video_gen_"),
    # ...
)

# Cleanup after use
import shutil
shutil.rmtree(config.output_path)
```

### Persistent Storage

Use permanent storage path:

```python
config = CoreEngineConfig(
    output_path="/var/video_storage/",
    # ...
)
```

### Cloud Storage Upload

After generation, upload to S3/GCS:

```python
import boto3

s3 = boto3.client('s3')

result = await engine.generate_high_fidelity_video(...)

# Upload to S3
with open(result.final_video_url, 'rb') as f:
    s3.upload_fileobj(
        f,
        'my-bucket',
        f'videos/{user_id}/video.mp4'
    )

# Delete local file
os.remove(result.final_video_url)
```

---

## Celery Integration Configuration

### Configure Celery Task

```python
# tasks.py
from celery import Celery
from core_engine import generate_high_fidelity_video

celery = Celery('tasks', broker='redis://localhost:6379/0')

@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60
)
def generate_video_task(self, user_id: str, prompt: str):
    """Generate video as Celery task."""
    
    try:
        result = asyncio.run(
            generate_high_fidelity_video(
                reference_faces_dir=f"./faces/{user_id}",
                prompt=prompt,
                duration_seconds=5,
                output_path=f"./outputs/{user_id}/"
            )
        )
        
        return {
            'status': 'success',
            'video_url': result['video_url'],
            'duration': result['duration']
        }
        
    except Exception as e:
        # Retry on failure
        self.retry(exc=e)
```

### Configure Task Routing

```python
# celeryconfig.py
task_routes = {
    'tasks.generate_video_task': {
        'queue': 'video_generation',
        'routing_key': 'video.generation'
    }
}

# Task time limits
task_time_limit = 600  # 10 minutes
task_soft_time_limit = 540  # 9 minutes warning
```

---

## Logging Configuration

### Basic Logging

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('video_generation.log'),
        logging.StreamHandler()
    ]
)
```

### Advanced Logging with JSON

```python
import json
import logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName
        })

handler = logging.FileHandler('video_gen.jsonl')
handler.setFormatter(JSONFormatter())
logging.getLogger().addHandler(handler)
```

---

## Testing Configuration

### Unit Testing

```python
# test_config.py
import pytest
from core_engine import CoreEngineConfig, QualityPreset

@pytest.fixture
def test_config():
    """Test configuration with fast settings."""
    return CoreEngineConfig(
        reference_faces_dir="./test_faces",
        duration_seconds=5.0,
        quality_preset=QualityPreset.DRAFT,
        enable_autoregressive=False,
        output_path="./test_outputs/"
    )

def test_generation(test_config):
    engine = CoreEngine(config=test_config)
    result = await engine.generate_high_fidelity_video(
        reference_faces_dir="./test_faces",
        prompt="Test prompt",
        duration_seconds=5
    )
    assert result is not None
```

---

## Security Configuration

### Rate Limiting

```python
from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()

@app.post("/generate")
@limiter.limit("5/minute")  # 5 requests per minute
async def generate_video(request: Request, prompt: str):
    result = await generate_high_fidelity_video(
        reference_faces_dir="./faces",
        prompt=prompt,
        duration_seconds=5
    )
    return result
```

### API Key Validation

```python
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_api_key(credentials = Security(security)):
    if credentials.credentials != os.getenv("API_SECRET_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials

@app.post("/generate")
async def generate_video(
    prompt: str,
    api_key: str = Depends(verify_api_key)
):
    # Generate video
    pass
```

---

## Monitoring Configuration

### Sentry Integration

```python
import sentry_sdk

sentry_sdk.init(
    dsn="your-sentry-dsn",
    traces_sample_rate=1.0,
    profiles_sample_rate=1.0,
)

# Errors are automatically captured
```

### Custom Metrics

```python
from prometheus_client import Counter, Histogram

video_generations = Counter(
    'video_generations_total',
    'Total number of video generations'
)

generation_duration = Histogram(
    'video_generation_duration_seconds',
    'Video generation duration'
)

# Use in code
video_generations.inc()
with generation_duration.time():
    result = await generate_high_fidelity_video(...)
```

---

## Performance Tuning

### Optimize for Speed

```python
config = CoreEngineConfig(
    duration_seconds=5.0,          # Shorter videos
    enable_autoregressive=False,   # Single segment
    quality_preset=QualityPreset.DRAFT,
    num_inference_steps=20,        # Fewer steps
    fps=24,                        # Standard FPS
)
```

### Optimize for Quality

```python
config = CoreEngineConfig(
    duration_seconds=10.0,
    enable_autoregressive=True,
    quality_preset=QualityPreset.ULTRA,
    num_inference_steps=30,        # More steps
    temporal_consistency=0.95,     # Higher consistency
    identity_adapter_strength=0.98,
    fps=30,                        # Higher FPS
)
```

---

## Troubleshooting

See `NETWORK_LAYER_IMPLEMENTATION.md` for detailed troubleshooting guide.

---

**Last Updated:** May 22, 2026
