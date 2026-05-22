# ============================================================================
# PHASE 2 SPRINT 1: Celery Worker Startup Script (Windows PowerShell)
# ============================================================================
# Starts Celery worker with optimal configuration for video generation tasks.
#
# Usage:
#   .\worker_start.ps1
#
# Features:
# - 4 concurrent workers (adjust based on GPU availability)
# - video_generation and default queues
# - Autoscaling support
# - Comprehensive logging
# ============================================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting Celery Worker for AppVideoAI" -ForegroundColor Cyan
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

# Create logs directory if it doesn't exist
if (-not (Test-Path -Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

# Set environment variables
$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"
if (-not $env:REDIS_URL) {
    $env:REDIS_URL = "redis://localhost:6379/0"
}

Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Redis URL: $env:REDIS_URL" -ForegroundColor White
Write-Host "  Queues: video_generation, default" -ForegroundColor White
Write-Host "  Concurrency: 4 workers" -ForegroundColor White
Write-Host "  Log file: logs\celery_worker.log" -ForegroundColor White
Write-Host ""

# Start Celery worker
Write-Host "Starting Celery worker..." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

celery -A celery_app worker `
    --loglevel=info `
    --concurrency=4 `
    --queues=video_generation,default `
    --logfile=logs\celery_worker.log `
    --pidfile=logs\celery_worker.pid `
    --hostname=worker@%h `
    --max-tasks-per-child=10 `
    --time-limit=600 `
    --soft-time-limit=540 `
    --pool=solo

# Note: On Windows, use --pool=solo for better compatibility
# For production, consider using --pool=threads or --pool=gevent

# Alternative: Autoscaling mode
# Uncomment the following to use autoscaling (scales from 2 to 8 workers)
#
# celery -A celery_app worker `
#     --loglevel=info `
#     --autoscale=8,2 `
#     --queues=video_generation,default `
#     --logfile=logs\celery_worker.log `
#     --pidfile=logs\celery_worker.pid `
#     --hostname=worker@%h `
#     --max-tasks-per-child=10 `
#     --time-limit=600 `
#     --soft-time-limit=540 `
#     --pool=solo
