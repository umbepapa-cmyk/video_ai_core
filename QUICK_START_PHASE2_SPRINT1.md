# QUICK START: Phase 2 Sprint 1

Get async video generation running in **5 minutes**.

---

## 📋 Prerequisites

- ✅ Python 3.9+
- ✅ Docker & Docker Compose
- ✅ Week 1-4 dependencies installed
- ✅ Supabase configured

---

## 🚀 5-Minute Setup

### Step 1: Install Dependencies (30s)

```bash
pip install celery>=5.3.4 redis>=5.0.1 kombu>=5.3.4 flower>=2.0.1
```

### Step 2: Configure Environment (30s)

```bash
# Add to .env
echo "REDIS_URL=redis://localhost:6379/0" >> .env
echo "CELERY_BROKER_URL=redis://localhost:6379/0" >> .env
echo "CELERY_RESULT_BACKEND=redis://localhost:6379/0" >> .env
```

### Step 3: Start Redis (1 min)

```bash
docker-compose -f docker-compose.redis.yml up -d
```

**Verify:**
```bash
docker ps  # Should see redis container
```

### Step 4: Start Celery Workers (30s)

```bash
bash worker_start.sh 4
```

**Verify:**
```bash
tail -f logs/celery_worker.log  # Should see worker startup logs
```

### Step 5: Test Setup (1 min)

```bash
python test_celery_setup.py
```

**Expected output:**
```
🎉 All tests passed! Celery setup is working correctly.
```

### Step 6: Start Services (1 min)

**Terminal 1 - FastAPI:**
```bash
python main.py
```

**Terminal 2 - Streamlit:**
```bash
streamlit run app.py
```

**Terminal 3 - Flower (optional):**
```bash
bash monitor_celery.sh
```

---

## ✅ Verify Everything Works

### 1. Health Check

```bash
curl http://localhost:8000/health
```

**Expected:**
```json
{
  "status": "ok",
  "timestamp": "2026-05-22T09:30:00Z",
  "checks": {...}
}
```

### 2. Submit Test Job

Open Streamlit: http://localhost:8501

1. Login with test credentials
2. Upload reference video
3. Enter prompt: "test video"
4. Click "Generate Video"
5. Watch progress bar update every 2s

### 3. Monitor with Flower

Open Flower: http://localhost:5555

You should see:
- Active workers: 4
- Task queue: video_generation
- Real-time task monitoring

---

## 🎯 What Changed?

### Before (Synchronous)
```python
# main.py - OLD
@app.post("/api/v1/generate-video")
async def generate_video(background_tasks: BackgroundTasks, ...):
    # Process immediately in background task
    background_tasks.add_task(process_video_generation, ...)
    return {"job_id": job_id}
```

**Issues:**
- ❌ Timeout after 60s
- ❌ Lost on worker restart
- ❌ No scalability

### After (Asynchronous)
```python
# main.py - NEW
@app.post("/api/v1/generate-video", status_code=202)
async def generate_video_v2_celery(...):
    # Submit to Celery queue
    task = generate_video_task.apply_async(
        kwargs={...},
        task_id=job_id,
        queue='video_generation'
    )
    return {"job_id": task.id, "status": "accepted"}
```

**Benefits:**
- ✅ No HTTP timeouts
- ✅ Survives restarts
- ✅ Horizontal scaling
- ✅ Auto-retry on failure

---

## 📊 Architecture Overview

```
┌─────────────┐
│  Streamlit  │ (Frontend)
└──────┬──────┘
       │ HTTP POST
       ↓
┌─────────────┐
│   FastAPI   │ (API Gateway)
└──────┬──────┘
       │ Submit task
       ↓
┌─────────────┐
│    Redis    │ (Broker + Result Backend)
└──────┬──────┘
       │ Poll for tasks
       ↓
┌─────────────┐
│   Celery    │ (Worker Pool)
│   Workers   │
└──────┬──────┘
       │ Execute
       ↓
┌─────────────┐
│ Core Engine │ (AnimateDiff + ControlNet)
└─────────────┘
```

---

## 🔍 Monitoring Commands

```bash
# Check active tasks
celery -A celery_app inspect active

# Check registered tasks
celery -A celery_app inspect registered

# Check worker stats
celery -A celery_app inspect stats

# View logs
tail -f logs/celery_worker.log

# Flower dashboard
open http://localhost:5555
```

---

## 🛑 Stop Everything

```bash
# Stop workers
bash stop_workers.sh

# Stop Redis
docker-compose -f docker-compose.redis.yml down

# Stop FastAPI & Streamlit
# Ctrl+C in their terminals
```

---

## 🐛 Common Issues

### Issue: "No workers responding"

**Solution:**
```bash
# Check if workers are running
ps aux | grep celery

# If not, start them
bash worker_start.sh 4
```

### Issue: "Redis connection failed"

**Solution:**
```bash
# Check if Redis is running
docker ps | grep redis

# If not, start it
docker-compose -f docker-compose.redis.yml up -d

# Test connection
redis-cli ping  # Should return PONG
```

### Issue: "Task stuck in PENDING"

**Solution:**
```bash
# Check worker logs
tail -f logs/celery_worker.log

# Restart workers
bash stop_workers.sh
bash worker_start.sh 4
```

### Issue: "ImportError: No module named 'celery'"

**Solution:**
```bash
pip install -r requirements.txt
```

---

## 📈 Next Steps

1. **Monitor Production:**
   ```bash
   bash monitor_celery.sh
   open http://localhost:5555
   ```

2. **Scale Workers:**
   ```bash
   # Add more workers for high load
   bash worker_start.sh 8
   ```

3. **Add GPU Workers:**
   ```bash
   # GPU 0
   CUDA_VISIBLE_DEVICES=0 celery -A celery_app worker --concurrency=2 --hostname=gpu0@%h
   
   # GPU 1
   CUDA_VISIBLE_DEVICES=1 celery -A celery_app worker --concurrency=2 --hostname=gpu1@%h
   ```

4. **Configure Alerts:**
   - Monitor task failure rate in Flower
   - Set up Sentry alerts for errors
   - Configure email notifications

---

## 📚 Documentation

- **Full guide:** [README_PHASE2_SPRINT1.md](README_PHASE2_SPRINT1.md)
- **API reference:** [API_REFERENCE.md](API_REFERENCE.md)
- **Celery docs:** https://docs.celeryq.dev/

---

## ✅ Success Checklist

- [ ] Redis container running
- [ ] 4 Celery workers active
- [ ] FastAPI server running (port 8000)
- [ ] Streamlit UI accessible (port 8501)
- [ ] Flower monitoring available (port 5555)
- [ ] Test job completes successfully
- [ ] Progress updates visible in UI
- [ ] Video downloads after completion

---

## 🎉 You're Ready!

Phase 2 Sprint 1 is now **live and operational**.

**Test the full pipeline:**
1. Open Streamlit: http://localhost:8501
2. Upload a reference video
3. Enter prompt: "A person dancing in a futuristic city"
4. Click "Generate Video"
5. Watch real-time progress through stages:
   - ⏳ Pending (0%)
   - 🔄 Processing - Biometric extraction (10%)
   - 🎭 Processing - Celebrity check (20%)
   - 🔒 Processing - Identity lock (30%)
   - 🎨 Generating - Core generation (50-70%)
   - ✂️ Stitching (80%)
   - ☁️ Uploading (90%)
   - ✅ Completed (100%)

**Enjoy async video generation! 🚀**
