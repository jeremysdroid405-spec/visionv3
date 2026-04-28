"""
MLB Pick History — Persistent Forward-Testing Logger (Total Bases v1)
======================================================================
Mirrors the NBA `pick_history` design but writes to a separate
`mlb_pick_history` collection so MLB and NBA never collide.

Design tenets:
  * Read-only to model behavior — no scoring fields are mutated.
  * Idempotent — unique index `(game_date, player, stat_family, line,
    side, bookmaker)` means re-running a slate updates rather than
    duplicates a pick.
  * Survives recompute overwrites — written from the engine after gate
    settlement, but the outcome triplet (`hit`/`result`/`actual`) is
    `$setOnInsert`-only, so a graded pick is never silently re-set
    when the slate is rescored.

Public surface:
  ensure_indexes(db)            — bootstrap the unique + analytics indexes
  log_selected_picks(db, picks) — bulk-upsert selected picks
  query_*                       — analytics helpers (overall, by-tier, …)

Schema (per spec):
  timestamp, game_date, player, team, opponent,
  stat_family ('TOTAL_BASES'), line, side, tier,
  market_type, is_alternate, bookmaker, ref_odds,
  mu, sigma, p_model, tp, edge_pct, vision_score,
  hit_rate, cv,
  expected_PA, batting_order, woba_proxy,
  xwOBA (None if unavailable), barrel_rate, hard_hit_rate,
  matchup_factor, model_version='mlb_total_bases_v1',
  result, actual, hit          # outcome — populated by updater
"""
from __future__ import annotations

import logging
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION_NAME = "mlb_pick_history"

# Tiers we log. Anything outside this is unqualified — never a pick.
SELECTED_TIERS = ("safe_haven", "front_lines", "war_zone")

# Static for v1; bump when the μ/σ formula changes.
MODEL_VERSION = "mlb_total_bases_v1_locked"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
async def ensure_indexes(db) -> None:
    """Create unique + analytics indexes. Safe to call repeatedly."""
    coll = db[COLLECTION_NAME]
    # Unique compound — one row per (game_date, player, stat_family,
    # line, side, bookmaker). Bookmaker is in the key so the same
    # player/line picked off DK and FD both persist.
    await coll.create_index(
        [("game_date", 1), ("player", 1), ("stat_family", 1),
         ("line", 1), ("side", 1), ("bookmaker", 1)],
        name="uniq_date_player_stat_line_side_book",
        unique=True,
    )
    # Analytics + updater hot-path indexes.
    await coll.create_index([("hit", 1), ("game_date", 1)], name="hit_date")
    await coll.create_index([("model_version", 1)], name="model_version")
    await coll.create_index([("tier", 1), ("game_date", 1)], name="tier_date")
    await coll.create_index([("player", 1), ("game_date", 1)],
                              name="player_date")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_date_str(v: Any) -> Optional[str]:
    if v is None: return None
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _board_fingerprint(picks: List[Dict[str, Any]]) -> str:
    """Stable hash of the slate so a (sport, recompute) batch is
    identifiable even after a re-score overwrite."""
    keys = sorted(
        f"{p.get('game_date')}|{p.get('player')}|{p.get('line')}|"
        f"{p.get('side')}|{p.get('bookmaker')}"
        for p in picks)
    h = hashlib.sha1()
    for k in keys: h.update(k.encode("utf-8"))
    return h.hexdigest()[:16]


def _build_pick_doc(p: Dict[str, Any], *, fingerprint: str
                     ) -> Optional[Dict[str, Any]]:
    """Project an engine-emitted pick into the pick_history schema.
    Returns None if the pick is unqualified or missing required keys."""
    tier = p.get("tier") or p.get("tier_final")
    if tier not in SELECTED_TIERS:
        return None
    player = p.get("player")
    line   = p.get("line")
    side   = (p.get("side") or "OVER").upper()
    book   = (p.get("bookmaker") or p.get("ref_book") or "").strip().lower()
    date   = _to_date_str(p.get("game_date") or p.get("date"))
    if (not player or line is None or not date or not book
            or side not in ("OVER", "UNDER")):
        return None
    return {
        "timestamp":        datetime.now(timezone.utc),
        "game_date":        date,

        "player":           player,
        "team":             p.get("team"),
        "opponent":         p.get("opponent") or p.get("opponent_team"),

        "stat_family":      "TOTAL_BASES",
        "line":             float(line),
        "side":             side,
        "tier":             tier,

        "market_type":      p.get("market_type"),
        "is_alternate":     bool(p.get("is_alt") or p.get("is_alternate")),
        "bookmaker":        book,
        "ref_odds":         p.get("ref_odds"),

        # Production μ/σ + market features
        "mu":               p.get("mu"),
        "sigma":            p.get("sigma"),
        "p_model":          p.get("p_model_pct"),
        "tp":               p.get("tp"),
        "edge_pct":         p.get("edge_pct"),
        "vision_score":     p.get("vision_score"),
        "hit_rate":         p.get("hit_rate"),
        "cv":               p.get("cv"),

        # MLB-specific feature snapshot (per spec)
        "expected_PA":      p.get("expected_PA")  or p.get("pa_proj"),
        "batting_order":    p.get("batting_order"),
        "woba_proxy":       p.get("woba_proxy")   or p.get("woba_long"),
        "xwOBA":            p.get("xwOBA"),         # None if unavailable
        "barrel_rate":      p.get("barrel_rate")  or p.get("barrel"),
        "hard_hit_rate":    p.get("hard_hit_rate"),  # None if unavailable
        "matchup_factor":   p.get("matchup_factor") or p.get("matchup"),

        "model_version":    MODEL_VERSION,
        "board_fingerprint": fingerprint,

        # Identity-resolution snapshot (lock-down spec requirement)
        "identity_match_method": p.get("identity_match_method"),
        "identity_confidence":   p.get("identity_confidence"),
        "statcast_id":           p.get("statcast_id"),

        # Sample-quality fields (used by forward_test_report BBE slice)
        "bbe_30":           p.get("bbe_30"),
        "pa_30":            p.get("pa_30"),

        # Pitcher-context (SHADOW — never affects μ/σ/edge/gates).
        # Populated post-hoc by `scripts.mlb_backfill_pitcher_context`
        # after Statcast for the pick's game_date is ingested. See the
        # daily pipeline; expect None on freshly-logged picks.
        "pitcher_id":              p.get("pitcher_id"),
        "pitcher_name":            p.get("pitcher_name"),
        "pitcher_p_throws":        p.get("pitcher_p_throws"),
        "batter_stand":            p.get("batter_stand"),
        "pitcher_xwOBA_allowed":   p.get("pitcher_xwOBA_allowed"),
        "pitcher_split_used":      p.get("pitcher_split_used"),
        "matchup_factor_shadow":   p.get("matchup_factor_shadow"),
        "pitcher_confidence_flag": p.get("pitcher_confidence_flag"),
        "pitcher_resolved_via":    p.get("pitcher_resolved_via"),
        "shadow_feature_version":  p.get("shadow_feature_version"),

        # Outcome (filled by scripts/update_mlb_pick_results.py)
        "result":           None,
        "actual":           None,
        "hit":              None,

        # Audit trail
        "event_id":         p.get("event_id"),
    }


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
async def log_selected_picks(
    db, picks: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Persist every pick whose tier is in SELECTED_TIERS into
    `mlb_pick_history`. Idempotent via the unique index. Outcome
    triplet (`hit`/`result`/`actual`) is `$setOnInsert`-only so a
    graded pick is preserved across re-scores.

    Returns `{inserted, updated, skipped, errors}`."""
    if not picks:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    fingerprint = _board_fingerprint(picks)
    docs: List[Dict[str, Any]] = []
    for p in picks:
        pd = _build_pick_doc(p, fingerprint=fingerprint)
        if pd is not None: docs.append(pd)
    if not docs:
        return {"inserted": 0, "updated": 0,
                "skipped": len(picks), "errors": 0}

    coll = db[COLLECTION_NAME]
    from pymongo import UpdateOne
    ops = []
    outcome_only = {"result", "actual", "hit", "timestamp"}
    for pd in docs:
        update_set = {k: v for k, v in pd.items() if k not in outcome_only}
        set_on_insert = {k: pd[k] for k in outcome_only if k in pd}
        ops.append(UpdateOne(
            filter={
                "game_date":   pd["game_date"],
                "player":      pd["player"],
                "stat_family": pd["stat_family"],
                "line":        pd["line"],
                "side":        pd["side"],
                "bookmaker":   pd["bookmaker"],
            },
            update={"$set": update_set, "$setOnInsert": set_on_insert},
            upsert=True,
        ))
    inserted = updated = errors = 0
    try:
        result = await coll.bulk_write(ops, ordered=False)
        inserted = result.upserted_count or 0
        updated  = result.modified_count or 0
    except Exception as e:
        errors = len(ops)
        logger.warning(f"[MLB_PICK_HISTORY] bulk_write failed: {e!r}")
    skipped = len(picks) - len(docs)
    logger.info(
        f"[MLB_PICK_HISTORY] inserted={inserted} updated={updated} "
        f"skipped={skipped} errors={errors} fingerprint={fingerprint} "
        f"model_version={MODEL_VERSION}"
    )
    return {"inserted": inserted, "updated": updated,
            "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def _roi_minus110(wins: int, losses: int) -> float:
    n = wins + losses
    return (wins * 100 - losses * 110) / (n * 110) * 100 if n else 0.0


def _bucket_edge(e: Optional[float]) -> str:
    if e is None: return "—"
    if e < 0:    return "<0%"
    if e < 5:    return "0–5%"
    if e < 10:   return "5–10%"
    if e < 15:   return "10–15%"
    return "15%+"


async def _aggregate(coll, group_id: Any,
                     model_version: Optional[str] = None,
                     match_extra: Optional[Dict[str, Any]] = None,
                     ) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {"hit": {"$in": [True, False]}}
    if model_version: match["model_version"] = model_version
    if match_extra:   match.update(match_extra)
    pipe = [
        {"$match": match},
        {"$group": {
            "_id":  group_id,
            "n":    {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$hit", True]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    out = []
    async for d in coll.aggregate(pipe):
        n = d["n"]; wins = d["wins"]; losses = n - wins
        out.append({
            "key":      d["_id"],
            "n":        n,
            "wins":     wins,
            "losses":   losses,
            "win_rate": round(wins / n * 100, 2) if n else 0.0,
            "roi_110":  round(_roi_minus110(wins, losses), 2),
        })
    return out


async def query_overall(db, model_version: Optional[str] = MODEL_VERSION):
    rows = await _aggregate(db[COLLECTION_NAME], group_id=None,
                            model_version=model_version)
    return rows[0] if rows else {"n": 0, "wins": 0, "losses": 0,
                                   "win_rate": 0.0, "roi_110": 0.0}


async def query_by_tier(db, model_version: Optional[str] = MODEL_VERSION):
    return await _aggregate(db[COLLECTION_NAME], group_id="$tier",
                              model_version=model_version)


async def query_by_side(db, model_version: Optional[str] = MODEL_VERSION):
    return await _aggregate(db[COLLECTION_NAME], group_id="$side",
                              model_version=model_version)


async def query_by_market_type(db, model_version: Optional[str] = MODEL_VERSION):
    return await _aggregate(db[COLLECTION_NAME], group_id="$market_type",
                              model_version=model_version)


async def query_by_batting_order(db, model_version: Optional[str] = MODEL_VERSION):
    return await _aggregate(db[COLLECTION_NAME], group_id="$batting_order",
                              model_version=model_version)


async def query_by_player(db, model_version: Optional[str] = MODEL_VERSION,
                           min_n: int = 1):
    rows = await _aggregate(db[COLLECTION_NAME], group_id="$player",
                              model_version=model_version)
    return [r for r in rows if r["n"] >= min_n]


async def query_by_team(db, model_version: Optional[str] = MODEL_VERSION):
    return await _aggregate(db[COLLECTION_NAME], group_id="$team",
                              model_version=model_version)


async def query_by_edge_bucket(db, model_version: Optional[str] = MODEL_VERSION):
    coll = db[COLLECTION_NAME]
    match: Dict[str, Any] = {"hit": {"$in": [True, False]}}
    if model_version: match["model_version"] = model_version
    counts: Dict[str, Tuple[int, int]] = {}
    async for d in coll.find(match, {"edge_pct": 1, "hit": 1, "_id": 0}):
        b = _bucket_edge(d.get("edge_pct"))
        n, w = counts.get(b, (0, 0))
        counts[b] = (n + 1, w + (1 if d.get("hit") else 0))
    out = []
    for b in ("<0%", "0–5%", "5–10%", "10–15%", "15%+", "—"):
        if b not in counts: continue
        n, w = counts[b]; losses = n - w
        out.append({
            "key": b, "n": n, "wins": w, "losses": losses,
            "win_rate": round(w / n * 100, 2) if n else 0.0,
            "roi_110":  round(_roi_minus110(w, losses), 2),
        })
    return out


async def query_picks_per_slate(db,
                                 model_version: Optional[str] = MODEL_VERSION):
    """Picks per game_date — both unsettled and settled. Useful for
    confirming the engine is firing the expected daily volume."""
    coll = db[COLLECTION_NAME]
    match: Dict[str, Any] = {}
    if model_version: match["model_version"] = model_version
    pipe = [
        {"$match": match},
        {"$group": {
            "_id":      "$game_date",
            "n":        {"$sum": 1},
            "settled":  {"$sum": {"$cond":
                                   [{"$ne": ["$hit", None]}, 1, 0]}},
            "wins":     {"$sum": {"$cond":
                                   [{"$eq": ["$hit", True]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    out = []
    async for d in coll.aggregate(pipe):
        out.append({
            "key": d["_id"], "n": d["n"], "settled": d["settled"],
            "wins": d["wins"], "losses": d["settled"] - d["wins"],
        })
    return out


__all__ = [
    "COLLECTION_NAME", "MODEL_VERSION", "SELECTED_TIERS",
    "ensure_indexes", "log_selected_picks",
    "query_overall", "query_by_tier", "query_by_side",
    "query_by_market_type", "query_by_batting_order",
    "query_by_player", "query_by_team",
    "query_by_edge_bucket", "query_picks_per_slate",
]
