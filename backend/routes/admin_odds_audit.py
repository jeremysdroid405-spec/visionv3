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
