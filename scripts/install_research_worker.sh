#!/usr/bin/env bash
# Install / repair the research_worker supervisor service.
#
# Usage (run as root on the prod host):
#     sudo bash /app/scripts/install_research_worker.sh
#
# Idempotent. Safe to re-run. Writes /etc/supervisor/conf.d/research_worker.conf
# (overwrites if it exists) then forces supervisor to pick up the change.
# Exits non-zero with a clear message if something went wrong.

set -euo pipefail

CONF_DST="/etc/supervisor/conf.d/research_worker.conf"
BACKEND_DIR="/app/backend"
PY_BIN="/root/.venv/bin/python"

# ── Sanity checks ─────────────────────────────────────────────────────
if [[ "${EUID:-1000}" -ne 0 ]]; then
    echo "error: must run as root (sudo)" >&2
    exit 2
fi
if [[ ! -d "$BACKEND_DIR" ]]; then
    echo "error: backend dir $BACKEND_DIR not found" >&2
    exit 2
fi
if [[ ! -x "$PY_BIN" ]]; then
    echo "error: python interpreter $PY_BIN not found / not executable" >&2
    exit 2
fi
if [[ ! -f "$BACKEND_DIR/workers/research_worker.py" ]]; then
    echo "error: $BACKEND_DIR/workers/research_worker.py missing — pull latest code first" >&2
    exit 2
fi
if ! command -v supervisorctl >/dev/null 2>&1; then
    echo "error: supervisorctl not on PATH — supervisor isn't installed?" >&2
    exit 2
fi

# ── Write the conf ───────────────────────────────────────────────────
cat > "$CONF_DST" <<'CONF'
; research_worker — drains emergent_admin_jobs (worker_queue=true) one
; at a time, in a separate process from the FastAPI backend so heavy
; compute (optimizer sweeps, historical replay) cannot starve the API.
;
; Resource caps (nice, RLIMIT_AS, oom_score_adj, timeout) are applied
; PER SPAWNED CHILD SUBPROCESS by workers.research_worker, NOT to the
; daemon itself.
[program:research_worker]
command=/root/.venv/bin/python -u -m workers.research_worker
directory=/app/backend
autostart=true
autorestart=true
environment=PYTHONUNBUFFERED="1",PYTHONPATH="/app/backend",RW_HEARTBEAT_PATH="/tmp/research_worker.heartbeat"
stderr_logfile=/var/log/supervisor/research_worker.err.log
stdout_logfile=/var/log/supervisor/research_worker.out.log
stopsignal=TERM
stopwaitsecs=30
stopasgroup=true
killasgroup=true
priority=20
CONF
echo "wrote $CONF_DST"

# ── Activate ─────────────────────────────────────────────────────────
supervisorctl reread
supervisorctl update research_worker || supervisorctl update
sleep 2
supervisorctl start research_worker || true
sleep 2
supervisorctl status research_worker

# ── Verify heartbeat ─────────────────────────────────────────────────
for i in 1 2 3 4 5; do
    if [[ -f /tmp/research_worker.heartbeat ]]; then
        age=$(( $(date +%s) - $(stat -c %Y /tmp/research_worker.heartbeat) ))
        echo "heartbeat present, age=${age}s"
        if [[ "$age" -lt 10 ]]; then
            echo "✓ research_worker is consuming the queue"
            exit 0
        fi
    fi
    echo "  waiting for heartbeat… ($i/5)"
    sleep 2
done
echo "error: research_worker did not produce a heartbeat. Check:" >&2
echo "  tail /var/log/supervisor/research_worker.err.log" >&2
exit 1
