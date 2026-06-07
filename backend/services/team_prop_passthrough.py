"""
Team-Prop Passthrough  (Phase 1 — odds-routed, no model)
========================================================
Mirrors the player-side write pattern:

   live  →   prop_scores       (the read source for the board)
   ----      -----------
   team_live_props   →   team_prop_scores      (this module)
   {sport}_live_props →   {sport}_prop_scores   (player path — unchanged)

This module does NOT call any live provider. It is strictly a
normalizing copier that the future live-ingest path can invoke
after writing to `team_live_props`. For Phase 1 it can also be
invoked lazily by the board API when the scoring collection is
empty or stale, so the UI works without any new cron wiring.

Rules (per latest directive):
  * No fake model outputs.  edge, true_probability, projection, cv,
    hit_rate_l{5,10,20} are explicitly None.
  * Every passthrough row carries:
        prop_type            = "team"
        team_model_pending   = True
        routing_source       = "odds"
        odds_routed          = True
  * Tier classification uses the SAME thresholds the player tier
    router uses today (odds-only fallback).
  * Idempotent: re-running over the same `team_live_props` slice
    upserts on the canonical compound unique key — no row growth.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

logger = logging.getLogger(__name__)

LIVE_COLL   = "team_live_props"
SCORES_COLL = "team_prop_scores"

# Odds-only tier-routing thresholds. Intentionally permissive; the
# Unranked bucket below catches anything malformed so we don't ship
# garbage to the board.
SAFE_HAVEN_MAX_ODDS = -250
FRONT_LINES_NEG_MIN = -249
FRONT_LINES_POS_MAX = +249
WAR_ZONE_MIN_ODDS   = +250
WAR_ZONE_MAX_ODDS   = +1500
ODDS_HARD_MIN       = -10_000
ODDS_HARD_MAX       = +10_000


def _odds_to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        n = int(float(v)) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        return None
    if n < ODDS_HARD_MIN or n > ODDS_HARD_MAX:
        return None
    return n


def _route_tier(odds_us: Optional[int]) -> Optional[str]:
    """Pure odds → tier. None == 'unranked'."""
    if odds_us is None:
        return None
    if odds_us <= SAFE_HAVEN_MAX_ODDS:
        return "safe_haven"
    if FRONT_LINES_NEG_MIN <= odds_us <= FRONT_LINES_POS_MAX:
        return "front_lines"
    if WAR_ZONE_MIN_ODDS <= odds_us <= WAR_ZONE_MAX_ODDS:
        return "war_zone"
    return None


def _normalize_team_row(
    row: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Take a `team_live_props` row, return the
    `team_prop_scores`-shaped dict. None means rejected (will land in
    the unranked bucket and never reach the board)."""
    tid = row.get("team_id")
    if not tid or tid in ("game", "all", "home", "away"):
        return None
    event_id = row.get("event_id")
    if not event_id:
        return None
    market = row.get("market") or row.get("market_key") \
             or row.get("statID")
    side   = (row.get("side") or "").upper()
    book   = row.get("book") or row.get("bookmaker") or ""
    if not (market and book):
        return None
    line = row.get("line")
    try:
        line = float(line) if line not in (None, "", "None") else None
    except (TypeError, ValueError):
        line = None
    odds = _odds_to_int(row.get("odds"))
    tier = _route_tier(odds)

    badges: List[str] = ["odds_routed", "team_model_pending"]
    if row.get("reference_only") in (True, "True"):
        badges.append("reference_only")
    if row.get("is_alternate") in (True, "True"):
        badges.append("alternate")
    if row.get("blocked") in (True, "True"):
        badges.append("blocked_book")

    return {
        # Identity (compound unique key — mirrors COMPOUND_UNIQUE_KEYS
        # for team_prop_scores).
        "event_id":             event_id,
        "team_id":              tid,
        "market":               market,
        "line":                 line,
        "side":                 side,
        "book":                 book,
        # model_version/gate_config_version are part of the unique
        # key in the scores collection. Keeping them null until a
        # real team model exists. Same null-token shape on every
        # row → idempotent passthrough.
        "model_version":        None,
        "gate_config_version":  None,

        # Display passthrough
        "prop_type":            "team",
        "sport":                row.get("sport"),
        "league":               row.get("league"),
        "game_date":            row.get("game_date"),
        "commence_time":        row.get("commence_time"),
        "market_key":           row.get("market_key"),
        "stat_type":            row.get("statID")
                                  or row.get("stat_type"),
        "period_id":            row.get("periodID"),
        "odds":                 odds,
        "sharp_price":          odds,
        "is_alternate":         row.get("is_alternate") in (True, "True"),
        "reference_only":       row.get("reference_only") in (True, "True"),
        "blocked":              row.get("blocked") in (True, "True"),

        # Matchup display strings — passed through so the tier
        # service / detail page can resolve opponent identity even
        # when `team_master_hub` doesn't carry the event yet
        # (synthetic seeds, brand-new sports, etc.).
        "home_team":            row.get("home_team"),
        "away_team":            row.get("away_team"),
        "home_team_id":         row.get("home_team_id"),
        "away_team_id":         row.get("away_team_id"),

        # Tier + badges
        "tier":                 tier,
        "tier_label":           {"safe_haven":  "Safe Haven",
                                  "front_lines": "Front Lines",
                                  "war_zone":    "War Zone"
                                  }.get(tier) if tier else "unranked",
        "badges":               badges,
        "team_model_pending":   True,
        "routing_source":       "odds",
        "odds_routed":          True,

        # Model fields — explicitly None per directive.
        "edge":                 None,
        "edge_pct":             None,
        "true_probability":     None,
        "projection":           None,
        "cv":                   None,
        "vision_score":         None,
        "intel_score":          None,
        "confidence":           None,
        "vk_predicted":         None,
        "season_avg":           None,
        "hit_rate_l5":          None,
        "hit_rate_l10":         None,
        "hit_rate_l20":         None,

        # Lineage
        "snapshot_iso":         row.get("snapshot_iso"),
        "ingested_at":          row.get("ingested_at"),
        "passthrough_at":       datetime.now(timezone.utc).isoformat(),
    }


async def _ensure_scores_index(db) -> None:
    """Self-heal the unique index on team_prop_scores. Mirrors the
    pattern used in the team-historical-ingest worker."""
    target_name = "ix_team_score_compound_unique"
    target_keys = [("event_id", 1), ("team_id", 1),
                    ("market", 1), ("line", 1),
                    ("side", 1), ("book", 1),
                    ("model_version", 1),
                    ("gate_config_version", 1)]
    try:
        info = await db[SCORES_COLL].index_information()
    except Exception:
        info = {}
    existing = info.get(target_name)
    if existing is not None:
        existing_keys = [(k, int(v)) for k, v
                          in (existing.get("key") or [])]
        if existing_keys == target_keys \
                and existing.get("unique"):
            return
        try:
            await db[SCORES_COLL].drop_index(target_name)
        except Exception:
            pass
    try:
        await db[SCORES_COLL].create_index(
            target_keys, unique=True, name=target_name)
        logger.info("[team_passthrough] created %s on %s",
                    target_name, SCORES_COLL)
    except Exception:
        logger.exception(
            "[team_passthrough] failed to create unique index")


async def passthrough_team_live_to_scores(
    db, *, sport: Optional[str] = None,
    max_rows: int = 50_000,
) -> Dict[str, Any]:
    """Copy every row from `team_live_props` (filtered by `sport` if
    given) into `team_prop_scores`, normalizing into the
    board-card shape with model fields nulled out.

    Idempotent: re-runs upsert on the compound unique key —
    `team_live_props` re-running doesn't grow `team_prop_scores`.

    Returns an audit dict with counts.
    """
    await _ensure_scores_index(db)

    flt: Dict[str, Any] = {}
    if sport:
        flt["sport"] = sport.lower()

    n_seen     = 0
    n_skipped  = 0
    n_upserted = 0
    n_unranked = 0
    ops: List[UpdateOne] = []
    cur = db[LIVE_COLL].find(flt, projection={"_id": 0}).limit(max_rows)
    async for row in cur:
        n_seen += 1
        scored = _normalize_team_row(row)
        if scored is None:
            n_skipped += 1
            continue
        if scored["tier"] is None:
            n_unranked += 1
        filter_doc = {
            "event_id":            scored["event_id"],
            "team_id":             scored["team_id"],
            "market":              scored["market"],
            "line":                scored["line"],
            "side":                scored["side"],
            "book":                scored["book"],
            "model_version":       scored["model_version"],
            "gate_config_version": scored["gate_config_version"],
        }
        ops.append(UpdateOne(filter_doc, {"$set": scored}, upsert=True))
        if len(ops) >= 1000:
            r = await db[SCORES_COLL].bulk_write(ops, ordered=False)
            n_upserted += len(r.upserted_ids or {}) \
                          + int(r.modified_count or 0)
            ops.clear()
    if ops:
        r = await db[SCORES_COLL].bulk_write(ops, ordered=False)
        n_upserted += len(r.upserted_ids or {}) \
                      + int(r.modified_count or 0)

    return {
        "passthrough_at": datetime.now(timezone.utc).isoformat(),
        "sport":          sport,
        "n_live_seen":    n_seen,
        "n_skipped":      n_skipped,
        "n_unranked":     n_unranked,
        "n_upserted":     n_upserted,
        "live_coll":      LIVE_COLL,
        "scores_coll":    SCORES_COLL,
    }
