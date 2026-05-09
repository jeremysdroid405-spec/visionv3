"""
Replay ingest helper — fetch + persist for one (event, snapshot_label) pair.

Splits the bundled-markets API response into one snapshot doc per
(event_id, market_key, snapshot_label) so the unique index in
`replay_odds_snapshots` is enforceable, while still using a single
costly API call to fetch all markets at once.

Side-effects: writes to MongoDB collections `replay_odds_snapshots`
and `replay_props_normalized`. NOTHING ELSE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

from .canary_events import CanaryEvent
from .markets import (
    REPLAY_BOOK_WHITELIST_PHASE1,
    REPLAY_NBA_MARKETS,
    REPLAY_REGIONS_PHASE1,
)
from .normalizer import (
    NORMALIZER_VERSION,
    normalize_envelope,
    stable_payload_hash,
)
from .odds_fetch import (
    SnapshotNotAvailable,
    fetch_historical_event_odds_envelope,
)
from .snapshot_plan import REPLAY_WINDOWS, snapshot_for

logger = logging.getLogger(__name__)


REPLAY_ODDS_SNAPSHOTS = "replay_odds_snapshots"
REPLAY_PROPS_NORMALIZED = "replay_props_normalized"


def _split_envelope_per_market(
    envelope: Dict[str, Any],
    *,
    keep_market_keys: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Return {market_key: per-market sub-envelope}.

    Each sub-envelope retains the top-level timestamps but its
    `data.bookmakers[].markets[]` list is filtered to that one market_key.
    Books that don't carry the market are dropped from that sub-envelope.
    """
    out: Dict[str, Dict[str, Any]] = {m: None for m in keep_market_keys}
    data = envelope.get("data") or {}
    bookmakers = data.get("bookmakers") or []

    for mkt in keep_market_keys:
        sub_books: List[Dict[str, Any]] = []
        for b in bookmakers:
            sub_markets = [m for m in (b.get("markets") or [])
                            if m.get("key") == mkt]
            if not sub_markets:
                continue
            sub_books.append({**b, "markets": sub_markets})
        if not sub_books:
            out[mkt] = None
            continue
        out[mkt] = {
            "timestamp":          envelope.get("timestamp"),
            "previous_timestamp": envelope.get("previous_timestamp"),
            "next_timestamp":     envelope.get("next_timestamp"),
            "data": {
                **{k: v for k, v in data.items() if k != "bookmakers"},
                "bookmakers": sub_books,
            },
        }
    return out


async def _upsert_snapshot_doc(db, doc: Dict[str, Any]) -> Tuple[int, int]:
    """Returns (inserted, modified)."""
    flt = {
        "event_id":       doc["event_id"],
        "market_key":     doc["market_key"],
        "snapshot_label": doc["snapshot_label"],
    }
    res = await db[REPLAY_ODDS_SNAPSHOTS].update_one(
        flt,
        {"$set": doc,
         "$setOnInsert": {"_first_seen": doc["ingested_at"]}},
        upsert=True,
    )
    inserted = 1 if res.upserted_id is not None else 0
    return inserted, res.modified_count or 0


async def _bulk_upsert_normalized(db, rows: List[Dict[str, Any]]
                                   ) -> Tuple[int, int]:
    """Chunked bulk-upsert. ~500 ops per chunk to ease memory pressure on
    the local mongod (the unique compound index + 3 secondary indexes
    means every doc triggers 4 index-maintenance ops)."""
    if not rows:
        return 0, 0
    CHUNK = 500
    total_ins = 0
    total_mod = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        ops: List[UpdateOne] = []
        for r in chunk:
            flt = {
                "event_id":       r["event_id"],
                "snapshot_label": r["snapshot_label"],
                "bookmaker":      r["bookmaker"],
                "market_key":     r["market_key"],
                "player":         r["player"],
                "line":           r["line"],
                "side":           r["side"],
            }
            ops.append(UpdateOne(
                flt,
                {"$set": r,
                 "$setOnInsert": {"_first_seen": r["normalized_at"]}},
                upsert=True,
            ))
        res = await db[REPLAY_PROPS_NORMALIZED].bulk_write(
            ops, ordered=False)
        total_ins += res.upserted_count or 0
        total_mod += res.modified_count or 0
    return total_ins, total_mod


async def ingest_event_window(
    db,
    *,
    client,
    sport_key: str,
    event: CanaryEvent,
    window_label: str,
    markets: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    book_whitelist: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch + persist for one (event, snapshot_label) pair.

    Returns a stats dict: api_credits, snapshot_inserts/mods,
    normalized_inserts/mods, market_counts, book_counts.
    """
    if markets is None:
        markets = REPLAY_NBA_MARKETS
    if regions is None:
        regions = REPLAY_REGIONS_PHASE1
    if book_whitelist is None:
        book_whitelist = REPLAY_BOOK_WHITELIST_PHASE1

    snap_dt = snapshot_for(event["commence_time"], window_label)
    snap_iso = snap_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    pre_credits = client.stats.get("credits_used_session", 0)
    try:
        envelope = await fetch_historical_event_odds_envelope(
            client,
            sport=sport_key,
            event_id=event["event_id"],
            markets=markets,
            regions=regions,
            snapshot_iso=snap_iso,
        )
    except SnapshotNotAvailable:
        post_credits = client.stats.get("credits_used_session", 0)
        return {
            "event_id":               event["event_id"],
            "window_label":           window_label,
            "requested_ts":           snap_iso,
            "returned_ts":            None,
            "api_credits":            post_credits - pre_credits,
            "snapshot_not_available": True,
            "markets_with_data":      [],
            "markets_empty":          list(markets),
            "snapshot_inserted":      0,
            "snapshot_modified":      0,
            "normalized_inserted":    0,
            "normalized_modified":    0,
            "market_counts":          {},
            "book_counts":            {},
            "normalizer_version":     NORMALIZER_VERSION,
        }
    post_credits = client.stats.get("credits_used_session", 0)
    api_credits = post_credits - pre_credits

    # 1) Fan out to per-market snapshot docs.
    per_market = _split_envelope_per_market(
        envelope, keep_market_keys=markets,
    )
    snap_inserted = snap_modified = 0
    markets_with_data: List[str] = []
    for mkt, sub in per_market.items():
        if sub is None:
            continue  # market not carried by any book at this snapshot
        markets_with_data.append(mkt)
        doc = {
            "sport_key":           sport_key,
            "event_id":            event["event_id"],
            "home_team":           event["home_team"],
            "away_team":           event["away_team"],
            "commence_time":       event["commence_time"],
            "market_key":          mkt,
            "snapshot_label":      window_label,
            "requested_ts":        snap_dt,
            "returned_ts":         sub.get("timestamp"),
            "previous_ts":         sub.get("previous_timestamp"),
            "next_ts":             sub.get("next_timestamp"),
            "region":              regions[0],
            "bookmakers":          sub["data"]["bookmakers"],
            "payload_hash":        stable_payload_hash(sub),
            "credits_charged_total_for_call": api_credits,  # whole-call cost
            "ingested_at":         datetime.now(timezone.utc),
            "source":              "odds_api_v4_historical",
        }
        ins, mod = await _upsert_snapshot_doc(db, doc)
        snap_inserted += ins
        snap_modified += mod

    # 2) Normalize the WHOLE envelope (single pass, keeps consistency
    #    with future re-flatten runs that re-read the raw envelope).
    norm = normalize_envelope(
        envelope,
        sport_key=sport_key,
        event_id=event["event_id"],
        home_team=event["home_team"],
        away_team=event["away_team"],
        commence_time=event["commence_time"],
        snapshot_label=window_label,
        requested_ts=snap_dt,
        region=regions[0],
        book_whitelist=book_whitelist,
    )
    norm_ins, norm_mod = await _bulk_upsert_normalized(db, norm["flat_rows"])

    return {
        "event_id":               event["event_id"],
        "window_label":           window_label,
        "requested_ts":           snap_iso,
        "returned_ts":            envelope.get("timestamp"),
        "api_credits":            api_credits,
        "markets_with_data":      markets_with_data,
        "markets_empty":          [m for m in markets
                                   if m not in markets_with_data],
        "snapshot_inserted":      snap_inserted,
        "snapshot_modified":      snap_modified,
        "normalized_inserted":    norm_ins,
        "normalized_modified":    norm_mod,
        "market_counts":          norm["market_counts"],
        "book_counts":            norm["book_counts"],
        "normalizer_version":     NORMALIZER_VERSION,
    }


__all__ = [
    "REPLAY_ODDS_SNAPSHOTS",
    "REPLAY_PROPS_NORMALIZED",
    "ingest_event_window",
]


def run_canary_window_grid(events, window_labels):
    """Helper to enumerate (event, window) pairs in canonical order."""
    for ev in events:
        for w in window_labels:
            yield ev, w
