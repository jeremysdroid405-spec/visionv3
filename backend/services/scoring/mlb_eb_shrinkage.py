"""Empirical-Bayes post-shrinkage for MLB HF projections on
zero-heavy rare-event stat families.

Behaviour (2026-04-24, behind feature flag MLB_HF_EB_SHRINKAGE_ENABLED):

    shrunk = w_model * raw_projection + w_player * player_career_mean

Only four stat families are whitelisted (home_runs, rbis, total_bases,
hits+runs+rbis). The HF projection is NOT modified when:

  • the flag is OFF (default)
  • the stat family is outside the whitelist
  • `bdl_player_id` is missing
  • the player has < `MIN_CAREER_GAMES` historical batter-AB games
  • the computed `shrunk` would be negative (defensive cap at 0.0)

No model retraining. No ECDF change. No gate change. The helper is a
pure post-predict adjustment — when applied, the ECDF call that
follows sees the shrunk projection, so probability_method counts do
not change.

Usage (from `mlb_scoring.py::build_context`):

    from services.scoring.mlb_eb_shrinkage import apply_eb_shrinkage
    shrunk, audit = apply_eb_shrinkage(
        db, bdl_player_id, stat_type, raw_projection=model_projection,
    )
    # `audit` is a dict with the six persistable fields.
    # `shrunk` is None when not applied; caller keeps the raw projection.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# ----- Config -----------------------------------------------------------

# Canonical stat_family → (w_model, w_player). Names must match the
# MLBHighFrictionModel._normalize_stat output for the whitelist to
# match at runtime.
_WEIGHTS: Dict[str, Tuple[float, float]] = {
    "home_runs": (0.30, 0.70),
    "rbis": (0.40, 0.60),
    "total_bases": (0.50, 0.50),
    "hits+runs+rbis": (0.60, 0.40),
}

# Only batter-AB games count toward career mean.
MIN_CAREER_GAMES = 20


def _normalize_stat(stat_type: str) -> str:
    """Mirror of MLBHighFrictionModel._normalize_stat (small alias map
    so callers don't have to import the model just to canonicalise)."""
    s = (stat_type or "").lower().replace(" ", "_")
    aliases = {
        "tb": "total_bases", "rbi": "rbis", "hr": "home_runs",
        "hrr": "hits+runs+rbis", "hits+runs+rbi": "hits+runs+rbis",
    }
    return aliases.get(s, s)


def flag_enabled() -> bool:
    return os.environ.get("MLB_HF_EB_SHRINKAGE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def stat_supported(stat_type: str) -> bool:
    return _normalize_stat(stat_type) in _WEIGHTS


def weights_for(stat_type: str) -> Tuple[float, float]:
    return _WEIGHTS[_normalize_stat(stat_type)]


def _career_mean_from_logs(
    logs: List[Dict[str, Any]],
    stat_family: str,
) -> Tuple[Optional[float], int]:
    """Return (career_mean, batter_game_count) for `stat_family`.
    Only counts games with at_bats>0 or plate_appearances>0.
    Returns (None, n) when < MIN_CAREER_GAMES qualifying games."""
    if not logs:
        return None, 0
    total = 0.0
    count = 0
    for log in logs:
        abs_ = log.get("at_bats")
        pa_ = log.get("plate_appearances")
        is_batter_log = (
            (abs_ is not None and abs_ > 0)
            or (pa_ is not None and pa_ > 0)
        )
        if not is_batter_log:
            continue
        if stat_family == "hits+runs+rbis":
            h, r, rbi = log.get("hits"), log.get("runs"), log.get("rbis")
            if h is None or r is None or rbi is None:
                continue
            total += float(h) + float(r) + float(rbi)
            count += 1
        else:
            v = log.get(stat_family)
            if v is None:
                continue
            total += float(v)
            count += 1
    if count < MIN_CAREER_GAMES:
        return None, count
    return total / count, count


# In-process cache so repeated lookups of the same player during a
# scoring pass don't re-hit Mongo.
_PLAYER_CACHE: Dict[int, Dict[str, Any]] = {}


def _lookup_player(db, bdl_player_id: int) -> Optional[Dict[str, Any]]:
    if bdl_player_id in _PLAYER_CACHE:
        return _PLAYER_CACHE[bdl_player_id]
    doc = db["mlb_master_hub_2026"].find_one(
        {"$or": [
            {"bdl_player_id": int(bdl_player_id)},
            {"bdl_id": int(bdl_player_id)},
        ]},
        {"_id": 0, "bdl_game_logs": 1, "bdl_player_id": 1, "bdl_id": 1},
    )
    _PLAYER_CACHE[bdl_player_id] = doc
    return doc


def reset_cache() -> None:
    _PLAYER_CACHE.clear()


def apply_eb_shrinkage(
    db,
    bdl_player_id: Optional[int],
    stat_type: str,
    raw_projection: Optional[float],
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Apply empirical-Bayes shrinkage to `raw_projection` for a single
    MLB prop. Returns `(shrunk_or_None, audit_dict)`.

    When `shrunk_or_None` is None, the caller retains the raw
    projection. The audit dict is always returned so the caller can
    persist the outcome (applied vs skipped + reason) for
    observability. `eb_shrinkage_applied=False` means the raw
    projection is in force.
    """
    audit: Dict[str, Any] = {
        "raw_hf_projection": (
            round(float(raw_projection), 4)
            if raw_projection is not None else None
        ),
        "eb_shrunk_projection": None,
        "eb_player_career_mean": None,
        "eb_weight_model": None,
        "eb_weight_player": None,
        "eb_shrinkage_applied": False,
        "eb_skip_reason": None,
        "eb_career_sample_n": 0,
    }

    if not flag_enabled():
        audit["eb_skip_reason"] = "flag_off"
        return None, audit
    if raw_projection is None:
        audit["eb_skip_reason"] = "no_raw_projection"
        return None, audit
    canon = _normalize_stat(stat_type)
    if canon not in _WEIGHTS:
        audit["eb_skip_reason"] = "stat_not_whitelisted"
        return None, audit
    if bdl_player_id is None:
        audit["eb_skip_reason"] = "missing_bdl_id"
        return None, audit

    try:
        player = _lookup_player(db, int(bdl_player_id))
    except Exception:
        player = None
    if not player:
        audit["eb_skip_reason"] = "player_not_found"
        return None, audit

    career_mean, n = _career_mean_from_logs(
        player.get("bdl_game_logs") or [], canon,
    )
    audit["eb_career_sample_n"] = int(n)
    if career_mean is None:
        audit["eb_skip_reason"] = f"insufficient_games_{n}<{MIN_CAREER_GAMES}"
        return None, audit

    w_model, w_player = _WEIGHTS[canon]
    shrunk = w_model * float(raw_projection) + w_player * float(career_mean)
    # Defensive floor — shrinkage should never pull negative.
    if shrunk < 0.0:
        shrunk = 0.0

    audit.update({
        "eb_shrunk_projection": round(shrunk, 4),
        "eb_player_career_mean": round(float(career_mean), 4),
        "eb_weight_model": w_model,
        "eb_weight_player": w_player,
        "eb_shrinkage_applied": True,
    })
    return round(shrunk, 6), audit
