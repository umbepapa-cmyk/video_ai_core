# ============================================================================
# PHASE 2 SPRINT 1: Celery Monitoring with Flower (Windows PowerShell)
# ============================================================================
# Starts Flower web UI for monitoring Celery workers and tasks.
#
# Usage:
#   .\monitor_celery.ps1
#
# Access Flower at: http://localhost:5555
# ============================================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting Flower - Celery Monitoring Dashboard" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check if Redis is running
Write-Host "Checking Redis connection..." -ForegroundColor Yellow

try {
    $redisCheck = redis-cli -h localhost -p 6379 ping 2>&1
    if ($redisCheck -eq "PONG") {
        Write-Host "✓ Redis is running" -ForegroundColor Green
    } else {
        throw "Redis not responding"
    }
} catch {
    Write-Host "❌ ERROR: Redis is not running!" -ForegroundColor Red
    Write-Host "Please start Redis first:" -ForegroundColor Yellow
    Write-Host "  docker-compose -f docker-compose.redis.yml up -d" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Set environment variables
$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"
if (-not $env:REDIS_URL) {
    $env:REDIS_URL = "redis://localhost:6379/0"
}

Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Redis URL: $env:REDIS_URL" -ForegroundColor White
Write-Host "  Flower Port: 5555" -ForegroundColor White
Write-Host "  Access URL: http://localhost:5555" -ForegroundColor White
Write-Host ""

Write-Host "Starting Flower..." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

celery -A celery_app flower `
    --port=5555 `
    --loglevel=info `
    --url_prefix= `
    --max_tasks=10000

# Alternative: With authentication
# Uncomment to enable basic auth (change credentials)
#
# celery -A celery_app flower `
#     --port=5555 `
#     --basic_auth=admin:secretpassword `
#     --loglevel=info
