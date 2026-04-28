"""
Hit Profile Regression Test  —  Vucevic P+R 9.5 ground truth
============================================================

Validates `services.hit_profile.compute_hit_profile` against the EXACT
Nikola Vucevic dataset from the 2026-04-29 audit:

    bdl_id     : 460
    stat_type  : "P+R"
    line       : 9.5
    last 10    : pts/reb pairs that yield P+R values
                 [9, 17, 14, 9, 18, 15, 9, 8, 0, 11]
    expected   : hit_count = 5, hit_rate_pct = 50.0, avg = 11.0

If this test fails, the dashboard Hit Rate and the L10 graph will be
out of alignment again. Failure is a hard regression.

Also verifies:
    * Window enforcement (L10 from a 12-game input).
    * Stamp helper rewrites pick.hit_rate, pick.l10_hit_count, etc.
    * `pick.hit_rate_over` is left UNTOUCHED (model probability).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.hit_profile import (
    HIT_RULE,
    WINDOW,
    compute_hit_profile,
    stamp_hit_profile_on_pick,
)

# ─── Ground truth fixture ─────────────────────────────────────────────
# The 10 game-log dicts mirror the shape of `nba_master_hub_2026.bdl_game_logs`.
# Sorting key is `(date, game_id)` newest-first.
_VUCEVIC_LAST_10: List[Dict[str, Any]] = [
    {"date": "2026-04-26", "pts": 4,  "reb": 5, "ast": 3, "game_id": 26},
    {"date": "2026-04-24", "pts": 11, "reb": 6, "ast": 2, "game_id": 24},
    {"date": "2026-04-21", "pts": 9,  "reb": 5, "ast": 2, "game_id": 21},
    {"date": "2026-04-19", "pts": 3,  "reb": 6, "ast": 1, "game_id": 19},
    {"date": "2026-04-10", "pts": 14, "reb": 4, "ast": 3, "game_id": 10},
    {"date": "2026-04-09", "pts": 10, "reb": 5, "ast": 2, "game_id":  9},
    {"date": "2026-04-07", "pts": 2,  "reb": 7, "ast": 1, "game_id":  7},
    {"date": "2026-04-05", "pts": 4,  "reb": 4, "ast": 1, "game_id":  5},
    {"date": "2026-03-06", "pts": 0,  "reb": 0, "ast": 0, "game_id":  6},
    {"date": "2026-03-04", "pts": 7,  "reb": 4, "ast": 2, "game_id":  4},
]

EXPECTED_VALUES: List[float] = [9, 17, 14, 9, 18, 15, 9, 8, 0, 11]


# ─── Core profile correctness ─────────────────────────────────────────
class TestVucevicGroundTruth:
    """Vucevic P+R 9.5 — every assertion derives from the audit data."""

    def test_values_extracted_in_recent_first_order(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["values"] == EXPECTED_VALUES, (
            f"Expected {EXPECTED_VALUES}, got {prof['values']}"
        )

    def test_hit_count_is_five_strict(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["hit_count"] == 5, (
            f"Expected hit_count=5, got {prof['hit_count']} "
            f"(values={prof['values']})"
        )

    def test_hit_rate_is_50_pct(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["hit_rate_pct"] == 50.0, (
            f"Expected hit_rate=50.0%, got {prof['hit_rate_pct']}%"
        )

    def test_avg_is_11_0(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["avg"] == 11.0, (
            f"Expected avg=11.0, got {prof['avg']}"
        )

    def test_total_is_10(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["total"] == 10

    def test_rule_is_gte(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=9.5, stat_type="P+R")
        assert prof["rule"] == ">=" == HIT_RULE


# ─── Window enforcement ──────────────────────────────────────────────
class TestWindowEnforcement:
    def test_window_caps_at_ten_even_with_more_input(self) -> None:
        extra = (
            [{"date": "2026-04-28", "pts": 99, "reb": 99, "ast": 0, "game_id": 99},
             {"date": "2026-04-27", "pts": 50, "reb": 50, "ast": 0, "game_id": 28}]
            + _VUCEVIC_LAST_10
        )
        prof = compute_hit_profile(extra, line=9.5, stat_type="P+R")
        assert len(prof["values"]) == 10
        # First two values must be the freshest (sorted by date desc).
        assert prof["values"][0] == 198  # 99+99
        assert prof["values"][1] == 100  # 50+50

    def test_window_constant(self) -> None:
        assert WINDOW == 10


# ─── Edge cases ───────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_games_returns_zero_count(self) -> None:
        prof = compute_hit_profile([], line=9.5, stat_type="P+R")
        assert prof["hit_count"] == 0
        assert prof["total"] == 0
        assert prof["hit_rate_pct"] is None  # no data → no rate
        assert prof["avg"] is None

    def test_null_line_does_not_crash(self) -> None:
        prof = compute_hit_profile(_VUCEVIC_LAST_10, line=None, stat_type="P+R")
        assert prof["values"] == EXPECTED_VALUES
        assert prof["hit_rate_pct"] is None  # line missing → no rate
        assert prof["avg"] == 11.0  # avg still computable

    def test_unknown_stat_type_yields_empty_values(self) -> None:
        prof = compute_hit_profile(
            _VUCEVIC_LAST_10, line=9.5, stat_type="totally_unknown_stat"
        )
        assert prof["values"] == []
        assert prof["hit_count"] == 0
        assert prof["total"] == 0


# ─── Stamp helper invariants ──────────────────────────────────────────
class TestStampHelper:
    def _vucevic_pick(self) -> Dict[str, Any]:
        return {
            "player_name": "Nikola Vucevic",
            "stat_type":   "P+R",
            "line":        9.5,
            "bdl_player_id": 460,
            "team":        "BOS",
            # The pre-fix card had this 75% leaking onto hit_rate. The
            # stamper must overwrite it with the empirical 50%.
            "hit_rate":      75.0,
            "hit_rate_over": 75.0,   # model probability — must remain
        }

    def test_stamp_overwrites_hit_rate_with_empirical_value(self) -> None:
        pick = self._vucevic_pick()
        stamp_hit_profile_on_pick(pick, _VUCEVIC_LAST_10, sport="nba")
        assert pick["hit_rate"] == 50.0, (
            f"Expected card hit_rate=50%, got {pick['hit_rate']}"
        )

    def test_stamp_writes_full_l10_metadata(self) -> None:
        pick = self._vucevic_pick()
        stamp_hit_profile_on_pick(pick, _VUCEVIC_LAST_10, sport="nba")
        assert pick["l10_hit_count"] == 5
        assert pick["l10_total"]     == 10
        assert pick["l10_values"]    == EXPECTED_VALUES
        assert pick["hit_profile_line"] == 9.5
        assert pick["hit_profile_rule"] == ">="
        assert pick["avg"] == 11.0

    def test_stamp_does_not_touch_model_hit_rate_over(self) -> None:
        """`hit_rate_over` is the model L20 probability used by
        ranking_score_v2 / scoring. The display fix MUST NOT mutate it."""
        pick = self._vucevic_pick()
        stamp_hit_profile_on_pick(pick, _VUCEVIC_LAST_10, sport="nba")
        assert pick["hit_rate_over"] == 75.0, (
            "stamp_hit_profile_on_pick must not modify model probability"
        )

    def test_displayed_hit_rate_equals_count_over_total(self) -> None:
        """The hard parity invariant called out in the user spec:
            displayed_hit_rate == l10_hit_count / l10_total
        """
        pick = self._vucevic_pick()
        stamp_hit_profile_on_pick(pick, _VUCEVIC_LAST_10, sport="nba")
        expected = round(100.0 * pick["l10_hit_count"] / pick["l10_total"], 1)
        assert pick["hit_rate"] == expected, (
            f"PARITY BROKEN: hit_rate={pick['hit_rate']} ≠ "
            f"l10_hit_count/l10_total = {pick['l10_hit_count']}/"
            f"{pick['l10_total']} = {expected}"
        )


# ─── Live-API contract spot-check ─────────────────────────────────────
# Optional integration check — only runs when the backend is up. Hits
# `/api/v3/ferrari/front-lines?sport=nba&sort=gap` and asserts every pick
# satisfies `hit_rate × l10_total == l10_hit_count × 100`.
def test_live_endpoint_hit_profile_parity_spot_check() -> None:
    import json
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(
            "http://localhost:8001/api/v3/ferrari/front-lines?sport=nba&sort=gap",
            timeout=5,
        ) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError):
        pytest.skip("backend not running on localhost:8001")
    picks = data.get("picks") or []
    if not picks:
        pytest.skip("no picks returned (empty NBA slate)")
    bad: List[str] = []
    for p in picks:
        hr  = p.get("hit_rate")
        cnt = p.get("l10_hit_count")
        tot = p.get("l10_total")
        if hr is None or cnt is None or not tot:
            continue  # fields not yet stamped
        expected = round(100.0 * cnt / tot, 1)
        if hr != expected:
            bad.append(
                f"  {p.get('player_name')!r} {p.get('stat_type')} "
                f"{p.get('line')}: hit_rate={hr} but {cnt}/{tot}="
                f"{expected}"
            )
    assert not bad, "\nLive parity violations:\n" + "\n".join(bad)
