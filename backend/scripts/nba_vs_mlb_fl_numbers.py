"""NBA Front Lines vs MLB Front Lines — actual NUMBERS comparison.

For each sport's `front_lines` tier (in `{sport}_prop_scores` /
version_tag=final-{sport}-rt / active=True), reports:
  • per-stat-family breakdown
  • hit_rate distribution (min / median / mean / max)
  • cv distribution
  • edge_vs_fair distribution
  • vision_score distribution
  • OVER vs UNDER split
  • tp_source (devig vs one_sided)
  • book_count distribution

Read-only aggregation, OOM-safe.
"""
import asyncio
import os
import statistics
import sys

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def percentiles(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {
        "n": n,
        "min": s[0],
        "p25": s[n // 4],
        "median": s[n // 2],
        "p75": s[(3 * n) // 4],
        "max": s[-1],
        "mean": sum(s) / n,
    }


async def collect(coll, sport):
    """Pull just the FL props (small subset) into memory — safe."""
    match = {"version_tag": f"final-{sport}-rt", "active": True, "tier": "front_lines"}
    projection = {
        "_id": 0, "stat_type": 1, "recommendation": 1,
        "hit_rate_l20": 1, "hit_rate_l10": 1, "hit_rate_l5": 1,
        "hit_rate_over": 1, "hit_rate_under": 1,
        "cv": 1, "edge_vs_fair": 1, "vision_score": 1,
        "tp": 1, "tp_source": 1, "tp_books_used": 1,
        "book_count": 1, "p_true_active": 1,
        "model_projection": 1, "line": 1,
    }
    out = []
    async for d in coll.find(match, projection):
        out.append(d)
    return out


def summarize(props, label):
    if not props:
        print(f"\n## {label}: NO PROPS\n")
        return

    print(f"\n## {label} — n = {len(props)}")
    over = [p for p in props if (p.get("recommendation") or "").upper() == "OVER"]
    under = [p for p in props if (p.get("recommendation") or "").upper() == "UNDER"]
    print(f"  OVER: {len(over)} | UNDER: {len(under)}")

    # Per-stat-family
    by_stat: dict = {}
    for p in props:
        st = p.get("stat_type") or "—"
        by_stat.setdefault(st, []).append(p)
    print()
    print("  | Stat | n | OVER/UNDER | hit_rate (med) | cv (med) | edge_pct (med) | vision (med) | tp (med) | book_ct (med) |")
    print("  | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for st, plist in sorted(by_stat.items(), key=lambda x: -len(x[1])):
        ov = sum(1 for p in plist if (p.get("recommendation") or "").upper() == "OVER")
        un = len(plist) - ov
        hr = [p.get("hit_rate_l20") for p in plist if p.get("hit_rate_l20") is not None]
        cv = [p.get("cv") for p in plist if p.get("cv") is not None]
        ed = [p.get("edge_vs_fair") for p in plist if p.get("edge_vs_fair") is not None]
        vs = [p.get("vision_score") for p in plist if p.get("vision_score") is not None]
        tp = [p.get("tp") for p in plist if p.get("tp") is not None]
        bc = [p.get("book_count") for p in plist if p.get("book_count") is not None]
        hr_med = statistics.median(hr) if hr else None
        cv_med = statistics.median(cv) if cv else None
        ed_med = (statistics.median(ed) * 100) if ed else None
        vs_med = statistics.median(vs) if vs else None
        tp_med = statistics.median(tp) if tp else None
        bc_med = statistics.median(bc) if bc else None
        print(f"  | {st} | {len(plist)} | {ov}/{un} | "
              f"{fmt(hr_med)} | {fmt(cv_med)} | {fmt(ed_med)} | "
              f"{fmt(vs_med)} | {fmt(tp_med)} | {fmt(bc_med)} |")

    # Whole tier rollup
    def col(k, scale=1.0):
        return [
            (p.get(k) or 0) * scale if isinstance(p.get(k), (int, float)) else None
            for p in props
        ]
    print()
    print(f"  ## Tier-wide percentiles (all {len(props)} {label} props):")
    for label2, key, scale in (
        ("hit_rate_l20", "hit_rate_l20", 1.0),
        ("hit_rate_l5",  "hit_rate_l5",  1.0),
        ("cv",           "cv",           1.0),
        ("edge_pct",     "edge_vs_fair", 100.0),
        ("vision_score", "vision_score", 1.0),
        ("tp",           "tp",           1.0),
        ("book_count",   "book_count",   1.0),
        ("tp_books_used","tp_books_used",1.0),
    ):
        vals = [v for v in col(key, scale) if v is not None]
        if not vals:
            print(f"     {label2}: no data")
            continue
        p = percentiles(vals)
        print(f"     {label2:18s} n={p['n']:4d} min={p['min']:8.2f} "
              f"p25={p['p25']:8.2f} med={p['median']:8.2f} "
              f"p75={p['p75']:8.2f} max={p['max']:8.2f} mean={p['mean']:8.2f}")

    # TP source breakdown
    devig = sum(1 for p in props if p.get("tp_source") == "devig")
    onesided = sum(1 for p in props if p.get("tp_source") == "one_sided")
    print(f"\n  tp_source: devig={devig} ({100*devig/len(props):.1f}%) | "
          f"one_sided={onesided} ({100*onesided/len(props):.1f}%)")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "pick_vision")]
    nba = await collect(db["nba_prop_scores"], "nba")
    mlb = await collect(db["mlb_prop_scores"], "mlb")
    summarize(nba, "NBA Front Lines")
    summarize(mlb, "MLB Front Lines")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
