#!/bin/bash
# Outer event-level loop. Each iteration processes 1 event (~1-2 min).
# Survives pod recycles between events because progress is persisted
# in replay_engine_progress.
set -u
RUN_ID="${1:?run_id required}"
START="${2:-2024-02-01}"
END="${3:-2024-03-01}"
SNAP="${4:-t-30m}"

cd /app/backend
echo "[evt-loop] run_id=$RUN_ID range=$START..$END"

i=0
while true; do
  i=$((i + 1))
  python3 scripts/run_full_replay_event_chunked.py \
    --start "$START" --end "$END" --snapshot-label "$SNAP" \
    --run-id "$RUN_ID" --max-events-per-invocation 1
  rc=$?
  echo "[evt-loop] iter $i exit=$rc"
  case $rc in
    0) sleep 1 ;;
    2) echo "[evt-loop] all done"; exit 0 ;;
    *) echo "[evt-loop] error $rc"; exit "$rc" ;;
  esac
done
