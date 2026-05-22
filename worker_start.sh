#!/bin/bash

# ============================================================================
# PHASE 2 SPRINT 1: Celery Worker Startup Script
# ============================================================================
# Starts Celery worker with optimal configuration for video generation tasks.
#
# Usage:
#   bash worker_start.sh
#
# Features:
# - 4 concurrent workers (adjust based on GPU availability)
# - video_generation and default queues
# - Autoscaling support
# - Comprehensive logging
# ============================================================================

echo "============================================================"
echo "Starting Celery Worker for AppVideoAI"
echo "============================================================"
echo ""

# Check if Redis is running
echo "Checking Redis connection..."
redis-cli -h localhost -p 6379 ping > /dev/null 2>&1

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Redis is not running!"
    echo "Please start Redis first:"
    echo "  docker-compose -f docker-compose.redis.yml up -d"
    exit 1
fi

echo "✓ Redis is running"
echo ""

# Create logs directory if it doesn't exist
mkdir -p logs

# Set environment variables
export PYTHONPATH=$(pwd):$PYTHONPATH
export REDIS_URL=${REDIS_URL:-"redis://localhost:6379/0"}

echo "Configuration:"
echo "  Redis URL: $REDIS_URL"
echo "  Queues: video_generation, default"
echo "  Concurrency: 4 workers"
echo "  Log file: logs/celery_worker.log"
echo ""

# Start Celery worker
echo "Starting Celery worker..."
echo "============================================================"
echo ""

celery -A celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=video_generation,default \
    --logfile=logs/celery_worker.log \
    --pidfile=logs/celery_worker.pid \
    --hostname=worker@%h \
    --max-tasks-per-child=10 \
    --time-limit=600 \
    --soft-time-limit=540

# Alternative: Autoscaling mode
# Uncomment the following to use autoscaling (scales from 2 to 8 workers)
#
# celery -A celery_app worker \
#     --loglevel=info \
#     --autoscale=8,2 \
#     --queues=video_generation,default \
#     --logfile=logs/celery_worker.log \
#     --pidfile=logs/celery_worker.pid \
#     --hostname=worker@%h \
#     --max-tasks-per-child=10 \
#     --time-limit=600 \
#     --soft-time-limit=540
