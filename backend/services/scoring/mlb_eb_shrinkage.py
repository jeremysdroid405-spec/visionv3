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
        master_hub=hf_model.master_hub,  # SYNC pymongo collection
        bdl_player_id=bdl_player_id,
        stat_type=stat_type,
        raw_projection=model_projection,
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

# ----- Sample-size policy (2026-04-30) ----------------------------------
# Earlier behaviour: hard floor of 20 games — anything below skipped EB
# entirely. That's brittle for two real-world cases:
#   1. New-season early weeks (every player has < 20 logs)
#   2. Mid-career callups / new starters (e.g. Bleday today with 18)
# In both, the model can produce wildly inflated projections WITH NO EB
# safety net. We replace the floor with a smooth sample-size ramp:
#
#     ramp = min(n / CAP_AT_GAMES, 1.0)
#     w_player_effective = static_w_player * ramp
#     w_model_effective  = 1.0 - w_player_effective
#
# At n=0..2 we still skip (too noisy to derive any prior — see
# `_career_mean_from_logs`). At n=3..19 we apply a partial ramp; from
# n=20 the weights match the legacy `_WEIGHTS` table exactly, so this
# is a strict superset (no behaviour change for players with ≥ 20
# games).
CAP_AT_GAMES = 20            # ramp saturates here (unchanged behaviour at/above)
MIN_GAMES_FOR_SHRINK = 3     # below this, n=1 / n=2 noise → skip entirely

# Backwards-compat alias (was previously used as the hard floor).
# Kept so external callers / tests that imported the name still work.
MIN_CAREER_GAMES = CAP_AT_GAMES


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
    """Return (rolling_mean, batter_game_count) for `stat_family`.

    Computes the mean over the **last `CAP_AT_GAMES` (20) batter
    games** — a rolling window that slides forward each game so the
    prior reflects the player's CURRENT form, not their first 20
    games of the season locked in forever.

    Sort order is enforced here (DESC by `date`) so the helper is
    independent of the upstream collection's ordering. BDL game logs
    are ingested in DESC order today, but explicit sort guards against
    a silent regression if that ever changes — without the sort, the
    "rolling" window could become "first 20 games" and the user would
    never see it.

    Only counts games with at_bats>0 or plate_appearances>0
    (excludes pitching-only appearances). Returns (None, n) when
    n < `MIN_GAMES_FOR_SHRINK` — below this floor we don't trust
    any career-mean estimate (1-2 games is too noisy to shrink toward,
    regardless of weight).
    """
    if not logs:
        return None, 0

    # Step 1: filter to batter games (have AB or PA).
    batter_games: List[Dict[str, Any]] = []
    for log in logs:
        abs_ = log.get("at_bats")
        pa_ = log.get("plate_appearances")
        if (abs_ is not None and abs_ > 0) or (pa_ is not None and pa_ > 0):
            batter_games.append(log)

    if not batter_games:
        return None, 0

    # Step 2: sort DESC by date so [:CAP_AT_GAMES] = last 20 games.
    # Falls back to original list order when `date` is missing /
    # unparseable — defensive only; production logs always carry it.
    def _date_key(log: Dict[str, Any]):
        v = log.get("date")
        # ISO-8601 strings sort lexically in chronological order so
        # this works without parsing into datetime.
        return v if isinstance(v, str) else ""
    batter_games.sort(key=_date_key, reverse=True)

    # Step 3: take the rolling window (last CAP_AT_GAMES games).
    window = batter_games[:CAP_AT_GAMES]

    # Step 4: compute mean over `stat_family`. Skip rows missing the
    # required field (rare — but a partial log shouldn't blow up the
    # whole computation).
    total = 0.0
    count = 0
    for log in window:
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

    if count < MIN_GAMES_FOR_SHRINK:
        return None, count
    return total / count, count


# In-process cache so repeated lookups of the same player during a
# scoring pass don't re-hit Mongo.
_PLAYER_CACHE: Dict[int, Dict[str, Any]] = {}


def _lookup_player(master_hub, bdl_player_id: int) -> Optional[Dict[str, Any]]:
    """`master_hub` is a SYNC pymongo collection (not a motor handle).
    Caller is expected to pass `hf_model.master_hub` from
    `MLBHighFrictionModel` which already owns a synchronous pymongo
    client initialised in `_get_hf_model()`."""
    if bdl_player_id in _PLAYER_CACHE:
        return _PLAYER_CACHE[bdl_player_id]
    doc = master_hub.find_one(
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
    master_hub,
    bdl_player_id: Optional[int],
    stat_type: str,
    raw_projection: Optional[float],
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Apply empirical-Bayes shrinkage to `raw_projection` for a single
    MLB prop. Returns `(shrunk_or_None, audit_dict)`.

    `master_hub` MUST be a synchronous pymongo collection (the
    `mlb_master_hub_2026` collection). Caller in production is
    `MLBScoringAdapter.build_context`, which already owns a
    sync-pymongo HF model and can reuse its `master_hub` attribute.

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
        # NOTE on naming (2026-04-30): `eb_player_career_mean` is now
        # the **rolling last-CAP_AT_GAMES** batter-game mean — NOT a
        # full-career mean. The legacy field name is preserved because
        # `prop_scores_store`, `recompute`, and `dashboard_card_contract`
        # all read it; renaming would require a coordinated migration.
        # Treat it as "rolling-N prior" everywhere.
        "eb_player_career_mean": None,
        "eb_weight_model": None,
        "eb_weight_player": None,
        # Sample-size ramp factor (0..1). 1.0 means n >= CAP_AT_GAMES so
        # weights match the legacy static table; <1 means partial ramp.
        # `None` if shrinkage was skipped before the ramp could be
        # computed.
        "eb_weight_ramp": None,
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
        player = _lookup_player(master_hub, int(bdl_player_id))
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
        # `_career_mean_from_logs` returns None only when n < MIN_GAMES_FOR_SHRINK.
        audit["eb_skip_reason"] = (
            f"insufficient_games_{n}<{MIN_GAMES_FOR_SHRINK}"
        )
        return None, audit

    # ── Sample-size ramp (2026-04-30) ───────────────────────────────
    # The static `_WEIGHTS` table gives the asymptotic player-vs-model
    # split for n ≥ CAP_AT_GAMES. For smaller samples we trust the
    # career-mean LESS, scaling its weight linearly toward zero as
    # n → 0. Any weight removed from the player side is added to the
    # model side so the two always sum to 1.0.
    static_w_model, static_w_player = _WEIGHTS[canon]
    ramp = min(float(n) / float(CAP_AT_GAMES), 1.0)
    w_player_eff = static_w_player * ramp
    w_model_eff = 1.0 - w_player_eff

    shrunk = w_model_eff * float(raw_projection) + w_player_eff * float(career_mean)
    # Defensive floor — shrinkage should never pull negative.
    if shrunk < 0.0:
        shrunk = 0.0

    audit.update({
        "eb_shrunk_projection": round(shrunk, 4),
        "eb_player_career_mean": round(float(career_mean), 4),
        "eb_weight_model": round(w_model_eff, 4),
        "eb_weight_player": round(w_player_eff, 4),
        "eb_weight_ramp": round(ramp, 4),
        "eb_shrinkage_applied": True,
    })
    return round(shrunk, 6), audit
