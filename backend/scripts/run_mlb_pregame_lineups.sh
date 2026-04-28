#!/usr/bin/env bash
# =============================================================================
# MLB Pre-Game Lineup Ingest   ⚠ DEPRECATED 2026-04-28 ⚠
# =============================================================================
# Phase 4 of Sync Hardening migrated this into APScheduler.
# Production now runs TWO in-process jobs registered in `server.py`:
#   - mlb_lineups_early   18:00 UTC   lock=lineup:mlb
#   - mlb_lineups_final   22:00 UTC   lock=lineup:mlb
# Source: /app/backend/services/scheduled/mlb_jobs.py (mlb_pregame_lineups)
#
# This shell script is kept for MANUAL ROLLBACK ONLY.  Do NOT
# install in host crontab — it would race the in-process scheduler.
# To run manually:
#   python -m services.scheduled.mlb_jobs lineups
#
# Run-mode contract:
#   * LINEUP-DATA PLUMBING ONLY.  This script never touches scoring,
#     gates, thresholds, tier routing, μ/σ, or selection logic.
#   * Strict no-leakage rule enforced inside the ingestor and the loader:
#     `as_of <= commence_time` always.
# =============================================================================
set -euo pipefail

cd /app/backend

DATE="${DATE:-$(date -u +%Y-%m-%d)}"

echo "============================================================"
echo "  MLB PRE-GAME LINEUP INGEST  ·  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  slate date: $DATE"
echo "============================================================"

echo
echo "[1/2] ingest MLB Stats API lineup cards  (mlb_projected_lineups)"
python -m scripts.ingest_mlb_projected_lineups --date "$DATE"

echo
echo "[2/2] coverage monitor  (read-only; WARNING on SLA breach)"
python -m scripts.monitor_mlb_lineup_coverage

echo
echo "============================================================"
echo "  PRE-GAME LINEUP INGEST DONE  ·  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================================"
