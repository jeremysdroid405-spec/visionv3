"""
Regression tests for the team-prop pipeline SSOT audit
(scripts/team_pipeline_ssot_audit.py).

These pin the audit's structural invariants — no DB hits, no
process spawns. They guarantee the audit script keeps producing
the EXACT shape the operator relies on for SSOT parity checks,
even after future refactors (e.g. dropping a sport, renaming a
collection).

Coverage:
  1. Audit module imports cleanly with no top-level side effects.
  2. SPORTS list is exactly the three the architecture doc locks in.
  3. MARKET_CATEGORIES list is exactly the four the trainer emits.
  4. `_score_adapter_present` returns ✓ for the four market-category
     XGB artifacts each sport in /app/backend/models/team_xgb/ ships.
  5. `_gap_check` honors the contract:
       - sport with feature_cache=0 → no gaps (out of scope)
       - sport with feature_cache>0 but score_ok=False → reports gap
       - sport with reshape>0 but replay_scored < reshape → reports
         partial-replay gap
       - sport with no successful grid run → reports grid gap
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")


@pytest.fixture(scope="module")
def audit():
    return importlib.import_module("scripts.team_pipeline_ssot_audit")


def test_sports_locked_to_mlb_nba_nfl(audit):
    assert audit.SPORTS == ("mlb", "nba", "nfl"), (
        "Team architecture (§1.1) locks the supported sports list. "
        "Adding a sport requires updating both the audit and the "
        "TEAM_PROPS_ARCHITECTURE.md seed inventory in lockstep."
    )


def test_market_categories_locked(audit):
    assert audit.MARKET_CATEGORIES == (
        "h2h", "spread", "game_total", "team_total"
    ), (
        "Trainer (scripts/sgo/train_team_xgb.py) emits exactly four "
        "market-category artifacts per sport. The audit must stay "
        "in lockstep — otherwise it would falsely flag missing "
        "adapters or pass over real coverage gaps."
    )


def test_artifact_root_matches_loader(audit):
    """The audit must point at the same artifact dir
    `services/team_xgb_loader.py::ARTIFACT_ROOT` reads from. Drift
    here is a P0 — the live scorer would happily load models the
    audit never inspected."""
    from services.team_xgb_loader import ARTIFACT_ROOT
    assert audit.ARTIFACT_ROOT == ARTIFACT_ROOT


def test_replay_collection_matches_reshape(audit):
    """The audit's replay-collection name must equal the reshape
    script's destination collection
    (`scripts.sgo.reshape_team_props_to_replay.DST_COLL`).
    Otherwise the audit would count a different collection than
    the one the reshape adapter populates."""
    from scripts.sgo.reshape_team_props_to_replay import DST_COLL
    assert audit.REPLAY_COLL == DST_COLL


def test_score_adapter_present_returns_dict_per_market(audit):
    out = audit._score_adapter_present("nba")
    assert set(out.keys()) == set(audit.MARKET_CATEGORIES)
    # Every value is a bool (existence flag).
    assert all(isinstance(v, bool) for v in out.values())


def test_score_adapter_present_unknown_sport_returns_all_false(
    audit, tmp_path, monkeypatch,
):
    """An unsupported sport (e.g. NHL before its trainer ships)
    must report every market-category adapter ✗ — not raise."""
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", tmp_path)
    out = audit._score_adapter_present("nhl")
    assert all(ok is False for ok in out.values())


def test_gap_check_skips_sport_with_empty_feature_cache(audit):
    """A sport whose feature_cache is 0 is treated as "not in
    scope" — the audit must NOT flag downstream gaps. This is the
    same contract that lets us add a future sport (e.g. NHL)
    without breaking the audit on day one."""
    rec = {
        "feature_cache": 0,
        "score_ok": False,
        "score_adapter": {"h2h": False, "spread": False,
                            "game_total": False, "team_total": False},
        "reshape": 0,
        "replay_ok": False,
        "replay_scored": 0,
        "grid_ok": False,
        "league": "NHL",
    }
    assert audit._gap_check(rec) == []


def test_gap_check_flags_missing_score_adapter(audit):
    rec = {
        "feature_cache": 1000,
        "score_ok": False,
        "score_adapter": {"h2h": True, "spread": True,
                            "game_total": False, "team_total": True},
        "reshape": 1000,
        "replay_ok": True,
        "replay_scored": 1000,
        "grid_ok": True,
        "league": "NBA",
    }
    gaps = audit._gap_check(rec)
    assert len(gaps) == 1
    assert gaps[0].startswith("score (missing artifacts: ")
    assert "game_total" in gaps[0]


def test_gap_check_flags_zero_reshape(audit):
    rec = {
        "feature_cache": 1000,
        "score_ok": True,
        "score_adapter": {mc: True for mc in audit.MARKET_CATEGORIES},
        "reshape": 0,
        "replay_ok": False,
        "replay_scored": 0,
        "grid_ok": True,
        "league": "NBA",
    }
    gaps = audit._gap_check(rec)
    assert any("reshape" in g for g in gaps)


def test_gap_check_flags_partial_replay(audit):
    """reshape>0 but replay_scored<reshape — only some rows carry
    the model_probability + model_version=team_xgb_v1 stamp."""
    rec = {
        "feature_cache": 1000,
        "score_ok": True,
        "score_adapter": {mc: True for mc in audit.MARKET_CATEGORIES},
        "reshape": 1000,
        "replay_ok": False,
        "replay_scored": 600,
        "grid_ok": True,
        "league": "NBA",
    }
    gaps = audit._gap_check(rec)
    assert any("replay" in g and "600" in g and "1,000" in g
                  for g in gaps)


def test_gap_check_flags_missing_grid(audit):
    rec = {
        "feature_cache": 1000,
        "score_ok": True,
        "score_adapter": {mc: True for mc in audit.MARKET_CATEGORIES},
        "reshape": 1000,
        "replay_ok": True,
        "replay_scored": 1000,
        "grid_ok": False,
        "league": "MLB",
    }
    gaps = audit._gap_check(rec)
    assert any("grid" in g and "MLB" in g for g in gaps)


def test_gap_check_returns_empty_for_healthy_sport(audit):
    rec = {
        "feature_cache": 1000,
        "score_ok": True,
        "score_adapter": {mc: True for mc in audit.MARKET_CATEGORIES},
        "reshape": 1000,
        "replay_ok": True,
        "replay_scored": 1000,
        "grid_ok": True,
        "league": "NBA",
    }
    assert audit._gap_check(rec) == []


def test_glyph_helper(audit):
    assert audit._glyph(True) == "✓"
    assert audit._glyph(False) == "✗"


def test_audit_script_main_entry_point_exists(audit):
    """Smoke: the script's CLI must expose `main()` so it works as
    a `python -m` module entry-point. Drift here breaks ops
    runbooks that pin the audit as an SSOT step."""
    assert callable(audit.main)
    assert callable(audit.amain)


def test_audit_script_is_executable_as_module():
    """The audit script must be importable as
    `scripts.team_pipeline_ssot_audit`. Drift breaks the CLI
    invocation `python -m scripts.team_pipeline_ssot_audit`."""
    p = Path("/app/backend/scripts/team_pipeline_ssot_audit.py")
    assert p.exists(), "audit script removed — runbook will break"
