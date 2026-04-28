#!/usr/bin/env bash
# =============================================================================
# MLB Total Bases v1 — Locked Daily Pipeline
# =============================================================================
# Run-mode contract (per spec): NO model / gate / threshold changes during
# the locked forward-test window. This script is the SINGLE entry point
# for the daily cron — anything not in here is out-of-band manual work.
#
# Order of operations:
#   1. Ingest yesterday's Statcast events
#   2. Rebuild rolling 7/14/30 + season features
#   3. Rebuild the player identity map (handles new call-ups)
#   4. Validate joins (fail-fast on coverage drop)
#   5. Score today's slate + log picks to mlb_pick_history
#
# Cron entry (run nightly at 04:00 UTC, after MLB games settle):
#   0 4 * * *  /app/backend/scripts/run_mlb_daily_pipeline.sh \
#               >> /var/log/mlb_pipeline.log 2>&1
#
# Manual one-shot for a specific date:
#   YESTERDAY=2026-04-26 /app/backend/scripts/run_mlb_daily_pipeline.sh
# =============================================================================
set -euo pipefail

# Always run from the backend root so `python -m scripts.…` works.
cd /app/backend

# Date-window override: caller can pass YESTERDAY=YYYY-MM-DD; default = real
# yesterday in UTC. We deliberately do not use TODAY because Statcast
# events lag one day (last night's games settle next morning).
YESTERDAY="${YESTERDAY:-$(date -u -d 'yesterday' +%Y-%m-%d)}"

echo "============================================================"
echo "  MLB DAILY PIPELINE  ·  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Statcast window: $YESTERDAY"
echo "============================================================"

echo
echo "[1/5] ingest Statcast for $YESTERDAY"
python -m scripts.mlb_statcast_ingest \
    --start "$YESTERDAY" --end "$YESTERDAY"

echo
echo "[2/5] rebuild rolling features"
python -m scripts.mlb_statcast_build_features

echo
echo "[3/5] rebuild player identity map"
python -m scripts.build_mlb_player_identity_map

echo
echo "[4/5] validate joins (must remain 26+/26 PASS)"
python -m scripts.mlb_statcast_validate

echo
echo "[5/5] score today's slate + log picks to mlb_pick_history"
python /app/backend/scripts/mlb_propvision_total_bases.py --log-picks

echo
echo "============================================================"
echo "  PIPELINE DONE  ·  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================================"
