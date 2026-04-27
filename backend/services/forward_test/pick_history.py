"""
NBA Pick History — Persistent Forward-Testing Logger
====================================================
Captures every prop selected by the UniversalGateEngine in a dedicated
collection (`nba_pick_history`) so the system can be measured against
actual outcomes over time.

Design tenets (per spec):
  * Read-only to model behavior — no scoring fields are mutated.
  * Lightweight & fast — single async bulk_write call per slate.
  * Idempotent — unique index (player, stat, line, game_date, side)
    means re-running a slate updates rather than duplicates.
  * Survives recompute overwrites — written from the same hook that
    persists score docs but as a separate collection.

Public surface:
  ensure_indexes(db)          — create the unique compound index
  log_selected_picks(db,...)  — called from recompute after gates settle
  query_*                     — analytics helpers (overall, by-stat, …)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION_NAME = "nba_pick_history"

# Tiers we log. Anything outside this set is intentionally dropped
# (the gate engine produces "unqualified" — never a pick).
SELECTED_TIERS = ("safe_haven", "front_lines", "war_zone")


def _model_version() -> str:
    """Stamp a version tag derived from the active prod-config flags so
    we can slice analytics by model generation later. Plain string —
    no parsing logic relies on the format."""
    blend = (os.environ.get("NBA_RATE_BLEND_MODE") or "100_0").strip()
    rfa = (os.environ.get("NBA_RFA_MINUTES_PENALTY") or "0.85").strip()
    return f"nba_v3_{blend}_rfa_{rfa}"


async def ensure_indexes(db) -> None:
    """Create the unique compound index that protects against duplicates.
    Safe to call repeatedly; pymongo will no-op when the index exists.
    """
    coll = db[COLLECTION_NAME]
    # Unique compound — one row per (player, stat, line, date, side).
    # `name` keeps the index human-readable in `db.indexes()`.
    await coll.create_index(
        [
            ("player", 1), ("stat", 1), ("line", 1),
            ("game_date", 1), ("side", 1),
        ],
        name="uniq_player_stat_line_date_side",
        unique=True,
    )
    # Secondary indexes for analytics / updater.
    await coll.create_index([("hit", 1), ("game_date", 1)], name="hit_date")
    await coll.create_index([("model_version", 1)], name="model_version")
    await coll.create_index([("tier", 1), ("game_date", 1)], name="tier_date")


def _to_date_str(v: Any) -> Optional[str]:
    """Normalize anything into 'YYYY-MM-DD'."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _board_fingerprint(score_docs: List[Dict[str, Any]]) -> str:
    """Stable hash of the slate so a given (sport, recompute) batch is
    identifiable even after an overwrite. The fingerprint deliberately
    avoids datetimes so the same slate hashes the same on retries."""
    import hashlib
    keys = sorted(d.get("canonical_key") or "" for d in score_docs)
    h = hashlib.sha1()
    for k in keys:
        h.update(k.encode("utf-8"))
    return h.hexdigest()[:16]


def _build_pick_doc(d: Dict[str, Any], *, fingerprint: str,
                    model_version: str) -> Optional[Dict[str, Any]]:
    """Project a score doc into the pick_history schema. Returns None
    if the doc is not a selected pick or is missing required fields."""
    tier = d.get("tier")
    if tier not in SELECTED_TIERS:
        return None

    side = (d.get("recommendation") or "OVER").upper()
    side = "OVER" if side not in ("OVER", "UNDER") else side
    line = d.get("line")
    stat_raw = d.get("stat_type")
    player = d.get("player_name")
    if line is None or not stat_raw or not player:
        return None
    # Canonical stat family — folds raw market names like
    # `player_points_rebounds_alternate` and `player_points` to the
    # same family code (`pra`, `pts`) so analytics group cleanly.
    # Falls back to the raw stat string when no family mapping exists.
    try:
        from services.scoring.gates.thresholds import resolve_stat_family
        stat = resolve_stat_family("nba", stat_raw)
        if stat in ("_default", "", None):
            stat = stat_raw.upper()
        else:
            stat = stat.upper()
    except Exception:
        stat = stat_raw.upper()

    # game_date — derive from game_start_utc when present, else fall
    # back to capture date (UTC).
    gs = d.get("game_start_utc")
    game_date = _to_date_str(gs) or _to_date_str(d.get("computed_at")) \
        or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # p_model = active p_true picked by the ladder.
    p_model = d.get("p_true_active")

    # tp = market-implied true probability percent (0..100). Some docs
    # legitimately store None; we round-trip whatever's there.
    tp = d.get("tp")

    edge = d.get("edge_pct")
    if edge is None:
        # Fall back to derived edge when stored field is absent.
        if p_model is not None and tp is not None:
            edge = round(p_model * 100.0 - tp, 3)

    # Availability + RFA — surfaced by the rate-layer audit fields the
    # adapter stamps on the score doc when applicable.
    avail = d.get("availability_status") or d.get("avail_status") or "—"
    rfa_applied = bool(d.get("rfa_minutes_penalty_applied") or False)
    rfa_factor = d.get("rfa_minutes_penalty_factor")
    try:
        rfa_factor = float(rfa_factor) if rfa_factor is not None else None
    except (TypeError, ValueError):
        rfa_factor = None

    return {
        "timestamp": datetime.now(timezone.utc),
        "game_date": game_date,

        "player": player,
        "stat": stat,
        "line": float(line),
        "side": side,
        "tier": tier,

        "mu":            d.get("model_projection"),
        "sigma":         d.get("model_sigma"),
        "p_model":       p_model,
        "tp":            tp,
        "edge":          edge,
        "vision_score":  d.get("vision_score"),

        "expected_minutes":      d.get("expected_minutes")
                                 or d.get("expected_minutes_after_rfa_penalty"),
        "availability_status":   avail,
        "rfa_penalty_applied":   rfa_applied,
        "rfa_penalty_factor":    rfa_factor,

        "book_odds":     d.get("tier_reference_odds"),
        "devig_source":  d.get("tp_source") or d.get("quality_source"),

        "board_fingerprint": fingerprint,

        # Outcome fields (filled by update_nba_pick_results.py)
        "result": None,
        "actual": None,
        "hit":    None,

        "model_version": model_version,

        # Audit trail — keep canonical_key + event_id so the updater
        # can re-link to the original score doc if needed.
        "canonical_key": d.get("canonical_key"),
        "event_id":      d.get("event_id"),
    }


async def log_selected_picks(
    db, score_docs: List[Dict[str, Any]], *, sport: str,
) -> Dict[str, int]:
    """Persist every score doc whose tier is in SELECTED_TIERS into
    `nba_pick_history`. Idempotent via the unique index — re-runs of
    the same slate update existing rows instead of duplicating.

    Returns `{inserted, updated, skipped, errors}` for caller logging.

    Only NBA props are logged (sport guard); the spec is NBA-scoped.
    Other sports pass through untouched.
    """
    if (sport or "").lower() != "nba" or not score_docs:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    fingerprint = _board_fingerprint(score_docs)
    model_version = _model_version()

    docs: List[Dict[str, Any]] = []
    for d in score_docs:
        pd = _build_pick_doc(d, fingerprint=fingerprint,
                             model_version=model_version)
        if pd is not None:
            docs.append(pd)

    if not docs:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    coll = db[COLLECTION_NAME]
    inserted = updated = errors = 0
    # Bulk upsert — idempotent. We $set everything except the outcome
    # fields (result/actual/hit) and timestamp, which are only set on
    # initial insert via $setOnInsert. That way re-running a slate
    # rescore does NOT clobber a result that was already graded.
    from pymongo import UpdateOne
    ops = []
    for pd in docs:
        outcome_only = {"result", "actual", "hit", "timestamp"}
        update_set = {k: v for k, v in pd.items() if k not in outcome_only}
        set_on_insert = {k: pd[k] for k in outcome_only if k in pd}
        ops.append(UpdateOne(
            filter={
                "player":    pd["player"],
                "stat":      pd["stat"],
                "line":      pd["line"],
                "game_date": pd["game_date"],
                "side":      pd["side"],
            },
            update={"$set": update_set, "$setOnInsert": set_on_insert},
            upsert=True,
        ))
    try:
        result = await coll.bulk_write(ops, ordered=False)
        inserted = (result.upserted_count or 0)
        updated  = (result.modified_count or 0)
    except Exception as e:
        errors = len(ops)
        logger.warning(f"[PICK_HISTORY] bulk_write failed: {e!r}")

    skipped = len(score_docs) - len(docs)
    logger.info(
        f"[PICK_HISTORY] sport={sport} inserted={inserted} updated={updated} "
        f"skipped={skipped} errors={errors} fingerprint={fingerprint} "
        f"model_version={model_version}"
    )
    return {"inserted": inserted, "updated": updated,
            "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------
def _roi_minus110(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 0.0
    return (wins * 100 - losses * 110) / (n * 110) * 100


def _bucket_edge(e: Optional[float]) -> str:
    if e is None:
        return "—"
    if e < 0:    return "<0%"
    if e < 5:    return "0–5%"
    if e < 10:   return "5–10%"
    if e < 15:   return "10–15%"
    return "15%+"


async def _aggregate(coll, group_id: Any, model_version: Optional[str] = None,
                     ) -> List[Dict[str, Any]]:
    """Reusable aggregation: groups by `group_id`, counts wins/losses on
    the subset where `hit` ∈ {True, False}."""
    match: Dict[str, Any] = {"hit": {"$in": [True, False]}}
    if model_version:
        match["model_version"] = model_version
    pipe = [
        {"$match": match},
        {"$group": {
            "_id":    group_id,
            "n":      {"$sum": 1},
            "wins":   {"$sum": {"$cond": [{"$eq": ["$hit", True]}, 1, 0]}},
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


async def query_overall(db, model_version: Optional[str] = None) -> Dict[str, Any]:
    rows = await _aggregate(db[COLLECTION_NAME], group_id=None,
                            model_version=model_version)
    return rows[0] if rows else {"n": 0, "wins": 0, "losses": 0,
                                  "win_rate": 0.0, "roi_110": 0.0}


async def query_by_stat(db, model_version: Optional[str] = None):
    return await _aggregate(db[COLLECTION_NAME], group_id="$stat",
                            model_version=model_version)


async def query_by_tier(db, model_version: Optional[str] = None):
    return await _aggregate(db[COLLECTION_NAME], group_id="$tier",
                            model_version=model_version)


async def query_by_availability(db, model_version: Optional[str] = None):
    return await _aggregate(db[COLLECTION_NAME],
                            group_id="$availability_status",
                            model_version=model_version)


async def query_by_side(db, model_version: Optional[str] = None):
    return await _aggregate(db[COLLECTION_NAME], group_id="$side",
                            model_version=model_version)


async def query_by_edge_bucket(db, model_version: Optional[str] = None):
    """Bucket on `edge`. Done client-side because edge is a continuous
    field and Mongo's $bucket would need duplicated boundary config —
    less readable than this 30-line python implementation."""
    coll = db[COLLECTION_NAME]
    match: Dict[str, Any] = {"hit": {"$in": [True, False]}}
    if model_version:
        match["model_version"] = model_version
    counts: Dict[str, Tuple[int, int]] = {}
    async for d in coll.find(match, {"edge": 1, "hit": 1, "_id": 0}):
        b = _bucket_edge(d.get("edge"))
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


__all__ = [
    "COLLECTION_NAME",
    "SELECTED_TIERS",
    "ensure_indexes",
    "log_selected_picks",
    "query_overall",
    "query_by_stat",
    "query_by_tier",
    "query_by_availability",
    "query_by_side",
    "query_by_edge_bucket",
]
