"""
Replay snapshot ladder — 8 pregame windows.

Per user directive (2026-05-09): we have 5M credits, prefer timing analysis
over cost savings, so the full 8-window plan is committed.

Per-tier canonical snapshot (used by tier-performance scorecards):
  - safe_haven = close
  - front_lines = t-60m
  - war_zone   = t-30m

Every prop is still scored at every window; the per-tier mapping only
selects which window's evaluation is the "headline" row in the scorecard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

# (label, minutes_before_start) — negative offset relative to commence_time.
# `close` = 5 minutes before tip — last reliably posted snapshot.
REPLAY_WINDOWS: List[Tuple[str, int]] = [
    ("t-24h", 24 * 60),
    ("t-12h", 12 * 60),
    ("t-6h",   6 * 60),
    ("t-3h",   3 * 60),
    ("t-90m",       90),
    ("t-60m",       60),
    ("t-30m",       30),
    ("close",        5),
]

REPLAY_WINDOW_LABELS: List[str] = [w[0] for w in REPLAY_WINDOWS]

# Tier → canonical evaluation snapshot for headline scorecards.
PER_TIER_CANONICAL_SNAPSHOT: Dict[str, str] = {
    "safe_haven":  "close",
    "front_lines": "t-60m",
    "war_zone":    "t-30m",
    # 'unqualified' rows are never in the scorecard, but map them to close
    # for diagnostic queries.
    "unqualified": "close",
}


def _label_lookup() -> Dict[str, int]:
    return {label: minutes for label, minutes in REPLAY_WINDOWS}


def snapshot_for(commence_time: datetime, label: str) -> datetime:
    """Return the UTC timestamp at which to query the historical odds API
    for a given event commence time and snapshot window label.

    Args:
        commence_time: event start time. Must be timezone-aware UTC.
        label: one of REPLAY_WINDOW_LABELS.

    Returns:
        UTC datetime (tz-aware) of the requested snapshot.

    Raises:
        ValueError: if commence_time is naive or label is unknown.
    """
    if commence_time.tzinfo is None:
        raise ValueError("commence_time must be timezone-aware (UTC)")
    if commence_time.utcoffset() != timedelta(0):
        raise ValueError("commence_time must be UTC")
    lookup = _label_lookup()
    if label not in lookup:
        raise ValueError(
            f"unknown snapshot label {label!r}; valid: {REPLAY_WINDOW_LABELS}"
        )
    minutes = lookup[label]
    return commence_time - timedelta(minutes=minutes)


def minutes_before_start(snapshot_ts: datetime, commence_time: datetime) -> int:
    """Return positive integer minutes between snapshot_ts and commence_time.
    Used by the normalizer to stamp every flat row.
    """
    if snapshot_ts.tzinfo is None or commence_time.tzinfo is None:
        raise ValueError("both timestamps must be timezone-aware")
    delta = commence_time - snapshot_ts
    return int(round(delta.total_seconds() / 60.0))


__all__ = [
    "REPLAY_WINDOWS",
    "REPLAY_WINDOW_LABELS",
    "PER_TIER_CANONICAL_SNAPSHOT",
    "snapshot_for",
    "minutes_before_start",
]
