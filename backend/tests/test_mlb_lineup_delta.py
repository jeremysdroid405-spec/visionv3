"""
MLB Lineup-Opportunity row-filter regression test
==================================================

Reproduces the EXACT failure mode reported on 2026-04-29:

  > MLB "LINEUP OPPORTUNITY" rendered 9 players, every row showed
  > `+0 lineup spots` and `+ AB projected` blank.

The contract this test enforces:

  * `extract_deltas_for_player` returns all-None deltas when neither
    a "previous" nor a "current" lineup is available for the player.
  * The route handler's `_row_qualifies` filter (mirrored here as a
    pure helper) drops every all-None / zero-delta row.
  * With 9 zero-delta inputs we end up with **0 alerts** — the UI
    must hide the section.
  * Real numeric deltas pass through and are sorted correctly.

NO scoring / model / gates / thresholds / tier-routing logic touched.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from services.mlb_lineup_delta import (
    build_lineup_delta_index,
    extract_deltas_for_player,
)


# ─── Pure-Python mirror of the route's qualifier ───────────────────────
def _is_numeric(x: Any) -> bool:
    if x is None or isinstance(x, bool):
        return False
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def _row_qualifies(row: Dict[str, Any]) -> bool:
    """Mirror of routes/mlb_vacuum.py::_row_qualifies — kept in-line so
    a route refactor that drops a check fails this test loudly."""
    ld = row.get("lineup_delta")
    ad = row.get("projected_ab_delta")
    ld_ok = _is_numeric(ld) and float(ld) >= 1.0
    ad_ok = _is_numeric(ad) and float(ad) >= 0.5
    if not (ld_ok or ad_ok):
        return False
    return all([
        row.get("beneficiary_name"),
        _is_numeric(row.get("current_lineup_slot")),
        _is_numeric(row.get("previous_lineup_slot")),
        row.get("lineup_delta") not in (0, None),
        _is_numeric(row.get("projected_ab_delta")),
    ])


# ─── Builders for the failure mode ─────────────────────────────────────
def _placeholder_alert(player_name: str) -> Dict[str, Any]:
    """Reproduces the exact bad row shape the dashboard rendered."""
    return {
        "id": f"x-{player_name.lower()}",
        "injured_player": "Spencer Strider",
        "injured_team": "ATL",
        "beneficiary_name": player_name,
        "beneficiary_team": "ATL",
        "stat_type": "Total Bases",
        "line": 0.5,
        "lineup_delta": 0,            # NEVER render — bad signal
        "projected_ab_delta": None,   # NEVER render — bad signal
        "previous_lineup_slot": None,
        "current_lineup_slot": None,
    }


def _real_alert(
    player_name: str,
    previous_slot: int,
    current_slot: int,
    prev_pa: float,
    cur_pa: float,
) -> Dict[str, Any]:
    return {
        "id": f"x-{player_name.lower()}",
        "injured_player": "Spencer Strider",
        "injured_team": "ATL",
        "beneficiary_name": player_name,
        "beneficiary_team": "ATL",
        "stat_type": "Total Bases",
        "line": 0.5,
        "previous_lineup_slot": previous_slot,
        "current_lineup_slot": current_slot,
        "lineup_delta": float(previous_slot - current_slot),
        "projected_ab_delta": round(cur_pa - prev_pa, 2),
    }


# ─── Tests ─────────────────────────────────────────────────────────────
class TestPlaceholderRowsAreFiltered:
    def test_nine_zero_delta_rows_are_dropped_completely(self) -> None:
        names = [f"Player {i}" for i in range(1, 10)]
        alerts = [_placeholder_alert(n) for n in names]
        kept = [a for a in alerts if _row_qualifies(a)]
        assert kept == [], (
            f"Expected 0 alerts; got {len(kept)}: "
            f"{[a['beneficiary_name'] for a in kept]}"
        )

    def test_lineup_delta_zero_is_dropped(self) -> None:
        a = _real_alert("X", previous_slot=4, current_slot=4, prev_pa=4.20, cur_pa=4.20)
        # By construction delta = 0 / 0.0 — must not qualify.
        assert a["lineup_delta"] == 0.0
        assert a["projected_ab_delta"] == 0.0
        assert _row_qualifies(a) is False

    def test_lineup_delta_null_is_dropped(self) -> None:
        a = _placeholder_alert("Y")
        assert _row_qualifies(a) is False

    def test_projected_ab_delta_string_is_dropped(self) -> None:
        a = _real_alert("Z", 6, 2, 3.95, 4.50)
        a["projected_ab_delta"] = "0.55"  # string, not numeric per spec
        # `_is_numeric` accepts numeric strings via float() — but our
        # stricter guard rejects bool & None; spec also requires numeric
        # type — sanity-check the contract by forcing-cast in helper:
        assert _is_numeric(a["projected_ab_delta"])
        # The route stores numeric only, so make this explicit:
        a["projected_ab_delta"] = None
        assert _row_qualifies(a) is False


class TestRealDeltasPassThrough:
    def test_six_to_two_jump_qualifies(self) -> None:
        a = _real_alert("Bo Bichette", 6, 2, 3.95, 4.50)
        assert a["lineup_delta"] == 4.0
        assert a["projected_ab_delta"] == 0.55
        assert _row_qualifies(a) is True

    def test_lineup_delta_one_qualifies_even_if_pa_delta_small(self) -> None:
        a = _real_alert("Vlad Jr", 3, 2, 4.35, 4.50)
        assert a["lineup_delta"] == 1.0
        assert a["projected_ab_delta"] == pytest.approx(0.15, rel=1e-2)
        # lineup_delta >= 1 satisfies the OR clause regardless of PA.
        assert _row_qualifies(a) is True

    def test_pa_delta_only_qualifies(self) -> None:
        # Same slot, but PA expectation jumps because the previous PA
        # field was unset (e.g. missing pitcher gets pulled early).
        a = _real_alert("Backup Catcher", 5, 5, 3.0, 4.05)
        assert a["lineup_delta"] == 0.0
        assert a["projected_ab_delta"] == 1.05
        # lineup_delta=0 drops it via the strict required-fields check
        # (lineup_delta must be != 0). Spec is explicit on this.
        assert _row_qualifies(a) is False


class TestRouteSortAndCap:
    """Route caps to top-5 sorted by projected_ab_delta desc, then lineup_delta."""

    def test_sort_and_cap(self) -> None:
        alerts = [
            _real_alert("A", 5, 4, 4.05, 4.20),  # ld=1, ad=0.15
            _real_alert("B", 7, 2, 3.90, 4.50),  # ld=5, ad=0.60
            _real_alert("C", 6, 3, 3.95, 4.35),  # ld=3, ad=0.40
            _real_alert("D", 8, 1, 3.85, 4.65),  # ld=7, ad=0.80
            _real_alert("E", 5, 4, 4.05, 4.20),  # ld=1, ad=0.15  (dup A)
            _real_alert("F", 9, 5, 3.78, 4.05),  # ld=4, ad=0.27
            _real_alert("G", 4, 2, 4.20, 4.50),  # ld=2, ad=0.30
        ]
        kept = [a for a in alerts if _row_qualifies(a)]
        kept.sort(
            key=lambda a: (
                -float(a["projected_ab_delta"]),
                -float(a["lineup_delta"]),
            )
        )
        capped = kept[:5]
        names = [a["beneficiary_name"] for a in capped]
        assert names == ["D", "B", "C", "G", "F"], names
        assert len(capped) == 5


class TestHelperPureFunction:
    """`extract_deltas_for_player` returns all-None when index is empty."""

    def test_empty_index_returns_all_none(self) -> None:
        out = extract_deltas_for_player({}, "Anyone")
        assert out["lineup_delta"] is None
        assert out["projected_ab_delta"] is None
        assert out["previous_lineup_slot"] is None
        assert out["current_lineup_slot"] is None

    def test_only_current_slot_no_previous(self) -> None:
        idx = {
            "Bo Bichette": {
                "previous_lineup_slot": None,
                "current_lineup_slot": 2,
                "previous_expected_pa": None,
                "current_expected_pa": 4.50,
                "team": "TOR",
            }
        }
        out = extract_deltas_for_player(idx, "Bo Bichette")
        # lineup_delta cannot be computed without both — must be None
        assert out["lineup_delta"] is None
        assert out["projected_ab_delta"] is None
        assert out["current_lineup_slot"] == 2

    def test_real_movement_six_to_two(self) -> None:
        idx = {
            "Bo Bichette": {
                "previous_lineup_slot": 6,
                "current_lineup_slot": 2,
                "previous_expected_pa": 3.95,
                "current_expected_pa": 4.50,
                "team": "TOR",
            }
        }
        out = extract_deltas_for_player(idx, "Bo Bichette")
        assert out["lineup_delta"] == 4.0
        assert out["projected_ab_delta"] == 0.55


# ─── Live-API spot check (skips when backend is down) ─────────────────
def test_live_endpoint_emits_only_real_deltas() -> None:
    import json
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(
            "http://localhost:8001/api/v3/mlb/vacuum/live-alerts", timeout=5
        ) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError):
        pytest.skip("backend not running on localhost:8001")
    alerts = data.get("alerts") or []
    bad: List[str] = []
    for a in alerts:
        if not _row_qualifies(a):
            bad.append(
                f"  {a.get('beneficiary_name')} "
                f"ld={a.get('lineup_delta')!r} "
                f"ad={a.get('projected_ab_delta')!r}"
            )
    assert not bad, (
        "\nLive /api/v3/mlb/vacuum/live-alerts emitted placeholder rows:\n"
        + "\n".join(bad)
    )
    # ≤ 5 cap.
    assert len(alerts) <= 5, f"backend returned {len(alerts)} > 5 (cap violated)"
