#!/bin/bash

# ============================================================================
# PHASE 2 SPRINT 1: Celery Worker Stop Script
# ============================================================================
# Gracefully stops all Celery workers.
#
# Usage:
#   bash worker_stop.sh
# ============================================================================

echo "============================================================"
echo "Stopping Celery Workers"
echo "============================================================"
echo ""

# Check if PID file exists
if [ -f "logs/celery_worker.pid" ]; then
    PID=$(cat logs/celery_worker.pid)
    
    echo "Found worker PID: $PID"
    echo "Sending TERM signal for graceful shutdown..."
    
    kill -TERM $PID
    
    # Wait for worker to finish current tasks (max 60 seconds)
    timeout=60
    while [ $timeout -gt 0 ]; do
        if ! ps -p $PID > /dev/null 2>&1; then
            echo "✓ Worker stopped gracefully"
            rm -f logs/celery_worker.pid
            exit 0
        fi
        
        echo "Waiting for worker to finish current tasks... ($timeout seconds remaining)"
        sleep 5
        timeout=$((timeout - 5))
    done
    
    # Force kill if still running
    if ps -p $PID > /dev/null 2>&1; then
        echo "⚠ Worker did not stop gracefully, forcing shutdown..."
        kill -KILL $PID
        rm -f logs/celery_worker.pid
        echo "✓ Worker forcefully stopped"
    fi
    
else
    echo "No PID file found at logs/celery_worker.pid"
    echo "Searching for running Celery workers..."
    
    # Try to find and kill celery processes
    pkill -f "celery.*worker"
    
    if [ $? -eq 0 ]; then
        echo "✓ Stopped running Celery workers"
    else
        echo "No running Celery workers found"
    fi
fi

echo ""
echo "============================================================"
