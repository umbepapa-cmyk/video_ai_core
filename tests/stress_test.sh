#!/bin/bash
# ============================================================================
# WEEK 4 - DAY 25-26: Stress Testing Script
# ============================================================================
# Automated stress testing for AppVideoAI
# Tests concurrent load, database locking, and system limits

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
HOST="${HOST:-http://localhost:8000}"
RESULTS_DIR="results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create results directory
mkdir -p "${RESULTS_DIR}"

echo "════════════════════════════════════════════════════════════════"
echo "  AppVideoAI Stress Testing Suite"
echo "  Target: ${HOST}"
echo "  Timestamp: ${TIMESTAMP}"
echo "════════════════════════════════════════════════════════════════"
echo ""

# ============================================================================
# Test 1: Warm-up Test (10 users, 1 minute)
# ============================================================================
echo -e "${GREEN}[TEST 1/5]${NC} Warm-up Test (10 users, 1 min)"
echo "Purpose: Verify system is ready and responsive"
echo ""

locust -f tests/load_test.py \
    --host="${HOST}" \
    --users=10 \
    --spawn-rate=2 \
    --run-time=1m \
    --headless \
    --csv="${RESULTS_DIR}/warmup_${TIMESTAMP}" \
    --html="${RESULTS_DIR}/warmup_${TIMESTAMP}.html"

echo ""
echo -e "${GREEN}✓${NC} Warm-up test completed"
echo ""
sleep 5

# ============================================================================
# Test 2: Normal Load Test (100 users, 5 minutes)
# ============================================================================
echo -e "${GREEN}[TEST 2/5]${NC} Normal Load Test (100 users, 5 min)"
echo "Purpose: Test typical production load"
echo ""

locust -f tests/load_test.py \
    --host="${HOST}" \
    --users=100 \
    --spawn-rate=10 \
    --run-time=5m \
    --headless \
    --csv="${RESULTS_DIR}/normal_load_${TIMESTAMP}" \
    --html="${RESULTS_DIR}/normal_load_${TIMESTAMP}.html"

echo ""
echo -e "${GREEN}✓${NC} Normal load test completed"
echo ""
sleep 10

# ============================================================================
# Test 3: Spike Test (500 users, 2 minutes)
# ============================================================================
echo -e "${GREEN}[TEST 3/5]${NC} Spike Test (500 users, 2 min)"
echo "Purpose: Test sudden traffic spike handling"
echo ""

locust -f tests/load_test.py \
    --host="${HOST}" \
    --users=500 \
    --spawn-rate=50 \
    --run-time=2m \
    --headless \
    --csv="${RESULTS_DIR}/spike_test_${TIMESTAMP}" \
    --html="${RESULTS_DIR}/spike_test_${TIMESTAMP}.html"

echo ""
echo -e "${GREEN}✓${NC} Spike test completed"
echo ""
sleep 10

# ============================================================================
# Test 4: Endurance Test (50 users, 10 minutes)
# ============================================================================
echo -e "${GREEN}[TEST 4/5]${NC} Endurance Test (50 users, 10 min)"
echo "Purpose: Test system stability over extended period"
echo ""

locust -f tests/load_test.py \
    --host="${HOST}" \
    --users=50 \
    --spawn-rate=5 \
    --run-time=10m \
    --headless \
    --csv="${RESULTS_DIR}/endurance_${TIMESTAMP}" \
    --html="${RESULTS_DIR}/endurance_${TIMESTAMP}.html"

echo ""
echo -e "${GREEN}✓${NC} Endurance test completed"
echo ""
sleep 10

# ============================================================================
# Test 5: Race Condition Test (200 users, aggressive)
# ============================================================================
echo -e "${GREEN}[TEST 5/5]${NC} Race Condition Test (200 users, aggressive)"
echo "Purpose: Test database locking and concurrent credit transactions"
echo ""

locust -f tests/load_test.py \
    --host="${HOST}" \
    --users=200 \
    --spawn-rate=40 \
    --run-time=3m \
    --headless \
    --user=StressTestUser \
    --csv="${RESULTS_DIR}/race_condition_${TIMESTAMP}" \
    --html="${RESULTS_DIR}/race_condition_${TIMESTAMP}.html"

echo ""
echo -e "${GREEN}✓${NC} Race condition test completed"
echo ""

# ============================================================================
# Generate Summary Report
# ============================================================================
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Test Summary"
echo "════════════════════════════════════════════════════════════════"
echo ""

echo "Results saved to: ${RESULTS_DIR}/"
echo ""
echo "Generated files:"
ls -lh "${RESULTS_DIR}"/*"${TIMESTAMP}"* | awk '{print "  - " $9}'
echo ""

# Parse and display key metrics
echo "Key Metrics:"
echo "────────────────────────────────────────────────────────────────"

for test_name in warmup normal_load spike_test endurance race_condition; do
    stats_file="${RESULTS_DIR}/${test_name}_${TIMESTAMP}_stats.csv"
    
    if [ -f "${stats_file}" ]; then
        echo ""
        echo "📊 ${test_name} Test:"
        
        # Extract aggregated stats (last line of stats.csv)
        tail -n 1 "${stats_file}" | awk -F',' '{
            printf "  Total Requests: %s\n", $3
            printf "  Failures: %s (%.2f%%)\n", $4, ($4/$3)*100
            printf "  Avg Response Time: %s ms\n", $5
            printf "  Min/Max: %s / %s ms\n", $6, $7
            printf "  RPS: %s\n", $11
        }'
    fi
done

echo ""
echo "════════════════════════════════════════════════════════════════"
echo -e "${GREEN}✅ All stress tests completed successfully!${NC}"
echo "════════════════════════════════════════════════════════════════"
echo ""

# ============================================================================
# Performance Analysis
# ============================================================================
echo "Performance Analysis:"
echo "────────────────────────────────────────────────────────────────"
echo ""
echo "✓ Check failure rates (should be < 1%)"
echo "✓ Verify P95 response times (should be < 5s)"
echo "✓ Monitor database for deadlocks (should be 0)"
echo "✓ Check memory usage (ephemeral storage cleanup)"
echo "✓ Verify credit transaction integrity"
echo ""
echo "Next Steps:"
echo "  1. Review HTML reports in ${RESULTS_DIR}/"
echo "  2. Check application logs for errors"
echo "  3. Verify database credit_transactions table"
echo "  4. Monitor Sentry for exceptions"
echo ""
