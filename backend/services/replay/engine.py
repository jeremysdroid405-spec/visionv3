"""
Replay scoring engine.

CRITICAL DESIGN PRINCIPLE
-------------------------
This module MUST call production scoring code directly:
    services.scoring.scoring_stack.compute_scoring_stack(...)
    services.scoring.tp_engine.compute_tp(...)
    services.scoring.scoring_stack._pick_reference_odds(...)
It MUST NOT reimplement vision_score / vision_v2 / tier / pp_utility / TP math.

PHASE 2.5 UPGRADE — group-by-canonical_key + TP wiring (2026-05-09 follow-up)
-----------------------------------------------------------------------------
Previous skeleton grouped by (event, snapshot, canonical_key, side) → reference
odds chain failed → 100% `tier=unqualified` / `tier_reason=no_reference_market`.
Refactored to group by (event, snapshot, canonical_key) and score BOTH sides
in one pass with paired flat odds keys, so:
  - `_pick_reference_odds()` finds a layer
  - `compute_tp()` finds same-book opposite prices and produces de-vigged TP
  - `edge_pct = p_true - implied(reference odds)` is computed
  - tier classification can fire

WHAT'S WIRED
------------
- Production `compute_scoring_stack()` call (no fork)
- Production `_pick_reference_odds()` (no fork)
- Production `compute_tp()` (no fork) — multi-book de-vig + one-sided fallback
- Production gate engine via `compute_tier` (called inside scoring_stack)
- Reference-odds chain via book layers built from real snapshots
- Leakage gates (assert_no_future_games, assert_pregame_only) — gate every
  feature build
- Resumable checkpointing (`replay_engine_progress`)
- Bulk-write chunking (200-op chunks)

WHAT'S STUBBED (`feature_completeness="partial"` once TP fires)
---------------------------------------------------------------
The full production feature set still requires historical reconstruction of:
    - VK2 player projections (vk2_projection / model_sigma)            DONE
    - Injury timeline (usage_vacuum_factor / usage_spike)               DONE
    - Matchup strength / pace factor                                    DONE
    - Avg hit/miss margin                                                P5
The minimal as-of features come from `bdl_historical_game_logs` only. Every
emitted evaluation carries `feature_completeness` so analytics can filter.
"""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

from services.scoring.scoring_stack import (
    _pick_reference_odds, compute_scoring_stack,
)
from services.scoring.tp_engine import compute_tp
from services.scoring.coverage_filter import classify_coverage
from .leakage_checks import (
    LeakageDetected, assert_no_future_games, assert_pregame_only,
)
from .vk2_historical import (
    PlayerIdResolver,
    REPLAY_FAMILY_TO_MODEL_KEY, VK2_UNSUPPORTED_FAMILIES, COMBO_COMPONENTS,
    predict_vk2_as_of, predict_combo_vk2_as_of,
)
from .cache import (
    REPLAY_VK2_CACHE, cache_row, ensure_cache_indexes, fingerprint_block,
)
from .matchup import (
    TeamIdResolver, compute_matchup_blob,
)
from .injury_history import (
    compute_team_injury_blob, compute_player_usage_spike,
    assemble_injury_blob,
)

logger = logging.getLogger(__name__)


REPLAY_EVALUATIONS = "replay_evaluations"
REPLAY_ENGINE_PROGRESS = "replay_engine_progress"
PROPS_NORMALIZED = "replay_props_normalized"
BDL_LOGS = "bdl_historical_game_logs"


FEATURE_COMPLETENESS_MINIMAL     = "minimal"     # μ/σ/CV/HR only
FEATURE_COMPLETENESS_PARTIAL     = "partial"     # + TP/edge wired
FEATURE_COMPLETENESS_VK2_PARTIAL = "vk2_partial" # + VK2 (no historical adv stats)
FEATURE_COMPLETENESS_VK2_FULL    = "vk2_full"    # + VK2 with adv stats >= 5/L10
FEATURE_COMPLETENESS_FULL        = "full"        # + injury/matchup (Phase 2.6+)

# Maps replay stat_family → BDL field name(s).
_BDL_FIELDS = {
    "PTS":     ("pts",),
    "REB":     ("reb",),
    "AST":     ("ast",),
    "THREES":  ("fg3m",),
    "BLK":     ("blk",),
    "STL":     ("stl",),
    "PTS_REB":   ("pts", "reb"),
    "PTS_AST":   ("pts", "ast"),
    "REB_AST":   ("reb", "ast"),
    "PRA":       ("pts", "reb", "ast"),
}

# Production book code → odds-key prefix used by tp_engine.compute_tp
_BOOK_TO_PREFIX = {
    "draftkings":     "dk",
    "fanduel":        "fd",
    "betmgm":         "mgm",
    "betonlineag":    "bol",
    # williamhill_us = Caesars; not in tp_engine's path-1 dict (only DK/FD/MGM/BOL)
    # so it contributes only via the `sharp_layer` to scoring_stack.
}


def _norm_name(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# ---------------------------------------------------------------- features
@dataclass
class AsOfFeatures:
    sample_size: int
    mu: Optional[float]
    sigma: Optional[float]
    cv: Optional[float]
    hit_rate_l5:  Optional[float]
    hit_rate_l10: Optional[float]
    hit_rate_l20: Optional[float]
    ceiling_rate: Optional[float]
    feature_completeness: str

    def asdict(self) -> Dict[str, Any]:
        return {**self.__dict__}


def _sum_for_family(g: Dict[str, Any], fields: Tuple[str, ...]) -> Optional[float]:
    total = 0.0
    for f in fields:
        v = g.get(f)
        if v is None:
            return None
        total += float(v)
    return total


async def build_as_of_features(
    db, *, player_norm: str, stat_family: str, line: float,
    side: str, as_of_ts: datetime,
    bdl_player_id: Optional[int] = None,
) -> AsOfFeatures:
    """Pull last 20 BDL game logs for `player_norm` strictly before
    `as_of_ts` and compute μ / σ / CV / hit-rate ladder.

    PARITY-TODO: This is the *minimal* feature set. Full production
    scoring also reads VK2, injury, usage_spike, matchup_strength, and
    pace_factor — none of which are implemented here. Every prop
    evaluated through this builder is stamped
    `feature_completeness="minimal"` so analytics can filter accordingly.

    `bdl_player_id`, when supplied, is used as the AUTHORITATIVE
    identity key — bypasses the slow name-strip path entirely. The
    name-fallback below is kept ONLY so unit tests with no resolver
    still work.
    """
    if as_of_ts.tzinfo is None:
        raise ValueError("as_of_ts must be tz-aware UTC")

    fields = _BDL_FIELDS.get(stat_family)
    if not fields:
        return AsOfFeatures(0, None, None, None, None, None, None, None,
                             FEATURE_COMPLETENESS_MINIMAL)

    cutoff_date_str = as_of_ts.date().isoformat()  # YYYY-MM-DD
    if bdl_player_id is not None:
        # Identity-keyed query — fast, exact, leakage-tight.
        cursor = db[BDL_LOGS].find(
            {"player_id": int(bdl_player_id),
             "date": {"$lt": cutoff_date_str}},
            projection={"_id": 0, "player_name": 1, "date": 1,
                        "min": 1, "pts": 1, "reb": 1, "ast": 1,
                        "fg3m": 1, "stl": 1, "blk": 1},
        ).sort("date", -1).limit(20)
        matched: List[Dict[str, Any]] = [g async for g in cursor]
        for g in matched:
            gd = g.get("date")
            if gd and gd >= cutoff_date_str:
                raise LeakageDetected(
                    f"feature builder loaded game date {gd} >= cutoff "
                    f"{cutoff_date_str} for player_id={bdl_player_id}")
    else:
        # Legacy name-strip fallback (slow, kept for unit tests).
        cursor = db[BDL_LOGS].find(
            {"date": {"$lt": cutoff_date_str}},
            projection={"_id": 0, "player_name": 1, "date": 1,
                        "min": 1, "pts": 1, "reb": 1, "ast": 1,
                        "fg3m": 1, "stl": 1, "blk": 1},
        ).sort("date", -1).limit(800)
        matched = []
        async for g in cursor:
            if _norm_name(g.get("player_name")) != player_norm:
                continue
            gd = g.get("date")
            if gd and gd >= cutoff_date_str:
                raise LeakageDetected(
                    f"feature builder loaded game date {gd} >= cutoff "
                    f"{cutoff_date_str} for player {player_norm}")
            matched.append(g)
            if len(matched) >= 20:
                break

    if not matched:
        return AsOfFeatures(0, None, None, None, None, None, None, None,
                             FEATURE_COMPLETENESS_MINIMAL)

    # Final defensive leakage assertion across the entire batch.
    assert_no_future_games(
        [{"game_date": g["date"]} for g in matched],
        as_of_ts=as_of_ts.replace(hour=0, minute=0, second=0, microsecond=0),
        timestamp_field="game_date",
    )

    values: List[float] = []
    for g in matched:
        v = _sum_for_family(g, fields)
        if v is not None:
            values.append(v)
    if not values:
        return AsOfFeatures(0, None, None, None, None, None, None, None,
                             FEATURE_COMPLETENESS_MINIMAL)

    mu = sum(values) / len(values)
    var = sum((v - mu) ** 2 for v in values) / len(values)
    sigma = math.sqrt(var) if var > 0 else None
    cv = (sigma / mu) if (sigma and mu and mu > 0) else None

    def _hr(window: int) -> Optional[float]:
        sub = values[:window]
        if not sub:
            return None
        if (side or "").upper() == "OVER":
            hits = sum(1 for v in sub if v > line)
        else:
            hits = sum(1 for v in sub if v < line)
        return hits / len(sub)

    hr5  = _hr(5)
    hr10 = _hr(10)
    hr20 = _hr(20)
    ceiling = max(filter(lambda x: x is not None,
                          [hr5, hr10, hr20]), default=None)

    return AsOfFeatures(
        sample_size=len(values),
        mu=round(mu, 3),
        sigma=round(sigma, 3) if sigma is not None else None,
        cv=round(cv, 4) if cv is not None else None,
        hit_rate_l5=hr5, hit_rate_l10=hr10, hit_rate_l20=hr20,
        ceiling_rate=ceiling,
        feature_completeness=FEATURE_COMPLETENESS_MINIMAL,
    )


# ---------------------------------------------------------------- book layers
def _empty_layer(line: Optional[float]) -> Dict[str, Any]:
    return {"line": line,
             "over_odds": None, "under_odds": None}


def collect_paired_layers(
    rows_for_canonical: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Optional[float]]:
    """Group rows for a single (event, snapshot, canonical_key) — i.e.
    ALL sides + ALL books — into per-book layer dicts that carry BOTH
    over_odds AND under_odds. This is what `_pick_reference_odds` and
    `compute_tp` both expect.
    """
    by_book: Dict[str, Dict[str, Any]] = {}
    line_seen: Optional[float] = None
    for r in rows_for_canonical:
        bk = r.get("bookmaker")
        if not bk:
            continue
        line = r.get("line")
        line_seen = line if line_seen is None else line_seen
        side = (r.get("side") or "").upper()
        odds = r.get("odds_american")
        layer = by_book.setdefault(bk, _empty_layer(line))
        if layer.get("line") is None:
            layer["line"] = line
        if side == "OVER":
            layer["over_odds"] = odds
        elif side == "UNDER":
            layer["under_odds"] = odds
    return by_book, line_seen


def populate_flat_odds(prop: Dict[str, Any],
                       *, by_book: Dict[str, Dict[str, Any]],
                       side: str) -> None:
    """tp_engine.compute_tp(path-1) reads `{prefix}_odds` (this side) and
    `{prefix}_odds_opp` (other side) flat off the prop. Populate them
    here per Phase-1 book whitelist."""
    side_u = (side or "").upper()
    for book_key, prefix in _BOOK_TO_PREFIX.items():
        layer = by_book.get(book_key) or {}
        over = layer.get("over_odds")
        under = layer.get("under_odds")
        if side_u == "OVER":
            prop[f"{prefix}_odds"]     = over
            prop[f"{prefix}_odds_opp"] = under
        else:
            prop[f"{prefix}_odds"]     = under
            prop[f"{prefix}_odds_opp"] = over


def _side_layer_for_pick(by_book: Dict[str, Dict[str, Any]],
                          *, side: str) -> Dict[str, Dict[str, Any]]:
    """`_pick_reference_odds` reads `layer["odds"]` (the picked-side price).
    Build a per-book copy that carries that single field for the chosen side.
    """
    side_u = (side or "").upper()
    out: Dict[str, Dict[str, Any]] = {}
    for book, lyr in by_book.items():
        side_price = (lyr.get("over_odds") if side_u == "OVER"
                      else lyr.get("under_odds"))
        if side_price is None:
            continue
        out[book] = {
            "line":      lyr.get("line"),
            "odds":      side_price,
            "over_odds": lyr.get("over_odds"),
            "under_odds": lyr.get("under_odds"),
        }
    return out


# ---------------------------------------------------------------- one-side scoring
def score_one_side(
    *,
    rows_for_canonical: List[Dict[str, Any]],
    side: str,
    line: float,
    stat_family: str,
    sport: str,
    feature_set: Optional[AsOfFeatures],
    vk2_blob: Optional[Dict[str, Any]] = None,
    matchup_blob: Optional[Dict[str, Any]] = None,
    injury_blob: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score a single (canonical_key, side) using full production stack:
       _pick_reference_odds → compute_tp → compute_scoring_stack.

    `vk2_blob`, if provided, is the dict returned by
    `services.replay.vk2_historical.predict_vk2_as_of` /
    `predict_combo_vk2_as_of`. When present and `error is None`,
    we stamp `prop["vk2_projection"] / model_projection /
    distribution_sigma / vk2_p_over` so production scoring picks them
    up exactly the way live scoring does — and we pass `p_model =
    vk2_p_over` to `compute_scoring_stack`.

    When `vk2_blob` is missing or carries an error, we DO NOT silently
    fall back to the rolling-μ feature_set as p_model. Instead the
    prop is stamped `vk2_unavailable` and `p_model = None` is passed
    to scoring — the production stack will then assign `unqualified`
    via its standard gate path (no replay-specific shortcut).
    """
    by_book, _ = collect_paired_layers(rows_for_canonical)
    side_layers = _side_layer_for_pick(by_book, side=side)

    head = next((r for r in rows_for_canonical
                  if (r.get("side") or "").upper() == side), rows_for_canonical[0])

    # Use VK2 outputs as the model anchor when present; otherwise
    # nothing (production scoring's gate engine handles missing μ/σ).
    vk2_ok = (vk2_blob is not None
              and vk2_blob.get("error") is None
              and vk2_blob.get("projection") is not None)
    vk2_proj  = vk2_blob.get("projection") if vk2_ok else None
    vk2_sigma = vk2_blob.get("sigma")      if vk2_ok else None
    vk2_p_over = vk2_blob.get("p_over")    if vk2_ok else None
    # For UNDER side, p_over needs to be flipped to p_under for use as
    # p_model (production interprets p_model as "model probability of
    # the chosen side hitting").
    if vk2_ok and (side or "").upper() == "UNDER" and vk2_p_over is not None:
        p_model_side = max(0.0, min(1.0, 1.0 - float(vk2_p_over)))
    else:
        p_model_side = vk2_p_over

    prop: Dict[str, Any] = {
        "sport":          sport,
        "player":         head.get("player"),
        "line":           line,
        "recommendation": side,
        "direction":      side,
        "stat_family":    stat_family,
        "is_alternate":   head.get("is_alternate"),
        "is_combo":       head.get("is_combo"),
        "market_key":     head.get("market_key"),
        "canonical_key":  head.get("canonical_key"),
        # Production-scoring contract: vision_v2 reads
        # prop.vk2_projection (preferred) → prop.model_projection;
        # sigma reads prop.distribution_sigma → prop.model_sigma.
        "vk2_projection":     vk2_proj,
        "model_projection":   vk2_proj if vk2_proj is not None
                              else (feature_set.mu if feature_set else None),
        "distribution_sigma": vk2_sigma if vk2_sigma is not None
                              else (feature_set.sigma if feature_set else None),
        "model_sigma":        vk2_sigma if vk2_sigma is not None
                              else (feature_set.sigma if feature_set else None),
        "hit_rate_sample_size":  (feature_set.sample_size
                                   if feature_set else 0),
        # HR ladder MUST be on the 0-100 scale (production gate engine
        # reads `min: 70.0` etc.). feature_set ships them on 0-1.
        "hit_rate_l5":  (feature_set.hit_rate_l5 * 100.0
                         if feature_set and feature_set.hit_rate_l5 is not None
                         else None),
        "hit_rate_l10": (feature_set.hit_rate_l10 * 100.0
                         if feature_set and feature_set.hit_rate_l10 is not None
                         else None),
        "hit_rate_l20": (feature_set.hit_rate_l20 * 100.0
                         if feature_set and feature_set.hit_rate_l20 is not None
                         else None),
        "ceiling_rate": (feature_set.ceiling_rate * 100.0
                         if feature_set and feature_set.ceiling_rate is not None
                         else None),
        # Production-shaped book layers (carry both `odds` for the picked
        # side AND over_odds/under_odds so the TP engine can de-vig).
        "dk_layer":   side_layers.get("draftkings"),
        "fd_layer":   side_layers.get("fanduel"),
        "mgm_layer":  side_layers.get("betmgm"),
        "bol_layer":  side_layers.get("betonlineag"),
        "sharp_layer": side_layers.get("williamhill_us"),
    }
    populate_flat_odds(prop, by_book=by_book, side=side)

    # Stamp matchup / pace context the same way live production does:
    # vision_v2 reads prop["matchup_strength"] and prop["pace_factor"]
    # directly. Missing values stay as None — vision_v2 treats None as
    # "neutral" (0 contribution).
    if matchup_blob and matchup_blob.get("error") is None:
        prop["matchup_strength"] = matchup_blob.get("matchup_strength")
        prop["pace_factor"]      = matchup_blob.get("pace_factor")
        # Diagnostic lineage (not consumed by scoring; kept for audit).
        prop["dvp_rank"]         = matchup_blob.get("dvp_rank")
        prop["opp_pace_l10"]     = matchup_blob.get("opp_pace_l10")
        prop["league_pace"]      = matchup_blob.get("league_pace")

    # Stamp injury / usage context the same way live production does:
    # vision_v2 reads prop["usage_vacuum_factor"] (via injury_context)
    # and prop["usage_spike"] directly. Missing values stay as None /
    # False — vision_v2 treats those as neutral (0 contribution).
    if injury_blob and injury_blob.get("error") is None:
        if injury_blob.get("usage_vacuum_factor") is not None:
            prop["usage_vacuum_factor"] = injury_blob.get("usage_vacuum_factor")
        if injury_blob.get("usage_spike") is not None:
            prop["usage_spike"]          = injury_blob.get("usage_spike")
        # Diagnostic lineage (not consumed by scoring).
        prop["key_player_out_flag"] = injury_blob.get("key_player_out_flag")
        prop["rotation_compression"] = injury_blob.get("rotation_compression")
        prop["team_injury_context"] = {
            "out_count":             injury_blob.get("out_count"),
            "missing_minutes":       injury_blob.get("missing_minutes"),
            "missing_usage_pct":     injury_blob.get("missing_usage_pct"),
            "team_total_usage":      injury_blob.get("team_total_usage"),
            "usage_vacuum_factor":   injury_blob.get("usage_vacuum_factor"),
        }

    # Production coverage classification (sets prop['book_count'],
    # 'coverage_class', 'books_anchored' in place).
    classify_coverage(prop)

    # 1) Reference-odds chain (production logic). Returns (odds, label).
    ref_odds, ref_book = (None, "none")
    try:
        ref_odds, ref_book = _pick_reference_odds(
            sport=sport,
            dk_layer=prop["dk_layer"], fd_layer=prop["fd_layer"],
            mgm_layer=prop["mgm_layer"], bol_layer=prop["bol_layer"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[replay.engine] _pick_reference_odds: {exc}")

    # 2) Multi-book de-vigged TP (production logic).
    tp_blob: Dict[str, Any] = {}
    try:
        tp_blob = compute_tp(prop=prop, side=side) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[replay.engine] compute_tp: {exc}")

    p_true = tp_blob.get("tp")          # field name from tp_engine
    if p_true is None:
        p_true = tp_blob.get("p_true_active")

    # 3) Edge in percentage points (production convention: p_model is
    #    0-1, implied is 0-1, both scaled to 0-100 for edge_pct).
    edge_pct: Optional[float] = None
    if p_model_side is not None and ref_odds is not None:
        if ref_odds > 0:
            implied = 100.0 / (ref_odds + 100.0)
        elif ref_odds < 0:
            implied = (-ref_odds) / ((-ref_odds) + 100.0)
        else:
            implied = None
        if implied is not None:
            edge_pct = round(p_model_side * 100.0 - implied * 100.0, 6)

    # Stash TP fields on the prop dict so the scoring layer / vision_v2
    # can read them downstream.
    if tp_blob:
        prop["tp_books_used"] = tp_blob.get("books_used")
        prop["tp_source"]     = tp_blob.get("tp_source")

    # 4) Production score. p_model = VK2's own gaussian p_side (NOT TP).
    #    TP is supplied separately as the `tp` arg.
    # NBA gate thresholds use HR on a 0-100 scale (min: 70.0, etc.) —
    # our `feature_set.hit_rate_l20` is on 0-1 scale, so scale here
    # before passing into the production stack.
    hr_for_stack = (feature_set.hit_rate_l20 * 100.0
                    if feature_set is not None
                    and feature_set.hit_rate_l20 is not None
                    else None)
    scored = compute_scoring_stack(
        prop=prop,
        p_model=p_model_side,
        cv=feature_set.cv if feature_set else None,
        hit_rate=hr_for_stack,
        edge_pct=edge_pct,
        tp=p_true,
        ceiling_rate=(feature_set.ceiling_rate if feature_set else None),
        books_available_count=len(by_book),
        sport=sport,
    )

    return {
        "prop":          prop,
        "scored":        scored,
        "ref_book":      ref_book,
        "ref_odds":      ref_odds,
        "tp_blob":       tp_blob,
        "tp":            p_true,
        "p_model":       p_model_side,
        "edge_pct":      edge_pct,
        "books_count":   len(by_book),
        "feature_set":   feature_set.asdict() if feature_set else None,
        "vk2_blob":      vk2_blob,
        "matchup_blob":  matchup_blob,
        "injury_blob":   injury_blob,
        "by_book":       by_book,
    }


# ---------------------------------------------------------------- engine driver
async def run_replay_engine(
    db, *,
    replay_run_id: str,
    range_start: datetime, range_end: datetime,
    snapshot_label: str = "t-30m",
    sport_key: str = "basketball_nba",
    sport_short: str = "nba",
    log_fn=print,
    chunk_size: int = 200,
    limit: Optional[int] = None,
    enable_vk2: bool = False,
    cache_outputs: bool = True,
    sample_event_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Score every (event, snapshot=label, market, player, line, side)
    offer in the range. Side-effects ONLY to `replay_evaluations`,
    `replay_engine_progress`, and `replay_vk2_cache` (Stage B cache).

    `enable_vk2=True` activates the historical VK2 path (no production
    fork): each canonical_key is projected via `predict_vk2_as_of`
    (or `predict_combo_vk2_as_of` for PTS_REB / PTS_AST / REB_AST).

    `cache_outputs=True` (default) populates `replay_vk2_cache` for
    fast-iteration incremental replays — no extra cost vs not caching
    since we already computed the values.

    `sample_event_ids` (optional) restricts the run to a subset of
    event_ids — used by the orchestrator's `--sample-events` flag for
    sub-minute tuning loops.
    """
    # Index assurance + uniqueness on evaluations (idempotent reruns).
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("event_id", 1), ("snapshot_label", 1),
         ("canonical_key", 1), ("bookmaker", 1), ("side", 1)],
        name="uniq_run_event_snap_can_book_side", unique=True,
    )
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("tier", 1)], name="run_tier")
    if cache_outputs:
        await ensure_cache_indexes(db)

    started = datetime.now(timezone.utc)
    counters = {
        "offers_seen":      0,
        "offers_scored":    0,
        "leakage_blocks":   0,
        "feature_failures": 0,
        "scoring_failures": 0,
        "evaluations_inserted": 0,
        "evaluations_modified": 0,
        "vk2_predictions": 0,
        "vk2_cache_hits":  0,
        "vk2_unavailable": 0,
        "vk2_unsupported_family": 0,
        "vk2_player_unresolved":   0,
        "cache_rows_written":      0,
    }

    # Build VK2 player-id resolver once per run.
    resolver: Optional[PlayerIdResolver] = None
    if enable_vk2:
        resolver = PlayerIdResolver(db)
        # Force-build the index now so the first prediction doesn't
        # block on a 200k-doc scan inside the per-canonical loop.
        await resolver.resolve("__warmup__")
        log_fn(f"[engine] VK2 enabled — resolver index built "
               f"({len(resolver._index or {})} unique normalized names)")

    # Build team-name → team_id resolver (for matchup/pace).
    team_resolver = TeamIdResolver(db)
    await team_resolver.resolve("__warmup__")
    log_fn(f"[engine] team resolver built "
           f"({len(team_resolver._index or {})} aliases)")

    # Build player → team_id map from `bdl_historical_game_logs`.
    # We use the LATEST team_id seen for each normalized player name as
    # of `range_end` — this is leak-safe because bdl_historical_game_logs
    # is a record of past games (snapshot rolls forward only) and gives
    # us the player's correct team for the replay window even after
    # mid-season trades.
    player_team_map: Dict[str, int] = {}
    cutoff_date = range_end.date().isoformat()
    pipe_pt = [
        {"$match": {"date": {"$lt": cutoff_date}}},
        {"$sort":  {"date": -1}},
        {"$group": {
            "_id": "$player_name",
            "team_id": {"$first": "$team_id"},
        }},
    ]
    import re as _re
    async for d in db["bdl_historical_game_logs"].aggregate(
        pipe_pt, allowDiskUse=True,
    ):
        nm = _re.sub(r"[^a-z0-9]+", "",
                      (d.get("_id") or "").lower())
        if nm and d.get("team_id") is not None:
            player_team_map[nm] = int(d["team_id"])
    log_fn(f"[engine] player→team map built ({len(player_team_map)})")

    # (event_id, snapshot_date_iso, stat_family, player_team_id) →
    # cached matchup blob. Per-run process cache so we don't re-aggregate
    # opponent's L60-day DvP for every prop in the same event.
    matchup_cache: Dict[Any, Dict[str, Any]] = {}

    # (snapshot_date_iso, team_id) → team-level injury blob. One per
    # team per snapshot date; reused across every prop on that team.
    injury_team_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
    # (snapshot_date_iso, bdl_player_id) → player usage spike blob.
    injury_player_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}

    # Process-level cache: key = (player_id, model_key, snapshot_date_iso, line)
    # Saves duplicate model.predict calls when many alternate lines
    # exist for the same (player, stat) at the same snapshot. The
    # Gaussian p_over depends on `line`, so line is part of the key.
    vk2_cache: Dict[Any, Dict[str, Any]] = {}

    async def _vk2_for(player_field: str, stat_family: str, line: float,
                        snapshot_ts: datetime) -> Optional[Dict[str, Any]]:
        """Returns a vk2 prediction blob, or None if VK2 is disabled
        / player can't be resolved / family unsupported. Each early
        return increments the matching counter so the run summary
        is auditable."""
        if resolver is None:
            return None
        family_up = (stat_family or "").upper()
        if family_up in VK2_UNSUPPORTED_FAMILIES:
            counters["vk2_unsupported_family"] += 1
            return {"projection": None, "sigma": None, "p_over": None,
                     "error": f"vk2_unsupported_family:{family_up}"}
        pid = await resolver.resolve(player_field)
        if pid is None:
            counters["vk2_player_unresolved"] += 1
            return {"projection": None, "sigma": None, "p_over": None,
                     "error": "vk2_player_unresolved"}
        snap_date = snapshot_ts.date().isoformat()
        cache_key = (pid, family_up, snap_date, float(line))
        if cache_key in vk2_cache:
            counters["vk2_cache_hits"] += 1
            return vk2_cache[cache_key]
        try:
            if family_up in COMBO_COMPONENTS:
                blob = await predict_combo_vk2_as_of(
                    db, bdl_player_id=pid, stat_family=family_up,
                    line=float(line), snapshot_ts=snapshot_ts,
                )
            else:
                blob = await predict_vk2_as_of(
                    db, bdl_player_id=pid, stat_family=family_up,
                    line=float(line), snapshot_ts=snapshot_ts,
                )
        except LeakageDetected:
            raise
        except Exception as exc:  # noqa: BLE001
            blob = {"projection": None, "sigma": None, "p_over": None,
                    "error": f"vk2_exception:{exc}"}
        if blob.get("error") is not None:
            counters["vk2_unavailable"] += 1
        else:
            counters["vk2_predictions"] += 1
        vk2_cache[cache_key] = blob
        return blob

    # 1. Enumerate distinct canonical_keys (BOTH sides bundled).
    match_stage: Dict[str, Any] = {
        "snapshot_label": snapshot_label,
        "sport_key":      sport_key,
        "commence_time":  {"$gte": range_start, "$lte": range_end},
    }
    if sample_event_ids:
        match_stage["event_id"] = {"$in": list(sample_event_ids)}
    pipe = [
        {"$match": match_stage},
        {"$group": {
            "_id": {
                "event_id":      "$event_id",
                "snapshot_label": "$snapshot_label",
                "canonical_key": "$canonical_key",
            },
            "rows": {"$push": "$$ROOT"},
            "commence_time": {"$first": "$commence_time"},
            "snapshot_ts":   {"$first": "$snapshot_ts"},
            "stat_family":   {"$first": "$stat_family"},
            "player":        {"$first": "$player"},
            "line":          {"$first": "$line"},
            "market_key":    {"$first": "$market_key"},
            "is_alternate":  {"$first": "$is_alternate"},
            "is_combo":      {"$first": "$is_combo"},
            "home_team":     {"$first": "$home_team"},
            "away_team":     {"$first": "$away_team"},
        }},
    ]
    if limit:
        pipe.append({"$limit": limit})

    eval_buffer: List[Dict[str, Any]] = []
    cache_buffer: List[Dict[str, Any]] = []

    async def flush() -> None:
        if eval_buffer:
            ops = []
            for e in eval_buffer:
                flt = {k: e[k] for k in (
                    "replay_run_id", "event_id", "snapshot_label",
                    "canonical_key", "bookmaker", "side",
                )}
                ops.append(UpdateOne(
                    flt, {"$set": e,
                          "$setOnInsert": {"_first_seen": e["evaluated_at"]}},
                    upsert=True))
            res = await db[REPLAY_EVALUATIONS].bulk_write(ops, ordered=False)
            counters["evaluations_inserted"] += res.upserted_count or 0
            counters["evaluations_modified"] += res.modified_count or 0
            eval_buffer.clear()
        if cache_outputs and cache_buffer:
            cache_ops = []
            for c in cache_buffer:
                flt = {k: c[k] for k in (
                    "event_id", "snapshot_label",
                    "canonical_key", "side",
                )}
                cache_ops.append(UpdateOne(
                    flt, {"$set": c,
                          "$setOnInsert": {"_cached_first": c["cached_at"]}},
                    upsert=True))
            await db[REPLAY_VK2_CACHE].bulk_write(
                cache_ops, ordered=False)
            counters["cache_rows_written"] = (
                counters.get("cache_rows_written", 0) + len(cache_ops)
            )
            cache_buffer.clear()

    async for grp in db[PROPS_NORMALIZED].aggregate(pipe, allowDiskUse=True):
        counters["offers_seen"] += 1
        commence = grp["commence_time"]
        snap_ts = grp["snapshot_ts"]
        if commence and commence.tzinfo is None:
            commence = commence.replace(tzinfo=timezone.utc)
        if snap_ts and snap_ts.tzinfo is None:
            snap_ts = snap_ts.replace(tzinfo=timezone.utc)

        try:
            assert_pregame_only(snap_ts, commence)
        except Exception:
            counters["leakage_blocks"] += 1
            continue

        line = grp["line"]
        stat_family = grp["stat_family"]
        player_norm = grp["player"]   # already lowercased

        # Resolve player_id ONCE per canonical (used by both feature
        # builder and VK2). Falls back to None when resolver disabled
        # or player can't be matched — feature builder handles None
        # via the legacy slow path.
        resolved_pid: Optional[int] = None
        if resolver is not None:
            resolved_pid = await resolver.resolve(player_norm)

        # Build feature set ONCE per canonical_key (player-level).
        try:
            feats = await build_as_of_features(
                db, player_norm=player_norm, stat_family=stat_family,
                line=line, side="OVER", as_of_ts=snap_ts,
                bdl_player_id=resolved_pid,
            )
        except LeakageDetected:
            counters["leakage_blocks"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            counters["feature_failures"] += 1
            logger.debug(f"feature build failed: {exc}")
            continue

        # VK2 prediction (cached per (player, family, snap_date, line)).
        try:
            vk2_blob = await _vk2_for(
                player_field=player_norm, stat_family=stat_family,
                line=line, snapshot_ts=snap_ts,
            )
        except LeakageDetected:
            counters["leakage_blocks"] += 1
            continue

        # Matchup / pace blob — cached per (event, snap_date, family, team).
        player_team_id = player_team_map.get(
            _re.sub(r"[^a-z0-9]+", "", (player_norm or "").lower())
        )
        home_tid = await team_resolver.resolve(grp.get("home_team"))
        away_tid = await team_resolver.resolve(grp.get("away_team"))
        opp_team_id: Optional[int] = None
        if player_team_id is not None:
            if player_team_id == home_tid:
                opp_team_id = away_tid
            elif player_team_id == away_tid:
                opp_team_id = home_tid

        matchup_cache_key = (grp["_id"]["event_id"],
                              snap_ts.date().isoformat(),
                              stat_family.upper(),
                              player_team_id, opp_team_id)
        matchup_blob = matchup_cache.get(matchup_cache_key)
        if matchup_blob is None:
            try:
                matchup_blob = await compute_matchup_blob(
                    db,
                    player_team_id=player_team_id,
                    opponent_team_id=opp_team_id,
                    stat_family=stat_family,
                    snapshot_ts=snap_ts,
                )
            except LeakageDetected:
                counters["leakage_blocks"] += 1
                continue
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"compute_matchup_blob failed: {exc}")
                matchup_blob = {"pace_factor": None,
                                  "matchup_strength": None,
                                  "feature_completeness": "matchup_missing",
                                  "error": f"matchup_exception:{exc}"}
            matchup_cache[matchup_cache_key] = matchup_blob
            counters["matchup_blobs_built"] = (
                counters.get("matchup_blobs_built", 0) + 1
            )
            if matchup_blob.get("feature_completeness") == "matchup_full":
                counters["matchup_full"] = counters.get("matchup_full", 0) + 1
        else:
            counters["matchup_cache_hits"] = (
                counters.get("matchup_cache_hits", 0) + 1
            )

        # ---- Injury / usage layer (Stage-B cached) ----
        # Team-level injury blob: cache per (snapshot_date, team_id).
        snap_date_iso = snap_ts.date().isoformat()
        injury_team_blob: Optional[Dict[str, Any]] = None
        if player_team_id is not None:
            t_key = (snap_date_iso, int(player_team_id))
            injury_team_blob = injury_team_cache.get(t_key)
            if injury_team_blob is None:
                try:
                    injury_team_blob = await compute_team_injury_blob(
                        db,
                        team_id=int(player_team_id),
                        snapshot_ts=snap_ts,
                    )
                except LeakageDetected:
                    counters["leakage_blocks"] += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"compute_team_injury_blob failed: {exc}")
                    injury_team_blob = {
                        "usage_vacuum_factor": 1.0,
                        "key_player_out_flag": 0,
                        "feature_completeness": "team_injury_missing",
                        "error": f"injury_team_exception:{exc}",
                    }
                injury_team_cache[t_key] = injury_team_blob
                counters["injury_team_built"] = (
                    counters.get("injury_team_built", 0) + 1
                )
            else:
                counters["injury_team_cache_hits"] = (
                    counters.get("injury_team_cache_hits", 0) + 1
                )

        # Player-level usage spike: cache per (snapshot_date, player_id).
        injury_spike_blob: Optional[Dict[str, Any]] = None
        if resolved_pid is not None:
            p_key = (snap_date_iso, int(resolved_pid))
            injury_spike_blob = injury_player_cache.get(p_key)
            if injury_spike_blob is None:
                try:
                    injury_spike_blob = await compute_player_usage_spike(
                        db,
                        bdl_player_id=int(resolved_pid),
                        snapshot_ts=snap_ts,
                    )
                except LeakageDetected:
                    counters["leakage_blocks"] += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"compute_player_usage_spike failed: {exc}")
                    injury_spike_blob = {
                        "usage_spike_flag": False,
                        "feature_completeness": "usage_spike_missing",
                        "error": f"usage_spike_exception:{exc}",
                    }
                injury_player_cache[p_key] = injury_spike_blob
                counters["injury_player_built"] = (
                    counters.get("injury_player_built", 0) + 1
                )
            else:
                counters["injury_player_cache_hits"] = (
                    counters.get("injury_player_cache_hits", 0) + 1
                )

        injury_blob = assemble_injury_blob(
            team_blob=injury_team_blob or {},
            spike_blob=injury_spike_blob or {},
        )
        if injury_blob.get("feature_completeness") == "injury_full":
            counters["injury_full"] = counters.get("injury_full", 0) + 1

        # Determine which sides are actually quoted.
        sides_in_data = {(r.get("side") or "").upper()
                         for r in grp["rows"]}
        sides_in_data = sides_in_data & {"OVER", "UNDER"}

        for side in sorted(sides_in_data):
            feats_side = feats

            try:
                res = score_one_side(
                    rows_for_canonical=grp["rows"],
                    side=side, line=line,
                    stat_family=stat_family, sport=sport_short,
                    feature_set=feats_side,
                    vk2_blob=vk2_blob,
                    matchup_blob=matchup_blob,
                    injury_blob=injury_blob,
                )
            except Exception as exc:  # noqa: BLE001
                counters["scoring_failures"] += 1
                logger.debug(f"scoring failed: {exc}")
                continue

            scored = res["scored"]
            vk2 = res.get("vk2_blob") or {}
            vk2_ok = (vk2.get("error") is None
                      and vk2.get("projection") is not None)

            # `feature_completeness` resolution order:
            #   vk2_full / vk2_partial > partial (TP fired) > minimal
            if vk2_ok:
                fc = vk2.get("feature_completeness") or FEATURE_COMPLETENESS_VK2_PARTIAL
            elif res.get("tp") is not None:
                fc = FEATURE_COMPLETENESS_PARTIAL
            else:
                fc = (feats_side.feature_completeness if feats_side
                      else "missing")

            # Persist one row per (canonical_key, side, bookmaker) so
            # downstream analytics can group by book.
            side_rows = [r for r in grp["rows"]
                          if (r.get("side") or "").upper() == side]

            # Stage-B cache write — one row per (event, snap, canonical, side).
            # Holds everything expensive so the incremental scorer can
            # rebuild gates / vision_v2 without re-touching BDL data.
            if cache_outputs:
                # Compact by_book: only the side currently scored, since
                # the cached row is keyed per side. (TP recomputation
                # needs both sides — kept on `tp_blob.books_used` snapshot.)
                cache_buffer.append(cache_row(
                    source_run_id=replay_run_id,
                    event_id=grp["_id"]["event_id"],
                    snapshot_label=grp["_id"]["snapshot_label"],
                    canonical_key=grp["_id"]["canonical_key"],
                    market_key=grp.get("market_key"),
                    stat_family=stat_family,
                    player=player_norm,
                    line=line,
                    side=side,
                    commence_time=commence,
                    snapshot_ts=snap_ts,
                    by_book_layers={
                        # Compact, JSON-serialisable copy of per-book
                        # paired layers: {line, over_odds, under_odds}.
                        b: {"line":      (l or {}).get("line"),
                              "over_odds":  (l or {}).get("over_odds"),
                              "under_odds": (l or {}).get("under_odds")}
                        for b, l in (res.get("by_book") or {}).items()
                    },
                    ref_book=res.get("ref_book"),
                    ref_odds=res.get("ref_odds"),
                    tp_blob=res.get("tp_blob") or {},
                    edge_pct=res.get("edge_pct"),
                    vk2_blob=vk2,
                    feature_set=res.get("feature_set"),
                ))
                # Stamp matchup_blob on the cache row separately so the
                # incremental scorer can pick it up. Spec: matchup
                # enrichment must persist on Stage-B cache rows so
                # Stage-C never re-aggregates BDL.
                cache_buffer[-1]["matchup_blob"]   = matchup_blob
                # Flat copies for indexing / cheap analytics.
                cache_buffer[-1]["matchup_factor"]  = (matchup_blob or {}).get("matchup_strength")
                cache_buffer[-1]["pace_factor"]     = (matchup_blob or {}).get("pace_factor")
                cache_buffer[-1]["defensive_rank_context"] = {
                    "dvp_rank":     (matchup_blob or {}).get("dvp_rank"),
                    "dvp_allowed":  (matchup_blob or {}).get("dvp_allowed"),
                    "lookback_days": (matchup_blob or {}).get("lookback_days_dvp"),
                }
                cache_buffer[-1]["matchup_completeness"] = (
                    (matchup_blob or {}).get("feature_completeness"))
                # Stamp injury_blob on cache row — Stage-C uses it to
                # rebuild prop["usage_vacuum_factor"] / ["usage_spike"]
                # without re-aggregating bdl_historical_game_logs.
                cache_buffer[-1]["injury_blob"] = injury_blob
                cache_buffer[-1]["usage_vacuum_factor"] = (
                    (injury_blob or {}).get("usage_vacuum_factor"))
                cache_buffer[-1]["usage_spike_flag"] = bool(
                    (injury_blob or {}).get("usage_spike"))
                cache_buffer[-1]["injury_completeness"] = (
                    (injury_blob or {}).get("feature_completeness"))
                if len(cache_buffer) >= chunk_size:
                    await flush()

            for r in side_rows:
                doc = {
                    "replay_run_id":  replay_run_id,
                    "event_id":       grp["_id"]["event_id"],
                    "snapshot_label": grp["_id"]["snapshot_label"],
                    "snapshot_ts":    snap_ts,
                    "commence_time":  commence,
                    "canonical_key":  grp["_id"]["canonical_key"],
                    "market_key":     grp["market_key"],
                    "stat_family":    stat_family,
                    "player":         player_norm,
                    "line":           line,
                    "side":           side,
                    "bookmaker":      r["bookmaker"],
                    "odds_american":  r["odds_american"],
                    "is_alternate":   grp["is_alternate"],
                    "is_combo":       grp["is_combo"],
                    "ref_book":       res["ref_book"],
                    "ref_odds":       res["ref_odds"],
                    "books_count":    res["books_count"],
                    "feature_set":    res["feature_set"],
                    "feature_completeness": fc,
                    # VK2 lineage (always stamped — error or success).
                    "vk2_projection":     vk2.get("projection"),
                    "vk2_sigma":          vk2.get("sigma"),
                    "vk2_p_over":         vk2.get("p_over"),
                    "vk2_model_version":  vk2.get("model_version"),
                    "vk2_feature_count":  vk2.get("feature_count"),
                    "vk2_feature_hash":   vk2.get("feature_hash"),
                    "vk2_adv_coverage_l10": vk2.get("adv_coverage_l10"),
                    "vk2_history_size":   vk2.get("history_size"),
                    "vk2_error":          vk2.get("error"),
                    "vk2_components":     vk2.get("components"),
                    "vk2_covariance_source": vk2.get("covariance_source"),
                    "matchup_pace_factor":      (matchup_blob or {}).get("pace_factor"),
                    "matchup_strength":         (matchup_blob or {}).get("matchup_strength"),
                    "matchup_dvp_rank":         (matchup_blob or {}).get("dvp_rank"),
                    "matchup_feature_completeness": (
                        (matchup_blob or {}).get("feature_completeness")),
                    "usage_vacuum_factor":      (injury_blob or {}).get("usage_vacuum_factor"),
                    "usage_spike":              bool((injury_blob or {}).get("usage_spike")),
                    "key_player_out_flag":      (injury_blob or {}).get("key_player_out_flag"),
                    "rotation_compression":     (injury_blob or {}).get("rotation_compression"),
                    "injury_out_count":         (injury_blob or {}).get("out_count"),
                    "injury_feature_completeness": (
                        (injury_blob or {}).get("feature_completeness")),
                    "p_model":            res.get("p_model"),
                    "tier":            scored.get("tier"),
                    "tier_reason":     scored.get("tier_reason"),
                    "vision_score":    scored.get("vision_score"),
                    "vision_score_v2": scored.get("vision_score_v2"),
                    "p_true_active":   res.get("tp"),
                    "tp_books_used":   res["tp_blob"].get("books_used"),
                    "tp_source":       res["tp_blob"].get("tp_source"),
                    "edge_vs_fair":    res.get("edge_pct"),
                    "ev_per_dollar":   scored.get("ev_per_dollar"),
                    "evaluated_at":    datetime.now(timezone.utc),
                    "scoring_payload": scored,
                }
                eval_buffer.append(doc)
                if len(eval_buffer) >= chunk_size:
                    await flush()
            counters["offers_scored"] += 1

        if counters["offers_seen"] % 250 == 0:
            log_fn(f"[engine] offers_seen={counters['offers_seen']} "
                   f"scored={counters['offers_scored']} "
                   f"leakage={counters['leakage_blocks']} "
                   f"feat_fail={counters['feature_failures']} "
                   f"score_fail={counters['scoring_failures']} "
                   f"vk2_ok={counters['vk2_predictions']} "
                   f"vk2_unavail={counters['vk2_unavailable']} "
                   f"vk2_cache={counters['vk2_cache_hits']}")

    await flush()
    finished = datetime.now(timezone.utc)

    return {
        "replay_run_id":     replay_run_id,
        "snapshot_label":    snapshot_label,
        "range_start":       range_start.isoformat(),
        "range_end":         range_end.isoformat(),
        "started_utc":       started.isoformat(),
        "finished_utc":      finished.isoformat(),
        "wallclock_seconds": (finished - started).total_seconds(),
        "counters":          counters,
        "vk2_enabled":       enable_vk2,
        "cache_outputs":     cache_outputs,
        "fingerprint":       fingerprint_block(sport_short),
        "feature_completeness_overall":
            "varies (vk2_full / vk2_partial / partial / minimal)",
        "parity_warnings": [
            "Avg hit/miss margin not yet wired.",
            ("VK2 advanced-stats coverage may be 0 for the replay window — "
             "rows tagged `vk2_partial` reflect this; see "
             "audit_reports/vk2_production_map.md."),
        ],
    }


__all__ = [
    "REPLAY_EVALUATIONS",
    "FEATURE_COMPLETENESS_MINIMAL",
    "FEATURE_COMPLETENESS_PARTIAL",
    "FEATURE_COMPLETENESS_VK2_PARTIAL",
    "FEATURE_COMPLETENESS_VK2_FULL",
    "FEATURE_COMPLETENESS_FULL",
    "AsOfFeatures",
    "build_as_of_features",
    "collect_paired_layers",
    "populate_flat_odds",
    "score_one_side",
    "run_replay_engine",
]
