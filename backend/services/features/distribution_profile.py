"""
Player distribution-profile features (2026-04-24).

Per-stat empirical `hit_N_rate` + explicit `zero_rate` features at
multiple thresholds, computed from a player's historical game logs
strictly BEFORE the target game. Teaches VK2 each player's real
outcome distribution — especially zero-outcome frequency on low-count
stats (3PM/STL/BLK), where the biggest missing signal is how often
the player records zero.

Design invariants
-----------------
- Uses `bdl_player_id`-scoped history only. Caller passes the player's
  descending-chronological history slice for the target game; we
  flip to ascending inside.
- Zero future leakage: every feature is computed on events that
  occurred strictly earlier than the target date.
- Shrinkage for small samples: L20 window pulls rates toward a uniform
  prior (p0=0.5) with pseudo-count α=3. L50 and career use the raw
  empirical rate (sample counts are large enough that shrinkage is a
  no-op).
- Minimum window size = 5 games. Shorter windows emit 0.5 for every
  rate feature and 0.0 for zero_rate. These samples are rare because
  the VK2 trainer already requires >= 5 history games.
- Deterministic feature order via FEATURE_SCHEMA. Safe to use as
  inputs to a fixed-schema XGBoost model.

Feature count
-------------
  PTS  : 1 zero + 8 hit = 9  × 3 windows = 27
  REB  : 1 + 7 = 8          × 3 = 24
  AST  : 1 + 6 = 7          × 3 = 21
  3PM  : 1 + 5 = 6          × 3 = 18
  PRA  : 1 + 10 = 11        × 3 = 33
  ----
  Total: 123 features
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence
from services.observability import log_silent_failure

THRESHOLDS: Dict[str, List[int]] = {
    "pts":   [1, 5, 10, 15, 20, 25, 30, 35],
    "reb":   [1, 3, 5, 7, 10, 12, 15],
    "ast":   [1, 2, 4, 6, 8, 10],
    "threes": [1, 2, 3, 4, 5],   # 3PM
    "pra":   [1, 10, 15, 20, 25, 30, 35, 40, 45, 50],
}
WINDOWS = (("L20", 20), ("L50", 50), ("career", None))
SHRINKAGE_ALPHA = 3.0
SHRINKAGE_PRIOR = 0.5
MIN_WINDOW_GAMES = 5


def _build_schema() -> List[str]:
    out: List[str] = []
    for stat in THRESHOLDS:
        for win_label, _ in WINDOWS:
            out.append(f"{stat}_zero_rate_{win_label}")
            for thr in THRESHOLDS[stat]:
                out.append(f"{stat}_hit_{thr}_rate_{win_label}")
    return out


FEATURE_SCHEMA: List[str] = _build_schema()
assert len(FEATURE_SCHEMA) == 123


def _value_for_stat(stat: str, row: Dict[str, Any]) -> float | None:
    """Extract the stat value from a BDL game log row. Handles the
    three name-conventions used across the codebase (`fg3m` vs
    `threes`, `pra` synthesised from PTS+REB+AST)."""
    if stat == "threes":
        v = row.get("fg3m")
        if v is None:
            v = row.get("threes")
    elif stat == "pra":
        p, r, a = row.get("pts"), row.get("reb"), row.get("ast")
        if p is None or r is None or a is None:
            return None
        try:
            return float(p) + float(r) + float(a)
        except (TypeError, ValueError):
            return None
    else:
        v = row.get(stat)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rate(hits: int, n: int, shrink: bool) -> float:
    if n <= 0:
        return SHRINKAGE_PRIOR
    if shrink:
        return (hits + SHRINKAGE_ALPHA * SHRINKAGE_PRIOR) / (n + SHRINKAGE_ALPHA)
    return hits / n


def _window_rates(values: List[float], thresholds: List[int],
                  shrink: bool) -> Dict[str, float]:
    """Given the stat values in ASCENDING chronological order for the
    chosen window, compute zero_rate and hit_N_rate for each threshold.
    Convention: hit_N means value >= N (so hit_1 means non-zero; 1 -
    hit_1 is exactly the zero rate, but we emit zero_rate explicitly
    for model legibility)."""
    n = len(values)
    if n < MIN_WINDOW_GAMES:
        # Not enough observations for a stable profile — emit priors.
        out = {"zero_rate": 0.0}
        for thr in thresholds:
            out[f"hit_{thr}_rate"] = SHRINKAGE_PRIOR
        return out
    zeros = sum(1 for v in values if v == 0.0)
    out = {"zero_rate": _rate(zeros, n, shrink=shrink)}
    for thr in thresholds:
        hits = sum(1 for v in values if v >= thr)
        out[f"hit_{thr}_rate"] = _rate(hits, n, shrink=shrink)
    return out


def build(history_logs: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Build the 123-dim distribution-profile feature dict.

    history_logs: the player's game-log slice BEFORE the target game.
    May be in descending OR ascending order — we detect by the first
    two `game_id` values and flip if needed. Games WITHOUT a usable
    stat value are skipped, not padded.

    Returns a dict keyed by FEATURE_SCHEMA entries.
    """
    if not history_logs:
        # Empty history — emit priors for every feature.
        return {name: SHRINKAGE_PRIOR if "hit_" in name else 0.0
                for name in FEATURE_SCHEMA}

    logs = list(history_logs)
    # Detect ordering. Desc → flip.
    if len(logs) >= 2:
        g0 = logs[0].get("game_id")
        g1 = logs[1].get("game_id")
        try:
            if g0 is not None and g1 is not None and int(g0) > int(g1):
                logs = list(reversed(logs))
        except (TypeError, ValueError) as _swept_exc:
            log_silent_failure("services.features.distribution_profile.build", _swept_exc)  # sweep-auto-converted

    out: Dict[str, float] = {}
    for stat, thresholds in THRESHOLDS.items():
        vals_all = [_value_for_stat(stat, row) for row in logs]
        vals_all = [v for v in vals_all if v is not None]
        for win_label, size in WINDOWS:
            if size is None:
                vals = vals_all
                shrink = False
            else:
                vals = vals_all[-size:] if len(vals_all) > size else vals_all
                shrink = (win_label == "L20")
            rates = _window_rates(vals, thresholds, shrink=shrink)
            out[f"{stat}_zero_rate_{win_label}"] = rates["zero_rate"]
            for thr in thresholds:
                out[f"{stat}_hit_{thr}_rate_{win_label}"] = rates[f"hit_{thr}_rate"]
    # Guard: every schema entry must be present.
    for name in FEATURE_SCHEMA:
        if name not in out:
            out[name] = SHRINKAGE_PRIOR
    return out
