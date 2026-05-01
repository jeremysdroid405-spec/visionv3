"""
PrizePicks Multiplier Lab
==========================
Stores tested PrizePicks lineup combinations and the payout multiplier
returned by the public game_types endpoint, so we can reverse-engineer
PrizePicks' payout structure over time.

SAFETY (READ-ONLY):
-------------------
- Hits ONLY the publicly visible browser-Network endpoints
  (/projections, /game_types).
- DOES NOT submit entries, place bets, or touch any auth, billing, or
  bot-protection (PerimeterX / px-cloud) endpoints.
- Conservative randomized delays (8-15 s) between requests.
- Bails on the FIRST 403 / 429 / captcha / auth-challenge / abnormal
  response — never retries, never escalates.
- Batch size hard-capped at 50 tests.
- A `dry_run` mode runs the full lineup-generation + persistence
  pipeline using cached projections WITHOUT making any outbound HTTP
  call to PrizePicks.

DB
--
collection: pp_payout_structure_tests
"""
from __future__ import annotations

import os
import json
import random
import asyncio
import logging
import secrets
from itertools import combinations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────
COLLECTION_NAME = "pp_payout_structure_tests"

# Read-only, public endpoints. These are the same URLs a normal browser
# requests when viewing the lineup-builder. We do NOT touch any auth,
# entry-submission, or bot-protection (px-cloud / PerimeterX) endpoints.
PRIZEPICKS_PROJECTIONS_URL = (
    "https://api.prizepicks.com/projections"
)
PRIZEPICKS_GAME_TYPES_URL = (
    "https://api.prizepicks.com/game_types"
)

# Block these endpoints / hosts even by accident.
FORBIDDEN_HOST_FRAGMENTS = (
    "px-cloud", "perimeterx", "/entries", "/auth", "/picks",
    "captcha", "bot-defender",
)

# Conservative delay range (seconds).
DEFAULT_MIN_DELAY = 8.0
DEFAULT_MAX_DELAY = 15.0

# Batch caps.
MAX_BATCH_SIZE = 50
DEFAULT_BATCH_SIZE = 5  # admin endpoint default
RUN_NOW_HARD_CAP = 25   # `run-now` (auto-source + run) is more cautious

# Stop responses.
STOP_STATUS_CODES = (401, 403, 429)

# Cache: discovered projection IDs (so repeat run-now calls don't
# re-hit the public projections endpoint within a short window).
PROJECTION_ID_CACHE = "pp_projection_id_cache"
DEFAULT_DISCOVERY_TTL_MINUTES = 15

# Public PrizePicks league IDs (commonly known + visible in browser
# Network panel; these are NOT auth-bound). We map sport → league_id
# only as a convenience; caller can override.
SPORT_TO_LEAGUE_ID = {
    "NBA": "7", "MLB": "2", "NFL": "9", "NHL": "8",
    "WNBA": "3", "PGA": "12", "MMA": "20",
}


# ─── Module-level state (db handle injected from server) ────────────
_db = None


def set_db(db) -> None:
    """Inject the MongoDB handle.

    Accepts either a sync `pymongo` Database or an async motor
    `AsyncIOMotorDatabase`. If an async DB is supplied we open our
    own sync client from `MONGO_URL`/`DB_NAME` because all CRUD in
    this service is intentionally synchronous (read-only research
    tooling that runs in admin batches, not on the hot path).
    """
    global _db
    is_async_motor = (
        type(db).__module__.startswith("motor.")
        or "Motor" in type(db).__name__
    )
    if is_async_motor:
        from pymongo import MongoClient
        sync_client = MongoClient(
            os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        )
        _db = sync_client[os.environ.get("DB_NAME", "pick_vision")]
        logger.info(
            "[PP_LAB] Async motor db detected — opened parallel sync client"
        )
    else:
        _db = db


def _require_db():
    if _db is None:
        raise RuntimeError("pp_multiplier_lab: db not initialized")
    return _db


# ─── Index management ───────────────────────────────────────────────
INDEX_SPECS: List[Tuple[List[Tuple[str, int]], Dict[str, Any]]] = [
    ([("created_at", -1)], {"name": "ix_created_at"}),
    ([("leg_count", 1)], {"name": "ix_leg_count"}),
    ([("sport", 1)], {"name": "ix_sport"}),
    ([("league_id", 1)], {"name": "ix_league_id"}),
    ([("mix_type", 1)], {"name": "ix_mix_type"}),
    ([("power_play_multiplier", 1)], {"name": "ix_power_play_multiplier"}),
    ([("is_adjusted", 1)], {"name": "ix_is_adjusted"}),
    ([("selected_projection_ids", 1)], {"name": "ix_selected_projection_ids"}),
    ([("selected_projections.odds_type", 1)],
        {"name": "ix_proj_odds_type"}),
    ([("selected_projections.stat_type", 1)],
        {"name": "ix_proj_stat_type"}),
    ([("selected_projections.group_key", 1)],
        {"name": "ix_proj_group_key"}),
]


def ensure_collection_and_indexes() -> Dict[str, Any]:
    """Create the collection (if missing) and ensure all indexes.

    Idempotent — safe to call on every startup.
    """
    db = _require_db()
    if COLLECTION_NAME not in db.list_collection_names():
        db.create_collection(COLLECTION_NAME)
        logger.info(
            "[PP_LAB] Created collection %s", COLLECTION_NAME
        )
    coll = db[COLLECTION_NAME]
    created: List[str] = []
    for keys, opts in INDEX_SPECS:
        try:
            coll.create_index(keys, **opts)
            created.append(opts["name"])
        except PyMongoError as e:
            logger.warning(
                "[PP_LAB] index %s create failed: %s", opts["name"], e
            )
    return {
        "collection": COLLECTION_NAME,
        "indexes_created_or_existing": created,
        "projection_id_cache": _ensure_projection_id_cache(),
    }


# ─── Projection-ID cache (discovered IDs, TTL'd) ────────────────────
def _ensure_projection_id_cache() -> Dict[str, Any]:
    """Idempotent index ensure for the projection-id cache."""
    db = _require_db()
    if PROJECTION_ID_CACHE not in db.list_collection_names():
        db.create_collection(PROJECTION_ID_CACHE)
        logger.info("[PP_LAB] Created collection %s", PROJECTION_ID_CACHE)
    coll = db[PROJECTION_ID_CACHE]
    created: List[str] = []
    for keys, opts in [
        ([("league_id", 1)], {"name": "ix_league_id", "unique": True}),
        ([("fetched_at", -1)], {"name": "ix_fetched_at"}),
    ]:
        try:
            coll.create_index(keys, **opts)
            created.append(opts["name"])
        except PyMongoError as e:
            logger.warning(
                "[PP_LAB] cache index %s failed: %s", opts["name"], e
            )
    return {"collection": PROJECTION_ID_CACHE,
            "indexes_created_or_existing": created}


def _read_cached_projection_ids(
    league_id: str, max_age_minutes: int = DEFAULT_DISCOVERY_TTL_MINUTES,
) -> Optional[Dict[str, Any]]:
    """Return cached projection-ID list if newer than `max_age_minutes`."""
    db = _require_db()
    doc = db[PROJECTION_ID_CACHE].find_one(
        {"league_id": str(league_id)}, {"_id": 0},
    )
    if not doc:
        return None
    fetched_at = doc.get("fetched_at")
    if not isinstance(fetched_at, datetime):
        return None
    age_s = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age_s > max_age_minutes * 60:
        return None
    return doc


def _write_cached_projection_ids(
    league_id: str, projection_ids: List[str], source: str,
    raw_count: int,
) -> None:
    db = _require_db()
    db[PROJECTION_ID_CACHE].update_one(
        {"league_id": str(league_id)},
        {"$set": {
            "league_id": str(league_id),
            "projection_ids": list(projection_ids),
            "source": source,
            "raw_count": int(raw_count),
            "fetched_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ─── Helper field derivations ───────────────────────────────────────
def _norm_odds_type(v: Optional[str]) -> str:
    """Normalize PP odds_type strings to one of: goblin, demon, standard."""
    s = (v or "").strip().lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    return "standard"


def derive_mix_type(projections: List[Dict[str, Any]]) -> str:
    """E.g. goblin_demon, standard_standard, goblin_standard_demon, ...

    For 2-leg lineups returns a 2-token string in alphabetical order
    of the canonical types (so goblin_standard == standard_goblin).
    For larger lineups, returns a sorted underscore-joined string of
    counts: e.g. ``goblin2_standard1`` for two goblins + one standard.
    """
    if not projections:
        return "empty"
    types = sorted(_norm_odds_type(p.get("odds_type")) for p in projections)
    if len(types) <= 3:
        return "_".join(types)
    counts: Dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    return "_".join(f"{k}{v}" for k, v in sorted(counts.items()))


def derive_same_game(projections: List[Dict[str, Any]]) -> bool:
    """All selected projections share the same game_id."""
    if not projections:
        return False
    gids = {p.get("game_id") for p in projections if p.get("game_id")}
    return len(gids) == 1


def derive_same_player_or_group_conflict(
    projections: List[Dict[str, Any]]
) -> bool:
    """True if two legs share the same group_key or player+stat combo."""
    if not projections:
        return False
    seen_gk: set = set()
    seen_player_stat: set = set()
    for p in projections:
        gk = p.get("group_key")
        if gk:
            if gk in seen_gk:
                return True
            seen_gk.add(gk)
        ps = (p.get("player_name") or "", p.get("stat_type") or "")
        if ps[0] and ps[1]:
            if ps in seen_player_stat:
                return True
            seen_player_stat.add(ps)
    return False


# ─── Projection-shape extractor ─────────────────────────────────────
def extract_selected_projection(
    raw_projection: Dict[str, Any],
    included_index: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    """Flatten a JSON:API `data` row into the schema we persist.

    `included_index` maps (type, id) → attributes for cross-referencing
    new_player + league relations.
    """
    attrs = raw_projection.get("attributes") or {}
    rels = raw_projection.get("relationships") or {}

    new_player_ref = ((rels.get("new_player") or {}).get("data") or {})
    player_attrs: Dict[str, Any] = {}
    if new_player_ref:
        player_attrs = (
            included_index.get(
                (str(new_player_ref.get("type")), str(new_player_ref.get("id")))
            )
            or {}
        )

    return {
        "projection_id": str(raw_projection.get("id") or ""),
        "player_name": player_attrs.get("display_name")
            or player_attrs.get("name") or attrs.get("description"),
        "team": player_attrs.get("team") or attrs.get("team"),
        "opponent": attrs.get("description"),
        "game_id": attrs.get("game_id"),
        "stat_type": attrs.get("stat_type"),
        "stat_display_name": attrs.get("stat_display_name")
            or attrs.get("stat_type"),
        "projection_type": attrs.get("projection_type"),
        "event_type": attrs.get("event_type"),
        "line_score": attrs.get("line_score"),
        "odds_type": attrs.get("odds_type"),
        "adjusted_odds": attrs.get("adjusted_odds"),
        "group_key": attrs.get("group_key"),
        "start_time": attrs.get("start_time"),
        "status": attrs.get("status"),
    }


# ─── Game-types parser ──────────────────────────────────────────────
def parse_game_types_response(
    raw: Dict[str, Any], leg_count: int,
) -> Dict[str, Any]:
    """Pull power_play_multiplier, srp_multiplier, is_adjusted from
    the canonical game_types payload shape documented in the spec."""
    out: Dict[str, Any] = {
        "power_play_multiplier": None,
        "srp_multiplier": None,
        "is_adjusted": None,
        "raw_game_type_response": raw,
    }
    data = raw.get("data") or []
    for entry in data:
        attrs = entry.get("attributes") or {}
        if (attrs.get("name") or "").lower() != "power play":
            continue
        payouts = attrs.get("payouts") or {}
        # Spec: payouts[str(leg_count)][str(leg_count)] = mult
        leg_block = payouts.get(str(leg_count)) or {}
        if isinstance(leg_block, dict):
            out["power_play_multiplier"] = leg_block.get(str(leg_count))
        out["is_adjusted"] = payouts.get("is_adjusted")
        srp = (attrs.get("payouts_srp") or {}).get("power") or []
        if srp and isinstance(srp, list) and srp[0]:
            out["srp_multiplier"] = srp[0][0]
        break
    return out


# ─── HTTP wrappers (with paranoia checks) ───────────────────────────
def _safety_check_url(url: str) -> None:
    low = url.lower()
    for frag in FORBIDDEN_HOST_FRAGMENTS:
        if frag in low:
            raise RuntimeError(
                f"BLOCKED: forbidden host fragment {frag!r} in {url!r}"
            )


async def _fetch_json(
    client: httpx.AsyncClient, url: str, params: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Read-only GET, returns (status_code, json_or_None).

    Hard-fails on forbidden hosts. Caller decides what to do with the
    status code (most callers should bail on STOP_STATUS_CODES).
    """
    _safety_check_url(url)
    resp = await client.get(url, params=params, timeout=20.0)
    try:
        body = resp.json() if "application/json" in (
            resp.headers.get("content-type", "")
        ) else None
    except (ValueError, json.JSONDecodeError):
        body = None
    return resp.status_code, body


async def fetch_projections_for_ids(
    client: httpx.AsyncClient, projection_ids: List[str],
) -> Tuple[int, Optional[Dict[str, Any]]]:
    params = {
        "single_stat": "true",
        "in_game": "true",
        "ids": ",".join(str(i) for i in projection_ids),
    }
    return await _fetch_json(client, PRIZEPICKS_PROJECTIONS_URL, params=params)


async def fetch_game_types(
    client: httpx.AsyncClient,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    return await _fetch_json(client, PRIZEPICKS_GAME_TYPES_URL)


# ─── Test-doc builder ───────────────────────────────────────────────
def build_test_document(
    *,
    sport: Optional[str],
    league_id: Optional[str],
    state_code: Optional[str],
    game_mode: Optional[str],
    leg_count: int,
    selected_projections: List[Dict[str, Any]],
    raw_projection_response: Optional[Dict[str, Any]],
    game_types_parsed: Dict[str, Any],
    request_metadata: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Compose the full document in the canonical schema."""
    selected_ids = [p.get("projection_id") for p in selected_projections]
    return {
        "test_id": secrets.token_hex(8),
        "created_at": datetime.now(timezone.utc),
        "source": "prizepicks_network",
        "sport": sport,
        "league_id": league_id,
        "state_code": state_code,
        "game_mode": game_mode,
        "leg_count": leg_count,
        "game_type": "Power Play",
        "selected_projection_ids": selected_ids,
        "selected_projections": selected_projections,
        "mix_type": derive_mix_type(selected_projections),
        "same_game": derive_same_game(selected_projections),
        "same_player_or_group_conflict":
            derive_same_player_or_group_conflict(selected_projections),
        "returned_payouts": (
            (game_types_parsed.get("raw_game_type_response", {}) or {})
            .get("data") or []
        ),
        "power_play_multiplier": game_types_parsed.get("power_play_multiplier"),
        "srp_multiplier": game_types_parsed.get("srp_multiplier"),
        "is_adjusted": game_types_parsed.get("is_adjusted"),
        "raw_projection_response": raw_projection_response,
        "raw_game_type_response": game_types_parsed.get("raw_game_type_response"),
        "request_metadata": request_metadata or {},
        "notes": notes,
    }


def insert_test(doc: Dict[str, Any]) -> str:
    """Persist a test document and return its `test_id`."""
    db = _require_db()
    coll = db[COLLECTION_NAME]
    coll.insert_one(doc)
    return doc.get("test_id") or ""


# ─── Lineup generation ──────────────────────────────────────────────
def candidate_lineups_from_projection_ids(
    projection_ids: List[str], leg_count: int, max_lineups: int = 50,
) -> List[List[str]]:
    """Generate up to `max_lineups` distinct k-combinations."""
    leg_count = max(2, int(leg_count))
    seen = set()
    out: List[List[str]] = []
    # Combinations of leg_count from projection_ids; bail when we have
    # max_lineups OR the combinatorial space is exhausted.
    for combo in combinations(sorted(projection_ids), leg_count):
        key = combo
        if key in seen:
            continue
        seen.add(key)
        out.append(list(combo))
        if len(out) >= max_lineups:
            break
    return out


# ─── Batch runner ───────────────────────────────────────────────────
async def run_batch(
    *,
    sport: Optional[str] = None,
    league_id: Optional[str] = None,
    state_code: Optional[str] = None,
    game_mode: Optional[str] = "power",
    leg_count: int = 2,
    projection_ids: Optional[List[str]] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_delay: float = DEFAULT_MIN_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Run a small batch of payout-structure tests.

    Args:
        projection_ids: Required (caller-supplied) list of PrizePicks
            projection IDs to combine. We do NOT auto-pull a global
            board from PP — caller is responsible for sourcing IDs
            (typically from the cached partner board they already have).
        batch_size: Hard-capped at MAX_BATCH_SIZE. Default 5.
        dry_run: When True, no outbound HTTP. Builds the document
            structure from internal cache (or from passed-in
            projection metadata) and persists it with an empty
            `returned_payouts`. Intended for verification + smoke tests.
    """
    if not projection_ids or len(projection_ids) < leg_count:
        return {
            "ok": False,
            "error": (
                f"need at least {leg_count} projection_ids; "
                f"got {len(projection_ids or [])}"
            ),
        }
    batch_size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
    leg_count = max(2, int(leg_count))
    if max_delay < min_delay:
        max_delay = min_delay

    lineups = candidate_lineups_from_projection_ids(
        projection_ids, leg_count, max_lineups=batch_size,
    )

    summary: Dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "requested_batch_size": batch_size,
        "generated_lineups": len(lineups),
        "lineups_persisted": 0,
        "stopped_early": False,
        "stop_reason": None,
        "test_ids": [],
        "errors": [],
    }
    if dry_run:
        # Persist dry-run docs so the operator can verify the pipeline
        # end-to-end without hitting PrizePicks.
        for ids in lineups:
            doc = build_test_document(
                sport=sport,
                league_id=league_id,
                state_code=state_code,
                game_mode=game_mode,
                leg_count=leg_count,
                selected_projections=[
                    {"projection_id": str(i), "odds_type": "standard"}
                    for i in ids
                ],
                raw_projection_response=None,
                game_types_parsed={
                    "power_play_multiplier": None,
                    "srp_multiplier": None,
                    "is_adjusted": None,
                    "raw_game_type_response": None,
                },
                request_metadata={"dry_run": True},
                notes="dry_run=True; no PrizePicks HTTP performed",
            )
            tid = insert_test(doc)
            summary["test_ids"].append(tid)
            summary["lineups_persisted"] += 1
        return summary

    # Live (read-only) path. Bails immediately on the first stop signal.
    headers = {
        "User-Agent": "PickVision-PPMultiplierLab/1.0 (read-only research)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        for idx, ids in enumerate(lineups):
            try:
                # 1) /projections — selected lineup metadata
                proj_status, proj_body = await fetch_projections_for_ids(
                    client, ids,
                )
                if proj_status in STOP_STATUS_CODES or proj_body is None:
                    summary["stopped_early"] = True
                    summary["stop_reason"] = (
                        f"projections HTTP {proj_status}"
                    )
                    summary["errors"].append({
                        "lineup_idx": idx, "endpoint": "projections",
                        "status": proj_status,
                    })
                    break

                # Build the included-index for cross-reference.
                included = (proj_body or {}).get("included") or []
                included_index = {
                    (str(it.get("type")), str(it.get("id"))): (
                        it.get("attributes") or {}
                    )
                    for it in included
                }
                rows = (proj_body or {}).get("data") or []
                selected_projections = [
                    extract_selected_projection(r, included_index) for r in rows
                ]

                # Conservative inter-request delay.
                await asyncio.sleep(random.uniform(min_delay, max_delay))

                # 2) /game_types — adjusted payout
                gt_status, gt_body = await fetch_game_types(client)
                if gt_status in STOP_STATUS_CODES or gt_body is None:
                    summary["stopped_early"] = True
                    summary["stop_reason"] = (
                        f"game_types HTTP {gt_status}"
                    )
                    summary["errors"].append({
                        "lineup_idx": idx, "endpoint": "game_types",
                        "status": gt_status,
                    })
                    break

                parsed = parse_game_types_response(gt_body, leg_count)

                # 3) Persist
                doc = build_test_document(
                    sport=sport,
                    league_id=league_id,
                    state_code=state_code,
                    game_mode=game_mode,
                    leg_count=leg_count,
                    selected_projections=selected_projections,
                    raw_projection_response=proj_body,
                    game_types_parsed=parsed,
                    request_metadata={
                        "projections_status": proj_status,
                        "game_types_status": gt_status,
                    },
                )
                tid = insert_test(doc)
                summary["test_ids"].append(tid)
                summary["lineups_persisted"] += 1

                # Inter-lineup delay (separate from inter-request delay).
                if idx + 1 < len(lineups):
                    await asyncio.sleep(random.uniform(min_delay, max_delay))

            except RuntimeError as e:
                # _safety_check_url raised — never recover, never retry.
                summary["stopped_early"] = True
                summary["stop_reason"] = f"safety_block: {e}"
                summary["errors"].append({"lineup_idx": idx, "error": str(e)})
                break
            except (httpx.HTTPError, asyncio.CancelledError) as e:
                summary["stopped_early"] = True
                summary["stop_reason"] = f"http_error: {type(e).__name__}: {e}"
                summary["errors"].append({"lineup_idx": idx, "error": str(e)})
                break

    return summary


# ─── Stats / read helpers ───────────────────────────────────────────
def get_recent_tests(limit: int = 25) -> List[Dict[str, Any]]:
    db = _require_db()
    coll = db[COLLECTION_NAME]
    cursor = coll.find({}, {"_id": 0}).sort("created_at", -1).limit(int(limit))
    out = []
    for d in cursor:
        # JSON-safe datetime
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


def get_stats() -> Dict[str, Any]:
    db = _require_db()
    coll = db[COLLECTION_NAME]
    total = coll.count_documents({})
    multipliers_seen = sorted(
        m for m in coll.distinct("power_play_multiplier") if m is not None
    )
    grouped_counts: Dict[str, Any] = {}

    def _by(field: str):
        pipeline = [
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        return [
            {"key": d["_id"], "count": d["count"]}
            for d in coll.aggregate(pipeline)
        ]

    grouped_counts["leg_count"] = _by("leg_count")
    grouped_counts["mix_type"] = _by("mix_type")
    grouped_counts["power_play_multiplier"] = _by("power_play_multiplier")
    grouped_counts["same_game"] = _by("same_game")

    odds_type_pipeline = [
        {"$unwind": "$selected_projections"},
        {"$group": {
            "_id": "$selected_projections.odds_type", "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}},
    ]
    grouped_counts["odds_type_legs"] = [
        {"key": d["_id"], "count": d["count"]}
        for d in coll.aggregate(odds_type_pipeline)
    ]

    return {
        "total_tests": total,
        "multipliers_seen": multipliers_seen,
        "grouped_counts": grouped_counts,
        "latest_tests": get_recent_tests(limit=5),
    }



# ─── Auto-source: projection-ID discovery ──────────────────────────
async def discover_projection_ids(
    *,
    sport: Optional[str] = None,
    league_id: Optional[str] = None,
    max_age_minutes: int = DEFAULT_DISCOVERY_TTL_MINUTES,
    force_refresh: bool = False,
    max_ids: int = 100,
) -> Dict[str, Any]:
    """Get a list of current PrizePicks projection IDs for a league.

    Source preference (in order):
      1. `pp_projection_id_cache` (TTL'd, default 15 min) — preferred.
      2. ONE read-only HTTP GET to the public
         `https://api.prizepicks.com/projections?league_id=…&per_page=N`
         endpoint (same endpoint a logged-out browser hits when
         viewing the lineup builder). Goes through the existing
         `_safety_check_url` so it can never accidentally hit
         px-cloud / PerimeterX / entries / auth / picks.

    Returns:
        {
          "ok": bool,
          "league_id": str,
          "projection_ids": List[str],  # may be []
          "source": "cache" | "live_http" | "none",
          "raw_count": int,
          "fetched_at": iso8601,
          "error": Optional[str],
        }
    """
    if not league_id:
        league_id = SPORT_TO_LEAGUE_ID.get((sport or "").upper())
    if not league_id:
        return {
            "ok": False, "league_id": None, "projection_ids": [],
            "source": "none", "raw_count": 0, "fetched_at": None,
            "error": (
                f"unknown sport={sport!r} — pass league_id explicitly"
            ),
        }
    league_id = str(league_id)

    # 1) Cache hit
    if not force_refresh:
        cached = _read_cached_projection_ids(league_id, max_age_minutes)
        if cached and cached.get("projection_ids"):
            return {
                "ok": True, "league_id": league_id,
                "projection_ids": list(cached["projection_ids"])[:max_ids],
                "source": "cache",
                "raw_count": int(cached.get("raw_count") or 0),
                "fetched_at": cached["fetched_at"].isoformat()
                    if isinstance(cached.get("fetched_at"), datetime)
                    else cached.get("fetched_at"),
                "error": None,
            }

    # 2) Single read-only fetch
    headers = {
        "User-Agent": "PickVision-PPMultiplierLab/1.0 (read-only research)",
        "Accept": "application/json",
    }
    params = {
        "league_id": league_id,
        "per_page": str(min(max_ids, 250)),
        "single_stat": "true",
    }
    try:
        async with httpx.AsyncClient(headers=headers) as client:
            _safety_check_url(PRIZEPICKS_PROJECTIONS_URL)
            resp = await client.get(
                PRIZEPICKS_PROJECTIONS_URL, params=params, timeout=20.0
            )
            status = resp.status_code
            if status in STOP_STATUS_CODES:
                return {
                    "ok": False, "league_id": league_id,
                    "projection_ids": [], "source": "live_http",
                    "raw_count": 0,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"HTTP {status} from /projections — abort",
                }
            try:
                body = resp.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            if not body or not isinstance(body, dict):
                return {
                    "ok": False, "league_id": league_id,
                    "projection_ids": [], "source": "live_http",
                    "raw_count": 0,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"non-JSON response (status={status})",
                }
    except RuntimeError as e:
        return {
            "ok": False, "league_id": league_id, "projection_ids": [],
            "source": "live_http", "raw_count": 0, "fetched_at": None,
            "error": f"safety_block: {e}",
        }
    except (httpx.HTTPError, asyncio.CancelledError) as e:
        return {
            "ok": False, "league_id": league_id, "projection_ids": [],
            "source": "live_http", "raw_count": 0, "fetched_at": None,
            "error": f"http_error: {type(e).__name__}: {e}",
        }

    rows = body.get("data") or []
    ids = [str(r.get("id")) for r in rows if r.get("id")][:max_ids]
    if ids:
        _write_cached_projection_ids(
            league_id, ids, source="live_http", raw_count=len(rows)
        )
    return {
        "ok": bool(ids),
        "league_id": league_id,
        "projection_ids": ids,
        "source": "live_http",
        "raw_count": len(rows),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": None if ids else "no projections returned",
    }


# ─── End-to-end auto runner ─────────────────────────────────────────
async def run_now(
    *,
    sport: Optional[str] = "NBA",
    league_id: Optional[str] = None,
    state_code: Optional[str] = None,
    game_mode: Optional[str] = "power",
    leg_count: int = 2,
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_delay: float = DEFAULT_MIN_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    dry_run: bool = False,
    max_candidates: int = 25,
) -> Dict[str, Any]:
    """End-to-end auto-runner: discover IDs → build lineups → run batch.

    Wraps the existing `run_batch` so all of its safety guarantees
    (no entries, no auth, no PerimeterX, hard-stop on 401/403/429,
    8-15 s randomized delays, batch size cap) flow through unchanged.

    `run-now` is more cautious than `run-batch`: hard-cap is
    `RUN_NOW_HARD_CAP` (25) instead of `MAX_BATCH_SIZE` (50).
    """
    batch_size = max(1, min(int(batch_size), RUN_NOW_HARD_CAP))
    leg_count = max(2, int(leg_count))

    # ── 1. Discover candidate projection IDs
    disc = await discover_projection_ids(
        sport=sport, league_id=league_id,
        max_ids=max(max_candidates, batch_size * leg_count + 5),
        force_refresh=not bool(dry_run),
    )
    discovered = disc.get("projection_ids") or []

    # In dry-run mode, if discovery returned nothing (no cache + no
    # network), fall back to synthetic IDs from `nba_cached_board`
    # composite_keys so the operator can still smoke-test the
    # pipeline without leaving the box. These pseudo-IDs are
    # clearly tagged via the test doc's `notes` field.
    synthetic_used = False
    if dry_run and not discovered:
        synthetic = _synthetic_ids_from_cached_board(sport, batch_size * leg_count + 5)
        if synthetic:
            discovered = synthetic
            synthetic_used = True

    report: Dict[str, Any] = {
        "ok": True,
        "sport": sport,
        "league_id": disc.get("league_id"),
        "leg_count": leg_count,
        "batch_size_requested": batch_size,
        "discovery": {
            "source": "synthetic_cached_board" if synthetic_used else disc.get("source"),
            "raw_count": disc.get("raw_count"),
            "fetched_at": disc.get("fetched_at"),
            "error": disc.get("error"),
        },
        "total_candidates_found": len(discovered),
        "tests_attempted": 0,
        "tests_saved": 0,
        "stopped_early": False,
        "stop_reason": None,
        "multipliers_found": [],
        "latest_test_ids": [],
        "errors": [],
    }
    if len(discovered) < leg_count:
        report["ok"] = False
        report["stop_reason"] = (
            f"only {len(discovered)} candidate projection IDs available; "
            f"need at least {leg_count}"
        )
        return report

    # ── 2. Run the existing batch pipeline
    notes_prefix = (
        "synthetic_dry_run (no PP HTTP)" if synthetic_used
        else None
    )
    batch_summary = await run_batch(
        sport=sport,
        league_id=disc.get("league_id"),
        state_code=state_code,
        game_mode=game_mode,
        leg_count=leg_count,
        projection_ids=discovered,
        batch_size=batch_size,
        min_delay=min_delay,
        max_delay=max_delay,
        dry_run=dry_run,
    )

    report["tests_attempted"] = batch_summary.get("generated_lineups", 0)
    report["tests_saved"] = batch_summary.get("lineups_persisted", 0)
    report["stopped_early"] = batch_summary.get("stopped_early", False)
    report["stop_reason"] = batch_summary.get("stop_reason")
    report["latest_test_ids"] = batch_summary.get("test_ids") or []
    report["errors"] = batch_summary.get("errors") or []

    # Annotate dry-run synthetic notes after the fact so /recent
    # shows the source clearly.
    if notes_prefix and report["latest_test_ids"]:
        db = _require_db()
        db[COLLECTION_NAME].update_many(
            {"test_id": {"$in": report["latest_test_ids"]}},
            {"$set": {"notes": notes_prefix}},
        )

    # Multipliers seen in the just-saved tests.
    if report["latest_test_ids"]:
        db = _require_db()
        cur = db[COLLECTION_NAME].find(
            {"test_id": {"$in": report["latest_test_ids"]}},
            {"_id": 0, "power_play_multiplier": 1},
        )
        report["multipliers_found"] = sorted({
            d.get("power_play_multiplier") for d in cur
            if d.get("power_play_multiplier") is not None
        })
    return report


def _synthetic_ids_from_cached_board(
    sport: Optional[str], n: int
) -> List[str]:
    """Pseudo-IDs derived from the cached_board's composite_keys.

    Used ONLY for `run_now(dry_run=True)` when the operator has no
    network access AND no projection-ID cache hit. The inserted docs
    are clearly labelled `synthetic_dry_run` in their `notes` field.
    """
    if not sport:
        return []
    coll_name = f"{sport.lower()}_cached_board"
    db = _require_db()
    if coll_name not in db.list_collection_names():
        return []
    cur = db[coll_name].find(
        {}, {"_id": 0, "_composite_key": 1,
             "standard": {"$slice": 1},
             "demons": {"$slice": 1}, "goblins": {"$slice": 1}}
    ).limit(max(n, 5))
    out: List[str] = []
    for doc in cur:
        for fld in ("standard", "demons", "goblins"):
            arr = doc.get(fld) or []
            for item in arr:
                ck = item.get("_composite_key") if isinstance(item, dict) else None
                if ck:
                    out.append(f"synthetic:{ck}")
                if len(out) >= n:
                    return out
    return out
