# 🚀 Quick Start - Week 4 V2

**Guida rapida per testare e deployare le nuove funzionalità Week 4**

---

## 📋 Prerequisites

```bash
# Verifica Python version
python --version  # Should be 3.10+

# Installa dipendenze
pip install -r requirements.txt

# Verifica installazione
python -c "import insightface; import locust; import sentry_sdk; print('OK')"
```

---

## 🧪 Testing Phase

### 1. Test Payment Handler

```bash
# Test signature validation
python payment_handler.py

# Output atteso:
# ✓ PaymentHandler initialized
# ✓ Signature verification test: PASSED
```

### 2. Test Celebrity Blocker

```bash
# Crea database vuoto
python celebrity_blocker.py create

# Aggiungi identità protetta (opzionale - requires image)
# python celebrity_blocker.py add "Test Person" path/to/image.jpg celebrity

# Verifica database
python celebrity_blocker.py list

# Output atteso:
# Protected Identities: 0 (or 1 if added)
# Threshold: 0.85
```

### 3. Test GDPR Compliance

```bash
# Test security module con GDPR handler
python security_module.py path/to/test/image.jpg

# Output atteso:
# Test 1: Ephemeral Storage - PASSED
# Test 2: GDPR Compliance Handler - PASSED
# Test 3: Age Verification - PASSED (or blocked if <25)
```

### 4. Test Monitoring

```bash
# Test metrics collection
python monitoring.py

# Output atteso:
# ✓ Metrics collection (thread-safe)
# ✓ Health check system
# ✓ Alert management
```

### 5. Run Load Tests (Local)

```bash
# Start backend first
python main.py

# In another terminal:
cd tests

# Quick load test (10 users, 1 min)
locust -f load_test.py \
    --host=http://localhost:8000 \
    --users=10 \
    --spawn-rate=2 \
    --run-time=1m \
    --headless

# Full stress test suite
bash stress_test.sh
```

---

## 🗄️ Database Setup

### Apply Week 4 Extensions

```sql
-- In Supabase SQL Editor, run:
-- File: setup_database_v2.sql

-- Verify tables created:
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('payment_history', 'credit_transactions', 'payment_webhooks');

-- Verify RPC functions:
SELECT routine_name FROM information_schema.routines 
WHERE routine_schema = 'public' 
AND routine_name IN ('add_credits_secure', 'consume_credits_secure', 'refund_credits_secure');
```

### Test RPC Functions

```sql
-- Test add_credits_secure
SELECT add_credits_secure(
    'USER_UUID_HERE'::uuid,
    100,
    'TEST_TXN_123'
);

-- Expected output:
-- {"success": true, "new_balance": 100, "credits_added": 100}

-- Test consume_credits_secure
SELECT consume_credits_secure(
    'USER_UUID_HERE'::uuid,
    10,
    'JOB_UUID_HERE'::uuid
);

-- Expected output:
-- {"success": true, "old_balance": 100, "new_balance": 90, "consumed": 10}
```

---

## 🐳 Docker Testing

### Build & Test Locally

```bash
# Build image
docker build -t appvideoai:test .

# Check image size (should be ~1.5GB)
docker images appvideoai:test

# Run container
docker run -p 8000:8000 \
    -e SUPABASE_URL=$SUPABASE_URL \
    -e SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY \
    -e FAL_KEY=$FAL_KEY \
    appvideoai:test

# Test health endpoint
curl http://localhost:8000/health

# Expected output:
# {"status": "healthy", "timestamp": "...", "checks": {...}}
```

### Test Docker Compose

```bash
# Start services
docker-compose -f docker-compose.prod.yml up -d

# Check logs
docker-compose -f docker-compose.prod.yml logs -f

# Test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/metrics
curl http://localhost:8501  # Streamlit frontend

# Stop services
docker-compose -f docker-compose.prod.yml down
```

---

## 💳 Payment Webhook Testing

### Setup ngrok for Local Testing

```bash
# Install ngrok (if not installed)
# https://ngrok.com/download

# Start ngrok
ngrok http 8000

# Copy public URL (e.g., https://abc123.ngrok.io)
# Configure in payment gateway dashboard:
#   Webhook URL: https://abc123.ngrok.io/webhooks/ccbill
```

### Test Webhook with curl

```bash
# Generate test signature
SECRET="your_webhook_secret"
PAYLOAD='{"email":"test@example.com","package_id":"basic_19.99","transaction_id":"TEST123","amount":19.99}'

# Calculate signature (bash)
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)

# Send test webhook
curl -X POST http://localhost:8000/webhooks/ccbill \
    -H "Content-Type: application/json" \
    -H "X-CCBill-Signature: $SIGNATURE" \
    -d "$PAYLOAD"

# Expected response:
# {"status": "success", "result": {"credits_added": 350, ...}}
```

---

## 🎯 Integration Testing

### Full End-to-End Flow

```python
# test_e2e_week4.py

import requests
import hmac
import hashlib
import json

BASE_URL = "http://localhost:8000"

def test_payment_to_generation():
    """Test complete flow: payment → credits → video generation"""
    
    # 1. Create test user
    email = "test_week4@example.com"
    
    # 2. Simulate payment webhook
    payload = {
        "email": email,
        "package_id": "basic_19.99",
        "transaction_id": "TEST_E2E_123",
        "amount": 19.99
    }
    
    secret = "your_webhook_secret"
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    
    response = requests.post(
        f"{BASE_URL}/webhooks/ccbill",
        json=payload,
        headers={"X-CCBill-Signature": signature}
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("✓ Payment processed")
    
    # 3. Submit video generation job
    response = requests.post(
        f"{BASE_URL}/api/v1/generate-video",
        data={
            "user_email": email,
            "prompt": "A woman dancing",
            "duration_seconds": 5,
            "credits_required": 10
        }
    )
    
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    print(f"✓ Job submitted: {job_id}")
    
    # 4. Poll job status
    response = requests.get(f"{BASE_URL}/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    print(f"✓ Job status: {response.json()['status']}")
    
    print("\n✅ E2E test passed!")

if __name__ == "__main__":
    test_payment_to_generation()
```

Run:
```bash
python test_e2e_week4.py
```

---

## 📊 Performance Verification

### Quick Performance Check

```bash
# Test response times
time curl http://localhost:8000/health
# Should be < 100ms

time curl http://localhost:8000/metrics
# Should be < 200ms

# Test database query performance
time curl -X POST http://localhost:8000/api/v1/generate-video \
    -F "user_email=test@example.com" \
    -F "prompt=test" \
    -F "duration_seconds=5" \
    -F "credits_required=10"
# Should be < 500ms
```

### Load Test Results Verification

```bash
# After running stress tests, check results
cat results/stress_*_stats.csv | tail -1

# Expected metrics:
# - Average response time: < 2000ms
# - 95th percentile: < 5000ms
# - Error rate: < 1%
# - Requests/sec: > 10
```

---

## 🔍 Debugging

### Check Logs

```bash
# Backend logs
tail -f logs/app.log

# Docker logs
docker-compose logs -f backend

# Sentry (if configured)
# Visit: https://sentry.io/organizations/your-org/issues/
```

### Common Issues

#### Issue: "InsightFace models not found"
```bash
# Solution: Download models
python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l').prepare(ctx_id=-1)"
```

#### Issue: "Database connection failed"
```bash
# Solution: Check environment variables
echo $SUPABASE_URL
echo $SUPABASE_SERVICE_ROLE_KEY

# Test connection
python -c "from database import SupabaseClient; SupabaseClient(); print('OK')"
```

#### Issue: "Webhook signature invalid"
```bash
# Solution: Verify secret matches
echo $CCBILL_WEBHOOK_SECRET

# Test signature generation
python -c "import hmac, hashlib; print(hmac.new(b'secret', b'test', hashlib.sha256).hexdigest())"
```

---

## ✅ Production Deployment

### Pre-Deployment Checklist

```bash
# 1. Run full test suite
pytest
bash tests/stress_test.sh

# 2. Verify environment
cat .env | grep -v "^#" | grep "="

# 3. Check database
# - All migrations applied
# - RPC functions exist
# - Test data removed

# 4. Build Docker image
docker build -t appvideoai:production .

# 5. Test locally
docker run -p 8000:8000 appvideoai:production
```

### Deploy to Production

```bash
# Method 1: Render
export RENDER_SERVICE_ID=your_service_id
export RENDER_API_KEY=your_api_key
bash deploy.sh render production

# Method 2: Railway
railway up

# Method 3: Fly.io
flyctl deploy

# Method 4: Docker Compose (VPS)
bash deploy.sh docker-compose production
```

### Post-Deployment Verification

```bash
# 1. Health check
curl https://your-domain.com/health

# 2. Metrics check
curl https://your-domain.com/metrics

# 3. Test webhook
curl -X POST https://your-domain.com/webhooks/ccbill \
    -H "X-CCBill-Signature: test" \
    -d '{"test": true}'

# 4. Monitor Sentry
# Visit: https://sentry.io

# 5. Check production logs
# Via platform dashboard
```

---

## 📞 Support

### Get Help

- **Documentation:** README_WEEK4.md
- **Checklist:** PRODUCTION_CHECKLIST.md
- **Issues:** GitHub Issues
- **Email:** support@yourdomain.com

### Report Bugs

```bash
# Include in bug report:
# 1. Error logs
# 2. Environment details
python --version
pip list | grep -E "(fastapi|supabase|insightface|locust)"

# 3. Steps to reproduce
# 4. Expected vs actual behavior
```

---

## 🎉 Success Criteria

Week 4 is successfully deployed when:

- ✅ All tests passing (unit + load)
- ✅ Payment webhooks receiving and processing
- ✅ Celebrity blocker active and blocking
- ✅ Age verification working
- ✅ GDPR cleanup happening
- ✅ Health checks green
- ✅ Metrics endpoint responding
- ✅ Docker deployment successful
- ✅ No critical errors in Sentry

---

**Good luck with your deployment! 🚀**

If you encounter issues, check PRODUCTION_CHECKLIST.md for detailed troubleshooting steps.
