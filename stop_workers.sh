#!/bin/bash
# =============================================================================
# PHASE 2 SPRINT 1: Stop Celery Workers Script
# =============================================================================
#
# Gracefully stops all Celery worker and Flower processes.
#
# Usage:
#   bash stop_workers.sh [--force]
#
# Examples:
#   bash stop_workers.sh           # Graceful shutdown (SIGTERM)
#   bash stop_workers.sh --force   # Force kill (SIGKILL)
#
# =============================================================================

set -e

# Configuration
LOG_DIR="logs"
WORKER_PID_FILE="${LOG_DIR}/celery_worker.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

FORCE_KILL=false

if [ "$1" == "--force" ]; then
    FORCE_KILL=true
fi

echo -e "${YELLOW}======================================${NC}"
echo -e "${YELLOW}Stopping Celery Workers${NC}"
echo -e "${YELLOW}======================================${NC}"

# Stop Celery worker from PID file
if [ -f "${WORKER_PID_FILE}" ]; then
    WORKER_PID=$(cat "${WORKER_PID_FILE}")
    
    if ps -p "${WORKER_PID}" > /dev/null 2>&1; then
        echo -e "\n${YELLOW}Stopping Celery worker (PID: ${WORKER_PID})...${NC}"
        
        if [ "${FORCE_KILL}" = true ]; then
            kill -9 "${WORKER_PID}"
            echo -e "${RED}✓ Force killed worker${NC}"
        else
            kill -TERM "${WORKER_PID}"
            echo -e "${GREEN}✓ Sent SIGTERM to worker (graceful shutdown)${NC}"
            
            # Wait for graceful shutdown
            echo -e "${YELLOW}Waiting for worker to finish current tasks...${NC}"
            for i in {1..30}; do
                if ! ps -p "${WORKER_PID}" > /dev/null 2>&1; then
                    break
                fi
                sleep 1
                echo -n "."
            done
            echo ""
            
            if ps -p "${WORKER_PID}" > /dev/null 2>&1; then
                echo -e "${YELLOW}Worker still running, force killing...${NC}"
                kill -9 "${WORKER_PID}"
            fi
        fi
        
        rm "${WORKER_PID_FILE}"
    else
        echo -e "${YELLOW}Worker PID ${WORKER_PID} not running${NC}"
        rm "${WORKER_PID_FILE}"
    fi
else
    echo -e "${YELLOW}No PID file found at ${WORKER_PID_FILE}${NC}"
fi

# Stop all Celery processes by name
echo -e "\n${YELLOW}Checking for other Celery processes...${NC}"

if [ "${FORCE_KILL}" = true ]; then
    pkill -9 -f "celery.*worker" 2>/dev/null && echo -e "${RED}✓ Force killed Celery workers${NC}" || echo -e "${YELLOW}No additional workers found${NC}"
else
    pkill -TERM -f "celery.*worker" 2>/dev/null && echo -e "${GREEN}✓ Stopped Celery workers${NC}" || echo -e "${YELLOW}No additional workers found${NC}"
fi

# Stop Flower
echo -e "\n${YELLOW}Stopping Flower monitoring...${NC}"

if [ "${FORCE_KILL}" = true ]; then
    pkill -9 -f "celery.*flower" 2>/dev/null && echo -e "${RED}✓ Force killed Flower${NC}" || echo -e "${YELLOW}Flower not running${NC}"
else
    pkill -TERM -f "celery.*flower" 2>/dev/null && echo -e "${GREEN}✓ Stopped Flower${NC}" || echo -e "${YELLOW}Flower not running${NC}"
fi

echo -e "\n${GREEN}======================================${NC}"
echo -e "${GREEN}All Celery processes stopped${NC}"
echo -e "${GREEN}======================================${NC}"
