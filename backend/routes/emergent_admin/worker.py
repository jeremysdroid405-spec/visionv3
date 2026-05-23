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
