#!/bin/bash
# PropVision Database Initialization Script
# Run this after deploying to populate the database

set -e

echo "=========================================="
echo "PropVision Database Initialization"
echo "=========================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
BACKEND_URL="${1:-http://localhost:8001}"

echo -e "${YELLOW}Using backend URL: ${BACKEND_URL}${NC}"
echo ""

# Function to call API
call_api() {
    local endpoint=$1
    local description=$2
    echo -e "${YELLOW}[$description]${NC}"
    response=$(curl -s -X POST "${BACKEND_URL}${endpoint}" -H "Content-Type: application/json" 2>&1)
    if echo "$response" | grep -q "error\|Error\|ERROR"; then
        echo -e "${RED}Failed: $response${NC}"
    else
        echo -e "${GREEN}Success!${NC}"
    fi
    echo ""
    sleep 2
}

# Check if backend is running
echo "Checking backend health..."
health=$(curl -s "${BACKEND_URL}/api/health" 2>&1)
if echo "$health" | grep -q "ok\|healthy\|status"; then
    echo -e "${GREEN}Backend is running!${NC}"
else
    echo -e "${RED}Backend not responding. Make sure it's running first.${NC}"
    echo "Start with: pm2 start ecosystem.config.js"
    exit 1
fi
echo ""

echo "=========================================="
echo "Step 1: Sync Live Odds from Odds API"
echo "=========================================="
call_api "/api/v3/sync" "Syncing Odds API data"

echo "=========================================="
echo "Step 2: Build Master Hub (Player Database)"
echo "=========================================="
call_api "/api/v3/hub/sync" "Building Master Hub"

echo "=========================================="
echo "Step 3: Sync BDL Player Data"
echo "=========================================="
call_api "/api/v3/hub/bdl-sync" "Syncing BallDontLie data"

echo "=========================================="
echo "Step 4: Build Deep Intel"
echo "=========================================="
call_api "/api/demon/sync" "Building Deep Intel"

echo "=========================================="
echo "Step 5: Refresh Live Board"
echo "=========================================="
call_api "/api/board/refresh" "Refreshing board"

echo ""
echo "=========================================="
echo -e "${GREEN}Database Initialization Complete!${NC}"
echo "=========================================="
echo ""
echo "Verify data was loaded:"
echo "  curl ${BACKEND_URL}/api/v3/board"
echo ""
echo "Check collection counts in MongoDB:"
echo "  mongosh pick_vision --eval 'db.dg_cached_board.countDocuments()'"
echo "  mongosh pick_vision --eval 'db.nba_master_hub_2026.countDocuments()'"
