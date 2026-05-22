# 🚀 Production Deployment Checklist - AppVideoAI Week 4

## Pre-Deployment Verification

### Infrastructure
- [ ] Docker image builds successfully (`docker build -t appvideoai:latest .`)
- [ ] Docker Compose starts all services (`docker-compose -f docker-compose.prod.yml up`)
- [ ] Health check endpoint responds (`curl http://localhost:8000/health`)
- [ ] SSL/TLS certificates configured
- [ ] Domain DNS configured correctly
- [ ] CDN configured for static assets (if applicable)

### Database
- [ ] PostgreSQL database created on Supabase
- [ ] `setup_database_v2.sql` applied successfully
- [ ] RLS policies enabled (`setup_rls_policies.sql`)
- [ ] Service role key configured securely
- [ ] Database backups configured
- [ ] Connection pooling configured (pgbouncer)

### Environment Variables
- [ ] `SUPABASE_URL` set
- [ ] `SUPABASE_SERVICE_ROLE_KEY` set (SECRET)
- [ ] `FAL_KEY` set (SECRET)
- [ ] `CCBILL_WEBHOOK_SECRET` set (SECRET)
- [ ] `SEGPAY_WEBHOOK_SECRET` set (SECRET)
- [ ] `EPOCH_WEBHOOK_SECRET` set (SECRET)
- [ ] `SENTRY_DSN` set
- [ ] `ENVIRONMENT=production` set
- [ ] All secrets stored in secure vault (not in code)

---

## Security Checklist

### Age Verification
- [ ] DeepFace models downloaded and working
- [ ] Age threshold set to 25+ years
- [ ] Age verification tested with sample images
- [ ] Blocking works correctly for underage detection
- [ ] Audit logs enabled for age checks

### Celebrity Blocking
- [ ] InsightFace models downloaded
- [ ] `celebrity_embeddings.pkl` database created
- [ ] Similarity threshold set to 0.85
- [ ] Protected identities added to database
- [ ] Celebrity blocking tested with sample faces
- [ ] Audit logs enabled for blocked attempts

### GDPR Compliance
- [ ] Ephemeral storage using tmpfs (Linux) or temp (Windows)
- [ ] Automatic cleanup verified post-processing
- [ ] No persistent biometric data storage
- [ ] Secure deletion with overwriting implemented
- [ ] Privacy Policy published and linked
- [ ] Terms of Service published

### Authentication & Authorization
- [ ] JWT authentication working (Supabase Auth)
- [ ] RLS policies active and tested
- [ ] Service role key restricted to backend only
- [ ] CORS configured correctly
- [ ] Rate limiting enabled (if applicable)

---

## Payment Gateway Integration

### CCBill
- [ ] Merchant account approved and active
- [ ] Webhook URL configured: `https://yourdomain.com/webhooks/ccbill`
- [ ] Webhook secret key configured
- [ ] Signature verification tested
- [ ] Package IDs mapped to credit amounts
- [ ] Test payment processed successfully
- [ ] Refund/chargeback handling tested

### Segpay
- [ ] Merchant account approved and active
- [ ] Webhook URL configured: `https://yourdomain.com/webhooks/segpay`
- [ ] Webhook secret key configured
- [ ] Signature verification tested
- [ ] Test payment processed successfully

### Epoch
- [ ] Merchant account approved and active
- [ ] Webhook URL configured: `https://yourdomain.com/webhooks/epoch`
- [ ] Webhook secret key configured
- [ ] Test payment processed successfully

### Credit Transactions
- [ ] RPC functions deployed (`add_credits_secure`, `consume_credits_secure`, `refund_credits_secure`)
- [ ] FOR UPDATE locking verified (no race conditions)
- [ ] Idempotency tested (duplicate webhook protection)
- [ ] Audit trail logging verified (`credit_transactions` table)

---

## Testing

### Unit Tests
- [ ] All pytest tests passing
- [ ] Coverage > 80%
- [ ] Critical paths tested (payment, age verification, celebrity blocking)

### Integration Tests
- [ ] End-to-end video generation flow tested
- [ ] Payment → Credit → Job → Video pipeline verified
- [ ] Webhook delivery tested (use ngrok for local testing)

### Load Testing
- [ ] `tests/stress_test.sh` executed successfully
- [ ] Normal load test: 100 concurrent users ✓
- [ ] Spike test: 500 concurrent users ✓
- [ ] Endurance test: 50 users for 10 minutes ✓
- [ ] Race condition test: No database deadlocks ✓
- [ ] Failure rate < 1% ✓
- [ ] P95 response time < 5s for job submission ✓
- [ ] P95 response time < 500ms for status checks ✓

### Security Tests
- [ ] Age verification blocks underage faces
- [ ] Celebrity blocker blocks protected identities
- [ ] Invalid webhook signatures rejected (401)
- [ ] SQL injection attempts fail
- [ ] XSS attempts sanitized

---

## Monitoring & Alerting

### Sentry Configuration
- [ ] Sentry project created
- [ ] `SENTRY_DSN` configured
- [ ] Error tracking verified
- [ ] Performance monitoring enabled
- [ ] Alerts configured:
  - [ ] Error rate > 5% → Email/Slack
  - [ ] P95 response time > 10s → Email/Slack
  - [ ] Critical errors → PagerDuty/On-call

### Metrics
- [ ] Metrics collection enabled (`monitoring.py`)
- [ ] Dashboard created (Grafana/Datadog/custom)
- [ ] Key metrics tracked:
  - [ ] Jobs submitted/completed/failed
  - [ ] Credits consumed/purchased/refunded
  - [ ] Age blocks
  - [ ] Celebrity blocks
  - [ ] Request rate and latency

### Logging
- [ ] Structured logging enabled
- [ ] Log aggregation configured (Papertrail/Loggly/ELK)
- [ ] Log retention policy set
- [ ] PII/sensitive data redacted from logs

---

## Performance Optimization

### Backend
- [ ] Uvicorn workers configured (2-4 workers)
- [ ] Connection pooling enabled
- [ ] Ephemeral storage cleanup optimized
- [ ] Static file serving optimized (Nginx/CDN)

### Database
- [ ] B-Tree indices created and verified
- [ ] Query performance tested (< 100ms for most queries)
- [ ] Connection pool size configured
- [ ] Slow query logging enabled

### Caching
- [ ] API response caching (if applicable)
- [ ] Static asset caching (CDN)
- [ ] Model weight caching (DeepFace/InsightFace)

---

## Legal & Compliance

### Documentation
- [ ] Privacy Policy published
- [ ] Terms of Service published
- [ ] Cookie Policy published (if applicable)
- [ ] Age verification disclaimer visible
- [ ] GDPR compliance statement published

### Data Protection
- [ ] GDPR Article 9 compliance verified
- [ ] Data Processing Agreement (DPA) signed with vendors
- [ ] Data retention policy defined
- [ ] Right to be forgotten (RTBF) process documented
- [ ] Data breach notification procedure documented

### Content Moderation
- [ ] Celebrity blocking active
- [ ] Age verification active
- [ ] Content reporting mechanism (if applicable)
- [ ] Abuse prevention measures documented

---

## Deployment Steps

### 1. Build and Test
```bash
# Build Docker image
docker build -t appvideoai:latest .

# Run tests
pytest tests/

# Run load tests
bash tests/stress_test.sh
```

### 2. Deploy to Staging
```bash
# Deploy to staging environment
./deploy.sh docker staging

# Run smoke tests
curl https://staging.yourdomain.com/health
```

### 3. Deploy to Production
```bash
# Deploy to production
./deploy.sh render production

# Verify deployment
curl https://yourdomain.com/health

# Monitor logs
tail -f /var/log/appvideoai.log
```

### 4. Post-Deployment Verification
- [ ] Health check returns 200 OK
- [ ] Frontend loads correctly
- [ ] Test job submission works
- [ ] Payment webhook delivers successfully
- [ ] Age verification works
- [ ] Celebrity blocking works
- [ ] Metrics reporting to Sentry
- [ ] No errors in logs

---

## Rollback Plan

### If Deployment Fails
1. **Immediate Rollback:**
   ```bash
   # Rollback to previous version
   docker-compose -f docker-compose.prod.yml down
   docker pull appvideoai:previous
   docker-compose -f docker-compose.prod.yml up -d
   ```

2. **Database Rollback:**
   ```sql
   -- If database migration fails, rollback
   -- (Keep migration rollback scripts ready)
   ```

3. **Notify Team:**
   - Post incident in Slack #incidents
   - Update status page
   - Investigate root cause

---

## Performance Targets

### Latency
- Health check: < 100ms
- Job submission: < 5s (P95)
- Job status check: < 500ms (P95)
- Webhook processing: < 2s (P95)

### Throughput
- 100+ concurrent users
- 1000+ requests/minute
- 500+ video jobs/hour

### Reliability
- Uptime: 99.9% (< 43 minutes downtime/month)
- Error rate: < 1%
- Database deadlocks: 0

---

## Success Criteria

- [ ] ✅ Zero critical errors in first 24 hours
- [ ] ✅ Payment webhooks processing successfully
- [ ] ✅ No customer complaints about age verification
- [ ] ✅ No protected identities bypassed
- [ ] ✅ GDPR compliance audit passed
- [ ] ✅ Load test targets met
- [ ] ✅ Monitoring dashboards showing green
- [ ] ✅ Team trained on production operations

---

## Post-Launch

### Week 1
- [ ] Monitor error rates daily
- [ ] Review Sentry issues
- [ ] Check payment reconciliation
- [ ] Verify credit transaction integrity
- [ ] Review customer feedback

### Week 2-4
- [ ] Optimize slow queries
- [ ] Tune celebrity blocker threshold if needed
- [ ] Add more protected identities to database
- [ ] Scale infrastructure if needed
- [ ] Plan for next features

---

**Deployment Date:** _________________

**Deployed By:** _________________

**Sign-off:**
- [ ] Tech Lead
- [ ] DevOps Lead
- [ ] Legal/Compliance
- [ ] Product Owner

**Notes:**
_____________________________________________________________________________
_____________________________________________________________________________
_____________________________________________________________________________
