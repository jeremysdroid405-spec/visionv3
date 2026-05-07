"""
Hit Profile  —  Single source of truth for empirical L10 hit-rate display
=========================================================================

Why this exists
---------------
Before 2026-04-29 the dashboard pick card displayed `hit_rate = pick.hit_rate_over`
(a model-derived L20 probability from `services/scoring/scoring_stack.py`).
The graph (`frontend/.../GameLogBarChart.jsx`) computed its own bars from
`bdl_game_logs` empirically. The two could (and did) disagree — e.g. Vucevic
P+R 9.5 displayed Hit Rate **75%** while the graph rendered **5/10 (50%)**.

`compute_hit_profile` is the ONE function the dashboard card and the graph
agree on. Both render from the value it returns; they cannot drift.

Contract
--------
- Window: most-recent **10** games.
- Rule: stat_value >= line  ⇒  HIT (matches `GameLogBarChart` line 201).
- Identical stat-type → log-field map as `services/dashboard_card_contract`
  uses for the avg backfill, so the avg shown on the card and the avg of
  the bars in the chart are computed from the SAME 10 values.

What this module does NOT touch
-------------------------------
* No scoring formulas, μ, σ, gates, thresholds, tier-routing, pick-selection.
* No model state — `pick.hit_rate_over` stays on the prop and is still
  consumed by `ranking_score_v2` and downstream selection logic. Only the
  `pick.hit_rate` *display* field is rewritten.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

# ─── Stat-type → game-log field maps ──────────────────────────────────
# Keep in sync with services.dashboard_card_contract _NBA_LOG_FIELD /
# _MLB_LOG_FIELD. We re-export them from there to avoid drift.
from services.dashboard_card_contract import (
    _NBA_LOG_FIELD,
    _MLB_LOG_FIELD,
    _extract_stat_value,
)

# Public rule label — the graph uses `>=`, so this module does too.
HIT_RULE: str = ">="
WINDOW: int = 10


def _coerce_line(line: Any) -> Optional[float]:
    """Cast a card line to float; return None if uncastable / null."""
    if line is None:
        return None
    try:
        return float(line)
    except (TypeError, ValueError):
        return None


def compute_hit_profile(
    games: List[Mapping[str, Any]],
    line: Any,
    stat_type: str,
    sport: str = "nba",
    window: int = WINDOW,
) -> Dict[str, Any]:
    """Compute the empirical L<window> hit profile for a card.

    Parameters
    ----------
    games:      A list of game-log dicts. Will be sorted newest-first by
                `date` then `game_id`. Stat values are extracted via the
                same `_extract_stat_value` helper used by the avg backfill.
    line:       The card line (float / int / numeric string).
    stat_type:  Pick stat-type (e.g. "P+R", "Pts+Rebs", "Earned Runs",
                "Hits"). Looked up via `_NBA_LOG_FIELD` / `_MLB_LOG_FIELD`.
    sport:      "nba" | "mlb" (default "nba").
    window:     L-window size (default 10).

    Returns
    -------
    dict with keys:
        values        list[float]  — newest-first, length ≤ window
        hit_count     int          — count where value >= line
        total         int          — len(values) (≤ window)
        hit_rate_pct  float        — hit_count / total × 100, rounded 1dp
        avg           float|None   — mean(values), rounded 1dp
        line          float|None   — same line passed in
        rule          str          — ">=" (HIT_RULE)
        window        int          — the window size used
    """
    line_f = _coerce_line(line)
    s = (sport or "").lower()

    # Sort newest-first using the same key the graph & avg-backfill use.
    sorted_games = sorted(
        games or [],
        key=lambda g: (g.get("date") or "", g.get("game_id") or 0),
        reverse=True,
    )

    # Extract per-game stat values using the canonical helper. None means
    # "no usable value" — skip; we want exactly `window` valid samples.
    values: List[float] = []
    for g in sorted_games:
        if len(values) >= window:
            break
        v = _extract_stat_value(stat_type, s, g)
        if v is None:
            continue
        values.append(float(v))

    total = len(values)
    if line_f is None or total == 0:
        return {
            "values": values,
            "hit_count": 0,
            "total": total,
            "hit_rate_pct": None,
            "avg": (round(sum(values) / total, 1) if total else None),
            "line": line_f,
            "rule": HIT_RULE,
            "window": window,
        }

    hit_count = sum(1 for v in values if v >= line_f)
    return {
        "values": values,
        "hit_count": hit_count,
        "total": total,
        "hit_rate_pct": round(100.0 * hit_count / total, 1),
        "avg": round(sum(values) / total, 1),
        "line": line_f,
        "rule": HIT_RULE,
        "window": window,
    }


def stamp_hit_profile_on_pick(
    pick: Dict[str, Any],
    games: List[Mapping[str, Any]],
    sport: str = "nba",
) -> Dict[str, Any]:
    """Stamp the canonical hit-profile fields onto a card-payload dict.

    Reads `pick['stat_type']` and `pick['line']`, computes the profile,
    and writes:

        pick['hit_rate']         (≡ hit_rate_pct, the displayed value)
        pick['l10_hit_count']    (the green-bar count the graph must match)
        pick['l10_total']        (denominator)
        pick['l10_values']       (the 10 raw values the graph charts)
        pick['avg']              (mean of the same 10 values)
        pick['hit_profile_line'] (the line the profile was computed against)
        pick['hit_profile_rule'] ('>=')

    Returns the mutated `pick`.
    """
    profile = compute_hit_profile(
        games=list(games or []),
        line=pick.get("line"),
        stat_type=pick.get("stat_type") or "",
        sport=sport,
    )

    if profile["hit_rate_pct"] is not None:
        # 2026-05-07 P0 Phase 4B: writes canonical `hit_rate_l10` (the
        # 10-game window the profile actually computes — `WINDOW=10`).
        # Legacy `hit_rate` (active-side alias) is no longer stamped;
        # frontend computes active-side from `hit_rate_over` /
        # `hit_rate_under` per Phase 4B SSOT spec.
        pick["hit_rate_l10"] = profile["hit_rate_pct"]
    if profile["avg"] is not None:
        pick["avg"] = profile["avg"]

    pick["l10_hit_count"] = profile["hit_count"]
    pick["l10_total"] = profile["total"]
    pick["l10_values"] = profile["values"]
    pick["hit_profile_line"] = profile["line"]
    pick["hit_profile_rule"] = profile["rule"]

    return pick


__all__ = [
    "HIT_RULE",
    "WINDOW",
    "compute_hit_profile",
    "stamp_hit_profile_on_pick",
]
