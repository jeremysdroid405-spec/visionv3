"""Phase 6 Phase 4 — Canonical TP / devig SH-only validation sweep.

Re-runs the 2026-05-05 SH canonical replay with the new devig
method preference (same-book > cross-book > one-sided) and the
Phase 4 audit fields stamped on every output doc.

Compares against:
  • Phase 2 baseline   (`...-SH-1100UTC-00073`, canonical_v1)
  • Legacy universal  (`...-SH-1100UTC-00015`, non-canonical)

Persists machine-readable audit to:
    /app/backend/audits/phase6_phase4_canonical_sh_2026_05_05.json
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.production_replay_runner import (
    run_production_replay, CANONICAL_ENGINE_VERSION,
)


GAME_DATE = "2026-05-05"
SNAPSHOT = "2026-05-05T11:00:00Z"
TIER = "safe_haven"
PHASE2_BASELINE = "MLB-PRODREPLAY-20260505-SH-1100UTC-00073"
LEGACY_BASELINE = "MLB-PRODREPLAY-20260505-SH-1100UTC-00015"

AUDIT_OUT = Path("/app/backend/audits/phase6_phase4_canonical_sh_2026_05_05.json")


async def _serial_summary(db, serial):
    return await db.mlb_production_replay_runs.find_one(
        {"serial": serial},
        {"_id": 0, "serial": 1, "tier": 1, "gate_path": 1,
         "canonical_path": 1, "canonical_engine_version": 1,
         "rows_qualified": 1, "rows_scanned": 1,
         "wins": 1, "losses": 1, "pushes": 1, "ungraded": 1,
         "hit_rate_pct": 1, "roi_pct": 1, "profit_units": 1,
         "cards_displayed": 1, "canonical_summary": 1},
    )


async def _routed_distribution(db, serial):
    pipe = [
        {"$match": {"replay_serial": serial}},
        {"$group": {"_id": "$routed_tier", "n": {"$sum": 1}}},
    ]
    return {r["_id"]: r["n"] async for r in db.mlb_production_replay_outputs.aggregate(pipe)}


async def _gate_failure_distribution(db, serial, routed_tier):
    pipe = [
        {"$match": {"replay_serial": serial, "routed_tier": routed_tier}},
        {"$unwind": "$failed_gates"},
        {"$group": {"_id": "$failed_gates", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    return [{"gate": r["_id"], "n": r["n"]}
            async for r in db.mlb_production_replay_outputs.aggregate(pipe)]


async def _devig_method_distribution(db, serial, routed_tier):
    pipe = [
        {"$match": {"replay_serial": serial, "routed_tier": routed_tier}},
        {"$group": {"_id": "$devig_method", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    return [{"method": r["_id"], "n": r["n"]}
            async for r in db.mlb_production_replay_outputs.aggregate(pipe)]


async def _newly_qualified_examples(db, new_serial, old_serial, limit=10):
    """Find canonical (event, player, market, line, side) keys that
    were rejected in old_serial and qualified in new_serial."""
    # Build a set of qualified keys in new_serial:
    new_q = {}
    async for r in db.mlb_production_replay_outputs.find(
        {"replay_serial": new_serial, "gate_pass": True},
        {"_id": 0, "event_id": 1, "player_name_normalized": 1,
         "market": 1, "line": 1, "side": 1,
         "player_name": 1, "odds": 1, "book": 1, "devig_method": 1,
         "same_book_pair_count": 1, "cross_book_pair_count": 1,
         "tier_reference_odds": 1},
    ):
        key = (r["event_id"], r["player_name_normalized"],
               r["market"], r["line"], r["side"])
        new_q[key] = r
    if not new_q:
        return []
    # Old qualified keys
    old_q = set()
    async for r in db.mlb_production_replay_outputs.find(
        {"replay_serial": old_serial, "gate_pass": True},
        {"_id": 0, "event_id": 1, "player_name_normalized": 1,
         "market": 1, "line": 1, "side": 1},
    ):
        old_q.add((r["event_id"], r["player_name_normalized"],
                   r["market"], r["line"], r["side"]))
    diffs = [v for k, v in new_q.items() if k not in old_q]
    return diffs[:limit]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        legacy = await _serial_summary(db, LEGACY_BASELINE)
        phase2 = await _serial_summary(db, PHASE2_BASELINE)

        print(f"\n[legacy non-canonical universal] {LEGACY_BASELINE}:")
        print(json.dumps(legacy, indent=2, default=str))
        print(f"\n[phase2 canonical v1] {PHASE2_BASELINE}:")
        print(json.dumps(phase2, indent=2, default=str))

        print(f"\n[phase4 canonical v2] running canonical_path=True SH sweep "
              f"for {GAME_DATE} @ {SNAPSHOT} ...")
        t0 = datetime.now(timezone.utc)
        summary = await run_production_replay(
            db, sport="mlb",
            game_date=GAME_DATE, snapshot_iso=SNAPSHOT,
            tier=TIER,
            canonical_path=True,
            force_layer3=False,
            notes="Phase 6 Phase 4 canonical TP / devig SH parity sweep",
        )
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        new_serial = summary["serial"]
        print(f"[phase4] completed in {elapsed:.1f}s → {new_serial}")
        print(json.dumps(
            {k: v for k, v in summary.items() if k not in ("layer3_summary",)},
            indent=2, default=str,
        ))

        # ── Diff analytics ───────────────────────────────────────
        new_routed = await _routed_distribution(db, new_serial)
        phase2_routed = await _routed_distribution(db, PHASE2_BASELINE)
        new_sh_fails = await _gate_failure_distribution(db, new_serial, "safe_haven")
        phase2_sh_fails = await _gate_failure_distribution(db, PHASE2_BASELINE, "safe_haven")
        new_sh_devig = await _devig_method_distribution(db, new_serial, "safe_haven")
        new_qualified_examples = await _newly_qualified_examples(
            db, new_serial, PHASE2_BASELINE, limit=10,
        )

        report = {
            "audit_kind": "phase6_phase4_canonical_tp_devig_sh_parity",
            "canonical_engine_version": CANONICAL_ENGINE_VERSION,
            "game_date": GAME_DATE,
            "snapshot_iso": SNAPSHOT,
            "tier": TIER,
            "baselines": {
                "legacy_non_canonical_universal": legacy,
                "phase2_canonical_v1": phase2,
            },
            "phase4_canonical_v2": {
                k: v for k, v in summary.items()
                if k not in ("layer3_summary",)
            },
            "routed_tier_distribution": {
                "phase2": phase2_routed,
                "phase4": new_routed,
            },
            "sh_routed_gate_failures": {
                "phase2": phase2_sh_fails,
                "phase4": new_sh_fails,
            },
            "phase4_sh_devig_method_distribution": new_sh_devig,
            "deltas_phase4_vs_phase2": {
                "rows_qualified_delta": (
                    summary["rows_qualified"]
                    - (phase2.get("rows_qualified") or 0)
                ),
                "wins_delta": (
                    summary["wins"] - (phase2.get("wins") or 0)
                ),
                "hit_rate_pct_delta": (
                    summary["hit_rate_pct"]
                    - (phase2.get("hit_rate_pct") or 0)
                ),
                "roi_pct_delta": (
                    summary["roi_pct"] - (phase2.get("roi_pct") or 0)
                ),
                "profit_units_delta": (
                    summary["profit_units"]
                    - (phase2.get("profit_units") or 0)
                ),
            },
            "newly_qualified_examples_vs_phase2": new_qualified_examples,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_OUT.write_text(json.dumps(report, indent=2, default=str))
        print(f"\n[audit] wrote {AUDIT_OUT}")
        return report
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
