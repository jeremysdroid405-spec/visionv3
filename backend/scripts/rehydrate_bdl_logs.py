"""
MLB BDL Game-Log Rehydration
============================
Restores multi-season historical game logs into
`mlb_master_hub_2026.bdl_game_logs`. Append-only, idempotent
(dedupes on `game_id`).

Workflow:
  1. For each target season (2023, 2024, 2025): fetch all MLB games
     (game_id → date / home / away / venue) into an in-memory cache.
  2. For the same season: paginate through /mlb/v1/stats and group
     rows by `player.id` (= `bdl_id`).
  3. Per hub player: build new log dicts in the hub's existing
     `bdl_game_logs` schema, merge with existing logs deduping on
     `game_id` (prefer existing on conflict), sort ascending by
     `date`, and write back.
  4. Stamp `bdl_logs_rehydrated = True` on the player doc.

Safety:
  - Never overwrites the master_hub array — it always merges.
  - If a player has no historical data, skipped silently.
  - On API error, logs and continues.
  - Reruns are safe (no duplicate game_ids).

Run:
  cd /app/backend && python3 scripts/rehydrate_bdl_logs.py
  cd /app/backend && python3 scripts/rehydrate_bdl_logs.py --seasons 2024,2025
  cd /app/backend && python3 scripts/rehydrate_bdl_logs.py --dry-run
"""
from __future__ import annotations
import argparse, asyncio, logging, os, statistics, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rehydrate_bdl")

BDL_API_KEY = os.environ["BDL_API_KEY"]
BDL_MLB_BASE = "https://api.balldontlie.io/mlb/v1"
DEFAULT_SEASONS = [2023, 2024, 2025]
RATE_LIMIT = 0.35
MAX_RETRIES = 4
PER_PAGE = 100


# ---------------------------------------------------------------------------
async def _api_get(client: httpx.AsyncClient, endpoint: str,
                    params: Dict[str, Any]) -> Optional[Dict]:
    """GET with retries + 429 backoff."""
    url = f"{BDL_MLB_BASE}{endpoint}"
    for attempt in range(MAX_RETRIES):
        try:
            await asyncio.sleep(RATE_LIMIT)
            r = await client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                log.warning(f"[BDL] 429 — waiting {wait}s …")
                await asyncio.sleep(wait); continue
            if r.status_code == 404:
                return None
            log.warning(f"[BDL] {endpoint} → {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.error(f"[BDL] error on {endpoint}: {e!r}")
            await asyncio.sleep(2)
    return None


async def _paginate(client: httpx.AsyncClient, endpoint: str,
                     params: Dict[str, Any], max_pages: int = 10000
                     ) -> List[Dict]:
    """Walk all pages via cursor pagination."""
    all_rows: List[Dict] = []
    cursor = None
    pages = 0
    while pages < max_pages:
        p = dict(params)
        if cursor is not None: p["cursor"] = cursor
        result = await _api_get(client, endpoint, p)
        if not result: break
        rows = result.get("data") or []
        if not rows: break
        all_rows.extend(rows)
        meta = result.get("meta") or {}
        cursor = meta.get("next_cursor")
        pages += 1
        if not cursor: break
        if pages % 25 == 0:
            log.info(f"  [{endpoint}] {pages} pages, {len(all_rows):,} rows so far")
    log.info(f"  [{endpoint}] DONE: {pages} pages, {len(all_rows):,} rows")
    return all_rows


# ---------------------------------------------------------------------------
async def fetch_games_cache(client: httpx.AsyncClient, season: int
                              ) -> Dict[int, Dict[str, Any]]:
    """game_id → {date, season, home_team, away_team, ...}."""
    log.info(f"[GAMES] season={season} — building game_id cache")
    rows = await _paginate(client, "/games",
                            {"seasons[]": season, "per_page": PER_PAGE},
                            max_pages=60)
    cache: Dict[int, Dict[str, Any]] = {}
    for g in rows:
        gid = g.get("id")
        if gid is None: continue
        cache[int(gid)] = g
    log.info(f"[GAMES] season={season} → {len(cache):,} games cached")
    return cache


async def fetch_season_stats(client: httpx.AsyncClient, season: int
                                ) -> List[Dict]:
    """All `/stats` rows for a season (one stat row per player per game)."""
    log.info(f"[STATS] season={season} — paginating /stats")
    rows = await _paginate(client, "/stats",
                            {"seasons[]": season, "per_page": PER_PAGE},
                            max_pages=10000)
    log.info(f"[STATS] season={season} → {len(rows):,} stat rows")
    return rows


# ---------------------------------------------------------------------------
def _row_to_log(row: Dict[str, Any], game_cache: Dict[int, Dict[str, Any]],
                  season: int) -> Optional[Dict[str, Any]]:
    """Convert a /stats row + game cache → a `bdl_game_logs` entry that
    matches the schema already on the hub."""
    gid = row.get("game_id")
    if gid is None: return None
    g = game_cache.get(int(gid)) or {}
    p = row.get("player") or {}
    bdl_id = p.get("id")
    if bdl_id is None: return None

    team_name = row.get("team_name")
    # Opponent abbreviation
    opp_abbr = None
    home = g.get("home_team") or {}
    away = g.get("away_team") or {}
    home_name = g.get("home_team_name") or home.get("display_name") or home.get("name")
    if team_name and home_name:
        if team_name == home_name:
            opp_abbr = away.get("abbreviation") or away.get("name_short")
        else:
            opp_abbr = home.get("abbreviation") or home.get("name_short")

    return {
        "game_id": int(gid),
        "date": g.get("date"),
        "season": season,
        "bdl_player_id": int(bdl_id),
        "player_name": (
            (p.get("first_name") or "") + " " + (p.get("last_name") or "")
        ).strip() or None,
        "team_name": team_name,
        "opponent_abbr": opp_abbr,
        "sport": "mlb",
        # Batting
        "at_bats":          row.get("at_bats"),
        "hits":             row.get("hits"),
        "runs":             row.get("runs"),
        "rbis":             row.get("rbi"),
        "home_runs":        row.get("hr"),
        "doubles":          row.get("doubles"),
        "triples":          row.get("triples"),
        "stolen_bases":     row.get("stolen_bases"),
        "walks":            row.get("bb"),
        "strikeouts":       row.get("k"),
        "total_bases":      row.get("total_bases"),
        "plate_appearances": row.get("plate_appearances"),
        "batting_avg":      row.get("avg"),
        "obp":              row.get("obp"),
        "slg":              row.get("slg"),
        # Pitching
        "innings_pitched":   row.get("ip"),
        "pitcher_strikeouts": row.get("p_k"),
        "pitcher_walks":     row.get("p_bb"),
        "hits_allowed":      row.get("p_hits"),
        "earned_runs":       row.get("er"),
        "era":               row.get("era"),
        "pitch_count":       row.get("pitch_count"),
        "wins":              row.get("wins"),
        "losses":            row.get("losses"),
        "saves":             row.get("saves"),
    }


def _date_key(log_dict: Dict[str, Any]) -> str:
    """Sort key: ISO date (UTC). Empty string sorts oldest."""
    d = log_dict.get("date")
    return str(d) if d else ""


def _merge_logs(existing: List[Dict], incoming: List[Dict]) -> List[Dict]:
    """Append-only merge keyed on game_id (preferring existing rows on
    conflict). Falls back to (date, team, opponent) when game_id is
    missing on either side."""
    by_gid: Dict[int, Dict] = {}
    no_gid: Dict[tuple, Dict] = {}
    for x in existing or []:
        gid = x.get("game_id")
        if gid is not None:
            by_gid[int(gid)] = x
        else:
            k = (x.get("date") or "", x.get("team_name") or "",
                 x.get("opponent_abbr") or "")
            no_gid[k] = x
    for x in incoming or []:
        gid = x.get("game_id")
        if gid is not None:
            if int(gid) not in by_gid:    # prefer existing on conflict
                by_gid[int(gid)] = x
            continue
        k = (x.get("date") or "", x.get("team_name") or "",
             x.get("opponent_abbr") or "")
        if k not in no_gid:
            no_gid[k] = x
    merged = list(by_gid.values()) + list(no_gid.values())
    merged.sort(key=_date_key)  # ascending
    return merged


# ---------------------------------------------------------------------------
async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default=",".join(str(s) for s in DEFAULT_SEASONS),
                     help="Comma-separated seasons to fetch (default 2023,2024,2025)")
    ap.add_argument("--dry-run", action="store_true",
                     help="Fetch + print plan, do not write to DB")
    ap.add_argument("--limit-players", type=int, default=0,
                     help="Cap players updated for testing (0=all)")
    args = ap.parse_args()

    seasons = [int(s) for s in args.seasons.split(",") if s.strip()]
    log.info(f"Seasons to fetch: {seasons}  dry={args.dry_run}")

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    hub = db.mlb_master_hub_2026

    # ---- BEFORE snapshot ----
    pre_counts = []
    async for d in hub.find({"bdl_id": {"$ne": None}},
                              {"_id": 0, "bdl_id": 1, "bdl_game_logs": 1}):
        pre_counts.append(len(d.get("bdl_game_logs") or []))
    pre_mean = round(statistics.mean(pre_counts), 1) if pre_counts else 0
    pre_max = max(pre_counts) if pre_counts else 0
    pre_min = min(pre_counts) if pre_counts else 0
    log.info(f"BEFORE: {len(pre_counts):,} players, "
              f"mean={pre_mean} median={statistics.median(pre_counts) if pre_counts else 0} "
              f"min={pre_min} max={pre_max}")

    # ---- Fetch all seasons ----
    t0 = time.time()
    by_player_logs: Dict[int, List[Dict]] = defaultdict(list)
    fetch_errors: int = 0

    async with httpx.AsyncClient(
            timeout=90.0, headers={"Authorization": BDL_API_KEY},
            limits=httpx.Limits(max_connections=8)) as client:
        for season in seasons:
            try:
                games = await fetch_games_cache(client, season)
                stats = await fetch_season_stats(client, season)
            except Exception as e:
                log.error(f"[FETCH] season {season} failed: {e!r}")
                fetch_errors += 1
                continue

            if not games or not stats:
                log.warning(f"[FETCH] season {season} — empty result, skip")
                continue

            for row in stats:
                lg = _row_to_log(row, games, season)
                if lg is not None:
                    by_player_logs[lg["bdl_player_id"]].append(lg)

            log.info(f"[FETCH] season={season} → "
                      f"{sum(1 for r in stats if r.get('player', {}).get('id'))} player-game rows mapped, "
                      f"{len(by_player_logs):,} unique players accumulated")

    fetch_dt = time.time() - t0
    log.info(f"Fetch complete in {fetch_dt:.1f}s — total players w/ new logs: {len(by_player_logs):,}")

    # ---- Merge + write per player ----
    log.info("Merging into master_hub …")
    bulk_ops: List[UpdateOne] = []
    n_processed = 0; n_updated = 0; n_skipped_no_data = 0
    post_counts: List[int] = []

    async for d in hub.find({"bdl_id": {"$ne": None}},
                              {"_id": 0, "bdl_id": 1,
                               "bdl_game_logs": 1, "player_name": 1}):
        bdl_id = d.get("bdl_id")
        try: bdl_id = int(bdl_id)
        except (TypeError, ValueError): continue
        n_processed += 1
        existing = d.get("bdl_game_logs") or []
        incoming = by_player_logs.get(bdl_id) or []
        if not incoming:
            post_counts.append(len(existing))
            n_skipped_no_data += 1
            continue
        merged = _merge_logs(existing, incoming)
        post_counts.append(len(merged))
        if len(merged) == len(existing):
            continue   # nothing new
        bulk_ops.append(UpdateOne(
            {"bdl_id": bdl_id},
            {"$set": {
                "bdl_game_logs": merged,
                "bdl_logs_rehydrated": True,
                "bdl_logs_rehydrated_at": datetime.now(timezone.utc).isoformat(),
                "bdl_game_logs_count": len(merged),
                "total_game_logs": max(d.get("total_game_logs", 0) or 0, len(merged)),
            }}
        ))
        n_updated += 1
        if args.limit_players and n_updated >= args.limit_players:
            log.info(f"--limit-players reached ({args.limit_players})")
            break

    if args.dry_run:
        log.info(f"[DRY RUN] would write {len(bulk_ops):,} updates — skipping bulk_write")
    elif bulk_ops:
        BATCH = 200
        for i in range(0, len(bulk_ops), BATCH):
            chunk = bulk_ops[i:i + BATCH]
            try:
                res = await hub.bulk_write(chunk, ordered=False)
                log.info(f"[WRITE] batch {i:4d}-{i+len(chunk):4d}: "
                          f"matched={res.matched_count} modified={res.modified_count}")
            except Exception as e:
                log.error(f"[WRITE] batch {i} failed: {e!r}")

    # ---- AFTER snapshot ----
    post_mean = round(statistics.mean(post_counts), 1) if post_counts else 0
    post_med  = round(statistics.median(post_counts), 1) if post_counts else 0
    post_max = max(post_counts) if post_counts else 0
    post_min = min(post_counts) if post_counts else 0

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "seasons": seasons,
        "dry_run": args.dry_run,
        "fetch_errors": fetch_errors,
        "players_processed": n_processed,
        "players_updated": n_updated,
        "players_no_new_data": n_skipped_no_data,
        "avg_logs_before": pre_mean,
        "median_logs_before": float(statistics.median(pre_counts)) if pre_counts else 0,
        "min_logs_before": pre_min,
        "max_logs_before": pre_max,
        "avg_logs_after":  post_mean,
        "median_logs_after": post_med,
        "min_logs_after": post_min,
        "max_logs_after": post_max,
        "fetch_seconds": round(fetch_dt, 1),
    }
    log.info("=" * 70)
    log.info("REHYDRATION SUMMARY")
    log.info("=" * 70)
    for k, v in summary.items():
        log.info(f"  {k}: {v}")
    log.info("=" * 70)

    # Persist summary alongside other snapshots
    out_dir = "/app/backend/data/snapshots"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir, f"bdl_rehydrate_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json")
    import json
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    log.info(f"Wrote summary → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
