"""
Pure grader functions for team prop outcomes — h2h, spreads, totals, team_totals.

NO I/O. All inputs are plain dicts; outputs are plain dicts. Easy to unit-test.

Public API
    grade_h2h(side_id, home_score, away_score) -> Outcome
    grade_spread(side_id, line, home_score, away_score) -> Outcome
    grade_game_total(side_id, line, home_score, away_score) -> Outcome
    grade_team_total(side_id, stat_entity_id, line, home_score, away_score) -> Outcome
    grade_row(row, home_score, away_score) -> Outcome
        Dispatches to the right grader based on row['betTypeID'] and
        row['statEntityID']. Returns the unified Outcome dict shape.

An Outcome dict has the following keys, in all paths:
    outcome:           "WIN" | "LOSS" | "PUSH" | "UNRESOLVED"
    outcome_resolved:  bool
    outcome_numeric:   1 | 0 | 0.5 | None
    hit:               bool | None
    push:              bool | None
    actual_value:      float | None         (the team-stat value graded against)
    margin_vs_line:    float | None
    unresolved_reason: str | None            ("malformed_row"
                                                | "missing_score"
                                                | "missing_line"
                                                | "unknown_bet_type"
                                                | "unknown_side"
                                                | "non_numeric_score"
                                                | "non_numeric_line")
"""
from __future__ import annotations
from typing import Any, Dict, Optional


# ───── Outcome constructors ─────
def _unresolved(reason: str, actual: Optional[float] = None) -> Dict[str, Any]:
    return {
        "outcome":           "UNRESOLVED",
        "outcome_resolved":  False,
        "outcome_numeric":   None,
        "hit":               None,
        "push":              None,
        "actual_value":      actual,
        "margin_vs_line":    None,
        "unresolved_reason": reason,
    }


def _push(actual: float) -> Dict[str, Any]:
    return {
        "outcome":           "PUSH",
        "outcome_resolved":  True,
        "outcome_numeric":   0.5,
        "hit":               False,
        "push":              True,
        "actual_value":      actual,
        "margin_vs_line":    0.0,
        "unresolved_reason": None,
    }


def _win_loss(won: bool, actual: float, margin: float) -> Dict[str, Any]:
    return {
        "outcome":           "WIN" if won else "LOSS",
        "outcome_resolved":  True,
        "outcome_numeric":   1 if won else 0,
        "hit":               bool(won),
        "push":              False,
        "actual_value":      actual,
        "margin_vs_line":    margin,
        "unresolved_reason": None,
    }


# ───── coercion helpers ─────
def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _side_norm(side: Any) -> Optional[str]:
    if not isinstance(side, str):
        return None
    s = side.strip().lower()
    if s in ("home", "h"):       return "home"
    if s in ("away", "a"):       return "away"
    if s in ("over", "o", "yes", "y"): return "over"
    if s in ("under", "u", "no", "n"): return "under"
    return None


# ───── grader: h2h / moneyline ─────
def grade_h2h(side_id: Any, home_score: Any, away_score: Any) -> Dict[str, Any]:
    """ml / ml3way: bet on `side_id` to win outright.
    A regulation tie → PUSH on `ml`; on `ml3way` ties have their own
    "draw" side which we handle by treating any tie-side bet as a
    win when home==away, else loss."""
    hs = _num(home_score); as_ = _num(away_score)
    if hs is None or as_ is None:
        return _unresolved("missing_score")
    side = _side_norm(side_id)
    if side not in ("home", "away", "over", "under"):
        # Some NFL ml3way feeds use "draw"/"tie" as a side. Tolerate.
        if isinstance(side_id, str) and side_id.strip().lower() in (
                "draw", "tie", "x"):
            won = (hs == as_)
            margin = -abs(hs - as_) if not won else 0.0
            actual = abs(hs - as_)
            if won:
                return _win_loss(True, actual, 0.0)
            return _win_loss(False, actual, margin)
        return _unresolved("unknown_side")
    if side not in ("home", "away"):
        return _unresolved("unknown_side")
    margin_team = (hs - as_) if side == "home" else (as_ - hs)
    if margin_team == 0:
        # Regulation tie on a 2-way ml. Books usually push.
        return _push(margin_team)
    return _win_loss(margin_team > 0, margin_team, margin_team)


# ───── grader: spreads ─────
def grade_spread(side_id: Any, line: Any,
                  home_score: Any, away_score: Any,
                  stat_entity_id: Any = None) -> Dict[str, Any]:
    """sp: bet `side_id` to cover `line`. Line is given from the
    perspective of the side being bet on (e.g. side=HOME, line=-3.5
    means home -3.5; team must win by >3.5 to cover)."""
    hs = _num(home_score); as_ = _num(away_score)
    if hs is None or as_ is None:
        return _unresolved("missing_score")
    ln = _num(line)
    if ln is None:
        return _unresolved("missing_line")
    side = _side_norm(side_id)
    if side not in ("home", "away"):
        return _unresolved("unknown_side")
    # margin = your_team_score - opponent_score + line
    margin = ((hs - as_) if side == "home" else (as_ - hs)) + ln
    if margin == 0:
        return _push(margin)
    return _win_loss(margin > 0, margin, margin)


# ───── grader: game total ─────
def grade_game_total(side_id: Any, line: Any,
                      home_score: Any, away_score: Any) -> Dict[str, Any]:
    """ou with statEntityID=all: bet OVER/UNDER on home+away."""
    hs = _num(home_score); as_ = _num(away_score)
    if hs is None or as_ is None:
        return _unresolved("missing_score")
    ln = _num(line)
    if ln is None:
        return _unresolved("missing_line")
    side = _side_norm(side_id)
    if side not in ("over", "under"):
        return _unresolved("unknown_side")
    total = hs + as_
    if total == ln:
        return _push(total)
    margin = total - ln if side == "over" else ln - total
    return _win_loss(margin > 0, total, margin)


# ───── grader: team total ─────
def grade_team_total(side_id: Any, stat_entity_id: Any, line: Any,
                      home_score: Any, away_score: Any) -> Dict[str, Any]:
    """ou with statEntityID=home|away: bet OVER/UNDER on that team's
    score only."""
    hs = _num(home_score); as_ = _num(away_score)
    if hs is None or as_ is None:
        return _unresolved("missing_score")
    ln = _num(line)
    if ln is None:
        return _unresolved("missing_line")
    side = _side_norm(side_id)
    if side not in ("over", "under"):
        return _unresolved("unknown_side")
    ent = (stat_entity_id or "").strip().lower()
    if ent == "home":
        team_total = hs
    elif ent == "away":
        team_total = as_
    else:
        return _unresolved("unknown_side")
    if team_total == ln:
        return _push(team_total)
    margin = team_total - ln if side == "over" else ln - team_total
    return _win_loss(margin > 0, team_total, margin)


# ───── dispatcher ─────
def grade_row(row: Dict[str, Any],
                home_score: Any, away_score: Any) -> Dict[str, Any]:
    """Single entry point. Reads row['betTypeID'] + row['statEntityID']
    and dispatches to the right grader."""
    if not isinstance(row, dict):
        return _unresolved("malformed_row")
    bt = row.get("betTypeID")
    ent = row.get("statEntityID")
    side = row.get("side") or row.get("sideID")
    line = row.get("line")
    if not isinstance(bt, str):
        return _unresolved("malformed_row")
    bt_l = bt.strip().lower()
    if bt_l in ("ml", "ml3way"):
        return grade_h2h(side, home_score, away_score)
    if bt_l == "sp":
        return grade_spread(side, line, home_score, away_score,
                              stat_entity_id=ent)
    if bt_l == "ou":
        ent_l = (ent or "").strip().lower()
        if ent_l == "all":
            return grade_game_total(side, line, home_score, away_score)
        if ent_l in ("home", "away"):
            return grade_team_total(side, ent_l, line,
                                      home_score, away_score)
        return _unresolved("malformed_row")
    return _unresolved("unknown_bet_type")
