#!/bin/bash
# =============================================================================
# PHASE 2 SPRINT 1: Celery Monitoring Script (Flower)
# =============================================================================
#
# Starts Flower web UI for monitoring Celery workers and tasks.
#
# Flower provides:
# - Real-time task monitoring
# - Worker status and statistics
# - Task history and details
# - Queue inspection
# - Rate limiting controls
#
# Usage:
#   bash monitor_celery.sh [port]
#
# Examples:
#   bash monitor_celery.sh         # Start on default port 5555
#   bash monitor_celery.sh 8888    # Start on custom port
#
# =============================================================================

set -e

# Configuration
PORT="${1:-5555}"
LOG_DIR="logs"
LOG_FILE="${LOG_DIR}/flower.log"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Starting Flower Monitoring UI${NC}"
echo -e "${GREEN}======================================${NC}"

# Create logs directory
mkdir -p "${LOG_DIR}"

# Start Flower
echo -e "\n${CYAN}Starting Flower on http://localhost:${PORT}${NC}"
echo -e "  Log file: ${LOG_FILE}\n"

celery -A celery_app flower \
    --port="${PORT}" \
    --broker="$(python -c 'import celery_config; print(celery_config.REDIS_URL)')" \
    --logging=info \
    --persistent=True \
    --db=flower_db.sqlite \
    --max_tasks=10000 \
    --enable_events=True \
    > "${LOG_FILE}" 2>&1 &

FLOWER_PID=$!

echo -e "${GREEN}✓ Flower started with PID ${FLOWER_PID}${NC}"
echo -e ""
echo -e "  ${CYAN}Open in browser: http://localhost:${PORT}${NC}"
echo -e "  Logs: tail -f ${LOG_FILE}"
echo -e "  Stop: kill ${FLOWER_PID}"

echo -e "\n${GREEN}======================================${NC}"
echo -e "${GREEN}Flower is running!${NC}"
echo -e "${GREEN}======================================${NC}"

# Wait for Flower to start
sleep 3

# Try to open browser (optional)
if command -v xdg-open > /dev/null 2>&1; then
    xdg-open "http://localhost:${PORT}" &> /dev/null &
elif command -v open > /dev/null 2>&1; then
    open "http://localhost:${PORT}" &> /dev/null &
fi

echo -e "\n${YELLOW}Flower dashboard features:${NC}"
echo -e "  • Task monitoring (real-time)"
echo -e "  • Worker statistics"
echo -e "  • Queue inspection"
echo -e "  • Task history"
echo -e "  • Rate limiting"
echo -e "  • Task revocation"

# Keep script running
wait "${FLOWER_PID}"
