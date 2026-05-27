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
from workers.queue import enqueue as worker_enqueue, is_heavy

logger = logging.getLogger("emergent_admin.jobs")

JOBS_COLL = "emergent_admin_jobs"
TAIL_PREVIEW_LINES = 200
FLUSH_LINE_THRESHOLD = 25
FLUSH_SECONDS = 1.0

router = APIRouter()


def _backend_cwd() -> str:
    """Locked cwd for subprocesses.

    Tries known install paths first, then `BACKEND_DIR` env override, then
    the directory of `backend/server.py` discovered via this module's
    location. Worst case falls back to `os.getcwd()` so the runner NEVER
    raises before reaching the DB update — silent failures were leaving
    jobs stuck in `queued` forever.
    """
    for p in ("/var/www/app/backend", "/app/backend"):
        if os.path.isdir(p):
            return p
    env_override = os.environ.get("BACKEND_DIR") or os.environ.get("APP_BACKEND_DIR")
    if env_override and os.path.isdir(env_override):
        return env_override
    # Discover relative to this file: routes/emergent_admin/jobs.py → backend/
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(3):
        candidate = os.path.dirname(here)
        if os.path.isfile(os.path.join(candidate, "server.py")):
            return candidate
        here = candidate
    return os.getcwd()


def _running_pids() -> Dict[str, int]:
    """Lazy in-process registry of pids for active jobs."""
    if not hasattr(_running_pids, "_reg"):
        _running_pids._reg = {}  # type: ignore[attr-defined]
    return _running_pids._reg  # type: ignore[attr-defined]


# Strong references to background runner tasks — without this,
# Python's asyncio is permitted to garbage-collect the task before it
# completes, which would leave the job stuck in "queued" forever.
# (Bit us once in prod; documented at docs.python.org/3/library/asyncio-task.html#asyncio.create_task)
_RUNNER_TASKS: set = set()


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
    # 2026-05-27 — Inline-job log cap (was unbounded $push). When a
    # LIGHT module runs in-process here, every line was being pushed
    # to the Mongo log array forever. Two failure modes:
    #   (a) doc grows past the 16 MB BSON ceiling → silent insert
    #       failure → log polling returns truncated/stale data
    #   (b) /jobs/{id}/log polling (1 s loop) reads the WHOLE array
    #       each tick → backend RSS climbs as motor buffers the
    #       full doc transiently. Combined with multiple browser
    #       tabs polling, this can pile up to GB+ before GC catches
    #       up.
    # Use $slice so the rolling buffer is hard-capped at LOG_CAP_LINES
    # and bump a lifetime counter for the UI. Same contract as
    # workers/queue.append_log().
    from workers.queue import LOG_CAP_LINES
    await db[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$push": {"log": {"$each": lines, "$slice": -LOG_CAP_LINES}},
          "$inc":  {"log_lines_total": len(lines)}})


async def _run_job(job_id: str, module: str, args: List[str]) -> None:
    """Top-level dispatcher. Wraps _run_job_impl with an outer try/except
    so that ANY exception — including pre-spawn errors like missing cwd,
    bad PYTHONPATH, Mongo down — surfaces on the job doc as `status="errored"`
    instead of leaving it silently stuck in `queued`.
    """
    try:
        await _run_job_impl(job_id, module, args)
    except Exception as outer:
        tb = traceback.format_exc()
        logger.exception("[job %s] top-level dispatch failed (pre-spawn)", job_id)
        # Best-effort write — if Mongo itself is the failure, this will
        # also fail and the supervisor log line above is the only record.
        try:
            await _get_db()[JOBS_COLL].update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "errored",
                    "error": repr(outer),
                    "traceback": tb,
                    "tail_preview": tb.splitlines()[-TAIL_PREVIEW_LINES:],
                    "finished_at": datetime.now(timezone.utc),
                }})
        except Exception as inner:
            logger.exception("[job %s] also failed to record the outer "
                                "failure: %r", job_id, inner)


async def _run_job_impl(job_id: str, module: str, args: List[str]) -> None:
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
    # 2026-05-26 — Server-side dedupe (defense in depth).
    # If an identical (module, args) job is already queued/claimed/
    # running, return that job_id INSTEAD of enqueueing a new one.
    # WHY: the AdminTesting Workflow tab's `drive()` runs every 1 s
    # and can fire `/jobs/run` multiple times in flight while the
    # first response is in-flight (frontend mutex closes that hole
    # going forward, but old browser tabs / external clients / future
    # bugs can still produce the storm). A prod log this morning
    # showed 87 identical reshape_sgo_to_replay_odds jobs queued in
    # under 2 minutes; the user reasonably perceived this as "the
    # worker is hung" because each one takes 10 s and the chain
    # never advanced.
    db = _get_db()
    existing = await db[JOBS_COLL].find_one(
        {"module": body.module,
          "args":   body.args,
          "status": {"$in": ["queued", "claimed", "running"]}},
        {"_id": 0, "job_id": 1, "status": 1, "queued_at": 1},
        sort=[("queued_at", -1)],
        max_time_ms=5_000,
    )
    if existing:
        logger.info("[job dedupe] reusing in-flight job %s (status=%s) "
                       "for module=%s args=%s — refused duplicate enqueue",
                       existing["job_id"], existing.get("status"),
                       body.module, body.args)
        return {"ok":         True,
                  "job_id":     existing["job_id"],
                  "status":     existing.get("status") or "queued",
                  "deduped":    True,
                  "routed_to":  "research_worker" if is_heavy(body.module) else "inline"}
    job_id = str(uuid.uuid4())
    # Heavy modules route through the dedicated research_worker (separate
    # process, resource-capped). The endpoint still returns immediately
    # with the job_id so the UI's polling path is unchanged.
    if is_heavy(body.module):
        await worker_enqueue(
            job_id, module=body.module, args=body.args,
            agent_id=auth["agent_id"], token_hash=auth["token_hash"],
            kind="script",
        )
        await audit_log(request, action="job_run",
                          params={"module": body.module, "args": body.args,
                                    "job_id": job_id, "queued_to": "research_worker"},
                          response_summary={"job_id": job_id,
                                                "queued_to": "research_worker"},
                          **auth)
        logger.info("[job %s] queued to research_worker module=%s args=%s by=%s",
                       job_id, body.module, body.args, auth.get("agent_id"))
        return {"ok": True, "job_id": job_id, "status": "queued",
                  "routed_to": "research_worker"}
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
    # Kick off in background — hold a STRONG reference so the asyncio
    # runtime cannot garbage-collect the task mid-execution.
    task = asyncio.create_task(_run_job(job_id, body.module, body.args))
    _RUNNER_TASKS.add(task)
    task.add_done_callback(_RUNNER_TASKS.discard)
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
    # 2026-05-26 — Cap server-side query time so a degenerate index
    # miss can't blow nginx's 60 s ceiling. The job doc is keyed off
    # `job_id` (indexed); 15 s is generous.
    doc = await db[JOBS_COLL].find_one(
        {"job_id": job_id}, proj, max_time_ms=15_000)
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
    # 2026-05-26 — Use `$slice` projection so Mongo sends ONLY the last
    # `tail` entries of the (already-capped) log array, NOT the whole
    # 2 000-line buffer × N bytes per line. Without this, every 1 s
    # poll from AdminTesting's WorkflowTab pulled the full doc (~1-2 MB
    # for a noisy script) → 60 polls/min × N concurrent steps quickly
    # overwhelmed Mongo + nginx → 504s during long-running steps.
    proj = {"_id": 0, "log": {"$slice": -tail},
              "status": 1, "exit_code": 1,
              "error": 1, "traceback": 1, "tail_preview": 1,
              "log_lines_total": 1}
    doc = await db[JOBS_COLL].find_one(
        {"job_id": job_id}, proj,
        max_time_ms=15_000)
    if not doc:
        raise HTTPException(404, f"job {job_id} not found")
    log = doc.get("log") or []
    await audit_log(request, action="job_log",
                      params={"job_id": job_id, "tail": tail}, **auth)
    return {"ok": True,
              "status":           doc.get("status"),
              "exit_code":        doc.get("exit_code"),
              "error":            doc.get("error"),
              "traceback":        doc.get("traceback"),
              "tail_preview":     doc.get("tail_preview"),
              "lines":            log,
              "total_lines":      len(log),
              # Lifetime line count (incremented by append_log on every
              # push so the UI can show "got 12,345 lines so far, tail
              # below" even though the rolling buffer is capped).
              "log_lines_total":  doc.get("log_lines_total") or len(log)}


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


@router.post("/_self_test")
async def runner_self_test(request: Request,
                                auth=Depends(require_admin_token)):
    """Diagnose the job runner WITHOUT going through the allowlist —
    inspects the host's runtime environment so silent-failure causes
    (missing cwd, no python executable, no scripts dir, Mongo down) are
    surfaced immediately.

    NO subprocess is actually spawned by this endpoint. Read-only.
    """
    import shutil
    db = _get_db()
    cwd = _backend_cwd()
    py = sys.executable
    py_exists = os.path.isfile(py)
    scripts_dir = os.path.join(cwd, "scripts")
    scripts_pkg = os.path.isdir(scripts_dir)
    sgo_pkg = os.path.isdir(os.path.join(scripts_dir, "sgo"))
    # Active-job registry health
    active_tasks = len(_RUNNER_TASKS)
    active_pids  = len(_running_pids())
    # Mongo connectivity
    try:
        mongo_ok = (await db.command("ping")).get("ok") == 1
    except Exception as e:
        mongo_ok = False
        mongo_err = repr(e)
    else:
        mongo_err = None
    # Stuck-queued count
    stuck_q = await db[JOBS_COLL].count_documents({"status": "queued"})
    payload = {
        "ok": all([py_exists, scripts_pkg, sgo_pkg, mongo_ok]),
        "cwd": cwd,
        "cwd_exists": os.path.isdir(cwd),
        "python_executable": py,
        "python_exists": py_exists,
        "scripts_pkg_present": scripts_pkg,
        "scripts_sgo_pkg_present": sgo_pkg,
        "active_tasks_in_memory": active_tasks,
        "active_pids_in_memory": active_pids,
        "mongo_ok": mongo_ok,
        "mongo_err": mongo_err,
        "queued_jobs_count": stuck_q,
        "shell_python": shutil.which("python") or shutil.which("python3"),
        "env_pythonpath": os.environ.get("PYTHONPATH", ""),
        "env_path": os.environ.get("PATH", "")[:200],
        "ts": datetime.now(timezone.utc),
    }
    await audit_log(request, action="job_self_test",
                       params={}, response_summary={"ok": payload["ok"]}, **auth)
    return payload


class ReconcileBody(BaseModel):
    older_than_seconds: int = Field(default=120, ge=10, le=86400)


@router.post("/_reconcile_stuck")
async def reconcile_stuck(body: ReconcileBody, request: Request,
                                auth=Depends(require_admin_token)):
    """Mark all `status=queued` jobs older than N seconds as errored.
    Useful after a backend OOM/restart that lost in-flight tasks.
    Does NOT touch jobs currently running — those have a pid registered
    in memory."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=body.older_than_seconds)
    db = _get_db()
    # Refuse to clobber currently-active jobs. We can't extract job_id from
    # Task objects safely, so we filter on the active-pids registry instead
    # — those entries definitely correspond to live subprocesses.
    live_pid_ids = list(_running_pids().keys())
    result = await db[JOBS_COLL].update_many(
        {"status": "queued",
          "queued_at": {"$lt": cutoff},
          "job_id": {"$nin": live_pid_ids}},
        {"$set": {"status": "errored",
                    "error": "Task lost — backend restart or pre-spawn crash. "
                                "Check supervisor logs.",
                    "finished_at": datetime.now(timezone.utc),
                    "reconciled": True}})
    await audit_log(request, action="job_reconcile_stuck",
                       params={"older_than_seconds": body.older_than_seconds},
                       response_summary={"matched": result.matched_count,
                                              "modified": result.modified_count},
                       **auth)
    return {"ok": True, "matched": result.matched_count,
              "modified": result.modified_count,
              "live_pid_ids_protected": live_pid_ids}
