"""
Job runner — launches allowlisted Python scripts as background subprocesses.

Hard constraints:
  - only modules in policy.ALLOWED_JOBS may be spawned
  - only flags in that module's allowed-args list are passed through
  - all stdout/stderr captured per-job into emergent_admin_jobs collection
  - jobs run as the same UID as the FastAPI process; NO sudo, no shell=True
  - command is always built as a list[str], never interpreted by a shell
  - cwd locked to /app/backend (or /var/www/app/backend in prod)

2026-05-21 — rewritten to fix silent-failure bug:
  • merged stderr into stdout via PIPE (was already correct) plus a periodic
    1-second flusher so short-lived failures appear in the UI even if they
    don't reach the 50-line batch threshold
  • full traceback captured on spawn errors (not just repr(e))
  • the last 200 lines of output written to the job doc as `tail_preview`
    so /jobs/{id} (without ?include_log=true) surfaces what failed
  • Python logger.error() called on any non-zero rc, with the tail attached,
    so journalctl / supervisor backend logs always see job failures
  • `traceback` field populated on the job doc for errored / failed jobs
"""
from __future__ import annotations
import asyncio
import logging
import os
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, \
                       Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db
from .policy import ALLOWED_JOBS, job_allowed, job_args_allowed

logger = logging.getLogger("emergent_admin.jobs")

JOBS_COLL = "emergent_admin_jobs"
TAIL_PREVIEW_LINES = 200
FLUSH_LINE_THRESHOLD = 25
FLUSH_SECONDS = 1.0

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
async def _flush(db, job_id: str, lines: List[str]) -> None:
    if not lines:
        return
    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$push": {"log": {"$each": lines}}})


async def _run_job(job_id: str, module: str, args: List[str]) -> None:
    db = _get_db()
    started = datetime.now(timezone.utc)
    cmd = [sys.executable, "-u", "-m", module, *args]   # -u for unbuffered stdout
    cwd = _backend_cwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = cwd + ":" + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Ensure system binaries are visible (venv-launched python often scrubs
    # PATH). Without /usr/bin many scripts that shell-out to git/ssh/etc.
    # would silently fail.
    if "/usr/bin" not in env.get("PATH", "").split(":"):
        env["PATH"] = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                          "/sbin:/bin:" + env.get("PATH", ""))

    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"status": "running", "started_at": started,
                    "pid": None, "cmd": cmd}})

    try:
        # shell=False, list-form argv — no shell interpolation possible.
        # Stderr is merged into stdout so we capture a single chronological
        # stream (the Python -u + PYTHONUNBUFFERED=1 above keep ordering sane).
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as spawn_exc:
        tb = traceback.format_exc()
        logger.error("[job %s] spawn failed: %s\n%s", job_id,
                          spawn_exc, tb)
        await db[JOBS_COLL].update_one(
            {"job_id": job_id},
            {"$set": {"status": "errored",
                        "error": repr(spawn_exc),
                        "traceback": tb,
                        "tail_preview": tb.splitlines()[-TAIL_PREVIEW_LINES:],
                        "finished_at": datetime.now(timezone.utc)}})
        return

    _running_pids()[job_id] = proc.pid
    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"pid": proc.pid}})

    # In-memory rolling tail for the job doc + Python logger fallback.
    rolling_tail: List[str] = []
    log_chunks: List[str] = []
    last_flush = time.monotonic()

    try:
        assert proc.stdout is not None
        while True:
            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                line_bytes = b""
            if line_bytes:
                line = line_bytes.decode("utf-8", errors="replace")
                log_chunks.append(line)
                rolling_tail.append(line)
                if len(rolling_tail) > TAIL_PREVIEW_LINES:
                    rolling_tail = rolling_tail[-TAIL_PREVIEW_LINES:]
            elif proc.returncode is None and not line_bytes:
                # No output for FLUSH_SECONDS but proc still alive — just
                # take this opportunity to flush whatever we have.
                pass
            now = time.monotonic()
            if (len(log_chunks) >= FLUSH_LINE_THRESHOLD
                    or (log_chunks and now - last_flush >= FLUSH_SECONDS)):
                await _flush(db, job_id, log_chunks)
                log_chunks = []
                last_flush = now
            if proc.returncode is not None and not line_bytes:
                # Drain any final bytes the streaming reader may have missed
                try:
                    remainder = await proc.stdout.read()
                    if remainder:
                        for line in remainder.decode(
                                "utf-8", errors="replace").splitlines(keepends=True):
                            log_chunks.append(line)
                            rolling_tail.append(line)
                except Exception:
                    pass
                break
            # If line was empty AND proc has exited, the next loop iteration
            # will see returncode is not None and break.
            if not line_bytes:
                # Poll proc.returncode by issuing wait() in a non-blocking way
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.01)
                except asyncio.TimeoutError:
                    pass
        if log_chunks:
            await _flush(db, job_id, log_chunks)
            log_chunks = []
        rc = proc.returncode if proc.returncode is not None else await proc.wait()
        rolling_tail = rolling_tail[-TAIL_PREVIEW_LINES:]
        status = "succeeded" if rc == 0 else "failed"
        update: Dict[str, Any] = {
            "status":       status,
            "exit_code":    rc,
            "finished_at":  datetime.now(timezone.utc),
            "tail_preview": rolling_tail,
        }
        if rc != 0:
            # Always log a failure summary to the Python logger so journalctl
            # / supervisor logs surface it. Include the last ~30 lines.
            tail_for_log = "".join(rolling_tail[-30:])
            logger.error(
                "[job %s] FAILED rc=%s module=%s args=%s\n--- tail ---\n%s",
                job_id, rc, module, args, tail_for_log)
        else:
            logger.info("[job %s] OK rc=%s module=%s args=%s",
                          job_id, rc, module, args)
        await db[JOBS_COLL].update_one({"job_id": job_id}, {"$set": update})
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception("[job %s] runner crashed", job_id)
        # Try to flush whatever's been collected
        try:
            if log_chunks:
                await _flush(db, job_id, log_chunks)
        except Exception:
            pass
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            pass
        await db[JOBS_COLL].update_one(
            {"job_id": job_id},
            {"$set": {"status":       "errored",
                        "error":        repr(e),
                        "traceback":    tb,
                        "tail_preview": rolling_tail[-TAIL_PREVIEW_LINES:],
                        "finished_at":  datetime.now(timezone.utc)}})
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
    logger.info("[job %s] queued module=%s args=%s by=%s",
                  job_id, body.module, body.args, auth.get("agent_id"))
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
    doc = await db[JOBS_COLL].find_one(
        {"job_id": job_id},
        {"_id": 0, "log": 1, "status": 1, "exit_code": 1,
          "error": 1, "traceback": 1, "tail_preview": 1})
    if not doc:
        raise HTTPException(404, f"job {job_id} not found")
    log = doc.get("log") or []
    await audit_log(request, action="job_log",
                      params={"job_id": job_id, "tail": tail}, **auth)
    return {"ok": True,
              "status":       doc.get("status"),
              "exit_code":    doc.get("exit_code"),
              "error":        doc.get("error"),
              "traceback":    doc.get("traceback"),
              "tail_preview": doc.get("tail_preview"),
              "lines":        log[-tail:],
              "total_lines":  len(log)}


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
    logger.info("[job %s] cancelled via SIGTERM pid=%s", job_id, pid)
    return {"ok": True, "job_id": job_id, "killed_pid": pid}
