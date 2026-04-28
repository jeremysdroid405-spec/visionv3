"""
Universal Sync Health Endpoint
==============================
Read-only aggregator. Surfaces a single JSON payload for ops dashboards.

GET /api/health/sync          — full payload (NBA + MLB)
GET /api/health/sync?sport=mlb   — single-sport slice

Status semantics:
  healthy   — primary signals present, no recent errors, freshness OK
  warning   — at least one secondary signal stale or one warning
              (e.g. lineup coverage low, statcast > 24 h old)
  critical  — primary signal failing (live_props empty, no recent sync)

Never raises 5xx for missing optional subsystems — every probe is
wrapped in best-effort try/except and reports `unavailable` instead.
This route does NOT trigger any sync, write any data, or interact
with model state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

_db = None  # injected by server.py at startup via set_db()


def set_db(db) -> None:
    global _db
    _db = db


# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).total_seconds()


def _coerce_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = datetime.fromisoformat(s)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
async def _probe_live_props(db, sport: str) -> Dict[str, Any]:
    coll = f"{sport}_live_props"
    try:
        active = await db[coll].count_documents({})
        sample = await db[coll].find_one(
            {"synced_at": {"$exists": True}},
            sort=[("synced_at", -1)],
            projection={"_id": 0, "synced_at": 1, "sync_batch_id": 1},
        )
        last_sync = _coerce_dt((sample or {}).get("synced_at"))
        batch_ids = await db[coll].distinct("sync_batch_id")
        return {
            "collection":     coll,
            "row_count":      active,
            "last_synced_at": last_sync.isoformat() if last_sync else None,
            "sync_age_sec":   _age_seconds(last_sync),
            "distinct_batches": len(batch_ids),
        }
    except Exception as exc:  # noqa: BLE001
        return {"collection": coll, "error": repr(exc), "row_count": 0}


async def _probe_prop_scores(db, sport: str) -> Dict[str, Any]:
    coll = f"{sport}_prop_scores"
    try:
        n = await db[coll].count_documents({})
        latest = await db[coll].find_one(
            {}, sort=[("scored_at", -1)],
            projection={"_id": 0, "scored_at": 1, "version_tag": 1},
        )
        scored_at = _coerce_dt((latest or {}).get("scored_at"))
        return {
            "collection":   coll,
            "row_count":    n,
            "last_scored_at": scored_at.isoformat() if scored_at else None,
            "score_age_sec": _age_seconds(scored_at),
            "latest_version_tag": (latest or {}).get("version_tag"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"collection": coll, "error": repr(exc), "row_count": 0}


async def _probe_pick_history(db, sport: str) -> Dict[str, Any]:
    coll = f"{sport}_pick_history"
    try:
        n = await db[coll].count_documents({})
        latest = await db[coll].find_one(
            {}, sort=[("inserted_at", -1)],
            projection={"_id": 0, "inserted_at": 1, "game_date": 1},
        )
        ungraded = await db[coll].count_documents({"hit": None})
        last_at = _coerce_dt((latest or {}).get("inserted_at"))
        return {
            "collection":   coll,
            "row_count":    n,
            "ungraded":     ungraded,
            "last_inserted_at": last_at.isoformat() if last_at else None,
            "insert_age_sec": _age_seconds(last_at),
        }
    except Exception as exc:  # noqa: BLE001
        return {"collection": coll, "error": repr(exc), "row_count": 0}


async def _probe_injuries(db) -> Dict[str, Any]:
    try:
        n = await db["injuries_normalized"].count_documents({})
        latest = await db["injuries_normalized"].find_one(
            {}, sort=[("updated_at", -1)],
            projection={"_id": 0, "updated_at": 1},
        )
        updated_at = _coerce_dt((latest or {}).get("updated_at"))
        return {
            "row_count":   n,
            "last_updated_at": updated_at.isoformat() if updated_at else None,
            "age_sec":     _age_seconds(updated_at),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


async def _probe_lineups_mlb(db) -> Dict[str, Any]:
    try:
        cards = await db["mlb_projected_lineups"].count_documents({})
        confirmed = await db["mlb_projected_lineups"].count_documents(
            {"confirmed": True}
        )
        latest = await db["mlb_projected_lineups"].find_one(
            {}, sort=[("as_of", -1)],
            projection={"_id": 0, "as_of": 1, "source": 1},
        )
        as_of = _coerce_dt((latest or {}).get("as_of"))
        # Active-slate live-prop coverage for batting_order
        lo = _now() - timedelta(hours=12)
        hi = _now() + timedelta(hours=36)
        active_total = 0
        active_with_bo = 0
        async for r in db["mlb_live_props"].find(
            {}, {"_id": 0, "commence_time": 1, "batting_order": 1},
        ):
            ct = _coerce_dt(r.get("commence_time"))
            if ct is None or not (lo <= ct <= hi):
                continue
            active_total += 1
            if r.get("batting_order") is not None:
                active_with_bo += 1
        coverage_pct = (active_with_bo / active_total * 100.0) if active_total else 0.0
        return {
            "card_count":  cards,
            "confirmed_cards": confirmed,
            "last_as_of":  as_of.isoformat() if as_of else None,
            "as_of_age_sec": _age_seconds(as_of),
            "active_slate_size": active_total,
            "active_slate_with_batting_order": active_with_bo,
            "active_slate_coverage_pct": round(coverage_pct, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


async def _probe_statcast(db) -> Dict[str, Any]:
    try:
        n = await db["mlb_statcast_raw"].count_documents({})
        latest = await db["mlb_statcast_raw"].find_one(
            {}, sort=[("game_date", -1)],
            projection={"_id": 0, "game_date": 1},
        )
        gd = (latest or {}).get("game_date")
        return {"row_count": n, "latest_game_date": gd}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


async def _probe_locks(db) -> Dict[str, Any]:
    try:
        from services.sync_lock import describe
        return await describe(db)
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


async def _probe_delta_engine(db, sport: str) -> Dict[str, Any]:
    try:
        wm = await db["delta_watermarks"].find_one(
            {"sport": sport}, {"_id": 0},
        )
        return wm or {"sport": sport, "watermark": None}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


async def _probe_watchers() -> Dict[str, Any]:
    try:
        from services.event_bus import get_event_bus
        return {"event_bus_stats": get_event_bus().get_stats()}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


# ---------------------------------------------------------------------------
def _classify_sport(payload: Dict[str, Any], sport: str) -> str:
    """Reduce per-sport probes into a single status. Conservative — any
    primary signal red ⇒ critical. Secondary stale ⇒ warning."""
    warnings: List[str] = []
    critical: List[str] = []

    lp = payload.get("live_props") or {}
    if not lp.get("row_count"):
        critical.append("live_props.row_count == 0")
    age = lp.get("sync_age_sec")
    if age is not None and age > 7200:        # 2 h
        critical.append("live_props.sync_age_sec > 7200")
    elif age is not None and age > 1800:      # 30 min
        warnings.append("live_props.sync_age_sec > 1800")

    if lp.get("distinct_batches", 0) > 1:
        warnings.append("multiple sync_batch_ids present (in-flight prune)")

    ps = payload.get("prop_scores") or {}
    if ps.get("row_count", 0) == 0:
        warnings.append("prop_scores.row_count == 0")

    if sport == "mlb":
        lu = payload.get("lineups") or {}
        cov = lu.get("active_slate_coverage_pct", 0)
        # Only complain after 22:00 UTC
        if _now().hour >= 22 and cov < 60:
            warnings.append(
                f"lineup coverage {cov:.0f}% < 60% after 22:00 UTC"
            )

    payload["warnings"] = warnings
    payload["critical"] = critical
    if critical:
        return "critical"
    if warnings:
        return "warning"
    return "healthy"


# ---------------------------------------------------------------------------
@router.get("/sync")
async def health_sync(
    response: Response,
    sport: Optional[str] = Query(
        None, description="Filter to a single sport (nba|mlb). Omit for all."
    ),
):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        return {"error": "db_not_initialised", "generated_at": _now().isoformat()}
    sports = [sport.lower()] if sport else ["nba", "mlb"]
    out: Dict[str, Any] = {
        "generated_at": _now().isoformat(),
        "overall_status": "healthy",
        "sports": {},
        "global": {
            "locks":   await _probe_locks(_db),
            "watchers": await _probe_watchers(),
        },
    }
    statuses: List[str] = []
    for sp in sports:
        if sp not in ("nba", "mlb"):
            continue
        sport_payload: Dict[str, Any] = {
            "live_props":    await _probe_live_props(_db, sp),
            "prop_scores":   await _probe_prop_scores(_db, sp),
            "pick_history":  await _probe_pick_history(_db, sp),
            "delta_engine":  await _probe_delta_engine(_db, sp),
        }
        if sp == "mlb":
            sport_payload["lineups"]  = await _probe_lineups_mlb(_db)
            sport_payload["statcast"] = await _probe_statcast(_db)
        sport_payload["status"] = _classify_sport(sport_payload, sp)
        statuses.append(sport_payload["status"])
        out["sports"][sp] = sport_payload
    # Global injuries (shared across both sports)
    out["global"]["injuries"] = await _probe_injuries(_db)
    if "critical" in statuses:
        out["overall_status"] = "critical"
    elif "warning" in statuses:
        out["overall_status"] = "warning"
    return out
