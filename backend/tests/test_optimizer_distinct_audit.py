"""
SSOT v2 distinct-bet metrics contract (2026-05-26).

Pins the headline-metrics-are-distinct guarantee added to `_evaluate_combo`
in response to the "100% / 58" inflation bug. Under SSOT v2:

  Headline (UI / ranking inputs):
    n_bets, n_graded, n_ungraded, wins, losses, pushes, hit_rate, roi
    All are counted at the unique-opportunity level
    `(event_id, player, market, side, line, game_date)`.

  Raw row counters (audit only; never drive ranking):
    n_bets_raw_rows, wins_raw_rows, losses_raw_rows, pushes_raw_rows,
    ungraded_raw_rows, n_with_odds_raw_rows
    These expose how many book × snapshot rows backed each unique bet
    so the operator can detect duplication (e.g. n_bets=14 / raw=58
    means "14 unique bets × ~4 books").

  Legacy aliases (still emitted, equal to the headline distincts):
    n_distinct_bets, wins_distinct, losses_distinct, pushes_distinct,
    hit_rate_distinct
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import _evaluate_combo


def _row(**kw):
    base = {
        "hit_rate_l20": 80.0, "hit_rate_l10": 70.0, "hit_rate_l5": 70.0,
        "cv": 0.5, "edge": 0.10, "tp": 0.65,
        "outcome_numeric": 1, "odds": -200, "line": 0.5,
        "side": "OVER", "market": "batter_hits",
        "player_name_normalized": "player_a",
        "event_id": "evt1", "game_date": "2025-05-01",
    }
    base.update(kw)
    return base


def test_no_duplication_distinct_equals_raw():
    """When every row is a unique bet, distinct counts == raw counts."""
    rows = [
        _row(event_id="e1", player_name_normalized="p1", outcome_numeric=1),
        _row(event_id="e2", player_name_normalized="p2", outcome_numeric=1),
        _row(event_id="e3", player_name_normalized="p3", outcome_numeric=0),
    ]
    m = _evaluate_combo(rows, {}, min_bets=1)
    # Headline (SSOT v2)
    assert m["n_bets"] == 3
    assert m["wins"]   == 2
    assert m["losses"] == 1
    assert m["hit_rate"] == 2 / 3
    # Raw rows match because there's no duplication
    assert m["n_bets_raw_rows"]  == 3
    assert m["wins_raw_rows"]    == 2
    assert m["losses_raw_rows"]  == 1


def test_cross_book_duplication_collapses_to_one_bet():
    """The exact bug from the screenshot: same physical bet appears
    4× (once per anchor book). Headline n_bets/wins/hit_rate MUST
    reflect ONE bet, not four. Raw counts surface the duplication.
    """
    same_bet = lambda book: _row(  # noqa: E731
        event_id="e1", player_name_normalized="player_a",
        market="batter_hits", side="OVER", line=0.5,
        game_date="2025-05-01", outcome_numeric=1,
        anchor_book=book)
    rows = [same_bet("draftkings"), same_bet("fanduel"),
              same_bet("betmgm"),     same_bet("caesars")]
    m = _evaluate_combo(rows, {}, min_bets=1)
    # Headline — ONE unique bet, ONE win, 100% on n=1
    assert m["n_bets"] == 1, (
        f"expected 1 unique bet (same prop × 4 books); got {m['n_bets']}")
    assert m["wins"] == 1
    assert m["losses"] == 0
    assert m["hit_rate"] == 1.0
    # Raw rows surface the 4× duplication — operator-visible audit
    assert m["n_bets_raw_rows"] == 4
    assert m["wins_raw_rows"]   == 4
    # Legacy aliases agree
    assert m["n_distinct_bets"] == 1
    assert m["wins_distinct"]   == 1


def test_roi_uses_mean_payout_across_books_per_unique_bet():
    """ROI must reflect ONE bet per unique opportunity, with the
    payout averaged across the books that quoted it (= what a real
    bettor placing one bet at "the available market" would realize).
    NOT the sum of all per-book payouts (which would inflate ROI by
    a factor of K)."""
    # Same bet on 4 books, all win — payouts at -200 odds = +0.5 units each.
    rows = [_row(event_id="e1", odds=-200, outcome_numeric=1)
              for _ in range(4)]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m["n_bets"] == 1
    assert m["wins"] == 1
    # Mean payout across 4 backing rows = +0.5 units; one unique bet.
    # ROI = 0.5 / 1 = 0.5.  Old (buggy) behaviour would have given 2.0.
    assert abs(m["roi"] - 0.5) < 1e-9, (
        f"ROI must be per-unique-bet mean payout, got {m['roi']}")


def test_hit_rate_none_when_no_settled_outcomes():
    """If all rows are pushes/ungraded, hit_rate is None."""
    rows = [_row(outcome_numeric=0.5), _row(outcome_numeric=None,
                                                          event_id="e2")]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m["hit_rate"] is None
    assert m["hit_rate_distinct"] is None


def test_distinct_keys_use_event_player_market_side_line_date():
    """Lock the distinct-key tuple — must include every field that
    makes a bet unique. Missing any one would falsely conflate
    different bets."""
    base = _row(event_id="e1", player_name_normalized="p1",
                  market="batter_hits", side="OVER", line=0.5,
                  game_date="2025-05-01", outcome_numeric=1)
    different_side = {**base, "side": "UNDER"}
    different_line = {**base, "line": 1.5}
    different_market = {**base, "market": "batter_strikeouts"}
    different_day  = {**base, "game_date": "2025-05-02"}
    rows = [base, different_side, different_line,
              different_market, different_day]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m["n_bets"] == 5, (
        f"all 5 rows differ on at least one key field — expected 5 "
        f"distinct bets, got {m['n_bets']}")


def test_metrics_version_v2():
    """Cell emits metrics_version='v2_distinct' so downstream consumers
    can detect / handle the schema."""
    rows = [_row()]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m.get("metrics_version") == "v2_distinct"
