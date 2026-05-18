"""Fix 2 verification — confirm the new output-mapping fields are
populated on `mlb_test_outputs` post-fix.

Checks for the SH serial from the Phase B finalisation sweep:
  • tp                  — must be non-null on canonical-path rows
  • tp_source           — must be non-null on canonical-path rows
  • edge_pct            — must be non-null on canonical-path rows
  • is_alternate_market — must be a bool on every row
  • devig_method        — must be non-null on canonical-path rows
  • canonical_edge      — must be a float on canonical-path rows
  • gate_failed_reasons — must be a dict on every row

Also prints a sample of one FAIL row and (if any) one PASS row so
the audit can see the values cleanly.
"""
import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

SERIAL = "MLB-HIST-20260505-1100UTC-00002-SH"
FIELDS = [
    "tp", "tp_source", "edge_pct", "is_alternate_market",
    "devig_method", "canonical_edge", "gate_failed_reasons",
]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    total = await db["mlb_test_outputs"].count_documents(
        {"replay_serial": SERIAL})
    print(f"Total outputs for {SERIAL}: {total}\n")

    # Counts: how many rows have each field present-and-non-null
    populated = {f: 0 for f in FIELDS}
    canonical_rows = 0
    sample_canonical = None
    sample_non_canonical = None

    async for r in db["mlb_test_outputs"].find(
        {"replay_serial": SERIAL},
        projection={"_id": 0},
    ):
        is_canon = bool(r.get("canonical_path"))
        if is_canon:
            canonical_rows += 1
            if sample_canonical is None:
                sample_canonical = r
        else:
            if sample_non_canonical is None:
                sample_non_canonical = r
        for f in FIELDS:
            v = r.get(f)
            if v is not None:
                populated[f] += 1

    print(f"Canonical-path rows: {canonical_rows} / {total}\n")
    print(f"{'field':<22s} {'populated':>10s} {'pct':>7s}")
    for f in FIELDS:
        pct = 100.0 * populated[f] / total if total else 0.0
        print(f"  {f:<20s} {populated[f]:>10d} {pct:>6.1f}%")

    def _show(label, row):
        if row is None:
            print(f"\n{label}: <none found>")
            return
        print(f"\n{label}: "
              f"player={row.get('player_name_normalized')} "
              f"market={row.get('market')} side={row.get('side')} "
              f"line={row.get('line')} odds={row.get('odds')} "
              f"gate_pass={row.get('gate_pass')}")
        for f in FIELDS:
            print(f"   {f:<22s} = {row.get(f)!r}")
        # Helpful context
        print(f"   failed_gates           = {row.get('failed_gates')!r}")
        print(f"   tier_reference_odds    = {row.get('tier_reference_odds')!r}")

    _show("Sample canonical-path row", sample_canonical)
    _show("Sample non-canonical row", sample_non_canonical)

    # Reason-code distribution across SH failures
    reason_counter = Counter()
    async for r in db["mlb_test_outputs"].find(
        {"replay_serial": SERIAL, "gate_pass": False},
        projection={"gate_failed_reasons": 1, "_id": 0},
    ):
        gfr = r.get("gate_failed_reasons") or {}
        if isinstance(gfr, dict):
            for gt, rc in gfr.items():
                reason_counter[(gt, rc)] += 1
    print("\nTop 15 (gate_type, reason_code) failure counts:")
    for (gt, rc), n in reason_counter.most_common(15):
        print(f"  {n:>5d}  {gt:<28s}  {rc!r}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
