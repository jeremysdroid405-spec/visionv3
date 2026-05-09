"""
Running telemetry + safety guards for the full ingest driver.

Side-effect-free: holds in-memory counters; safety guards are pure
functions that read DB but only ABORT (caller decides what to do).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class IngestTelemetry:
    started_monotonic: float = field(default_factory=time.monotonic)
    started_credits: int = 0

    calls_total: int = 0
    calls_200: int = 0
    calls_404: int = 0
    calls_error: int = 0
    rate_limited: int = 0

    snapshot_inserts: int = 0
    snapshot_modifications: int = 0
    normalized_inserts: int = 0
    normalized_modifications: int = 0

    events_completed: int = 0
    windows_completed: int = 0
    expected_total_calls: int = 0

    def credits_used(self, current_session_credits: int) -> int:
        return current_session_credits - self.started_credits

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic

    def credits_per_minute(self, current_session_credits: int) -> float:
        elapsed = self.elapsed_seconds()
        if elapsed <= 0:
            return 0.0
        return self.credits_used(current_session_credits) / (elapsed / 60.0)

    def calls_per_minute(self) -> float:
        elapsed = self.elapsed_seconds()
        if elapsed <= 0:
            return 0.0
        return self.calls_total / (elapsed / 60.0)

    def write_rps(self) -> float:
        elapsed = self.elapsed_seconds()
        if elapsed <= 0:
            return 0.0
        return self.normalized_inserts / elapsed

    def eta_seconds(self) -> Optional[float]:
        if self.expected_total_calls <= 0 or self.calls_total <= 0:
            return None
        remaining_calls = self.expected_total_calls - self.calls_total
        if remaining_calls <= 0:
            return 0.0
        rps = self.calls_per_minute() / 60.0
        if rps <= 0:
            return None
        return remaining_calls / rps

    def snapshot(self, current_session_credits: int) -> Dict[str, Any]:
        return {
            "elapsed_seconds":     round(self.elapsed_seconds(), 1),
            "credits_used":        self.credits_used(current_session_credits),
            "credits_per_minute":  round(
                self.credits_per_minute(current_session_credits), 1),
            "calls_total":         self.calls_total,
            "calls_200":           self.calls_200,
            "calls_404":           self.calls_404,
            "calls_error":         self.calls_error,
            "rate_limited":        self.rate_limited,
            "calls_per_minute":    round(self.calls_per_minute(), 1),
            "events_completed":    self.events_completed,
            "windows_completed":   self.windows_completed,
            "expected_total_calls": self.expected_total_calls,
            "eta_seconds":         (round(self.eta_seconds(), 1)
                                     if self.eta_seconds() is not None
                                     else None),
            "snapshot_inserts":    self.snapshot_inserts,
            "normalized_inserts":  self.normalized_inserts,
            "normalized_modifications": self.normalized_modifications,
            "write_rps":           round(self.write_rps(), 1),
        }


# ---------------------------------------------------------------- safety
class IngestAborted(RuntimeError):
    """Raised by safety guards when an abort condition is met."""


async def assert_no_duplicate_anomaly(db) -> None:
    """If the unique index ever fails to dedupe (it shouldn't), abort."""
    pipe = [
        {"$group": {
            "_id": {
                "event_id": "$event_id", "snapshot_label": "$snapshot_label",
                "bookmaker": "$bookmaker", "market_key": "$market_key",
                "player": "$player", "line": "$line", "side": "$side",
            },
            "n": {"$sum": 1},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$count": "dups"},
    ]
    res = await db["replay_props_normalized"].aggregate(pipe).to_list(length=1)
    dups = res[0]["dups"] if res else 0
    if dups > 0:
        raise IngestAborted(
            f"DUPLICATE ANOMALY: {dups} duplicate groups in "
            f"replay_props_normalized (unique index breach)."
        )


async def assert_malformed_below_threshold(db, threshold: float = 0.005) -> None:
    """Abort if > 0.5% of normalized rows have null required fields."""
    total = await db["replay_props_normalized"].count_documents({})
    if total == 0:
        return
    pipe = {
        "$or": [
            {"player": {"$in": [None, ""]}},
            {"side": {"$in": [None, ""]}},
            {"line": None},
            {"odds_american": None},
            {"market_key": {"$in": [None, ""]}},
            {"bookmaker": {"$in": [None, ""]}},
            {"snapshot_ts": None},
        ]
    }
    bad = await db["replay_props_normalized"].count_documents(pipe)
    pct = bad / total
    if pct > threshold:
        raise IngestAborted(
            f"MALFORMED ANOMALY: {bad}/{total} ({pct:.2%}) malformed "
            f"normalized rows exceeds threshold {threshold:.2%}."
        )


async def assert_book_whitelist_compliance(db, *, allowed_books: set) -> None:
    """Abort if a normalized row sneaked through with a non-whitelisted book."""
    found = set()
    async for d in db["replay_props_normalized"].aggregate([
        {"$group": {"_id": "$bookmaker"}},
    ]):
        if d["_id"]:
            found.add(d["_id"])
    bad = found - allowed_books
    if bad:
        raise IngestAborted(
            f"BOOK MISMATCH ANOMALY: rows with non-whitelisted bookmaker(s): "
            f"{sorted(bad)}"
        )


async def assert_chronology_intact(db) -> None:
    """Abort if any normalized row's snapshot_ts is AFTER its commence_time
    (i.e. snapshot taken after tip-off — that would be live, not pregame)."""
    pipe = {"$expr": {"$gt": ["$snapshot_ts", "$commence_time"]}}
    bad = await db["replay_props_normalized"].count_documents(pipe)
    if bad > 0:
        raise IngestAborted(
            f"TIMESTAMP ORDER VIOLATION: {bad} normalized rows have "
            f"snapshot_ts > commence_time (post-tip data leaked into pregame)."
        )


async def run_safety_checks(db, *, allowed_books: set) -> None:
    """Run every check in sequence; raise IngestAborted on first failure."""
    await assert_no_duplicate_anomaly(db)
    await assert_malformed_below_threshold(db)
    await assert_book_whitelist_compliance(db, allowed_books=allowed_books)
    await assert_chronology_intact(db)


__all__ = [
    "IngestTelemetry",
    "IngestAborted",
    "assert_no_duplicate_anomaly",
    "assert_malformed_below_threshold",
    "assert_book_whitelist_compliance",
    "assert_chronology_intact",
    "run_safety_checks",
]
