# API Reference - Week 3 V2

## FastAPI Backend API Documentation

**Base URL:** `http://localhost:8000`  
**Version:** 0.2.0 (Week 3 V2)  
**Protocol:** HTTP/REST

---

## Table of Contents

1. [Authentication](#authentication)
2. [Video Generation](#video-generation)
3. [Job Status](#job-status)
4. [Health Check](#health-check)
5. [Error Responses](#error-responses)
6. [Rate Limiting](#rate-limiting)

---

## Authentication

Authentication is handled by **Supabase Auth** via JWT tokens.

### Flow

1. User signs up/logs in via Streamlit UI
2. Supabase returns JWT access token
3. Frontend stores token in `st.session_state`
4. Token is verified by RLS policies in database

### Headers

All authenticated requests should include:
```
Authorization: Bearer <access_token>
```

(Note: Week 3 V2 uses email-based identification; Week 4 will add full JWT header validation)

---

## Video Generation

### POST /api/v1/generate-video

Submit a video generation request and receive job ID immediately.

**Endpoint:** `POST /api/v1/generate-video`  
**Status Code:** `202 Accepted`  
**Auth:** Required (email-based)

#### Request

**Content-Type:** `multipart/form-data`

**Form Fields:**

| Field              | Type    | Required | Description                           |
|--------------------|---------|----------|---------------------------------------|
| user_email         | string  | Yes      | User email (authenticated)            |
| prompt             | string  | Yes      | Text prompt for video generation      |
| duration_seconds   | integer | No       | Video duration (3-10s, default: 5)    |
| credits_required   | integer | No       | Credits to consume (default: 10)      |
| video              | file    | No       | Optional reference video (max 50MB)   |

**Example (cURL):**

```bash
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=user@example.com" \
  -F "prompt=A cinematic sunset over mountains" \
  -F "duration_seconds=5" \
  -F "credits_required=10" \
  -F "video=@reference.mp4"
```

**Example (Python):**

```python
import requests

files = {
    'video': ('input.mp4', open('input.mp4', 'rb'), 'video/mp4')
}

data = {
    'user_email': 'user@example.com',
    'prompt': 'A cinematic sunset over mountains',
    'duration_seconds': 5,
    'credits_required': 10
}

response = requests.post(
    'http://localhost:8000/api/v1/generate-video',
    files=files,
    data=data
)

result = response.json()
job_id = result['job_id']
```

#### Response (202 Accepted)

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "accepted",
  "message": "Video generation job accepted and queued",
  "poll_url": "/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000",
  "estimated_duration_seconds": 50
}
```

**Response Fields:**

| Field                      | Type    | Description                               |
|----------------------------|---------|-------------------------------------------|
| job_id                     | string  | Unique job identifier (UUID)              |
| status                     | string  | Always "accepted" on success              |
| message                    | string  | Human-readable status message             |
| poll_url                   | string  | Endpoint to poll for job status           |
| estimated_duration_seconds | integer | Rough estimate of processing time         |

#### Error Responses

**400 Bad Request:**
```json
{
  "detail": "Video too large: 75.3MB (max: 50MB)"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Failed to accept job: <error message>"
}
```

---

## Job Status

### GET /api/v1/jobs/{job_id}

Poll job status and get real-time progress updates.

**Endpoint:** `GET /api/v1/jobs/{job_id}`  
**Status Code:** `200 OK`  
**Auth:** Required

#### Request

**URL Parameters:**

| Parameter | Type   | Description                    |
|-----------|--------|--------------------------------|
| job_id    | string | Job ID returned from POST      |

**Example (cURL):**

```bash
curl http://localhost:8000/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000
```

**Example (Python):**

```python
import requests
import time

job_id = "550e8400-e29b-41d4-a716-446655440000"

while True:
    response = requests.get(f'http://localhost:8000/api/v1/jobs/{job_id}')
    status = response.json()
    
    print(f"Status: {status['status']} ({status['progress']}%)")
    
    if status['status'] in ['completed', 'failed']:
        break
    
    time.sleep(2)  # Poll every 2 seconds

if status['status'] == 'completed':
    video_url = status['result_url']
    print(f"Video ready: {video_url}")
```

#### Response (200 OK)

**During Processing:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 65,
  "result_url": null,
  "error": null
}
```

**On Completion:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "progress": 100,
  "result_url": "https://fal.media/files/generated_video.mp4",
  "error": null
}
```

**On Failure:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "progress": 50,
  "result_url": null,
  "error": "API rate limit exceeded"
}
```

**Response Fields:**

| Field      | Type    | Description                                      |
|------------|---------|--------------------------------------------------|
| job_id     | string  | Job identifier                                   |
| status     | string  | Job state (see below)                            |
| progress   | integer | Progress percentage (0-100)                      |
| result_url | string  | Video URL when status="completed", else null     |
| error      | string  | Error message when status="failed", else null    |

**Status Values:**

| Status              | Description                              |
|---------------------|------------------------------------------|
| `pending`           | Job queued, not started yet              |
| `initializing`      | Setting up resources                     |
| `checking_credits`  | Verifying user credits                   |
| `setup_storage`     | Creating ephemeral storage               |
| `extracting_frames` | Extracting frames from reference video   |
| `verifying_age`     | Age verification (security)              |
| `generating_video`  | AI video generation in progress          |
| `processing`        | Generic processing state                 |
| `completed`         | ✅ Video ready (check result_url)        |
| `failed`            | ❌ Generation failed (check error)       |

#### Error Responses

**404 Not Found:**
```json
{
  "detail": "Job not found: 550e8400-..."
}
```

---

## Health Check

### GET /health

Check if API is running and responsive.

**Endpoint:** `GET /health`  
**Status Code:** `200 OK`  
**Auth:** None

#### Response

```json
{
  "status": "healthy",
  "timestamp": "2026-05-22T01:23:45.678901"
}
```

---

## Legacy Endpoints (V1 - Backward Compatibility)

### POST /api/generate

Original synchronous endpoint (legacy).

**Note:** Use `/api/v1/generate-video` (async) instead for better performance.

### GET /api/status/{job_id}

Original status endpoint (legacy).

**Note:** Use `/api/v1/jobs/{job_id}` instead.

---

## Error Responses

### Standard Error Format

All errors return JSON with `detail` field:

```json
{
  "detail": "Error message here"
}
```

### HTTP Status Codes

| Code | Meaning                    | Description                           |
|------|----------------------------|---------------------------------------|
| 200  | OK                         | Request successful                    |
| 202  | Accepted                   | Job accepted and queued               |
| 400  | Bad Request                | Invalid input (validation failed)     |
| 404  | Not Found                  | Resource not found (job_id invalid)   |
| 500  | Internal Server Error      | Server error (check logs)             |

### Common Errors

**Insufficient Credits:**
```json
{
  "success": false,
  "message": "Insufficient credits. Available: 5, Required: 10",
  "credits_remaining": 5
}
```

**File Too Large:**
```json
{
  "detail": "Video too large: 75.3MB (max: 50MB)"
}
```

**User Not Found:**
```json
{
  "detail": "Database error: User not found"
}
```

---

## Rate Limiting

**Week 3 V2 Status:** Not implemented yet (planned for Week 4)

**Future Rate Limits:**
- 10 requests/minute per user
- 100 requests/hour per user
- Response headers will include:
  ```
  X-RateLimit-Limit: 10
  X-RateLimit-Remaining: 7
  X-RateLimit-Reset: 1685012345
  ```

---

## Polling Best Practices

### Recommended Polling Strategy

```python
import requests
import time

def poll_job(job_id, max_wait=300, interval=2):
    """
    Poll job status until completion.
    
    Args:
        job_id: Job identifier
        max_wait: Maximum time to wait (seconds)
        interval: Polling interval (seconds)
    
    Returns:
        Final job status dict
    """
    start = time.time()
    
    while time.time() - start < max_wait:
        response = requests.get(f'http://localhost:8000/api/v1/jobs/{job_id}')
        status = response.json()
        
        # Terminal states
        if status['status'] in ['completed', 'failed']:
            return status
        
        # Wait before next poll
        time.sleep(interval)
    
    # Timeout
    raise TimeoutError(f"Job {job_id} did not complete in {max_wait}s")
```

### Adaptive Polling

For better efficiency, increase interval over time:

```python
def adaptive_poll(job_id):
    """Poll with increasing intervals."""
    intervals = [2, 2, 3, 3, 5, 5, 10]  # seconds
    
    for interval in intervals:
        response = requests.get(f'http://localhost:8000/api/v1/jobs/{job_id}')
        status = response.json()
        
        if status['status'] in ['completed', 'failed']:
            return status
        
        time.sleep(interval)
```

---

## WebSocket Support

**Week 3 V2 Status:** Not implemented (planned for Week 5)

**Future:** Real-time updates via WebSocket:
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/jobs/550e8400-...');

ws.onmessage = (event) => {
  const status = JSON.parse(event.data);
  console.log(`Progress: ${status.progress}%`);
};
```

---

## CORS Configuration

CORS is enabled for all origins in development:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Production:** Configure specific origins in `.env`

---

## Changelog

### Week 3 V2 (0.2.0)
- ✅ Added `/api/v1/generate-video` (async, 202 response)
- ✅ Added `/api/v1/jobs/{job_id}` (polling endpoint)
- ✅ Progress tracking (0-100%)
- ✅ Detailed status states

### Week 1-2 (0.1.0)
- ✅ `/api/generate` (synchronous)
- ✅ `/api/status/{job_id}` (basic status)
- ✅ `/health` endpoint

---

## Support & Resources

- **Documentation:** `README_WEEK3.md`
- **Database Schema:** `setup_database_v2.sql`
- **RLS Policies:** `setup_rls_policies.sql`
- **Source Code:** `main.py`, `app.py`

---

**Version:** Week 3 V2 (0.2.0)  
**Last Updated:** May 2026  
**License:** Academic PoC
