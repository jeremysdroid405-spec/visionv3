"""Structured error logger — replaces silent `except Exception: pass`.

WHY THIS EXISTS
---------------
The codebase accumulated 558 broad `except Exception` handlers, many of
which swallow errors silently (log-only or `pass`). That pattern is the
single largest reason bugs regress without detection: a downstream sync
fails, the caller's exception handler catches + logs (or drops) it, the
sync_history row still marks success, and the UI quietly serves stale
data. Nothing alerts; nothing breaks; the fix from three days ago
silently rots.

This module gives every exception handler ONE line to call that:

  1. Writes a structured row to `error_log` (Mongo, TTL 14 days) with
     context (sport, subsystem, canonical_key, version_tag, exception
     type, stack, caller metadata).
  2. Still returns normally — callers control flow-control decisions;
     this module ONLY observes.
  3. Never raises. A failure to log an error must never create a new
     error. We fall back to stdlib `logging.exception` if the DB write
     itself fails.

USAGE
-----
    from services.observability import log_caught_exception

    try:
        await sync_something(sport, event_id)
    except Exception as e:
        await log_caught_exception(
            db, e,
            subsystem="odds_sync.fetch_event",
            sport=sport,
            context={"event_id": event_id, "attempt": attempt},
        )
        # caller decides whether to retry, skip, or re-raise

    # Fire-and-forget variant (no db handle available, sync code):
    log_silent_failure(
        subsystem="adapter.canonical_key",
        exc=e,
        context={"doc_id": doc.get("_id")},
    )

OPERATIONAL SURFACE
-------------------
The `GET /api/v3/admin/errors/summary` endpoint (see
`routes/admin_errors.py`) aggregates the last 7d by subsystem →
exception_type → count so you can triage the top-N regressions each
week.

INVARIANTS
----------
- Never raises from any public function.
- Never logs PII or raw user credentials (callers control `context`).
- Writes are best-effort; a dropped error row is tolerable, a crash in
  the logger is not.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ERROR_LOG_COLLECTION = "error_log"

# TTL in seconds (14 days). Keeps the collection bounded without a
# dedicated sweeper. Mongo TTL monitor runs every 60s in Atlas/M0.
_TTL_SECONDS = 14 * 24 * 3600

# Index is idempotent-created on first write. We cache the creation so
# we don't re-run ensureIndex on every call (one extra round-trip).
_indexes_ensured: set = set()


def get_error_log_collection(db):
    """Return the error_log collection handle. Pure wrapper for testing."""
    return db[ERROR_LOG_COLLECTION]


async def _ensure_indexes(db) -> None:
    coll_name = ERROR_LOG_COLLECTION
    if coll_name in _indexes_ensured:
        return
    try:
        coll = db[coll_name]
        # TTL index on `ts` — Mongo prunes rows older than _TTL_SECONDS.
        await coll.create_index("ts", expireAfterSeconds=_TTL_SECONDS)
        # Triage index: (subsystem, ts desc) for admin dashboard.
        await coll.create_index([("subsystem", 1), ("ts", -1)])
        # Group-by index: (exception_type, ts desc) for top-N bug triage.
        await coll.create_index([("exception_type", 1), ("ts", -1)])
        _indexes_ensured.add(coll_name)
    except Exception:
        # Never raise from the logger. Index creation failure is rare
        # and non-fatal — writes still work without the TTL.
        logger.exception("[ERROR_LOG] index creation failed (non-fatal)")


def _build_record(
    exc: BaseException,
    subsystem: str,
    *,
    sport: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    severity: str = "caught",
) -> Dict[str, Any]:
    tb = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    # Trim very long tracebacks — Mongo has a 16MB doc cap but we don't
    # want a 1MB traceback eating the log collection.
    if len(tb) > 16_000:
        tb = tb[:8_000] + "\n... [truncated] ...\n" + tb[-4_000:]

    caller_file = None
    caller_line = None
    tb_frame = exc.__traceback__
    while tb_frame and tb_frame.tb_next:
        tb_frame = tb_frame.tb_next
    if tb_frame:
        caller_file = tb_frame.tb_frame.f_code.co_filename
        caller_line = tb_frame.tb_lineno

    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc),
        "subsystem": subsystem,
        "exception_type": type(exc).__name__,
        "exception_module": type(exc).__module__,
        "message": str(exc)[:2000],
        "traceback": tb,
        "severity": severity,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "python_version": f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}",
    }
    if sport:
        record["sport"] = sport.lower()
    if caller_file:
        record["caller_file"] = caller_file
        record["caller_line"] = caller_line
    if context:
        # Shallow-copy to avoid holding references to large objects the
        # caller might mutate after the log call.
        record["context"] = {
            k: _safe_value(v) for k, v in context.items()
        }
    return record


def _safe_value(v: Any) -> Any:
    """Coerce a context value to something mongo-serializable."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_safe_value(x) for x in v][:50]  # cap list size
    if isinstance(v, dict):
        return {str(k): _safe_value(val) for k, val in list(v.items())[:50]}
    # Fallback: stringify unknown types.
    s = str(v)
    return s[:500]


async def log_caught_exception(
    db,
    exc: BaseException,
    *,
    subsystem: str,
    sport: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    severity: str = "caught",
) -> bool:
    """Record a caught exception. NEVER raises.

    Returns True on successful write, False otherwise.
    """
    try:
        await _ensure_indexes(db)
        record = _build_record(
            exc, subsystem, sport=sport, context=context, severity=severity,
        )
        await db[ERROR_LOG_COLLECTION].insert_one(record)
        # Also mirror to stdlib logger so tailing supervisor logs still
        # surfaces the error inline.
        logger.warning(
            f"[ERR:{subsystem}] {type(exc).__name__}: {str(exc)[:200]}"
        )
        return True
    except Exception:
        # Last-resort: dump to stdlib logger. Never raise from logger.
        try:
            logger.exception(
                f"[ERR:{subsystem}] ORIGINAL: {type(exc).__name__}: "
                f"{str(exc)[:200]} — logger write itself failed"
            )
        except Exception:
            pass
        return False


def log_silent_failure(
    subsystem: str,
    exc: BaseException,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Sync variant — no DB handle needed. Logs to stdlib only.

    Use in hot paths where `db` isn't available (e.g., adapter
    canonical_key reconstruction, scoring math helpers). The stdlib
    log line still carries subsystem + context so grep works.

    If you CAN pass a db handle, prefer `log_caught_exception` — it
    persists structured rows the admin dashboard can aggregate.
    """
    try:
        ctx_str = ""
        if context:
            ctx_str = " " + " ".join(
                f"{k}={_safe_value(v)}" for k, v in context.items()
            )[:400]
        logger.warning(
            f"[ERR:{subsystem}] {type(exc).__name__}: "
            f"{str(exc)[:200]}{ctx_str}"
        )
    except Exception:
        # Truly nothing we can do.
        pass


def log_silent_failure_fire_and_forget(
    db,
    subsystem: str,
    exc: BaseException,
    *,
    sport: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Schedule an async log write from sync code. Never raises.

    Use this when you have a `db` handle in sync code (or inside an
    already-running event loop) and want the structured row without
    awaiting it. The write is scheduled on the current loop.
    """
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            log_caught_exception(
                db, exc, subsystem=subsystem, sport=sport, context=context,
            )
        )
    except RuntimeError:
        # No running loop — fall back to stdlib.
        log_silent_failure(subsystem, exc, context=context)
