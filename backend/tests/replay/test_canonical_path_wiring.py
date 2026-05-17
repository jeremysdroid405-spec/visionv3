"""Phase 6 Phase 2 — Canonical path wiring tests for production_replay_runner.

Unit tests for the `_build_canonical_eval_rows` helper and the
`canonical_path` flag contract on `run_production_replay`.

What these tests DO cover:
  - Raw Layer-3 rows collapse to ONE eval row per canonical prop × side.
  - Std + alt market rows merge to the canonical_market_key.
  - Multi-book OVER/UNDER coverage is preserved on the attached
    CanonicalProp.
  - Best-book / best-price is promoted onto the evaluation row.
  - Canonical engine version pin is stable.
  - `__canonical_prop__` attachment carries the right aggregates.

What these tests DO NOT cover (out of Phase 2 scope):
  - Live serving wiring (Phase 3).
  - Cross-book opposite-side TP (Phase 4 of canonical project).
  - End-to-end DB-backed sweep — that is the 2026-05-05 SH-only parity
    sweep the user runs after this wiring lands.
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")

import pytest

from services.replay.production_replay_runner import (
    _build_canonical_eval_rows,
    CANONICAL_ENGINE_VERSION,
)
from services.canonical.canonical_prop import CanonicalProp


# ── Minimal Layer-3 row factory ────────────────────────────────────
def _layer3_row(**kw):
    """Build a minimal mlb_replay_model_outputs-shaped dict."""
    base = dict(
        sport="mlb",
        game_date="2026-05-05",
        snapshot_iso="2026-05-05T11:00:00Z",
        event_id="evt-2026-05-05-ATL-NYM",
        home_team="ATL", away_team="NYM",
        commence_time="2026-05-05T19:10:00Z",
        player_name="Matt Olson",
        player_name_normalized="matt olson",
        stat_family="total_bases",
        market="batter_total_bases",
        is_alternate=False,
        line=1.5,
        side="OVER",
        book="draftkings",
        odds=-180,
        projection_mu=2.05,
        sigma=1.40,
        model_probability=0.61,
        fair_probability=0.59,
        implied_probability=0.64,
        edge=-0.05,
        hit_rate_l5=60.0,
        hit_rate_l10=55.0,
        hit_rate_l20=50.0,
        cv=0.55,
    )
    base.update(kw)
    return base


def test_canonical_engine_version_pin_stable():
    """The version pin must NOT change without a CHANGELOG entry."""
    assert CANONICAL_ENGINE_VERSION == "canonical_v2_phase4_2026_05_17"


def test_empty_input_returns_empty():
    eval_rows, summary = _build_canonical_eval_rows([], sport="mlb")
    assert eval_rows == []
    assert summary["canonical_props_built"] == 0
    assert summary["canonical_eval_rows"] == 0
    assert summary["raw_rows_collapsed_from"] == 0
    assert summary["canonical_engine_version"] == CANONICAL_ENGINE_VERSION


def test_three_books_one_side_collapse_to_one_eval_row():
    """3 DK/FD/MGM OVER rows → 1 canonical prop → 1 OVER eval row."""
    rows = [
        _layer3_row(book="draftkings", odds=-180),
        _layer3_row(book="fanduel",    odds=-175),  # best price for bettor
        _layer3_row(book="betmgm",     odds=-200),
    ]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["raw_rows_collapsed_from"] == 3
    assert summary["canonical_props_built"] == 1
    assert summary["canonical_eval_rows"] == 1
    ev = eval_rows[0]
    assert ev["side"] == "OVER"
    # Best book promoted (fanduel = least negative)
    assert ev["book"] == "fanduel"
    assert ev["odds"] == -175
    assert ev["market"] == "batter_total_bases"
    assert ev["is_alternate"] is False
    # Canonical attached
    cp = ev["__canonical_prop__"]
    assert isinstance(cp, CanonicalProp)
    assert cp.book_count_over == 3
    assert cp.book_count_under == 0
    assert cp.best_over_book == "fanduel"
    assert cp.best_over_price == -175


def test_std_plus_alt_collapse_to_canonical_market_key():
    """std batter_hits + alt batter_hits_alternate → one canonical row."""
    rows = [
        _layer3_row(market="batter_hits",            line=0.5, stat_family="hits",
                    book="draftkings", odds=-200),
        _layer3_row(market="batter_hits_alternate",  line=0.5, stat_family="hits",
                    book="fanduel",    odds=-180, is_alternate=True),
    ]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["canonical_props_built"] == 1
    assert summary["canonical_eval_rows"] == 1
    ev = eval_rows[0]
    # Canonical key is the alt-stripped root.
    assert ev["market"] == "batter_hits"
    assert ev["is_alternate"] is False
    # Best of (-200, -180) is -180 (fanduel)
    assert ev["book"] == "fanduel"
    assert ev["odds"] == -180
    cp = ev["__canonical_prop__"]
    assert cp.canonical_market_key == "batter_hits"
    assert cp.source_rows_count == 2
    assert "batter_hits" in cp.source_market_keys
    assert "batter_hits_alternate" in cp.source_market_keys


def test_over_and_under_produce_two_eval_rows():
    """One canonical prop with both sides → 2 eval rows (OVER, UNDER)."""
    rows = [
        _layer3_row(book="draftkings", side="OVER",  odds=-180),
        _layer3_row(book="fanduel",    side="UNDER", odds=+170),
    ]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["canonical_props_built"] == 1
    assert summary["canonical_eval_rows"] == 2
    sides = sorted(ev["side"] for ev in eval_rows)
    assert sides == ["OVER", "UNDER"]
    # OVER eval row carries the OVER best book; UNDER its UNDER best book.
    over_row = next(ev for ev in eval_rows if ev["side"] == "OVER")
    under_row = next(ev for ev in eval_rows if ev["side"] == "UNDER")
    assert over_row["book"] == "draftkings" and over_row["odds"] == -180
    assert under_row["book"] == "fanduel" and under_row["odds"] == 170
    # Cross-book devig flag set on the shared canonical prop.
    cp_over = over_row["__canonical_prop__"]
    cp_under = under_row["__canonical_prop__"]
    assert cp_over is cp_under  # same canonical attached on both sides
    assert cp_over.has_cross_book_devig is True
    assert cp_over.devig_over_probability is not None
    assert cp_over.devig_under_probability is not None


def test_distinct_lines_produce_distinct_canonicals():
    """Same player + stat, different lines → separate canonical props."""
    rows = [
        _layer3_row(line=1.5, book="draftkings", odds=-180),
        _layer3_row(line=2.5, book="draftkings", odds=+140),
    ]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["canonical_props_built"] == 2
    assert summary["canonical_eval_rows"] == 2
    lines = sorted(ev["line"] for ev in eval_rows)
    assert lines == [1.5, 2.5]


def test_distinct_players_produce_distinct_canonicals():
    rows = [
        _layer3_row(player_name="Matt Olson", player_name_normalized="matt olson"),
        _layer3_row(player_name="Ozzie Albies",
                    player_name_normalized="ozzie albies"),
    ]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["canonical_props_built"] == 2
    assert summary["canonical_eval_rows"] == 2


def test_unknown_market_silently_skipped():
    """Unknown markets must NOT silently default — they drop out."""
    rows = [_layer3_row(market="not_a_real_market")]
    eval_rows, summary = _build_canonical_eval_rows(rows, sport="mlb")
    assert summary["canonical_props_built"] == 0
    assert summary["canonical_eval_rows"] == 0


def test_canonical_path_implies_universal_gate_path_signature():
    """`canonical_path=True` MUST imply `gate_path='universal'`.

    Smoke check on the function signature — we can't call the full
    runner from a unit test (needs DB + Layer-3 model). This asserts
    the parameter exists and defaults to False.
    """
    from services.replay.production_replay_runner import run_production_replay
    import inspect
    sig = inspect.signature(run_production_replay)
    assert "canonical_path" in sig.parameters
    assert sig.parameters["canonical_path"].default is False
    # gate_path must still be there with its legacy default.
    assert sig.parameters["gate_path"].default == "legacy_wz"


def test_canonical_prop_attached_carries_all_aggregates():
    """The CanonicalProp attached on eval_rows must carry the audit
    aggregates we stamp on the persisted output doc."""
    rows = [
        _layer3_row(book="draftkings", side="OVER", odds=-180),
        _layer3_row(book="fanduel",    side="OVER", odds=-175),
        _layer3_row(book="betmgm",     side="OVER", odds=-200),
        _layer3_row(book="draftkings", side="UNDER", odds=+155),
        _layer3_row(book="fanduel",    side="UNDER", odds=+150),
    ]
    eval_rows, _ = _build_canonical_eval_rows(rows, sport="mlb")
    over = next(ev for ev in eval_rows if ev["side"] == "OVER")
    cp = over["__canonical_prop__"]
    # All aggregate audit fields populated:
    assert cp.book_count_over == 3
    assert cp.book_count_under == 2
    assert cp.book_count_either_side_any_book == 3   # DK ∪ FD ∪ MGM
    assert cp.book_count_both_sides_same_book == 2   # DK + FD have both sides
    assert cp.best_over_book == "fanduel"
    assert cp.best_over_price == -175
    assert cp.best_under_book == "draftkings"
    assert cp.best_under_price == 155
    assert cp.has_cross_book_devig is True
    assert cp.has_same_book_devig is True
    assert cp.source_rows_count == 5
