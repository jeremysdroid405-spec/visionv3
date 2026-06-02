"""Smoke test for the replay-mode eligibility bypass.

User contract (CHANGELOG 2026-06-02):
   "The optimizer pool MUST include every scored / graded prop.
    Do NOT filter optimizer inputs by gate_pass, tier, qualified
    status, current production tier gates, safe_haven/front_lines
    /war_zone eligibility."

The historical NBA universe is heavily PP-only — many props ship with
NO sportsbook quotes at all. Production `filter_priceable` drops
those because the live board can't price them. Replay must NOT.

This test verifies:
  • A PP-only prop (zero sportsbook quotes) is STILL scored by
    `recompute_sport(..., bypass_eligibility=True)`.
  • The resulting Layer-3 row is persisted to
    `nba_replay_model_outputs` with `tier="rejected"` (or some
    non-qualifying tier) as METADATA — NEVER dropped from the
    output.
  • A control prop with sportsbook quotes scores normally.
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
EVENT_ID = "nba_bypass_eligibility_test"
# Player A: only PP odds — would be dropped by production
# `filter_priceable` (book_count==0 because PP is not in _BOOK_FIELDS).
PLAYER_A = "PP Only Player"
BDL_A = 999998001
# Player B: PP + sportsbook — passes production eligibility.
PLAYER_B = "Multi Book Player"
BDL_B = 999998002


def _build_logs(end_iso: str, bdl_id: int, n: int = 20):
    end = datetime.fromisoformat(end_iso) - timedelta(days=1)
    return [{
        "game_id": 77000000 + i,
        "date": (end - timedelta(days=i + 1)).strftime("%Y-%m-%d"),
        "season": 2025, "bdl_player_id": bdl_id,
        "pts": 18 + (i % 5) * 2, "reb": 4, "ast": 5, "fg3m": 2,
        "stl": 1, "blk": 0, "turnover": 2, "min": "32",
        "fgm": 7, "fga": 15, "fg3a": 5, "ftm": 4, "fta": 5,
        "oreb": 1, "dreb": 3, "pf": 2, "plus_minus": 0,
        "opponent_team_id": 10, "home_game": (i % 2 == 0),
    } for i in range(n)]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    # Cleanup
    for coll in ("sgo_replay_alt_odds_raw", "nba_replay_model_outputs",
                 "nba_replay_model_status"):
        await db[coll].delete_many({"event_id": EVENT_ID})
    await db.nba_master_hub_2026.delete_many(
        {"bdl_player_id": {"$in": [BDL_A, BDL_B]}})
    # Seed master_hub for both players.
    await db.nba_master_hub_2026.insert_many([
        {"display_name": PLAYER_A, "player_name": PLAYER_A,
         "bdl_id": BDL_A, "bdl_player_id": BDL_A,
         "bdl_game_logs_count": 20,
         "bdl_game_logs": _build_logs(GAME_DATE, BDL_A)},
        {"display_name": PLAYER_B, "player_name": PLAYER_B,
         "bdl_id": BDL_B, "bdl_player_id": BDL_B,
         "bdl_game_logs_count": 20,
         "bdl_game_logs": _build_logs(GAME_DATE, BDL_B)},
    ])

    base = {
        "sport": "nba", "league": "NBA", "sport_key": "basketball_nba",
        "game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO,
        "event_id": EVENT_ID, "home_team": "T1", "away_team": "T2",
        "commence_time": f"{GAME_DATE}T22:00:00Z",
        "market": "player_points", "stat": "player_points",
        "is_alternate": False,
        "line": 19.5,
        "anchor_book": "prizepicks",
        "available_books": ["prizepicks"],
        "playable_on_pp": True,
        "ingested_at": datetime.now(timezone.utc),
    }
    # Player A: ONLY PP quotes (book_count would be 0 under production).
    await db.sgo_replay_alt_odds_raw.insert_many([
        {**base, "player_name": PLAYER_A,
         "player_name_normalized": "pp only player",
         "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**base, "player_name": PLAYER_A,
         "player_name_normalized": "pp only player",
         "side": "UNDER", "odds": -118, "book": "prizepicks"},
        # Player B: PP + DK + FD (production-eligible).
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "UNDER", "odds": -118, "book": "prizepicks"},
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "OVER",  "odds": -115, "book": "draftkings"},
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "UNDER", "odds": -105, "book": "draftkings"},
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "OVER",  "odds": -110, "book": "fanduel"},
        {**base, "player_name": PLAYER_B,
         "player_name_normalized": "multi book player",
         "side": "UNDER", "odds": -110, "book": "fanduel"},
    ])

    from services.replay.nba_replay_engine import replay_date

    summary = await replay_date(
        db, GAME_DATE,
        snapshot_iso=SNAPSHOT_ISO,
        force=True,
        odds_collection="sgo_replay_alt_odds_raw",
    )

    print("=" * 78)
    print("  nba_replay_engine.replay_date summary "
          "(bypass_eligibility=True contract):")
    for k in ("alt_odds_rows_seen", "props_built", "score_docs_returned",
              "recompute_processed", "recompute_skipped",
              "model_outputs_written",
              "candidates_skipped_no_score_doc",
              "bypass_eligibility"):
        print(f"    {k:36s} = {summary.get(k)!r}")
    print("=" * 78)

    # Assertions.
    # 2 odds rows for A (PP-only, OVER+UNDER) + 6 for B (PP/DK/FD × 2)
    # = 8. `_reshape_to_live_props` collapses per (event, player,
    # stat, line, side):
    #   A: 2 canonical props (OVER + UNDER, PP only)
    #   B: 2 canonical props (OVER + UNDER, multi-book)
    # → 4 canonical props built.
    assert summary["alt_odds_rows_seen"] == 8, (
        f"expected 8 odds rows, got {summary['alt_odds_rows_seen']}")
    assert summary["props_built"] == 4, (
        f"expected 4 canonical props (A×2 + B×2), got "
        f"{summary['props_built']}")
    # CRITICAL: with bypass_eligibility=True, ALL 4 props must be
    # scored. Under the old (broken) contract, A's two PP-only props
    # would silently drop here → score_docs_returned == 2.
    assert summary["score_docs_returned"] == 4, (
        f"BYPASS CONTRACT VIOLATED: expected 4 score docs (PP-only "
        f"props MUST score with tier as metadata, not drop), got "
        f"{summary['score_docs_returned']}")
    # Layer-3 fans out per (book, side):
    #   A: 2 rows (PP OVER + PP UNDER)
    #   B: 6 rows (PP/DK/FD × OVER+UNDER)
    # → 8 rows written.
    assert summary["model_outputs_written"] == 8, (
        f"expected 8 Layer-3 rows (A×2 + B×6), got "
        f"{summary['model_outputs_written']}")

    # Inspect Player A's PP-only rows directly.
    rows_a = []
    async for r in db.nba_replay_model_outputs.find(
        {"event_id": EVENT_ID, "player_name_normalized": "pp only player"},
        projection={"_id": 0, "player_name_normalized": 1, "book": 1,
                     "side": 1, "tier": 1, "gate_pass": 1,
                     "model_probability": 1, "projection_mu": 1,
                     "coverage_class": 1, "book_count": 1},
    ):
        rows_a.append(r)
    print(f"  Player A (PP-only) Layer-3 rows: {len(rows_a)}")
    for r in rows_a:
        print(f"    {r}")
    assert len(rows_a) == 2, (
        f"Player A must have 2 Layer-3 rows (OVER + UNDER PP), "
        f"got {len(rows_a)}")
    for r in rows_a:
        assert r.get("projection_mu") is not None, (
            "PP-only prop must be SCORED (projection_mu populated), "
            "even though it doesn't qualify for the production board")
        assert r.get("model_probability") is not None, (
            "PP-only prop must have model_probability — score doc "
            "field, not gate decision")
        # The tier decision is metadata. Most likely "rejected" since
        # coverage_gate requires book_count >= 1 (PP not counted), but
        # we don't ASSERT a specific tier — just that the production
        # decision survived as a label.
        assert "tier" in r, "tier must be stamped as metadata"
        assert "gate_pass" in r, "gate_pass must be stamped as metadata"

    # Player B (control) should pass production eligibility AND score.
    rows_b = []
    async for r in db.nba_replay_model_outputs.find(
        {"event_id": EVENT_ID, "player_name_normalized": "multi book player"},
        projection={"_id": 0, "tier": 1, "gate_pass": 1,
                     "book": 1, "side": 1},
    ):
        rows_b.append(r)
    print(f"  Player B (multi-book) Layer-3 rows: {len(rows_b)}")
    assert len(rows_b) == 6, (
        f"Player B must have 6 Layer-3 rows (3 books × 2 sides), "
        f"got {len(rows_b)}")

    # Cleanup
    await db.sgo_replay_alt_odds_raw.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO})
    await db.nba_master_hub_2026.delete_many(
        {"bdl_player_id": {"$in": [BDL_A, BDL_B]}})

    print("\n  ✓ nba_replay_engine bypass_eligibility contract verified")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
