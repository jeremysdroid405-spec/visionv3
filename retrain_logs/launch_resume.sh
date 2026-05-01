#!/bin/bash
LOGFILE=/app/retrain_logs/v3_bayes_resume_$(date +%Y%m%d_%H%M%S).log
cd /app/backend
export MLB_HF_STATS="singles,hits+runs+rbis,earned_runs,hits_allowed,pitcher_walks"
exec setsid python scripts/retrain_mlb_models_v2.py > "$LOGFILE" 2>&1 < /dev/null
