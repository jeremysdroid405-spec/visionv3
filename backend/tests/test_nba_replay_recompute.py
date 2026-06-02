"""End-to-end recompute regression: one replay row reaches
`model_probability` output with the availability-guard bypass,
fantasy_score canonicalisation, and blank-date safety all
exercised.

Verifies the user's explicit success criterion from 2026-06-02:
  "one replay row reaches model_probability output"
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
SNAPSHOT_ISO = f"{GAME_DATE}T11:00:00Z"
EVENT_ID = "nba_recompute_e2e_test"
PLAYER = "Recompute E2E Player"
BDL_PID = 999996001


def _build_logs(end_iso: str, n: int = 20):
    """Synthetic NBA logs with TWO blank-date entries injected to
    exercise the `_classify_availability` safety fix. The scorer
    must skip them, not crash."""
    end = datetime.fromisoformat(end_iso) - timedelta(days=1)
    logs = []
    for i in range(n):
        # Inject blank dates at positions 2 and 5 to exercise the
        # blank-date guard in _classify_availability.
        if i in (2, 5):
            d = ""
        else:
            d = (end - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        logs.append({
            "game_id": 55000000 + i,
            "date": d,
            "season": 2025,
            "bdl_player_id": BDL_PID,
            "pts": 18 + (i % 5) * 2,
            "reb": 4 + (i % 3),
            "ast": 5 + (i % 4),
            "fg3m": 2,
            "stl": 1,
            "blk": 0,
            "turnover": 2,
            "min": "32",
            "fgm": 7, "fga": 15, "fg3a": 5, "ftm": 4, "fta": 5,
            "oreb": 1, "dreb": 3, "pf": 2, "plus_minus": 0,
            "opponent_team_id": 10, "home_game": (i % 2 == 0),
        })
    return logs


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    # Cleanup.
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

    # Seed: standard PTS prop (multi-book) + a fantasy_score prop
    # (PP-only). Verifies both stat families round-trip through the
    # production scorer.
    base = {
        "sport": "nba", "league": "NBA",
        "sport_key": "basketball_nba",
        "game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO,
        "event_id": EVENT_ID,
        "home_team": "T1", "away_team": "T2",
        "commence_time": f"{GAME_DATE}T22:00:00Z",
        "is_alternate": False,
        "player_name": PLAYER,
        "player_name_normalized": "recompute e2e player",
        "anchor_book": "prizepicks",
        "available_books": ["prizepicks"],
        "playable_on_pp": True,
        "ingested_at": datetime.now(timezone.utc),
    }
    pts = {**base, "market": "player_points", "stat": "player_points",
            "line": 19.5}
    fan = {**base, "market": "player_fantasy_score",
            "stat": "player_fantasy_score", "line": 35.0}
    await db.sgo_replay_alt_odds_raw.insert_many([
        # PTS — multi-book
        {**pts, "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**pts, "side": "UNDER", "odds": -118, "book": "prizepicks"},
        {**pts, "side": "OVER",  "odds": -115, "book": "draftkings"},
        {**pts, "side": "UNDER", "odds": -105, "book": "draftkings"},
        # fantasy_score — PP-only (exercises the canonical registration)
        {**fan, "side": "OVER",  "odds": -120, "book": "prizepicks"},
        {**fan, "side": "UNDER", "odds": -120, "book": "prizepicks"},
    ])

    from services.replay.nba_replay_engine import replay_date

    summary = await replay_date(
        db, GAME_DATE,
        snapshot_iso=SNAPSHOT_ISO,
        force=True,
        odds_collection="sgo_replay_alt_odds_raw",
    )

    print("=" * 78)
    print("  E2E recompute summary:")
    for k in ("alt_odds_rows_seen", "props_built",
              "score_docs_returned", "model_outputs_written",
              "snapshot_resolution_tier", "bypass_eligibility"):
        print(f"    {k:32s} = {summary.get(k)!r}")
    print("=" * 78)

    # SUCCESS CRITERION: at least one replay row reaches
    # model_probability output.
    rows = []
    async for r in db.nba_replay_model_outputs.find(
        {"event_id": EVENT_ID},
        projection={"_id": 0, "stat_type": 1, "stat_family": 1,
                     "side": 1, "book": 1, "model_probability": 1,
                     "projection_mu": 1, "tier": 1, "gate_pass": 1,
                     "edge": 1, "vision_score": 1, "tp": 1,
                     "availability_guard_applied": 1,
                     "availability_guard_reason": 1},
    ):
        rows.append(r)

    print(f"\n  {len(rows)} Layer-3 rows persisted:")
    for r in rows:
        print(f"    {r}")

    assert len(rows) >= 1, "expected ≥1 row, got 0"

    # Every row must have a non-None model_probability + projection_mu.
    for r in rows:
        assert r.get("model_probability") is not None, (
            f"row missing model_probability: {r}")
        assert r.get("projection_mu") is not None, (
            f"row missing projection_mu: {r}")

    # Verify the fantasy_score rows specifically reached the scorer
    # (no STAT_REGISTRY_MISS → _default fall-through; they routed
    # through the PRA family and produced a model_probability).
    fantasy_rows = [r for r in rows if r.get("stat_type") == "FANTASY"]
    assert len(fantasy_rows) >= 1, (
        "expected ≥1 fantasy_score row reaching model_probability "
        "output; got 0 (likely the canonicalisation regressed)")
    for r in fantasy_rows:
        # Fantasy routes to the PRA family per canonical_stats.
        assert r.get("stat_family") == "pra", (
            f"fantasy_score row should carry stat_family='pra', "
            f"got {r.get('stat_family')!r}")
        assert r.get("model_probability") is not None
        assert r.get("projection_mu") is not None

    # Verify the availability guard was DISABLED by the replay
    # contract.
    for r in rows:
        if r.get("availability_guard_applied") is False:
            assert (r.get("availability_guard_reason")
                    == "disabled_by_replay"), (
                f"row had availability_guard_applied=False but "
                f"reason was {r.get('availability_guard_reason')!r}; "
                f"expected 'disabled_by_replay'")

    # Cleanup.
    await db.sgo_replay_alt_odds_raw.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO})
    await db.nba_master_hub_2026.delete_one({"bdl_player_id": BDL_PID})

    print("\n  ✓ E2E recompute regression PASSED — "
          f"{len(rows)} rows scored, {len(fantasy_rows)} fantasy")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
