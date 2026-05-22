# PHASE 2 SPRINT 1: Command Cheat Sheet

Quick reference for common commands and operations.

---

## 🚀 STARTUP

```bash
# 1. Start Redis
docker-compose -f docker-compose.redis.yml up -d

# 2. Start Workers
bash worker_start.sh 4

# 3. Start Monitoring (optional)
bash monitor_celery.sh

# 4. Start API
python main.py

# 5. Start UI
streamlit run app.py
```

---

## 🛑 SHUTDOWN

```bash
# Graceful shutdown
bash stop_workers.sh

# Force shutdown
bash stop_workers.sh --force

# Stop Redis
docker-compose -f docker-compose.redis.yml down
```

---

## 🔍 MONITORING

```bash
# Worker status
celery -A celery_app inspect active

# Worker stats
celery -A celery_app inspect stats

# Registered tasks
celery -A celery_app inspect registered

# Queue length
redis-cli llen celery

# Flower dashboard
open http://localhost:5555
```

---

## 🧪 TESTING

```bash
# Quick test
python test_celery_setup.py

# Redis ping
redis-cli ping

# Health check
curl http://localhost:8000/health

# Worker ping
celery -A celery_app inspect ping
```

---

## 📊 DEBUGGING

```bash
# View worker logs
tail -f logs/celery_worker.log

# View Flower logs
tail -f logs/flower.log

# Redis monitor
redis-cli monitor

# Task result
redis-cli get celery-task-meta-<job_id>
```

---

## 🎬 JOB OPERATIONS

```bash
# Submit job
curl -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=Test" \
  -F "duration_seconds=5" \
  -F "video=@video.mp4"

# Check status
curl http://localhost:8000/api/v1/jobs/<job_id>

# Revoke job
celery -A celery_app revoke <job_id>

# Revoke and terminate
celery -A celery_app revoke <job_id> --terminate
```

---

## 🔧 MAINTENANCE

```bash
# Purge queue
celery -A celery_app purge

# Restart workers
bash stop_workers.sh
bash worker_start.sh 4

# Clean Redis
redis-cli flushdb

# Clear logs
rm logs/*.log
```

---

## 📈 SCALING

```bash
# Add more workers
bash worker_start.sh 8

# GPU workers
CUDA_VISIBLE_DEVICES=0 celery -A celery_app worker --hostname=gpu0@%h

# Multiple machines
celery -A celery_app worker --hostname=worker1@machine1
celery -A celery_app worker --hostname=worker2@machine2
```

---

## 🔑 REDIS COMMANDS

```bash
# Ping
redis-cli ping

# List keys
redis-cli keys "*"

# Get value
redis-cli get <key>

# Delete key
redis-cli del <key>

# Queue length
redis-cli llen celery

# View queue
redis-cli lrange celery 0 -1
```

---

## 📡 API ENDPOINTS

```bash
# Health check
GET http://localhost:8000/health

# Submit job
POST http://localhost:8000/api/v1/generate-video

# Job status
GET http://localhost:8000/api/v1/jobs/{job_id}

# Metrics
GET http://localhost:8000/metrics
```

---

## 🌐 UI URLS

| Service | URL |
|---------|-----|
| Streamlit | http://localhost:8501 |
| FastAPI | http://localhost:8000 |
| Flower | http://localhost:5555 |
| API Docs | http://localhost:8000/docs |

---

## 📁 FILE LOCATIONS

| File | Path |
|------|------|
| Worker logs | `logs/celery_worker.log` |
| Flower logs | `logs/flower.log` |
| PID file | `logs/celery_worker.pid` |
| Config | `celery_config.py` |
| Tasks | `tasks.py` |
| Environment | `.env` |

---

## 🎯 COMMON TASKS

### Check Everything is Running

```bash
# Redis
docker ps | grep redis

# Workers
ps aux | grep celery

# API
curl http://localhost:8000/health

# Result: All should return OK
```

### Submit and Monitor Job

```bash
# Submit
JOB_ID=$(curl -s -X POST http://localhost:8000/api/v1/generate-video \
  -F "user_email=test@example.com" \
  -F "prompt=Test" \
  -F "duration_seconds=5" \
  -F "video=@video.mp4" \
  | jq -r '.job_id')

# Monitor
watch -n 2 "curl -s http://localhost:8000/api/v1/jobs/$JOB_ID | jq '.status,.progress,.message'"
```

### View Real-Time Stats

```bash
# Active tasks
watch -n 1 'celery -A celery_app inspect active'

# Queue length
watch -n 1 'redis-cli llen celery'

# Worker count
watch -n 1 'celery -A celery_app inspect ping | grep -c OK'
```

---

## 🆘 EMERGENCY COMMANDS

### Workers Not Responding

```bash
bash stop_workers.sh --force
bash worker_start.sh 4
```

### Redis Full

```bash
redis-cli flushdb
```

### Queue Stuck

```bash
celery -A celery_app purge
```

### Reset Everything

```bash
# Stop all
bash stop_workers.sh --force
docker-compose -f docker-compose.redis.yml down

# Clear data
rm -rf logs/*
docker volume rm appvideoai_redis_data

# Restart
docker-compose -f docker-compose.redis.yml up -d
bash worker_start.sh 4
```

---

## 📊 PERFORMANCE MONITORING

```bash
# Worker CPU
ps aux | grep celery | awk '{print $3}' | awk '{sum+=$1} END {print sum "%"}'

# Worker memory
ps aux | grep celery | awk '{print $6}' | awk '{sum+=$1} END {print sum/1024 " MB"}'

# Tasks per minute
redis-cli llen celery
# Run again after 60s, calculate difference
```

---

## 🎯 QUICK REFERENCE

| Action | Command |
|--------|---------|
| **Start all** | `docker-compose -f docker-compose.redis.yml up -d && bash worker_start.sh 4` |
| **Stop all** | `bash stop_workers.sh && docker-compose -f docker-compose.redis.yml down` |
| **Test setup** | `python test_celery_setup.py` |
| **View logs** | `tail -f logs/celery_worker.log` |
| **Monitor** | `bash monitor_celery.sh` |
| **Health check** | `curl http://localhost:8000/health` |
| **Submit job** | See "Submit Job" section above |
| **Restart workers** | `bash stop_workers.sh && bash worker_start.sh 4` |

---

## 📚 DOCUMENTATION QUICK LINKS

| Document | Purpose |
|----------|---------|
| [README_PHASE2_SPRINT1.md](README_PHASE2_SPRINT1.md) | Full guide |
| [QUICK_START_PHASE2_SPRINT1.md](QUICK_START_PHASE2_SPRINT1.md) | 5-minute setup |
| [EXAMPLES_PHASE2_SPRINT1.md](EXAMPLES_PHASE2_SPRINT1.md) | Usage examples |
| [PHASE2_SPRINT1_DELIVERY_REPORT.md](PHASE2_SPRINT1_DELIVERY_REPORT.md) | Project report |
| [PHASE2_SPRINT1_FILES_INDEX.md](PHASE2_SPRINT1_FILES_INDEX.md) | File inventory |

---

## 💡 TIPS

- **Always start Redis before workers**
- **Use Flower for visual monitoring**
- **Check logs when things fail**
- **Purge queue if stuck**
- **Scale workers based on load**
- **Monitor memory usage**
- **Use job_id for support**

---

**PHASE 2 SPRINT 1: QUICK REFERENCE ⚡**

*Keep this handy for daily operations!*
