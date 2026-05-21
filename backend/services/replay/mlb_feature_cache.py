"""
MLB Replay Historical Feature Cache — Layer 2.
==============================================

Caches the AS-OF-DATE inputs that production-model inference requires,
so Layer 3 (model replay) can run without rebuilding Statcast or
calling external APIs.

Data sources (all in-cluster, no external API calls)
----------------------------------------------------
* `mlb_master_hub_2026.bdl_game_logs[]` — recent batter game logs
  (filtered to dates strictly BEFORE the replay date)
* `mlb_statcast_player_features` — newest doc with `game_date < replay_date`
* `mlb_statcast_pitcher_features` — same, for pitcher universe
* `mlb_player_identity_map` — bdl_id ↔ MLBAM resolution

Universe selection
------------------
We cache features only for `(player_name_normalized, stat_family)` pairs
that actually had a prop priced on the date in `mlb_historical_alt_odds_raw`.
That keeps the cache exactly co-extensive with replay demand and bounds
the row-count tightly.

OOM-safety
----------
date → player → stat_family → buffer → flush(500–1000)
RSS guard fires at `DEFAULT_MEM_LIMIT_MB`; checkpoints status and exits.

Future-leakage prevention
-------------------------
1. `bdl_game_logs` filtered to `date < replay_date`.
2. Statcast docs filtered to `game_date < replay_date`.
3. PA-windowed cache (which has no game_date column) is **not used** —
   the model imputes when missing, which is fine for replay accuracy.
4. Opposing pitcher context is recorded as `None` for now (we don't have
   a historical starting-pitcher feed; replay will fall back to the
   model's imputed-pitcher path). Layer 3 will mark these rows
   `opp_pitcher_is_imputed=1`.

DOES NOT call the model, run gates, or grade outcomes — that is Layers 3/4.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil
import pymongo
from pymongo import ASCENDING, UpdateOne

logger = logging.getLogger(__name__)

CACHE_COLL = "mlb_replay_feature_cache"
STATUS_COLL = "mlb_replay_feature_status"
SOURCE_VERSION = "feature_cache_v1.0_2026_05_16"

DEFAULT_MEM_LIMIT_MB = 1_500
WINDOW_DEPTH = 30  # cache 30 most-recent games for any stat family


# ── Market → stat-family map (mirrors canonical_stats SSOT) ─────────
# 2026-05-18: canonical names ONLY. `batter_strikeouts` and
# `walks_allowed` here match the canonical_stats registry so the
# entire downstream pipeline (gate engine, audit tools, output writer)
# sees ONE family token per stat. Legacy values stored in
# `mlb_replay_feature_cache` from before this fix should be backfilled
# OR normalized on read via `canonical_stats.canonical_family`.
_STAT_FAMILY_MAP: Dict[str, str] = {
    "batter_hits":              "hits",
    "batter_total_bases":       "total_bases",
    "batter_runs_scored":       "runs",
    "batter_rbis":              "rbis",
    "batter_hits_runs_rbis":    "hits_runs_rbis",
    "batter_strikeouts":        "batter_strikeouts",  # ← was "strikeouts"
    "pitcher_strikeouts":       "pitcher_strikeouts",
    "pitcher_hits_allowed":     "hits_allowed",       # ← matches SSOT
    "pitcher_walks":            "walks_allowed",      # ← was "pitcher_walks"
    "pitcher_earned_runs":      "earned_runs",
    "pitcher_outs":             "pitching_outs",     # ← matches SSOT
}


def market_to_stat_family(market: str) -> Optional[str]:
    """Drops the `_alternate` suffix when present."""
    if market.endswith("_alternate"):
        market = market[: -len("_alternate")]
    return _STAT_FAMILY_MAP.get(market)


# Statcast field name lookup for hit-rate computation. Keys MUST match
# canonical family tokens emitted by `_STAT_FAMILY_MAP` above so the
# replay engine's `cache_row["stat_family"]` lookup hits without a
# second translation step.
_STAT_FIELD_MAP: Dict[str, str] = {
    "hits": "hits",
    "total_bases": "total_bases",
    "runs": "runs",
    "rbis": "rbis",
    "hits_runs_rbis": "hits_runs_rbis",
    "batter_strikeouts": "strikeouts",        # ← key renamed
    "pitcher_strikeouts": "pitcher_strikeouts",
    "hits_allowed": "pitcher_hits_allowed",   # ← SSOT family name
    "walks_allowed": "pitcher_walks",         # ← key renamed; statcast col stays
    "earned_runs": "earned_runs",
    "pitching_outs": "outs_recorded",  # MLB box: pitching_outs is "outs"
}


# Pitcher families derived from pitcher rows, not batter logs.
# 2026-05-18 — names match canonical family tokens (SSOT registry).
_PITCHER_FAMILIES = {
    "pitcher_strikeouts", "hits_allowed", "walks_allowed",
    "earned_runs", "pitching_outs",
}


# ── Canonical family → MLB-HF model artifact key ───────────────────
# The trained model pkl was indexed under legacy keys (`strikeouts`,
# `pitcher_walks`, `hits_allowed`, `hits+runs+rbis`). The
# `_STAT_FAMILY_MAP` above now emits canonical family tokens
# downstream. THIS map ONLY translates at the model-call boundary
# (replay engine → `model.feature_cols[<key>]` / `model.models[<key>]` /
# `model.scalers[<key>]`). It is NOT used for any audit/test path.
# The day the model is re-trained with canonical keys this map can
# go away.
_CANONICAL_FAMILY_TO_MODEL_KEY: Dict[str, str] = {
    "batter_strikeouts": "strikeouts",
    "walks_allowed":     "pitcher_walks",
    "hits_allowed":      "hits_allowed",
    "hits_runs_rbis":    "hits+runs+rbis",
    "pitching_outs":     "pitcher_outs",
    # All other canonical families are byte-identical to model keys.
}


def family_to_model_key(family: Optional[str]) -> Optional[str]:
    """Translate a canonical family token to the legacy key the MLB-HF
    model artifact expects. Idempotent: legacy keys round-trip."""
    if not family:
        return family
    return _CANONICAL_FAMILY_TO_MODEL_KEY.get(family, family)


_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def normalize_player_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ── Index ensure ──────────────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    await db[CACHE_COLL].create_index(
        [("sport", ASCENDING), ("game_date", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("stat_family", ASCENDING),
         ("source_version", ASCENDING)],
        name="feature_cache_compound_unique", unique=True,
    )
    await db[CACHE_COLL].create_index("game_date")
    await db[CACHE_COLL].create_index("stat_family")
    await db[CACHE_COLL].create_index("player_id")

    await db[STATUS_COLL].create_index(
        [("game_date", ASCENDING), ("source_version", ASCENDING)],
        name="feature_status_unique", unique=True,
    )
    await db[STATUS_COLL].create_index("status")
    await db[STATUS_COLL].create_index("completed_at")


# ── Identity / hub resolution ─────────────────────────────────────────
async def _build_name_to_hub_index(db) -> Dict[str, Dict[str, Any]]:
    """One-shot scan of `mlb_master_hub_2026` keyed by normalized
    display_name. Also joins identity-map MLBAM ids so Statcast lookups
    actually find rows (the hub doc itself rarely carries `mlbam_id`)."""
    # Identity map: bdl_id → mlb_id (MLBAM)
    bdl_to_mlbam: Dict[int, int] = {}
    async for m in db.mlb_player_identity_map.find(
        {}, {"_id": 0, "bdl_id": 1, "mlb_id": 1, "statcast_id": 1},
    ):
        bdl = m.get("bdl_id")
        mid = m.get("mlb_id") or m.get("statcast_id")
        if bdl is not None and mid is not None:
            try:
                bdl_to_mlbam[int(bdl)] = int(mid)
            except (TypeError, ValueError):
                continue

    out: Dict[str, Dict[str, Any]] = {}
    proj = {
        "_id": 0,
        "display_name": 1, "player_name": 1, "mlb_full_name": 1,
        "bdl_id": 1, "mlbam_id": 1, "mlb_id": 1, "statcast_id": 1,
        "team": 1, "position": 1, "bat_side": 1, "throws": 1,
        "is_pitcher": 1, "is_batter": 1,
        "bdl_game_logs": {"$slice": WINDOW_DEPTH},
    }
    async for p in db.mlb_master_hub_2026.find({}, proj):
        # Stamp the identity-map MLBAM onto the doc.
        bdl = p.get("bdl_id")
        try:
            bdl_i = int(bdl) if bdl is not None else None
        except (TypeError, ValueError):
            bdl_i = None
        if bdl_i is not None and not any(
            p.get(k) for k in ("mlbam_id", "mlb_id", "statcast_id")
        ):
            mid = bdl_to_mlbam.get(bdl_i)
            if mid is not None:
                p["mlbam_id"] = mid
        for nk in (p.get("display_name"), p.get("player_name"),
                   p.get("mlb_full_name")):
            n = normalize_player_name(nk)
            if n and n not in out:
                out[n] = p
    return out


def _resolve_mlbam_id(player: Dict[str, Any]) -> Optional[int]:
    for k in ("mlbam_id", "mlb_id", "statcast_id"):
        v = player.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


# ── Feature extraction ────────────────────────────────────────────────
def _stat_values_as_of(
    game_logs: List[Dict[str, Any]], stat: str, replay_date: str,
) -> Tuple[List[float], List[Optional[float]], List[Optional[str]]]:
    """Return (stat_values, pa_values, dates) filtered to game_date <
    replay_date, newest-first, max WINDOW_DEPTH."""
    field = _STAT_FIELD_MAP.get(stat, stat)
    stat_vals: List[float] = []
    pa_vals: List[Optional[float]] = []
    dates: List[Optional[str]] = []
    for g in game_logs:
        d = (g.get("date") or g.get("game_date") or "")[:10]
        if not d or d >= replay_date:
            continue
        v = g.get(field)
        if v is None:
            continue
        try:
            stat_vals.append(float(v))
        except (TypeError, ValueError):
            continue
        pa = g.get("plate_appearances")
        try:
            pa_vals.append(float(pa) if pa is not None else None)
        except (TypeError, ValueError):
            pa_vals.append(None)
        dates.append(d)
        if len(stat_vals) >= WINDOW_DEPTH:
            break
    return stat_vals, pa_vals, dates


async def _statcast_batter_as_of(
    db, mlbam_id: Optional[int], replay_date: str,
) -> Optional[Dict[str, Any]]:
    if mlbam_id is None:
        return None
    doc = await db.mlb_statcast_player_features.find_one(
        {"player_id": int(mlbam_id),
         "game_date": {"$lt": replay_date}},
        projection={"_id": 0, "game_date": 1,
                    "rolling_7": 1, "rolling_14": 1,
                    "rolling_30": 1, "season_window": 1},
        sort=[("game_date", pymongo.DESCENDING)],
    )
    return doc or None


async def _statcast_pitcher_as_of(
    db, mlbam_id: Optional[int], replay_date: str,
) -> Optional[Dict[str, Any]]:
    if mlbam_id is None:
        return None
    doc = await db.mlb_statcast_pitcher_features.find_one(
        {"pitcher_id": int(mlbam_id),
         "game_date": {"$lt": replay_date}},
        projection={"_id": 0, "game_date": 1,
                    "rolling_14": 1, "rolling_30": 1, "season_window": 1},
        sort=[("game_date", pymongo.DESCENDING)],
    )
    return doc or None


def _compute_hit_rate_panels(
    stat_vals: List[float],
) -> Dict[str, Any]:
    """Return a structure containing per-window mean/sd/CV plus the
    arrays needed at replay time for line-specific hit-rate
    derivation. Hit-rate vs line is NOT pre-computed (lines vary per
    market row)."""
    out: Dict[str, Any] = {
        "n_games": len(stat_vals),
        "stat_values": list(stat_vals),
    }
    if not stat_vals:
        return out
    for n in (3, 5, 10, 20):
        sub = stat_vals[:n]
        if len(sub) >= max(1, min(n, 3)):
            mean = sum(sub) / len(sub)
            if len(sub) > 1:
                var = sum((v - mean) ** 2 for v in sub) / (len(sub) - 1)
                sd = var ** 0.5
            else:
                sd = 0.0
            cv = (sd / mean) if mean > 0 else 0.0
            out[f"l{n}_mean"] = mean
            out[f"l{n}_sd"] = sd
            out[f"l{n}_cv"] = cv
            out[f"l{n}_max"] = max(sub)
            out[f"l{n}_min"] = min(sub)
    out["cv_l10"] = out.get("l10_cv", 0.0)
    return out


# ── Universe discovery ───────────────────────────────────────────────
async def list_universe_for_date(
    db, replay_date: str, *,
    odds_collection: str = "mlb_historical_alt_odds_raw",
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """Read distinct `(player_name_normalized, stat_family)` tuples
    that had a prop priced on `replay_date`.

    Returns (sorted_pairs, stats) where `stats` carries telemetry:
        odds_rows_in_window
        distinct_player_market_pairs
        skipped_stat_family_mismatch  — markets that don't map to a family
        unknown_markets_sample        — up to 10 unmapped market values
        sample_players                — up to 10 normalized player names

    `odds_collection` lets SGO-namespace replays read
    `sgo_replay_alt_odds_raw` instead of the live archive — must match
    the collection the Layer-3 engine reads (set by run_production_replay
    `odds_collection=` kwarg).
    """
    n_rows = await db[odds_collection].count_documents(
        {"game_date": replay_date})
    cursor = db[odds_collection].aggregate([
        {"$match": {"game_date": replay_date}},
        {"$group": {
            "_id": {"player": "$player_name_normalized",
                    "market": "$market"},
        }},
    ])
    pairs: set = set()
    n_pair_rows = 0
    n_no_family = 0
    unknown_markets: Counter = Counter()
    sample_players: set = set()
    async for r in cursor:
        n_pair_rows += 1
        market = r["_id"]["market"]
        fam = market_to_stat_family(market)
        player = r["_id"]["player"] or ""
        if len(sample_players) < 10 and player:
            sample_players.add(player)
        if fam is None:
            n_no_family += 1
            unknown_markets[market] += 1
            continue
        pairs.add((player, fam))
    stats = {
        "odds_collection": odds_collection,
        "odds_rows_in_window": n_rows,
        "distinct_player_market_pairs": n_pair_rows,
        "skipped_stat_family_mismatch": n_no_family,
        "unknown_markets_sample": dict(unknown_markets.most_common(10)),
        "sample_players": sorted(sample_players),
    }
    return sorted(pairs), stats


# ── Per-date cache build ─────────────────────────────────────────────
async def cache_date(
    db, replay_date: str, *,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    force: bool = False,
    odds_collection: str = "mlb_historical_alt_odds_raw",
) -> Dict[str, Any]:
    """Build cache for one date. Returns a summary dict.

    `odds_collection` lets SGO-namespace replays read
    `sgo_replay_alt_odds_raw`. MUST match the collection the Layer-3
    engine reads (set by run_production_replay `odds_collection=` kwarg)
    or candidates_skipped_no_cache spikes.

    HARD-FAIL: raises RuntimeError when odds universe > 0 but
    rows_written == 0. This stops the chain immediately when the cache
    isn't covering the replay universe instead of silently letting
    Layer-3 skip 100% of props.
    """
    await ensure_indexes(db)

    # Resume short-circuit
    if not force:
        s = await db[STATUS_COLL].find_one(
            {"game_date": replay_date, "source_version": SOURCE_VERSION},
            projection={"_id": 0, "status": 1},
        )
        if s and s.get("status") == "completed":
            logger.info(
                "[feature_cache] %s already completed (source_version=%s)",
                replay_date, SOURCE_VERSION,
            )
            return {"date": replay_date, "skipped": True}

    started_at = datetime.now(timezone.utc)
    rss0 = _rss_mb()
    await db[STATUS_COLL].update_one(
        {"game_date": replay_date, "source_version": SOURCE_VERSION},
        {"$set": {"status": "in_progress", "started_at": started_at,
                  "rss_mb_start": round(rss0, 1)}},
        upsert=True,
    )

    universe, universe_stats = await list_universe_for_date(
        db, replay_date, odds_collection=odds_collection,
    )
    if not universe:
        # Either zero odds rows in the window OR every market failed
        # stat_family lookup. Both cases reported in universe_stats.
        await db[STATUS_COLL].update_one(
            {"game_date": replay_date, "source_version": SOURCE_VERSION},
            {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc),
                      "players": 0, "pairs": 0, "rows_written": 0,
                      "reason": "empty_universe", **universe_stats}},
        )
        return {"date": replay_date, "players": 0, "pairs": 0,
                "rows_written": 0, "elapsed_s": 0,
                "rss_mb_start": round(rss0, 1),
                "rss_mb_peak": round(rss0, 1),
                "rss_mb_end": round(_rss_mb(), 1),
                **universe_stats}

    name_to_hub = await _build_name_to_hub_index(db)
    rss_peak = max(rss0, _rss_mb())

    buffer: List[Dict[str, Any]] = []
    rows_written = 0
    n_pairs_cached = 0
    n_pairs_skipped_no_hub = 0
    n_pairs_skipped_no_logs = 0
    missed_player_sample: List[str] = []   # up to 10 normalized names with no hub match
    players_seen: set = set()

    async def _flush() -> int:
        nonlocal buffer
        if not buffer:
            return 0
        ops = []
        for r in buffer:
            f = {k: r[k] for k in
                 ("sport", "game_date", "player_name_normalized",
                  "stat_family", "source_version")}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        try:
            res = await db[CACHE_COLL].bulk_write(ops, ordered=False)
            n = int(res.upserted_count or 0) + int(res.modified_count or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("[feature_cache] bulk_write failed: %s", exc)
            n = 0
        buffer.clear()
        return n

    statcast_batter_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    statcast_pitcher_cache: Dict[int, Optional[Dict[str, Any]]] = {}

    for player_norm, stat_family in universe:
        rss = _rss_mb()
        if rss > rss_peak:
            rss_peak = rss
        if rss > mem_limit_mb:
            rows_written += await _flush()
            await db[STATUS_COLL].update_one(
                {"game_date": replay_date, "source_version": SOURCE_VERSION},
                {"$set": {"status": "memory_halt",
                          "rss_mb_at_halt": round(rss, 1),
                          "pairs_done": n_pairs_cached,
                          "pairs_total": len(universe)}},
            )
            raise MemoryError(
                f"RSS {rss:.1f}MB > {mem_limit_mb}MB at pair "
                f"{n_pairs_cached}/{len(universe)} ({player_norm}/{stat_family})"
            )

        p = name_to_hub.get(player_norm)
        if not p:
            n_pairs_skipped_no_hub += 1
            if len(missed_player_sample) < 10:
                missed_player_sample.append(player_norm)
            continue
        mlbam = _resolve_mlbam_id(p)
        game_logs = p.get("bdl_game_logs", []) or []
        stat_vals, pa_vals, dates = _stat_values_as_of(
            game_logs, stat_family, replay_date,
        )
        if len(stat_vals) < 5:
            n_pairs_skipped_no_logs += 1
            continue

        if stat_family in _PITCHER_FAMILIES:
            sc_self = statcast_pitcher_cache.get(mlbam) if mlbam else None
            if mlbam and mlbam not in statcast_pitcher_cache:
                sc_self = await _statcast_pitcher_as_of(db, mlbam, replay_date)
                statcast_pitcher_cache[mlbam] = sc_self
        else:
            sc_self = statcast_batter_cache.get(mlbam) if mlbam else None
            if mlbam and mlbam not in statcast_batter_cache:
                sc_self = await _statcast_batter_as_of(db, mlbam, replay_date)
                statcast_batter_cache[mlbam] = sc_self

        panels = _compute_hit_rate_panels(stat_vals)
        row = {
            "sport": "mlb",
            "game_date": replay_date,
            "player_name_normalized": player_norm,
            "player_name_canonical": p.get("display_name") or p.get("player_name"),
            "player_id": mlbam,
            "bdl_id": p.get("bdl_id"),
            "team": p.get("team"),
            "position": p.get("position"),
            "bat_side": p.get("bat_side"),
            "throws": p.get("throws"),
            "is_pitcher": bool(p.get("is_pitcher")),
            "stat_family": stat_family,
            "as_of_cutoff": replay_date,
            "source_version": SOURCE_VERSION,
            "n_games": panels["n_games"],
            "stat_values": panels["stat_values"],
            "pa_values": pa_vals,
            "dates": dates,
            "l3_mean":  panels.get("l3_mean"),
            "l5_mean":  panels.get("l5_mean"),
            "l10_mean": panels.get("l10_mean"),
            "l20_mean": panels.get("l20_mean"),
            "l5_sd":    panels.get("l5_sd"),
            "l10_sd":   panels.get("l10_sd"),
            "l20_sd":   panels.get("l20_sd"),
            "l5_cv":    panels.get("l5_cv"),
            "l10_cv":   panels.get("l10_cv"),
            "l20_cv":   panels.get("l20_cv"),
            "cv_l10":   panels.get("cv_l10"),
            "l5_max":   panels.get("l5_max"),
            "l10_max":  panels.get("l10_max"),
            "l5_min":   panels.get("l5_min"),
            "l10_min":  panels.get("l10_min"),
            # Statcast (as-of, future-safe)
            "statcast_self_as_of": sc_self,
            "statcast_self_kind":
                "pitcher" if stat_family in _PITCHER_FAMILIES else "batter",
            # Opp pitcher context — null for now (no historical SP feed
            # available; Layer 3 imputes). Schema reserved here so we
            # can fill it without re-running the rest of the cache.
            "opp_pitcher_id": None,
            "opp_pitcher_name": None,
            "opp_pitcher_throws": None,
            "opp_pitcher_statcast_as_of": None,
            "opp_pitcher_is_imputed": True,
            # Provenance
            "cached_at": datetime.now(timezone.utc),
        }
        buffer.append(row)
        players_seen.add(player_norm)
        n_pairs_cached += 1

        if len(buffer) >= 500:
            rows_written += await _flush()

    rows_written += await _flush()

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    rss_end = _rss_mb()
    completed_at = datetime.now(timezone.utc)
    summary = {
        "date": replay_date,
        "universe_size": len(universe),
        "pairs_cached": n_pairs_cached,
        "skipped_no_hub": n_pairs_skipped_no_hub,                    # = player_name_mismatch
        "skipped_player_name_mismatch": n_pairs_skipped_no_hub,
        "skipped_few_logs": n_pairs_skipped_no_logs,
        "missed_player_sample": missed_player_sample,
        "players_cached": len(players_seen),
        "rows_written": rows_written,
        "elapsed_s": elapsed,
        "rss_mb_start": round(rss0, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "rss_mb_end": round(rss_end, 1),
        "source_version": SOURCE_VERSION,
        # Universe-side telemetry from list_universe_for_date()
        **universe_stats,
    }
    await db[STATUS_COLL].update_one(
        {"game_date": replay_date, "source_version": SOURCE_VERSION},
        {"$set": {"status": "completed", "completed_at": completed_at, **summary}},
    )
    logger.info(
        "[feature_cache] %s done pairs=%d players=%d rows=%d "
        "no_hub=%d few_logs=%d stat_fam_miss=%d "
        "rss=%.1f/%.1f/%.1fMB elapsed=%.1fs",
        replay_date, n_pairs_cached, len(players_seen), rows_written,
        n_pairs_skipped_no_hub, n_pairs_skipped_no_logs,
        universe_stats.get("skipped_stat_family_mismatch", 0),
        rss0, rss_peak, rss_end, elapsed,
    )
    # ── HARD-FAIL: odds universe was non-empty but we wrote nothing ──
    if rows_written == 0 and universe_stats.get("odds_rows_in_window", 0) > 0:
        msg = (
            f"[feature_cache] HARD-FAIL: rows_written=0 while "
            f"odds_rows_in_window={universe_stats.get('odds_rows_in_window')} "
            f"in {odds_collection}. universe_size={len(universe)}, "
            f"skipped_no_hub={n_pairs_skipped_no_hub}, "
            f"skipped_few_logs={n_pairs_skipped_no_logs}, "
            f"skipped_stat_family_mismatch={universe_stats.get('skipped_stat_family_mismatch', 0)}. "
            f"sample missed-name normalized values: {missed_player_sample[:5]}. "
            f"This means Layer-3 will skip 100% of props — fix matching "
            f"BEFORE re-running replay."
        )
        await db[STATUS_COLL].update_one(
            {"game_date": replay_date, "source_version": SOURCE_VERSION},
            {"$set": {"status": "hard_fail_zero_rows", "error": msg}},
        )
        raise RuntimeError(msg)
    return summary


__all__ = [
    "CACHE_COLL", "STATUS_COLL", "SOURCE_VERSION",
    "DEFAULT_MEM_LIMIT_MB", "WINDOW_DEPTH",
    "ensure_indexes", "list_universe_for_date", "cache_date",
    "market_to_stat_family", "normalize_player_name",
]
