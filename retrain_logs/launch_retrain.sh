#!/bin/bash
# Launches retrain as a true daemon, fully detached.
LOGFILE=/app/retrain_logs/v3_bayes_$(date +%Y%m%d_%H%M%S).log
cd /app/backend
exec setsid python scripts/retrain_mlb_models_v2.py > "$LOGFILE" 2>&1 < /dev/null
