"""
PA-v2 Coverage Verifier
=======================
Read-only validator that answers four questions:

  1. % of `mlb_live_props` carrying `batting_order != None`
  2. % of selected MLB Total Bases picks with `pa_source == 'lineup'`
     (replays the engine in audit mode via audit_mlb_pa_inputs.py)
  3. `expected_PA` distribution — should NOT be a 4.2 point-mass once a
     lineup feed is wired
  4. Future-leakage check on `mlb_projected_lineups`:
        all rows must have `as_of <= commence_time` of the matching event

No model state, gates, thresholds, μ formula, or σ are touched.

Until an external lineup feed populates `mlb_projected_lineups`, this
script will report 0% lineup coverage and the PA-v2 fallback path
(PA = 4.2) will remain in effect — which is exactly the behaviour we
want before the feed lands.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from services.mlb_lineups_loader import COLLECTION as LU_COLL


def _pct(num, denom):
    return (num / denom * 100) if denom else 0.0


async def _live_prop_coverage(db) -> None:
    print("=" * 78)
    print("  1. mlb_live_props batting_order coverage")
    print("=" * 78)
    total = await db.mlb_live_props.count_documents({})
    nn = await db.mlb_live_props.count_documents(
        {"batting_order": {"$ne": None}}
    )
    confirmed = await db.mlb_live_props.count_documents(
        {"lineup_confirmed": True}
    )
    print(f"  total mlb_live_props rows : {total:>8,}")
    print(f"  batting_order != None     : {nn:>8,}  "
          f"({_pct(nn, total):>5.1f}%)")
    print(f"  lineup_confirmed = True   : {confirmed:>8,}  "
          f"({_pct(confirmed, total):>5.1f}%)")

    # By stat_type — focus on Total Bases (the PA-v2 path).
    tb_total = await db.mlb_live_props.count_documents(
        {"stat_type": "Total Bases"}
    )
    tb_nn = await db.mlb_live_props.count_documents(
        {"stat_type": "Total Bases", "batting_order": {"$ne": None}}
    )
    print(f"  TB rows                   : {tb_total:>8,}")
    print(f"  TB rows w/ batting_order  : {tb_nn:>8,}  "
          f"({_pct(tb_nn, tb_total):>5.1f}%)")
    print()


async def _projected_lineups_health(db) -> None:
    print("=" * 78)
    print(f"  2. {LU_COLL} health  (source contract for batting_order)")
    print("=" * 78)
    n = await db[LU_COLL].count_documents({})
    print(f"  total lineup cards        : {n:>8,}")
    if n == 0:
        print("  → collection is empty.  Until an external lineup ingestor")
        print("    populates it, PA-v2 falls back to PA = 4.2 by design.")
        print()
        return
    by_source: Counter = Counter()
    confirmed_n = 0
    bad_slot = 0
    bad_pid = 0
    async for d in db[LU_COLL].find({}, {"_id": 0}):
        by_source[d.get("source") or "unknown"] += 1
        if d.get("confirmed"):
            confirmed_n += 1
        for entry in (d.get("lineup") or []):
            try:
                slot = int(entry.get("slot"))
                if not (1 <= slot <= 9):
                    bad_slot += 1
            except (TypeError, ValueError):
                bad_slot += 1
            try:
                int(entry.get("bdl_player_id"))
            except (TypeError, ValueError):
                bad_pid += 1
    print(f"  confirmed cards           : {confirmed_n:>8,}")
    print(f"  bad slot entries          : {bad_slot:>8,}")
    print(f"  bad bdl_player_id entries : {bad_pid:>8,}")
    print(f"  by source                 : {dict(by_source)}")
    print()


async def _future_leakage_check(db) -> None:
    print("=" * 78)
    print("  3. Future-leakage check  (as_of <= event commence_time)")
    print("=" * 78)
    n = await db[LU_COLL].count_documents({})
    if n == 0:
        print("  (no lineup cards present — nothing to check)")
        print()
        return
    # Build {event_id: commence_time} lookup from mlb_live_props.
    eids = await db[LU_COLL].distinct("event_id")
    commence_by_event: dict = {}
    cursor = db.mlb_live_props.find(
        {"event_id": {"$in": eids}, "commence_time": {"$ne": None}},
        {"event_id": 1, "commence_time": 1, "_id": 0},
    )
    async for d in cursor:
        eid = d.get("event_id")
        ct_raw = d.get("commence_time")
        ct = None
        if isinstance(ct_raw, datetime):
            ct = ct_raw if ct_raw.tzinfo else ct_raw.replace(tzinfo=timezone.utc)
        elif isinstance(ct_raw, str):
            s = ct_raw.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                ct = datetime.fromisoformat(s)
            except ValueError:
                ct = None
        if eid and ct is not None:
            commence_by_event.setdefault(eid, ct)

    leaks = 0
    checked = 0
    missing_commence = 0
    async for d in db[LU_COLL].find(
        {}, {"_id": 0, "event_id": 1, "as_of": 1, "team_abbr": 1, "source": 1}
    ):
        eid = d.get("event_id")
        as_of_raw = d.get("as_of")
        as_of = None
        if isinstance(as_of_raw, datetime):
            as_of = (as_of_raw if as_of_raw.tzinfo
                     else as_of_raw.replace(tzinfo=timezone.utc))
        if as_of is None:
            continue
        ct = commence_by_event.get(eid)
        if ct is None:
            missing_commence += 1
            continue
        checked += 1
        if as_of > ct:
            leaks += 1
            print(f"  LEAK: event={eid} team={d.get('team_abbr')} "
                  f"src={d.get('source')} as_of={as_of} > commence={ct}")
    print(f"  cards checked             : {checked:>8,}")
    print(f"  cards w/o commence_time   : {missing_commence:>8,}  "
          f"(unable to verify)")
    print(f"  future-leakage rows       : {leaks:>8,}")
    if leaks == 0 and checked > 0:
        print("  ✓ no leakage detected")
    print()


async def _replay_pa_v2() -> None:
    print("=" * 78)
    print("  4. Engine replay — pa_source distribution + expected_PA shape")
    print("=" * 78)
    # Delegate to the dedicated audit script (does not modify state).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audit_pa", "/app/backend/scripts/audit_mlb_pa_inputs.py")
    mod = importlib.util.module_from_spec(spec)
    print("  (running audit_mlb_pa_inputs.py …)")
    print("  (full report at /tmp/mlb_pa_audit.log — see tail below)")
    spec.loader.exec_module(mod)
    await mod.main()


async def main() -> None:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print()
    print("#" * 78)
    print("#  PA-v2 COVERAGE VERIFIER  (read-only)")
    print("#" * 78)
    print()

    await _live_prop_coverage(db)
    await _projected_lineups_health(db)
    await _future_leakage_check(db)
    print("[VERIFY] live-prop & lineup-source checks done.")
    print()
    # Engine replay is heavy — run only when --full passed.
    if "--full" in sys.argv:
        await _replay_pa_v2()
    else:
        print("  (skip engine replay — re-run with --full to also replay "
              "the audit)")


if __name__ == "__main__":
    asyncio.run(main())
