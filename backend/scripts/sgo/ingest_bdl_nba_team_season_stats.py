"""
Ingest NBA team season stats from BallDontLie into bdl_nba_team_season_stats.

Fetches 6 general stat types (base, advanced, scoring, misc, opponent, defense)
plus hustle stats, merges them into one flat document per team per season with
per-type field prefixes, and upserts into MongoDB.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import pymongo
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from scripts.sgo import get_bdl_team_mapping  # noqa: E402

BDL_BASE = "https://api.balldontlie.io/nba/v1"
COLLECTION = "bdl_nba_team_season_stats"

GENERAL_STAT_TYPES = ["base", "advanced", "scoring", "misc", "opponent", "defense"]


def _fetch_page(url: str, headers: dict, params: dict) -> dict:
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_general_stat_type(
    api_key: str, season: int, season_type: str, stat_type: str
) -> dict[int, dict]:
    """Return {team_id: raw_row} for one stat type."""
    headers = {"Authorization": api_key}
    params = {
        "season": season,
        "season_type": season_type,
        "type": stat_type,
        "per_page": 30,
    }
    team_stats: dict[int, dict] = {}

    while True:
        body = _fetch_page(f"{BDL_BASE}/team_season_averages/general", headers, params)
        for row in body.get("data", []):
            team_id = (row.get("team") or {}).get("id")
            if team_id is None:
                continue
            team_stats[team_id] = row

        cursor = body.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor

    return team_stats


def fetch_hustle_stats(
    api_key: str, season: int, season_type: str
) -> dict[int, dict]:
    """Return {team_id: raw_row} for hustle stats."""
    headers = {"Authorization": api_key}
    params = {"season": season, "season_type": season_type, "per_page": 30}
    team_stats: dict[int, dict] = {}

    while True:
        body = _fetch_page(f"{BDL_BASE}/team_season_averages/hustle", headers, params)
        for row in body.get("data", []):
            team_id = (row.get("team") or {}).get("id")
            if team_id is None:
                continue
            team_stats[team_id] = row

        cursor = body.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor

    return team_stats


def build_season_docs(
    api_key: str,
    season: int,
    season_type: str,
    nba_team_mapping: dict[int, str],
) -> list[dict]:
    """Merge all stat types into one flat document per team."""
    merged: dict[int, dict] = {}

    for stat_type in GENERAL_STAT_TYPES:
        type_data = fetch_general_stat_type(api_key, season, season_type, stat_type)
        for team_id, stat_row in type_data.items():
            doc = merged.setdefault(team_id, {"team": stat_row.get("team") or {}})
            for key, val in stat_row.get("stats", {}).items():
                if not key.endswith("_rank"):
                    doc[f"{stat_type}_{key}"] = val

    hustle_data = fetch_hustle_stats(api_key, season, season_type)
    for team_id, stat_row in hustle_data.items():
        doc = merged.setdefault(team_id, {"team": stat_row.get("team") or {}})
        for key, val in stat_row.get("stats", {}).items():
            if not key.endswith("_rank"):
                doc[f"hustle_{key}"] = val

    now = datetime.now(timezone.utc)
    docs = []
    for team_id, fields in merged.items():
        doc = {
            "team_id": team_id,
            "season": season,
            "season_type": season_type,
            "team": fields.pop("team", {}),
            "ingested_at": now,
        }
        nba_team_id = nba_team_mapping.get(team_id)
        if nba_team_id:
            doc["nba_team_id"] = nba_team_id
        doc.update(fields)
        docs.append(doc)

    return docs


def upsert_docs(coll, docs: list[dict]) -> int:
    if not docs:
        return 0

    ops = [
        pymongo.UpdateOne(
            {
                "team_id": doc["team_id"],
                "season": doc["season"],
                "season_type": doc["season_type"],
            },
            {"$set": doc},
            upsert=True,
        )
        for doc in docs
    ]

    result = coll.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def main():
    parser = argparse.ArgumentParser(
        description="Ingest NBA team season stats from BDL"
    )
    parser.add_argument("--seasons", nargs="+", type=int, default=[2023, 2024, 2025])
    args = parser.parse_args()

    api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
    if not api_key:
        print(
            "ERROR: BDL_API_KEY or BALLDONTLIE_API_KEY environment variable not set",
            file=sys.stderr,
        )
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    client = pymongo.MongoClient(mongo_url)
    coll = client[db_name][COLLECTION]

    coll.create_index(
        [
            ("team_id", pymongo.ASCENDING),
            ("season", pymongo.ASCENDING),
            ("season_type", pymongo.ASCENDING),
        ],
        unique=True,
        background=True,
    )

    nba_team_mapping = get_bdl_team_mapping("nba")
    season_type = "regular"
    print(f"Ingesting seasons: {args.seasons}")

    for season in args.seasons:
        try:
            docs = build_season_docs(api_key, season, season_type, nba_team_mapping)
            count = upsert_docs(coll, docs)
            print(f"  {season}: {len(docs)} teams merged, {count} upserted/updated")
        except requests.HTTPError as exc:
            print(f"  {season}: HTTP error — {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"  {season}: {exc}", file=sys.stderr)

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
