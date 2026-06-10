"""
reshape_team_props_to_replay.py — Adapter that feeds team props into the
EXISTING player backtesting pipeline.

WHY THIS EXISTS
    The PropVision player optimizer/backtester reads from
    `sgo_propvision_full_pipeline_replay`. Team props live in
    `team_model_prop_features` (Phase 2B). Rather than build a second
    backtesting framework, this adapter translates each team prop into
    one document with the SAME row shape, written into the SAME
    collection, with `prop_type="team"` so the optimizer can filter.

WHAT IT DOES NOT DO
    - Does not duplicate the replay engine, optimizer, reporting,
      ROI calc, hit-rate, tier breakdown, or odds-bucket logic.
    - Does not touch player rows in the replay collection.
    - Does not touch live routing.
    - Does not touch NCAAF.

FIELD MAPPING (team_model_prop_features → replay row)
    league_id              = sport.upper()
    prop_type              = "team"                (NEW; player rows
                                                    legacy/absent)
    team_id, opponent_team_id  (NEW columns on the replay row)
    player_name(_normalized)   = None
    event_id, game_date, side, line, book, odds, is_alternate, periodID,
        betTypeID, market(_key), market_category as `stat_family`
    odds_bucket            = same `_odds_bucket(odds)` helper player uses
    hit_rate_l5/l10/l20    = projected from team_features (per-market):
        market_category   →  hr_l5                hr_l10                hr_l20
        ─────────────────    ─────────            ─────────             ─────────
        h2h               →  win_rate_l5          win_rate_l10          win_rate_season
        spread            →  None                 spread_cover_rate_l10 None
        game_total        →  None                 ou_hit_rate_l10       None
        team_total        →  None                 None                  None  (Phase 4)
    cv                     = team_features.cv_points_scored
    tp / edge / vision_score / model_probability = None  (Phase 4)
    tier flags             = None / False         (no team gates yet —
                                                    optimizer's odds-range
                                                    tier filter still works)
    outcome_resolved / outcome_numeric / hit  = lifted from the prop row
    pipeline_version       = "team_v1"
    ssot_source            = "team_prop_features"
    scored_at, as_of_date  = computed_at / game_date

USAGE
    python -m scripts.sgo.reshape_team_props_to_replay --sport mlb --dry-run
    python -m scripts.sgo.reshape_team_props_to_replay --sport all
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo
from pymongo import UpdateOne

# Reuse the SAME odds-bucket helper player props use — never re-implement.
from scripts.sgo.historical_full_pipeline_replay import _odds_bucket
from services.replay.markets import REPLAY_BOOK_WHITELIST_PHASE1

TEAM_ID_TO_NAME = {
    "mlb_ari": "arizona diamondbacks", "mlb_atl": "atlanta braves",
    "mlb_bal": "baltimore orioles", "mlb_bos": "boston red sox",
    "mlb_chc": "chicago cubs", "mlb_chw": "chicago white sox",
    "mlb_cin": "cincinnati reds", "mlb_cle": "cleveland guardians",
    "mlb_col": "colorado rockies", "mlb_det": "detroit tigers",
    "mlb_hou": "houston astros", "mlb_kcr": "kansas city royals",
    "mlb_laa": "los angeles angels", "mlb_lad": "los angeles dodgers",
    "mlb_mia": "miami marlins", "mlb_mil": "milwaukee brewers",
    "mlb_min": "minnesota twins", "mlb_nym": "new york mets",
    "mlb_nyy": "new york yankees", "mlb_oak": "oakland athletics",
    "mlb_phi": "philadelphia phillies", "mlb_pit": "pittsburgh pirates",
    "mlb_sdp": "san diego padres", "mlb_sea": "seattle mariners",
    "mlb_sfg": "san francisco giants", "mlb_stl": "st. louis cardinals",
    "mlb_tbr": "tampa bay rays", "mlb_tex": "texas rangers",
    "mlb_tor": "toronto blue jays", "mlb_wsn": "washington nationals",
    "nba_atl": "atlanta hawks", "nba_bkn": "brooklyn nets",
    "nba_bos": "boston celtics", "nba_cha": "charlotte hornets",
    "nba_chi": "chicago bulls", "nba_cle": "cleveland cavaliers",
    "nba_dal": "dallas mavericks", "nba_den": "denver nuggets",
    "nba_det": "detroit pistons", "nba_gsw": "golden state warriors",
    "nba_hou": "houston rockets", "nba_ind": "indiana pacers",
    "nba_lac": "la clippers", "nba_lal": "los angeles lakers",
    "nba_mem": "memphis grizzlies", "nba_mia": "miami heat",
    "nba_mil": "milwaukee bucks", "nba_min": "minnesota timberwolves",
    "nba_nop": "new orleans pelicans", "nba_nyk": "new york knicks",
    "nba_okc": "oklahoma city thunder", "nba_orl": "orlando magic",
    "nba_phi": "philadelphia 76ers", "nba_phx": "phoenix suns",
    "nba_por": "portland trail blazers", "nba_sac": "sacramento kings",
    "nba_sas": "san antonio spurs", "nba_tor": "toronto raptors",
    "nba_uta": "utah jazz", "nba_was": "washington wizards",
}

PIPELINE_VERSION = "team_v1_scored"
SSOT_SOURCE = "team_prop_features"
SRC_COLL = "team_model_prop_features"
DST_COLL = "sgo_propvision_full_pipeline_replay"
ARCHIVE_COLL = "sgo_propvision_full_pipeline_replay_archive"

SUPPORTED_SPORTS = ("mlb", "nba", "nfl")


# ───── tier routing ─────
# Mirrors `_tier_odds_filter` in routes/emergent_admin/optimizer.py.
# Pure odds-bucket-based routing (per operator: "tier routing only").
def tier_for_odds_bucket(bucket: str) -> str:
    if bucket in ("odds_-200_-100", "odds_lt_-200"):
        return "safe_haven"
    if bucket in ("odds_+150_+300", "odds_+300p"):
        return "war_zone"
    return "front_lines"   # +0_+150, -100_-0, na


# ───── pure helpers (unit-tested) ─────
def project_hit_rates_from_team_features(
    market_category: Optional[str],
    team_features: Optional[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """Map team rolling priors → player-style hr_l5/l10/l20 by
    market_category. Pure function.

    Player rows store hr_lN as Optional[float] in [0, 1].
    For team rows, the closest analogue per market:
      - h2h:        win_rate_l5/l10/season
      - spread:     spread_cover_rate_l10 → l10 only
      - game_total: ou_hit_rate_l10 → l10 only
      - team_total: None (no per-line team_total prior yet)
    """
    if not team_features:
        return {"hit_rate_l5": None, "hit_rate_l10": None, "hit_rate_l20": None}
    mc = (market_category or "").lower()
    if mc == "h2h":
        return {
            "hit_rate_l5":  team_features.get("win_rate_l5"),
            "hit_rate_l10": team_features.get("win_rate_l10"),
            "hit_rate_l20": team_features.get("win_rate_season"),
        }
    if mc == "spread":
        return {
            "hit_rate_l5":  None,
            "hit_rate_l10": team_features.get("spread_cover_rate_l10"),
            "hit_rate_l20": None,
        }
    if mc == "game_total":
        return {
            "hit_rate_l5":  None,
            "hit_rate_l10": team_features.get("ou_hit_rate_l10"),
            "hit_rate_l20": None,
        }
    return {"hit_rate_l5": None, "hit_rate_l10": None, "hit_rate_l20": None}


def _lookup_odds_api_implied(
    odds_api_cache: Dict, team_id, opponent_team_id, game_date, side, market_category,
    *, event_id: str = None, event_team_cache: Dict = None, prop_line: float = None,
):
    """Dict lookup into a pre-loaded cache keyed by (game_date, home_team, away_team).

    When team_id is "game" (or otherwise unmapped), falls back to
    event_team_cache[event_id] to resolve home/away names.
    For game_total the OVER/UNDER side logic needs no is_home; for h2h/spread
    with an unmapped team_id we return None.

    When prop_line is provided, checks alternate_totals_books (game_total) or
    alternate_spreads_books (spread) first; falls back to consensus fields.
    """
    mc = (market_category or "").lower()
    if mc == "team_total":
        return None
    team_name = TEAM_ID_TO_NAME.get(team_id)
    opp_name = TEAM_ID_TO_NAME.get(opponent_team_id)
    if not team_name or not opp_name:
        if event_id and event_team_cache:
            pair = event_team_cache.get(event_id)
            if pair and mc == "game_total":
                ht, at = pair
                doc = odds_api_cache.get((game_date, ht, at)) or odds_api_cache.get((game_date, at, ht))
                if doc:
                    if prop_line is not None:
                        alt_books = doc.get("alternate_totals_books") or {}
                        imp_key = "over_implied" if (side or "").upper() == "OVER" else "under_implied"
                        for book in _ODDS_BOOK_PRIORITY:
                            for entry in (alt_books.get(book) or []):
                                if entry.get("line") == prop_line:
                                    val = entry.get(imp_key)
                                    if val is not None:
                                        return val
                    return doc.get("consensus_over_implied") if (side or "").upper() == "OVER" else doc.get("consensus_under_implied")
        return None
    doc = (
        odds_api_cache.get((game_date, team_name, opp_name))
        or odds_api_cache.get((game_date, opp_name, team_name))
    )
    if not doc:
        return None
    if mc == "game_total":
        if prop_line is not None:
            alt_books = doc.get("alternate_totals_books") or {}
            imp_key = "over_implied" if (side or "").upper() == "OVER" else "under_implied"
            for book in _ODDS_BOOK_PRIORITY:
                for entry in (alt_books.get(book) or []):
                    if entry.get("line") == prop_line:
                        val = entry.get(imp_key)
                        if val is not None:
                            return val
        return doc.get("consensus_over_implied") if (side or "").upper() == "OVER" else doc.get("consensus_under_implied")
    is_home = doc["home_team"] == team_name
    if mc == "spread":
        if prop_line is not None:
            alt_books = doc.get("alternate_spreads_books") or {}
            imp_key = "home_spread_implied" if is_home else "away_spread_implied"
            for book in _ODDS_BOOK_PRIORITY:
                for entry in (alt_books.get(book) or []):
                    if entry.get("line") == prop_line:
                        val = entry.get(imp_key)
                        if val is not None:
                            return val
        return doc.get("consensus_home_spread_implied") if is_home else doc.get("consensus_away_spread_implied")
    # h2h (default)
    return doc.get("consensus_home_implied") if is_home else doc.get("consensus_away_implied")


_ODDS_BOOK_PRIORITY = [
    "draftkings", "fanduel", "betmgm", "betrivers", "fanatics", "lowvig", "betonlineag",
]


def _lookup_odds_api_odds(
    odds_api_cache: Dict, team_id, opponent_team_id, game_date, side, market_category,
    *, event_id: str = None, event_team_cache: Dict = None, prop_line: float = None,
) -> Optional[int]:
    """Return a clean American odds integer from the Odds API cache using book priority.

    Falls back to event_team_cache[event_id] when team_id is unmapped ("game");
    only game_total is resolved via that path (h2h/spread need a known side).

    When prop_line is provided, checks alternate_totals_books (game_total) or
    alternate_spreads_books (spread) before the standard book dicts.
    """
    mc = (market_category or "").lower()
    if mc == "team_total":
        return None
    team_name = TEAM_ID_TO_NAME.get(team_id)
    opp_name = TEAM_ID_TO_NAME.get(opponent_team_id)
    if not team_name or not opp_name:
        if event_id and event_team_cache:
            pair = event_team_cache.get(event_id)
            if pair and mc == "game_total":
                ht, at = pair
                doc = odds_api_cache.get((game_date, ht, at)) or odds_api_cache.get((game_date, at, ht))
                if doc:
                    key = "over_odds" if (side or "").upper() == "OVER" else "under_odds"
                    if prop_line is not None:
                        alt_books = doc.get("alternate_totals_books") or {}
                        for book in _ODDS_BOOK_PRIORITY:
                            for entry in (alt_books.get(book) or []):
                                if entry.get("line") == prop_line:
                                    val = entry.get(key)
                                    if val is not None:
                                        return val
                    totals_books = doc.get("totals_books") or {}
                    for book in _ODDS_BOOK_PRIORITY:
                        val = (totals_books.get(book) or {}).get(key)
                        if val is not None:
                            return val
        return None
    doc = (
        odds_api_cache.get((game_date, team_name, opp_name))
        or odds_api_cache.get((game_date, opp_name, team_name))
    )
    if not doc:
        return None
    is_home = doc.get("home_team") == team_name
    if mc == "h2h":
        key = "home_odds_american" if is_home else "away_odds_american"
        books = doc.get("books") or {}
        for book in _ODDS_BOOK_PRIORITY:
            val = (books.get(book) or {}).get(key)
            if val is not None:
                return val
    elif mc == "spread":
        key = "home_spread_odds" if is_home else "away_spread_odds"
        if prop_line is not None:
            alt_books = doc.get("alternate_spreads_books") or {}
            for book in _ODDS_BOOK_PRIORITY:
                for entry in (alt_books.get(book) or []):
                    if entry.get("line") == prop_line:
                        val = entry.get(key)
                        if val is not None:
                            return val
        spread_books = doc.get("spread_books") or {}
        for book in _ODDS_BOOK_PRIORITY:
            val = (spread_books.get(book) or {}).get(key)
            if val is not None:
                return val
    elif mc == "game_total":
        key = "over_odds" if (side or "").upper() == "OVER" else "under_odds"
        if prop_line is not None:
            alt_books = doc.get("alternate_totals_books") or {}
            for book in _ODDS_BOOK_PRIORITY:
                for entry in (alt_books.get(book) or []):
                    if entry.get("line") == prop_line:
                        val = entry.get(key)
                        if val is not None:
                            return val
        if prop_line is not None:
            return None
        totals_books = doc.get("totals_books") or {}
        for book in _ODDS_BOOK_PRIORITY:
            val = (totals_books.get(book) or {}).get(key)
            if val is not None:
                return val
    return None


async def assemble_replay_row(
    prop: Dict[str, Any],
    *, model_score: Optional[Dict[str, Any]] = None,
    odds_api_cache: Optional[Dict] = None,
    event_team_cache: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Translate one `team_model_prop_features` doc into one replay row
    matching the player schema. Pure function — easy to unit-test.

    If `model_score` is provided (from `score_team_props_batch` or
    `score_team_prop`), populates `model_probability`, `edge`, etc.
    If not, the function calls the single-row scorer itself so callers
    using the legacy 1-arg API still get scoring (the orchestrator
    now uses the batch path for speed).

    `odds_api_cache` is a pre-loaded dict keyed by
    (game_date, home_team, away_team) built once in `reshape_sport`."""
    sport = prop.get("sport") or ""
    tf = prop.get("team_features") or None
    hrs = project_hit_rates_from_team_features(
        prop.get("market_category"), tf)
    cv_team = (tf or {}).get("cv_points_scored")
    bucket = _odds_bucket(prop.get("odds"))
    tier = tier_for_odds_bucket(bucket)

    # If caller already batch-scored, skip the single-row call.
    if model_score is None:
        try:
            from services.team_xgb_loader import score_team_prop
            model_score = score_team_prop(prop)
        except Exception:
            model_score = None

    model_prob   = (model_score or {}).get("model_probability")
    implied_prob = (model_score or {}).get("implied_probability")
    eid = prop.get("event_id")
    if odds_api_cache is not None:
        odds_api_implied = _lookup_odds_api_implied(
            odds_api_cache, prop.get("team_id"), prop.get("opponent_team_id"),
            prop.get("game_date"), prop.get("side"), prop.get("market_category"),
            event_id=eid, event_team_cache=event_team_cache,
            prop_line=prop.get("line"),
        )
        if odds_api_implied is not None:
            implied_prob = odds_api_implied
    odds_api_odds = _lookup_odds_api_odds(
        odds_api_cache, prop.get("team_id"), prop.get("opponent_team_id"),
        prop.get("game_date"), prop.get("side"), prop.get("market_category"),
        event_id=eid, event_team_cache=event_team_cache,
        prop_line=prop.get("line"),
    ) if odds_api_cache is not None else None
    edge         = (model_score or {}).get("edge")
    vision_score = (model_score or {}).get("vision_score")
    model_ver    = (model_score or {}).get("model_version")

    # Gate reasons — per-row diagnostic. Today we emit an empty list
    # when the model scored the row, or a single non-fatal note when
    # it did not (no model available for that sport/market_category).
    gate_reasons: List[str] = ([] if model_score is not None
                                  else ["no_team_xgb_model_for_market"])

    # Tier-pass booleans. We rely on the optimizer's odds-bucket tier
    # router rather than the player gates (per operator: tier routing
    # only). Every row gets exactly ONE tier_pass=True, matching the
    # bucket it routes to.
    sh_pass = (tier == "safe_haven")
    fl_pass = (tier == "front_lines")
    wz_pass = (tier == "war_zone")

    return {
        # identity
        "event_id":               prop.get("event_id"),
        "league_id":              sport.upper(),
        "sport":                  sport,
        "prop_type":              "team",
        "team_id":                prop.get("team_id"),
        "opponent_team_id":       prop.get("opponent_team_id"),
        "player_name":            None,
        "player_name_normalized": None,
        # bet
        "market":                 prop.get("market_key"),
        "market_key":             prop.get("market_key"),
        "market_category":        prop.get("market_category"),
        "market_name":            prop.get("market_name"),
        "stat_family":            prop.get("market_category"),
        "stat_id":                prop.get("statID"),
        "side":                   prop.get("side"),
        "sideID":                 prop.get("sideID"),
        "line":                   prop.get("line"),
        "book":                   prop.get("book"),
        "odds":                   prop.get("odds"),
        "clean_odds":             odds_api_odds,
        "odds_bucket":            bucket,
        "is_alternate":           prop.get("is_alternate"),
        "is_alternate_market":    prop.get("is_alternate"),
        "period_id":              prop.get("periodID"),
        # priors (from rolling features)
        "cv":                     cv_team,
        "hit_rate_l5":            hrs["hit_rate_l5"],
        "hit_rate_l10":           hrs["hit_rate_l10"],
        "hit_rate_l20":           hrs["hit_rate_l20"],
        # scoring (from trained XGB model)
        "tp":                     model_prob,
        "edge":                   edge,
        "model_probability":      model_prob,
        "implied_probability":    implied_prob,
        "fair_probability":       model_prob,
        "vision_score":           vision_score,
        "model_version":          model_ver,
        # tiers (odds-bucket-based routing)
        "tier":                   tier,
        "selected_tier":          tier,
        "safe_haven_pass":        sh_pass,
        "front_lines_pass":       fl_pass,
        "war_zone_pass":          wz_pass,
        "safe_haven_failed_reasons":  ([] if sh_pass else ["tier_route"]),
        "front_lines_failed_reasons": ([] if fl_pass else ["tier_route"]),
        "war_zone_failed_reasons":    ([] if wz_pass else ["tier_route"]),
        "gate_reasons":           gate_reasons,
        # outcome (already graded into the prop row by Phase 1)
        "outcome_resolved": bool(prop.get("outcome_resolved")),
        "outcome_numeric":  prop.get("outcome_numeric"),
        "hit":              prop.get("hit"),
        # provenance
        "pipeline_version": PIPELINE_VERSION,
        "ssot_source":      SSOT_SOURCE,
        "scored_at":        datetime.now(timezone.utc),
        "as_of_date":       prop.get("game_date"),
        "game_date":        prop.get("game_date"),
    }


def upsert_filter(row: Dict[str, Any]) -> Dict[str, Any]:
    """Composite upsert key for team rows. Distinct from the player
    row's filter (uses `team_id` in lieu of `player_name_normalized`)
    so team upserts never collide with player upserts and vice versa.
    Includes `prop_type="team"` so the partial-filter index can serve
    the lookup (otherwise upserts fall back to a collection scan and
    grind to 60 docs/sec on a non-empty replay collection).
    Pure function."""
    return {
        "prop_type":        "team",
        "event_id":         row["event_id"],
        "team_id":          row["team_id"],
        "market":           row["market"],
        "line":             row["line"],
        "side":             row["side"],
        "book":             row["book"],
        "pipeline_version": row["pipeline_version"],
    }


# ───── one-time dirty-data migration ─────
async def _archive_dirty(db: AsyncIOMotorDatabase) -> None:
    """Move all team rows that have no clean_odds into the archive collection,
    then delete them from the live replay collection.  One-time operation;
    idempotent (re-running finds 0 docs and exits early)."""
    print(f"\n[--archive-dirty] archiving prop_type=team rows with clean_odds=null …")
    query = {"prop_type": "team", "clean_odds": None}
    dirty_docs = await db[DST_COLL].find(query).to_list(length=None)
    if not dirty_docs:
        print("  nothing to archive — 0 docs matched.")
        return
    for doc in dirty_docs:
        doc.pop("_id", None)
    res = await db[ARCHIVE_COLL].insert_many(dirty_docs, ordered=False)
    print(f"  archived {len(res.inserted_ids):,} docs → {ARCHIVE_COLL}")
    del_res = await db[DST_COLL].delete_many(query)
    print(f"  deleted  {del_res.deleted_count:,} docs from {DST_COLL}")


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Tolerant index creation — same pattern as the rest of SGO scripts."""
    try:
        await db[DST_COLL].create_index(
            [("event_id", pymongo.ASCENDING),
              ("team_id",  pymongo.ASCENDING),
              ("market",   pymongo.ASCENDING),
              ("line",     pymongo.ASCENDING),
              ("side",     pymongo.ASCENDING),
              ("book",     pymongo.ASCENDING),
              ("pipeline_version", pymongo.ASCENDING)],
            name="team_upsert_key",
            partialFilterExpression={"prop_type": "team"},
        )
        await db[DST_COLL].create_index(
            [("prop_type", pymongo.ASCENDING),
              ("league_id", pymongo.ASCENDING),
              ("game_date", pymongo.ASCENDING),
              ("stat_family", pymongo.ASCENDING)],
            name="prop_type_query",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


async def reshape_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, dry_run: bool,
    max_props: int, bulk_chunk: int,
) -> Dict[str, Any]:
    print(f"\n  [{sport.upper()}] reshape team props → {DST_COLL}")
    n_total = await db[SRC_COLL].count_documents({"sport": sport})
    print(f"  [{sport.upper()}] source rows: {n_total:,}")
    if not dry_run:
        await _ensure_indexes(db)

    counters = {
        "scanned":               0,
        "rows_emitted":          0,
        "rows_written":          0,
        "missing_event_id":      0,
        "missing_team_id":       0,
        "skipped_no_clean_odds": 0,
        "scored":                0,
        "unscored":              0,
        "dry_run":               dry_run,
    }
    sample_rows: List[Dict[str, Any]] = []
    pending_props: List[Dict[str, Any]] = []   # raw prop docs awaiting batch-score
    pending_ops:   List[UpdateOne] = []

    # Pre-load the entire odds_api_team_h2h collection once so
    # _lookup_odds_api_implied can do a plain dict lookup per row
    # instead of a MongoDB find_one (eliminates ~7 k round-trips/chunk).
    print(f"  [{sport.upper()}] loading odds_api_team_h2h cache …")
    odds_api_cache: Dict[tuple, Any] = {}
    async for doc in db["odds_api_team_h2h"].find(
        {},
        {"game_date": 1, "home_team": 1, "away_team": 1,
         "consensus_home_implied": 1, "consensus_away_implied": 1,
         "consensus_over_implied": 1, "consensus_under_implied": 1,
         "consensus_home_spread_implied": 1, "consensus_away_spread_implied": 1,
         "books": 1, "spread_books": 1, "totals_books": 1,
         "alternate_totals_books": 1, "alternate_spreads_books": 1},
    ):
        odds_api_cache[(doc["game_date"], doc["home_team"], doc["away_team"])] = doc
    print(f"  [{sport.upper()}] odds_api_team_h2h cache: {len(odds_api_cache):,} entries")

    # event_id → (home_team_name_lower, away_team_name_lower)
    event_team_cache: Dict[str, tuple] = {}
    async for doc in db["team_matchups"].find(
        {"sport": sport},
        {"event_id": 1, "home_team_name": 1, "away_team_name": 1, "_id": 0},
    ):
        eid = doc.get("event_id")
        ht = (doc.get("home_team_name") or "").lower()
        at = (doc.get("away_team_name") or "").lower()
        if eid and ht and at:
            event_team_cache[eid] = (ht, at)
    print(f"  [{sport.upper()}] event_team_cache: {len(event_team_cache):,} entries")

    # Lazy-import the batch scorer so the unit tests stay fast.
    try:
        from services.team_xgb_loader import score_team_props_batch
    except Exception:
        score_team_props_batch = None

    async def _flush() -> None:
        if not pending_props:
            return
        # Batch-score the whole pending buffer in one shot (groups by
        # (sport, mc) internally — typically 1-2 predict_proba calls
        # per flush vs `len(pending_props)` in the unbatched path).
        scores = (score_team_props_batch(pending_props)
                  if score_team_props_batch else [None] * len(pending_props))
        for prop, score in zip(pending_props, scores):
            row = await assemble_replay_row(prop, model_score=score,
                                            odds_api_cache=odds_api_cache,
                                            event_team_cache=event_team_cache)
            counters["scored" if score is not None else "unscored"] += 1
            if len(sample_rows) < 5 and score is not None:
                sample_rows.append({
                    "league_id": row["league_id"], "team_id": row["team_id"],
                    "stat_family": row["stat_family"], "side": row["side"],
                    "line": row["line"], "book": row["book"],
                    "odds": row["odds"],
                    "odds_bucket": row["odds_bucket"],
                    "tier": row["tier"],
                    "tp": row["tp"], "edge": row["edge"],
                    "vision": row["vision_score"],
                    "hr_l10": row["hit_rate_l10"],
                    "cv": row["cv"],
                    "hit": row["hit"],
                })
            # Only write rows with clean Odds API odds — skip all other books
            if row.get("clean_odds") is None:
                counters["skipped_no_clean_odds"] = counters.get("skipped_no_clean_odds", 0) + 1
                continue
            pending_ops.append(UpdateOne(upsert_filter(row),
                                              {"$set": row}, upsert=True))
        pending_props.clear()
        if dry_run:
            counters["rows_emitted"] += len(pending_ops)
            pending_ops.clear()
            return
        if not pending_ops:
            pending_ops.clear()
            return
        res = await db[DST_COLL].bulk_write(pending_ops, ordered=False)
        counters["rows_emitted"] += len(pending_ops)
        counters["rows_written"] += ((res.upserted_count or 0)
                                          + (res.modified_count or 0))
        pending_ops.clear()

    cursor = db[SRC_COLL].find({"sport": sport}).batch_size(2000)
    async for p in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_props:
            print(f"  [{sport.upper()}] hit --max-props={max_props}; "
                  f"stopping early.")
            break
        if not p.get("event_id"):
            counters["missing_event_id"] += 1
            continue
        if not p.get("team_id"):
            counters["missing_team_id"] += 1
            continue
        if p.get("book") not in REPLAY_BOOK_WHITELIST_PHASE1:
            continue
        pending_props.append(p)
        if len(pending_props) >= bulk_chunk:
            await _flush()
            if counters["scanned"] % 20000 == 0:
                print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                      f"written={counters['rows_written']:,}  "
                      f"scored={counters['scored']:,}")
    await _flush()
    return {"sport": sport, "coll": DST_COLL,
              "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} TEAM-PROP RESHAPE SUMMARY ──")
    print(f"     scanned:              {c['scanned']:,}")
    print(f"     missing event_id:     {c['missing_event_id']:,}")
    print(f"     missing team_id:      {c['missing_team_id']:,}")
    print(f"     skipped (no clean_odds): {c.get('skipped_no_clean_odds', 0):,}")
    print(f"     rows emitted:         {c['rows_emitted']:,}")
    print(f"     rows written/changed: {c['rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            print(f"        {s['league_id']} {s['team_id']:<10s} "
                  f"{s['stat_family']:<11s} side={s['side']:<6s} "
                  f"line={s['line']!s:<6s} book={s['book']:<10s} "
                  f"odds={s['odds']!s:<7s} bucket={s['odds_bucket']:<14s} "
                  f"hr_l10={s['hr_l10']}  cv={s['cv']}  hit={s['hit']!s}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"reshape_team_props_to_replay  pipeline_version={PIPELINE_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}  "
          f"max_props={args.max_props}  bulk_chunk={args.bulk_chunk}")
    print(f"  CONTRACT: idempotent upserts into {DST_COLL} with "
          "prop_type='team'. Player rows untouched.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        if getattr(args, "archive_dirty", False):
            await _archive_dirty(db)
        for sp in sports:
            r = await reshape_sport(
                db, sport=sp, dry_run=dry_run,
                max_props=args.max_props, bulk_chunk=args.bulk_chunk)
            _print_summary(r)
        if dry_run:
            print("\n  DRY-RUN — no writes. Re-run without --dry-run to persist.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                    default="all")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--archive-dirty", action="store_true",
                   help="Before reshaping, move existing team rows with no "
                        "clean_odds into the archive collection and delete "
                        "them from the live replay collection.")
    p.add_argument("--max-props", type=int, default=10_000_000)
    p.add_argument("--bulk-chunk", type=int, default=1000)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    rc = main()
    from scripts.sgo.handoff import update_handoff
    update_handoff()
    raise SystemExit(rc)
