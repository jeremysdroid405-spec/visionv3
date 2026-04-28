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
# Cron entries (install in ops crontab):
#
#   # Daily pipeline — Statcast + features + identity + scoring (post-slate).
#   0 4  * * *  /app/backend/scripts/run_mlb_daily_pipeline.sh \
#                  >> /var/log/mlb_pipeline.log 2>&1
#
#   # Pre-game lineup ingest — runs ~6 PM ET, when MLB Stats API has
#   # 95%+ of confirmed cards posted.  Lineup-only; never touches scoring.
#   0 22 * * *  /app/backend/scripts/run_mlb_pregame_lineups.sh \
#                  >> /var/log/mlb_lineups.log 2>&1
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
echo "[1/8] ingest today's MLB Stats API lineup cards (PA-v2 input)"
python -m scripts.ingest_mlb_projected_lineups --date "$(date -u +%Y-%m-%d)"

echo
echo "[2/8] ingest Statcast for $YESTERDAY"
python -m scripts.mlb_statcast_ingest \
    --start "$YESTERDAY" --end "$YESTERDAY"

echo
echo "[3/8] rebuild rolling batter features"
python -m scripts.mlb_statcast_build_features

echo
echo "[4/8] rebuild rolling pitcher features (shadow inputs)"
python -m scripts.mlb_statcast_build_pitcher_features

echo
echo "[5/8] backfill pitcher context onto mlb_pick_history (SHADOW)"
python -m scripts.mlb_backfill_pitcher_context

echo
echo "[6/8] rebuild player identity map"
python -m scripts.build_mlb_player_identity_map

echo
echo "[7/8] validate joins (must remain 26+/26 PASS)"
python -m scripts.mlb_statcast_validate

echo
echo "[8/8] score today's slate + log picks to mlb_pick_history"
python /app/backend/scripts/mlb_propvision_total_bases.py --log-picks

echo
echo "============================================================"
echo "  PIPELINE DONE  ·  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================================"
