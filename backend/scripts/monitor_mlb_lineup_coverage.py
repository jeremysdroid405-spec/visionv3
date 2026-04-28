"""
PA-v2 Lineup Coverage Monitor   (read-only)
============================================
Diagnostic wrapper that surfaces today's PA-v2 input health without
touching scoring, gates, or any model state.

Reports
-------
  • date / now (UTC)
  • total mlb_live_props rows on the active slate
  • TB-only rows on the active slate
  • % active-slate rows with batting_order != None
  • % active-slate rows with lineup_confirmed = True
  • % TB rows with pa_source = "lineup" (computed via the rate
    actually exposed on `mlb_live_props` after the latest hydrator
    refresh — it does NOT replay the engine; for that, run
    `scripts/verify_pa_v2_coverage.py --full`)
  • expected_PA distribution snapshot (computed inline from
    `services.mlb_engine.project_pa` if available, else skipped)

SLA
---
  --sla 60                  warn (do NOT fail) if active-slate
                            batting_order coverage < 60 %
  --enforce-after-utc 22    only enforce SLA when current UTC hour ≥ 22
                            (default).  Before that, lineup ingest may
                            legitimately not have run yet and missing
                            cards is expected.

Safety contract
---------------
This script is strictly read-only:
  ✓ No writes to any collection.
  ✓ No post-game lineup inference.
  ✓ No game-log derivations.
  ✓ No model / gate / threshold changes.
  ✓ When lineups are missing, PA=4.2 fallback is reported as expected.

Exit codes
----------
  0  always — even on SLA breach we print WARNING but do NOT fail
            (per spec: "do not fail pipeline, do not change model
             behavior").  Caller can grep stdout for "WARNING" if it
             wants alert routing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _pct(num: int, denom: int) -> float:
    return (num / denom * 100.0) if denom else 0.0


def _parse_iso(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return None


def _active_window(now: datetime) -> Tuple[datetime, datetime]:
    """Active slate = games starting in (now - 12 h, now + 36 h).
    Covers in-progress games AND tomorrow's day slate.  Excludes
    stale rows that may still linger in `mlb_live_props`."""
    return now - timedelta(hours=12), now + timedelta(hours=36)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
async def _snapshot(db, now: datetime) -> Dict[str, Any]:
    lo, hi = _active_window(now)
    # commence_time is stored as ISO-string — pull all and filter in Py
    # (collection has ~thousands of rows; cheap).
    coll = db["mlb_live_props"]
    total = 0
    tb_total = 0
    bo_total = 0
    bo_tb = 0
    confirmed_total = 0
    expected_pa_samples: List[float] = []
    by_source = {"lineup": 0, "fallback": 0, "other": 0}

    cursor = coll.find(
        {},
        {"_id": 0, "stat_type": 1, "batting_order": 1,
         "lineup_confirmed": 1, "lineup_source": 1,
         "commence_time": 1, "team_total": 1, "is_home_team": 1},
    )
    async for r in cursor:
        ct = _parse_iso(r.get("commence_time"))
        if ct is None or not (lo <= ct <= hi):
            continue
        total += 1
        bo = r.get("batting_order")
        if bo is not None:
            bo_total += 1
        if r.get("lineup_confirmed"):
            confirmed_total += 1
        if r.get("stat_type") == "Total Bases":
            tb_total += 1
            if bo is not None:
                bo_tb += 1
                # Inline expected-PA estimate using the same project_pa
                # the engine uses (read-only — no scoring side-effects).
                try:
                    import importlib.util
                    if "_mlb_pv" not in globals():
                        _spec = importlib.util.spec_from_file_location(
                            "_mlb_pv",
                            "/app/backend/scripts/mlb_propvision_total_bases.py")
                        _mod = importlib.util.module_from_spec(_spec)
                        _spec.loader.exec_module(_mod)
                        globals()["_mlb_pv"] = _mod
                    project_pa = globals()["_mlb_pv"].project_pa
                    pa, src = project_pa(
                        bo, r.get("team_total"), r.get("is_home_team"))
                    expected_pa_samples.append(pa)
                    by_source[src if src in by_source else "other"] += 1
                except Exception:  # noqa: BLE001  (defensive — read-only)
                    pass

    return {
        "now":                  now,
        "window_lo":            lo,
        "window_hi":            hi,
        "active_total":         total,
        "active_tb":            tb_total,
        "active_with_bo":       bo_total,
        "active_confirmed":     confirmed_total,
        "tb_with_bo":           bo_tb,
        "expected_pa_samples":  expected_pa_samples,
        "by_pa_source":         by_source,
    }


def _print_summary(snap: Dict[str, Any], sla_pct: float,
                   enforce_after_utc: int) -> None:
    now = snap["now"]
    print()
    print("#" * 78)
    print("#  PA-v2 LINEUP COVERAGE MONITOR  (read-only)")
    print("#" * 78)
    print(f"  date (UTC)              : {now.strftime('%Y-%m-%d')}")
    print(f"  now  (UTC)              : {now.strftime('%H:%M:%S')}")
    print(f"  active window (UTC)     : {snap['window_lo'].strftime('%m-%d %H:%M')}"
          f"  →  {snap['window_hi'].strftime('%m-%d %H:%M')}")
    print()
    print("=" * 78)
    print("  Active-slate coverage")
    print("=" * 78)
    n = snap["active_total"]
    bo = snap["active_with_bo"]
    cf = snap["active_confirmed"]
    tb = snap["active_tb"]
    tb_bo = snap["tb_with_bo"]
    bo_pct = _pct(bo, n)
    cf_pct = _pct(cf, n)
    tb_bo_pct = _pct(tb_bo, tb)
    print(f"  active mlb_live_props rows         : {n:>8,}")
    print(f"  active TB rows                     : {tb:>8,}")
    print(f"  active rows w/ batting_order       : {bo:>8,}  ({bo_pct:>5.1f}%)")
    print(f"  active rows w/ lineup_confirmed    : {cf:>8,}  ({cf_pct:>5.1f}%)")
    print(f"  active TB rows w/ batting_order    : {tb_bo:>8,}  ({tb_bo_pct:>5.1f}%)")
    src = snap["by_pa_source"]
    print(f"  TB rows by pa_source (sample)      : "
          f"lineup={src.get('lineup',0)}  fallback={src.get('fallback',0)}  "
          f"other={src.get('other',0)}")
    print()
    # expected_PA distribution
    print("=" * 78)
    print("  expected_PA distribution  (lineup-resolved TB rows only)")
    print("=" * 78)
    s = snap["expected_pa_samples"]
    if not s:
        print("  (no lineup-resolved TB rows yet — distribution would be a")
        print("   single point mass at 4.20 from the fallback rule)")
    else:
        s_sorted = sorted(s)
        n = len(s_sorted)
        q = lambda p: s_sorted[min(n - 1, int(p * (n - 1)))]  # noqa: E731
        print(f"  n={n}  min={s_sorted[0]:.2f}  med={q(.5):.2f}  "
              f"p75={q(.75):.2f}  max={s_sorted[-1]:.2f}  "
              f"avg={statistics.mean(s_sorted):.2f}")
    print()
    # SLA
    print("=" * 78)
    print(f"  SLA  (active-slate batting_order coverage ≥ {sla_pct:.0f}% "
          f"after {enforce_after_utc:02d}:00 UTC)")
    print("=" * 78)
    cur_hour = now.hour
    if cur_hour < enforce_after_utc:
        print(f"  current UTC hour {cur_hour:02d} < {enforce_after_utc:02d} — "
              f"SLA not yet enforceable (lineup window not opened).")
        print(f"  current coverage: {bo_pct:.1f}%  (informational only)")
    else:
        if n == 0:
            print("  no active props — SLA undefined (skipped).")
        elif bo_pct < sla_pct:
            print(f"  WARNING: active-slate batting_order coverage = "
                  f"{bo_pct:.1f}%  (< SLA {sla_pct:.0f}%).")
            print("  WARNING: PA-v2 is mostly running on the 4.2 fallback.")
            print("  WARNING: this is a measurement signal only — model, "
                  "gates, thresholds, and selection are UNCHANGED.")
        else:
            print(f"  ✓ coverage {bo_pct:.1f}% meets SLA {sla_pct:.0f}%")
    print()
    print("[MONITOR] DONE — read-only.  exit=0")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
async def _amain():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sla", type=float, default=60.0,
        help="active-slate batting_order coverage SLA in %% (default 60)")
    ap.add_argument(
        "--enforce-after-utc", type=int, default=22,
        help="only enforce SLA when current UTC hour ≥ this (default 22)")
    args = ap.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    snap = await _snapshot(db, datetime.now(timezone.utc))
    _print_summary(snap, args.sla, args.enforce_after_utc)


if __name__ == "__main__":
    asyncio.run(_amain())
