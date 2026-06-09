"""
Ingest MLB team season stats from BallDontLie into bdl_mlb_team_season_stats.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import pymongo
import requests

BDL_BASE = "https://api.balldontlie.io/mlb/v1"
COLLECTION = "bdl_mlb_team_season_stats"


def fetch_season(api_key: str, season: int, season_type: str = "regular") -> list[dict]:
    headers = {"Authorization": api_key}
    params = {"season": season, "season_type": season_type, "per_page": 100}
    records = []

    while True:
        resp = requests.get(f"{BDL_BASE}/teams/season_stats", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        records.extend(body.get("data", []))

        cursor = body.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor

    return records


def upsert_records(coll, records: list[dict], season: int, season_type: str) -> int:
    if not records:
        return 0

    now = datetime.now(timezone.utc)
    ops = []
    for doc in records:
        team_id = doc.get("team", {}).get("id") if isinstance(doc.get("team"), dict) else doc.get("team_id")
        if team_id is None:
            continue
        ops.append(
            pymongo.UpdateOne(
                {"team_id": team_id, "season": season, "season_type": season_type},
                {"$set": {**doc, "team_id": team_id, "season": season, "season_type": season_type, "ingested_at": now}},
                upsert=True,
            )
        )

    if not ops:
        return 0

    result = coll.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def main():
    parser = argparse.ArgumentParser(description="Ingest MLB team season stats from BDL")
    parser.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    args = parser.parse_args()

    api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
    if not api_key:
        print("ERROR: BDL_API_KEY or BALLDONTLIE_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    client = pymongo.MongoClient(mongo_url)
    coll = client[db_name][COLLECTION]

    coll.create_index(
        [("team_id", pymongo.ASCENDING), ("season", pymongo.ASCENDING), ("season_type", pymongo.ASCENDING)],
        unique=True,
        background=True,
    )

    season_type = "regular"
    print(f"Ingesting seasons: {args.seasons}")

    for season in args.seasons:
        try:
            records = fetch_season(api_key, season, season_type)
            count = upsert_records(coll, records, season, season_type)
            print(f"  {season}: {len(records)} teams fetched, {count} upserted/updated")
        except requests.HTTPError as exc:
            print(f"  {season}: HTTP error — {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"  {season}: {exc}", file=sys.stderr)

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
