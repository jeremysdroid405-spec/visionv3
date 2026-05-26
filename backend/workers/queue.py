"""
Mongo-backed job queue for the research worker.

A "research job" is a heavy/long-running compute task we want to keep
*out* of the FastAPI event loop:
  • optimizer sweeps
  • historical replay (full pipeline)
  • grid sweeps
  • candidate generation scripts

The queue uses the existing `emergent_admin_jobs` collection so the
existing /jobs/{id} UI keeps working unchanged. A job is "ours" when it
has `worker_queue=True`. The worker atomically claims jobs by flipping
status `queued → claimed` with `findOneAndUpdate`, which is safe even
if multiple worker processes are accidentally started.

`HEAVY_MODULES` lists script modules that must route through the
worker. Everything else still runs immediately via the existing
in-process subprocess path in routes/emergent_admin/jobs.py.

This module is import-safe: it ONLY uses motor + bson, no FastAPI.
"""
from __future__ import annotations
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

JOBS_COLL = "emergent_admin_jobs"
WORKER_ID = os.environ.get("RESEARCH_WORKER_ID", "research-worker-1")
WORKER_HEARTBEAT_PATH = os.environ.get("RW_HEARTBEAT_PATH",
                                            "/tmp/research_worker.heartbeat")
# A queued job becomes "orphaned" (no worker alive) when the heartbeat
# is stale beyond this many seconds. The API refuses enqueue in that
# state so jobs don't pile up invisibly.
WORKER_STALE_AFTER_S = float(os.environ.get("RW_STALE_AFTER_S", "30"))

# Default resource caps applied to every spawned subprocess.
DEFAULT_RESOURCE_CAPS: Dict[str, Any] = {
    "nice":            int(os.environ.get("RW_NICE", "10")),
    "rlimit_as_bytes": int(os.environ.get("RW_RLIMIT_AS_BYTES",
                                            str(4 * 1024**3))),   # 4 GB
    "timeout_seconds": int(os.environ.get("RW_TIMEOUT_SECONDS",
                                            str(2 * 60 * 60))),   # 2 h
    "oom_score_adj":   int(os.environ.get("RW_OOM_SCORE_ADJ", "500")),
}

# Modules that MUST be routed through the worker queue rather than
# spawned inline. Anything not in this set still runs the old way so
# light preflight checks / coverage reads stay snappy.
HEAVY_MODULES = frozenset({
    "scripts.research.grid_sweep",
    "scripts.research.run_optimizer_cli",
    "scripts.sgo.probe_nfl_data",
    "scripts.sgo.build_pp_research_core",
    "scripts.sgo.historical_full_pipeline_replay",
    "scripts.sgo.historical_gate_replay_grid",
    "scripts.sgo.build_historical_outcomes",
    "scripts.sgo.build_historical_model_features",
    "scripts.sgo.build_historical_model_predictions",
    "scripts.sgo.score_historical_with_live_mlb_hf",
    "scripts.sgo.ingest_historical_player_stats",
    "scripts.sgo.ingest_bdl_mlb_season",
    "scripts.sgo.reshape_sgo_to_replay_odds",
})


def _client() -> AsyncIOMotorClient:
    """Lazy single connection per process. The worker process and the
    FastAPI process each maintain their own client; that's intentional."""
    if not hasattr(_client, "_c"):
        _client._c = AsyncIOMotorClient(os.environ["MONGO_URL"])  # type: ignore[attr-defined]
    return _client._c  # type: ignore[attr-defined]


def db() -> AsyncIOMotorDatabase:
    return _client()[os.environ["DB_NAME"]]


def is_heavy(module: str) -> bool:
    return module in HEAVY_MODULES


async def enqueue(
    job_id: str,
    module: str,
    args: List[str],
    *,
    agent_id: str = "",
    token_hash: str = "",
    kind: str = "script",
    payload: Optional[Dict[str, Any]] = None,
    resource_caps: Optional[Dict[str, Any]] = None,
    require_worker: bool = True,
) -> Dict[str, Any]:
    """Insert a worker-routed job doc. The worker daemon picks it up.

    When `require_worker=True` (default) we refuse the enqueue if no
    research_worker daemon has touched the heartbeat file within the
    `WORKER_STALE_AFTER_S` window. This prevents the silent failure mode
    where jobs pile up in `queued` forever because the worker service
    isn't installed on the host.
    """
    if require_worker:
        import os as _os
        try:
            st = _os.stat(WORKER_HEARTBEAT_PATH)
            age = time.time() - st.st_mtime
        except FileNotFoundError:
            age = None
        if age is None or age > WORKER_STALE_AFTER_S:
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(
                503,
                detail=(
                    "research_worker is not running. Last heartbeat: "
                    f"{'never' if age is None else f'{age:.0f}s ago'} "
                    f"(stale threshold {WORKER_STALE_AFTER_S:.0f}s). "
                    "Refusing to enqueue — jobs would pile up forever. "
                    "Fix: `sudo supervisorctl reread && sudo supervisorctl "
                    "update research_worker && sudo supervisorctl start "
                    "research_worker`. If the service is missing, run "
                    "/app/scripts/install_research_worker.sh on the host."
                ),
            )
    doc = {
        "job_id":        job_id,
        "module":        module,
        "args":          args,
        "kind":          kind,
        "payload":       payload or {},
        "status":        "queued",
        "worker_queue":  True,
        "queued_at":     datetime.now(timezone.utc),
        "agent_id":      agent_id,
        "token_hash":    token_hash,
        "log":           [],
        "resource_caps": {**DEFAULT_RESOURCE_CAPS, **(resource_caps or {})},
    }
    await db()[JOBS_COLL].insert_one(doc)
    return {"ok": True, "job_id": job_id, "status": "queued"}


async def atomic_claim(worker_id: str = WORKER_ID) -> Optional[Dict[str, Any]]:
    """Pop one queued worker-routed job, marking it `claimed`.

    Returns the full job doc (with `_id` stripped) or None if nothing
    queued. Concurrency-safe via `findOneAndUpdate` — only one caller
    can transition any given doc from `queued → claimed`.
    """
    doc = await db()[JOBS_COLL].find_one_and_update(
        {"status": "queued", "worker_queue": True},
        {"$set": {"status":      "claimed",
                    "claimed_at":  datetime.now(timezone.utc),
                    "claimed_by":  worker_id}},
        sort=[("queued_at", 1)],
        projection={"_id": 0},
    )
    return doc


async def mark_running(job_id: str, pid: int, cmd: List[str]) -> None:
    await db()[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"status":     "running",
                    "started_at": datetime.now(timezone.utc),
                    "pid":        pid,
                    "cmd":        cmd}},
    )


async def finalize(
    job_id: str, *,
    status: str, exit_code: Optional[int] = None,
    error: Optional[str] = None, traceback_text: Optional[str] = None,
    tail_preview: Optional[List[str]] = None,
    rss_peak_bytes: Optional[int] = None,
    cpu_seconds: Optional[float] = None,
) -> None:
    update: Dict[str, Any] = {
        "status":        status,
        "finished_at":   datetime.now(timezone.utc),
    }
    if exit_code is not None:       update["exit_code"]       = exit_code
    if error is not None:           update["error"]           = error
    if traceback_text is not None:  update["traceback"]       = traceback_text
    if tail_preview is not None:    update["tail_preview"]    = tail_preview
    if rss_peak_bytes is not None:  update["rss_peak_bytes"]  = rss_peak_bytes
    if cpu_seconds is not None:     update["cpu_seconds"]     = cpu_seconds
    await db()[JOBS_COLL].update_one({"job_id": job_id}, {"$set": update})


async def append_log(job_id: str, lines: List[str]) -> None:
    if not lines:
        return
    await db()[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$push": {"log": {"$each": lines}}},
    )


async def heartbeat_job(job_id: str, rss_bytes: int = 0) -> None:
    """Update the `last_heartbeat_at` watermark on a running job so the
    reconciler can distinguish 'truly working' from 'zombie / crashed'.
    Called from research_worker every few seconds during a job's run.
    """
    await db()[JOBS_COLL].update_one(
        {"job_id": job_id},
        {"$set": {"last_heartbeat_at": datetime.now(timezone.utc),
                    "rss_bytes_current": int(rss_bytes)}},
    )


async def reconcile_zombies(stale_after_s: float = 15 * 60) -> int:
    """Finalize any `running`/`claimed` worker job whose
    `last_heartbeat_at` is older than `stale_after_s`.

    Returns count of zombie jobs reaped. Called by the worker daemon
    every few minutes. Idempotent / safe to run from many places at
    once because each update narrows by job_id + status condition.

    Why this exists: if research_worker is killed mid-job by the
    kernel (OOM SIGKILL) the parent never gets a chance to write
    `finalize(status='errored', ...)`. On the next worker startup
    `_recover_stuck_jobs` catches anything we owned, but ONLY at
    startup — between starts, the UI sees the job hanging in
    'running' for hours. Periodic reconciliation fixes that.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - stale_after_s
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    res = await db()[JOBS_COLL].update_many(
        {"status": {"$in": ["claimed", "running"]},
          "worker_queue": True,
          "$or": [
              {"last_heartbeat_at": {"$lt": cutoff_dt}},
              # Job claimed but never wrote a heartbeat (immediate crash)
              {"last_heartbeat_at": {"$exists": False},
               "claimed_at": {"$lt": cutoff_dt}},
          ]},
        {"$set": {"status":      "errored",
                    "error":       (f"zombie reconciled "
                                          f"(no heartbeat for ≥"
                                          f"{int(stale_after_s)}s)"),
                    "finished_at": datetime.now(timezone.utc),
                    "reconciled":  True}},
    )
    return int(res.modified_count or 0)


async def queue_depth() -> int:
    return await db()[JOBS_COLL].count_documents(
        {"status": "queued", "worker_queue": True})


async def active_job() -> Optional[Dict[str, Any]]:
    """Returns the single in-flight worker job, if any."""
    return await db()[JOBS_COLL].find_one(
        {"status": {"$in": ["claimed", "running"]}, "worker_queue": True},
        sort=[("started_at", -1)],
        projection={"_id": 0, "log": 0},
    )


async def list_recent(limit: int = 50,
                          status_in: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    q: Dict[str, Any] = {"worker_queue": True}
    if status_in:
        q["status"] = {"$in": status_in}
    cur = db()[JOBS_COLL].find(q, {"_id": 0, "log": 0}) \
                              .sort([("queued_at", -1)]).limit(limit)
    return [d async for d in cur]
