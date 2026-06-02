"""Smoke test for the NBA replay engine wrap.

Seeds synthetic historical odds + master_hub data, runs
`nba_replay_engine.replay_date()` end-to-end, and verifies that
Layer-3 rows land in `nba_replay_model_outputs` with the SSOT
fields the runner expects (projection_mu, sigma, model_probability,
edge, hit_rate_l5/10/20, cv, tier, gate_pass, vision_score, tp,
tp_source, edge_pct).

The test does NOT mock the production scorer — it calls
`recompute_sport(db, "nba", ..., dry_run=True)` for real and
inspects the score docs it returns. That is the whole point of
the wrap: same scoring logic, historical inputs, replay outputs.
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

GAME_DATE = "2025-10-22"
SNAPSHOT_ISO = f"{GAME_DATE}T11:00:00Z"
EVENT_ID = "nba_replay_smoke_test_event"
PLAYER_NAME = "Smoke Test Player"
BDL_PID = 999999001
# stat_family from history → drive the recency / availability /
# rate × minutes layers in `NBAScoringAdapter`. PTS is a recency-
# blend target so the production scoring path exercises the
# heaviest code path.
LINE_PTS = 19.5


def _build_game_logs(end_date_iso: str, n: int = 20) -> list:
    """Build N synthetic NBA game logs ending the day BEFORE `end_date`.

    Each log includes the canonical fields `NBAScoringAdapter` reads:
    `date`, `pts`, `reb`, `ast`, `fg3m`, `stl`, `blk`, `turnover`, `min`.
    Stat values rotate around a mean of ~20 PTS to give the scorer a
    realistic CV / hit-rate distribution.
    """
    end = datetime.fromisoformat(end_date_iso) - timedelta(days=1)
    logs = []
    for i in range(n):
        d = end - timedelta(days=i + 1)
        pts = 18 + (i % 5) * 2          # 18, 20, 22, 24, 26 cycle
        logs.append({
            "game_id": 99000000 + i,
            "date": d.strftime("%Y-%m-%d"),
            "season": 2025,
            "bdl_player_id": BDL_PID,
            "pts": pts,
            "reb": 4 + (i % 3),
            "ast": 5 + (i % 4),
            "fg3m": 2 + (i % 2),
            "stl": 1,
            "blk": 0,
            "turnover": 2,
            "min": "32",
            "fgm": 7,
            "fga": 15,
            "fg3a": 5,
            "ftm": 4,
            "fta": 5,
            "oreb": 1,
            "dreb": 3,
            "pf": 2,
            "plus_minus": 0,
            "opponent_team_id": 10,
            "home_game": (i % 2 == 0),
        })
    return logs


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Cleanup any stale state from prior runs of this smoke test.
    for coll in ("sgo_replay_alt_odds_raw", "nba_replay_model_outputs",
                 "nba_replay_model_status"):
        await db[coll].delete_many({"event_id": EVENT_ID})
    await db.sgo_replay_alt_odds_raw.delete_many(
        {"game_date": GAME_DATE, "event_id": EVENT_ID})
    await db.nba_master_hub_2026.delete_one({"bdl_player_id": BDL_PID})

    # Seed master_hub row for the player.
    await db.nba_master_hub_2026.insert_one({
        "display_name": PLAYER_NAME,
        "player_name": PLAYER_NAME,
        "bdl_id": BDL_PID,
        "bdl_player_id": BDL_PID,
        "bdl_game_logs_count": 20,
        "bdl_game_logs": _build_game_logs(GAME_DATE, n=20),
    })

    # Seed two historical odds rows: PP OVER + PP UNDER (so apply_production_eligibility
    # produces a usable companion map for de-vig).
    base_row = {
        "sport": "nba",
        "league": "NBA",
        "sport_key": "basketball_nba",
        "game_date": GAME_DATE,
        "snapshot_iso": SNAPSHOT_ISO,
        "event_id": EVENT_ID,
        "home_team": "Test Home",
        "away_team": "Test Away",
        "commence_time": f"{GAME_DATE}T22:00:00Z",
        "market": "player_points",
        "stat": "player_points",
        "is_alternate": False,
        "player_name": PLAYER_NAME,
        "player_name_normalized": "smoke test player",
        "line": LINE_PTS,
        "book": "prizepicks",
        "anchor_book": "prizepicks",
        "available_books": ["prizepicks"],
        "playable_on_pp": True,
        "ingested_at": datetime.now(timezone.utc),
    }
    await db.sgo_replay_alt_odds_raw.insert_many([
        # PrizePicks anchor (required for playable_on_pp).
        {**base_row, "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**base_row, "side": "UNDER", "odds": -118, "book": "prizepicks"},
        # Sportsbook quotes so `classify_coverage` produces book_count >= 1.
        # `filter_priceable` drops 0-book (pp_only) props.
        {**base_row, "side": "OVER",  "odds": -115, "book": "draftkings"},
        {**base_row, "side": "UNDER", "odds": -105, "book": "draftkings"},
        {**base_row, "side": "OVER",  "odds": -110, "book": "fanduel"},
        {**base_row, "side": "UNDER", "odds": -110, "book": "fanduel"},
    ])

    # Invoke the NBA replay engine directly.
    from services.replay.nba_replay_engine import replay_date

    t0 = time.monotonic()
    summary = await replay_date(
        db, GAME_DATE,
        snapshot_iso=SNAPSHOT_ISO,
        force=True,    # ignore status short-circuit
        odds_collection="sgo_replay_alt_odds_raw",
    )
    elapsed = time.monotonic() - t0

    print("=" * 78)
    print(f"  nba_replay_engine.replay_date summary (elapsed {elapsed:.2f}s)")
    for k, v in summary.items():
        print(f"    {k:36s} = {v}")
    print("=" * 78)

    # Assertions.
    assert summary.get("alt_odds_rows_seen") == 6, (
        f"expected 6 odds rows, got {summary.get('alt_odds_rows_seen')}")
    assert summary.get("props_built") == 2, (
        f"expected 2 props built (OVER + UNDER), "
        f"got {summary.get('props_built')}")
    assert summary.get("score_docs_returned") >= 1, (
        f"expected ≥1 score doc returned from recompute_sport, "
        f"got {summary.get('score_docs_returned')}")
    assert summary.get("model_outputs_written") >= 1, (
        f"expected ≥1 model output written, "
        f"got {summary.get('model_outputs_written')}")

    # Verify the output rows carry the SSOT fields.
    rows = []
    async for r in db.nba_replay_model_outputs.find(
        {"event_id": EVENT_ID, "snapshot_iso": SNAPSHOT_ISO},
        projection={"_id": 0},
    ):
        rows.append(r)

    print(f"\n  Found {len(rows)} nba_replay_model_outputs rows")
    assert rows, "No Layer-3 rows persisted"

    required_fields = (
        "projection_mu", "sigma", "model_probability", "fair_probability",
        "implied_probability", "edge", "hit_rate_l5", "hit_rate_l10",
        "hit_rate_l20", "cv", "tp", "tp_source", "edge_pct", "tier",
        "gate_pass", "vision_score", "stat_family", "stat_type",
    )

    sample = rows[0]
    missing = [f for f in required_fields if f not in sample]
    assert not missing, f"Missing SSOT fields on Layer-3 row: {missing}"

    print("\n  Sample Layer-3 row SSOT fields:")
    for f in required_fields:
        print(f"    {f:24s} = {sample.get(f)!r}")

    # ── Phase 2: run the FULL orchestrator (Layer-3 + Layer-4) ────────
    print("\n" + "=" * 78)
    print("  Phase 2: run_production_replay end-to-end (universal gate path)")
    print("=" * 78)
    from services.replay.production_replay_runner import run_production_replay

    # Cleanup runner-level collections before the second pass.
    await db.nba_propvision_full_pipeline_outputs.delete_many(
        {"sport": "nba", "game_date": GAME_DATE})
    await db.nba_propvision_full_pipeline_runs.delete_many(
        {"sport": "nba", "game_date": GAME_DATE})
    await db.nba_propvision_full_pipeline_cards.delete_many(
        {"sport": "nba", "game_date": GAME_DATE})
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO})

    # Re-seed historical odds rows (Phase 1 cleaned them up earlier).
    await db.sgo_replay_alt_odds_raw.delete_many({"event_id": EVENT_ID})
    await db.sgo_replay_alt_odds_raw.insert_many([
        {**base_row, "side": "OVER",  "odds": -118, "book": "prizepicks"},
        {**base_row, "side": "UNDER", "odds": -118, "book": "prizepicks"},
        {**base_row, "side": "OVER",  "odds": -115, "book": "draftkings"},
        {**base_row, "side": "UNDER", "odds": -105, "book": "draftkings"},
        {**base_row, "side": "OVER",  "odds": -110, "book": "fanduel"},
        {**base_row, "side": "UNDER", "odds": -110, "book": "fanduel"},
    ])

    runner_summary = await run_production_replay(
        db, sport="nba",
        game_date=GAME_DATE,
        snapshot_iso=SNAPSHOT_ISO,
        tier="front_lines",
        gate_path="universal",
        output_namespace="propvision_full_pipeline",
        dry_run=False,
        notes="nba_replay_engine smoke test",
        odds_collection="sgo_replay_alt_odds_raw",
    )
    print("\n  run_production_replay summary:")
    for k in ("serial", "sport", "rows_scanned", "rows_qualified",
              "wins", "losses", "pushes", "ungraded", "hit_rate_pct",
              "roi_pct", "cards_displayed", "elapsed_s"):
        print(f"    {k:24s} = {runner_summary.get(k)!r}")

    assert runner_summary.get("rows_scanned") >= 1, (
        f"runner scanned 0 rows, got "
        f"{runner_summary.get('rows_scanned')}")
    assert runner_summary.get("rows_qualified") >= 1, (
        f"runner qualified 0 rows on tier=front_lines, got "
        f"{runner_summary.get('rows_qualified')} "
        f"(expected ≥1 since Phase 1 row tier was front_lines)")

    # Verify the per-tier output doc carries the SSOT NBA fields.
    out_doc = await db.nba_propvision_full_pipeline_outputs.find_one(
        {"replay_serial": runner_summary["serial"], "gate_pass": True},
        projection={"_id": 0},
    )
    assert out_doc is not None, "no gate_pass=True output doc persisted"
    runner_required_fields = (
        "tp", "tp_source", "edge_pct", "tier", "gate_pass",
        "vision_score", "p_model", "p_true_active",
        "model_probability", "projection_mu", "sigma", "edge",
        "hit_rate_l5", "hit_rate_l10", "hit_rate_l20", "cv",
    )
    missing_r = [f for f in runner_required_fields if f not in out_doc]
    assert not missing_r, (
        f"output doc missing universal SSOT fields: {missing_r}")
    print("\n  Sample propvision output SSOT fields:")
    for f in runner_required_fields:
        print(f"    {f:24s} = {out_doc.get(f)!r}")

    # Cleanup runner collections too.
    await db.nba_propvision_full_pipeline_outputs.delete_many(
        {"replay_serial": runner_summary["serial"]})
    await db.nba_propvision_full_pipeline_runs.delete_many(
        {"serial": runner_summary["serial"]})
    await db.nba_propvision_full_pipeline_cards.delete_many(
        {"replay_serial": runner_summary["serial"]})

    # Cleanup the seeded synthetic data so we don't pollute the DB.
    await db.sgo_replay_alt_odds_raw.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_outputs.delete_many({"event_id": EVENT_ID})
    await db.nba_replay_model_status.delete_many(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT_ISO})
    await db.nba_master_hub_2026.delete_one({"bdl_player_id": BDL_PID})

    print("\n  ✓ nba_replay_engine smoke test PASSED")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
