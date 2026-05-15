#!/usr/bin/env python3
"""Phase 2A retrain launcher — double-forks so the retrain Python
process becomes a daemon orphan parented to init, surviving any MCP
bash-session restarts. Writes PID to /tmp/retrain_phase2a.pid.
"""
import os, sys

if os.fork() != 0:
    sys.exit(0)
os.setsid()
if os.fork() != 0:
    sys.exit(0)

# Now we're the grandchild — fully detached.
os.chdir("/app/backend")
os.environ["MLB_HF_STATS"] = (
    "hits,total_bases,rbis,runs,home_runs,doubles,walks,singles,"
    "hits+runs+rbis,stolen_bases"
)
os.environ["PYTHONUNBUFFERED"] = "1"

fd_in = os.open("/dev/null", os.O_RDONLY)
fd_out = os.open("/tmp/retrain_phase2a.log",
                  os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd_in, 0)
os.dup2(fd_out, 1)
os.dup2(fd_out, 2)

with open("/tmp/retrain_phase2a.pid", "w") as f:
    f.write(f"{os.getpid()}\n")

os.execv("/root/.venv/bin/python",
          ["python", "-u", "/app/backend/scripts/retrain_mlb_models_v2.py"])
