#!/bin/bash
# 15-day MLB replay sweep orchestrator (2026-05-01 .. 2026-05-15).
# Runs Layers 1→2→3 for any date missing data, then runs the
# Layer-4 multi-tier sweep with audit/serial generation for every date.
#
# All output captured to /app/logs/mlb_replay_sweep_<ts>.log.
set -uo pipefail

WINDOW_START="2026-05-01"
WINDOW_END="2026-05-15"
SNAPSHOT_HOUR=11
LOG_DIR=/app/logs
mkdir -p "$LOG_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/mlb_replay_sweep_${TS}.log"

cd /app/backend

log() { echo "[$(date -u +%H:%M:%S)Z] $*" | tee -a "$LOG"; }

log "=== MLB REPLAY 15-DAY SWEEP — start ==="
log "window: $WINDOW_START .. $WINDOW_END  snapshot_hour: ${SNAPSHOT_HOUR} UTC"
log "log_file: $LOG"

# ── LAYER 1: alt-odds ingest (Odds API). Idempotent / resumable. ─────
log ""
log "─── Layer 1: alt-odds ingest ($WINDOW_START..$WINDOW_END) ───"
python -m scripts.mlb_historical_alt_odds_ingest \
    --start "$WINDOW_START" --end "$WINDOW_END" \
    --chunk-size 20 \
    --snapshot-hour ${SNAPSHOT_HOUR} \
    --mem-limit 1500 >> "$LOG" 2>&1
L1_RC=$?
log "Layer 1 exit=$L1_RC"

if [[ $L1_RC -ne 0 && $L1_RC -ne 2 ]]; then
    log "ABORT: Layer 1 failed unexpectedly (exit=$L1_RC)."
    exit 1
fi

# ── LAYER 2 + LAYER 3 (per-date, in sequence; both idempotent) ───────
DATES=$(python - <<'PY'
from datetime import datetime, timedelta
d0 = datetime.strptime("2026-05-01", "%Y-%m-%d")
d1 = datetime.strptime("2026-05-15", "%Y-%m-%d")
out = []
while d0 <= d1:
    out.append(d0.strftime("%Y-%m-%d"))
    d0 += timedelta(days=1)
print(" ".join(out))
PY
)

for D in $DATES; do
    log ""
    log "─── Layers 2-3 for $D ───"
    python -m scripts.mlb_replay_build_feature_cache --date "$D" \
        --mem-limit 1500 >> "$LOG" 2>&1
    L2_RC=$?
    log "  L2 exit=$L2_RC"
    if [[ $L2_RC -ne 0 ]]; then
        log "  L2 failed for $D — continuing to next date (best-effort)."
        continue
    fi
    SNAP="${D}T$(printf '%02d' ${SNAPSHOT_HOUR}):00:00Z"
    python -m scripts.mlb_replay_model_outputs \
        --date "$D" --snapshot-iso "$SNAP" \
        --mem-limit 1500 >> "$LOG" 2>&1
    L3_RC=$?
    log "  L3 exit=$L3_RC"
done

# ── LAYER 4: multi-tier sweep with audit + serial per date ──────────
log ""
log "─── Layer 4: multi-tier sweep + audit ───"
for D in $DATES; do
    SNAP="${D}T$(printf '%02d' ${SNAPSHOT_HOUR}):00:00Z"
    log ""
    log "  === $D @ $SNAP ==="
    python -m scripts.mlb_replay_multi_tier_sweep \
        --date "$D" --snapshot-iso "$SNAP" \
        --mem-limit 1500 >> "$LOG" 2>&1
    L4_RC=$?
    log "  L4 exit=$L4_RC"
done

log ""
log "=== MLB REPLAY 15-DAY SWEEP — done ==="
