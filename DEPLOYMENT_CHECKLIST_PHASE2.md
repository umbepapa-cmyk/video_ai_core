# Phase 2 Sprint 1: Deployment Checklist

## 📋 Pre-Deployment Verification

### Environment Setup
- [ ] Python 3.10+ installed
- [ ] Docker & Docker Compose installed
- [ ] Redis CLI tools installed
- [ ] All dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` file configured with all required variables

### Configuration Files
- [ ] `celery_config.py` reviewed and tuned for production
- [ ] `docker-compose.redis.yml` memory limits set appropriately
- [ ] Redis persistence enabled (AOF)
- [ ] Worker concurrency set based on GPU availability

### Testing
- [ ] `python test_celery_setup.py` passes all tests
- [ ] Redis connection verified (`redis-cli ping`)
- [ ] Celery worker can start successfully
- [ ] Debug task executes successfully
- [ ] FastAPI health endpoint responds
- [ ] Streamlit UI loads without errors

---

## 🚀 Deployment Steps

### 1. Infrastructure Setup

#### Redis Deployment
```bash
# Production Redis with persistence
docker-compose -f docker-compose.redis.yml up -d

# Verify Redis health
docker ps | grep redis
docker logs appvideoai_redis
redis-cli -h localhost -p 6379 INFO stats
```

**Production Redis Config:**
```yaml
# docker-compose.redis.yml
command: redis-server 
  --appendonly yes 
  --maxmemory 4gb 
  --maxmemory-policy allkeys-lru
  --save 900 1 
  --save 300 10
  --requirepass ${REDIS_PASSWORD}  # Set in production!
```

**Verify persistence:**
```bash
docker exec appvideoai_redis ls -lh /data
# Should see appendonly.aof
```

#### Environment Variables

**Required production variables:**
```bash
# .env
REDIS_URL=redis://:${REDIS_PASSWORD}@localhost:6379/0
CELERY_BROKER_URL=${REDIS_URL}
CELERY_RESULT_BACKEND=${REDIS_URL}
CELERY_WORKER_CONCURRENCY=4
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-key
SENTRY_DSN=https://your-sentry-dsn  # For error tracking
```

### 2. Celery Workers Deployment

#### Single Server Deployment
```bash
# Start 4 workers (one per GPU if available)
celery -A celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=video_generation,default \
    --logfile=/var/log/celery/worker.log \
    --pidfile=/var/run/celery/worker.pid \
    --hostname=worker1@%h \
    --max-tasks-per-child=10 \
    --time-limit=600 \
    --soft-time-limit=540
```

#### Multi-Server Deployment
```bash
# Server 1 (high-priority video generation)
celery -A celery_app worker \
    --queues=video_generation \
    --concurrency=8 \
    --hostname=video_worker@server1

# Server 2 (background tasks)
celery -A celery_app worker \
    --queues=default,maintenance \
    --concurrency=2 \
    --hostname=default_worker@server2
```

#### Systemd Service (Production)
```bash
# /etc/systemd/system/celery-worker.service
[Unit]
Description=Celery Worker for AppVideoAI
After=network.target redis.service

[Service]
Type=forking
User=appvideoai
Group=appvideoai
WorkingDirectory=/opt/appvideoai
EnvironmentFile=/opt/appvideoai/.env
ExecStart=/opt/appvideoai/venv/bin/celery -A celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=video_generation,default \
    --logfile=/var/log/celery/worker.log \
    --pidfile=/var/run/celery/worker.pid
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable celery-worker
sudo systemctl start celery-worker
sudo systemctl status celery-worker
```

### 3. Celery Beat (Periodic Tasks)

```bash
# Start Celery Beat for scheduled tasks
celery -A celery_app beat \
    --loglevel=info \
    --logfile=/var/log/celery/beat.log \
    --pidfile=/var/run/celery/beat.pid
```

**Systemd service:**
```bash
# /etc/systemd/system/celery-beat.service
[Unit]
Description=Celery Beat Scheduler
After=network.target redis.service

[Service]
Type=forking
User=appvideoai
Group=appvideoai
WorkingDirectory=/opt/appvideoai
EnvironmentFile=/opt/appvideoai/.env
ExecStart=/opt/appvideoai/venv/bin/celery -A celery_app beat \
    --loglevel=info \
    --logfile=/var/log/celery/beat.log \
    --pidfile=/var/run/celery/beat.pid
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

### 4. FastAPI Deployment

#### Gunicorn (Production)
```bash
gunicorn main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 30 \
    --access-logfile /var/log/fastapi/access.log \
    --error-logfile /var/log/fastapi/error.log \
    --log-level info
```

**Systemd service:**
```bash
# /etc/systemd/system/fastapi.service
[Unit]
Description=FastAPI AppVideoAI
After=network.target

[Service]
Type=notify
User=appvideoai
Group=appvideoai
WorkingDirectory=/opt/appvideoai
EnvironmentFile=/opt/appvideoai/.env
ExecStart=/opt/appvideoai/venv/bin/gunicorn main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 30
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 5. Flower Monitoring (Optional but Recommended)

```bash
celery -A celery_app flower \
    --port=5555 \
    --basic_auth=admin:${FLOWER_PASSWORD} \
    --url_prefix=/flower \
    --loglevel=info
```

**Systemd service:**
```bash
# /etc/systemd/system/flower.service
[Unit]
Description=Flower Celery Monitoring
After=network.target redis.service

[Service]
Type=simple
User=appvideoai
Group=appvideoai
WorkingDirectory=/opt/appvideoai
EnvironmentFile=/opt/appvideoai/.env
ExecStart=/opt/appvideoai/venv/bin/celery -A celery_app flower \
    --port=5555 \
    --basic_auth=admin:${FLOWER_PASSWORD}
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

**Nginx reverse proxy:**
```nginx
location /flower/ {
    proxy_pass http://localhost:5555/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    auth_basic "Flower Monitoring";
    auth_basic_user_file /etc/nginx/.htpasswd;
}
```

### 6. Nginx Configuration

```nginx
# /etc/nginx/sites-available/appvideoai

upstream fastapi_backend {
    server localhost:8000;
}

server {
    listen 80;
    server_name your-domain.com;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # API endpoints
    location /api/ {
        proxy_pass http://fastapi_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Increase timeout for polling endpoints
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }

    # Health check (no auth)
    location /health {
        proxy_pass http://fastapi_backend;
        access_log off;
    }

    # Flower monitoring (authenticated)
    location /flower/ {
        proxy_pass http://localhost:5555/;
        proxy_set_header Host $host;
        auth_basic "Flower Monitoring";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
}
```

**Enable site:**
```bash
sudo ln -s /etc/nginx/sites-available/appvideoai /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 🔒 Security Checklist

### Redis Security
- [ ] Set Redis password (`requirepass` in config)
- [ ] Bind Redis to localhost only (unless using remote workers)
- [ ] Enable AOF persistence
- [ ] Set maxmemory and eviction policy
- [ ] Disable dangerous commands (`FLUSHALL`, `CONFIG`)

```bash
# redis.conf
requirepass ${REDIS_PASSWORD}
bind 127.0.0.1
maxmemory 4gb
maxmemory-policy allkeys-lru
rename-command FLUSHALL ""
rename-command CONFIG ""
```

### Celery Security
- [ ] Use strong Redis password in broker URL
- [ ] Enable task message signing (for untrusted networks)
- [ ] Disable pickle serializer (use JSON only)
- [ ] Set task time limits
- [ ] Enable task acks_late

```python
# celery_config.py (production)
task_serializer = 'json'  # Never use pickle
accept_content = ['json']
result_serializer = 'json'
task_acks_late = True
task_reject_on_worker_lost = True
```

### FastAPI Security
- [ ] Enable CORS with specific origins
- [ ] Use HTTPS in production
- [ ] Validate all user inputs
- [ ] Rate limiting implemented (Phase 2 Sprint 2)
- [ ] API key authentication for webhooks
- [ ] JWT token expiration set appropriately

### Firewall Rules
```bash
# Allow only necessary ports
sudo ufw allow 80/tcp    # HTTP (redirects to HTTPS)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw deny 6379/tcp   # Redis (localhost only)
sudo ufw deny 5555/tcp   # Flower (Nginx proxy only)
sudo ufw enable
```

---

## 📊 Monitoring & Logging

### Log Files Setup
```bash
# Create log directories
sudo mkdir -p /var/log/celery
sudo mkdir -p /var/log/fastapi
sudo chown -R appvideoai:appvideoai /var/log/celery /var/log/fastapi

# Logrotate configuration
sudo tee /etc/logrotate.d/appvideoai << EOF
/var/log/celery/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    missingok
    create 0640 appvideoai appvideoai
    sharedscripts
    postrotate
        systemctl reload celery-worker
    endscript
}

/var/log/fastapi/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    missingok
    create 0640 appvideoai appvideoai
}
EOF
```

### Monitoring Commands
```bash
# Worker health
celery -A celery_app inspect active
celery -A celery_app inspect stats

# Redis stats
redis-cli -h localhost -p 6379 INFO stats
redis-cli -h localhost -p 6379 INFO memory

# Queue length
redis-cli LLEN video_generation

# Systemd status
systemctl status celery-worker
systemctl status celery-beat
systemctl status fastapi

# Logs
journalctl -u celery-worker -f
journalctl -u fastapi -f
tail -f /var/log/celery/worker.log
```

### Prometheus Metrics (Optional)
```bash
# Install Celery exporter
pip install celery-exporter

# Start exporter
celery-exporter --broker-url=${REDIS_URL} --port=9808
```

**Prometheus scrape config:**
```yaml
scrape_configs:
  - job_name: 'celery'
    static_configs:
      - targets: ['localhost:9808']
```

---

## 🧪 Post-Deployment Testing

### 1. Health Checks
```bash
# Redis
redis-cli -h localhost -p 6379 ping

# FastAPI
curl https://your-domain.com/health

# Celery workers
celery -A celery_app inspect active
```

### 2. Functional Tests
```bash
# Submit test job
curl -X POST https://your-domain.com/api/v2/generate-video \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test-user-uuid" \
  -d '{
    "reference_faces_dir": "./test_reference_faces",
    "prompt": "Production test",
    "duration_seconds": 5
  }'

# Poll job status
curl https://your-domain.com/api/v2/jobs/{job_id} \
  -H "X-User-ID: test-user-uuid"
```

### 3. Load Testing
```bash
# Use locust for load testing
locust -f tests/locustfile.py --host=https://your-domain.com
```

### 4. Failure Recovery Tests
```bash
# Kill worker mid-task (should auto-retry)
sudo systemctl stop celery-worker
# Wait 30s
sudo systemctl start celery-worker

# Kill Redis (should reconnect)
sudo systemctl stop redis
# Wait 10s
sudo systemctl start redis
```

---

## 🚨 Alerting Setup

### Sentry Integration
Already integrated in Week 4, verify in production:
```python
# main.py
import sentry_sdk
sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"))
```

### Custom Alerts (Prometheus + Alertmanager)
```yaml
# alerts.yml
groups:
  - name: celery
    rules:
      - alert: CeleryWorkerDown
        expr: up{job="celery"} == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Celery worker is down"
          
      - alert: CeleryQueueBacklog
        expr: celery_queue_length{queue="video_generation"} > 100
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Video generation queue backlog"
```

---

## 📈 Performance Tuning

### Redis Tuning
```bash
# redis.conf
maxmemory 8gb  # Increase based on available RAM
maxmemory-policy allkeys-lru
tcp-backlog 511
timeout 300
tcp-keepalive 300
```

### Celery Tuning
```python
# celery_config.py
worker_prefetch_multiplier = 1  # For GPU tasks
worker_max_tasks_per_child = 20  # Increase if no memory leaks
worker_max_memory_per_child = 4000000  # 4GB
```

### FastAPI Tuning
```bash
# Increase workers based on CPU cores
gunicorn main:app \
    --workers $(nproc) \
    --worker-class uvicorn.workers.UvicornWorker \
    --worker-connections 1000
```

---

## 🔄 Backup & Disaster Recovery

### Redis Backups
```bash
# Manual backup
redis-cli -h localhost -p 6379 SAVE

# Automated backup (cron)
0 2 * * * redis-cli -h localhost -p 6379 BGSAVE && \
  cp /var/lib/redis/dump.rdb /backup/redis_$(date +\%Y\%m\%d).rdb
```

### Database Backups
```bash
# Supabase automatic backups (verify in Supabase dashboard)
# Daily automated backups enabled
```

### Configuration Backups
```bash
# Backup all config files
tar -czf /backup/appvideoai_config_$(date +%Y%m%d).tar.gz \
    /opt/appvideoai/.env \
    /opt/appvideoai/celery_config.py \
    /etc/nginx/sites-available/appvideoai \
    /etc/systemd/system/celery-*.service
```

---

## ✅ Final Deployment Checklist

### Pre-Launch
- [ ] All services running and healthy
- [ ] All tests passing
- [ ] Monitoring configured and tested
- [ ] Backups configured
- [ ] SSL certificates valid
- [ ] Firewall rules applied
- [ ] Log rotation configured
- [ ] Alerting tested

### Launch
- [ ] DNS records updated
- [ ] SSL certificate verified
- [ ] Health checks passing
- [ ] Monitoring dashboards accessible
- [ ] Test job submitted and completed successfully

### Post-Launch
- [ ] Monitor logs for 24 hours
- [ ] Verify no memory leaks
- [ ] Check Redis memory usage
- [ ] Verify task completion rates
- [ ] Review Sentry for errors

---

## 📞 Support & Escalation

### Troubleshooting Steps
1. Check service status: `systemctl status <service>`
2. Review logs: `journalctl -u <service> -f`
3. Check Redis: `redis-cli INFO`
4. Inspect Celery: `celery -A celery_app inspect active`
5. Review Sentry dashboard
6. Check Flower monitoring

### Escalation Contacts
- DevOps Lead: [contact info]
- Backend Team: [contact info]
- Database Admin: [contact info]

---

**Deployment Date:** _______________  
**Deployed By:** _______________  
**Sign-off:** _______________
