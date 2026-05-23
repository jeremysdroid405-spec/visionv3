"""
Research worker API — health, queue, and cancellation surface for the
out-of-process worker daemon (workers/research_worker.py).

Endpoints (token-gated, audit-logged):

    GET  /api/emergent-admin/worker/health
        Returns queue depth, active job, worker pid/cpu/memory,
        heartbeat age, backend pod CPU/memory for comparison.

    GET  /api/emergent-admin/worker/queue
        Recent worker-routed jobs (queued + active + finished tail).

    POST /api/emergent-admin/worker/cancel/{job_id}
        SIGTERM the worker subprocess for the named job (does not stop
        the worker daemon itself).

    GET  /api/emergent-admin/worker/testing-mode
    POST /api/emergent-admin/worker/testing-mode  body={enabled: bool}
        Pause/resume the APScheduler in the backend pod. When enabled,
        every periodic sync (SGO pulls, recompute, delta) is suspended
        so memory-heavy research workloads have the pod to themselves.
"""
from __future__ import annotations
import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import audit_log, require_admin_token, _get_db

logger = logging.getLogger(__name__)
router = APIRouter()

JOBS_COLL          = "emergent_admin_jobs"
WORKER_HEARTBEAT_PATH = os.environ.get("RW_HEARTBEAT_PATH",
                                          "/tmp/research_worker.heartbeat")


def _proc_metrics(pid: int) -> Dict[str, Any]:
    """psutil-based snapshot. Tolerates psutil absence."""
    try:
        import psutil
        p = psutil.Process(pid)
        with p.oneshot():
            mi = p.memory_info()
            cts = p.cpu_times()
            return {
                "pid": pid, "alive": p.is_running(),
                "rss_bytes":   int(mi.rss),
                "vms_bytes":   int(mi.vms),
                "cpu_percent": p.cpu_percent(interval=None),
                "cpu_user_s":   float(cts.user),
                "cpu_system_s": float(cts.system),
                "num_threads": p.num_threads(),
                "create_time": p.create_time(),
                "status":      p.status(),
            }
    except Exception as e:  # noqa: BLE001
        return {"pid": pid, "alive": False, "error": repr(e)[:200]}


def _find_worker_pid() -> Optional[int]:
    """Lookup the running research_worker process via /proc, no psutil
    dependency. Falls back to psutil if available."""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\x00", b" ").decode(
                        "utf-8", errors="ignore")
                if "workers.research_worker" in cmd:
                    return int(entry)
            except (FileNotFoundError, PermissionError):
                continue
    except FileNotFoundError:
        pass
    return None


def _heartbeat_age_s() -> Optional[float]:
    try:
        st = os.stat(WORKER_HEARTBEAT_PATH)
        return max(0.0, time.time() - st.st_mtime)
    except FileNotFoundError:
        return None


@router.get("/health")
async def health(request: Request, auth=Depends(require_admin_token)):
    db = _get_db()
    queued = await db[JOBS_COLL].count_documents(
        {"status": "queued", "worker_queue": True})
    claimed = await db[JOBS_COLL].count_documents(
        {"status": {"$in": ["claimed", "running"]}, "worker_queue": True})
    failed_recent = await db[JOBS_COLL].count_documents(
        {"status": {"$in": ["failed", "errored", "timeout"]},
          "worker_queue": True})
    active = await db[JOBS_COLL].find_one(
        {"status": {"$in": ["claimed", "running"]}, "worker_queue": True},
        sort=[("started_at", -1)],
        projection={"_id": 0, "log": 0},
    )
    worker_pid = _find_worker_pid()
    worker_metrics = _proc_metrics(worker_pid) if worker_pid else None
    backend_pid = os.getpid()
    backend_metrics = _proc_metrics(backend_pid)
    hb_age = _heartbeat_age_s()
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc),
        "queue": {
            "queued": queued, "active": claimed, "failed_total": failed_recent,
        },
        "active_job": active,
        "worker": {
            "running":          worker_pid is not None,
            "pid":              worker_pid,
            "heartbeat_age_s":  hb_age,
            "stale": (hb_age is not None and hb_age > 30.0),
            "metrics":          worker_metrics,
        },
        "backend": {
            "pid": backend_pid,
            "metrics": backend_metrics,
        },
    }


@router.get("/queue")
async def queue(
    request: Request,
    status_in: Optional[str] = Query(default=None,
                                            description="comma-sep list"),
    limit: int = Query(default=50, ge=1, le=500),
    auth=Depends(require_admin_token),
):
    db = _get_db()
    q: Dict[str, Any] = {"worker_queue": True}
    if status_in:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}
    cur = db[JOBS_COLL].find(q, {"_id": 0, "log": 0}) \
                              .sort([("queued_at", -1)]).limit(limit)
    jobs = [d async for d in cur]
    return {"ok": True, "n": len(jobs), "jobs": jobs}


@router.post("/cancel/{job_id}")
async def cancel(job_id: str, request: Request,
                    auth=Depends(require_admin_token)):
    db = _get_db()
    job = await db[JOBS_COLL].find_one(
        {"job_id": job_id, "worker_queue": True}, {"_id": 0})
    if not job:
        raise HTTPException(404, f"no worker job with id {job_id}")
    if job["status"] not in ("queued", "claimed", "running"):
        raise HTTPException(409, f"job is already {job['status']}")
    pid = job.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"SIGTERM failed: {e!r}")
    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"status": "cancelled",
                    "finished_at": datetime.now(timezone.utc)}})
    await audit_log(request, action="worker_cancel",
                      params={"job_id": job_id, "pid": pid}, **auth)
    return {"ok": True, "job_id": job_id, "killed_pid": pid}


# ── Testing-mode toggle (pause scheduler in the backend pod) ──────────
@router.get("/testing-mode")
async def get_testing_mode(request: Request,
                                auth=Depends(require_admin_token)):
    """Reports whether the APScheduler is paused via the in-process
    flag. Falls back to the TESTING_MODE env var when the scheduler is
    unavailable (e.g. early in app startup)."""
    enabled = False
    state = "unknown"
    try:
        from server import scheduler  # local import to avoid cycle at boot
        from apscheduler.schedulers.base import (
            STATE_RUNNING, STATE_PAUSED, STATE_STOPPED,
        )
        if scheduler is None:
            state = "not-initialised"
            enabled = os.environ.get("TESTING_MODE", "0") == "1"
        elif scheduler.state == STATE_PAUSED:
            state = "paused"
            enabled = True
        elif scheduler.state == STATE_STOPPED:
            state = "stopped"
            enabled = True
        elif scheduler.state == STATE_RUNNING:
            state = "running"
            enabled = False
        else:
            state = f"state={scheduler.state}"
    except Exception as e:  # noqa: BLE001
        state = f"error: {type(e).__name__}"
    return {"ok": True, "enabled": enabled, "scheduler_state": state}


@router.post("/testing-mode")
async def set_testing_mode(body: Dict[str, Any], request: Request,
                                auth=Depends(require_admin_token)):
    """Pause or resume the scheduler at runtime, no restart needed.

    body = {"enabled": true|false}

    When enabled=true we call `scheduler.pause()` which suspends ALL
    jobs but keeps queued state intact. When enabled=false we call
    `scheduler.resume()` to restore live sync. The pause is in-process
    only — restart will re-read TESTING_MODE from the env.
    """
    want_enabled = bool(body.get("enabled"))
    try:
        from server import scheduler
        from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"scheduler unavailable: {e!r}")
    if scheduler is None:
        raise HTTPException(503, "scheduler not initialised")
    try:
        if want_enabled:
            if scheduler.state == STATE_RUNNING:
                scheduler.pause()
                action = "paused"
            elif scheduler.state == STATE_PAUSED:
                action = "already-paused"
            else:
                action = f"left at state={scheduler.state}"
        else:
            if scheduler.state == STATE_PAUSED:
                scheduler.resume()
                action = "resumed"
            elif scheduler.state == STATE_RUNNING:
                action = "already-running"
            else:
                # stopped — restart
                scheduler.start()
                action = "restarted"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"toggle failed: {e!r}")
    await audit_log(request, action="testing_mode_toggle",
                      params={"want_enabled": want_enabled,
                                  "scheduler_action": action}, **auth)
    return {"ok": True, "enabled": want_enabled,
              "scheduler_action": action,
              "scheduler_state": scheduler.state}
