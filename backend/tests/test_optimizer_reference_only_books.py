"""
Reference-only books contract in the optimizer (2026-06-02).

ROOT CAUSE this pins:
  PrizePicks and Underdog quote every prop at exactly +100. They are
  fixed-payout DFS pick'em platforms, NOT real sportsbooks. Including
  their rows in optimizer aggregation:
    1. Inflates ROI — every "win" pays +1 unit at +100 regardless of
       what the true line was at real books.
    2. Corrupts `avg_tp` / `calibration_delta` — averaging real prices
       with a fixed placeholder pulls every estimate toward 0.5.
    3. Inflates `n_bets` / `hit_rate` denominators with non-playable
       opportunities (the wins would never have been booked at +100
       at any real sportsbook).

CONTRACT pinned here:
  `_evaluate_combo` MUST skip rows whose `book` is in
  `REFERENCE_ONLY_BOOKS` when computing wins / losses / payouts /
  TP / CV / EDGE. The reference-only rows ARE counted into a separate
  `n_reference_only_skipped` audit field so the operator can verify
  the optimizer correctly ignored them.
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")
from routes.emergent_admin.optimizer import (  # noqa: E402
    REFERENCE_ONLY_BOOKS, _evaluate_combo, _is_reference_only,
)


def _row(book, tp=0.5, outcome=1, odds=-110):
    return {
        "event_id":  "E1",
        "player_name_normalized": "p1",
        "market":    "m",
        "side":      "OVER",
        "line":      1.5,
        "game_date": "2025-06-15",
        "book":      book,
        "tp":        tp,
        "cv":        0.2,
        "edge":      0.05,
        "odds":      odds,
        "outcome_numeric": outcome,
    }


def test_reference_only_set_contains_pp_and_underdog() -> None:
    assert "prizepicks" in REFERENCE_ONLY_BOOKS
    assert "underdog" in REFERENCE_ONLY_BOOKS


def test_is_reference_only_predicate() -> None:
    assert _is_reference_only({"book": "prizepicks"}) is True
    assert _is_reference_only({"book": "Underdog"}) is True  # case-insensitive
    assert _is_reference_only({"book": "draftkings"}) is False
    assert _is_reference_only({"book": None}) is False


def test_evaluate_combo_skips_prizepicks_in_math() -> None:
    """A pure PrizePicks/Underdog cell must score as if it had no rows."""
    rows = [_row("prizepicks", odds=100, outcome=1),
            _row("underdog", odds=100, outcome=1),
            _row("underdog", odds=100, outcome=1)]
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    # All three rows are reference-only; ZERO bets count toward math.
    # _evaluate_combo's min_bets gate operates on the qualifying-row
    # count BEFORE the reference-only filter, so it returns a result
    # — but every counter must be 0 / null and the audit field must
    # surface all 3 skipped rows.
    assert m is not None
    assert m["n_reference_only_skipped"] == 3
    assert m["n_bets"] == 0, (
        f"n_bets={m['n_bets']} — reference-only rows must NOT count as bets."
    )
    assert m["wins"] == 0
    assert m["losses"] == 0
    assert m["roi"] is None or m["roi"] == 0.0


def test_evaluate_combo_mixed_books_uses_only_real() -> None:
    """A mixed cell counts only the real-book rows; PrizePicks ignored."""
    rows = [_row("draftkings", odds=-110, outcome=1, tp=0.6),  # real WIN
            _row("draftkings", odds=-110, outcome=0, tp=0.4),  # real LOSS
            _row("prizepicks", odds=100,  outcome=1, tp=0.5),  # ignored
            _row("underdog",   odds=100,  outcome=1, tp=0.5)]  # ignored
    # Each row has a different (event,player,market,line,side,date)
    # only if event_id differs — give them distinct keys.
    for i, r in enumerate(rows):
        r["event_id"] = f"E{i}"
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    assert m is not None
    assert m["n_reference_only_skipped"] == 2
    assert m["n_bets"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 1
    # avg_tp computed from only the DK rows (0.6 + 0.4) / 2 = 0.5
    assert abs(m["avg_tp"] - 0.5) < 1e-6


def test_evaluate_combo_no_reference_book_rows() -> None:
    """Sanity: when no reference-only rows exist, the audit field is 0."""
    rows = [_row("draftkings", odds=-110, outcome=1, tp=0.6),
            _row("fanduel", odds=-110, outcome=0, tp=0.4)]
    for i, r in enumerate(rows):
        r["event_id"] = f"E{i}"
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    assert m is not None
    assert m["n_reference_only_skipped"] == 0
    assert m["n_bets"] == 2
