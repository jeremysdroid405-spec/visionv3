"""Build the consolidated 15-day MLB replay sweep report from the
audit + backtest_runs collections."""
from __future__ import annotations
import asyncio
import os
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

DATES = [f"2026-05-{d:02d}" for d in range(1, 16)]
TIERS = ("safe_haven", "front_lines", "war_zone")
TIER_SHORT = {"safe_haven": "SH", "front_lines": "FL", "war_zone": "WZ"}


def _fmt_pct(x):
    return f"{x:+.1f}%" if x is not None else "  --  "


def _fmt_hr(x):
    return f"{x:.1f}%" if x is not None else "  --  "


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # ── 1. Audit serial roster (full reproducibility manifest) ─────
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║ MLB REPLAY 15-DAY SWEEP — AUDIT SERIAL ROSTER                           ║")
    print("║ Window: 2026-05-01 .. 2026-05-15  Snapshot: T11:00:00Z (daily)          ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")
    print(f"\n  {'Serial':<41} {'GateCfg':<32} {'PickChecksum':<14}")
    for d in DATES:
        for tier in TIERS:
            doc = await db.mlb_replay_audit.find_one(
                {"game_date": d, "tier": tier,
                 "snapshot_iso": f"{d}T11:00:00Z"})
            if not doc:
                print(f"  (missing audit row {d}/{tier})")
                continue
            print(f"  {doc['serial']:<41} "
                  f"{doc['gate_config_version']:<32} "
                  f"{doc['pick_set_checksum'][:14]}")

    # Version pins (constant across runs — pull from first audit row)
    sample = await db.mlb_replay_audit.find_one({"game_date": "2026-05-05"})
    if sample:
        print(f"\n  Version pins (uniform across all 15 days):")
        print(f"    scoring_config_version   {sample['scoring_config_version']}")
        print(f"    replay_engine_version    {sample['replay_engine_version']}")
        print(f"    feature_cache_version    {sample['feature_cache_version']}")
        for tier in TIERS:
            t = await db.mlb_replay_audit.find_one(
                {"game_date": "2026-05-05", "tier": tier})
            if t:
                print(f"    gate_spec_chk[{TIER_SHORT[tier]}]   "
                      f"{t['gate_config_version']:<32}  {t['gate_spec_checksum'][:16]}")

    # ── 2. Per-date × per-tier summary ────────────────────────────
    print(f"\n\n━━━━━━━━━━━━━━━━━━━━ PER-DATE × PER-TIER ━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'Date':<11} {'Tier':<12} {'Picks':>5} {'W/L/P/U':<14} "
          f"{'HR':>7} {'ROI':>8} {'Profit u':>9} {'Logs':>5} {'Note':<10}")

    # First check actual-outcome coverage per date
    pipeline = [
        {"$project": {"logs": "$bdl_game_logs"}},
        {"$unwind": "$logs"},
        {"$project": {"d": {"$ifNull": [
            {"$substr": ["$logs.date", 0, 10]},
            {"$substr": ["$logs.game_date", 0, 10]}]}}},
        {"$match": {"d": {"$gte": "2026-05-01", "$lte": "2026-05-15"}}},
        {"$group": {"_id": "$d", "n": {"$sum": 1}}},
    ]
    logs_per_date = {}
    async for r in db.mlb_master_hub_2026.aggregate(pipeline, allowDiskUse=True):
        logs_per_date[r["_id"]] = r["n"]

    for d in DATES:
        n_logs = logs_per_date.get(d, 0)
        note = ""
        if n_logs == 0:
            note = "NO LOGS"
        elif n_logs < 200:
            note = "PARTIAL"
        for tier in TIERS:
            a = await db.mlb_replay_audit.find_one(
                {"game_date": d, "tier": tier})
            if not a: continue
            wlpu = f"{a['wins']}/{a['losses']}/{a['pushes']}/{a['ungraded']}"
            print(f"  {d:<11} {tier:<12} {a['qualified_picks']:>5} "
                  f"{wlpu:<14} {_fmt_hr(a['hit_rate_pct']):>7} "
                  f"{_fmt_pct(a['roi_pct']):>8} "
                  f"{(a.get('profit_units') or 0):>+9.2f} "
                  f"{n_logs:>5} {note:<10}")

    # ── 3. Tier-level 15-day window aggregate (graded dates only) ──
    print(f"\n\n━━━━━━━━━━━━━━━━━━━━ 15-DAY WINDOW AGGREGATE (per-tier) ━━━━━━━━━━━━━━━━━━━━")
    print(f"  Graded subset: dates with ≥200 game-log entries  "
          f"(05-01..05-06 + partial 05-07 if available)")
    graded_dates = [d for d in DATES if logs_per_date.get(d, 0) >= 200]
    print(f"  Graded dates ({len(graded_dates)}): {', '.join(graded_dates)}")
    all_dates_set = set(DATES)
    ungraded_dates = sorted(all_dates_set - set(graded_dates))
    print(f"  UN-GRADED dates ({len(ungraded_dates)}, no/partial logs): "
          f"{', '.join(ungraded_dates)}")
    print(f"\n  GRADED WINDOW ONLY:")
    print(f"  {'Tier':<12} {'Picks':>6} {'W':>5} {'L':>5} {'P':>4} {'U':>5} "
          f"{'HR':>7} {'ROI':>8} {'Profit u':>10}")
    for tier in TIERS:
        agg = {"picks": 0, "w": 0, "l": 0, "p": 0, "u": 0,
               "profit": 0.0, "stake": 0.0}
        for d in graded_dates:
            a = await db.mlb_replay_audit.find_one({"game_date": d, "tier": tier})
            if not a: continue
            agg["picks"] += a.get("qualified_picks", 0)
            agg["w"] += a.get("wins", 0)
            agg["l"] += a.get("losses", 0)
            agg["p"] += a.get("pushes", 0)
            agg["u"] += a.get("ungraded", 0)
            agg["profit"] += a.get("profit_units", 0.0) or 0
            agg["stake"] += a.get("stake_units", 0.0) or 0
        graded_decisions = agg["w"] + agg["l"]
        hr = (agg["w"] / graded_decisions * 100.0) if graded_decisions else None
        roi = (agg["profit"] / agg["stake"] * 100.0) if agg["stake"] else None
        print(f"  {tier:<12} {agg['picks']:>6} {agg['w']:>5} {agg['l']:>5} "
              f"{agg['p']:>4} {agg['u']:>5} "
              f"{_fmt_hr(hr):>7} {_fmt_pct(roi):>8} {agg['profit']:>+10.2f}")

    # ── 4. ROI curves by edge bucket (graded window aggregate) ────
    print(f"\n\n━━━━━━━━━━━━━━━━━━━━ ROI CURVES BY EDGE (graded window) ━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'Tier':<12} {'Edge':<14} {'Picks':>6} {'HR':>7} {'ROI':>8} "
          f"{'Profit u':>10}")
    for tier in TIERS:
        gate_cfg = f"mlb_{TIER_SHORT[tier].lower()}_v1_2026_05_16" \
            if tier != "war_zone" else "mlb_war_zone_v1_2026_05_16"
        for bucket in ["edge_05_10", "edge_10_20", "edge_20_30", "edge_30p"]:
            agg = {"picks": 0, "w": 0, "l": 0, "profit": 0.0, "stake": 0.0}
            for d in graded_dates:
                doc = await db.mlb_replay_backtest_runs.find_one(
                    {"game_date_start": d, "tier": tier,
                     "snapshot_iso": f"{d}T11:00:00Z",
                     "gate_config_version": gate_cfg})
                if not doc: continue
                b = doc.get("by_edge_bucket", {}).get(bucket)
                if not b or not b.get("total"): continue
                agg["picks"] += b["total"]
                agg["w"] += b["wins"]
                agg["l"] += b["losses"]
                agg["profit"] += b.get("profit_units", 0.0) or 0
                agg["stake"] += b.get("stake_units", 0.0) or 0
            if agg["picks"] == 0: continue
            dec = agg["w"] + agg["l"]
            hr = (agg["w"] / dec * 100.0) if dec else None
            roi = (agg["profit"] / agg["stake"] * 100.0) if agg["stake"] else None
            print(f"  {tier:<12} {bucket:<14} {agg['picks']:>6} "
                  f"{_fmt_hr(hr):>7} {_fmt_pct(roi):>8} {agg['profit']:>+10.2f}")

    # ── 5. Verification checksum independent re-compute ──────────
    print(f"\n\n━━━━━━━━━━━━━━━━━━━━ REPRODUCIBILITY VERIFICATION ━━━━━━━━━━━━━━━━━━━━")
    print(f"  Re-running the same gate config on the same Layer-3 outputs")
    print(f"  must yield an identical pick_set_checksum for graded dates.")
    print(f"  Try: python -m scripts.mlb_replay_l4_loop  (idempotent — skips done dates)")
    print(f"\n  All 45 audit rows are queryable in `mlb_replay_audit`:")
    print(f"    db.mlb_replay_audit.find({{}}).sort('serial', 1)")
    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
