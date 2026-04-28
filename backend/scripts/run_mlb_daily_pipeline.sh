#!/usr/bin/env bash
# =============================================================================
# MLB Total Bases v1 — Locked Daily Pipeline   ⚠ DEPRECATED 2026-04-28 ⚠
# =============================================================================
# Phase 4 of Sync Hardening migrated this pipeline into APScheduler.
# Production scheduling is now `mlb_daily_pipeline` job in
# `/app/backend/services/scheduled/mlb_jobs.py`, registered by
# `server.py` at boot, fires daily at 04:00 UTC, lock=`sync:mlb`.
#
# This shell script is kept for MANUAL ROLLBACK ONLY.  Do NOT
# install in host crontab — it would race the in-process scheduler.
# To run manually:
#   python -m services.scheduled.mlb_jobs daily
#
# Order of operations (mirrored in services/scheduled/mlb_jobs.py):
#   1. Lineup ingest (today)
#   2. Statcast ingest (yesterday)
#   3. Batter features rebuild
#   4. Pitcher features rebuild
#   5. Pitcher context backfill (shadow)
#   6. Identity map rebuild
#   7. Statcast validation
#   8. Score today's slate + log picks
#
# Manual one-shot for a specific date (legacy path, still works):
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
