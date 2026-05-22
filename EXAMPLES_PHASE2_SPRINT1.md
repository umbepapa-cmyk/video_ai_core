# PHASE 2 SPRINT 1: Usage Examples

Practical examples for testing and using the async video generation system.

---

## 📝 TABLE OF CONTENTS

1. [Basic Job Submission](#basic-job-submission)
2. [Polling Job Status](#polling-job-status)
3. [Concurrent Jobs](#concurrent-jobs)
4. [Error Handling](#error-handling)
5. [Monitoring](#monitoring)
6. [Advanced Usage](#advanced-usage)

---

## 1. BASIC JOB SUBMISSION

### Example 1: Simple Video Generation

```bash
# Submit job
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=A person walking in a park" \
  -F "duration_seconds=5" \
  -F "quality_preset=standard" \
  -F "video=@./test_videos/reference.mp4"
```

**Response (202 Accepted):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "accepted",
  "message": "Video generation job submitted to queue",
  "poll_url": "/api/v1/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "credits_consumed": 50,
  "estimated_completion_time": "10s"
}
```

### Example 2: High-Quality Generation with ControlNet

```bash
# Submit job with ControlNet
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=premium@example.com" \
  -F "prompt=A person dancing in a futuristic city, neon lights, cinematic" \
  -F "duration_seconds=10" \
  -F "quality_preset=ultra" \
  -F "video=@./test_videos/reference.mp4" \
  -F "controlnet_map=@./test_videos/pose_map.png"
```

**Response:**
```json
{
  "job_id": "f7g8h9i0-j1k2-3456-lmno-pq7890123456",
  "status": "accepted",
  "message": "Video generation job submitted to queue",
  "poll_url": "/api/v1/jobs/f7g8h9i0-j1k2-3456-lmno-pq7890123456",
  "credits_consumed": 250,
  "estimated_completion_time": "20s"
}
```

---

## 2. POLLING JOB STATUS

### Example 3: Manual Polling

```bash
# Save job_id from submission
JOB_ID="a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# Poll status
curl http://localhost:8000/api/v1/jobs/$JOB_ID
```

**Response (Processing):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "state": "PROCESSING",
  "status": "processing",
  "stage": "identity_lock",
  "progress": 30,
  "message": "Locking 3D identity features...",
  "started_at": "2026-05-22T09:30:15Z",
  "created_at": "2026-05-22T09:30:10Z",
  "prompt": "A person walking in a park",
  "duration_seconds": 5
}
```

### Example 4: Automated Polling Script

```bash
#!/bin/bash
# poll_job.sh - Automated polling script

JOB_ID=$1

if [ -z "$JOB_ID" ]; then
    echo "Usage: ./poll_job.sh <job_id>"
    exit 1
fi

echo "Polling job: $JOB_ID"

while true; do
    # Get status
    RESPONSE=$(curl -s http://localhost:8000/api/v1/jobs/$JOB_ID)
    
    # Extract fields
    STATUS=$(echo $RESPONSE | jq -r '.status')
    PROGRESS=$(echo $RESPONSE | jq -r '.progress')
    MESSAGE=$(echo $RESPONSE | jq -r '.message')
    
    # Display
    echo "[$PROGRESS%] $STATUS: $MESSAGE"
    
    # Check terminal states
    if [ "$STATUS" == "completed" ]; then
        VIDEO_URL=$(echo $RESPONSE | jq -r '.video_url')
        echo "✅ Completed! Video: $VIDEO_URL"
        exit 0
    elif [ "$STATUS" == "failed" ]; then
        ERROR=$(echo $RESPONSE | jq -r '.error')
        echo "❌ Failed: $ERROR"
        exit 1
    fi
    
    # Wait 2 seconds
    sleep 2
done
```

**Usage:**
```bash
chmod +x poll_job.sh
./poll_job.sh a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

---

## 3. CONCURRENT JOBS

### Example 5: Submit Multiple Jobs

```bash
# Submit 5 jobs concurrently
for i in {1..5}; do
    curl -X POST http://localhost:8000/api/v1/generate-video \
      -F "user_email=test@example.com" \
      -F "prompt=Test video $i" \
      -F "duration_seconds=5" \
      -F "quality_preset=draft" \
      -F "video=@./test_videos/reference.mp4" &
done

wait
echo "All jobs submitted!"
```

### Example 6: Batch Processing Script

```bash
#!/bin/bash
# batch_generate.sh - Process multiple videos

VIDEOS_DIR="./input_videos"
OUTPUT_FILE="./job_ids.txt"

> $OUTPUT_FILE  # Clear file

for video in $VIDEOS_DIR/*.mp4; do
    echo "Processing: $video"
    
    RESPONSE=$(curl -s -X POST http://localhost:8000/api/v1/generate-video \
      -F "user_email=batch@example.com" \
      -F "prompt=Batch processed video" \
      -F "duration_seconds=5" \
      -F "quality_preset=standard" \
      -F "video=@$video")
    
    JOB_ID=$(echo $RESPONSE | jq -r '.job_id')
    echo $JOB_ID >> $OUTPUT_FILE
    
    echo "  Job ID: $JOB_ID"
done

echo "Submitted $(wc -l < $OUTPUT_FILE) jobs"
```

---

## 4. ERROR HANDLING

### Example 7: Insufficient Credits

```bash
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=broke@example.com" \
  -F "prompt=Test" \
  -F "duration_seconds=10" \
  -F "quality_preset=ultra" \
  -F "video=@./test_videos/reference.mp4"
```

**Response (402 Payment Required):**
```json
{
  "detail": "Insufficient credits. Required: 250, Available: 50"
}
```

### Example 8: Invalid Input

```bash
# Missing prompt
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=" \
  -F "duration_seconds=5" \
  -F "video=@./test_videos/reference.mp4"
```

**Response (400 Bad Request):**
```json
{
  "detail": "Prompt cannot be empty"
}
```

### Example 9: Age Verification Failed

```bash
# Video with person < 25 years old
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=Test video" \
  -F "duration_seconds=5" \
  -F "video=@./test_videos/underage.mp4"
```

**Job fails with:**
```json
{
  "job_id": "...",
  "state": "FAILURE",
  "status": "failed",
  "error": "Age verification failed: detected age 22 < 25 years"
}
```

---

## 5. MONITORING

### Example 10: Check Worker Status

```bash
# Inspect active workers
celery -A celery_app inspect active

# Output:
# -> worker@hostname: OK
#   - empty

# Inspect registered tasks
celery -A celery_app inspect registered

# Output:
# -> worker@hostname: OK
#   - tasks.generate_video_task
#   - tasks.debug_task
#   - tasks.cleanup_task
#   - tasks.health_check
```

### Example 11: Worker Statistics

```bash
celery -A celery_app inspect stats
```

**Output:**
```json
{
  "worker@hostname": {
    "total": {
      "tasks.generate_video_task": 42,
      "tasks.debug_task": 5
    },
    "pool": {
      "implementation": "prefork",
      "max-concurrency": 4,
      "processes": [1234, 1235, 1236, 1237]
    },
    "rusage": {
      "stime": 12.5,
      "utime": 456.7
    }
  }
}
```

### Example 12: Flower Dashboard

```bash
# Start Flower
bash monitor_celery.sh

# Access metrics via API
curl http://localhost:5555/api/workers

# Get task info
curl http://localhost:5555/api/task/info/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

---

## 6. ADVANCED USAGE

### Example 13: Priority Queues (Future)

```python
# High priority (premium users)
from tasks import generate_video_task

task = generate_video_task.apply_async(
    kwargs={...},
    queue='video_generation',
    priority=9  # Higher priority
)
```

### Example 14: Custom Task Routing

```python
# celery_config.py
task_routes = {
    'tasks.generate_video_task': {
        'queue': 'video_generation',
        'routing_key': 'video.generation'
    },
    'tasks.quick_task': {
        'queue': 'default',
        'routing_key': 'default'
    }
}
```

### Example 15: Task Revocation

```bash
# Revoke running task
celery -A celery_app revoke a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Revoke and terminate (if already started)
celery -A celery_app revoke a1b2c3d4-e5f6-7890-abcd-ef1234567890 --terminate
```

### Example 16: Queue Inspection

```bash
# Check queue length
redis-cli llen celery

# View pending tasks
redis-cli lrange celery 0 -1

# Purge queue (delete all tasks)
celery -A celery_app purge

# Confirmation:
# WARNING: This will remove all pending tasks. Proceed? [y/N]: y
# Purged 42 messages from 1 queue.
```

### Example 17: Result Backend Inspection

```bash
# List all task results in Redis
redis-cli keys celery-task-meta-*

# Get specific task result
redis-cli get celery-task-meta-a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Output (JSON):
{
  "status": "SUCCESS",
  "result": {
    "video_url": "https://...",
    "duration": 5.0,
    "identity_stability": 0.99
  },
  "traceback": null,
  "children": []
}
```

### Example 18: Health Check Automation

```bash
#!/bin/bash
# health_check.sh - Automated health monitoring

# Check API
API_STATUS=$(curl -s http://localhost:8000/health | jq -r '.status')

# Check Redis
REDIS_STATUS=$(redis-cli ping 2>/dev/null)

# Check Workers
WORKER_STATUS=$(celery -A celery_app inspect ping 2>/dev/null)

# Report
echo "API: $API_STATUS"
echo "Redis: $REDIS_STATUS"
echo "Workers: $(echo $WORKER_STATUS | grep -c 'OK') active"

# Alert if any service down
if [ "$API_STATUS" != "ok" ] || [ "$REDIS_STATUS" != "PONG" ]; then
    echo "⚠️ ALERT: Services down!"
    # Send notification (email, Slack, etc.)
fi
```

---

## 🧪 TESTING SCENARIOS

### Scenario 1: Stress Test

```bash
# Generate load with 100 concurrent jobs
for i in {1..100}; do
    curl -X POST http://localhost:8000/api/v1/generate-video \
      -F "user_email=loadtest$i@example.com" \
      -F "prompt=Load test video $i" \
      -F "duration_seconds=3" \
      -F "quality_preset=draft" \
      -F "video=@./test_videos/reference.mp4" &
done

wait

echo "Load test complete!"
```

### Scenario 2: Failure Recovery Test

```bash
# Submit job
JOB_ID=$(curl -s -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=Test" \
  -F "duration_seconds=5" \
  -F "video=@./test_videos/reference.mp4" \
  | jq -r '.job_id')

echo "Job ID: $JOB_ID"

# Kill worker mid-execution
sleep 2
pkill -f "celery.*worker"

# Restart worker
bash worker_start.sh 4

# Job should auto-retry
./poll_job.sh $JOB_ID
```

### Scenario 3: Memory Leak Test

```bash
# Submit 50 jobs to trigger worker restart
for i in {1..50}; do
    curl -s -X POST http://localhost:8000/api/v1/generate-video \
      -F "user_email=memtest@example.com" \
      -F "prompt=Memory test $i" \
      -F "duration_seconds=5" \
      -F "video=@./test_videos/reference.mp4"
    
    echo "Job $i submitted"
done

# Worker should restart after 10 tasks
# Check logs: tail -f logs/celery_worker.log
```

---

## 📊 MONITORING EXAMPLES

### Example 19: Real-Time Task Count

```bash
watch -n 1 'celery -A celery_app inspect active | grep -c "id"'
```

### Example 20: Queue Length Monitoring

```bash
watch -n 1 'echo "Queue: $(redis-cli llen celery) tasks pending"'
```

### Example 21: Worker Memory Usage

```bash
ps aux | grep celery | awk '{sum+=$6} END {print "Memory: " sum/1024 " MB"}'
```

---

## 🐛 DEBUGGING EXAMPLES

### Example 22: Enable Debug Logging

```bash
# Start worker with debug logging
celery -A celery_app worker --loglevel=debug --concurrency=1
```

### Example 23: Task Tracing

```python
# In tasks.py
import logging
logger = logging.getLogger(__name__)

@celery_app.task(bind=True)
def generate_video_task(self, ...):
    logger.debug(f"Task {self.request.id} started")
    logger.debug(f"Args: {self.request.args}")
    logger.debug(f"Kwargs: {self.request.kwargs}")
    
    # ... rest of task
```

### Example 24: Redis Debugging

```bash
# Monitor Redis commands in real-time
redis-cli monitor

# Output:
# OK
# 1590000000.123456 [0 127.0.0.1:12345] "LPUSH" "celery" "..."
# 1590000000.234567 [0 127.0.0.1:12345] "BRPOP" "celery" "1"
```

---

## 🎯 BEST PRACTICES

1. **Always check credits before submission**
2. **Poll with 2-second intervals** (don't spam)
3. **Handle terminal states** (completed/failed)
4. **Implement retry logic** for transient errors
5. **Monitor worker health** regularly
6. **Use job_id for support** inquiries
7. **Clean up ephemeral storage** after testing
8. **Scale workers** based on queue length

---

## 📚 ADDITIONAL RESOURCES

- **Full Documentation:** [README_PHASE2_SPRINT1.md](README_PHASE2_SPRINT1.md)
- **Quick Start:** [QUICK_START_PHASE2_SPRINT1.md](QUICK_START_PHASE2_SPRINT1.md)
- **Delivery Report:** [PHASE2_SPRINT1_DELIVERY_REPORT.md](PHASE2_SPRINT1_DELIVERY_REPORT.md)
- **Celery Docs:** https://docs.celeryq.dev/
- **Redis Docs:** https://redis.io/docs/

---

**Happy generating! 🎬**
