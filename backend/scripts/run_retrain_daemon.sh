#!/bin/bash
# Daemonize the MLB weakest-4 retrain so it survives agent transport blips.
# Writes log + pidfile + exit-code file so we can monitor without holding
# the bash session open.

LOG=/var/log/retrain_mlb_v2.log
PIDFILE=/var/run/retrain_mlb.pid
DONEFILE=/var/log/retrain_mlb_done

rm -f "$DONEFILE"
cd /app/backend

(
  exec setsid -f /root/.venv/bin/python3 \
    /app/backend/scripts/retrain_mlb_models_v2.py \
    > "$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  wait $! 2>/dev/null
  echo "exit=$? at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DONEFILE"
) &
disown

# brief sleep so the inner process has a PID before we exit
sleep 2
echo "spawned, pid: $(cat $PIDFILE 2>/dev/null || echo unknown)"
echo "log:    $LOG"
echo "done:   $DONEFILE  (written when process exits)"
