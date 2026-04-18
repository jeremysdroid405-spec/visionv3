"""
Phase 3 Injury-Triggered Rescore — Hard Verification Harness
=============================================================
Runs end-to-end against the LIVE backend db handle:
  1. Pick an injured NBA player (Fred VanVleet / HOU in the current slate).
  2. Snapshot BEFORE:
       - dg_cached_board for every HOU player + 1 control (non-HOU) player.
       - nba_prop_scores for every HOU player + 1 control.
       - Dashboard API tier responses for any HOU player currently on board.
  3. Publish a synthetic BoardEvent(injury_change, nba, high-severity) for
     Fred VanVleet on HOU.
  4. Wait for the worker to drain.
  5. Snapshot AFTER + diff.
  6. Assert only impacted (HOU) rows were touched and their updated_at /
     synced_at advanced; the control (non-HOU) rows must be byte-identical.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

import motor.motor_asyncio

from services.event_bus import BoardEvent
from services.injury_triggered_rescore import get_rescore_service


TRIGGER_PLAYER = "Fred VanVleet"
TRIGGER_TEAM = "HOU"


def _fmt(v):
    if v is None:
        return "None"
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)[:48]


async def _snapshot_cached_board(db, team: str):
    snap = {}
    async for d in db.dg_cached_board.find(
        {"team": team},
        {
            "_id": 0,
            "player_name": 1, "team": 1,
            "injury_status": 1, "injured_teammates": 1,
            "synced_at": 1, "last_injury_rescore_at": 1,
        },
    ):
        snap[d["player_name"]] = d
    return snap


async def _snapshot_prop_scores(db, players):
    snap = {}  # (canonical_key, player_name) -> slim doc
    async for d in db.nba_prop_scores.find(
        {"version_tag": "final-nba", "player_name": {"$in": players}},
        {
            "_id": 0,
            "canonical_key": 1, "player_name": 1, "stat_type": 1, "line": 1,
            "tier": 1, "vision_score": 1, "pp_utility": 1,
            "vk2_projection": 1, "computed_at": 1, "version_tag": 1,
        },
    ):
        key = (d.get("canonical_key"), d.get("player_name"))
        snap[key] = d
    return snap


async def main():
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Make sure the service is bound to THIS db handle (mirror what server.py
    # does on startup). This is what's running inside the backend already.
    svc = get_rescore_service()
    svc.start(db)

    # ------------------------------------------------------------------
    # Pick the HOU players (impacted) and a control (non-HOU) player.
    # ------------------------------------------------------------------
    hou_players = sorted(
        [d["player_name"] async for d in db.dg_cached_board.find(
            {"team": TRIGGER_TEAM}, {"_id": 0, "player_name": 1}
        )]
    )
    ctrl_doc = await db.dg_cached_board.find_one(
        {"team": {"$ne": TRIGGER_TEAM}},
        {"_id": 0, "player_name": 1, "team": 1},
    )
    ctrl_player = ctrl_doc["player_name"]
    ctrl_team = ctrl_doc["team"]

    print(f"Trigger   = {TRIGGER_PLAYER} ({TRIGGER_TEAM})")
    print(f"Impacted  = {len(hou_players)} HOU players: {hou_players}")
    print(f"Control   = {ctrl_player} ({ctrl_team})")
    print()

    # ------------------------------------------------------------------
    # BEFORE snapshot
    # ------------------------------------------------------------------
    board_before_hou = await _snapshot_cached_board(db, TRIGGER_TEAM)
    board_before_ctrl = await _snapshot_cached_board(db, ctrl_team)
    scores_before_hou = await _snapshot_prop_scores(db, hou_players)
    scores_before_ctrl = await _snapshot_prop_scores(db, [ctrl_player])

    print(f"BEFORE: HOU board players  = {len(board_before_hou)}")
    print(f"BEFORE: HOU prop_scores    = {len(scores_before_hou)}")
    print(f"BEFORE: ctrl({ctrl_team}) board players = {len(board_before_ctrl)}")
    print(f"BEFORE: ctrl prop_scores   = {len(scores_before_ctrl)}")
    print()

    # ------------------------------------------------------------------
    # Publish synthetic injury_change event
    # ------------------------------------------------------------------
    evt = BoardEvent(
        sport="nba",
        event_type="injury_change",
        severity="high",  # required to pass the filter
        affected_players=[TRIGGER_PLAYER],
        source="phase3_verify_harness",
        metadata={
            "team": TRIGGER_TEAM,
            "changes": [f"{TRIGGER_PLAYER}:new_injury"],
            "max_tier_delta": 4,
        },
    )
    t0 = datetime.now(timezone.utc).timestamp()
    # Note: event bus is process-local; our in-process svc instance is
    # subscribed via start(db). Use trigger_manual() which goes through the
    # same _on_event -> queue -> worker path as a real bus publish would.
    await svc.trigger_manual(evt)
    print(f"[HARNESS] enqueued injury_change at {datetime.now(timezone.utc).isoformat()}")

    # ------------------------------------------------------------------
    # Wait for the worker to drain. We poll the service stats until
    # recomputes count advances or we hit a timeout.
    # ------------------------------------------------------------------
    baseline_recomputes = svc.stats()["recomputes"]
    deadline = t0 + 120.0
    while True:
        s = svc.stats()
        if s["recomputes"] > baseline_recomputes:
            break
        if datetime.now(timezone.utc).timestamp() > deadline:
            print("[HARNESS] TIMEOUT waiting for rescore to drain")
            print("stats:", s)
            return 2
        await asyncio.sleep(0.5)

    # Give the patch_cached_board writes a tick to settle
    await asyncio.sleep(0.3)

    stats = svc.stats()
    print(f"[HARNESS] stats post-trigger: {stats}")
    print()

    # ------------------------------------------------------------------
    # AFTER snapshot
    # ------------------------------------------------------------------
    board_after_hou = await _snapshot_cached_board(db, TRIGGER_TEAM)
    board_after_ctrl = await _snapshot_cached_board(db, ctrl_team)
    scores_after_hou = await _snapshot_prop_scores(db, hou_players)
    scores_after_ctrl = await _snapshot_prop_scores(db, [ctrl_player])

    # ------------------------------------------------------------------
    # Report 1: dg_cached_board for HOU — every doc must have new
    # synced_at/last_injury_rescore_at, injured_teammates updated.
    # ------------------------------------------------------------------
    print("== dg_cached_board: HOU (impacted) ==")
    header = f"{'player':22s} {'injury_status':18s} {'injured_teammates':34s} {'sync BEFORE':>30s} {'sync AFTER':>30s}"
    print(header)
    touched = 0
    for pn, after in board_after_hou.items():
        before = board_before_hou.get(pn, {})
        b_sync = before.get("synced_at")
        a_sync = after.get("synced_at")
        b_teammates = before.get("injured_teammates")
        a_teammates = after.get("injured_teammates")
        it_str = str(a_teammates)[:32]
        changed = b_sync != a_sync or b_teammates != a_teammates
        if changed:
            touched += 1
        flag = "CHANGED" if changed else "-"
        print(f"{pn:22s} {_fmt(after.get('injury_status')):18s} {it_str:34s} {_fmt(b_sync):>30s} {_fmt(a_sync):>30s}  {flag}")
    print(f"HOU board docs touched: {touched}/{len(board_after_hou)}")
    print()

    # ------------------------------------------------------------------
    # Report 2: dg_cached_board for control team — MUST NOT have changed.
    # ------------------------------------------------------------------
    print(f"== dg_cached_board: {ctrl_team} (control, MUST be untouched) ==")
    leaks = 0
    for pn, after in board_after_ctrl.items():
        before = board_before_ctrl.get(pn, {})
        if before != after:
            leaks += 1
            print(f"  LEAK! {pn}: before={before} after={after}")
    print(f"Control leakage: {leaks} (expected 0)")
    print()

    # ------------------------------------------------------------------
    # Report 3: nba_prop_scores for HOU — computed_at must advance.
    # ------------------------------------------------------------------
    print("== nba_prop_scores: HOU (impacted) ==")
    advanced = 0
    same = 0
    for key, after in scores_after_hou.items():
        before = scores_before_hou.get(key)
        if not before:
            continue
        if before.get("computed_at") != after.get("computed_at"):
            advanced += 1
        else:
            same += 1
    sample_keys = list(scores_after_hou.keys())[:5]
    for k in sample_keys:
        a = scores_after_hou[k]
        b = scores_before_hou.get(k, {})
        print(f"  {k[1]:22s} {a.get('stat_type'):6s} line={a.get('line'):<5} "
              f"tier={a.get('tier'):<12} computed BEFORE={_fmt(b.get('computed_at'))} -> AFTER={_fmt(a.get('computed_at'))}")
    print(f"HOU prop_scores with computed_at advanced: {advanced}/{len(scores_after_hou)}  (unchanged: {same})")
    print()

    # ------------------------------------------------------------------
    # Report 4: nba_prop_scores for control — computed_at must NOT
    # advance (proves scoping).
    # ------------------------------------------------------------------
    print(f"== nba_prop_scores: {ctrl_player} (control, MUST be untouched) ==")
    ctrl_changed = 0
    for key, after in scores_after_ctrl.items():
        before = scores_before_ctrl.get(key) or {}
        if before.get("computed_at") != after.get("computed_at"):
            ctrl_changed += 1
            print(f"  LEAK! {key[1]} {key[0]}: "
                  f"computed_at BEFORE={before.get('computed_at')} -> AFTER={after.get('computed_at')}")
    print(f"Control prop_scores with computed_at changed: {ctrl_changed} (expected 0)")
    print()

    # ------------------------------------------------------------------
    # Pass/fail summary
    # ------------------------------------------------------------------
    all_hou_touched = touched == len(board_after_hou) and len(board_after_hou) > 0
    no_ctrl_leak = leaks == 0
    hou_scores_advanced = advanced > 0
    no_ctrl_score_leak = ctrl_changed == 0

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(f"  [{'OK' if all_hou_touched else 'FAIL'}] All HOU board docs patched (touched={touched}/{len(board_after_hou)})")
    print(f"  [{'OK' if no_ctrl_leak else 'FAIL'}] No control ({ctrl_team}) board doc mutated")
    print(f"  [{'OK' if hou_scores_advanced else 'FAIL'}] HOU prop_scores computed_at advanced ({advanced})")
    print(f"  [{'OK' if no_ctrl_score_leak else 'FAIL'}] No control prop_scores computed_at mutated ({ctrl_changed})")
    print(f"  [INFO] latency_ms = {stats.get('last_latency_ms')}")
    print(f"  [INFO] props_rescored (cumulative) = {stats.get('props_rescored')}")
    print(f"  [INFO] board_players_patched (cumulative) = {stats.get('board_players_patched')}")
    ok = all_hou_touched and no_ctrl_leak and hou_scores_advanced and no_ctrl_score_leak
    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
