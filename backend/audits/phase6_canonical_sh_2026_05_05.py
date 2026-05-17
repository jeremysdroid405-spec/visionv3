"""Phase 6 Phase 2 — Canonical path SH-only parity sweep for 2026-05-05.

Runs `run_production_replay(..., canonical_path=True)` for the
Safe Haven tier on 2026-05-05 (snapshot 11:00:00Z) and compares
against the prior universal (non-canonical) SH baseline
`MLB-PRODREPLAY-20260505-SH-1100UTC-00015`:

    104 qualified / 69W-11L / 86.25% HR / +31.34% ROI / +$25.08

Layer-3 outputs for 2026-05-05 already exist (25,431 rows); this
sweep does NOT re-run the model — it consumes the persisted
`mlb_replay_model_outputs` rows.

Persists the result audit to:
    /app/backend/audits/phase6_canonical_sh_2026_05_05.json
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
    run_production_replay,
    CANONICAL_ENGINE_VERSION,
)


GAME_DATE = "2026-05-05"
SNAPSHOT = "2026-05-05T11:00:00Z"
TIER = "safe_haven"
BASELINE_SERIAL = "MLB-PRODREPLAY-20260505-SH-1100UTC-00015"

AUDIT_OUT = Path("/app/backend/audits/phase6_canonical_sh_2026_05_05.json")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        # Look up baseline (universal, non-canonical) SH run.
        baseline = await db.mlb_production_replay_runs.find_one(
            {"serial": BASELINE_SERIAL},
            {"_id": 0, "serial": 1, "tier": 1, "gate_path": 1,
             "rows_qualified": 1, "rows_scanned": 1,
             "wins": 1, "losses": 1, "pushes": 1, "ungraded": 1,
             "hit_rate_pct": 1, "roi_pct": 1, "profit_units": 1,
             "cards_displayed": 1},
        )
        print(f"\n[baseline] {BASELINE_SERIAL}:")
        print(json.dumps(baseline, indent=2, default=str))

        print(f"\n[canonical] running canonical_path=True SH sweep for "
              f"{GAME_DATE} @ {SNAPSHOT} ...")
        t0 = datetime.now(timezone.utc)
        summary = await run_production_replay(
            db, sport="mlb",
            game_date=GAME_DATE, snapshot_iso=SNAPSHOT,
            tier=TIER,
            canonical_path=True,           # ← Phase 2 wiring under test
            force_layer3=False,
            notes="Phase 6 Phase 2 canonical_path SH parity sweep",
        )
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"[canonical] completed in {elapsed:.1f}s")
        print(json.dumps(
            {k: v for k, v in summary.items()
             if k not in ("layer3_summary",)},
            indent=2, default=str,
        ))

        # Compose diff report
        report = {
            "audit_kind": "phase6_phase2_canonical_sh_parity",
            "canonical_engine_version": CANONICAL_ENGINE_VERSION,
            "game_date": GAME_DATE,
            "snapshot_iso": SNAPSHOT,
            "tier": TIER,
            "baseline": baseline or {},
            "canonical": {
                k: v for k, v in summary.items()
                if k not in ("layer3_summary",)
            },
            "deltas": {
                "rows_qualified_delta": (
                    summary["rows_qualified"]
                    - (baseline.get("rows_qualified") or 0)
                    if baseline else None
                ),
                "wins_delta": (
                    summary["wins"] - (baseline.get("wins") or 0)
                    if baseline else None
                ),
                "losses_delta": (
                    summary["losses"] - (baseline.get("losses") or 0)
                    if baseline else None
                ),
                "hit_rate_pct_delta": (
                    summary["hit_rate_pct"]
                    - (baseline.get("hit_rate_pct") or 0)
                    if baseline else None
                ),
                "roi_pct_delta": (
                    summary["roi_pct"] - (baseline.get("roi_pct") or 0)
                    if baseline else None
                ),
                "profit_units_delta": (
                    summary["profit_units"]
                    - (baseline.get("profit_units") or 0)
                    if baseline else None
                ),
            },
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
