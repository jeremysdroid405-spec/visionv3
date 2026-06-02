"""Test the snapshot_iso fallback policy in nba_replay_engine.

Replay contract: must NEVER silently score zero rows when the
orchestrator-supplied `snapshot_iso` doesn't match the ingested
data's snapshot label. Three fallback tiers:

  1. Exact `snapshot_iso` match.
  2. Latest snapshot for the `(sport, game_date)`.
  3. Any rows for the date (no snapshot constraint).

This test:
  • Seeds NBA odds rows under one snapshot_iso label
    (`2025-10-22T15:00:00Z`).
  • Calls `replay_date()` with a DIFFERENT requested snapshot
    (`2025-10-22T11:00:00Z` — would have returned 0 under the old
    over-constrained query).
  • Verifies the resolver falls back to `latest_for_date`,
    surfaces the resolution tier + telemetry on the summary, AND
    actually scores the data.
"""
from __future__ import annotations
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

GAME_DATE = "2025-10-22"
INGESTED_SNAPSHOT = f"{GAME_DATE}T15:00:00Z"   # actual ingest tag
REQUESTED_SNAPSHOT = f"{GAME_DATE}T11:00:00Z"  # orchestrator wishful tag
EVENT_ID = "nba_snapshot_fallback_test"
PLAYER = "Snapshot Fallback Player"
BDL_PID = 999997001


def _build_logs(end_iso: str, n: int = 20):
    end = datetime.fromisoformat(end_iso) - timedelta(days=1)
    return [{
        "game_id": 66000000 + i,
        "date": (end - timedelta(days=i + 1)).strftime("%Y-%m-%d"),
        "season": 2025, "bdl_player_id": BDL_PID,
        "pts": 18 + (i % 5) * 2, "reb": 4, "ast": 5, "fg3m": 2,
        "stl": 1, "blk": 0, "turnover": 2, "min": "32",
        "fgm": 7, "fga": 15, "fg3a": 5, "ftm": 4, "fta": 5,
        "oreb": 1, "dreb": 3, "pf": 2, "plus_minus": 0,
        "opponent_team_id": 10, "home_game": (i % 2 == 0),
    } for i in range(n)]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Cleanup any stale state.
    for coll in ("sgo_replay_alt_odds_raw", "nba_replay_model_outputs",
                 "nba_replay_model_status"):
        await db[coll].delete_many({"event_id": EVENT_ID})
    await db.nba_master_hub_2026.delete_one({"bdl_player_id": BDL_PID})
    await db.nba_master_hub_2026.insert_one({
        "display_name": PLAYER, "player_name": PLAYER,
        "bdl_id": BDL_PID, "bdl_player_id": BDL_PID,
        "bdl_game_logs_count": 20,
        "bdl_game_logs": _build_logs(GAME_DATE),
    })

    # Seed odds rows under INGESTED_SNAPSHOT — NOT under
    # REQUESTED_SNAPSHOT.
    base = {
        "sport": "nba", "league": "NBA",
        "sport_key": "basketball_nba",
        "game_date": GAME_DATE,
        "snapshot_iso": INGESTED_SNAPSHOT,
        "event_id": EVENT_ID,
        "home_team": "T1", "away_team": "T2",
        "commence_time": f"{GAME_DATE}T22:00:00Z",
        "market": "player_points", "stat": "player_points",
        "is_alternate": False,
        "player_name": PLAYER,
        "player_name_normalized": "snapshot fallback player",
        "line": 19.5,
        "anchor_book": "prizepicks",
        "available_books": ["prizepicks"],
        "playable_on_pp": True,
        "ingested_at": datetime.now(timezone.utc),
    }
    await db.sgo_replay_alt_odds_raw.insert_many([
        {**base, "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**base, "side": "UNDER", "odds": -118, "book": "prizepicks"},
        {**base, "side": "OVER",  "odds": -115, "book": "draftkings"},
        {**base, "side": "UNDER", "odds": -105, "book": "draftkings"},
    ])

    # ── Scenario A: Requested snapshot has NO rows; fallback engages.
    from services.replay.nba_replay_engine import replay_date

    summary_A = await replay_date(
        db, GAME_DATE,
        snapshot_iso=REQUESTED_SNAPSHOT,
        force=True,
        odds_collection="sgo_replay_alt_odds_raw",
    )

    print("=" * 78)
    print(f"  Scenario A — requested snapshot {REQUESTED_SNAPSHOT}")
    print(f"               actual ingest at   {INGESTED_SNAPSHOT}")
    print("-" * 78)
    for k in ("snapshot_iso", "snapshot_iso_resolved",
              "snapshot_resolution_tier",
              "snapshot_resolution_telemetry",
              "alt_odds_rows_seen", "props_built",
              "score_docs_returned", "model_outputs_written"):
        print(f"    {k:36s} = {summary_A.get(k)!r}")
    print("=" * 78)

    assert summary_A["snapshot_resolution_tier"] == "latest_for_date", (
        f"expected fallback tier 'latest_for_date', got "
        f"{summary_A['snapshot_resolution_tier']!r}")
    assert summary_A["snapshot_iso_resolved"] == INGESTED_SNAPSHOT, (
        f"resolver should have picked the only ingested snapshot "
        f"{INGESTED_SNAPSHOT!r}, got "
        f"{summary_A['snapshot_iso_resolved']!r}")
    assert summary_A["alt_odds_rows_seen"] == 4, (
        f"expected 4 odds rows under fallback, got "
        f"{summary_A['alt_odds_rows_seen']}")
    assert summary_A["score_docs_returned"] == 2, (
        f"expected 2 score docs (OVER + UNDER), got "
        f"{summary_A['score_docs_returned']}")
    assert summary_A["model_outputs_written"] == 4, (
        f"expected 4 Layer-3 rows (2 books × 2 sides), got "
        f"{summary_A['model_outputs_written']}")

    # Verify the persisted Layer-3 rows are keyed under the
    # RESOLVED snapshot, not the requested one.
    n_under_resolved = await db.nba_replay_model_outputs.count_documents(
        {"event_id": EVENT_ID, "snapshot_iso": INGESTED_SNAPSHOT})
    n_under_requested = await db.nba_replay_model_outputs.count_documents(
        {"event_id": EVENT_ID, "snapshot_iso": REQUESTED_SNAPSHOT})
    assert n_under_resolved == 4, (
        f"Layer-3 rows should be keyed under resolved snapshot "
        f"({INGESTED_SNAPSHOT}); got {n_under_resolved}")
    assert n_under_requested == 0, (
        f"Layer-3 rows must NOT carry the requested-but-unmatched "
        f"snapshot ({REQUESTED_SNAPSHOT}); got {n_under_requested}")

    # ── Scenario B: Exact match still works (regression check). ─────
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE})

    summary_B = await replay_date(
        db, GAME_DATE,
        snapshot_iso=INGESTED_SNAPSHOT,
        force=True,
        odds_collection="sgo_replay_alt_odds_raw",
    )

    print(f"\n  Scenario B — exact-match regression check")
    for k in ("snapshot_iso", "snapshot_resolution_tier",
              "alt_odds_rows_seen", "model_outputs_written"):
        print(f"    {k:36s} = {summary_B.get(k)!r}")
    assert summary_B["snapshot_resolution_tier"] == "exact", (
        f"exact match must report tier='exact', got "
        f"{summary_B['snapshot_resolution_tier']!r}")
    assert summary_B["model_outputs_written"] == 4

    # ── Scenario C: NO data for the date. Resolver returns 'none',
    #               engine returns zero with loud diagnostics.
    GAME_DATE_EMPTY = "2099-12-31"
    summary_C = await replay_date(
        db, GAME_DATE_EMPTY,
        snapshot_iso=f"{GAME_DATE_EMPTY}T11:00:00Z",
        force=True,
        odds_collection="sgo_replay_alt_odds_raw",
    )
    print(f"\n  Scenario C — no data for the date")
    for k in ("snapshot_iso", "snapshot_resolution_tier",
              "snapshot_resolution_telemetry",
              "alt_odds_rows_seen", "model_outputs_written"):
        print(f"    {k:36s} = {summary_C.get(k)!r}")
    assert summary_C["snapshot_resolution_tier"] == "none"
    assert summary_C["snapshot_resolution_telemetry"]["rows_for_date"] == 0
    assert summary_C["alt_odds_rows_seen"] == 0
    assert summary_C["model_outputs_written"] == 0

    # Cleanup.
    await db.sgo_replay_alt_odds_raw.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE_EMPTY})
    await db.nba_master_hub_2026.delete_one({"bdl_player_id": BDL_PID})

    print("\n  ✓ nba_replay_engine snapshot fallback contract verified")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
