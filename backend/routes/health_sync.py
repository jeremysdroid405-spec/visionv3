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


async def _probe_pp_projection_ids(db, sport: str) -> Dict[str, Any]:
    """Coverage probe for `pp_projection_id_cache` (FIELD_OWNERSHIP.md:
    pp_projection_id + odds_type).

    The PP scraper writes one doc per `league_id` into
    `pp_projection_id_cache` — each doc carries a `projection_ids`
    array + `fetched_at` timestamp + `source`. When the scraper
    stops (rate limit, DNS, cron drift), the cache goes stale
    silently: downstream code falls back to "standard" odds_type
    everywhere and nobody notices. This probe makes staleness
    visible.

    Read-only. If the doc for this sport's league_id is missing or
    stale beyond 60 min, we flag `source_available=False` — we do
    NOT synthesise a projection ID.

    The paired `odds_type` coverage lives on `selected_projections`
    inside each `pp_multiplier_lab` doc (indexed at
    `ix_proj_odds_type`), so we surface its top-level distribution
    from there.

    Staleness logging (2026-05-04 Tier D): every invocation of this
    probe checks `last_refresh_age_sec` against two thresholds and
    emits a single log line at the appropriate level. No scheduler,
    no separate cron — the log fires whenever anything reads
    `/api/health/sync`, which is already polled by the ops tooling.
    Emission is per-probe-call: each `sport` logs independently.
      - WARN  at 6h+  (cache getting stale; scraper may be lagging)
      - CRITICAL at 24h+  (cache effectively dead)
    """
    try:
        _LEAGUE_ID = {"nba": "7", "mlb": "2"}  # PrizePicks internal IDs
        league_id = _LEAGUE_ID.get(sport)
        if league_id is None:
            return {"source_available": False, "reason": "no_league_id_mapping"}

        cache = db["pp_projection_id_cache"]
        doc = await cache.find_one(
            {"league_id": league_id},
            {"_id": 0, "projection_ids": 1, "fetched_at": 1,
             "raw_count": 1, "source": 1},
        )
        if not doc:
            # No cache doc at all — log once at CRITICAL so ops sees it.
            logger.critical(
                "[PP_STALENESS:%s] no pp_projection_id_cache row for "
                "league_id=%s — PP scraper has never successfully "
                "seeded this sport. All multipliers falling back to "
                "'standard' odds_type.",
                sport.upper(), league_id,
            )
            return {
                "league_id":           league_id,
                "cached":              False,
                "source_available":    False,
                "projection_id_count": 0,
                "last_refresh":        None,
                "last_refresh_age_sec": None,
            }

        pids   = doc.get("projection_ids") or []
        fetched = _coerce_dt(doc.get("fetched_at"))
        age_s   = _age_seconds(fetched)

        # Thresholds (FIELD_OWNERSHIP.md:pp_projection_id).
        WARN_S     = 6 * 3600
        CRITICAL_S = 24 * 3600

        stale = (age_s is not None and age_s > 3600)  # > 60 min: not fresh

        if age_s is not None:
            hrs = age_s / 3600.0
            if age_s >= CRITICAL_S:
                logger.critical(
                    "[PP_STALENESS:%s] pp_projection_id_cache age="
                    "%.1fh (CRITICAL ≥ 24h). league_id=%s, "
                    "projection_ids=%d, last_refresh=%s, source=%r. "
                    "Scraper effectively dead; downstream "
                    "demon/goblin lookups will return standard only.",
                    sport.upper(), hrs, league_id, len(pids),
                    fetched.isoformat() if fetched else "?",
                    doc.get("source"),
                )
            elif age_s >= WARN_S:
                logger.warning(
                    "[PP_STALENESS:%s] pp_projection_id_cache age="
                    "%.1fh (WARN ≥ 6h). league_id=%s, "
                    "projection_ids=%d, last_refresh=%s, source=%r. "
                    "Scraper is lagging; investigate before 24h "
                    "CRITICAL threshold.",
                    sport.upper(), hrs, league_id, len(pids),
                    fetched.isoformat() if fetched else "?",
                    doc.get("source"),
                )

        # odds_type mix lives on pp_multiplier_lab.selected_projections.
        odds_mix: Dict[str, int] = {}
        try:
            mix_cursor = db["pp_multiplier_lab"].aggregate([
                {"$match": {"league_id": league_id}},
                {"$unwind": "$selected_projections"},
                {"$group": {
                    "_id": "$selected_projections.odds_type",
                    "count": {"$sum": 1},
                }},
            ])
            async for r in mix_cursor:
                key = r.get("_id") or "null"
                odds_mix[key] = int(r.get("count") or 0)
        except Exception:  # pragma: no cover
            odds_mix = {}

        return {
            "league_id":            league_id,
            "cached":               True,
            "projection_id_count":  len(pids),
            "raw_count":            doc.get("raw_count"),
            "source":               doc.get("source"),
            "last_refresh":         fetched.isoformat() if fetched else None,
            "last_refresh_age_sec": age_s,
            "stale":                stale,
            "source_available":     (not stale) and len(pids) > 0,
            "odds_type_mix":        odds_mix,
            "staleness_threshold_hours": {"warn": 6, "critical": 24},
        }
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
            # 2026-05-04 Tier C — PP multiplier source health.
            "pp_projection_ids": await _probe_pp_projection_ids(_db, sp),
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


# ---------------------------------------------------------------------------
@router.get("/contracts")
async def health_contracts(response: Response):
    """Runtime API-contract violation counters (last 24 h).

    Surfaced from the `contract_violations` collection (TTL 24 h).
    Each counter resets automatically as old documents expire. Read-only —
    this endpoint NEVER writes, NEVER triggers a sync, NEVER touches model
    state.

    Payload shape (always present, defaults to 0):

        invalid_pick_card_count_last_24h
        suppressed_lineup_opportunity_count_last_24h
        hit_profile_mismatch_count_last_24h
        past_game_ticket_suppressed_count_last_24h
        logo_lookup_not_sport_keyed_count_last_24h
        missing_required_card_fields_by_sport: {nba: int, mlb: int, ...}
        status: 'healthy' | 'warning'    — warning when ANY counter > 0
        generated_at: ISO timestamp
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        return {
            "error": "db_not_initialised",
            "generated_at": _now().isoformat(),
        }
    try:
        from services.contract_enforcer import aggregate_24h_counters
        counters = await aggregate_24h_counters(_db)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": repr(exc),
            "generated_at": _now().isoformat(),
        }
    spike = (
        counters["invalid_pick_card_count_last_24h"]
        + counters["suppressed_lineup_opportunity_count_last_24h"]
        + counters["hit_profile_mismatch_count_last_24h"]
        + counters["past_game_ticket_suppressed_count_last_24h"]
        + counters["logo_lookup_not_sport_keyed_count_last_24h"]
    )
    return {
        "generated_at": _now().isoformat(),
        "status": "healthy" if spike == 0 else "warning",
        **counters,
    }


# ---------------------------------------------------------------------------
@router.get("/board")
async def health_board(response: Response):
    """Board observability — counts, fill %, ages, churn per (sport,
    tier, side). Read-only; reads from `board_state` and
    `board_state_events` (TTL 7d). Does NOT trigger reconcile, never
    touches publish logic.

    Front Lines reports OVER and UNDER as separate buckets (split_by_side).
    Safe Haven and War Zone report a single combined bucket (`side=null`).
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        return {
            "error": "db_not_initialised",
            "generated_at": _now().isoformat(),
        }
    try:
        from services.board.publisher import board_health_report
        return await board_health_report(_db)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": repr(exc),
            "generated_at": _now().isoformat(),
        }


# ---------------------------------------------------------------------------
@router.get("/active-transitions")
async def health_active_transitions(
    response: Response,
    sport: str = Query("nba", regex="^(nba|mlb)$"),
    hours: int = Query(24, ge=1, le=24 * 7),
) -> Dict[str, Any]:
    """Diagnostic surface for the `active_transitions` audit collection
    written by `services.board.set_active::set_active`.

    Read-only. Does NOT mutate state, does NOT re-score, does NOT
    touch the canonical `active` field on `{sport}_prop_scores`. This
    endpoint exists purely to answer "why did this pick fall off /
    reappear" by replaying the last N hours of lifecycle transitions.

    Query params:
        sport   — "nba" or "mlb"
        hours   — 1 .. 168, default 24

    Returns a small envelope:
        {
          "generated_at": iso,
          "sport":        "nba",
          "window_hours": 24,
          "total":              <int>,
          "active_to_inactive": <int>,
          "inactive_to_active": <int>,
          "top_reasons":        [{"reason": str, "count": int}, ...up to 5],
          "top_writers":        [{"source_writer": str, "count": int}, ...up to 5],
          "latest":             [... up to 25 transition rows ...]
        }

    Each `latest` row is grouped around the fields the operator needs
    to pinpoint a specific transition:
        sport · player · prop (stat_type + line + side) · active_from
        · active_to · reason · source_writer · timestamp
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        return {
            "error": "db_not_initialised",
            "generated_at": _now().isoformat(),
        }

    from services.board.set_active import AUDIT_COLL
    since = _now() - timedelta(hours=hours)

    try:
        audit = _db[AUDIT_COLL]
        match_stage = {"sport": sport, "occurred_at": {"$gte": since}}

        total = await audit.count_documents(match_stage)
        to_inactive = await audit.count_documents(
            {**match_stage, "to_state": False}
        )
        to_active = await audit.count_documents(
            {**match_stage, "to_state": True}
        )

        # Top reasons — aggregation. Cheap, indexed on (sport,
        # canonical_key, occurred_at) already.
        reason_cursor = audit.aggregate([
            {"$match": match_stage},
            {"$group": {"_id": "$reason", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ])
        top_reasons: List[Dict[str, Any]] = []
        async for row in reason_cursor:
            top_reasons.append({
                "reason": row.get("_id") or "unknown",
                "count":  int(row.get("count") or 0),
            })

        # `source_writer` isn't currently on the audit doc; we derive
        # it from the reason string (each reason is emitted by exactly
        # one writer per the SSOT contract). Keeps this endpoint
        # truthful without adding a new field to the writer — when
        # Tier B ships we can add the explicit column.
        _REASON_WRITER_MAP = {
            "retired_by_delta_engine": "services/scoring/tiering.py:mark_retired_inactive",
            "game_started":            "services/board/scanner.py:scan_sport",
        }
        writer_counts: Dict[str, int] = {}
        for r in top_reasons:
            w = _REASON_WRITER_MAP.get(r["reason"], "unknown")
            writer_counts[w] = writer_counts.get(w, 0) + r["count"]
        top_writers = sorted(
            ({"source_writer": w, "count": c} for w, c in writer_counts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:5]

        # Latest 25 transitions — join with `{sport}_prop_scores` on
        # canonical_key to surface player / stat / line / side in one
        # hop so the frontend doesn't have to look them up.
        latest_cursor = audit.find(
            match_stage, {"_id": 0}
        ).sort("occurred_at", -1).limit(25)
        latest_rows = await latest_cursor.to_list(length=25)

        keys = [r.get("canonical_key") for r in latest_rows if r.get("canonical_key")]
        id_lookup: Dict[str, Dict[str, Any]] = {}
        if keys:
            scores = _db[f"{sport}_prop_scores"]
            id_cursor = scores.find(
                {"canonical_key": {"$in": keys}},
                {"_id": 0, "canonical_key": 1, "player_name": 1,
                 "stat_type": 1, "line": 1, "recommendation": 1},
            )
            async for d in id_cursor:
                ck = d.get("canonical_key")
                if ck and ck not in id_lookup:
                    id_lookup[ck] = d

        latest: List[Dict[str, Any]] = []
        for r in latest_rows:
            ck = r.get("canonical_key")
            ident = id_lookup.get(ck) or {}
            reason = r.get("reason") or "unknown"
            to_state = bool(r.get("to_state"))
            # `active_from` is the logical inverse of `active_to` per
            # the set_active contract (we only audit actual transitions,
            # not no-ops). Stored explicitly for frontend clarity.
            active_from = not to_state
            occurred = r.get("occurred_at")
            latest.append({
                "sport":         r.get("sport"),
                "canonical_key": ck,
                "player":        ident.get("player_name"),
                "stat_type":     ident.get("stat_type"),
                "line":          ident.get("line"),
                "side":          ident.get("recommendation"),
                "active_from":   active_from,
                "active_to":     to_state,
                "reason":        reason,
                "source_writer": _REASON_WRITER_MAP.get(reason, "unknown"),
                "version_tag":   r.get("version_tag"),
                "timestamp":     occurred.isoformat()
                                 if hasattr(occurred, "isoformat") else occurred,
            })

        return {
            "generated_at":       _now().isoformat(),
            "sport":              sport,
            "window_hours":       hours,
            "total":              int(total),
            "active_to_inactive": int(to_inactive),
            "inactive_to_active": int(to_active),
            "top_reasons":        top_reasons,
            "top_writers":        top_writers,
            "latest":             latest,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("[HEALTH/active-transitions] failed: %s", exc)
        return {
            "error":        repr(exc),
            "generated_at": _now().isoformat(),
            "sport":        sport,
            "window_hours": hours,
        }



# ---------------------------------------------------------------------------
# SSOT Tier F #4 — score-document schema parity probe
# ---------------------------------------------------------------------------
#
# Tiny read-only diff between the Pydantic `ScoreDocument` declaration
# and the writer's `_project_score_doc` allowlist. Returns 200 with a
# `parity_ok` boolean so ops dashboards / CI can fail fast if a future
# adapter regression reopens the silent-drift gap.
#
# No scheduler, no writer, no fallback behaviour — the endpoint is a
# pure read of in-process metadata.
@router.get("/score-document-schema-parity")
async def score_document_schema_parity() -> Dict[str, Any]:
    try:
        from services.scoring.score_document_schema import ScoreDocument
        from services.scoring.prop_scores_store import (
            _IDENTITY_FIELDS,
            _SCORE_OUTPUT_FIELDS,
            _UNIVERSAL_POOL_FIELDS,
        )
    except Exception as exc:  # pragma: no cover
        return {
            "parity_ok": False,
            "error": f"import_failed: {exc!r}",
            "generated_at": _now().isoformat(),
        }

    declared = set(ScoreDocument.model_fields.keys())
    projected = (
        set(_IDENTITY_FIELDS)
        | set(_SCORE_OUTPUT_FIELDS)
        | set(_UNIVERSAL_POOL_FIELDS)
        | {"version_tag", "computed_at", "scored_at"}
    )
    missing_decls = sorted(projected - declared)
    declared_extras = sorted(declared - projected)
    extra_setting = ScoreDocument.model_config.get("extra")
    return {
        "parity_ok":               not missing_decls and extra_setting == "forbid",
        "extras_setting":          extra_setting,
        "declared_count":          len(declared),
        "projected_count":         len(projected),
        "missing_declarations":    missing_decls,
        "missing_count":           len(missing_decls),
        "declared_extras":         declared_extras,
        "declared_extras_count":   len(declared_extras),
        "generated_at":            _now().isoformat(),
    }


@router.get("/hit-rate-side-parity")
async def hit_rate_side_parity(
    sport: Optional[str] = Query(None, description="nba | mlb (omit for both)"),
) -> Dict[str, Any]:
    """Read-only invariant probe for the side-aware `hit_rate_l20` contract
    (CHANGELOG 2026-05-05). For every active doc on `final-{sport}-rt`:
        OVER  →  hit_rate_l20 == hit_rate_over
        UNDER →  hit_rate_l20 == hit_rate_under
    Returns one entry per sport. `status="ok"` iff both mismatch counts
    are zero. Pure count_documents queries — no scheduler, no writes."""
    sports = [sport.lower()] if sport else ["nba", "mlb"]
    results: List[Dict[str, Any]] = []
    for sp in sports:
        if sp not in ("nba", "mlb"):
            results.append({"sport": sp, "status": "unsupported"})
            continue
        coll = _db[f"{sp}_prop_scores"]
        tag = f"final-{sp}-rt"
        base = {
            "version_tag": tag, "active": True,
            "hit_rate_l20": {"$exists": True, "$ne": None},
        }
        over_checked = await coll.count_documents({
            **base, "recommendation": "OVER",
            "hit_rate_over": {"$exists": True, "$ne": None},
        })
        over_mismatches = await coll.count_documents({
            **base, "recommendation": "OVER",
            "hit_rate_over": {"$exists": True, "$ne": None},
            "$expr": {"$ne": ["$hit_rate_l20", "$hit_rate_over"]},
        })
        under_checked = await coll.count_documents({
            **base, "recommendation": "UNDER",
            "hit_rate_under": {"$exists": True, "$ne": None},
        })
        under_mismatches = await coll.count_documents({
            **base, "recommendation": "UNDER",
            "hit_rate_under": {"$exists": True, "$ne": None},
            "$expr": {"$ne": ["$hit_rate_l20", "$hit_rate_under"]},
        })
        results.append({
            "sport":               sp,
            "total_over_checked":  over_checked,
            "total_under_checked": under_checked,
            "over_mismatches":     over_mismatches,
            "under_mismatches":    under_mismatches,
            "status":              "ok" if (over_mismatches == 0 and under_mismatches == 0) else "drift",
        })
    return {"results": results, "generated_at": _now().isoformat()}

@router.get("/hit-rate-push-invariant")
async def hit_rate_push_invariant(
    sport: Optional[str] = Query(None, description="nba | mlb (omit for both)"),
) -> Dict[str, Any]:
    """Read-only invariant probe for the OVER+UNDER+push contract on
    whole-number-line docs (CHANGELOG 2026-05-05 NBA push fix).

    Two signals:
      * `mismatches`        — docs where `hit_rate_over + hit_rate_under > 100`
                              (impossible under any correct logic; flag as drift).
      * `pushes_observed`   — docs where `O + U < 100`. Independent OVER /
                              UNDER calculation produces this whenever pushes
                              exist in the L20 window. The complement bug
                              forces `O + U == 100` on every doc, so a
                              healthy sport will report `pushes_observed > 0`.

    Status:
      * `ok`                — `mismatches == 0` and `pushes_observed > 0`.
      * `no_pushes_observed`— `mismatches == 0` but no whole-number doc
                              shows `O + U < 100`. Could be legitimate
                              (rare for large samples) or a complement-
                              regression. Worth a manual look.
      * `drift`             — `mismatches > 0`. Hard data corruption."""
    sports = [sport.lower()] if sport else ["nba", "mlb"]
    out: List[Dict[str, Any]] = []
    for sp in sports:
        if sp not in ("nba", "mlb"):
            out.append({"sport": sp, "status": "unsupported"})
            continue
        coll = _db[f"{sp}_prop_scores"]
        tag = f"final-{sp}-rt"
        base = {
            "version_tag": tag, "active": True,
            "$expr": {"$eq": [{"$mod": ["$line", 1]}, 0]},
            "hit_rate_over": {"$exists": True, "$ne": None},
            "hit_rate_under": {"$exists": True, "$ne": None},
        }
        checked = await coll.count_documents(base)
        mismatches = await coll.count_documents({
            **base,
            "$expr": {
                "$and": [
                    {"$eq": [{"$mod": ["$line", 1]}, 0]},
                    {"$gt": [{"$add": ["$hit_rate_over", "$hit_rate_under"]}, 100]},
                ]
            },
        })
        pushes_observed = await coll.count_documents({
            **base,
            "$expr": {
                "$and": [
                    {"$eq": [{"$mod": ["$line", 1]}, 0]},
                    {"$lt": [{"$add": ["$hit_rate_over", "$hit_rate_under"]}, 100]},
                ]
            },
        })
        sample_mismatches: List[Dict[str, Any]] = []
        if mismatches > 0:
            cur = coll.find(
                {**base,
                 "$expr": {
                     "$and": [
                         {"$eq": [{"$mod": ["$line", 1]}, 0]},
                         {"$gt": [{"$add": ["$hit_rate_over", "$hit_rate_under"]}, 100]},
                     ]
                 }},
                {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
                 "recommendation": 1, "hit_rate_over": 1, "hit_rate_under": 1},
            ).limit(5)
            async for d in cur:
                sample_mismatches.append(d)
        if mismatches > 0:
            status = "drift"
        elif pushes_observed == 0 and checked > 0:
            status = "no_pushes_observed"
        else:
            status = "ok"
        out.append({
            "sport":                    sp,
            "whole_number_docs_checked": checked,
            "mismatches":               mismatches,
            "pushes_observed":          pushes_observed,
            "status":                   status,
            "sample_mismatches":        sample_mismatches,
        })
    return {"results": out, "generated_at": _now().isoformat()}


