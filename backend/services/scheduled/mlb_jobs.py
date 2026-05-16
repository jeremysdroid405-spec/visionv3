"""
MLB Daily / Pre-Game Jobs — APScheduler-callable wrappers.
==========================================================
Replaces the host-cron shell scripts:
  * scripts/run_mlb_daily_pipeline.sh       → mlb_daily_pipeline()
  * scripts/run_mlb_pregame_lineups.sh      → mlb_pregame_lineups()

Every job here is:
  1. **Async** — runs in the same event loop as APScheduler.
  2. **Lock-protected** via `services.sync_lock.with_sync_lock` so it can
     never race the in-process master-sync, the delta engine, or another
     copy of itself triggered by accident.
  3. **Skip-on-busy** — `raise_if_locked=False` means a busy lock logs
     "SKIPPED" and exits cleanly. No blocking, no retry, no override.
  4. **Idempotent** — every underlying script is safe to re-run.
  5. **Side-effect-free w.r.t. models / scoring / gates / thresholds /
     tier routing / selection / μ / σ.** This file is execution-layer
     migration ONLY.

Single-source-of-truth for MLB pipeline ordering. The shell scripts
are kept as thin wrappers (manual rollback) but should be considered
deprecated; production scheduling is in `server.py`.

NOTE: imports are deferred inside each function so a bug in one job
script never prevents server.py from booting.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# Make sure /app/backend is on sys.path for the script-imports below.
sys.path.insert(0, "/app/backend")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _get_db() -> Any:
    """Each job opens its own short-lived motor client. APScheduler
    workers may run on background threads / loops; sharing a global
    client would be fragile."""
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli, cli[os.environ["DB_NAME"]]


async def _run_locked(
    lock_key: str,
    *,
    ttl_seconds: int,
    holder: str,
    body: Callable[..., Any],
    db,
) -> Dict[str, Any]:
    """Wrap any async body in `with_sync_lock`. Skips on busy."""
    from services.sync_lock import with_sync_lock
    started = datetime.now(timezone.utc)
    async with with_sync_lock(
        db, lock_key, ttl_seconds=ttl_seconds, holder=holder,
        raise_if_locked=False,
    ) as handle:
        if handle is None:
            logger.warning(
                "[MLB_JOB] SKIPPED %s — lock %s busy", holder, lock_key,
            )
            return {"status": "skipped", "reason": "lock_busy",
                    "lock_key": lock_key, "holder": holder}
        try:
            result = await body() or {}
            return {
                "status": "ok",
                "lock_key": lock_key,
                "holder": holder,
                "duration_s": round(
                    (datetime.now(timezone.utc) - started).total_seconds(), 2),
                **result,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[MLB_JOB] FAILED %s on lock %s: %s",
                holder, lock_key, exc,
            )
            return {
                "status": "failed",
                "lock_key": lock_key,
                "holder": holder,
                "error": repr(exc),
                "traceback": traceback.format_exc()[-1500:],
            }


async def _record_history(db, job_id: str, payload: Dict[str, Any]) -> None:
    """Append a row to `sync_history` so the health endpoint can
    surface job runtimes / failures.  Best-effort — never raises."""
    try:
        await db["sync_history"].insert_one({
            "job_id": job_id,
            "completed_at": datetime.now(timezone.utc),
            **payload,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MLB_JOB] sync_history write failed: %s", exc)


# ---------------------------------------------------------------------------
# Step bodies — direct Python imports of the script logic.
# Each step returns a small dict of metrics; never raises (we let the
# orchestrator's try/except capture errors per-step so one failure
# doesn't poison the rest of the pipeline).
# ---------------------------------------------------------------------------
async def _step_lineup_ingest(db, *, date: str) -> Dict[str, Any]:
    from scripts.ingest_mlb_projected_lineups import ingest as _ingest
    counters = await _ingest(db, date)
    logger.info("[MLB_JOB] lineup_ingest counters=%s", counters)
    return {"lineup_ingest": counters}


async def _step_lineup_monitor(db) -> Dict[str, Any]:
    """Read-only coverage probe — pulls the same numbers the
    /api/health/sync endpoint would surface."""
    from scripts.monitor_mlb_lineup_coverage import _snapshot
    snap = await _snapshot(db, datetime.now(timezone.utc))
    summary = {
        "active_total":      snap["active_total"],
        "active_with_bo":    snap["active_with_bo"],
        "active_tb":         snap["active_tb"],
        "active_confirmed":  snap["active_confirmed"],
    }
    logger.info("[MLB_JOB] lineup_monitor %s", summary)
    return {"lineup_monitor": summary}


async def _step_statcast_ingest(db, *, start: str, end: str) -> Dict[str, Any]:
    from scripts.mlb_statcast_ingest import ingest_range
    res = await ingest_range(db, start=start, end=end, dry_run=False, chunk_days=7)
    logger.info("[MLB_JOB] statcast_ingest %s..%s -> %s", start, end, res)
    return {"statcast_ingest": res}


async def _step_build_batter_features(db) -> Dict[str, Any]:
    from scripts.mlb_statcast_build_features import build_features
    res = await build_features(db, since=None, player=None, dry_run=False) or {}
    return {"batter_features": res}


async def _step_build_pitcher_features(db) -> Dict[str, Any]:
    from scripts.mlb_statcast_build_pitcher_features import build
    res = await build(db, since=None, dry_run=False) or {}
    return {"pitcher_features": res}


async def _step_backfill_pitcher_context(db) -> Dict[str, Any]:
    from scripts.mlb_backfill_pitcher_context import backfill
    res = await backfill(db, since=None, dry_run=False) or {}
    return {"pitcher_context_backfill": res}


async def _step_build_identity_map(db) -> Dict[str, Any]:
    from scripts.build_mlb_player_identity_map import build
    res = await build(db, dry=False) or {}
    return {"identity_map": res}


async def _step_validate_statcast(db) -> Dict[str, Any]:
    from scripts.mlb_statcast_validate import validate
    checks = await validate(db) or []
    n_pass = sum(1 for c in checks if c.get("ok"))
    logger.info(
        "[MLB_JOB] statcast_validate %s/%s checks PASS",
        n_pass, len(checks),
    )
    return {"statcast_validate": {
        "checks_total": len(checks),
        "checks_pass": n_pass,
        "checks_fail": len(checks) - n_pass,
        # Only include failed checks in the summary to keep it small.
        "failed_checks": [c["name"] for c in checks if not c.get("ok")],
    }}


async def _step_score_mlb(db) -> Dict[str, Any]:
    """Run the MLB Total Bases engine and append picks to
    `mlb_pick_history`.  The engine opens its own client internally
    (legacy pattern); we let it do that so the engine stays unchanged."""
    from scripts import mlb_propvision_total_bases as _engine
    await _engine.main(log_picks=True)
    return {"mlb_score": "ok"}


async def _step_grade_mlb(db) -> Dict[str, Any]:
    from scripts.update_mlb_pick_results import update_results
    res = await update_results(db, since=None, dry_run=False) or {}
    return {"grade_mlb_picks": res}


# ---------------------------------------------------------------------------
# JOB: MLB daily pipeline (replaces run_mlb_daily_pipeline.sh)
# ---------------------------------------------------------------------------
async def mlb_daily_pipeline() -> Dict[str, Any]:
    """Runs once daily at 12:00 UTC. Holds `sync:mlb` for the duration.

    Steps (mirrors run_mlb_daily_pipeline.sh ordering):
      1. lineup ingest (today)         — lock:mlb
      2. statcast ingest (today-3 .. yesterday, idempotent self-heal)
      3. batter features rebuild
      4. pitcher features rebuild
      5. pitcher context backfill (shadow)
      6. identity map rebuild
      7. statcast validation
      8. score today's slate + log picks
    """
    cli, db = _get_db()
    yesterday = _yesterday_utc()
    today = _today_utc()
    # 2026-05-16 — widen the Statcast catch-up window from yesterday-only
    # to (today - 3 days .. yesterday). The ingest is idempotent on
    # (game_pk, at_bat_number, pitch_number), so re-pulling the prior
    # two days is free and self-heals any missed cron tick (e.g. pod
    # restart, OOM, Savant posting lag). Operational PR A+B (no model
    # or scoring change).
    statcast_start = (
        datetime.now(timezone.utc) - timedelta(days=3)
    ).strftime("%Y-%m-%d")
    job_id = "mlb_daily_pipeline"

    async def body() -> Dict[str, Any]:
        steps: Dict[str, Any] = {}
        # Each step wrapped so one failure doesn't abort the rest.
        for label, runner in (
            ("01_lineup_ingest",        lambda: _step_lineup_ingest(db, date=today)),
            ("02_statcast_ingest",      lambda: _step_statcast_ingest(db, start=statcast_start, end=yesterday)),
            ("03_batter_features",      lambda: _step_build_batter_features(db)),
            ("04_pitcher_features",     lambda: _step_build_pitcher_features(db)),
            ("05_pitcher_context",      lambda: _step_backfill_pitcher_context(db)),
            ("06_identity_map",         lambda: _step_build_identity_map(db)),
            ("07_statcast_validate",    lambda: _step_validate_statcast(db)),
            ("08_score_and_log",        lambda: _step_score_mlb(db)),
        ):
            try:
                steps[label] = await runner()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[MLB_DAILY] %s FAILED: %s", label, exc)
                steps[label] = {"error": repr(exc)}
        return {"steps": steps}

    try:
        result = await _run_locked(
            "sync:mlb", ttl_seconds=1800,  # 30 min; pipeline rarely > 15 min
            holder=job_id, body=body, db=db,
        )
        await _record_history(db, job_id, result)
        return result
    finally:
        cli.close()


# ---------------------------------------------------------------------------
# JOB: MLB pre-game lineups (replaces run_mlb_pregame_lineups.sh)
# ---------------------------------------------------------------------------
async def mlb_pregame_lineups(*, label: str = "mlb_lineups") -> Dict[str, Any]:
    """Runs at 18:00 UTC + 22:00 UTC. Holds `lineup:mlb`.

    Steps:
      1. ingest today's posted lineup cards from MLB Stats API
      2. read-only coverage monitor (logs WARNING on SLA breach)
    """
    cli, db = _get_db()
    today = _today_utc()
    job_id = label

    async def body() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            out.update(await _step_lineup_ingest(db, date=today))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[MLB_LINEUP] ingest FAILED: %s", exc)
            out["lineup_ingest_error"] = repr(exc)
        try:
            out.update(await _step_lineup_monitor(db))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[MLB_LINEUP] monitor FAILED: %s", exc)
            out["lineup_monitor_error"] = repr(exc)
        return out

    try:
        result = await _run_locked(
            "lineup:mlb", ttl_seconds=300, holder=job_id, body=body, db=db,
        )
        await _record_history(db, job_id, result)
        return result
    finally:
        cli.close()


# ---------------------------------------------------------------------------
# JOB: MLB pick result grading
# ---------------------------------------------------------------------------
async def mlb_pick_grade() -> Dict[str, Any]:
    """Runs at 05:00 UTC. Holds `grade:mlb`."""
    cli, db = _get_db()
    job_id = "mlb_pick_grade"

    async def body() -> Dict[str, Any]:
        return await _step_grade_mlb(db)

    try:
        result = await _run_locked(
            "grade:mlb", ttl_seconds=600, holder=job_id, body=body, db=db,
        )
        await _record_history(db, job_id, result)
        return result
    finally:
        cli.close()


async def mlb_pregame_lineups_18utc() -> Dict[str, Any]:
    """18:00 UTC variant — labelled distinctly so sync_history tracks
    the two daily ingest runs separately."""
    return await mlb_pregame_lineups(label="mlb_lineups_18utc")


async def mlb_pregame_lineups_22utc() -> Dict[str, Any]:
    """22:00 UTC variant — post-roster-lock pre-game pass."""
    return await mlb_pregame_lineups(label="mlb_lineups_22utc")


__all__ = [
    "mlb_daily_pipeline", "mlb_pregame_lineups",
    "mlb_pregame_lineups_18utc", "mlb_pregame_lineups_22utc",
    "mlb_pick_grade",
]


# ---------------------------------------------------------------------------
# Manual entrypoint for ops debugging:
#   python -m services.scheduled.mlb_jobs daily
#   python -m services.scheduled.mlb_jobs lineups
#   python -m services.scheduled.mlb_jobs grade
# ---------------------------------------------------------------------------
def _main_cli() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("daily", "lineups", "grade"):
        print("usage: python -m services.scheduled.mlb_jobs {daily|lineups|grade}")
        sys.exit(2)
    cmd = sys.argv[1]
    coro = {
        "daily":   mlb_daily_pipeline(),
        "lineups": mlb_pregame_lineups(label="mlb_lineups_manual"),
        "grade":   mlb_pick_grade(),
    }[cmd]
    res = asyncio.run(coro)
    import json
    print(json.dumps(res, default=str, indent=2))


if __name__ == "__main__":
    _main_cli()
