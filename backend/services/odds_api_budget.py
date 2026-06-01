"""
services/odds_api_budget.py — Hard call-budget guard for The Odds API.

EVERY outbound HTTPS call to `api.the-odds-api.com` MUST be funneled
through this module. The guard:

  • Rolling counters (1-min / 1-hour / 1-day) by caller/sport/endpoint.
  • Hard cap per hour (`ODDS_API_MAX_CALLS_PER_HOUR`, default 500).
    When the cap is crossed, `check_and_increment()` raises
    `OddsApiBudgetExceeded`. Subsequent calls in the same window are
    rejected before httpx is touched.
  • Kill switch (`ODDS_API_KILL_SWITCH=1`) — every guarded call raises
    immediately. Use this if a runaway loop is detected and you need
    to stop bleed before the next deploy.
  • Per-call log document persisted to Mongo (`odds_api_call_log`) for
    forensic. Cap on doc growth via TTL index (24h default).
  • Caller allow-list for FULL syncs (only `startup`, `manual_admin`,
    `scheduled_cron`). The Adaptive Sync watcher is explicitly NOT on
    the list and uses a separate `delta` mode that does not call this
    helper for full-board pulls.

NOT a rate limiter — this is a budget gate. It denies, does not delay.
Callers that need to back off should handle the exception (the watcher
returns control to its 240s sleep).
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── config ────────────────────────────────────────────────────────────
MAX_CALLS_PER_HOUR = int(os.environ.get("ODDS_API_MAX_CALLS_PER_HOUR", "500") or 500)
KILL_SWITCH = (os.environ.get("ODDS_API_KILL_SWITCH", "0") or "0").strip() in ("1", "true", "yes", "on")
CALL_LOG_COLL = "odds_api_call_log"
BUDGET_STATE_COLL = "odds_api_budget_state"
# Callers permitted to invoke `sync_sport_props` (full-board fetch).
# Anything outside this set raises `FullSyncNotAllowed`.
FULL_SYNC_ALLOWED_CALLERS = {
    "startup",
    "manual_admin",
    "scheduled_cron",
    "bootstrap_script",
}


class OddsApiBudgetExceeded(Exception):
    """Raised when the per-hour budget would be exceeded by the current call."""


class FullSyncNotAllowed(Exception):
    """Raised when `sync_sport_props` is invoked by a non-allow-listed caller."""


# ─── in-process counters ───────────────────────────────────────────────
# `(deque of unix-ts)` per scope. Old entries trimmed on each touch.
_HOUR_WINDOW_SECONDS = 3600
_DAY_WINDOW_SECONDS = 86_400
_lock = Lock()
_hour_calls: deque = deque()          # all-callers global window
_by_caller: Dict[str, deque] = defaultdict(deque)
_by_sport: Dict[str, deque] = defaultdict(deque)
_by_endpoint: Dict[str, deque] = defaultdict(deque)
_total_today = 0
_today_ymd = ""
_blocked_total = 0


def _trim(dq: deque, window: int = _HOUR_WINDOW_SECONDS) -> None:
    cutoff = time.time() - window
    while dq and dq[0] < cutoff:
        dq.popleft()


def _rotate_day() -> None:
    global _total_today, _today_ymd
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _today_ymd:
        _today_ymd = today
        _total_today = 0


def hourly_count() -> int:
    """All-callers count in the current rolling hour."""
    with _lock:
        _trim(_hour_calls)
        return len(_hour_calls)


def snapshot() -> Dict[str, Any]:
    """Diagnostic snapshot — used by the budget endpoint."""
    with _lock:
        _rotate_day()
        _trim(_hour_calls)
        out = {
            "hour_count":  len(_hour_calls),
            "hour_limit":  MAX_CALLS_PER_HOUR,
            "today_count": _total_today,
            "blocked_total": _blocked_total,
            "kill_switch": KILL_SWITCH,
            "by_caller":   {},
            "by_sport":    {},
            "by_endpoint": {},
        }
        for k, dq in _by_caller.items():
            _trim(dq); out["by_caller"][k] = len(dq)
        for k, dq in _by_sport.items():
            _trim(dq); out["by_sport"][k] = len(dq)
        for k, dq in _by_endpoint.items():
            _trim(dq); out["by_endpoint"][k] = len(dq)
        return out


def check_and_increment(
    *, caller: str, sport: Optional[str], endpoint: str,
) -> None:
    """Reserve one call against the budget. Raises if blocked.

    Call BEFORE the HTTP request goes out. The caller is responsible for
    logging the response via `log_call_result()`.
    """
    global _total_today, _blocked_total
    if KILL_SWITCH:
        with _lock:
            _blocked_total += 1
        raise OddsApiBudgetExceeded(
            f"ODDS_API_KILL_SWITCH=1 — caller={caller} endpoint={endpoint}")
    now = time.time()
    with _lock:
        _rotate_day()
        _trim(_hour_calls)
        if len(_hour_calls) >= MAX_CALLS_PER_HOUR:
            _blocked_total += 1
            logger.error(
                f"[ODDS_BUDGET] BLOCKED caller={caller} sport={sport} "
                f"endpoint={endpoint} hour_count={len(_hour_calls)} "
                f"limit={MAX_CALLS_PER_HOUR}")
            raise OddsApiBudgetExceeded(
                f"budget exceeded: {len(_hour_calls)}/{MAX_CALLS_PER_HOUR} "
                f"calls this hour (caller={caller}, endpoint={endpoint})")
        _hour_calls.append(now)
        _by_caller[caller].append(now)
        if sport:
            _by_sport[sport].append(now)
        _by_endpoint[endpoint].append(now)
        _total_today += 1
    # Warn at 80% of budget
    if len(_hour_calls) == int(MAX_CALLS_PER_HOUR * 0.8):
        logger.warning(
            f"[ODDS_BUDGET] 80% of hourly budget consumed "
            f"({len(_hour_calls)}/{MAX_CALLS_PER_HOUR})")


def assert_full_sync_allowed(caller: str) -> None:
    """Gate for `sync_sport_props`. Raises if `caller` isn't on the
    full-sync allow-list. Watcher-triggered full syncs MUST hit this
    error and be visible in logs."""
    if caller in FULL_SYNC_ALLOWED_CALLERS:
        return
    logger.error(
        f"[ODDS_BUDGET] BLOCKED full-sync attempt by non-allow-listed "
        f"caller={caller!r}. Use sync_sport_props_delta(...) for "
        f"watcher-driven refresh."
    )
    raise FullSyncNotAllowed(
        f"full sync not allowed for caller={caller!r}. "
        f"Allowed: {sorted(FULL_SYNC_ALLOWED_CALLERS)}")


async def log_call_result(
    db, *, caller: str, sport: Optional[str], endpoint: str,
    url: str, status_code: int, sync_mode: Optional[str] = None,
    run_id: Optional[str] = None, error: Optional[str] = None,
) -> None:
    """Persist one row to `odds_api_call_log` (best-effort).

    `sync_mode` defaults to the active `SyncModeTag` contextvar
    ("full"/"delta"). Explicit overrides are honored.
    """
    if db is None:
        return
    if sync_mode is None:
        sync_mode = current_sync_mode()
    try:
        doc = {
            "ts":          datetime.now(timezone.utc),
            "caller":      caller,
            "sport":       sport,
            "endpoint":    endpoint,
            "url":         url[:512],
            "status_code": status_code,
            "sync_mode":   sync_mode,
            "run_id":      run_id,
            "error":       (error[:240] if error else None),
            "hour_count":  hourly_count(),
        }
        await db[CALL_LOG_COLL].insert_one(doc)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ODDS_BUDGET] call-log persist failed: {e}")


async def ensure_indexes(db) -> None:
    """Idempotent — call once at startup."""
    if db is None:
        return
    try:
        await db[CALL_LOG_COLL].create_index("ts", expireAfterSeconds=86400)
        await db[CALL_LOG_COLL].create_index([("caller", 1), ("ts", -1)])
        await db[CALL_LOG_COLL].create_index([("sport", 1), ("ts", -1)])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ODDS_BUDGET] index create failed: {e}")


# ─── caller-tag context ────────────────────────────────────────────────
# Use this to tag the active caller for a block of work, so any
# downstream odds API call picks the tag up automatically.
import contextvars

_caller_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "odds_api_caller", default="unknown")
# 2026-06-01 — explicit sync_mode tagging. EVERY guarded call MUST land
# in either "full" (force-refresh / forced-bypass) or "delta" (TTL +
# hash gated). "unknown" is treated as "delta" by the call-log so the
# top-level UI never has to special-case it.
_sync_mode_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "odds_api_sync_mode", default="delta")


class CallerTag:
    """Context manager to scope a caller tag. Usage:

        with CallerTag("adaptive_sync_watcher"):
            await sync.fetch_event_odds(...)
    """
    def __init__(self, caller: str):
        self._caller = caller
        self._token = None

    def __enter__(self):
        self._token = _caller_ctx.set(self._caller)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _caller_ctx.reset(self._token)


class SyncModeTag:
    """Context manager to scope an explicit sync_mode ("full" | "delta").
    Mirrors CallerTag — set once at the top of a sync call, every
    downstream `check_and_increment` / `log_call_result` picks it up
    automatically.
    """
    def __init__(self, mode: str):
        if mode not in ("full", "delta"):
            raise ValueError(
                f"sync_mode must be 'full' or 'delta', got {mode!r}")
        self._mode = mode
        self._token = None

    def __enter__(self):
        self._token = _sync_mode_ctx.set(self._mode)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _sync_mode_ctx.reset(self._token)


def current_caller() -> str:
    """Read the active caller tag from the context var."""
    return _caller_ctx.get()


def current_sync_mode() -> str:
    """Read the active sync_mode ("full" or "delta")."""
    return _sync_mode_ctx.get()
