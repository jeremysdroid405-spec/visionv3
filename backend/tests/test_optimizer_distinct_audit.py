"""
Pin the audit fields added 2026-05-24 in response to the operator
discovering a 100%/58-bet stored result that recomputed to 64%/19.

`_evaluate_combo` MUST now return:
  - n_distinct_bets    : dedup by (event, player, market, side, line, date)
  - wins_distinct      : wins counted at the distinct-key level
  - losses_distinct
  - hit_rate_distinct  : wins_distinct / (wins_distinct + losses_distinct)

These let the operator detect cross-book duplication artifacts at a
glance: when `wins / n_bets` >> `wins_distinct / n_distinct_bets`,
the same physical bet is being counted N times (once per anchor
book) and the headline number is overstated.
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


def test_distinct_audit_fields_match_n_bets_when_no_duplication():
    """When every row is a unique bet, the distinct counts must
    equal the raw counts."""
    rows = [
        _row(event_id="e1", player_name_normalized="p1", outcome_numeric=1),
        _row(event_id="e2", player_name_normalized="p2", outcome_numeric=1),
        _row(event_id="e3", player_name_normalized="p3", outcome_numeric=0),
    ]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m["n_bets"] == 3
    assert m["n_distinct_bets"] == 3
    assert m["wins_distinct"] == 2
    assert m["losses_distinct"] == 1
    assert m["hit_rate_distinct"] == 2 / 3


def test_distinct_audit_catches_cross_book_duplication():
    """The exact bug from the screenshot: same physical bet appears
    4× (once per anchor book) → n_bets=4, but n_distinct_bets=1.
    Headline HR is 100%, distinct HR is also 100% — but the operator
    needs to SEE the duplication so they don't think the sample is 4
    independent observations."""
    same_bet = lambda book: _row(  # noqa: E731
        event_id="e1", player_name_normalized="player_a",
        market="batter_hits", side="OVER", line=0.5,
        game_date="2025-05-01", outcome_numeric=1,
        anchor_book=book)
    rows = [same_bet("draftkings"), same_bet("fanduel"),
              same_bet("betmgm"),     same_bet("caesars")]
    m = _evaluate_combo(rows, {}, min_bets=1)
    assert m["n_bets"] == 4
    assert m["n_distinct_bets"] == 1, (
        f"expected 1 distinct bet (same prop × 4 books), got "
        f"{m['n_distinct_bets']}")
    assert m["wins_distinct"] == 1
    assert m["losses_distinct"] == 0
    # When n_bets >> n_distinct_bets that's a duplication red flag —
    # the operator's audit view surfaces both numbers.
    assert m["n_bets"] / max(m["n_distinct_bets"], 1) == 4.0


def test_hit_rate_distinct_handles_zero_settled():
    """If all rows are pushes/ungraded, distinct hit rate is None
    (no settled outcomes to divide by)."""
    rows = [_row(outcome_numeric=0.5), _row(outcome_numeric=None,
                                                          event_id="e2")]
    m = _evaluate_combo(rows, {}, min_bets=1)
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
    assert m["n_distinct_bets"] == 5, (
        f"all 5 rows differ on at least one key field — expected 5 "
        f"distinct keys, got {m['n_distinct_bets']}")
