# Quick Start: Phase 2 Sprint 1

## 🚀 5-Minute Setup

Get Phase 2 Sprint 1 (Celery async orchestration) running in 5 minutes.

---

## Prerequisites

- ✅ Python 3.10+ installed
- ✅ Docker & Docker Compose installed
- ✅ Phases 1-5 base completed
- ✅ Week 4 completed (database, auth, payments)

---

## Step 1: Install Dependencies (1 min)

```bash
cd c:\Users\umbep\OneDrive\Desktop\uncensored_video_app\AppVideoAI

pip install -r requirements.txt
```

New dependencies installed:
- `celery>=5.3.4`
- `redis>=5.0.1`
- `kombu>=5.3.4`
- `flower>=2.0.1`

---

## Step 2: Start Redis (30 sec)

```bash
docker-compose -f docker-compose.redis.yml up -d
```

Verify:
```bash
docker ps | grep redis
redis-cli -h localhost -p 6379 ping  # Should return PONG
```

---

## Step 3: Test Setup (1 min)

```bash
python test_celery_setup.py
```

Expected output:
```
✅ Redis connection successful!
✅ Celery app configured
✅ All required tasks registered!
```

---

## Step 4: Start Celery Worker (30 sec)

**Linux/Mac:**
```bash
bash worker_start.sh
```

**Windows:**
```powershell
.\worker_start.ps1
```

**Manual (any platform):**
```bash
celery -A celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=video_generation,default
```

Verify:
```bash
celery -A celery_app inspect active
```

---

## Step 5: Start FastAPI (30 sec)

```bash
python main.py
```

Expected output:
```
Starting AppVideoAI Server - Phase 2 Sprint 1
...
V2 (Celery):     /api/v2/generate-video
Job Status V2:   /api/v2/jobs/{job_id}
```

Test endpoint:
```bash
curl http://localhost:8000/health
```

---

## Step 6: Start Streamlit (30 sec)

```bash
streamlit run app.py
```

Access UI: http://localhost:8501

---

## Step 7: Test Video Generation (2 min)

### Via Streamlit UI:

1. Login with your credentials
2. Navigate to **"🚀 Generate Video (Celery V2)"**
3. Enter prompt: "A person walking in the park"
4. Set duration: 10 seconds
5. Click **"🚀 Generate Video (Celery)"**
6. Watch real-time progress!

### Via API:

```bash
# Submit job
curl -X POST http://localhost:8000/api/v2/generate-video \
  -H "Content-Type: application/json" \
  -H "X-User-ID: your-user-uuid" \
  -d '{
    "reference_faces_dir": "./reference_faces",
    "prompt": "A person walking in the park",
    "duration_seconds": 10
  }'

# Response:
{
  "job_id": "abc-123-def-456",
  "status": "accepted",
  "poll_url": "/api/v2/jobs/abc-123-def-456"
}

# Poll status
curl http://localhost:8000/api/v2/jobs/abc-123-def-456 \
  -H "X-User-ID: your-user-uuid"
```

---

## Step 8: Monitor with Flower (Optional)

```bash
# Linux/Mac
bash monitor_celery.sh

# Windows
.\monitor_celery.ps1
```

Access Flower: http://localhost:5555

**Flower Dashboard:**
- Real-time worker stats
- Task history
- Queue inspection
- Task retry/revoke

---

## Architecture Overview

```
[Streamlit] → POST /api/v2/generate-video → [FastAPI]
                                                ↓
                                          [Redis Queue]
                                                ↓
                                        [Celery Workers]
                                                ↓
                                        [Core Engine] → GPU APIs
                                                ↓
[Streamlit] ← GET /api/v2/jobs/{id} ← [Redis Result Backend]
```

---

## Testing Checklist

- [ ] Redis running and responding to PING
- [ ] Celery worker started (check logs)
- [ ] FastAPI /health returns 200
- [ ] Streamlit UI accessible
- [ ] Can submit test job via API
- [ ] Job progresses through stages
- [ ] Video URL returned on completion
- [ ] Credits refunded on failure (test with invalid input)

---

## Common Issues

### Issue: Redis connection refused

**Solution:**
```bash
docker-compose -f docker-compose.redis.yml up -d
redis-cli -h localhost -p 6379 ping
```

### Issue: Worker not processing tasks

**Solution:**
```bash
# Check worker is running
ps aux | grep celery

# Check logs
tail -f logs/celery_worker.log

# Restart worker
bash worker_stop.sh
bash worker_start.sh
```

### Issue: Task stuck in PENDING

**Solution:**
```bash
# Inspect workers
celery -A celery_app inspect active

# Purge stale tasks
celery -A celery_app purge
```

---

## Next Steps

### Monitor Performance:
```bash
# Worker stats
celery -A celery_app inspect stats

# Queue length
redis-cli -h localhost -p 6379 LLEN video_generation

# Active tasks
celery -A celery_app inspect active
```

### Scale Workers:
```bash
# Start 8 workers instead of 4
celery -A celery_app worker --concurrency=8

# Autoscale (2-8 workers)
celery -A celery_app worker --autoscale=8,2
```

### Enable Debug Logging:
```bash
celery -A celery_app worker --loglevel=debug
```

---

## File Structure

```
AppVideoAI/
├── celery_config.py          # Celery configuration
├── celery_app.py             # Celery app initialization
├── tasks.py                  # Async tasks (generate_video_task)
├── main.py                   # FastAPI (updated with V2 endpoints)
├── app.py                    # Streamlit (updated with Celery UI)
├── docker-compose.redis.yml  # Redis container
├── worker_start.sh           # Start Celery worker (Linux/Mac)
├── worker_start.ps1          # Start Celery worker (Windows)
├── monitor_celery.sh         # Start Flower (Linux/Mac)
├── monitor_celery.ps1        # Start Flower (Windows)
├── test_celery_setup.py      # Setup verification script
└── README_PHASE2_SPRINT1.md  # Full documentation
```

---

## Resources

- **Full Documentation:** [README_PHASE2_SPRINT1.md](README_PHASE2_SPRINT1.md)
- **Celery Docs:** https://docs.celeryproject.org
- **Flower Docs:** https://flower.readthedocs.io
- **Redis Docs:** https://redis.io/docs

---

## Support

If you encounter issues:
1. Check [README_PHASE2_SPRINT1.md](README_PHASE2_SPRINT1.md) Troubleshooting section
2. Review logs: `logs/celery_worker.log`
3. Check Flower dashboard: http://localhost:5555
4. Run diagnostics: `python test_celery_setup.py`

---

## Summary

You've successfully implemented Phase 2 Sprint 1! 🎉

**What changed:**
- ✅ HTTP decoupled from GPU inference
- ✅ 202 Accepted immediate response
- ✅ Granular progress tracking (10 stages)
- ✅ Horizontal scaling via Celery workers
- ✅ Auto-retry on transient failures
- ✅ Credit refund on permanent failures

**Benefits:**
- ❌ No more 504 timeouts
- ❌ No more FastAPI worker saturation
- ✅ Scalable architecture (add more workers)
- ✅ Better UX (progress tracking)
- ✅ Production-ready async processing

**Next:** Phase 2 Sprint 2 - Rate Limiting & Quotas
