"""
Shadow VK Capture Service
=========================

PARALLEL, READ-ONLY pipeline that co-locates production VK predictions
with the new context features for honest forward-testing of a shadow
model. Does **not** modify any production scoring, gating, tier logic,
or live endpoint.

Lifecycle:
  1. capture_shadow_snapshots()
       Runs **after** ForwardTestingService.capture_all_sports() so
       today's `forward_test_snapshots` are already on disk. Joins
       each NBA snapshot to `nba_player_context_features`
       (by player_name + stat_type) and writes to
       `shadow_vk_snapshots`.

  2. resolve_shadow_outcomes()
       Runs after the standard forward-test resolver. Copies
       `outcome`, `actual_value`, `resolved_at` from the resolved
       `forward_test_snapshots` row back onto its sibling
       `shadow_vk_snapshots` row. Idempotent.

Schema of `shadow_vk_snapshots`:
    sport, capture_date, captured_at, capture_reason,
    player_name, player_id, team, opponent, game_id, commence_time,
    stat_type, line, side,                                # 'over'/'under'
    vk_predicted, vk_prob, vk_edge,                       # production VK
    shadow_predicted, shadow_prob,                        # null until model trained
    context_features: { ...10 fields from nba_feature_engine...,
                        feature_coverage },
    outcome, actual_value, resolved_at,                   # filled by resolver
    fts_key_hash                                          # join key

Indexes:
    (sport, capture_date)  ascending
    (sport, player_name, stat_type, capture_date) unique
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

SHADOW_COLL = "shadow_vk_snapshots"
FTS_COLL = "forward_test_snapshots"
NBA_CTX_COLL = "nba_player_context_features"
HUB_COLL = "nba_master_hub_2026"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _key_hash(sport: str, capture_date: str, player_name: str,
              stat_type: str, line: Any) -> str:
    raw = f"{sport}|{capture_date}|{player_name}|{stat_type}|{line}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _infer_side(prop: Dict[str, Any]) -> str:
    """Best-effort: production VK currently scores OVER bets."""
    rec = (prop.get("vk_recommendation") or
           (prop.get("full_prop_data") or {}).get("vk_recommendation") or "")
    rec = str(rec).lower()
    if "under" in rec:
        return "under"
    return "over"


async def _build_player_id_lookup(db: AsyncIOMotorDatabase) -> Dict[str, int]:
    """Lower-cased display_name → bdl_player_id."""
    out: Dict[str, int] = {}
    cur = db[HUB_COLL].find(
        {}, {"_id": 0, "display_name": 1, "player_name": 1,
             "bdl_player_id": 1, "bdl_id": 1},
    )
    async for d in cur:
        nm = (d.get("display_name") or d.get("player_name") or "").strip().lower()
        pid = d.get("bdl_player_id") or d.get("bdl_id")
        if not nm or pid is None:
            continue
        try:
            out[nm] = int(pid)
        except (TypeError, ValueError):
            pass
    return out


async def _build_context_lookup(db: AsyncIOMotorDatabase) -> Dict[tuple, Dict]:
    """
    (player_name_lc, stat_type) → most-recent context_features doc.
    The current feature engine only keeps the upcoming-slate state, so
    this lookup is naturally fresh.
    """
    out: Dict[tuple, Dict] = {}
    cur = db[NBA_CTX_COLL].find(
        {}, {"_id": 0}
    ).sort("computed_at", -1)
    async for d in cur:
        nm = (d.get("player_name") or "").strip().lower()
        st = d.get("stat_type")
        if not nm or not st:
            continue
        key = (nm, st)
        if key in out:
            continue  # keep first (most recent due to sort)
        out[key] = d
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[SHADOW_COLL]
    await coll.create_index([("sport", 1), ("capture_date", -1)])
    await coll.create_index(
        [("sport", 1), ("player_name", 1), ("stat_type", 1), ("capture_date", 1)],
        unique=True,
        name="shadow_uniq_key",
    )
    await coll.create_index([("outcome", 1), ("capture_date", -1)])


async def capture_shadow_snapshots(
    db: AsyncIOMotorDatabase,
    sport: str = "nba",
    capture_date: Optional[str] = None,
    capture_reason: str = "scheduled",
) -> Dict[str, Any]:
    """
    Read today's `forward_test_snapshots` rows for `sport`, enrich each
    with the matching context_features doc (NBA only — MLB has no
    context engine yet), and upsert into `shadow_vk_snapshots`.
    """
    sport = sport.lower()
    await ensure_indexes(db)

    if capture_date is None:
        capture_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    started = datetime.now(timezone.utc)
    fts_rows = await db[FTS_COLL].find(
        {"sport": sport, "capture_date": capture_date},
        {"_id": 0},
    ).to_list(length=None)

    if not fts_rows:
        logger.info(f"[SHADOW_CAPTURE] no FTS rows for {sport} {capture_date}")
        return {
            "sport": sport, "capture_date": capture_date,
            "fts_rows": 0, "shadow_written": 0, "ctx_hits": 0,
        }

    ctx_lookup: Dict[tuple, Dict] = {}
    pid_lookup: Dict[str, int] = {}
    if sport == "nba":
        ctx_lookup = await _build_context_lookup(db)
        pid_lookup = await _build_player_id_lookup(db)

    written = 0
    ctx_hits = 0
    for prop in fts_rows:
        nm = (prop.get("player_name") or "").strip()
        st = prop.get("stat_type")
        line = prop.get("line")
        if not nm or not st or line is None:
            continue

        ctx_doc = ctx_lookup.get((nm.lower(), st)) if sport == "nba" else None
        # Freshness gate — only attach context features when the
        # source doc was computed within ±2 days of capture_date.
        # Otherwise we'd be attaching today's context to historical
        # snapshots, polluting the eventual shadow training set.
        if ctx_doc is not None:
            try:
                src_ts = ctx_doc.get("computed_at")
                if isinstance(src_ts, str):
                    src_ts = datetime.fromisoformat(src_ts.replace("Z", "+00:00"))
                if isinstance(src_ts, datetime):
                    if src_ts.tzinfo is None:
                        src_ts = src_ts.replace(tzinfo=timezone.utc)
                    target = datetime.strptime(capture_date, "%Y-%m-%d") \
                        .replace(tzinfo=timezone.utc)
                    delta_days = abs((src_ts - target).total_seconds()) / 86400.0
                    if delta_days > 2.0:
                        ctx_doc = None  # too stale — drop
            except Exception:
                ctx_doc = None
        ctx_block = (ctx_doc or {}).get("context_features") or {}
        if ctx_doc is not None:
            ctx_hits += 1

        full = prop.get("full_prop_data") or {}
        bdl_pid = pid_lookup.get(nm.lower()) if sport == "nba" else None

        doc = {
            "sport": sport,
            "capture_date": capture_date,
            "captured_at": started,
            "capture_reason": capture_reason,

            "player_name": nm,
            "player_id": bdl_pid,
            "team": prop.get("team"),
            "opponent": prop.get("opponent"),
            "game_id": prop.get("game_id") or full.get("game_id"),
            "commence_time": prop.get("game_time") or full.get("game_time"),

            "stat_type": st,
            "line": _safe_float(line),
            "side": _infer_side(prop),

            # Production VK predictions (read-only; never altered)
            "vk_predicted": _safe_float(prop.get("vk_predicted")),
            "vk_prob": _safe_float(prop.get("vk_prob")),
            "vk_edge": _safe_float(prop.get("vk_edge")),

            # Shadow predictions filled by offline training job once
            # we have enough resolved rows.
            "shadow_predicted": None,
            "shadow_prob": None,
            "shadow_model_version": None,

            # Context features (frozen at capture time)
            "context_features": {
                "usage_vacuum_factor": ctx_block.get("usage_vacuum_factor"),
                "key_player_out_flag": ctx_block.get("key_player_out_flag"),
                "team_usage_removed_pct": ctx_block.get("team_usage_removed_pct"),
                "blowout_risk": ctx_block.get("blowout_risk"),
                "rest_days": ctx_block.get("rest_days"),
                "back_to_back_flag": ctx_block.get("back_to_back_flag"),
                "pace_differential": ctx_block.get("pace_differential"),
                "defensive_matchup_tier": ctx_block.get("defensive_matchup_tier"),
                "potential_assists_rate": ctx_block.get("potential_assists_rate"),
                "home_away_split_delta": ctx_block.get("home_away_split_delta"),
                "feature_coverage": ctx_block.get("feature_coverage"),
                "_source_event_id": (ctx_doc or {}).get("event_id"),
                "_source_computed_at": (ctx_doc or {}).get("computed_at"),
            },

            # Filled by resolver
            "outcome": None,
            "actual_value": None,
            "resolved_at": None,

            "fts_key_hash": _key_hash(
                sport, capture_date, nm, st, line
            ),
        }

        await db[SHADOW_COLL].update_one(
            {
                "sport": sport,
                "capture_date": capture_date,
                "player_name": nm,
                "stat_type": st,
            },
            {"$set": doc},
            upsert=True,
        )
        written += 1

    coverage_pct = round(ctx_hits / max(written, 1), 3)
    logger.info(
        f"[SHADOW_CAPTURE] sport={sport} date={capture_date} "
        f"fts={len(fts_rows)} written={written} ctx_hits={ctx_hits} "
        f"coverage={coverage_pct:.1%}"
    )
    return {
        "sport": sport,
        "capture_date": capture_date,
        "fts_rows": len(fts_rows),
        "shadow_written": written,
        "ctx_hits": ctx_hits,
        "ctx_coverage": coverage_pct,
    }


async def capture_all_shadow(
    db: AsyncIOMotorDatabase,
    capture_reason: str = "scheduled",
) -> Dict[str, Any]:
    """Capture for every sport that the FTS service knows about."""
    out = {"captured_at": datetime.now(timezone.utc).isoformat(), "sports": {}}
    for sport in ("nba", "mlb"):
        out["sports"][sport] = await capture_shadow_snapshots(
            db, sport=sport, capture_reason=capture_reason
        )
    return out


async def resolve_shadow_outcomes(
    db: AsyncIOMotorDatabase,
    sport: Optional[str] = None,
    backfill_days: int = 14,
) -> Dict[str, Any]:
    """
    For each `shadow_vk_snapshots` row with `outcome IS NULL`, copy the
    resolved outcome/actual_value from the matching
    `forward_test_snapshots` row. Idempotent.
    """
    q: Dict[str, Any] = {"outcome": None}
    if sport:
        q["sport"] = sport.lower()

    # Limit to recent capture_dates to bound the work
    pipe = [
        {"$match": q},
        {"$group": {"_id": {"sport": "$sport", "capture_date": "$capture_date"}}},
        {"$sort": {"_id.capture_date": -1}},
        {"$limit": backfill_days * 2},
    ]
    pairs = []
    async for d in db[SHADOW_COLL].aggregate(pipe):
        pairs.append((d["_id"]["sport"], d["_id"]["capture_date"]))

    total_resolved = 0
    by_sport: Dict[str, int] = {}
    for sp, cap_date in pairs:
        # bulk fetch resolved FTS rows for that day
        cursor = db[FTS_COLL].find(
            {
                "sport": sp,
                "capture_date": cap_date,
                "outcome": {"$in": ["hit", "miss", "push", "cancelled"]},
            },
            {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
             "outcome": 1, "actual_value": 1, "resolved_at": 1},
        )
        async for fts in cursor:
            res = await db[SHADOW_COLL].update_one(
                {
                    "sport": sp,
                    "capture_date": cap_date,
                    "player_name": fts["player_name"],
                    "stat_type": fts["stat_type"],
                    "outcome": None,
                },
                {"$set": {
                    "outcome": fts.get("outcome"),
                    "actual_value": _safe_float(fts.get("actual_value")),
                    "resolved_at": fts.get("resolved_at") or datetime.now(timezone.utc),
                }},
            )
            if res.modified_count:
                total_resolved += 1
                by_sport[sp] = by_sport.get(sp, 0) + 1

    logger.info(
        f"[SHADOW_RESOLVE] dates_checked={len(pairs)} resolved={total_resolved} "
        f"by_sport={by_sport}"
    )
    return {"dates_checked": len(pairs), "resolved": total_resolved,
            "by_sport": by_sport}


async def stats_summary(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Quick health metrics for the shadow pipeline (read-only)."""
    total = await db[SHADOW_COLL].count_documents({})
    resolved = await db[SHADOW_COLL].count_documents(
        {"outcome": {"$in": ["hit", "miss"]}}
    )
    nba_total = await db[SHADOW_COLL].count_documents({"sport": "nba"})
    nba_with_ctx = await db[SHADOW_COLL].count_documents(
        {"sport": "nba",
         "context_features.feature_coverage": {"$gt": 0}}
    )
    nba_resolved = await db[SHADOW_COLL].count_documents(
        {"sport": "nba", "outcome": {"$in": ["hit", "miss"]}}
    )
    return {
        "total_rows": total,
        "resolved_rows": resolved,
        "nba_total": nba_total,
        "nba_with_context": nba_with_ctx,
        "nba_resolved": nba_resolved,
        "nba_ctx_coverage": round(nba_with_ctx / max(nba_total, 1), 3),
    }
