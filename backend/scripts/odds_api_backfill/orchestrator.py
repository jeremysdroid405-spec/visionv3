"""
Slate-by-slate orchestrator.

For each (slate_date, snapshot_label) pair:
    1. Resolve the snapshot timestamp (ISO8601 UTC).
    2. List events at that snapshot.
    3. For each event, GET /historical/.../events/{id}/odds with ALL
       target markets in a single request.
    4. Flatten to row docs and bulk-upsert into `historical_odds_full`
       on the unique compound index.

Idempotent — re-running the same slate refreshes data without
duplicating.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import UpdateOne

from .client import CreditBudgetExceeded, OddsAPIClient
from .schema import COLLECTION_NAME, ensure_indexes
from .sport_markets import (
    DEFAULT_SPORT, is_alternate, is_combo, market_to_family, markets_for,
)

logger = logging.getLogger(__name__)

# Snapshot wall-clock plan (UTC). Matches the user's spec:
#   "morning/open" : 14:00 UTC  (before NBA / mid-day before MLB first-pitch)
#   "1h before"    : 23:00 UTC  (most NBA slates tip at midnight UTC;
#                                MLB first-pitches cluster near 23:00 UTC too)
#   "10m before"   : 23:50 UTC
SNAPSHOT_PLAN: List[Tuple[str, int, int]] = [
    ("open",         14,  0),
    ("pregame_-1h",  23,  0),
    ("pregame_-10m", 23, 50),
]

DEFAULT_REGIONS = ["us"]


def _iso(dt: datetime) -> str:
    # The Odds API historical endpoint REJECTS '+00:00' timezone suffix and
    # only accepts the 'Z' shortcut (error code INVALID_HISTORICAL_TIMESTAMP).
    return (dt.replace(microsecond=0)
              .astimezone(timezone.utc)
              .isoformat()
              .replace("+00:00", "Z"))


def _date_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _flatten_event_odds(
    *, sport_key: str, event_meta: Dict[str, Any], odds_payload: Dict[str, Any],
    snapshot_time: datetime, snapshot_label: str, region: str,
) -> List[Dict[str, Any]]:
    """Fan out one event's odds payload into one row per
    (bookmaker, market, player, line, side)."""
    rows: List[Dict[str, Any]] = []
    bookmakers = (odds_payload or {}).get("bookmakers") or []
    commence = event_meta.get("commence_time")
    try:
        commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")) \
                       if commence else None
    except (ValueError, AttributeError):
        commence_dt = None
    game_date = _date_str(commence_dt) if commence_dt else None

    base = {
        "sport_key":     sport_key,
        "event_id":      event_meta.get("id"),
        "commence_time": commence_dt,
        "game_date":     game_date,
        "home_team":     event_meta.get("home_team"),
        "away_team":     event_meta.get("away_team"),
        "snapshot_time": snapshot_time,
        "snapshot_label": snapshot_label,
        "region":        region,
        "source":        "odds_api_v4_historical",
        "ingested_at":   datetime.now(timezone.utc),
    }

    for bm in bookmakers:
        bm_key = (bm.get("key") or "").strip().lower()
        if not bm_key: continue
        for market in (bm.get("markets") or []):
            mkey = market.get("key")
            if not mkey: continue
            for outcome in (market.get("outcomes") or []):
                # Player markets: `description` = player name; `name` = OVER/UNDER.
                player = (outcome.get("description")
                           or outcome.get("participant")
                           or "").strip().lower()
                side = (outcome.get("name") or "").strip().upper()
                line = outcome.get("point")
                price = outcome.get("price")
                if not player or side not in ("OVER", "UNDER", "YES", "NO"):
                    continue
                if line is None or price is None:
                    continue
                rows.append({
                    **base,
                    "bookmaker":    bm_key,
                    "market_key":   mkey,
                    "is_alternate": is_alternate(mkey),
                    "is_combo":     is_combo(sport_key, mkey),
                    "stat_family":  market_to_family(sport_key, mkey),
                    "player":       player,
                    "line":         float(line),
                    "side":         side,
                    "odds_american": int(price),
                })
    return rows


async def _bulk_upsert(db, rows: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    rows = list(rows)
    if not rows:
        return (0, 0)
    ops = []
    for r in rows:
        flt = {
            "sport_key":     r["sport_key"],
            "event_id":      r["event_id"],
            "market_key":    r["market_key"],
            "snapshot_time": r["snapshot_time"],
            "bookmaker":     r["bookmaker"],
            "player":        r["player"],
            "line":          r["line"],
            "side":          r["side"],
        }
        ops.append(UpdateOne(flt, {"$set": r,
                                    "$setOnInsert": {"_first_seen":
                                                       r["ingested_at"]}},
                              upsert=True))
    res = await db[COLLECTION_NAME].bulk_write(ops, ordered=False)
    return (res.upserted_count or 0, res.modified_count or 0)


async def run_slate(
    db, *, client: OddsAPIClient, slate_date: datetime,
    sport_key: str = DEFAULT_SPORT,
    snapshot_plan: List[Tuple[str, int, int]] = SNAPSHOT_PLAN,
    markets: Optional[List[str]] = None,
    regions: List[str] = DEFAULT_REGIONS,
) -> Dict[str, Any]:
    """Backfill ONE slate (UTC date) for ONE sport. Returns counters + cost."""
    if markets is None:
        markets = markets_for(sport_key)
    summary = {
        "sport_key": sport_key,
        "slate_date": _date_str(slate_date),
        "snapshots": [],
        "rows_inserted": 0,
        "rows_modified": 0,
        "events_seen": 0,
        "credits_used_local": 0,
    }
    base = slate_date.replace(hour=0, minute=0, second=0,
                                microsecond=0, tzinfo=timezone.utc)
    for label, hh, mm in snapshot_plan:
        snap_dt = base.replace(hour=hh, minute=mm)
        snap_iso = _iso(snap_dt)
        try:
            events = await client.list_historical_events(
                sport=sport_key, snapshot_iso=snap_iso,
            )
        except CreditBudgetExceeded:
            raise
        except Exception as exc:
            logger.error(
                f"[orchestrator] list_events failed sport={sport_key} "
                f"slate={summary['slate_date']} label={label}: {exc!r}")
            summary["snapshots"].append({"label": label, "events": 0,
                                          "error": str(exc)})
            continue

        # Filter to events whose commence_time is on the target date.
        target_yyyymmdd = _date_str(slate_date)
        events = [e for e in events
                   if (e.get("commence_time") or "")[:10] == target_yyyymmdd]
        summary["events_seen"] = max(summary["events_seen"], len(events))

        snap_rows = 0; snap_inserted = 0; snap_modified = 0
        for ev in events:
            ev_id = ev.get("id")
            if not ev_id: continue
            try:
                payload = await client.get_historical_event_odds(
                    sport=sport_key, event_id=ev_id,
                    markets=markets, regions=regions,
                    snapshot_iso=snap_iso,
                )
                # Cost: markets × regions × 10 per event
                summary["credits_used_local"] += len(markets) * len(regions) * 10
            except CreditBudgetExceeded:
                raise
            except Exception as exc:
                logger.error(
                    f"[orchestrator] event_odds failed sport={sport_key} "
                    f"event={ev_id}: {exc!r}")
                continue

            rows = _flatten_event_odds(
                sport_key=sport_key,
                event_meta={**ev, "id": ev_id},
                odds_payload=payload,
                snapshot_time=snap_dt, snapshot_label=label,
                region=regions[0],
            )
            inserted, modified = await _bulk_upsert(db, rows)
            snap_rows += len(rows)
            snap_inserted += inserted
            snap_modified += modified

        summary["rows_inserted"] += snap_inserted
        summary["rows_modified"] += snap_modified
        summary["snapshots"].append({
            "label": label, "snap_iso": snap_iso, "events": len(events),
            "rows": snap_rows, "inserted": snap_inserted,
            "modified": snap_modified,
        })
        logger.info(
            f"[orchestrator] {sport_key} {summary['slate_date']} {label}: events="
            f"{len(events)} rows={snap_rows} ins={snap_inserted} "
            f"mod={snap_modified}")

    return summary


async def run_backfill(
    db, *, num_days: int = 30, end_date: Optional[datetime] = None,
    sport_key: str = DEFAULT_SPORT,
    snapshot_plan: List[Tuple[str, int, int]] = SNAPSHOT_PLAN,
    markets: Optional[List[str]] = None,
    regions: List[str] = DEFAULT_REGIONS,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Iterate `num_days` slates ending on `end_date` (UTC, default
    today-1) for a single sport."""
    await ensure_indexes(db)
    if end_date is None:
        end_date = datetime.now(timezone.utc) - timedelta(days=1)
    if markets is None:
        markets = markets_for(sport_key)
    summary = {
        "sport_key": sport_key,
        "num_days": num_days, "snapshot_plan": [s[0] for s in snapshot_plan],
        "markets": markets, "regions": regions,
        "slates": [], "credits_used_local": 0,
        "rows_inserted_total": 0, "rows_modified_total": 0,
    }
    async with OddsAPIClient(api_key=api_key) as client:
        for i in range(num_days):
            slate = end_date - timedelta(days=num_days - 1 - i)
            try:
                s = await run_slate(
                    db, client=client, slate_date=slate,
                    sport_key=sport_key,
                    snapshot_plan=snapshot_plan,
                    markets=markets, regions=regions,
                )
            except CreditBudgetExceeded as exc:
                logger.error(f"[backfill] HALT — {exc}")
                summary["halted_reason"] = str(exc)
                break
            summary["slates"].append(s)
            summary["credits_used_local"] += s["credits_used_local"]
            summary["rows_inserted_total"] += s["rows_inserted"]
            summary["rows_modified_total"] += s["rows_modified"]
        summary["api_stats"] = client.stats
    return summary


__all__ = ["run_backfill", "run_slate", "SNAPSHOT_PLAN"]
