"""Forensic odds-audit endpoints (2026-05-17 hardening).

Two read-only endpoints for upstream-payload forensic work:

  GET /api/admin/odds/raw-snapshots
      Query the append-only `dg_raw_odds_snapshots` collection.
      Returns VERBATIM stored raw JSON — never normalised, never
      filtered. Use this to confirm what the odds-API physically
      sent at scrape time.

  GET /api/admin/odds/canonical-trace
      Field-by-field diff visibility across every stage of the
      pipeline for a single canonical_key (legacy form). Stages:
        raw_snapshots (newest 5)
        live_props (current row)
        prop_scores (newest)

Both endpoints are unauthenticated (project has no auth layer yet)
and read-only. DB is injected at startup via `set_db(db)` — matches
the pattern used by `routes/admin_diagnostics.py`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/odds", tags=["admin-odds-audit"])

_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _require_db():
    if _db is None:
        raise HTTPException(503, "odds-audit db not initialized")
    return _db


def _strip_oid(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the BSON `_id` so the response is JSON-serialisable."""
    doc.pop("_id", None)
    return doc


# ───────────────────────────────────────────────────────────────────
# 1. Raw snapshot query
# ───────────────────────────────────────────────────────────────────
@router.get("/raw-snapshots")
async def raw_snapshots(
    player: Optional[str] = Query(None, description="Player name (exact match)"),
    event_id: Optional[str] = Query(None),
    bookmaker: Optional[str] = Query(None),
    market_key: Optional[str] = Query(None),
    line: Optional[float] = Query(None),
    since: Optional[str] = Query(None, description="ISO timestamp lower bound (fetched_at >=)"),
    until: Optional[str] = Query(None, description="ISO timestamp upper bound (fetched_at <=)"),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    """Query the append-only `dg_raw_odds_snapshots` collection.

    All filters AND together. Empty filters return the newest `limit`
    rows globally. The response carries the verbatim
    `raw_outcome_json` and `raw_market_json` fragments so callers can
    inspect the exact upstream payload.
    """
    db = _require_db()
    q: Dict[str, Any] = {}
    if player:
        q["outcome_description"] = player
    if event_id:
        q["event_id"] = event_id
    if bookmaker:
        q["bookmaker"] = bookmaker
    if market_key:
        q["market_key"] = market_key
    if line is not None:
        q["outcome_point"] = float(line)
    ts_filter: Dict[str, Any] = {}
    if since:
        ts_filter["$gte"] = since
    if until:
        ts_filter["$lte"] = until
    if ts_filter:
        q["fetched_at"] = ts_filter

    cursor = db["dg_raw_odds_snapshots"].find(q).sort("fetched_at", -1).limit(limit)
    rows: List[Dict[str, Any]] = []
    async for d in cursor:
        rows.append(_strip_oid(d))
    return {
        "query": q,
        "count": len(rows),
        "limit": limit,
        "snapshots": rows,
    }


# ───────────────────────────────────────────────────────────────────
# 2. Canonical trace — full lifecycle in one response
# ───────────────────────────────────────────────────────────────────
@router.get("/canonical-trace")
async def canonical_trace(
    canonical_key: Optional[str] = Query(
        None, description="Legacy canonical_key (sport|event|player|stat|line|side)"),
    canonical_key_v2: Optional[str] = Query(
        None, description="Augmented v2 key (legacy + |market_class)"),
    sport: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    player: Optional[str] = Query(None),
    stat_type: Optional[str] = Query(None),
    line: Optional[float] = Query(None),
    side: Optional[str] = Query(None),
    snapshot_limit: int = Query(5, ge=1, le=50,
        description="How many raw-snapshot rows to attach"),
) -> Dict[str, Any]:
    """Return the complete cross-stage trace for one canonical prop.

    Either pass a `canonical_key` directly OR specify the six tuple
    components and we'll reconstruct it.

    The response is intentionally a flat dict — one section per
    pipeline stage — so callers can diff fields side-by-side without
    having to follow joins.
    """
    db = _require_db()

    # ── Resolve the canonical_key ───────────────────────────────
    if not canonical_key and canonical_key_v2:
        # Strip the trailing `|<market_class>` segment.
        parts = canonical_key_v2.split("|")
        if len(parts) >= 7:
            canonical_key = "|".join(parts[:6])
    if not canonical_key:
        if not all([sport, event_id, player, stat_type, line is not None, side]):
            raise HTTPException(
                400, "Provide either `canonical_key` / `canonical_key_v2` OR "
                "all of (sport, event_id, player, stat_type, line, side)")
        canonical_key = (
            f"{sport}|{event_id}|{player}|{stat_type}|{float(line)}|"
            f"{(side or '').upper()}"
        )

    # ── Stage A: raw snapshots that match the canonical_candidate
    snaps: List[Dict[str, Any]] = []
    async for d in db["dg_raw_odds_snapshots"].find(
        {"canonical_candidate": canonical_key}
    ).sort("fetched_at", -1).limit(snapshot_limit):
        snaps.append(_strip_oid(d))

    # ── Stage B: latest-state cache row
    raw_latest = await db["dg_raw_odds_markets"].find_one(
        {}, sort=[("fetched_at", -1)])  # touch to keep the collection
    # (no per-canonical join — this collection isn't keyed by
    # canonical_key. We expose it for visibility only.)

    # ── Stage C: live props row
    live = await db["mlb_live_props"].find_one(
        {"canonical_key": canonical_key})

    # ── Stage D: score doc (most recent)
    score = await db["mlb_prop_scores"].find_one(
        {"canonical_key": canonical_key}, sort=[("computed_at", -1)])

    return {
        "canonical_key": canonical_key,
        "raw_snapshots": snaps,
        "raw_snapshots_count": len(snaps),
        "raw_markets_latest_seen": (
            _strip_oid(raw_latest) if raw_latest else None),
        "live_props": _strip_oid(live) if live else None,
        "prop_score": _strip_oid(score) if score else None,
        "field_diff": _build_field_diff(snaps, live, score),
    }


def _build_field_diff(snaps, live, score) -> Dict[str, Any]:
    """Per-field comparison across stages for the most-audited fields.

    Returns a dict whose keys are the field names and whose values are
    a per-stage dict. Missing-stage values come back as ``None``.
    """
    diff: Dict[str, Dict[str, Any]] = {}
    fields = (
        "line", "recommendation", "side", "direction",
        "market_class", "source_market_key", "is_alternate_market",
        "canonical_key", "canonical_key_v2",
    )

    snap = snaps[0] if snaps else None
    for f in fields:
        diff[f] = {
            "raw_snapshot[0]": (snap or {}).get(f) if snap else None,
            "live_props": (live or {}).get(f) if live else None,
            "score_doc": (score or {}).get(f) if score else None,
        }
    # Special: outcome.point in raw → line in live/score
    if snap:
        diff["line"]["raw_snapshot[0]"] = snap.get("outcome_point")
        diff["recommendation"]["raw_snapshot[0]"] = (
            "OVER" if (snap.get("outcome_name") or "").lower() == "over"
            else "UNDER" if (snap.get("outcome_name") or "").lower() == "under"
            else None
        )
    return diff


# ───────────────────────────────────────────────────────────────────
# 3. Pre-scoring book-quote integrity filter — last-24h stats
# ───────────────────────────────────────────────────────────────────
@router.get("/integrity-filter-stats")
async def integrity_filter_stats(
    sport: str = Query("mlb", description="Sport scope (filter is MLB-only today)"),
    hours: int = Query(24, ge=1, le=168,
                       description="Lookback window in hours (default 24)"),
    top_n: int = Query(25, ge=1, le=200,
                       description="How many quote examples to surface"),
) -> Dict[str, Any]:
    """Read-only stats for the pre-scoring book-quote integrity filter.

    All numbers are computed for the last ``hours`` hours from two
    persisted-state sources — no scoring / filter / gate behaviour is
    invoked. Sources:

      * ``{sport}_prop_scores`` rows with ``integrity_filter_applied=True``
        carry the verbatim ``excluded_book_quotes`` payload (one entry
        per ejected book). These produce: total excluded quotes,
        breakdown by sportsbook / stat-family / market_class, top-N
        examples, affected prop count.
      * ``{sport}_live_props`` rows eligible for the rule
        (``sport==mlb`` AND ``market_class=='alternate'`` AND
        ``line==0.5``) where EVERY entry in ``all_odds_alternate`` is
        ≥ ``+500`` (American). These would have been fully rejected by
        ``apply_to_prop_list`` and dropped before the score-doc writer
        ever saw them — i.e. ``dropped_props_count``.
    """
    db = _require_db()
    from datetime import datetime, timedelta, timezone
    from services.scoring.book_quote_integrity_filter import (
        _ABSURD_ODDS_THRESHOLD as _THRESHOLD,
        _FILTER_RULE as _RULE,
    )

    now = datetime.now(timezone.utc)
    cutoff_dt = now - timedelta(hours=hours)
    # ``computed_at`` on score docs is an ISO string (see
    # services/scoring/prop_scores_store._project_score_doc). Compare
    # lexicographically — UTC ISO-8601 strings sort the same as their
    # underlying instants for matching precision.
    cutoff_iso = cutoff_dt.isoformat()

    scores_coll = db[f"{sport}_prop_scores"]
    live_coll = db[f"{sport}_live_props"]

    # ── Stage A: affected props + excluded-quote rollups ────────────
    by_book: Dict[str, int] = {}
    by_stat_family: Dict[str, int] = {}
    by_market_class: Dict[str, int] = {}
    total_excluded_quotes = 0
    affected_props_count = 0
    top_examples: List[Dict[str, Any]] = []

    cursor = scores_coll.find(
        {
            "integrity_filter_applied": True,
            "computed_at": {"$gte": cutoff_iso},
        },
        {
            "_id": 0,
            "canonical_key": 1,
            "sport": 1,
            "player_name": 1,
            "stat_type": 1,
            "line": 1,
            "market_class": 1,
            "computed_at": 1,
            "event_id": 1,
            "excluded_book_quotes": 1,
        },
    )
    async for doc in cursor:
        affected_props_count += 1
        ex_list = doc.get("excluded_book_quotes") or []
        if not isinstance(ex_list, list):
            continue
        stat = doc.get("stat_type") or "unknown"
        for q in ex_list:
            if not isinstance(q, dict):
                continue
            total_excluded_quotes += 1
            book = q.get("book") or "unknown"
            mc = q.get("market_class") or "unknown"
            by_book[book] = by_book.get(book, 0) + 1
            by_stat_family[stat] = by_stat_family.get(stat, 0) + 1
            by_market_class[mc] = by_market_class.get(mc, 0) + 1
            if len(top_examples) < top_n:
                top_examples.append({
                    "canonical_key": doc.get("canonical_key"),
                    "sport": doc.get("sport"),
                    "event_id": doc.get("event_id"),
                    "player_name": doc.get("player_name"),
                    "stat_type": stat,
                    "line": doc.get("line"),
                    "market_class": mc,
                    "book": book,
                    "odds": q.get("odds"),
                    "reason": q.get("reason"),
                    "computed_at": doc.get("computed_at"),
                })

    # ── Stage B: would-be-dropped props (every alt quote ≥ threshold)
    # Scan live_props for the eligible set; count those where every
    # entry in ``all_odds_alternate`` is ≥ the threshold. Rejected
    # props are never written to ``{sport}_prop_scores``, so the live
    # collection is the only persisted surface that retains them.
    dropped_props_count = 0
    dropped_examples: List[Dict[str, Any]] = []
    live_cursor = live_coll.find(
        {
            "sport": sport,
            "market_class": "alternate",
            "line": 0.5,
            "all_odds_alternate": {"$exists": True, "$ne": None},
            "fetched_at": {"$gte": cutoff_dt},
        },
        {
            "_id": 0,
            "canonical_key": 1,
            "player_name": 1,
            "stat_type": 1,
            "line": 1,
            "all_odds_alternate": 1,
            "fetched_at": 1,
            "event_id": 1,
        },
    )
    async for ld in live_cursor:
        alt = ld.get("all_odds_alternate") or {}
        if not isinstance(alt, dict) or not alt:
            continue
        try:
            all_bad = all(int(v) >= _THRESHOLD for v in alt.values())
        except (TypeError, ValueError):
            continue
        if all_bad:
            dropped_props_count += 1
            if len(dropped_examples) < top_n:
                dropped_examples.append({
                    "canonical_key": ld.get("canonical_key"),
                    "player_name": ld.get("player_name"),
                    "stat_type": ld.get("stat_type"),
                    "line": ld.get("line"),
                    "event_id": ld.get("event_id"),
                    "alt_book_count": len(alt),
                    "alt_odds": alt,
                    "fetched_at": (
                        ld["fetched_at"].isoformat()
                        if hasattr(ld.get("fetched_at"), "isoformat")
                        else ld.get("fetched_at")
                    ),
                })

    def _sort_desc(d: Dict[str, int]) -> Dict[str, int]:
        return dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True))

    return {
        "sport": sport,
        "window_hours": hours,
        "window_start": cutoff_iso,
        "window_end": now.isoformat(),
        "rule": _RULE,
        "threshold_american_odds": _THRESHOLD,
        "total_excluded_quotes": total_excluded_quotes,
        "affected_props_count": affected_props_count,
        "dropped_props_count": dropped_props_count,
        "excluded_quotes_by_sportsbook": _sort_desc(by_book),
        "excluded_quotes_by_stat_family": _sort_desc(by_stat_family),
        "excluded_quotes_by_market_class": _sort_desc(by_market_class),
        "top_excluded_quote_examples": top_examples,
        "dropped_prop_examples": dropped_examples,
        "notes": {
            "affected_props_source": (
                f"{sport}_prop_scores where integrity_filter_applied=True "
                f"AND computed_at >= window_start"
            ),
            "dropped_props_source": (
                f"{sport}_live_props rows matching the rule's eligibility "
                f"(sport=mlb, market_class=alternate, line==0.5) where "
                f"EVERY entry in all_odds_alternate is >= "
                f"+{_THRESHOLD}. Rejected props are never written to "
                f"{sport}_prop_scores; live_props is the only persisted "
                f"surface that retains them."
            ),
        },
    }
