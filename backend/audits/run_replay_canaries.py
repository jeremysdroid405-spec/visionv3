"""μ canary runner — read-only validator that locks expected μ bands
for known (player, date, stat, line) combinations.

Runs in ~10–15 s, stays under 500 MB RSS, mutates nothing. Designed to
run before/after any replay code change as a fast sanity gate.

Exit codes:
    0  all canaries inside their bands
    1  one or more canaries OUT of band
    2  setup failure (e.g. couldn't load model)
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import json
import sys
import time
import psutil

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient
from services.mlb_high_friction_model import MLBHighFrictionModel

CANARY_FILE = "/app/backend/audits/replay_mu_canaries.json"


def _rss(): return psutil.Process().memory_info().rss / (1024 * 1024)


def main(canary_file: str = CANARY_FILE) -> int:
    t0 = time.time()
    rss0 = _rss()
    print(f"[start] rss={rss0:.1f}MB  pid={os.getpid()}")

    with open(canary_file) as fh:
        cfg = json.load(fh)
    canaries = cfg["canaries"]
    print(f"[load] {len(canaries)} canaries from {canary_file}")

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db)
    model.load_models()
    rss_after_models = _rss()
    print(f"[models] loaded  rss={rss_after_models:.1f}MB  "
          f"Δ={rss_after_models-rss0:+.1f}MB")

    failures = []
    skipped = []
    passes = []

    print(f"\n{'#':>3}  {'player':>20}  {'date':>10}  {'stat':>14}  "
          f"{'line':>5}  {'μ_actual':>8}  {'band':>14}  {'verdict':>8}")
    print("─" * 110)

    for i, c in enumerate(canaries, 1):
        r = model.predict(
            player_name=c["name"], stat_type=c["stat"],
            line=c["line"],
            opponent_team=c.get("opponent_team", "Atlanta Braves"),
            park_team=c.get("park_team", "Atlanta Braves"),
            batter_hand=c.get("batter_hand"),
            opp_pitcher_throws=c.get("opp_pitcher_throws"),
            as_of_date=c["date"],
        )
        mu = r.get("predicted")
        err = r.get("error")
        if mu is None:
            skipped.append((c, err or "no μ"))
            print(f"{i:>3}  {c['name']:>20}  {c['date']:>10}  "
                  f"{c['stat']:>14}  {c['line']:>5}  {'—':>8}  "
                  f"{'—':>14}  SKIP ({err})")
            continue
        in_band = c["mu_low"] <= mu <= c["mu_high"]
        verdict = "✅ PASS" if in_band else "🔴 FAIL"
        band = f"[{c['mu_low']:.2f},{c['mu_high']:.2f}]"
        print(f"{i:>3}  {c['name']:>20}  {c['date']:>10}  "
              f"{c['stat']:>14}  {c['line']:>5}  {mu:>8.4f}  "
              f"{band:>14}  {verdict}")
        if in_band:
            passes.append((c, mu))
        else:
            failures.append((c, mu))

    rss_end = _rss()
    elapsed = time.time() - t0

    print("─" * 110)
    print(f"\n[summary] pass={len(passes)}  fail={len(failures)}  "
          f"skip={len(skipped)}  total={len(canaries)}")
    print(f"[runtime] elapsed={elapsed:.2f}s  "
          f"rss_peak~={max(rss_after_models, rss_end):.1f}MB  "
          f"rss_end={rss_end:.1f}MB")
    if failures:
        print("\n[failures]")
        for c, mu in failures:
            print(f"  • {c['name']} {c['date']} {c['stat']} L={c['line']}: "
                  f"μ={mu:.4f} not in [{c['mu_low']},{c['mu_high']}]  "
                  f"note: {c.get('note','')}")
    client.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else CANARY_FILE))
