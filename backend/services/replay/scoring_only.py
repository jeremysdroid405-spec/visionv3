"""
Incremental replay — Stage C only.

Reads `replay_vk2_cache` (Stage B) and runs ONLY:
  - reference-odds chain (cheap, but we cache `ref_book`/`ref_odds` from
    the source run so we don't even repeat that)
  - compute_tp (cheap; we'll skip when `tp_engine_hash` matches cache row)
  - edge_pct
  - compute_scoring_stack → tier / gate_results / vision_score_v2

Writes to `replay_evaluations` under a fresh `replay_run_id`. NEVER
touches `bdl_*`, `replay_props_normalized`, or any feature pipeline.

Targets the < 5-minute / 500k-rows runtime requirement: the inner loop
is ~1ms/row (compute_scoring_stack + a small dict re-serialize) and
no I/O outside cache reads + bulk eval upserts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from services.scoring.scoring_stack import compute_scoring_stack
from services.scoring.tp_engine import compute_tp
from services.scoring.coverage_filter import classify_coverage

from .cache import REPLAY_VK2_CACHE, fingerprint_block
from .engine import REPLAY_EVALUATIONS

logger = logging.getLogger(__name__)


def _rebuild_prop(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    """Rebuild a `prop` dict identical to what `score_one_side`
    constructs in `engine.py`. We pull from the cache row so no I/O
    is required."""
    vk2 = row.get("vk2_blob") or {}
    fs = row.get("feature_set") or {}
    layers = row.get("by_book_layers") or {}

    def side_layer(book: str) -> Optional[Dict[str, Any]]:
        lyr = layers.get(book)
        if not lyr:
            return None
        side_u = (side or "").upper()
        side_price = (lyr.get("over_odds") if side_u == "OVER"
                      else lyr.get("under_odds"))
        if side_price is None:
            return None
        return {
            "line":      lyr.get("line"),
            "odds":      side_price,
            "over_odds": lyr.get("over_odds"),
            "under_odds": lyr.get("under_odds"),
        }

    prop: Dict[str, Any] = {
        "sport":             "nba",
        "player":            row.get("player"),
        "line":              row.get("line"),
        "recommendation":    side,
        "direction":         side,
        "stat_family":       row.get("stat_family"),
        "market_key":        row.get("market_key"),
        "canonical_key":     row.get("canonical_key"),
        "vk2_projection":    vk2.get("projection"),
        "model_projection":  (vk2.get("projection")
                               if vk2.get("projection") is not None
                               else fs.get("mu")),
        "distribution_sigma": (vk2.get("sigma") if vk2.get("sigma") is not None
                                else fs.get("sigma")),
        "model_sigma":        (vk2.get("sigma") if vk2.get("sigma") is not None
                                else fs.get("sigma")),
        "hit_rate_sample_size": fs.get("sample_size") or 0,
        "hit_rate_l5":  (fs["hit_rate_l5"] * 100.0
                         if fs.get("hit_rate_l5") is not None else None),
        "hit_rate_l10": (fs["hit_rate_l10"] * 100.0
                         if fs.get("hit_rate_l10") is not None else None),
        "hit_rate_l20": (fs["hit_rate_l20"] * 100.0
                         if fs.get("hit_rate_l20") is not None else None),
        "ceiling_rate": (fs["ceiling_rate"] * 100.0
                         if fs.get("ceiling_rate") is not None else None),
        "dk_layer":   side_layer("draftkings"),
        "fd_layer":   side_layer("fanduel"),
        "mgm_layer":  side_layer("betmgm"),
        "bol_layer":  side_layer("betonlineag"),
        "sharp_layer": side_layer("williamhill_us"),
    }
    # Same flat-odds populate that production scoring expects from
    # `_pick_reference_odds` / `compute_tp`.
    from services.replay.engine import populate_flat_odds, _BOOK_TO_PREFIX  # noqa: E402,F401
    side_u = (side or "").upper()
    for book_key in layers.keys():
        # Only books in the production whitelist are relevant.
        from services.replay.engine import _BOOK_TO_PREFIX as _BTP
        prefix = _BTP.get(book_key)
        if not prefix:
            continue
        lyr = layers.get(book_key) or {}
        if side_u == "OVER":
            prop[f"{prefix}_odds"]     = lyr.get("over_odds")
            prop[f"{prefix}_odds_opp"] = lyr.get("under_odds")
        else:
            prop[f"{prefix}_odds"]     = lyr.get("under_odds")
            prop[f"{prefix}_odds_opp"] = lyr.get("over_odds")
    classify_coverage(prop)

    # Wire matchup / pace context onto the prop dict so vision_v2
    # picks them up exactly the same way it does in production. The
    # cache row's `matchup_blob` was computed by services/replay/matchup.py
    # against historical bdl data, strictly as-of the snapshot date.
    matchup = row.get("matchup_blob") or {}
    if matchup and matchup.get("error") is None:
        prop["matchup_strength"] = matchup.get("matchup_strength")
        prop["pace_factor"]      = matchup.get("pace_factor")
        prop["dvp_rank"]         = matchup.get("dvp_rank")
        prop["opp_pace_l10"]     = matchup.get("opp_pace_l10")

    # Wire injury / usage context (Stage-B cached). vision_v2 reads
    # prop["usage_vacuum_factor"] (via injury_context) and
    # prop["usage_spike"] directly. The blob was assembled by
    # services/replay/injury_history.assemble_injury_blob during the
    # full engine run and never re-aggregates BDL during Stage-C.
    inj = row.get("injury_blob") or {}
    if inj and inj.get("error") is None:
        if inj.get("usage_vacuum_factor") is not None:
            prop["usage_vacuum_factor"] = inj.get("usage_vacuum_factor")
        if inj.get("usage_spike") is not None:
            prop["usage_spike"] = bool(inj.get("usage_spike"))
        prop["key_player_out_flag"]  = inj.get("key_player_out_flag")
        prop["rotation_compression"] = inj.get("rotation_compression")
        prop["team_injury_context"] = {
            "out_count":           inj.get("out_count"),
            "missing_minutes":     inj.get("missing_minutes"),
            "missing_usage_pct":   inj.get("missing_usage_pct"),
            "team_total_usage":    inj.get("team_total_usage"),
            "usage_vacuum_factor": inj.get("usage_vacuum_factor"),
        }
    return prop


def _p_model_for_side(vk2: Dict[str, Any], side: str) -> Optional[float]:
    p_over = vk2.get("p_over")
    if p_over is None or vk2.get("error") is not None:
        return None
    if (side or "").upper() == "UNDER":
        return max(0.0, min(1.0, 1.0 - float(p_over)))
    return p_over


def _edge_pct(p_model: Optional[float],
              ref_odds: Optional[int]) -> Optional[float]:
    if p_model is None or ref_odds is None:
        return None
    if ref_odds > 0:
        implied = 100.0 / (ref_odds + 100.0)
    elif ref_odds < 0:
        implied = (-ref_odds) / ((-ref_odds) + 100.0)
    else:
        return None
    return round(p_model * 100.0 - implied * 100.0, 6)


async def run_scoring_only(
    db, *,
    replay_run_id: str,
    source_run_ids: Optional[List[str]] = None,
    sport_short: str = "nba",
    log_fn=print,
    chunk_size: int = 500,
    limit: Optional[int] = None,
    recompute_tp: bool = True,
) -> Dict[str, Any]:
    """Run scoring layer over the cache. Writes fresh rows to
    `replay_evaluations` under `replay_run_id`.

    `source_run_ids`: optional filter — restricts the cache iteration
    to rows seeded by these source runs. When omitted, ALL cache rows
    are scored (typical in fast-iteration mode).

    `recompute_tp`: when True (default) we re-run the multi-book
    de-vig on the cached `by_book_layers`. Set False to reuse the
    cache's `tp_blob` verbatim (cheaper, only safe when
    `tp_engine_hash` hasn't moved — the diff runner enforces this).
    """
    started = datetime.now(timezone.utc)

    counters = {
        "rows_scanned":      0,
        "rows_scored":       0,
        "tp_recomputed":     0,
        "tp_reused":         0,
        "evaluations_inserted": 0,
        "evaluations_modified": 0,
    }

    flt: Dict[str, Any] = {}
    if source_run_ids:
        flt["source_run_id"] = {"$in": list(source_run_ids)}

    # Index assurance on output collection (engine.py also creates these).
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("event_id", 1), ("snapshot_label", 1),
         ("canonical_key", 1), ("bookmaker", 1), ("side", 1)],
        name="uniq_run_event_snap_can_book_side", unique=True,
    )
    await db[REPLAY_EVALUATIONS].create_index(
        [("replay_run_id", 1), ("tier", 1)], name="run_tier")

    eval_buffer: List[Dict[str, Any]] = []

    async def flush() -> None:
        if not eval_buffer:
            return
        ops = []
        for e in eval_buffer:
            f = {k: e[k] for k in (
                "replay_run_id", "event_id", "snapshot_label",
                "canonical_key", "bookmaker", "side",
            )}
            ops.append(UpdateOne(
                f, {"$set": e,
                     "$setOnInsert": {"_first_seen": e["evaluated_at"]}},
                upsert=True))
        res = await db[REPLAY_EVALUATIONS].bulk_write(ops, ordered=False)
        counters["evaluations_inserted"] += res.upserted_count or 0
        counters["evaluations_modified"] += res.modified_count or 0
        eval_buffer.clear()

    cursor = db[REPLAY_VK2_CACHE].find(flt, no_cursor_timeout=True)
    if limit:
        cursor = cursor.limit(limit)

    async for row in cursor:
        counters["rows_scanned"] += 1
        side = row.get("side")
        if side not in ("OVER", "UNDER"):
            continue
        prop = _rebuild_prop(row, side)
        vk2 = row.get("vk2_blob") or {}
        fs = row.get("feature_set") or {}
        ref_odds = row.get("ref_odds")
        p_model = _p_model_for_side(vk2, side)

        # TP: recompute or reuse.
        if recompute_tp:
            try:
                tp_blob = compute_tp(prop=prop, side=side) or {}
                counters["tp_recomputed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"compute_tp failed: {exc}")
                tp_blob = row.get("tp_blob") or {}
        else:
            tp_blob = row.get("tp_blob") or {}
            counters["tp_reused"] += 1

        p_true = tp_blob.get("tp")
        if p_true is None:
            p_true = tp_blob.get("p_true_active")

        edge_pct = _edge_pct(p_model, ref_odds)
        if edge_pct is None:
            edge_pct = row.get("edge_pct")

        hr_for_stack = (fs["hit_rate_l20"] * 100.0
                        if fs.get("hit_rate_l20") is not None
                        else None)

        layers = row.get("by_book_layers") or {}
        books_count = len(layers)

        scored = compute_scoring_stack(
            prop=prop,
            p_model=p_model,
            cv=fs.get("cv"),
            hit_rate=hr_for_stack,
            edge_pct=edge_pct,
            tp=p_true,
            ceiling_rate=fs.get("ceiling_rate"),
            books_available_count=books_count,
            sport=sport_short,
        )

        # One eval row per (canonical, side, bookmaker) — same shape as
        # `engine.py` so downstream analytics work unchanged.
        for book, lyr in layers.items():
            side_u = (side or "").upper()
            offer_odds = (lyr.get("over_odds") if side_u == "OVER"
                          else lyr.get("under_odds"))
            if offer_odds is None:
                continue
            doc = {
                "replay_run_id":  replay_run_id,
                "source_cache_run_id": row.get("source_run_id"),
                "event_id":       row.get("event_id"),
                "snapshot_label": row.get("snapshot_label"),
                "snapshot_ts":    row.get("snapshot_ts"),
                "commence_time":  row.get("commence_time"),
                "canonical_key":  row.get("canonical_key"),
                "market_key":     row.get("market_key"),
                "stat_family":    row.get("stat_family"),
                "player":         row.get("player"),
                "line":           row.get("line"),
                "side":           side,
                "bookmaker":      book,
                "odds_american":  offer_odds,
                "ref_book":       row.get("ref_book"),
                "ref_odds":       ref_odds,
                "feature_set":    fs,
                "feature_completeness": (
                    "vk2_full" if (vk2.get("error") is None
                                    and vk2.get("feature_completeness") == "vk2_full")
                    else (vk2.get("feature_completeness")
                           or fs.get("feature_completeness")
                           or "missing")
                ),
                "vk2_projection":     vk2.get("projection"),
                "vk2_sigma":          vk2.get("sigma"),
                "vk2_p_over":         vk2.get("p_over"),
                "vk2_model_version":  vk2.get("model_version"),
                "vk2_feature_count":  vk2.get("feature_count"),
                "vk2_feature_hash":   vk2.get("feature_hash"),
                "vk2_adv_coverage_l10": vk2.get("adv_coverage_l10"),
                "vk2_history_size":   vk2.get("history_size"),
                "vk2_error":          vk2.get("error"),
                "matchup_pace_factor":  (row.get("matchup_blob") or {}).get("pace_factor"),
                "matchup_strength":     (row.get("matchup_blob") or {}).get("matchup_strength"),
                "matchup_dvp_rank":     (row.get("matchup_blob") or {}).get("dvp_rank"),
                "matchup_feature_completeness": (
                    (row.get("matchup_blob") or {}).get("feature_completeness")),
                "usage_vacuum_factor":  (row.get("injury_blob") or {}).get("usage_vacuum_factor"),
                "usage_spike":          bool((row.get("injury_blob") or {}).get("usage_spike")),
                "key_player_out_flag":  (row.get("injury_blob") or {}).get("key_player_out_flag"),
                "rotation_compression": (row.get("injury_blob") or {}).get("rotation_compression"),
                "injury_out_count":     (row.get("injury_blob") or {}).get("out_count"),
                "injury_feature_completeness": (
                    (row.get("injury_blob") or {}).get("feature_completeness")),
                "p_model":            p_model,
                "tier":            scored.get("tier"),
                "tier_reason":     scored.get("tier_reason"),
                "vision_score":    scored.get("vision_score"),
                "vision_score_v2": scored.get("vision_score_v2"),
                "p_true_active":   p_true,
                "tp_books_used":   tp_blob.get("books_used"),
                "tp_source":       tp_blob.get("tp_source"),
                "edge_vs_fair":    edge_pct,
                "ev_per_dollar":   scored.get("ev_per_dollar"),
                "evaluated_at":    datetime.now(timezone.utc),
                "scoring_payload": scored,
                "incremental":     True,
            }
            eval_buffer.append(doc)
            if len(eval_buffer) >= chunk_size:
                await flush()
        counters["rows_scored"] += 1

        if counters["rows_scanned"] % 25_000 == 0:
            log_fn(f"[scoring_only] scanned={counters['rows_scanned']} "
                   f"scored={counters['rows_scored']} "
                   f"ins={counters['evaluations_inserted']} "
                   f"mod={counters['evaluations_modified']}")

    await flush()
    finished = datetime.now(timezone.utc)

    return {
        "replay_run_id":     replay_run_id,
        "source_run_ids":    source_run_ids,
        "started_utc":       started.isoformat(),
        "finished_utc":      finished.isoformat(),
        "wallclock_seconds": (finished - started).total_seconds(),
        "counters":          counters,
        "fingerprint":       fingerprint_block(sport_short),
    }


__all__ = ["run_scoring_only"]
