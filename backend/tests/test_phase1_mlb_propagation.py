"""Phase 1 — MLB context-propagation tests.

Tests for `_propagate_phase1_context` in
`services/scoring/adapters/mlb_scoring.py` and the conservative EB
whitelist expansion for hits / singles / doubles / runs / stolen_bases
/ batter_walks.
"""
from __future__ import annotations

import os

import pytest

from services.scoring import mlb_eb_shrinkage as ebs
from services.scoring.adapters.mlb_scoring import (
    _normalise_batter_hand,
    _propagate_phase1_context,
)
from tests.test_mlb_eb_shrinkage import _FakeColl, _fake_hub


# Always reset cache + flag before each test.
def setup_function(_):
    ebs.reset_cache()
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"


# ─── Batter-hand normalisation ─────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("L", "L"), ("Left", "L"), ("LH", "L"), ("LHB", "L"),
    ("R", "R"), ("Right", "R"), ("RH", "R"), ("RHB", "R"),
    ("S", "S"), ("B", "S"), ("Switch", "S"), ("SH", "S"), ("Both", "S"),
    ("  l  ", "L"), ("right", "R"),
    (None, None), ("", None), ("junk", None),
])
def test_normalise_batter_hand(raw, expected):
    assert _normalise_batter_hand(raw) == expected


# ─── Propagation: batter_hand from bats_throws ─────────────────────
def test_propagate_batter_hand_from_bats_throws():
    """Master_hub stores `bats_throws='<Bats>/<Throws>'`. Helper must
    parse the first half."""
    master_hub = {176: {"bdl_id": 176, "bats_throws": "Right/Right"}}
    prop = {"player_name": "Andy Pages", "bdl_player_id": 176}
    _propagate_phase1_context(prop, master_hub, 176)
    assert prop["batter_hand"] == "R"


def test_propagate_batter_hand_switch_hitter():
    master_hub = _fake_hub([{"bdl_id": 5, "bats_throws": "Both/Right"}])
    # The propagation helper expects a dict-like `master_hub.get(id)` so we
    # build a thin wrapper to bridge the _FakeColl into dict-like usage.
    hub_dict = {5: {"bdl_id": 5, "bats_throws": "Both/Right"}}
    prop = {"player_name": "Geraldo Perdomo", "bdl_player_id": 5}
    _propagate_phase1_context(prop, hub_dict, 5)
    assert prop["batter_hand"] == "S"


def test_propagate_batter_hand_prefers_bats_when_set():
    """If `bats` is populated, use it directly without parsing
    bats_throws."""
    master_hub = {1: {"bdl_id": 1, "bats": "L", "bats_throws": "Right/Right"}}
    prop = {"bdl_player_id": 1}
    _propagate_phase1_context(prop, master_hub, 1)
    assert prop["batter_hand"] == "L"


def test_propagate_batter_hand_silent_skip_when_missing():
    """No master_hub entry → no exception, no field stamped."""
    prop = {"bdl_player_id": 999}
    _propagate_phase1_context(prop, {}, 999)
    assert "batter_hand" not in prop


def test_propagate_does_not_clobber_existing_batter_hand():
    master_hub = {1: {"bdl_id": 1, "bats_throws": "Right/Right"}}
    prop = {"bdl_player_id": 1, "batter_hand": "L"}  # already set
    _propagate_phase1_context(prop, master_hub, 1)
    assert prop["batter_hand"] == "L"  # unchanged


# ─── Propagation: batting_order alias fallback ─────────────────────
def test_propagate_batting_order_already_set_kept():
    prop = {"batting_order": 3}
    _propagate_phase1_context(prop, {}, None)
    assert prop["batting_order"] == 3


def test_propagate_batting_order_from_lineup_spot_alias():
    """Legacy ingest paths emit `lineup_spot`; we fall back to it."""
    prop = {"lineup_spot": 5}
    _propagate_phase1_context(prop, {}, None)
    assert prop["batting_order"] == 5


def test_propagate_batting_order_missing_stays_missing():
    prop = {}
    _propagate_phase1_context(prop, {}, None)
    assert prop.get("batting_order") is None


# ─── EB Phase-1 whitelist coverage ─────────────────────────────────
def _phase1_logs(values, stat_family):
    """Per-game log rows shaped like the production
    `mlb_master_hub_2026.bdl_game_logs` schema. Same template as
    `tests/test_mlb_eb_shrinkage._logs` but adds the Phase-1 columns
    (`doubles`, `triples`, `walks`, `stolen_bases`) so per-stat
    derivation paths can be exercised."""
    out = []
    for v in values:
        out.append({
            "at_bats": 4, "plate_appearances": 4,
            "hits": v if stat_family in ("hits",) else 0,
            "runs": v if stat_family == "runs" else 0,
            "rbis": 0,
            "home_runs": 0,
            "total_bases": 0,
            "doubles": v if stat_family == "doubles" else 0,
            "triples": 0,
            "walks": v if stat_family == "batter_walks" else 0,
            "stolen_bases": v if stat_family == "stolen_bases" else 0,
        })
    return out


def test_hits_now_eb_protected_against_extreme_underprojection():
    """The Andy Pages case in microcosm: raw model says 0.48 hits but
    player's last 20 games average 1.5. Conservative shrinkage (0.80
    model / 0.20 player) with ramp at n=20 should produce a measurable
    pull toward the truth."""
    logs = _phase1_logs([1.5] * 20, "hits")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "hits", 0.48)
    assert audit["eb_skip_reason"] is None
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_player_career_mean"] == pytest.approx(1.5, abs=1e-3)
    # n=20 hits the CAP_AT_GAMES ramp; effective weights are (0.80, 0.20).
    # 0.80 * 0.48 + 0.20 * 1.5 = 0.684
    assert shrunk == pytest.approx(0.684, abs=1e-3)


def test_singles_derived_from_hits_minus_extra_bases():
    """Logs ship `hits / doubles / triples / home_runs` separately —
    singles must be derived: H − 2B − 3B − HR."""
    logs = []
    # 20 games: each with 3H, 1HR, 1-2B → 1 single/game → mean=1.0
    for _ in range(20):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "hits": 3, "doubles": 1, "triples": 0, "home_runs": 1,
            "runs": 0, "rbis": 0, "walks": 0, "stolen_bases": 0,
            "total_bases": 0,
        })
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "singles", 0.5)
    assert audit["eb_player_career_mean"] == pytest.approx(1.0, abs=1e-3)
    # 0.80 * 0.5 + 0.20 * 1.0 = 0.60
    assert shrunk == pytest.approx(0.60, abs=1e-3)


def test_batter_walks_reads_walks_log_column():
    """Log column is `walks`; canonical stat_family is `batter_walks`
    — the family-aware reader must bridge the alias."""
    logs = _phase1_logs([1] * 20, "batter_walks")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "batter_walks", 0.4)
    assert shrunk is not None
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_player_career_mean"] == pytest.approx(1.0, abs=1e-3)
    assert shrunk == pytest.approx(0.52, abs=1e-3)  # 0.80*0.4+0.20*1.0


def test_stolen_bases_uses_most_model_weight():
    """SB has the most conservative weights (0.90/0.10) — rare stat,
    high model trust."""
    logs = _phase1_logs([1] * 20, "stolen_bases")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "stolen_bases", 0.2)
    # 0.90 * 0.2 + 0.10 * 1.0 = 0.28
    assert shrunk == pytest.approx(0.28, abs=1e-3)


def test_pitcher_strikeouts_still_excluded_until_phase2():
    """Phase 1 explicitly leaves pitcher Ks for Phase 2."""
    logs = _phase1_logs([6] * 20, "hits")  # log content irrelevant — skipped before read
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(
        hub, 1, "pitcher_strikeouts", 5.0,
    )
    assert shrunk is None
    assert audit["eb_skip_reason"] == "stat_not_whitelisted"
