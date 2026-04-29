"""
Vision v1 vs v2 — Live Slate Comparison + Structural Backtest
==============================================================

Read-only script. Pulls every NBA prop in `final-nba-rt`, recomputes
vision_v2 in memory using the live score-doc fields (no DB writes),
then produces:

  1. Top-20 right-side picks by v2 (with v1, edge, TP, projection).
  2. Bottom-20 picks by v2 (the wrong-side suspects v1 boosted).
  3. Random 20 representative samples for spot-checking.
  4. Structural backtest:
       - correlation(v2, edge_pct)
       - correlation(v2, p_true_active)
       - correlation(v2, direction_alignment)
       - by-side / by-stat / by-tier mean v2.
       - share of v1-top-20 with NEGATIVE direction_alignment
         (= picks v1 elevated despite wrong-side projection).
"""
import asyncio
import json
import os
import statistics
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

import sys
sys.path.insert(0, "/app/backend")
from services.scoring.vision_v2 import compute_vision_v2  # noqa: E402


def _projection(d):
    for k in ("vk2_projection", "model_projection",
              "mu_after_availability_guard", "mu_recency_blend_l20"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _sigma(d):
    for k in ("distribution_sigma", "model_sigma", "sigma"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _v2_for_doc(d):
    side = (d.get("recommendation") or "").upper()
    return compute_vision_v2(
        side=side,
        projection=_projection(d),
        line=d.get("line"),
        sigma=_sigma(d),
        p_true_active=d.get("p_true_active"),
        tp=d.get("tp"),
        edge_pct=d.get("edge_pct"),
        cv=d.get("cv"),
        hit_rate=(d.get("hit_rate_over") if side == "OVER"
                  else d.get("hit_rate_under")),
        hit_rate_sample_size=d.get("hit_rate_sample_size"),
        stat_family=d.get("stat_family"),
        prop_type=d.get("pp_multiplier_label"),
        books_count=d.get("book_count"),
        tp_books_used=d.get("tp_books_used"),
        tp_source=d.get("tp_source"),
        injury_context={"usage_vacuum_factor":
                        d.get("usage_vacuum_factor")},
        usage_spike=d.get("usage_spike"),
        matchup_strength=d.get("matchup_strength"),
        pace_factor=d.get("pace_factor"),
    )


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    den_y = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _fmt_row(d, v2):
    side = (d.get("recommendation") or "").upper()
    hr = d.get("hit_rate_over") if side == "OVER" else d.get("hit_rate_under")
    return (
        f"{(d.get('player_name') or '?')[:22]:<22} "
        f"{(d.get('stat_type') or '?'):<5} "
        f"{str(d.get('line')):<5} {side:<5} "
        f"v1={d.get('vision_score') or 0:>5.1f} "
        f"v2={v2['vision_score_v2']:>5.1f} "
        f"align={v2['vision_direction_alignment'] or 0:>+5.2f} "
        f"proj={_projection(d) or 0:>6.2f} "
        f"edge={d.get('edge_pct') or 0:>5.1f} "
        f"tp={d.get('tp') or 0:>5.1f} "
        f"p={d.get('p_true_active') or 0:>4.2f} "
        f"hr={hr or 0:>4.0f} "
        f"cv={d.get('cv') or 0:>5.3f} "
        f"tier={(d.get('tier') or '?')[:8]:<8}"
    )


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(f"[VISION-V1-vs-V2 BACKTEST] {datetime.now(timezone.utc).isoformat()}")
    print("=" * 132)

    docs = await db.nba_prop_scores.find(
        {"version_tag": "final-nba-rt"},
        {"_id": 0},
    ).to_list(length=20000)

    print(f"Total NBA props: {len(docs)}")

    rows = []
    for d in docs:
        if not isinstance(d.get("vision_score"), (int, float)):
            continue
        v2 = _v2_for_doc(d)
        rows.append((d, v2))

    print(f"Scorable rows (v1 present): {len(rows)}")
    print()

    # 1. Top-20 by v2
    rows_by_v2 = sorted(rows, key=lambda r: -r[1]["vision_score_v2"])
    print("=" * 132)
    print("TOP 20 BY VISION V2 (right-side, model-aligned)")
    print("=" * 132)
    for d, v2 in rows_by_v2[:20]:
        print("  " + _fmt_row(d, v2))

    # 2. Bottom-20 by v2 (probably wrong-side picks v1 elevated)
    print()
    print("=" * 132)
    print("BOTTOM 20 BY VISION V2 (wrong-side / no-confidence)")
    print("=" * 132)
    for d, v2 in rows_by_v2[-20:]:
        print("  " + _fmt_row(d, v2))

    # 3. The user's specific 20 ideally — top-20 v1 vs their v2
    rows_by_v1 = sorted(rows, key=lambda r: -(r[0].get("vision_score") or 0))
    print()
    print("=" * 132)
    print("V1 TOP 20 vs V2 SCORE (the picks v1 was elevating)")
    print("=" * 132)
    for d, v2 in rows_by_v1[:20]:
        print("  " + _fmt_row(d, v2))

    # 4. Structural backtest
    print()
    print("=" * 132)
    print("STRUCTURAL BACKTEST")
    print("=" * 132)
    v2s = [r[1]["vision_score_v2"] for r in rows]
    v1s = [r[0].get("vision_score") or 0.0 for r in rows]
    edges = [r[0].get("edge_pct") or 0.0 for r in rows]
    ps = [r[0].get("p_true_active") or 0.0 for r in rows]
    aligns = [r[1]["vision_direction_alignment"] or 0.0 for r in rows]

    print(f"  count                                           : {len(rows)}")
    print(f"  v2 mean / median / stdev                        : "
          f"{statistics.mean(v2s):.2f} / {statistics.median(v2s):.2f} / "
          f"{statistics.stdev(v2s):.2f}")
    print(f"  v1 mean / median / stdev                        : "
          f"{statistics.mean(v1s):.2f} / {statistics.median(v1s):.2f} / "
          f"{statistics.stdev(v1s):.2f}")
    print(f"  pearson(v2, v1)                                 : "
          f"{_pearson(v2s, v1s):.4f}")
    print(f"  pearson(v2, edge_pct)                           : "
          f"{_pearson(v2s, edges):.4f}")
    print(f"  pearson(v2, p_true_active)                      : "
          f"{_pearson(v2s, ps):.4f}")
    print(f"  pearson(v2, direction_alignment)                : "
          f"{_pearson(v2s, aligns):.4f}")
    print(f"  pearson(v1, edge_pct)                           : "
          f"{_pearson(v1s, edges):.4f}")
    print(f"  pearson(v1, direction_alignment)                : "
          f"{_pearson(v1s, aligns):.4f}")

    # The killer metric: % of v1-top-20 with NEGATIVE direction_alignment
    v1_top20 = rows_by_v1[:20]
    n_wrong_side = sum(1 for d, v2 in v1_top20
                       if (v2["vision_direction_alignment"] or 0) < 0)
    print(f"  v1-top-20 with NEGATIVE direction_alignment     : "
          f"{n_wrong_side}/20  ← v1 elevated wrong-side picks")
    v2_top20 = rows_by_v2[:20]
    n_wrong_side_v2 = sum(1 for d, v2 in v2_top20
                          if (v2["vision_direction_alignment"] or 0) < 0)
    print(f"  v2-top-20 with NEGATIVE direction_alignment     : "
          f"{n_wrong_side_v2}/20  ← v2 should be 0/20")

    # By tier
    print()
    print("  Mean vision_score_v2 by tier:")
    by_tier = {}
    for d, v2 in rows:
        t = d.get("tier") or "unknown"
        by_tier.setdefault(t, []).append(v2["vision_score_v2"])
    for t, vals in sorted(by_tier.items()):
        print(f"    {t:<14} n={len(vals):>4}  mean v2={statistics.mean(vals):>6.2f}  "
              f"max={max(vals):>5.1f}  min={min(vals):>5.1f}")

    # By side
    print()
    print("  Mean vision_score_v2 by side:")
    by_side = {}
    for d, v2 in rows:
        s = (d.get("recommendation") or "?").upper()
        by_side.setdefault(s, []).append(v2["vision_score_v2"])
    for s, vals in sorted(by_side.items()):
        print(f"    {s:<5}  n={len(vals):>4}  mean v2={statistics.mean(vals):>6.2f}")

    # By stat family
    print()
    print("  Mean vision_score_v2 by stat_type (top 10):")
    by_stat = {}
    for d, v2 in rows:
        s = (d.get("stat_type") or "?")
        by_stat.setdefault(s, []).append(v2["vision_score_v2"])
    leaderboard = sorted(by_stat.items(),
                         key=lambda kv: -statistics.mean(kv[1]))
    for s, vals in leaderboard[:10]:
        print(f"    {s:<35}  n={len(vals):>4}  mean v2={statistics.mean(vals):>6.2f}")

    # Persist
    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/vision_v1_vs_v2_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version_tag": "final-nba-rt",
        "count": len(rows),
        "v2_top_20": [
            {
                "player": d.get("player_name"), "stat": d.get("stat_type"),
                "line": d.get("line"), "side": d.get("recommendation"),
                "v1": d.get("vision_score"), "v2": v2["vision_score_v2"],
                "alignment": v2["vision_direction_alignment"],
                "edge_pct": d.get("edge_pct"), "tp": d.get("tp"),
                "p_true_active": d.get("p_true_active"),
                "tier": d.get("tier"),
                "components": {
                    k: v2[k] for k in v2 if k.startswith("vision_") and k.endswith("component")
                },
            }
            for d, v2 in rows_by_v2[:20]
        ],
        "v1_top_20_with_v2": [
            {
                "player": d.get("player_name"), "stat": d.get("stat_type"),
                "line": d.get("line"), "side": d.get("recommendation"),
                "v1": d.get("vision_score"), "v2": v2["vision_score_v2"],
                "alignment": v2["vision_direction_alignment"],
                "edge_pct": d.get("edge_pct"),
                "wrong_side": (v2["vision_direction_alignment"] or 0) < 0,
            }
            for d, v2 in rows_by_v1[:20]
        ],
        "structural": {
            "v2_mean":   statistics.mean(v2s),
            "v1_mean":   statistics.mean(v1s),
            "pearson_v2_v1":     _pearson(v2s, v1s),
            "pearson_v2_edge":   _pearson(v2s, edges),
            "pearson_v2_p":      _pearson(v2s, ps),
            "pearson_v2_align":  _pearson(v2s, aligns),
            "pearson_v1_edge":   _pearson(v1s, edges),
            "pearson_v1_align":  _pearson(v1s, aligns),
            "v1_top20_wrong_side": n_wrong_side,
            "v2_top20_wrong_side": n_wrong_side_v2,
        },
    }
    with open(path, "w") as f:
        json.dump(payload, f, default=str, indent=2)
    print()
    print(f"ARTIFACT: {path}")


if __name__ == "__main__":
    asyncio.run(main())
