#!/usr/bin/env python3
"""
Post-ingestion migration: Create bdl_game_logs (mapped field names) + is_pitcher/is_batter
from the new history object, so existing endpoints work without code changes.
"""
from pymongo import MongoClient, UpdateOne
import time, os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

PITCHER_POSITIONS = {"pitcher", "relief pitcher", "starting pitcher", "closer", "rp", "sp", "p"}

FIELD_MAP = {
    "rbi": "rbis",
    "hr": "home_runs",
    "bb": "walks",
    "k": "strikeouts",
    "ip": "innings_pitched",
    "p_k": "pitcher_strikeouts",
    "p_bb": "pitcher_walks",
    "p_hits": "hits_allowed",
    "er": "earned_runs",
    "p_runs": "runs_allowed",
    "p_hr": "home_runs_allowed",
}


def map_game_log(raw, season_year):
    """Map BDL raw stat entry to the format endpoints expect."""
    mapped = {
        "game_id": raw.get("game_id"),
        "team_name": raw.get("team_name", ""),
        "season": season_year,
        # Batting - direct pass-through
        "at_bats": raw.get("at_bats"),
        "hits": raw.get("hits"),
        "runs": raw.get("runs"),
        "doubles": raw.get("doubles"),
        "triples": raw.get("triples"),
        "stolen_bases": raw.get("stolen_bases"),
        "caught_stealing": raw.get("caught_stealing"),
        "plate_appearances": raw.get("plate_appearances"),
        "total_bases": raw.get("total_bases"),
        "avg": raw.get("avg"),
        "obp": raw.get("obp"),
        "slg": raw.get("slg"),
        "singles": (raw.get("hits") or 0) - (raw.get("doubles") or 0) - (raw.get("triples") or 0) - (raw.get("hr") or 0) if raw.get("hits") else None,
        # Mapped fields
        "rbis": raw.get("rbi"),
        "home_runs": raw.get("hr"),
        "walks": raw.get("bb"),
        "strikeouts": raw.get("k"),
        # Pitcher mapped
        "innings_pitched": raw.get("ip"),
        "pitcher_strikeouts": raw.get("p_k"),
        "pitcher_walks": raw.get("p_bb"),
        "hits_allowed": raw.get("p_hits"),
        "earned_runs": raw.get("er"),
        "runs_allowed": raw.get("p_runs"),
        "home_runs_allowed": raw.get("p_hr"),
        "pitch_count": raw.get("pitch_count"),
        "era": raw.get("era"),
        "batters_faced": raw.get("batters_faced"),
        "pitching_outs": raw.get("pitching_outs"),
    }
    return mapped


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.mlb_master_hub_2026

    total = hub.count_documents({})
    print(f"[MIGRATE] Processing {total} documents...")

    batch_ops = []
    processed = 0
    start = time.time()

    for doc in hub.find({"history": {"$exists": True}}, {"_id": 1, "history": 1, "position": 1}):
        history = doc.get("history", {})
        position = (doc.get("position") or "").lower()

        is_pitcher = position in PITCHER_POSITIONS
        is_batter = not is_pitcher

        all_logs = []
        for season_key in ["2023_season", "2024_season", "2025_season"]:
            year = int(season_key.split("_")[0])
            for raw in history.get(season_key, []):
                all_logs.append(map_game_log(raw, year))

        # Sort by game_id descending (most recent first)
        all_logs.sort(key=lambda x: x.get("game_id") or 0, reverse=True)

        batch_ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "bdl_game_logs": all_logs,
                "is_pitcher": is_pitcher,
                "is_batter": is_batter,
            }}
        ))

        processed += 1
        if len(batch_ops) >= 100:
            hub.bulk_write(batch_ops, ordered=False)
            elapsed = time.time() - start
            print(f"[MIGRATE] {processed}/{total} | {elapsed:.1f}s")
            batch_ops = []

    if batch_ops:
        hub.bulk_write(batch_ops, ordered=False)

    elapsed = time.time() - start
    # Verify
    with_logs = hub.count_documents({"bdl_game_logs": {"$exists": True, "$ne": []}})
    pitchers = hub.count_documents({"is_pitcher": True})
    batters = hub.count_documents({"is_batter": True})
    print(f"\n[MIGRATE COMPLETE] {elapsed:.1f}s")
    print(f"  Docs with bdl_game_logs: {with_logs}")
    print(f"  Pitchers: {pitchers}")
    print(f"  Batters: {batters}")

    client.close()


if __name__ == "__main__":
    main()
