"""Phase 4 — Replay-row → NormalizedMetrics field hydrators.

Pure read-only loaders that source the SAME data the live serving path
consumes, but for a historical (game_date, snapshot_iso) pair. No
duplicated business logic, no copied thresholds.

Three hydrators:

  1. `load_book_inventory(...)` — per-prop book coverage from
     `mlb_historical_alt_odds_raw` for a single snapshot. Drives
     both `book_count` (NormalizedMetrics.coverage_gate) and
     `tp_source` (NormalizedMetrics.tp_source_gate). Uses exactly the
     same key-tuple `tp_engine.compute_tp()` would use (event_id +
     player_norm + market + line) restricted to one snapshot_iso.

  2. `load_player_game_logs_as_of(...)` — game logs from
     `mlb_master_hub_2026.bdl_game_logs[]` filtered to entries strictly
     BEFORE the replay date. Identical filter the live margin computer
     in `MLBTierSorter._calculate_hit_margins` uses (sorted desc by
     date, take L20 / L10 / None per sample size).

  3. `compute_avg_margins_from_logs(...)` — pure function port of
     `MLBTierSorter._calculate_hit_margins(...)` byte-for-byte, ported
     here so we don't depend on the live `MLBTierSorter` object (which
     carries DB handles, vision state, etc.). Identical window
     (20/10/None), identical hit-rule (`value > line`), identical
     missing-value handling (None counted as miss with margin=line).
     A unit test in `tests/replay/test_phase4_margins.py` enforces
     parity against the live `MLBTierSorter` once that test exists.

Stat-family resolution for the replay-row → live-table bridge uses
the SSOT `services.scoring.canonical_stats` registry exactly as the
live `metrics_builder` does — no new alias table.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ── Stat-family bridge (replay row → live canonical) ───────────────────
# The replay engine emits a sport-internal `stat_family` token (e.g.
# `"strikeouts"`) that does NOT match the live threshold-table keys
# (which use `"batter_strikeouts"`). The live registry is the SSOT —
# we MUST round-trip the replay row's `market` field through it.
from services.scoring.canonical_stats import (
    market_to_stat_map,
    stat_family as _canonical_stat_family,
)


def resolve_canonical_stat_family(sport: str, market: str) -> str:
    """Round-trip a market key through the live SSOT registry.

    `mlb_historical_alt_odds_raw` rows carry e.g. `market="batter_hits"`.
    The registry maps that → `stat_type="Hits"` → family `"hits"` (the
    key used by `_MLB_SAFE_HAVEN["hits"]` and friends).

    Returns the canonical family string, or `"_default"` if the market
    is unknown (in which case the gate engine routes to the tier's
    `_default` config block — same fail-closed behaviour as live).
    """
    if not market:
        return "_default"
    m = market.lower().strip()
    # Strip the universal `_alternate` suffix so alt and standard
    # markets resolve to the same family (the live registry already
    # registers both with/without — see canonical_stats market_to_stat).
    m_root = m[:-len("_alternate")] if m.endswith("_alternate") else m
    mapping = market_to_stat_map(sport)
    stat_type = mapping.get(m) or mapping.get(m_root)
    if not stat_type:
        return "_default"
    return _canonical_stat_family(sport, stat_type, strict=False)


# ── 1. Book inventory ──────────────────────────────────────────────────
async def load_book_inventory(
    db, *, sport: str, game_date: str, snapshot_iso: str,
) -> Dict[Tuple[str, str, str, float], Dict[str, Set[str]]]:
    """Returns {key → {"OVER": set[book], "UNDER": set[book]}} where
    key = (event_id, player_name_normalized, market, line).

    `book_count` for a replay row (event, player, market, line, side)
        = len(inv[key][side])
    `tp_source`
        = "devig"     if inv[key]["OVER"] ∩ inv[key]["UNDER"] is non-empty
        = "one_sided" if our side has ≥1 book but no overlap
        = None        if our side has 0 books (impossible — the replay
                       row itself proves at least one book existed)

    Mirrors the per-book pairing logic of `tp_engine.compute_tp()`
    Path-1: a book is "paired" iff it quoted BOTH sides at this
    snapshot — that's what `{book}_odds_opp` represents in live.
    """
    coll = "mlb_historical_alt_odds_raw" if sport == "mlb" else None
    if coll is None:
        raise NotImplementedError(f"book_inventory for sport={sport!r}")
    cursor = db[coll].find(
        {"sport": sport, "game_date": game_date,
         "snapshot_iso": snapshot_iso},
        projection={"_id": 0, "event_id": 1, "player_name_normalized": 1,
                    "market": 1, "line": 1, "side": 1, "book": 1},
    )
    inv: Dict[Tuple[str, str, str, float], Dict[str, Set[str]]] = {}
    async for r in cursor:
        line = r.get("line")
        side = (r.get("side") or "").upper()
        book = (r.get("book") or "").strip().lower()
        if line is None or side not in ("OVER", "UNDER") or not book:
            continue
        key = (
            str(r["event_id"]),
            str(r["player_name_normalized"]),
            str(r["market"]),
            float(line),
        )
        bucket = inv.setdefault(key, {"OVER": set(), "UNDER": set()})
        bucket[side].add(book)
    return inv


def resolve_book_coverage(
    inv: Dict[Tuple[str, str, str, float], Dict[str, Set[str]]],
    *, event_id: str, player_norm: str, market: str, line: float,
    side: str,
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve (book_count, tp_source) for a single replay row."""
    key = (str(event_id), str(player_norm), str(market), float(line))
    bucket = inv.get(key)
    if not bucket:
        return None, None
    our_side = (side or "").upper()
    own = bucket.get(our_side) or set()
    opp = bucket.get("UNDER" if our_side == "OVER" else "OVER") or set()
    book_count = len(own) if own else 0
    paired = own & opp
    if paired:
        tp_source = "devig"
    elif own:
        tp_source = "one_sided"
    else:
        tp_source = None
    return book_count, tp_source


# ── 2. Player game logs as-of-date ─────────────────────────────────────
async def load_player_game_logs_as_of(
    db, *, game_date: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {player_name_normalized → list[game_log_dict]} where
    every game_log has `date < game_date` and the list is sorted
    DESC by date (newest-first), matching what live
    `MLBTierSorter._get_logs_by_id(...)` -> sort key=date desc does.

    Source: `mlb_master_hub_2026.bdl_game_logs[]`. Same source the
    live `MLBTierSorter` uses today.
    """
    # We need a player normalizer compatible with the replay rows'
    # `player_name_normalized` field.
    from services.replay.mlb_feature_cache import normalize_player_name

    out: Dict[str, List[Dict[str, Any]]] = {}
    cursor = db["mlb_master_hub_2026"].find(
        {"bdl_game_logs.0": {"$exists": True}},
        projection={"_id": 0, "player_name": 1, "bdl_game_logs": 1},
    )
    async for hub in cursor:
        pn = normalize_player_name(hub.get("player_name") or "")
        logs = hub.get("bdl_game_logs") or []
        # Filter strictly before game_date. The logs store ISO timestamps;
        # we compare on the date prefix.
        kept: List[Dict[str, Any]] = []
        for g in logs:
            d = (g.get("date") or "")[:10]
            if d and d < game_date:
                kept.append(g)
        if not kept:
            continue
        kept.sort(key=lambda g: (g.get("date") or ""), reverse=True)
        # If multiple hub docs ever resolve to the same normalized
        # name, prefer the longer log list (defensive).
        prior = out.get(pn)
        if prior is None or len(kept) > len(prior):
            out[pn] = kept
    return out


# ── 3. Margin computation (pure port of MLBTierSorter logic) ───────────
# Live logic: `services/mlb_tier_sorter.py:_calculate_hit_margins`
# (lines 260-338). Copied here as a pure function so the replay path
# has no dependency on the live MLBTierSorter object (which carries
# DB / vision state). A unit test in
# `tests/replay/test_phase4_margins.py` enforces byte parity.
_STAT_MAP_TO_LOG_FIELD: Dict[str, Any] = {
    "hits": "hits", "total_bases": "total_bases", "rbis": "rbis",
    "runs": "runs", "home_runs": "home_runs",
    "stolen_bases": "stolen_bases", "singles": "singles",
    "doubles": "doubles", "triples": "triples",
    "batter_walks": "walks", "walks": "walks",
    "batter_strikeouts": "strikeouts", "strikeouts": "strikeouts",
    "hits_runs_rbis": ["hits", "runs", "rbis"],
    # 2026-05 hub stores total_bases_runs_rbis as a derived sum.
    "total_bases_runs_rbis": ["total_bases", "runs", "rbis"],
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_outs": "innings_pitched",
    "pitching_outs": "innings_pitched",
    "earned_runs": "earned_runs",
    "hits_allowed": "hits_allowed",
    "walks_allowed": "pitcher_walks",
}


def compute_avg_margins_from_logs(
    *, logs: List[Dict[str, Any]], stat_family: str, line: float,
) -> Tuple[Optional[float], Optional[float]]:
    """Byte-for-byte port of `MLBTierSorter._calculate_hit_margins`.

    Window selection rule (live): use L20 if ≥20 logs, L10 if ≥10,
    otherwise return (None, None).

    Hit rule: `value > line`. Missing value counts as MISS with
    margin = `line - 0` (mirrors live: `miss_v = float(v) if v is
    not None else 0.0`).
    """
    if not logs:
        return None, None
    field = _STAT_MAP_TO_LOG_FIELD.get(stat_family, stat_family)
    sorted_logs = logs  # caller already sorted desc by date
    if len(sorted_logs) >= 20:
        window = 20
    elif len(sorted_logs) >= 10:
        window = 10
    else:
        return None, None

    hit_margins: List[float] = []
    miss_margins: List[float] = []
    for game in sorted_logs[:window]:
        if isinstance(field, list):
            # Composite stat — sum constituent fields (None → 0)
            v = sum(game.get(f) or 0 for f in field)
        elif field == "innings_pitched":
            ip = game.get(field)
            v = (ip * 3) if ip is not None else None
        elif field == "singles":
            h = game.get("hits")
            if h is not None:
                d = game.get("doubles") or 0
                t = game.get("triples") or 0
                hr = game.get("home_runs") or 0
                v = max(0, h - d - t - hr)
            else:
                v = None
        else:
            v = game.get(field)
        if v is not None and v > line:
            hit_margins.append(float(v) - line)
        else:
            miss_v = float(v) if v is not None else 0.0
            miss_margins.append(line - miss_v)

    avg_hit = round(sum(hit_margins) / len(hit_margins), 3) if hit_margins else None
    avg_miss = round(sum(miss_margins) / len(miss_margins), 3) if miss_margins else None
    return avg_hit, avg_miss


__all__ = [
    "resolve_canonical_stat_family",
    "load_book_inventory",
    "resolve_book_coverage",
    "load_player_game_logs_as_of",
    "compute_avg_margins_from_logs",
]
