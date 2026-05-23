"""
research_worker — long-running daemon that drains the research job
queue, one job at a time. Sits outside the FastAPI process so heavy
work (optimizer sweeps, historical replay, grid sweeps) cannot starve
live-scoring / API request handling.

Run as a supervisor service:
    [program:research_worker]
    command=/root/.venv/bin/python -m workers.research_worker
    directory=/app/backend

Hard guarantees:
    • max_concurrent = 1  — serial by construction (single process,
                                 single in-flight subprocess).
    • Each job spawns a SUBPROCESS with:
        - nice +10
        - RLIMIT_AS = 4 GB (configurable via RW_RLIMIT_AS_BYTES)
        - oom_score_adj = +500 (kernel prefers killing us during OOM)
        - hard timeout = 2 h
    • Output streamed back into emergent_admin_jobs.log in real time.
    • Crash recovery: any "claimed"/"running" job for THIS worker_id
      that has no live pid at startup is force-finalized as errored.

The daemon ALSO populates rss_peak_bytes + cpu_seconds via psutil so
the UI can plot per-job resource cost.
"""
from __future__ import annotations
import asyncio
import logging
import os
import resource
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ensure we can import from /app/backend
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv  # noqa: E402
for env_path in ("/app/backend/.env", "/var/www/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from workers.queue import (  # noqa: E402
    WORKER_ID, JOBS_COLL, db,
    atomic_claim, mark_running, finalize, append_log, queue_depth,
)
from workers.live_sync_state import (  # noqa: E402
    worker_pause_for_job, worker_finish_job,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [research_worker:%(process)d] %(levelname)s %(message)s",
)
logger = logging.getLogger("research_worker")

POLL_INTERVAL_S    = float(os.environ.get("RW_POLL_INTERVAL_S", "2.0"))
HEARTBEAT_PATH     = os.environ.get("RW_HEARTBEAT_PATH",
                                       "/tmp/research_worker.heartbeat")
TAIL_PREVIEW_LINES = 300
FLUSH_LINE_THRESHOLD = 25
FLUSH_SECONDS        = 1.0


def _set_subprocess_limits(caps: Dict[str, Any]):
    """preexec_fn for spawned compute subprocesses.
    Applies nice, RLIMIT_AS, and oom_score_adj BEFORE exec().
    """
    try:
        os.nice(caps.get("nice", 10))
    except Exception:  # noqa: BLE001
        pass
    rlimit = caps.get("rlimit_as_bytes")
    if rlimit:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (rlimit, rlimit))
        except Exception:  # noqa: BLE001
            pass
    oom = caps.get("oom_score_adj")
    if oom is not None:
        try:
            with open(f"/proc/{os.getpid()}/oom_score_adj", "w") as fh:
                fh.write(str(oom))
        except Exception:  # noqa: BLE001
            pass


def _backend_cwd() -> str:
    for p in ("/app/backend", "/var/www/app/backend"):
        if os.path.isdir(p):
            return p
    return os.getcwd()


async def _run_subprocess(job: Dict[str, Any]) -> Dict[str, Any]:
    """Spawn the job's CLI command, stream output to Mongo, enforce
    timeout. Returns a dict with status/exit_code/tail/resource stats."""
    job_id = job["job_id"]
    module = job["module"]
    args   = job.get("args") or []
    caps   = job.get("resource_caps") or {}
    timeout_s = float(caps.get("timeout_seconds", 7200))

    cwd = _backend_cwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = cwd + ":" + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if "/usr/bin" not in env.get("PATH", "").split(":"):
        env["PATH"] = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                          "/sbin:/bin:" + env.get("PATH", ""))

    cmd = [sys.executable, "-u", "-m", module, *args]
    logger.info("[%s] spawning: %s (caps=%s)", job_id, " ".join(cmd), caps)

    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        preexec_fn=lambda: _set_subprocess_limits(caps),
    )
    await mark_running(job_id, proc.pid, cmd)

    rolling_tail: List[str] = []
    log_chunks:   List[str] = []
    last_flush = time.monotonic()
    started   = time.monotonic()
    rss_peak  = 0

    # Optional psutil tracking — soft dependency; do nothing if absent.
    psproc = None
    try:
        import psutil
        psproc = psutil.Process(proc.pid)
    except Exception:  # noqa: BLE001
        psproc = None

    def _peek_rss() -> int:
        if psproc is None:
            return 0
        try:
            mi = psproc.memory_info()
            return int(mi.rss)
        except Exception:  # noqa: BLE001
            return 0

    assert proc.stdout is not None
    timed_out = False
    while True:
        if time.monotonic() - started > timeout_s:
            logger.error("[%s] timeout after %.0fs, killing pid=%d",
                              job_id, timeout_s, proc.pid)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            timed_out = True
            break

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

        rss_peak = max(rss_peak, _peek_rss())

        now = time.monotonic()
        if (len(log_chunks) >= FLUSH_LINE_THRESHOLD
                or (log_chunks and now - last_flush >= FLUSH_SECONDS)):
            await append_log(job_id, log_chunks)
            log_chunks = []
            last_flush = now

        if proc.returncode is not None and not line_bytes:
            # Drain
            try:
                remainder = await proc.stdout.read()
                if remainder:
                    for line in remainder.decode(
                            "utf-8", errors="replace").splitlines(keepends=True):
                        log_chunks.append(line)
                        rolling_tail.append(line)
            except Exception:  # noqa: BLE001
                pass
            break
        if not line_bytes:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                pass

    if log_chunks:
        await append_log(job_id, log_chunks)
    rc = proc.returncode if proc.returncode is not None else await proc.wait()

    cpu_seconds = None
    if psproc is not None:
        try:
            cts = psproc.cpu_times()
            cpu_seconds = float(cts.user + cts.system)
        except Exception:  # noqa: BLE001
            cpu_seconds = None

    if timed_out:
        return {"status": "timeout", "exit_code": rc,
                  "tail": rolling_tail[-TAIL_PREVIEW_LINES:],
                  "error": f"timeout after {timeout_s:.0f}s",
                  "rss_peak_bytes": rss_peak, "cpu_seconds": cpu_seconds}
    status = "succeeded" if rc == 0 else "failed"
    return {"status": status, "exit_code": rc,
              "tail": rolling_tail[-TAIL_PREVIEW_LINES:],
              "rss_peak_bytes": rss_peak, "cpu_seconds": cpu_seconds}


async def _process_job(job: Dict[str, Any]) -> None:
    job_id = job["job_id"]
    logger.info("[%s] claimed (module=%s args=%s)",
                  job_id, job.get("module"), job.get("args"))
    # ── Auto-pause live sync for the duration of this job ──────────
    try:
        await worker_pause_for_job(
            db(), job_id=job_id, worker_id=WORKER_ID)
        logger.info("[%s] live_sync paused", job_id)
    except Exception:  # noqa: BLE001
        logger.exception("[%s] worker_pause_for_job failed", job_id)
    try:
        outcome = await _run_subprocess(job)
        await finalize(
            job_id,
            status=outcome["status"],
            exit_code=outcome.get("exit_code"),
            error=outcome.get("error"),
            tail_preview=outcome.get("tail"),
            rss_peak_bytes=outcome.get("rss_peak_bytes"),
            cpu_seconds=outcome.get("cpu_seconds"),
        )
        logger.info("[%s] %s rc=%s rss_peak=%s cpu=%s",
                       job_id, outcome["status"], outcome.get("exit_code"),
                       outcome.get("rss_peak_bytes"), outcome.get("cpu_seconds"))
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        logger.exception("[%s] runner crashed", job_id)
        await finalize(
            job_id, status="errored", error=repr(e), traceback_text=tb,
            tail_preview=tb.splitlines()[-TAIL_PREVIEW_LINES:],
        )
    finally:
        # Try to auto-resume — only fires when queue is empty AND no
        # manual override. On crash the doc stays paused (per spec).
        try:
            depth = await queue_depth()
            await worker_finish_job(
                db(), job_id=job_id, worker_id=WORKER_ID,
                queue_depth=depth)
            logger.info("[%s] live_sync coordinator updated "
                          "(queue_depth=%d)", job_id, depth)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] worker_finish_job failed", job_id)


async def _recover_stuck_jobs() -> None:
    """On startup, force-finalize anything in claimed/running owned by us
    (we have no live child). Safe because max_concurrent=1."""
    res = await db()[JOBS_COLL].update_many(
        {"status": {"$in": ["claimed", "running"]},
          "worker_queue": True, "claimed_by": WORKER_ID},
        {"$set": {"status": "errored",
                    "error": "worker restarted while job was in-flight",
                    "finished_at": datetime.now(timezone.utc)}},
    )
    if res.modified_count:
        logger.warning("recovered %d stale claimed/running job(s)",
                          res.modified_count)


async def _touch_heartbeat() -> None:
    try:
        with open(HEARTBEAT_PATH, "w") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())
    except Exception:  # noqa: BLE001
        pass


_STOP = False


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _shutdown(sig):
        global _STOP
        logger.info("received %s, draining and exiting", sig)
        _STOP = True
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _shutdown, s.name)


async def main() -> int:
    logger.info("starting research_worker id=%s dir=%s py=%s",
                  WORKER_ID, _backend_cwd(), sys.executable)
    _install_signal_handlers(asyncio.get_event_loop())
    await _recover_stuck_jobs()
    while not _STOP:
        await _touch_heartbeat()
        try:
            job = await atomic_claim(WORKER_ID)
        except Exception as e:  # noqa: BLE001
            logger.exception("queue poll failed: %r", e)
            job = None
        if job is None:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        await _process_job(job)
    logger.info("worker shutdown complete (queued depth=%d)",
                  await queue_depth())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
