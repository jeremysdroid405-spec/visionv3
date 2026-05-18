"""Phase B finalisation — 3-tier MLB historical validation.

Runs `run_pipeline(tier="all")` for MLB historical 2026-05-05T11Z.
Demonstrates the universal runner persists THREE per-tier runs from
ONE provider load.

Persists JSON to:
    /app/backend/audits/phase_b_finalize_3tier_2026_05_05.json
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.pipeline import run_pipeline, PIPELINE_VERSION

TEST_ID = "MLB-HIST-20260505-1100UTC-00002"
SPORT = "mlb"
SNAPSHOT = "2026-05-05T11:00:00Z"
AUDIT_OUT = Path("/app/backend/audits/phase_b_finalize_3tier_2026_05_05.json")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    print(f"=== Phase B 3-tier validation ===")
    print(f"  test_id_root  = {TEST_ID}")
    print(f"  tier          = all  ({['safe_haven','front_lines','war_zone']})")
    print(f"  pipeline_ver  = {PIPELINE_VERSION}")
    t0 = datetime.now(timezone.utc)
    result = await run_pipeline(
        db, sport=SPORT, mode="historical",
        snapshot_time=SNAPSHOT,
        output_namespace="test",
        test_id=TEST_ID,
        tier="all",
        notes="Phase B finalisation 3-tier sweep",
    )
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    print(f"=== Completed 3-tier sweep in {elapsed:.1f}s ===\n")

    # Print per-tier summary
    print(f"{'tier':<12s} {'serial':<40s} {'scanned':>8s} "
          f"{'qual':>5s} {'cards':>6s} {'elig_rej':>9s}")
    for t, s in result["summaries"].items():
        print(f"  {t:<10s} {s.get('serial','?'):<40s} "
              f"{s.get('rows_scanned',0):>8d} "
              f"{s.get('rows_qualified',0):>5d} "
              f"{s.get('cards_displayed',0):>6d} "
              f"{s.get('eligibility_rejects',0):>9d}")

    # Verify each tier wrote its own doc.
    print(f"\n=== Persisted doc counts ===")
    for t, s in result["summaries"].items():
        serial = s["serial"]
        runs = await db["mlb_test_runs"].count_documents({"serial": serial})
        outs = await db["mlb_test_outputs"].count_documents(
            {"replay_serial": serial})
        cards = await db["mlb_test_cards"].count_documents(
            {"replay_serial": serial})
        print(f"  {t:<12s} serial={serial:<40s} runs={runs} "
              f"outputs={outs} cards={cards}")

    # Save artifact
    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "audit_kind": "phase_b_finalize_3tier",
        "test_id_root": TEST_ID,
        "elapsed_s": elapsed,
        "pipeline_version": PIPELINE_VERSION,
        "summaries": {
            t: {k: v for k, v in s.items() if k != "layer3_summary"}
            for t, s in result["summaries"].items()
        },
        "tiers": result["tiers"],
    }
    AUDIT_OUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[audit] wrote {AUDIT_OUT}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
