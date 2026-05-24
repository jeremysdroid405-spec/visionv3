"""
MLB Historical Alternate-Odds Ingest — Layer 1 of the replay system.
====================================================================

Pulls historical MLB props (standard + alternate lines) from The Odds API
and writes them as flat rows keyed by
  (sport, game_date, event_id, player_name_normalized, market, line, side, book).

Architecture
------------
20-date chunk → date → market → API fetch → normalize → bulk upsert (500-row
batches) → release. NO multi-day buffering. NO season-wide arrays.

Memory-safety
-------------
Every (date, market) loop iteration logs psutil RSS; if RSS exceeds
`mem_limit_mb` we flush + checkpoint + exit gracefully with status =
"memory_halt". The CLI re-entry will resume from the same `(chunk, date,
market)` triple because each completion is recorded as it happens.

Resumability
------------
`mlb_historical_alt_odds_ingest_status` rows track every
`(chunk_start, chunk_end, date, market)` triple with one of:
  pending | in_progress | completed | failed | memory_halt
Re-runs skip `completed` triples unless force=True.

DOES NOT call the model, run gates, or grade outcomes — that is Layers 2/3/4.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil
from pymongo import ASCENDING, UpdateOne

from scripts.odds_api_backfill.client import (
    CreditBudgetExceeded, OddsAPIClient,
)
from services.replay.odds_fetch import (
    SnapshotNotAvailable, fetch_historical_event_odds_envelope,
)

logger = logging.getLogger(__name__)

RAW_COLL = "mlb_historical_alt_odds_raw"
STATUS_COLL = "mlb_historical_alt_odds_ingest_status"


# ── Market catalog ──────────────────────────────────────────────────────
# Standard + alternate variants. The Odds API supports `<market>_alternate`
# for every player-prop market — they return broader line ladders.
_BASE_MARKETS = [
    "batter_hits",
    "batter_total_bases",
    "batter_runs_scored",
    "batter_rbis",
    "batter_hits_runs_rbis",
    "batter_strikeouts",
    "batter_singles",        # 2026-05-24 — model has it, was missing
    "batter_walks",          # 2026-05-24 — model has it (`walks`), was missing
    "batter_home_runs",      # 2026-05-24 — model has it, was missing
    "batter_stolen_bases",   # 2026-05-24 — model has it, was missing
    "batter_doubles",        # 2026-05-24 — model has it, was missing
    "pitcher_strikeouts",
    "pitcher_hits_allowed",
    "pitcher_walks",
    "pitcher_earned_runs",
    "pitcher_outs",
]
DEFAULT_MLB_MARKETS: List[str] = (
    _BASE_MARKETS + [f"{m}_alternate" for m in _BASE_MARKETS]
)

# Regions cover all books the user listed. The Odds API splits the US
# market across `us` (DK/FD/MGM/Caesars) and `us2` (ESPNBet/HardRock/
# BetRivers/BetParx/BallyBet/Fliff/BetOnline).
DEFAULT_REGIONS: List[str] = ["us", "us2"]

# Snapshot timestamp policy: 11:00 UTC = ~07:00 EDT, before any MLB game
# starts in the US. Captures the opening-line book quotes.
SNAPSHOT_HOUR_UTC = 11

# OOM guard. The pod has chronic ~16GB peak; we set a soft floor below
# that so the script halts long before the OS kills it.
DEFAULT_MEM_LIMIT_MB = 1_500


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ── Index ensure ────────────────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    """Idempotent. Called once per ingest run."""
    await db[RAW_COLL].create_index(
        [("sport", ASCENDING), ("game_date", ASCENDING),
         ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING), ("line", ASCENDING),
         ("side", ASCENDING), ("book", ASCENDING),
         ("commence_time", ASCENDING),
         ("snapshot_iso", ASCENDING)],
        name="alt_odds_compound_unique_v2", unique=True,
    )
    await db[RAW_COLL].create_index("game_date")
    await db[RAW_COLL].create_index("market")
    await db[RAW_COLL].create_index("player_name_normalized")
    await db[RAW_COLL].create_index("line")
    await db[RAW_COLL].create_index("side")
    await db[RAW_COLL].create_index("book")
    await db[RAW_COLL].create_index("event_id")

    await db[STATUS_COLL].create_index(
        [("chunk_start_date", ASCENDING),
         ("chunk_end_date", ASCENDING),
         ("current_date", ASCENDING),
         ("current_market", ASCENDING),
         ("snapshot_iso", ASCENDING)],
        name="status_compound_unique", unique=True,
    )
    await db[STATUS_COLL].create_index("snapshot_hour")
    await db[STATUS_COLL].create_index("status")
    await db[STATUS_COLL].create_index("completed_at")


# ── Name normalisation ─────────────────────────────────────────────────
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def normalize_player_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s)
    return s


# ── Normalization: envelope → flat rows ────────────────────────────────
@dataclass(frozen=True)
class _Outcome:
    book: str
    line: Optional[float]
    side: str
    odds: int
    player: str


def _normalize_event(
    env: Dict[str, Any],
    *,
    market: str,
    game_date: str,
    snapshot_iso: str,
) -> List[Dict[str, Any]]:
    """Flatten one (event, market) envelope into per-(book, side, line,
    player) rows. Ignores books that don't carry the market."""
    rows: List[Dict[str, Any]] = []
    event_id = env.get("id")
    commence_time = env.get("commence_time")
    sport_key = env.get("sport_key", "baseball_mlb")
    home_team = env.get("home_team")
    away_team = env.get("away_team")

    for bm in (env.get("bookmakers") or []):
        book = (bm.get("key") or "").lower().strip()
        if not book:
            continue
        last_update = bm.get("last_update")
        for mk in (bm.get("markets") or []):
            if (mk.get("key") or "") != market:
                continue
            for out in (mk.get("outcomes") or []):
                # The Odds API player-prop schema:
                #   outcomes[*].description = player name
                #   outcomes[*].name        = "Over" | "Under"
                #   outcomes[*].point       = line
                #   outcomes[*].price       = american odds
                player = out.get("description") or out.get("participant")
                side_raw = (out.get("name") or "").lower()
                if side_raw not in ("over", "under"):
                    continue
                side = "OVER" if side_raw == "over" else "UNDER"
                try:
                    line = float(out["point"]) if "point" in out else None
                except (TypeError, ValueError):
                    line = None
                try:
                    odds = int(out["price"])
                except (TypeError, ValueError, KeyError):
                    continue
                if not player:
                    continue
                rows.append({
                    "sport": "mlb",
                    "sport_key": sport_key,
                    "game_date": game_date,
                    "event_id": event_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": commence_time,
                    "market": market,
                    "stat": market,  # alias for replay-layer consumers
                    "is_alternate": market.endswith("_alternate"),
                    "player_name": player,
                    "player_name_normalized": normalize_player_name(player),
                    "line": line,
                    "side": side,
                    "book": book,
                    "odds": odds,
                    "book_last_update": last_update,
                    "snapshot_iso": snapshot_iso,
                    "ingested_at": datetime.now(timezone.utc),
                })
    return rows


# ── Bulk upsert ────────────────────────────────────────────────────────
_UPSERT_KEY_FIELDS = (
    "sport", "game_date", "event_id", "player_name_normalized",
    "market", "line", "side", "book", "commence_time", "snapshot_iso",
)


async def _flush_rows(
    db, buffer: List[Dict[str, Any]],
) -> Tuple[int, int]:
    """Write `buffer` in 500-row chunks. Clears buffer when done."""
    if not buffer:
        return 0, 0
    inserted = 0
    updated = 0
    CHUNK = 500
    while buffer:
        chunk = buffer[:CHUNK]
        del buffer[:CHUNK]
        ops = []
        for r in chunk:
            f = {k: r.get(k) for k in _UPSERT_KEY_FIELDS}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        if not ops:
            continue
        try:
            res = await db[RAW_COLL].bulk_write(ops, ordered=False)
            inserted += int(res.upserted_count or 0)
            updated += int(res.modified_count or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("[alt_odds] bulk_write failed (%d ops): %s",
                         len(ops), exc)
    return inserted, updated


# ── Status helpers ────────────────────────────────────────────────────
def _status_filter(chunk_start: str, chunk_end: str, date: str,
                   market: str, snapshot_iso: str) -> Dict[str, Any]:
    """Compound key for status rows. `snapshot_iso` is part of the key
    so morning/afternoon/pre-lock snapshots of the same date+market
    each get their own status row and never block each other."""
    return {"chunk_start_date": chunk_start, "chunk_end_date": chunk_end,
            "current_date": date, "current_market": market,
            "snapshot_iso": snapshot_iso}


async def _set_status(db, *, chunk_start: str, chunk_end: str,
                      date: str, market: str, snapshot_iso: str,
                      snapshot_hour: int, status: str, **extra) -> None:
    doc = {"status": status, "snapshot_hour": snapshot_hour,
           "updated_at": datetime.now(timezone.utc), **extra}
    if status == "completed":
        doc["completed_at"] = datetime.now(timezone.utc)
    await db[STATUS_COLL].update_one(
        _status_filter(chunk_start, chunk_end, date, market, snapshot_iso),
        {"$set": doc}, upsert=True,
    )


async def _is_completed(db, *, chunk_start: str, chunk_end: str,
                        date: str, market: str, snapshot_iso: str) -> bool:
    doc = await db[STATUS_COLL].find_one(
        _status_filter(chunk_start, chunk_end, date, market, snapshot_iso),
        projection={"_id": 0, "status": 1},
    )
    return bool(doc and doc.get("status") == "completed")


# ── Single-date ingest ────────────────────────────────────────────────
async def ingest_date(
    db, client: OddsAPIClient, *,
    date: str,
    markets: List[str],
    chunk_start: str,
    chunk_end: str,
    snapshot_hour: int = SNAPSHOT_HOUR_UTC,
    regions: Optional[List[str]] = None,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    force: bool = False,
) -> Dict[str, Any]:
    regions = regions or DEFAULT_REGIONS
    snapshot_iso = f"{date}T{snapshot_hour:02d}:00:00Z"

    # 1. List events for this date (one cheap call).
    try:
        events = await client.list_historical_events(
            sport="baseball_mlb", snapshot_iso=snapshot_iso,
        )
    except CreditBudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[alt_odds] %s list_historical_events failed: %s",
                     date, exc)
        return {"date": date, "events": 0, "error": repr(exc)}

    # Some Savant-side flakiness returns events with commence_time not on
    # `date`. Filter strict to MLB games starting on `date` (UTC date).
    same_day_events = [
        e for e in events
        if (e.get("commence_time") or "")[:10] == date
    ]

    market_stats: Dict[str, Dict[str, int]] = {}
    total_rows = 0
    total_inserted = 0
    total_updated = 0

    for market in markets:
        if not force and await _is_completed(
            db, chunk_start=chunk_start, chunk_end=chunk_end,
            date=date, market=market, snapshot_iso=snapshot_iso,
        ):
            logger.info("[alt_odds] skip completed %s/%s @%s",
                        date, market, snapshot_iso)
            continue

        await _set_status(
            db, chunk_start=chunk_start, chunk_end=chunk_end,
            date=date, market=market, snapshot_iso=snapshot_iso,
            snapshot_hour=snapshot_hour, status="in_progress",
            events_total=len(same_day_events),
        )

        # 2. For every event on this date, pull its odds for this market.
        buffer: List[Dict[str, Any]] = []
        ev_done = 0
        ev_with_data = 0
        ev_errors = 0
        for ev in same_day_events:
            ev_id = ev.get("id")
            if not ev_id:
                continue
            try:
                env = await fetch_historical_event_odds_envelope(
                    client, sport="baseball_mlb", event_id=ev_id,
                    markets=[market], regions=regions,
                    snapshot_iso=snapshot_iso,
                )
            except SnapshotNotAvailable:
                ev_done += 1
                continue
            except CreditBudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[alt_odds] %s ev=%s %s fetch failed: %s",
                    date, ev_id, market, exc,
                )
                ev_errors += 1
                ev_done += 1
                continue

            inner = (env or {}).get("data") if isinstance(env, dict) else None
            if not isinstance(inner, dict):
                inner = env  # already unwrapped by some code paths
            if isinstance(inner, dict):
                rows = _normalize_event(
                    inner, market=market, game_date=date,
                    snapshot_iso=snapshot_iso,
                )
            else:
                rows = []
            if rows:
                ev_with_data += 1
            buffer.extend(rows)
            ev_done += 1

            # Periodic flush keeps RSS flat.
            if len(buffer) >= 1000:
                ins, upd = await _flush_rows(db, buffer)
                total_inserted += ins
                total_updated += upd

            rss = _rss_mb()
            if rss > mem_limit_mb:
                # Flush whatever we have, checkpoint, exit gracefully.
                ins, upd = await _flush_rows(db, buffer)
                total_inserted += ins
                total_updated += upd
                await _set_status(
                    db, chunk_start=chunk_start, chunk_end=chunk_end,
                    date=date, market=market, snapshot_iso=snapshot_iso,
                    snapshot_hour=snapshot_hour, status="memory_halt",
                    rss_mb=rss, mem_limit_mb=mem_limit_mb,
                    events_done=ev_done, events_total=len(same_day_events),
                )
                raise MemoryError(
                    f"RSS {rss:.1f} MB exceeded limit {mem_limit_mb} MB "
                    f"at {date}/{market} (ev {ev_done}/{len(same_day_events)})"
                )

        # Final flush for this market.
        ins, upd = await _flush_rows(db, buffer)
        total_inserted += ins
        total_updated += upd
        n_rows = ins + upd

        market_stats[market] = {
            "events_total": len(same_day_events),
            "events_with_data": ev_with_data,
            "events_errors": ev_errors,
            "rows_written": n_rows,
        }
        total_rows += n_rows

        await _set_status(
            db, chunk_start=chunk_start, chunk_end=chunk_end,
            date=date, market=market, snapshot_iso=snapshot_iso,
            snapshot_hour=snapshot_hour, status="completed",
            events_total=len(same_day_events),
            events_with_data=ev_with_data, events_errors=ev_errors,
            rows_written=n_rows, rss_mb=round(_rss_mb(), 1),
        )
        logger.info(
            "[alt_odds] %s %s done events=%d/%d rows=%d rss=%.1fMB",
            date, market, ev_with_data, len(same_day_events), n_rows, _rss_mb(),
        )

    return {
        "date": date,
        "events_total": len(same_day_events),
        "markets": market_stats,
        "rows_total": total_rows,
        "inserted": total_inserted,
        "updated": total_updated,
        "rss_mb_end": round(_rss_mb(), 1),
    }


# ── 20-date chunk orchestrator ─────────────────────────────────────────
async def ingest_chunk(
    db, *,
    dates: List[str],
    markets: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    snapshot_hour: int = SNAPSHOT_HOUR_UTC,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    force: bool = False,
    min_credit_floor: int = 1_000,
) -> Dict[str, Any]:
    if not dates:
        return {"error": "empty_date_list"}
    markets = markets or DEFAULT_MLB_MARKETS
    chunk_start = dates[0]
    chunk_end = dates[-1]

    await ensure_indexes(db)

    rss0 = _rss_mb()
    started_at = datetime.now(timezone.utc)
    logger.info(
        "[alt_odds] chunk %s..%s (%d dates × %d markets) RSS=%.1fMB",
        chunk_start, chunk_end, len(dates), len(markets), rss0,
    )

    per_date: List[Dict[str, Any]] = []
    rss_peak = rss0
    halted = False
    halt_reason: Optional[str] = None

    async with OddsAPIClient(min_remaining_credits=min_credit_floor) as client:
        for date in dates:
            try:
                d_res = await ingest_date(
                    db, client, date=date, markets=markets,
                    chunk_start=chunk_start, chunk_end=chunk_end,
                    snapshot_hour=snapshot_hour, regions=regions,
                    mem_limit_mb=mem_limit_mb, force=force,
                )
            except MemoryError as me:
                halted = True
                halt_reason = repr(me)
                break
            except CreditBudgetExceeded as ce:
                halted = True
                halt_reason = repr(ce)
                break
            per_date.append(d_res)
            rss_peak = max(rss_peak, _rss_mb())

    finished_at = datetime.now(timezone.utc)
    summary = {
        "chunk_start": chunk_start,
        "chunk_end": chunk_end,
        "dates_processed": len(per_date),
        "dates_planned": len(dates),
        "markets": markets,
        "regions": regions or DEFAULT_REGIONS,
        "rows_total": sum(d.get("rows_total", 0) for d in per_date),
        "rss_mb_start": round(rss0, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "rss_mb_end": round(_rss_mb(), 1),
        "elapsed_s": (finished_at - started_at).total_seconds(),
        "halted": halted,
        "halt_reason": halt_reason,
        "per_date": per_date,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    return summary


def daterange(start: str, end: str) -> List[str]:
    """Inclusive YYYY-MM-DD range."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    out = []
    while d0 <= d1:
        out.append(d0.strftime("%Y-%m-%d"))
        d0 += timedelta(days=1)
    return out


__all__ = [
    "DEFAULT_MLB_MARKETS", "DEFAULT_REGIONS", "DEFAULT_MEM_LIMIT_MB",
    "RAW_COLL", "STATUS_COLL",
    "ensure_indexes", "ingest_date", "ingest_chunk",
    "normalize_player_name", "daterange",
]
