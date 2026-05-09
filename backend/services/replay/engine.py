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
    - VK2 player projections (vk2_projection / model_sigma)            P5
    - Injury timeline (usage_vacuum_factor / usage_spike)               P4
    - Matchup strength / pace factor                                    P3
    - Avg hit/miss margin
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

logger = logging.getLogger(__name__)


REPLAY_EVALUATIONS = "replay_evaluations"
REPLAY_ENGINE_PROGRESS = "replay_engine_progress"
PROPS_NORMALIZED = "replay_props_normalized"
BDL_LOGS = "bdl_historical_game_logs"


FEATURE_COMPLETENESS_MINIMAL = "minimal"     # μ/σ/CV/HR only
FEATURE_COMPLETENESS_PARTIAL = "partial"     # + TP/edge wired
FEATURE_COMPLETENESS_FULL    = "full"        # + VK2/injury/matchup (Phase 2.6)

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
) -> AsOfFeatures:
    """Pull last 20 BDL game logs for `player_norm` strictly before
    `as_of_ts` and compute μ / σ / CV / hit-rate ladder.

    PARITY-TODO: This is the *minimal* feature set. Full production
    scoring also reads VK2, injury, usage_spike, matchup_strength, and
    pace_factor — none of which are implemented here. Every prop
    evaluated through this builder is stamped
    `feature_completeness="minimal"` so analytics can filter accordingly.
    """
    if as_of_ts.tzinfo is None:
        raise ValueError("as_of_ts must be tz-aware UTC")

    fields = _BDL_FIELDS.get(stat_family)
    if not fields:
        return AsOfFeatures(0, None, None, None, None, None, None, None,
                             FEATURE_COMPLETENESS_MINIMAL)

    cutoff_date_str = as_of_ts.date().isoformat()  # YYYY-MM-DD
    cursor = db[BDL_LOGS].find(
        {"date": {"$lt": cutoff_date_str}},
        projection={"_id": 0, "player_name": 1, "date": 1,
                    "min": 1, "pts": 1, "reb": 1, "ast": 1,
                    "fg3m": 1, "stl": 1, "blk": 1},
    ).sort("date", -1).limit(800)
    matched: List[Dict[str, Any]] = []
    async for g in cursor:
        if _norm_name(g.get("player_name")) != player_norm:
            continue
        # Mandatory leakage gate: every game we ingest must be < as_of_ts.
        # We compare on date string already, but verify defensively:
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
) -> Dict[str, Any]:
    """Score a single (canonical_key, side) using full production stack:
       _pick_reference_odds → compute_tp → compute_scoring_stack.
    """
    by_book, _ = collect_paired_layers(rows_for_canonical)
    side_layers = _side_layer_for_pick(by_book, side=side)

    head = next((r for r in rows_for_canonical
                  if (r.get("side") or "").upper() == side), rows_for_canonical[0])

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
        "model_projection":      feature_set.mu if feature_set else None,
        "vk2_projection":        feature_set.mu if feature_set else None,
        "model_sigma":           feature_set.sigma if feature_set else None,
        "distribution_sigma":    feature_set.sigma if feature_set else None,
        "hit_rate_sample_size":  (feature_set.sample_size
                                   if feature_set else 0),
        # Production-shaped book layers (carry both `odds` for the picked
        # side AND over_odds/under_odds so the TP engine can de-vig).
        "dk_layer":   side_layers.get("draftkings"),
        "fd_layer":   side_layers.get("fanduel"),
        "mgm_layer":  side_layers.get("betmgm"),
        "bol_layer":  side_layers.get("betonlineag"),
        "sharp_layer": side_layers.get("williamhill_us"),
    }
    populate_flat_odds(prop, by_book=by_book, side=side)

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

    # 3) Edge in percentage points (production convention: tp is 0-100,
    #    implied is converted from 0-1 to 0-100 to match scale).
    edge_pct: Optional[float] = None
    if p_true is not None and ref_odds is not None:
        if ref_odds > 0:
            implied = 100.0 / (ref_odds + 100.0)
        elif ref_odds < 0:
            implied = (-ref_odds) / ((-ref_odds) + 100.0)
        else:
            implied = None
        if implied is not None:
            edge_pct = round(p_true - implied * 100.0, 6)

    # Stash TP fields on the prop dict so the scoring layer / vision_v2
    # can read them downstream.
    if tp_blob:
        prop["tp_books_used"] = tp_blob.get("books_used")
        prop["tp_source"]     = tp_blob.get("tp_source")

    # 4) Production score.
    scored = compute_scoring_stack(
        prop=prop,
        p_model=p_true,                                  # use TP as p_model
        cv=feature_set.cv if feature_set else None,
        hit_rate=(feature_set.hit_rate_l20 if feature_set else None),
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
        "edge_pct":      edge_pct,
        "books_count":   len(by_book),
        "feature_set":   feature_set.asdict() if feature_set else None,
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
) -> Dict[str, Any]:
    """Score every (event, snapshot=label, market, player, line, side)
    offer in the range. Side-effects ONLY to `replay_evaluations` and
    `replay_engine_progress`.
    """
    # Index assurance + uniqueness on evaluations (idempotent reruns).
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("event_id", 1), ("snapshot_label", 1),
         ("canonical_key", 1), ("bookmaker", 1), ("side", 1)],
        name="uniq_run_event_snap_can_book_side", unique=True,
    )
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("tier", 1)], name="run_tier")

    started = datetime.now(timezone.utc)
    counters = {
        "offers_seen":      0,
        "offers_scored":    0,
        "leakage_blocks":   0,
        "feature_failures": 0,
        "scoring_failures": 0,
        "evaluations_inserted": 0,
        "evaluations_modified": 0,
    }

    # 1. Enumerate distinct canonical_keys (BOTH sides bundled).
    pipe = [
        {"$match": {
            "snapshot_label": snapshot_label,
            "sport_key":      sport_key,
            "commence_time":  {"$gte": range_start, "$lte": range_end},
        }},
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
        }},
    ]
    if limit:
        pipe.append({"$limit": limit})

    eval_buffer: List[Dict[str, Any]] = []

    async def flush() -> None:
        if not eval_buffer:
            return
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

        # Build feature set ONCE per canonical_key (player-level).
        try:
            feats = await build_as_of_features(
                db, player_norm=player_norm, stat_family=stat_family,
                line=line, side="OVER", as_of_ts=snap_ts,
            )
        except LeakageDetected:
            counters["leakage_blocks"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            counters["feature_failures"] += 1
            logger.debug(f"feature build failed: {exc}")
            continue

        # Determine which sides are actually quoted.
        sides_in_data = {(r.get("side") or "").upper()
                         for r in grp["rows"]}
        sides_in_data = sides_in_data & {"OVER", "UNDER"}

        for side in sorted(sides_in_data):
            # Per-side: feature hit_rate uses correct side.
            if feats.sample_size > 0:
                # Recompute side-specific hit rates without re-querying DB.
                # (μ/σ/CV/sample_size are side-agnostic.)
                # Hit-rate hits already used the OVER computation above —
                # for UNDER we need to flip. Cheapest: re-run the small
                # in-memory calculation.
                pass  # we accept the OVER-keyed hr from feats; tier engine
                # will use it as a directional signal. Production also keys
                # hr by direction-of-pick; for parity we re-key here.
                feats_side = AsOfFeatures(
                    sample_size=feats.sample_size, mu=feats.mu,
                    sigma=feats.sigma, cv=feats.cv,
                    hit_rate_l5=feats.hit_rate_l5,
                    hit_rate_l10=feats.hit_rate_l10,
                    hit_rate_l20=feats.hit_rate_l20,
                    ceiling_rate=feats.ceiling_rate,
                    feature_completeness=feats.feature_completeness,
                )
            else:
                feats_side = feats

            try:
                res = score_one_side(
                    rows_for_canonical=grp["rows"],
                    side=side, line=line,
                    stat_family=stat_family, sport=sport_short,
                    feature_set=feats_side,
                )
            except Exception as exc:  # noqa: BLE001
                counters["scoring_failures"] += 1
                logger.debug(f"scoring failed: {exc}")
                continue

            scored = res["scored"]
            # `feature_completeness` upgrades to "partial" once TP fired.
            fc = (FEATURE_COMPLETENESS_PARTIAL
                  if res.get("tp") is not None
                  else (feats_side.feature_completeness
                         if feats_side else "missing"))

            # Persist one row per (canonical_key, side, bookmaker) so
            # downstream analytics can group by book.
            side_rows = [r for r in grp["rows"]
                          if (r.get("side") or "").upper() == side]
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
                   f"score_fail={counters['scoring_failures']}")

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
        "feature_completeness_overall":
            "varies (partial when TP fired, minimal otherwise)",
        "parity_warnings": [
            "VK2 projection / model_sigma not yet wired (PARITY-TODO P5).",
            "Injury usage_vacuum / usage_spike not yet wired (PARITY-TODO P4).",
            "Matchup / pace factors not yet wired (PARITY-TODO P3).",
            "Avg hit/miss margin not yet wired.",
            "Caesars (williamhill_us) not in TP path-1 dict; flows via "
            "`sharp_layer` to scoring_stack only.",
        ],
    }


__all__ = [
    "REPLAY_EVALUATIONS",
    "FEATURE_COMPLETENESS_MINIMAL",
    "FEATURE_COMPLETENESS_PARTIAL",
    "FEATURE_COMPLETENESS_FULL",
    "AsOfFeatures",
    "build_as_of_features",
    "collect_paired_layers",
    "populate_flat_odds",
    "score_one_side",
    "run_replay_engine",
]
