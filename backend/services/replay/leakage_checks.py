"""
As-of-time integrity checks for the replay engine.

The single biggest correctness risk in any replay system is feeding the
scoring engine data from games that haven't happened yet at the
evaluation snapshot timestamp ("future-data leakage"). Everything in
this module is a pure, side-effect-free check that operates on
explicitly-supplied lists of game logs, snapshots, and as-of timestamps
so it can be unit-tested without a database.

Public API:
    assert_no_future_games(game_logs, as_of_ts)
    assert_chronology(snapshots, commence_time)
    assert_pregame_only(snapshot_ts, commence_time)
    snapshot_lineage_chain_intact(envelope_chain)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence


class LeakageDetected(AssertionError):
    """Raised when a game log dated after `as_of_ts` is in the input."""


class ChronologyViolation(AssertionError):
    """Raised when snapshot_ts ordering is not monotonic-pregame."""


# ---------------------------------------------------------------------------
def assert_no_future_games(
    game_logs: Iterable[dict],
    *,
    as_of_ts: datetime,
    timestamp_field: str = "game_date",
) -> None:
    """All game-log rows must have `timestamp_field` <= as_of_ts.

    This is the primary leakage test. Replay feature builders MUST call
    this on the rolling window before computing μ / σ / hit_rate / vk2.
    """
    if as_of_ts.tzinfo is None:
        raise ValueError("as_of_ts must be tz-aware UTC")
    leaked: List[dict] = []
    for g in game_logs:
        ts = g.get(timestamp_field)
        if ts is None:
            continue
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > as_of_ts:
            leaked.append({"row": g, "ts": ts})
    if leaked:
        raise LeakageDetected(
            f"{len(leaked)} game-log row(s) dated after as_of_ts={as_of_ts}; "
            f"first leaked: {leaked[0]['ts']}"
        )


def assert_pregame_only(
    snapshot_ts: datetime, commence_time: datetime,
) -> None:
    """A snapshot must be taken strictly before the game starts."""
    if snapshot_ts.tzinfo is None or commence_time.tzinfo is None:
        raise ValueError("both timestamps must be tz-aware")
    if snapshot_ts >= commence_time:
        raise ChronologyViolation(
            f"snapshot_ts {snapshot_ts} >= commence_time {commence_time} "
            f"(post-tip data leaked into pregame snapshot)"
        )


def assert_chronology(
    snapshots: Sequence[dict],
    *,
    commence_time: datetime,
    label_field: str = "snapshot_label",
    ts_field: str = "snapshot_ts",
) -> None:
    """For an ordered list of snapshots (earliest → latest), assert:
      1) every snapshot is pre-game
      2) `snapshot_ts` is monotonically non-decreasing along the list
      3) labels are unique
    """
    seen_labels = set()
    last_ts: Optional[datetime] = None
    for s in snapshots:
        ts = s.get(ts_field)
        if ts is None:
            raise ChronologyViolation(f"missing {ts_field} in {s}")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        assert_pregame_only(ts, commence_time)
        if last_ts is not None and ts < last_ts:
            raise ChronologyViolation(
                f"non-monotonic snapshot_ts: {ts} after {last_ts}"
            )
        last_ts = ts
        lbl = s.get(label_field)
        if lbl in seen_labels:
            raise ChronologyViolation(f"duplicate label {lbl!r}")
        seen_labels.add(lbl)


def snapshot_lineage_chain_intact(envelope_chain: Sequence[dict]) -> bool:
    """Given the envelope chain returned by the API for a single event
    (each carrying timestamp / previous_timestamp / next_timestamp),
    verify each envelope's `next_timestamp` matches the next envelope's
    `timestamp`. Returns True if intact, False otherwise.
    """
    if len(envelope_chain) < 2:
        return True
    for a, b in zip(envelope_chain, envelope_chain[1:]):
        a_next = a.get("next_timestamp")
        b_ts = b.get("timestamp")
        if a_next is None or b_ts is None:
            continue
        if a_next != b_ts:
            return False
    return True


__all__ = [
    "LeakageDetected",
    "ChronologyViolation",
    "assert_no_future_games",
    "assert_pregame_only",
    "assert_chronology",
    "snapshot_lineage_chain_intact",
]
