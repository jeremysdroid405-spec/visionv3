"""
Team Live Sync Service  (Phase 1)
=================================
Parallel to player live ingestion. Reuses the EXISTING
`UniversalOddsSyncService` for:

  * The Odds API HTTP client + shared rate-limit handling
  * `fetch_events()` — same per-sport sport_key resolution
  * `fetch_event_odds()` — same bulk-call shape, same bookmaker
    config, same retry/error paths, same `[ODDS_DIAG]` instrumentation

The ONLY differences vs the player path are:

  * Markets requested:
        h2h, spreads, totals, team_totals
    (Game/team markets — no `player_*` keys, no alternates yet.)
  * Outcome shape: team_totals outcomes carry a `description` =
    team name + `point` = the team total, the other three carry a
    `description` = team name and `price` = the moneyline/spread.
  * Writes go to `team_live_props` (NOT {sport}_live_props).
  * Run audit goes to `team_odds_ingest_runs` (already exists).
  * After all writes, invokes
    `passthrough_team_live_to_scores(db, sport=sport)` to mirror
    new live rows into `team_prop_scores` with model fields = None.

Live providers only. NO SGO READS in this module.
NO writes to player live collections.
"""
from __future__ import annotations
import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

from services.universal_odds_sync import UniversalOddsSyncService
from services.team_prop_passthrough import (
    passthrough_team_live_to_scores,
)

logger = logging.getLogger(__name__)

TEAM_LIVE_COLL  = "team_live_props"
TEAM_RUNS_COLL  = "team_odds_ingest_runs"
TEAM_MASTER     = "team_master_hub"

# Phase 1 market scope — game/team only, no alternates.
TEAM_MARKETS_PHASE1: List[str] = [
    "h2h",            # moneylines
    "spreads",        # game spreads (per team)
    "totals",         # game totals (Over/Under)
    "team_totals",    # per-team totals
]


# ─────────────────────────────────────────────────────────────────────
# Team-id resolution: provider returns team display name; we need to
# join against `team_master_hub.display_names.{full,short,market}` to
# get the canonical `team_id`.
# ─────────────────────────────────────────────────────────────────────
_NAME_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm_team_name(s: Optional[str]) -> str:
    if not isinstance(s, str):
        return ""
    return _NAME_NORM_RE.sub("", s.lower())


async def _build_team_name_lookup(db, sport: str
                                       ) -> Dict[str, Dict[str, Any]]:
    """Returns {normalized_name → team_master_hub row}."""
    out: Dict[str, Dict[str, Any]] = {}
    async for t in db[TEAM_MASTER].find(
        {"sport": sport}, projection={"_id": 0}):
        tid = t.get("team_id")
        if not tid:
            continue
        dn = t.get("display_names") or {}
        for key in ("full", "short", "market", "abbrev"):
            v = dn.get(key)
            if v:
                out[_norm_team_name(v)] = t
        # Also index by team_id itself (some books send abbrev)
        out[_norm_team_name(tid.split("_")[-1])] = t
    return out


def _route_team_id(team_name: Optional[str],
                     lookup: Dict[str, Dict[str, Any]]
                     ) -> Optional[str]:
    n = _norm_team_name(team_name)
    if not n:
        return None
    t = lookup.get(n)
    if t:
        return t.get("team_id")
    # try contains-match (handles "LA Lakers" vs "Los Angeles Lakers")
    for k, v in lookup.items():
        if n in k or k in n:
            return v.get("team_id")
    return None


# ─────────────────────────────────────────────────────────────────────
# Provider payload → team_live_props rows
# ─────────────────────────────────────────────────────────────────────
def _extract_team_props_from_odds(
    odds_data: Dict[str, Any],
    event_info: Dict[str, Any],
    *, sport: str, snapshot_iso: str,
    team_lookup: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """The Odds API event-odds payload → list of team_live_props rows.

    Iterates every (bookmaker × market × outcome) and emits ONE row
    per outcome that classifies as a team-side market. Matches the
    contract used by `team_historical_props` and the passthrough,
    minus historical-only fields.
    """
    rows: List[Dict[str, Any]] = []
    event_id = odds_data.get("event_id") or event_info.get("event_id") \
               or event_info.get("id")
    if not event_id:
        return rows

    commence_time = event_info.get("commence_time") \
                    or odds_data.get("commence_time")
    home_name = (event_info.get("home_team")
                  or odds_data.get("home_team") or "")
    away_name = (event_info.get("away_team")
                  or odds_data.get("away_team") or "")
    home_id   = _route_team_id(home_name, team_lookup)
    away_id   = _route_team_id(away_name, team_lookup)

    game_date = None
    if isinstance(commence_time, str) and len(commence_time) >= 10:
        game_date = commence_time[:10]

    for bm in odds_data.get("bookmakers") or []:
        book = bm.get("key") or bm.get("title")
        if not book:
            continue
        for market in bm.get("markets") or []:
            mk = market.get("key")
            if mk not in TEAM_MARKETS_PHASE1:
                continue
            for outcome in market.get("outcomes") or []:
                desc = outcome.get("name") or outcome.get("description")
                price = outcome.get("price")
                if price is None:
                    continue

                # ── Determine team and side / line per market shape ──
                team_id_for_row: Optional[str] = None
                side: str = ""
                line: Optional[float] = None
                market_label: str

                if mk == "h2h":
                    # outcome.name = team display name; price = ml
                    team_id_for_row = _route_team_id(
                        desc, team_lookup)
                    side = "ML"
                    market_label = "Moneyline"
                elif mk == "spreads":
                    # outcome.name = team display name; point = spread
                    team_id_for_row = _route_team_id(
                        desc, team_lookup)
                    try:
                        line = float(outcome.get("point")) \
                            if outcome.get("point") is not None \
                            else None
                    except (TypeError, ValueError):
                        line = None
                    side = "HOME" \
                        if team_id_for_row == home_id else "AWAY"
                    market_label = "Spread"
                elif mk == "totals":
                    # outcome.name = "Over"/"Under"; point = game total
                    side = (desc or "").upper()
                    if side not in ("OVER", "UNDER"):
                        continue
                    try:
                        line = float(outcome.get("point")) \
                            if outcome.get("point") is not None \
                            else None
                    except (TypeError, ValueError):
                        line = None
                    # Game total is NOT a per-team market — skip per
                    # service contract (team_id required).
                    continue
                elif mk == "team_totals":
                    # outcome.description = team name;
                    # outcome.name = "Over"/"Under"
                    team_name = outcome.get("description")
                    team_id_for_row = _route_team_id(
                        team_name, team_lookup)
                    side = (desc or "").upper()
                    if side not in ("OVER", "UNDER"):
                        continue
                    try:
                        line = float(outcome.get("point")) \
                            if outcome.get("point") is not None \
                            else None
                    except (TypeError, ValueError):
                        line = None
                    market_label = "Team Total"
                else:
                    continue

                if not team_id_for_row:
                    continue

                rows.append({
                    "event_id":      event_id,
                    "team_id":       team_id_for_row,
                    "market":        market_label,
                    "market_key":    mk,
                    "statID":        mk,
                    "line":          line,
                    "side":          side,
                    "book":          book,
                    "odds":          int(price),
                    "sport":         sport,
                    "league":        sport.upper(),
                    "game_date":     game_date,
                    "commence_time": commence_time,
                    "is_alternate":  False,
                    "reference_only": False,
                    "blocked":       False,
                    "home_team":     home_name,
                    "away_team":     away_name,
                    "home_team_id":  home_id,
                    "away_team_id":  away_id,
                    "snapshot_iso":  snapshot_iso,
                    "ingested_at":   snapshot_iso,
                    "source":        "the_odds_api",
                })
    return rows


# ─────────────────────────────────────────────────────────────────────
# Self-heal unique index on team_live_props.
# Same contract as team_historical_props — no snapshot_iso in key.
# ─────────────────────────────────────────────────────────────────────
async def _ensure_team_live_index(db) -> None:
    target_name = "ix_live_prop_compound_unique"
    target_keys = [("event_id", 1), ("team_id", 1),
                    ("market", 1), ("line", 1),
                    ("side", 1), ("book", 1)]
    try:
        info = await db[TEAM_LIVE_COLL].index_information()
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
            await db[TEAM_LIVE_COLL].drop_index(target_name)
        except Exception:
            pass
    try:
        await db[TEAM_LIVE_COLL].create_index(
            target_keys, unique=True, name=target_name)
        logger.info("[team_live_sync] ensured %s on %s",
                    target_name, TEAM_LIVE_COLL)
    except Exception:
        logger.exception(
            "[team_live_sync] failed to create unique index")


# ─────────────────────────────────────────────────────────────────────
# Upserts — same idempotent pattern as the team-historical worker.
# Re-runs collide on the compound key → modified_count grows, no row
# growth.
# ─────────────────────────────────────────────────────────────────────
def _build_upserts(rows: List[Dict[str, Any]]) -> List[UpdateOne]:
    ops: List[UpdateOne] = []
    for r in rows:
        filter_doc = {
            "event_id": r["event_id"],
            "team_id":  r["team_id"],
            "market":   r["market"],
            "line":     r["line"],
            "side":     r["side"],
            "book":     r["book"],
        }
        ops.append(UpdateOne(filter_doc,
                              {"$set": r,
                                "$setOnInsert":
                                    {"first_seen_at": r["ingested_at"]}},
                              upsert=True))
    return ops


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────
async def sync_team_live_for_sport(
    db, *, sport: str,
    max_events: Optional[int] = None,
    do_passthrough: bool = True,
) -> Dict[str, Any]:
    """Pull live game/team markets from The Odds API for `sport`,
    upsert into `team_live_props`, then passthrough to
    `team_prop_scores`.

    Reuses `UniversalOddsSyncService` for HTTP + fetch_events +
    fetch_event_odds + bookmaker config. We pass `markets_override`
    so the player code path never sees team markets, and the team
    path never sees player markets.
    """
    run_id      = str(uuid.uuid4())
    started_at  = datetime.now(timezone.utc)
    snapshot_iso = started_at.isoformat()

    audit: Dict[str, Any] = {
        "run_id":          run_id,
        "kind":            "team_live_sync",
        "sport":           sport,
        "started_at":      snapshot_iso,
        "markets":         list(TEAM_MARKETS_PHASE1),
        "source":          "the_odds_api",
        "n_events_fetched": 0,
        "n_events_with_odds": 0,
        "n_rows_normalized": 0,
        "n_rows_upserted":   0,
        "n_passthrough_upserted": 0,
        "status":          "running",
        "errors":          [],
    }

    await _ensure_team_live_index(db)

    sync = UniversalOddsSyncService(db)
    try:
        # 1. Discover live events for the sport (existing helper)
        events = await sync.fetch_events(sport=sport)
        if max_events is not None:
            events = events[:max_events]
        audit["n_events_fetched"] = len(events)
        if not events:
            audit["status"] = "no_events"
            audit["finished_at"] = datetime.now(
                timezone.utc).isoformat()
            await db[TEAM_RUNS_COLL].insert_one(dict(audit))
            return audit

        team_lookup = await _build_team_name_lookup(db, sport)

        # 2. Per-event: fetch odds → extract rows → upsert
        all_rows: List[Dict[str, Any]] = []
        for ev in events:
            event_id = ev.get("id") or ev.get("event_id")
            if not event_id:
                continue
            try:
                odds = await sync.fetch_event_odds(
                    event_id=event_id,
                    event_info=ev,
                    sport=sport,
                    markets_override=list(TEAM_MARKETS_PHASE1),
                )
            except Exception as exc:  # noqa: BLE001
                audit["errors"].append(
                    {"event_id": event_id,
                     "error": str(exc)[:240]})
                continue
            if not odds:
                continue
            audit["n_events_with_odds"] += 1
            rows = _extract_team_props_from_odds(
                odds, ev, sport=sport, snapshot_iso=snapshot_iso,
                team_lookup=team_lookup)
            if rows:
                all_rows.extend(rows)

        audit["n_rows_normalized"] = len(all_rows)

        # 3. Idempotent bulk upsert (1000-row slabs, same as historical)
        if all_rows:
            CHUNK = 1000
            for i in range(0, len(all_rows), CHUNK):
                slab = all_rows[i:i + CHUNK]
                ops  = _build_upserts(slab)
                if not ops:
                    continue
                try:
                    rr = await db[TEAM_LIVE_COLL].bulk_write(
                        ops, ordered=False)
                    audit["n_rows_upserted"] += (
                        len(rr.upserted_ids or {})
                        + int(rr.modified_count or 0))
                except Exception as exc:  # noqa: BLE001
                    audit["errors"].append(
                        {"chunk_start": i, "error": str(exc)[:240]})

        # 4. Passthrough → team_prop_scores (idempotent)
        if do_passthrough and audit["n_rows_normalized"] > 0:
            try:
                pt = await passthrough_team_live_to_scores(
                    db, sport=sport)
                audit["n_passthrough_upserted"] = pt.get(
                    "n_upserted", 0)
                audit["passthrough"] = pt
            except Exception as exc:  # noqa: BLE001
                audit["errors"].append(
                    {"stage": "passthrough", "error": str(exc)[:240]})

        audit["status"] = "succeeded" if not audit["errors"] \
                          else "partial"
    finally:
        await sync.close_client()
        audit["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            await db[TEAM_RUNS_COLL].insert_one(dict(audit))
        except Exception:
            logger.exception("[team_live_sync] audit write failed")
    return audit
