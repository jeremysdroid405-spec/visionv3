"""Validate MLB ECDF coverage after adding hits+runs+rbis / doubles /
stolen_bases artifacts. Rescores final-mlb-rt, counts coverage,
confirms no crashes, reports before/after on representative lines.
"""
from __future__ import annotations
import asyncio, os, sys, time
from collections import Counter, defaultdict
from math import erf, sqrt
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient
from services.scoring.recompute import recompute_sport
from services.probability import get_universal_ecdf

VERSION_TAG = "final-mlb-rt"
NEW_STATS = {"hits+runs+rbis", "doubles", "stolen_bases"}


def _canon(stat):
    s = (stat or "").lower().replace(" ", "_")
    al = {"tb":"total_bases","rbi":"rbis","hr":"home_runs","hrr":"hits+runs+rbis","hits+runs+rbi":"hits+runs+rbis"}
    return al.get(s, s)


def _gauss(proj, sigma, line):
    if sigma is None or sigma <= 0: return 0.5
    return 0.5 * (1.0 + erf((proj - line) / sigma / sqrt(2.0)))


async def coverage(db):
    methods = Counter()
    per_stat = defaultdict(Counter)
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True},
        {"stat_type": 1, "probability_method": 1, "_id": 0},
    ):
        m = d.get("probability_method") or "unset"
        methods[m] += 1
        per_stat[_canon(d.get("stat_type") or "")][m] += 1
    return methods, per_stat


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Before
    m_before, s_before = await coverage(db)
    print("=== BEFORE ===  methods:", dict(m_before))
    for stat in sorted(NEW_STATS):
        print(f"  {stat}: {dict(s_before.get(stat) or {})}")

    # Rescore
    print("\n--- rescoring final-mlb-rt ---")
    t0 = time.time()
    result = await recompute_sport(
        db=db, sport="mlb", version_tag=VERSION_TAG, dry_run=False,
    )
    print(f"took {time.time() - t0:.1f}s  processed={result.get('processed')} "
          f"written={result.get('written')}")

    # After
    m_after, s_after = await coverage(db)
    print("\n=== AFTER ===  methods:", dict(m_after))
    for stat in sorted(NEW_STATS):
        print(f"  {stat}: {dict(s_after.get(stat) or {})}")

    # Check no crashes (recompute returned successfully == no crash).
    # Verify new stats now use ECDF.
    uni = get_universal_ecdf()
    print("\n--- Direct probe on new artifacts ---")
    probes = [
        ("hits+runs+rbis", 2.0, 1.5),
        ("hits+runs+rbis", 1.5, 0.5),
        ("doubles", 0.5, 0.5),
        ("doubles", 0.3, 0.5),
        ("stolen_bases", 0.1, 0.5),
        ("stolen_bases", 0.3, 0.5),
    ]
    for stat, proj, line in probes:
        pred = uni.predict_over_probability("mlb", stat, proj, line)
        if pred is None:
            print(f"  {stat} proj={proj} line={line} -> None (bucket too small)")
        else:
            print(f"  {stat} proj={proj} line={line} -> p_over={pred.p_over:.3f} "
                  f"bucket={pred.bucket} n={pred.bucket_n}")

    # Before/after comparison on new-stat live docs (shadow).
    print("\n--- Before/after gap on .5 lines (shadow) ---")
    gap_rows = []
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "stat_type": {"$exists": True},
         "model_projection": {"$ne": None}, "model_sigma": {"$ne": None}},
        {"stat_type":1, "player_name":1, "line":1, "recommendation":1,
         "model_projection":1, "model_sigma":1, "p_true_model":1, "_id":0},
    ):
        canon = _canon(d.get("stat_type") or "")
        if canon not in NEW_STATS: continue
        line = d.get("line")
        if line is None or abs(line - round(line)) < 0.4: continue  # keep .5 lines
        proj = float(d["model_projection"]); sigma = float(d["model_sigma"])
        side = (d.get("recommendation") or "OVER").upper()
        gauss_p = _gauss(proj, sigma, float(line))
        pred = uni.predict_over_probability("mlb", canon, proj, float(line))
        if pred is None:
            ecdf_p = None
        else:
            ecdf_p = pred.p_over
        gap_rows.append({
            "stat": canon, "player": d.get("player_name"), "line": line,
            "side": side, "gauss": gauss_p, "ecdf": ecdf_p,
            "persisted_p_true": d.get("p_true_model"),
        })

    from collections import defaultdict as dd
    per = dd(list)
    for r in gap_rows:
        if r["ecdf"] is not None:
            per[r["stat"]].append(abs(r["gauss"] - r["ecdf"]))
    for stat in sorted(NEW_STATS):
        arr = per.get(stat) or []
        if arr:
            a = np.array(arr)
            print(f"  {stat}: n={len(a)} mean|Δ|(gauss vs ecdf)={a.mean():.3f} "
                  f"max|Δ|={a.max():.3f}")
        else:
            print(f"  {stat}: no .5-line live props to compare")

    # Sample rows
    print("\n--- Sample tail rows (ecdf p_over high) ---")
    gap_rows_with_ecdf = [r for r in gap_rows if r["ecdf"] is not None]
    gap_rows_with_ecdf.sort(key=lambda r: r["ecdf"], reverse=True)
    for r in gap_rows_with_ecdf[:10]:
        print(f"  {r['player']:20s} {r['stat']:18s} {r['line']:>4} "
              f"gauss={r['gauss']:.3f} ecdf={r['ecdf']:.3f} "
              f"persisted_p_model={r['persisted_p_true']}")

    # Invariants
    print("\n--- Invariants ---")
    n_neg = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True,
         "model_projection": {"$lt": 0}})
    print(f"negative projections: {n_neg}")
    tiers = Counter()
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True}, {"tier":1,"_id":0}):
        tiers[d.get("tier") or "-"] += 1
    print(f"tiers: {dict(tiers)}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
