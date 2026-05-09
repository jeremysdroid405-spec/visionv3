"""
Canary fixture: 5 mid-season-2024 NBA events for a controlled ingest dry-run.

These event_ids were captured from the 2026-05-09 historical alt-prop
audit (`/app/audit_reports/odds_api_historical_audit_2026-05-09/01_events.json`).
Hardcoding them avoids spending a second `events` call during the canary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, TypedDict


class CanaryEvent(TypedDict):
    event_id: str
    commence_time: datetime
    home_team: str
    away_team: str


# Five 2024-03-01 NBA games (UTC commence). Spread across two tip windows
# (00:10Z and 00:40Z) to exercise different snapshot timestamps.
PHASE1_CANARY_EVENTS: List[CanaryEvent] = [
    {
        "event_id":      "3c6f663a318c5b8b977586ad331f3f76",
        "commence_time": datetime(2024, 3, 2,  0, 10, tzinfo=timezone.utc),
        "home_team":     "Philadelphia 76ers",
        "away_team":     "Charlotte Hornets",
    },
    {
        "event_id":      "cff2cc43041474a11b5fadff9125ae16",
        "commence_time": datetime(2024, 3, 2,  0, 10, tzinfo=timezone.utc),
        "home_team":     "Detroit Pistons",
        "away_team":     "Cleveland Cavaliers",
    },
    {
        "event_id":      "d9b1f16402c3cf1593bdbe0e2c96856e",
        "commence_time": datetime(2024, 3, 2,  0, 40, tzinfo=timezone.utc),
        "home_team":     "Boston Celtics",
        "away_team":     "Dallas Mavericks",
    },
    {
        "event_id":      "fcf9bb4582534ce916c0234df50efa77",
        "commence_time": datetime(2024, 3, 2,  0, 40, tzinfo=timezone.utc),
        "home_team":     "Toronto Raptors",
        "away_team":     "Golden State Warriors",
    },
    {
        "event_id":      "97ae7d83bcd2de002c74e686d683fcb5",
        "commence_time": datetime(2024, 3, 2,  1, 10, tzinfo=timezone.utc),
        "home_team":     "New Orleans Pelicans",
        "away_team":     "Indiana Pacers",
    },
]


__all__ = ["PHASE1_CANARY_EVENTS", "CanaryEvent"]
