#!/usr/bin/env bash
# =============================================================================
# MLB Pre-Game Lineup Ingest  (PA-v2 input refresh)
# =============================================================================
# Run-mode contract:
#   * LINEUP-DATA PLUMBING ONLY.  This script never touches scoring,
#     gates, thresholds, tier routing, μ/σ, or selection logic.
#   * Runs ~6 PM ET (22:00 UTC), when MLB Stats API has 95%+ of
#     confirmed lineup cards posted.
#   * Strictly read-only against MLB Stats API; writes only to
#     `mlb_projected_lineups` and the four lineup-related fields on
#     `mlb_live_props` (batting_order / lineup_confirmed / lineup_source).
#   * Strict no-leakage rule enforced inside the ingestor and the loader:
#     `as_of <= commence_time` always.
#   * After ingest, runs the read-only coverage monitor.  SLA breaches
#     print "WARNING" but never fail the pipeline.
#
# Cron entry:
#   0 22 * * *  /app/backend/scripts/run_mlb_pregame_lineups.sh \
#                 >> /var/log/mlb_lineups.log 2>&1
#
# Manual one-shot for a specific date:
#   DATE=2026-04-28 /app/backend/scripts/run_mlb_pregame_lineups.sh
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
