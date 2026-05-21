"""
Job runner — launches allowlisted Python scripts as background subprocesses.

Hard constraints:
  - only modules in policy.ALLOWED_JOBS may be spawned
  - only flags in that module's allowed-args list are passed through
  - all stdout/stderr captured per-job into emergent_admin_jobs collection
  - jobs run as the same UID as the FastAPI process; NO sudo, no shell=True
  - command is always built as a list[str], never interpreted by a shell
  - cwd locked to /app/backend (or /var/www/app/backend in prod)
"""
from __future__ import annotations
import asyncio
import os
import shlex
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, \
                       Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db
from .policy import ALLOWED_JOBS, job_allowed, job_args_allowed

JOBS_COLL = "emergent_admin_jobs"
router = APIRouter()


def _backend_cwd() -> str:
    """Locked cwd for subprocesses."""
    for p in ("/var/www/app/backend", "/app/backend"):
        if os.path.isdir(p):
            return p
    raise RuntimeError("backend cwd not found")


def _running_pids() -> Dict[str, int]:
    """Lazy in-process registry of pids for active jobs."""
    if not hasattr(_running_pids, "_reg"):
        _running_pids._reg = {}  # type: ignore[attr-defined]
    return _running_pids._reg  # type: ignore[attr-defined]


# ── Models ────────────────────────────────────────────────────────────────
class RunBody(BaseModel):
    module: str = Field(..., description="One of policy.ALLOWED_JOBS")
    args:   List[str] = Field(default_factory=list,
                                description="Pre-split argv list, e.g. "
                                             "['--league','MLB','--dry-run']")


class CancelBody(BaseModel):
    confirm: bool = False


# ── Runner ────────────────────────────────────────────────────────────────
async def _run_job(job_id: str, module: str, args: List[str]) -> None:
    db = _get_db()
    started = datetime.now(timezone.utc)
    cmd = [sys.executable, "-m", module, *args]
    cwd = _backend_cwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = cwd + ":" + env.get("PYTHONPATH", "")
    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"status": "running", "started_at": started, "pid": None}})
    try:
        # shell=False, list-form argv — no shell interpolation possible
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _running_pids()[job_id] = proc.pid
        await db[JOBS_COLL].update_one(
            {"job_id": job_id},
            {"$set": {"pid": proc.pid}})
        # Stream stdout into the doc in chunks
        log_chunks: List[str] = []
        assert proc.stdout is not None
        async for line in proc.stdout:
            try:
                log_chunks.append(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            # Periodically flush to mongo every 50 lines
            if len(log_chunks) >= 50:
                await db[JOBS_COLL].update_one(
                    {"job_id": job_id},
                    {"$push": {"log": {"$each": log_chunks}}})
                log_chunks = []
        rc = await proc.wait()
        if log_chunks:
            await db[JOBS_COLL].update_one(
                {"job_id": job_id},
                {"$push": {"log": {"$each": log_chunks}}})
        await db[JOBS_COLL].update_one(
            {"job_id": job_id},
            {"$set": {
                "status":   "succeeded" if rc == 0 else "failed",
                "exit_code": rc,
                "finished_at": datetime.now(timezone.utc),
            }})
    except Exception as e:
        await db[JOBS_COLL].update_one(
            {"job_id": job_id},
            {"$set": {"status": "errored",
                        "error":  repr(e),
                        "finished_at": datetime.now(timezone.utc)}})
    finally:
        _running_pids().pop(job_id, None)


# ── Endpoints ─────────────────────────────────────────────────────────────
@router.post("/run")
async def run_job(body: RunBody, request: Request,
                     bg: BackgroundTasks,
                     auth=Depends(require_admin_token)):
    if not job_allowed(body.module):
        raise HTTPException(403, f"job module not in allowlist or disabled: "
                                    f"{body.module}")
    ok, rejected = job_args_allowed(body.module, body.args)
    if not ok:
        raise HTTPException(400, f"rejected args (not in allowlist): "
                                    f"{rejected}")
    job_id = str(uuid.uuid4())
    doc = {
        "job_id":      job_id,
        "module":      body.module,
        "args":        body.args,
        "status":      "queued",
        "queued_at":   datetime.now(timezone.utc),
        "agent_id":    auth["agent_id"],
        "token_hash":  auth["token_hash"],
        "log":         [],
    }
    await _get_db()[JOBS_COLL].insert_one(doc)
    await audit_log(request, action="job_run",
                      params={"module": body.module, "args": body.args,
                                "job_id": job_id},
                      response_summary={"job_id": job_id}, **auth)
    # Kick off in background — survives request lifecycle via asyncio task
    asyncio.create_task(_run_job(job_id, body.module, body.args))
    return {"ok": True, "job_id": job_id, "status": "queued"}


@router.get("")
@router.get("/")
async def list_jobs(request: Request,
                       status_filter: Optional[str] = Query(None, alias="status"),
                       limit: int = Query(50, ge=1, le=500),
                       auth=Depends(require_admin_token)):
    q: Dict[str, Any] = {}
    if status_filter:
        q["status"] = status_filter
    db = _get_db()
    docs = []
    cur = db[JOBS_COLL].find(q, {"_id": 0, "log": 0}).sort(
        [("queued_at", -1)]).limit(limit)
    async for d in cur:
        docs.append(d)
    await audit_log(request, action="job_list",
                      params={"status": status_filter, "limit": limit},
                      response_summary={"n": len(docs)}, **auth)
    return {"ok": True, "jobs": docs}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request,
                     include_log: bool = Query(False),
                     auth=Depends(require_admin_token)):
    db = _get_db()
    proj = {"_id": 0}
    if not include_log:
        proj["log"] = 0
    doc = await db[JOBS_COLL].find_one({"job_id": job_id}, proj)
    if not doc:
        raise HTTPException(404, f"job {job_id} not found")
    await audit_log(request, action="job_get",
                      params={"job_id": job_id, "include_log": include_log},
                      **auth)
    return {"ok": True, "job": doc}


@router.get("/{job_id}/log")
async def get_job_log(job_id: str, request: Request,
                          tail: int = Query(500, ge=1, le=10000),
                          auth=Depends(require_admin_token)):
    db = _get_db()
    doc = await db[JOBS_COLL].find_one({"job_id": job_id},
                                          {"_id": 0, "log": 1, "status": 1})
    if not doc:
        raise HTTPException(404, f"job {job_id} not found")
    log = doc.get("log") or []
    await audit_log(request, action="job_log",
                      params={"job_id": job_id, "tail": tail}, **auth)
    return {"ok": True, "status": doc.get("status"),
             "lines": log[-tail:], "total_lines": len(log)}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, body: CancelBody, request: Request,
                         auth=Depends(require_admin_token)):
    if not body.confirm:
        raise HTTPException(400, "set confirm=true to cancel a running job")
    pid = _running_pids().get(job_id)
    if pid is None:
        raise HTTPException(404, f"job {job_id} not active or already finished")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as e:
        raise HTTPException(500, f"failed to SIGTERM pid={pid}: {e!r}")
    await _get_db()[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"status": "cancelled",
                    "finished_at": datetime.now(timezone.utc)}})
    await audit_log(request, action="job_cancel",
                      params={"job_id": job_id, "pid": pid}, **auth)
    return {"ok": True, "job_id": job_id, "killed_pid": pid}
