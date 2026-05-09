#!/bin/bash
# Re-invoke run_full_replay_chunked.py one day at a time until done or
# error. Each chunk (~2.5 min) is short enough to survive container
# restarts; checkpoint persists in `replay_engine_progress`.
#
# Usage: nohup bash run_full_replay_loop.sh <run_id> > /tmp/full_loop.log 2>&1 &
set -u
RUN_ID="${1:-full_chunked_$(date +%s)}"
START="${2:-2024-02-01}"
END="${3:-2024-03-01}"
SNAP="${4:-t-30m}"

cd /app/backend
echo "[loop] run_id=$RUN_ID  range=$START..$END  snap=$SNAP"

i=0
while true; do
  i=$((i + 1))
  echo "[loop] iter $i — invoking chunked driver"
  python3 scripts/run_full_replay_chunked.py \
    --start "$START" --end "$END" --snapshot-label "$SNAP" \
    --run-id "$RUN_ID" --max-dates-per-invocation 1
  rc=$?
  echo "[loop] iter $i exit=$rc"
  if [ "$rc" -eq 2 ]; then
    echo "[loop] all dates complete — done"
    exit 0
  fi
  if [ "$rc" -ne 0 ]; then
    echo "[loop] non-zero exit ($rc) — aborting"
    exit "$rc"
  fi
  # tiny pause so we don't hammer mongo between chunks
  sleep 2
done
