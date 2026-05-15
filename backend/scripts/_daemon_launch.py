#!/usr/bin/env python3
"""Generic double-fork launcher — daemonizes any command line.

Usage:
    python /app/backend/scripts/_daemon_launch.py \
        /tmp/myjob.log -- python script.py --arg1 foo

The grandchild becomes init's orphan and survives any parent shell
death (incl. MCP bash session restarts). PID written to
/tmp/<basename>.pid (basename derived from log path).
"""
import os, sys

if len(sys.argv) < 4 or sys.argv[2] != "--":
    print(f"Usage: {sys.argv[0]} <log_path> -- <cmd> [args...]")
    sys.exit(2)
log_path = sys.argv[1]
cmd = sys.argv[3:]

if os.fork() != 0:
    sys.exit(0)
os.setsid()
if os.fork() != 0:
    sys.exit(0)

fd_in = os.open("/dev/null", os.O_RDONLY)
fd_out = os.open(log_path,
                  os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd_in, 0)
os.dup2(fd_out, 1)
os.dup2(fd_out, 2)

pid_path = log_path.rsplit(".", 1)[0] + ".pid"
with open(pid_path, "w") as f:
    f.write(f"{os.getpid()}\n")

os.environ["PYTHONUNBUFFERED"] = "1"
os.execvp(cmd[0], cmd)
