"""
Contract tests for the research_worker memory + heartbeat guarantees
added 2026-05-26 in response to "worker crashes and locks up".

Verified via source inspection (the runtime behaviour requires
spawning real subprocesses and exhausting RAM — flaky/slow in CI).

Pinned contracts:

  1. **Pre-emptive RSS kill** before the kernel SIGKILLs the child.
     Kernel SIGKILL is uncatchable → the parent loses the chance to
     write `finalize(status='failed')` → the job row stays
     `status='running'` forever from the UI's perspective.
     With the guard, we SIGTERM at ~95 % of the rlimit and record a
     clean "RSS guard tripped" status.

  2. **Per-job heartbeat** written to Mongo every ~5s while a job
     runs. Without this the periodic zombie reconciler can't tell
     "in-flight, working hard" from "kernel killed us 20 min ago".

  3. **Periodic reconciler** wired into the worker's main loop —
     not just at startup. Catches zombies that appear *while* the
     worker is alive (e.g. SIGKILL'd by `kill -9` outside our
     control).
"""
from __future__ import annotations
import inspect
import sys

sys.path.insert(0, "/app/backend")

from workers import research_worker


def test_rss_guard_threshold_ratio_is_configurable_and_safe():
    """The RSS kill ratio must be configurable via env and default to
    a value strictly between 0.5 and 1.0 (any lower starves real jobs,
    any higher races the kernel)."""
    val = research_worker.RSS_KILL_RATIO
    assert isinstance(val, float)
    assert 0.50 < val < 1.0, (
        f"RSS_KILL_RATIO must be in (0.50, 1.0) to avoid both starving "
        f"real jobs and racing the kernel — got {val}")


def test_worker_streams_invoke_rss_guard():
    """The subprocess monitor loop must terminate the child when RSS
    exceeds the threshold — and it must use SIGTERM (catchable) not
    SIGKILL (uncatchable). SIGKILL leaves the parent unable to write
    a clean error and the job stays 'running'."""
    src = inspect.getsource(research_worker._run_subprocess)
    assert "rss_kill_threshold" in src
    assert "proc.terminate()" in src, (
        "RSS guard must call proc.terminate() (SIGTERM), NOT proc.kill() "
        "(SIGKILL). Catchable signal preserves the parent's ability to "
        "log + finalize.")
    assert "RSS guard" in src or "rss_guard" in src.lower()


def test_worker_streams_write_per_job_heartbeat():
    """The subprocess loop must call `heartbeat_job(...)` on every
    JOB_HEARTBEAT_EVERY_S tick so the reconciler can tell aliveness
    from zombieness."""
    src = inspect.getsource(research_worker._run_subprocess)
    assert "heartbeat_job(" in src, (
        "expected per-job heartbeat write inside _run_subprocess — "
        "without it the zombie reconciler can't distinguish working "
        "from crashed")


def test_worker_main_loop_runs_periodic_reconciler():
    """The reconciler must fire from the main poll loop, NOT only at
    startup. A worker killed mid-job & restarted N hours later would
    have left the running row 'orphaned' the entire time."""
    src = inspect.getsource(research_worker.main)
    assert "reconcile_zombies(" in src, (
        "main loop must call reconcile_zombies() periodically — "
        "_recover_stuck_jobs() at startup alone leaves zombies "
        "visible to the UI between restarts")
    assert "RECONCILE_EVERY_S" in src


def test_oom_killed_path_returns_clean_status():
    """When the RSS guard trips, the function must return a structured
    `failed` outcome with a human-readable error — not propagate raw
    `proc.returncode`. This is what makes the failure visible in the
    UI instead of silently turning into 'errored: -15'."""
    src = inspect.getsource(research_worker._run_subprocess)
    # Look for the 'oom_killed' branch returning a status/error pair.
    assert "oom_killed" in src
    assert "RSS guard tripped" in src
