"""
Tier Integrity Invariant
========================
Contract: on every Ferrari board endpoint, each tier MUST list distinct
players. Duplicate `player_name` rows (alternate lines, alternate stat
families, or any other cause) MUST be collapsed to a single pick — the one
with the highest `vision_score` (tie-broken by `pp_utility`, then by
`|edge_pct|`).

Root-cause this test exists for:
    2026-04-18 — `services/board/reader.py` did a raw `.sort().limit()`
    with no dedup; Amen Thompson appeared three times in Safe Haven NBA
    (PTS 15.5, PTS 14.5, AST 3.5). Fix installed in:
      - `services/board/reader.py::get_board` (reader-level dedup for NBA)
      - `routes/ferrari_tiers.py::_dedupe_picks_by_player` (applied at
        every tier exit point, covers MLB legacy collections that don't
        flow through `get_board`).
"""
from __future__ import annotations

import os
from collections import Counter

import pytest
import requests


def _api_base() -> str:
    return os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"


def _picks(sport: str, tier: str):
    url = f"{_api_base()}/api/v3/ferrari/{tier}?sport={sport}"
    r = requests.get(url, timeout=30)
    assert r.status_code == 200, f"{url} returned {r.status_code}: {r.text[:200]}"
    body = r.json()
    return body.get("picks") or body.get("apex_picks") or []


@pytest.mark.parametrize("sport", ["nba", "mlb"])
@pytest.mark.parametrize("tier", ["safe-haven", "front-lines", "war-zone"])
def test_tier_has_distinct_players(sport: str, tier: str) -> None:
    picks = _picks(sport, tier)
    names = [(p.get("player_name") or "").strip().lower() for p in picks]
    counts = Counter(n for n in names if n)
    dupes = {k: v for k, v in counts.items() if v > 1}
    assert not dupes, (
        f"{sport} {tier} has duplicate players: {dupes}. "
        f"Tier integrity invariant violated. "
        f"Affected picks: "
        + str([
            f"{p.get('player_name')} {p.get('stat_type')} {p.get('line')} {p.get('direction')}"
            for p in picks
            if (p.get("player_name") or "").strip().lower() in dupes
        ])
    )


def test_oracle_apex_has_distinct_players() -> None:
    picks = _picks("nba", "oracle-apex")
    names = [(p.get("player_name") or "").strip().lower() for p in picks]
    counts = Counter(n for n in names if n)
    dupes = {k: v for k, v in counts.items() if v > 1}
    assert not dupes, f"oracle-apex has duplicate players: {dupes}"


def test_dedupe_keeps_highest_vision_score() -> None:
    """When multiple props for the same player qualify, the kept pick
    must have the highest vision_score available across all occurrences.
    Regression for Amen Thompson PTS 15.5 (VS=88.7) vs PTS 14.5 (VS=72.6)."""
    from routes.ferrari_tiers import _dedupe_picks_by_player  # type: ignore

    candidates = [
        {"player_name": "Amen Thompson", "stat_type": "PTS", "line": 15.5,
         "direction": "Over", "vision_score": 88.7, "pp_utility": 0.41},
        {"player_name": "Amen Thompson", "stat_type": "PTS", "line": 14.5,
         "direction": "Over", "vision_score": 72.6, "pp_utility": 0.39},
        {"player_name": "Amen Thompson", "stat_type": "AST", "line": 3.5,
         "direction": "Over", "vision_score": 64.5, "pp_utility": 0.33},
        {"player_name": "Rudy Gobert", "stat_type": "REB", "line": 8.5,
         "direction": "Over", "vision_score": 80.0, "pp_utility": 0.50},
    ]
    out = _dedupe_picks_by_player(list(candidates))
    assert len(out) == 2, out
    by_name = {p["player_name"]: p for p in out}
    assert by_name["Amen Thompson"]["line"] == 15.5
    assert by_name["Amen Thompson"]["vision_score"] == 88.7
    assert by_name["Rudy Gobert"]["stat_type"] == "REB"
