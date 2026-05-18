"""Stat-family segmented grid-search sweep.

Builds on `universal_gate_grid_search.py` (imports its candidate
data model, combo evaluator, aggregator, CI machinery and pool
builder unchanged) — and adds:

  1. **Per stat-family pools** (filter the cached tier-pool in-memory
     so we never re-run the pipeline per stat family).
  2. **Odds-band breakdown** by 7 American-odds bins.
  3. **Daily stability table** for the top-10 ROI combos.
  4. **Outlier dependency check** (top-1 / top-5 single-pick profit %
     of total profit on the recommended combo).
  5. **Sharpness signal correlation** — Pearson r between each metric
     (cv, edge_pct, tp, hit_rate_l20) and per-pick `profit_units` on
     the recommended combo.
  6. **Book-quality breakdown** (devig / one_sided_std / one_sided_alt)
     — already produced by the base tool, kept here for parity.

Artifacts (per-(tier × stat_family)):
  • `audits/gss_{sport}_{tier}_{stat_family}_{start}_{end}_{stamp}.json`
  • `audits/gss_{sport}_{tier}_{stat_family}_{start}_{end}_{stamp}.csv`

Master summary:
  • `audits/gss_master_summary_{sport}_{start}_{end}_{stamp}.csv`

HISTORICAL TEST ONLY. NO production / live / threshold writes.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

# Reuse EVERYTHING from the base tool.
from audits.universal_gate_grid_search import (  # noqa: E402
    Candidate, Combo, _row_to_candidate, _passes_combo,
    _aggregate, _bucket_breakdown, _select_combos,
    _balanced_score, _build_candidate_pool, _dates_in_range,
    _stat_family_breakdown, _losing_archetypes,
    _gate_failure_waterfall, PROD_BASELINES, _wilson_ci,
)
from services.pipeline import PIPELINE_VERSION


# ── Required stat families (per spec) ──────────────────────────────
REQUIRED_STAT_FAMILIES = [
    "hits", "hits_runs_rbis", "total_bases",
    "runs", "rbis",
    "batter_strikeouts", "pitcher_strikeouts",
    "earned_runs", "pitcher_hits_allowed", "pitcher_walks",
    "stolen_bases", "batter_walks",
]


# ── Odds-band partition (per spec) ─────────────────────────────────
def _odds_band(odds: Optional[int]) -> str:
    if odds is None:
        return "unknown"
    if odds <= -300:           return "[-500, -300]"
    if odds <= -200:           return "[-299, -200]"
    if odds <= -150:           return "[-199, -150]"
    if odds <= -110:           return "[-149, -110]"
    if odds <=  100:           return "[-109, +100]"
    if odds <=  200:           return "[+101, +200]"
    return "[+201, +∞]"


_ODDS_BAND_ORDER = [
    "[-500, -300]", "[-299, -200]", "[-199, -150]",
    "[-149, -110]", "[-109, +100]", "[+101, +200]",
    "[+201, +∞]", "unknown",
]


def _odds_band_breakdown(rows: List[Candidate]) -> List[Dict[str, Any]]:
    by_b: Dict[str, List[Candidate]] = defaultdict(list)
    for c in rows:
        by_b[_odds_band(c.odds)].append(c)
    out = []
    for band in _ODDS_BAND_ORDER:
        items = by_b.get(band, [])
        if not items:
            continue
        out.append({"odds_band": band, **_aggregate(items)})
    return out


# ── Daily stability ────────────────────────────────────────────────
def _daily_stability(rows: List[Candidate]) -> Dict[str, Any]:
    """Group rows by `game_date`, aggregate per day. Returns daily
    rows + a single `consistency_score` in [0, 1].

    consistency_score = (# days with non-negative ROI) / (# days with
    at least one graded pick). 1.0 ⇒ profitable on every day with
    a graded pick. None ⇒ no graded days.
    """
    by_d: Dict[str, List[Candidate]] = defaultdict(list)
    for c in rows:
        if c.game_date:
            by_d[c.game_date].append(c)
    daily: List[Dict[str, Any]] = []
    pos_days = 0
    grd_days = 0
    for d in sorted(by_d.keys()):
        a = _aggregate(by_d[d])
        if a["n_graded"] > 0:
            grd_days += 1
            if (a["roi_pct"] or 0.0) >= 0.0:
                pos_days += 1
        daily.append({"date": d, **a})
    score = (pos_days / grd_days) if grd_days else None
    return {"days": daily, "consistency_score":
            round(score, 3) if score is not None else None,
            "graded_days": grd_days}


# ── Outlier dependency ─────────────────────────────────────────────
def _outlier_dependency(rows: List[Candidate]) -> Dict[str, Any]:
    """Detect whether the combo's P&L is concentrated in a small
    number of big wins. Returns top-1 / top-5 contribution as a
    fraction of total positive profit, plus an `is_outlier_dependent`
    flag (True when top-1 > 30 % of net P&L or top-5 > 60 %)."""
    pnls = [c.profit_units for c in rows
            if c.grade_status in ("win", "loss", "push")]
    total_pos = sum(p for p in pnls if p > 0)
    net = sum(pnls)
    pnls_sorted = sorted(pnls, reverse=True)
    top1 = pnls_sorted[0] if pnls_sorted else 0.0
    top5_sum = sum(pnls_sorted[:5]) if pnls_sorted else 0.0
    pct_of_pos = lambda x: (100.0 * x / total_pos) if total_pos else None
    pct_of_net = lambda x: (100.0 * x / net) if net else None
    is_outlier = False
    if net > 0:
        if (top1 / net) > 0.30:
            is_outlier = True
        elif (top5_sum / net) > 0.60:
            is_outlier = True
    return {
        "top1_profit_units": round(top1, 4),
        "top5_profit_units": round(top5_sum, 4),
        "top1_pct_of_positive_profit":
            round(pct_of_pos(top1), 2) if pct_of_pos(top1) is not None else None,
        "top5_pct_of_positive_profit":
            round(pct_of_pos(top5_sum), 2) if pct_of_pos(top5_sum) is not None else None,
        "top1_pct_of_net": round(pct_of_net(top1), 2)
            if pct_of_net(top1) is not None else None,
        "top5_pct_of_net": round(pct_of_net(top5_sum), 2)
            if pct_of_net(top5_sum) is not None else None,
        "is_outlier_dependent": is_outlier,
        "n_graded_with_pnl": len(pnls),
    }


# ── Sharpness correlation ──────────────────────────────────────────
def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    denx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    deny = math.sqrt(sum((b - my) ** 2 for b in ys))
    if denx == 0 or deny == 0:
        return None
    return round(num / (denx * deny), 4)


def _sharpness_signal(rows: List[Candidate]) -> Dict[str, Any]:
    """Pearson r between each metric and per-pick profit_units on the
    graded subset. Positive r ⇒ higher metric predicts higher profit.
    None when the metric or the y-vector has fewer than 3 valid points
    or zero variance."""
    graded = [c for c in rows
              if c.grade_status in ("win", "loss", "push")]
    pnl = [c.profit_units for c in graded]
    metrics = {
        "cv": [c.cv for c in graded],
        "edge_pct": [c.edge_pct for c in graded],
        "tp": [c.tp for c in graded],
        "hit_rate_l20": [c.hit_rate_l20 for c in graded],
        "p_model_pct": [c.p_model_pct for c in graded],
    }
    out: Dict[str, Any] = {"n_graded": len(graded)}
    for name, xs in metrics.items():
        clean_x = [x for x in xs if x is not None]
        clean_y = [pnl[i] for i, x in enumerate(xs) if x is not None]
        out[f"corr_{name}_vs_profit"] = _pearson(clean_x, clean_y)
    return out


# ── Side comparison ───────────────────────────────────────────────
def _side_compare(rows: List[Candidate]) -> Dict[str, Any]:
    over = [c for c in rows if c.side == "OVER"]
    under = [c for c in rows if c.side == "UNDER"]
    return {
        "over_only":   _aggregate(over),
        "under_only":  _aggregate(under),
        "combined":    _aggregate(rows),
    }


# ──────────────────────────────────────────────────────────────────
async def _build_tier_pool(
        db, *, sport: str, tier: str,
        dates: List[str], snapshot_time: str,
        disable_all_gates_for_accuracy_test: bool = False,
) -> Tuple[List[Candidate], List[Dict[str, Any]]]:
    """Build the WHOLE-tier candidate pool across all dates. One
    pipeline pass per (date) via the existing base-tool builder."""
    pool: List[Candidate] = []
    summaries: List[Dict[str, Any]] = []
    for d_iso in dates:
        snap_iso = f"{d_iso}T{snapshot_time}"
        try:
            day_pool, summary = await _build_candidate_pool(
                db, sport=sport, tier=tier,
                snapshot_iso=snap_iso, game_date=d_iso,
                test_id=(f"GSS-{sport.upper()}-"
                          f"{d_iso.replace('-','')}-"
                          f"{tier.upper()[:4]}-POOL"),
                disable_all_gates_for_accuracy_test=(
                    disable_all_gates_for_accuracy_test
                ),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [pool][{tier}][{d_iso}][ERROR] {exc!r}")
            continue
        # Keep only rows actually routed to this tier (the override
        # knobs admit everything, but we filter back to the target tier
        # so SH≠FL≠WZ pools stay disjoint).
        day_pool = [c for c in day_pool
                    if c.routed_tier is None or c.routed_tier == tier]
        pool.extend(day_pool)
        summaries.append({
            "date": d_iso, "serial": summary["serial"],
            "rows_scanned": summary.get("rows_scanned"),
            "candidates_kept": len(day_pool),
        })
        print(f"  [pool][{tier}][{d_iso}] +{len(day_pool)} "
              f"(scanned {summary.get('rows_scanned')})")
    return pool, summaries


# ──────────────────────────────────────────────────────────────────
def _evaluate_sf(sf_pool: List[Candidate],
                  *, min_graded_sample_size: int,
                  max_combinations: int,
                  graded_total: int,
                  ) -> Dict[str, Any]:
    """Run the combo sweep on a stat-family-filtered pool."""
    combos, plan = _select_combos(max_combinations)
    rows_out: List[Dict[str, Any]] = []
    for combo in combos:
        passing = [c for c in sf_pool if _passes_combo(c, combo)]
        agg = _aggregate(passing)
        bal = _balanced_score(
            agg, volume_target=max(graded_total // 4, 20),
            min_sample=min_graded_sample_size,
        )
        rows_out.append({
            "combo_label": combo.as_label(),
            "combo": combo.as_dict(),
            "overall": agg,
            "balanced_score": bal,
            "by_bucket": _bucket_breakdown(passing),
            "by_side": _side_compare(passing),
        })

    qualified = [r for r in rows_out
                 if (r["overall"].get("n_graded") or 0)
                 >= min_graded_sample_size]

    def _top(n, key):
        rows = [r for r in qualified
                if r["overall"].get(key) is not None]
        rows.sort(key=lambda r: r["overall"][key], reverse=True)
        return rows[:n]

    def _bottom(n, key):
        rows = [r for r in qualified
                if r["overall"].get(key) is not None]
        rows.sort(key=lambda r: r["overall"][key])
        return rows[:n]

    leaderboards = {
        "by_balanced_score": sorted(
            [r for r in qualified if r["balanced_score"] is not None],
            key=lambda r: r["balanced_score"], reverse=True,
        )[:20],
        "by_hit_rate":      _top(20, "hit_rate_pct"),
        "by_roi":           _top(20, "roi_pct"),
        "by_profit_units":  _top(20, "profit_units"),
        "worst_by_roi":     _bottom(20, "roi_pct"),
    }
    return {
        "plan": plan,
        "n_combos": len(rows_out),
        "rows": rows_out,
        "leaderboards": leaderboards,
    }


# ──────────────────────────────────────────────────────────────────
async def run_sweep(
        *, sport: str = "mlb",
        tiers: List[str],
        stat_families: List[str],
        date_start: str, date_end: str,
        snapshot_time: str = "11:00:00Z",
        allow_one_sided: bool = True,
        min_graded_sample_size: int = 50,
        max_combinations: int = 250,
        disable_all_gates_for_accuracy_test: bool = False,
        artifact_dir: Path = Path("/app/backend/audits"),
) -> Dict[str, Any]:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dates = _dates_in_range(date_start, date_end)

    print(f"\n{'='*82}")
    print("  STAT-FAMILY SEGMENTED GRID-SEARCH SWEEP")
    print(f"  sport={sport}  tiers={tiers}  stat_families={len(stat_families)}")
    print(f"  dates={date_start} → {date_end} ({len(dates)} days)")
    print(f"  snapshot={snapshot_time}  allow_one_sided={allow_one_sided}")
    print(f"  min_graded={min_graded_sample_size}  "
          f"max_combos={max_combinations} (per stat-family)")
    print(f"  disable_all_gates={disable_all_gates_for_accuracy_test}")
    print(f"  pipeline={PIPELINE_VERSION}")
    print(f"{'='*82}")

    # ── Stage 1: build tier pools (one pipeline pass per (tier, date))
    pool_by_tier: Dict[str, List[Candidate]] = {}
    pool_summaries: Dict[str, List[Dict[str, Any]]] = {}
    for tier in tiers:
        print(f"\n[pool-build] {tier} — {len(dates)} pipeline passes…")
        pool, summaries = await _build_tier_pool(
            db, sport=sport, tier=tier,
            dates=dates, snapshot_time=snapshot_time,
            disable_all_gates_for_accuracy_test=(
                disable_all_gates_for_accuracy_test
            ),
        )
        pool_by_tier[tier] = pool
        pool_summaries[tier] = summaries
        print(f"[pool-build] {tier} TOTAL: {len(pool)} candidates")

    # ── Stage 2: per (tier × stat_family) evaluation ───────────────
    master_rows: List[Dict[str, Any]] = []
    per_sf_artifacts: List[Dict[str, str]] = []
    for tier in tiers:
        tier_pool = pool_by_tier[tier]
        # Discover what families are actually present in the pool
        present = Counter(c.stat_family for c in tier_pool)
        print(f"\n[{tier}] families present in pool: {dict(present)}")
        for sf in stat_families:
            sf_pool = [c for c in tier_pool if c.stat_family == sf]
            graded_sf = sum(1 for c in sf_pool
                            if c.grade_status in ("win","loss","push"))
            print(f"  [{tier}/{sf}] n_pool={len(sf_pool)} "
                  f"graded={graded_sf}")
            if len(sf_pool) == 0 or graded_sf < min_graded_sample_size:
                # Still emit a master-row stub so it shows up
                master_rows.append({
                    "tier": tier, "stat_family": sf,
                    "status": ("no_pool" if len(sf_pool) == 0
                               else "insufficient_graded"),
                    "n_pool": len(sf_pool),
                    "graded_n": graded_sf,
                })
                continue

            sweep = _evaluate_sf(
                sf_pool,
                min_graded_sample_size=min_graded_sample_size,
                max_combinations=max_combinations,
                graded_total=graded_sf,
            )

            # ── Recommended combo = top balanced (fallback: top ROI) ─
            top_bal = sweep["leaderboards"]["by_balanced_score"]
            top_roi = sweep["leaderboards"]["by_roi"]
            recommended = top_bal[0] if top_bal else (
                top_roi[0] if top_roi else None)

            # ── New analyses on the recommended combo's passing rows
            if recommended is not None:
                rec_combo = Combo(**{
                    k: v for k, v in recommended["combo"].items()
                    if k in Combo.__dataclass_fields__
                })
                rec_passing = [c for c in sf_pool
                               if _passes_combo(c, rec_combo)]
            else:
                rec_passing = []

            odds_bands     = _odds_band_breakdown(rec_passing)
            daily          = _daily_stability(rec_passing)
            outlier        = _outlier_dependency(rec_passing)
            sharpness      = _sharpness_signal(sf_pool)
            buckets        = _bucket_breakdown(rec_passing)
            side_split     = _side_compare(rec_passing)
            gate_waterfall = _gate_failure_waterfall(
                sf_pool, rec_combo) if recommended is not None else {}

            # Top-10 ROI combos daily stability
            top10_roi = sweep["leaderboards"]["by_roi"][:10]
            top10_daily: List[Dict[str, Any]] = []
            for r in top10_roi:
                combo = Combo(**{
                    k: v for k, v in r["combo"].items()
                    if k in Combo.__dataclass_fields__
                })
                passing = [c for c in sf_pool if _passes_combo(c, combo)]
                top10_daily.append({
                    "combo_label": r["combo_label"],
                    "combo": r["combo"],
                    "daily": _daily_stability(passing),
                })

            # ── Per-(tier, sf) artifact paths ──────────────────────
            base = (f"gss_{sport}_{tier}_{sf}_"
                    f"{date_start}_{date_end}_{stamp}")
            json_path = artifact_dir / f"{base}.json"
            csv_path  = artifact_dir / f"{base}.csv"

            # CSV: leaderboard rows
            with csv_path.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "combo_label", "n_total", "n_graded", "n_ungraded",
                    "wins", "losses", "pushes",
                    "hit_rate_pct", "hit_rate_ci95_low",
                    "hit_rate_ci95_high",
                    "roi_pct", "roi_ci95_low", "roi_ci95_high",
                    "profit_units", "stake_units", "balanced_score",
                    "devig_grd", "devig_roi",
                    "onesided_std_grd", "onesided_std_roi",
                    "onesided_alt_grd", "onesided_alt_roi",
                ])
                for r in sweep["rows"]:
                    o = r["overall"]
                    bb = r["by_bucket"]
                    dv = bb.get("devig", {})
                    os1 = bb.get("one_sided_std", {})
                    os2 = bb.get("one_sided_alt", {})
                    w.writerow([
                        r["combo_label"], o["n_total"], o["n_graded"],
                        o["n_ungraded"], o["wins"], o["losses"],
                        o["pushes"], o["hit_rate_pct"],
                        o["hit_rate_ci95_low"], o["hit_rate_ci95_high"],
                        o["roi_pct"], o["roi_ci95_low"],
                        o["roi_ci95_high"], o["profit_units"],
                        o["stake_units"], r["balanced_score"],
                        dv.get("n_graded"), dv.get("roi_pct"),
                        os1.get("n_graded"), os1.get("roi_pct"),
                        os2.get("n_graded"), os2.get("roi_pct"),
                    ])

            payload = {
                "audit_kind": "stat_family_grid_search",
                "generated_at_iso":
                    datetime.now(timezone.utc).isoformat(),
                "sport": sport, "tier": tier, "stat_family": sf,
                "date_start": date_start, "date_end": date_end,
                "snapshot_time": snapshot_time,
                "min_graded_sample_size": min_graded_sample_size,
                "max_combinations": max_combinations,
                "pool_size": len(sf_pool),
                "pool_graded": graded_sf,
                "plan": sweep["plan"],
                "leaderboards": sweep["leaderboards"],
                "recommended": recommended,
                "recommended_analyses": {
                    "odds_band_breakdown": odds_bands,
                    "daily_stability": daily,
                    "outlier_dependency": outlier,
                    "sharpness_signal": sharpness,
                    "bucket_breakdown": buckets,
                    "side_split": side_split,
                    "gate_failure_waterfall": gate_waterfall,
                },
                "top10_roi_daily_stability": top10_daily,
                "all_rows": sweep["rows"],
            }
            json_path.write_text(
                json.dumps(payload, indent=2, default=str))
            per_sf_artifacts.append({
                "tier": tier, "stat_family": sf,
                "json": str(json_path), "csv": str(csv_path),
            })

            # ── Master-row entry ───────────────────────────────────
            if recommended is not None:
                o = recommended["overall"]
                master_rows.append({
                    "tier": tier, "stat_family": sf,
                    "status": "ok",
                    "recommended_combo": recommended["combo_label"],
                    "recommended_combo_dict": recommended["combo"],
                    "n_pool": len(sf_pool),
                    "graded_n": o["n_graded"],
                    "ungraded_n": o["n_ungraded"],
                    "wins": o["wins"], "losses": o["losses"],
                    "pushes": o["pushes"],
                    "HR": o["hit_rate_pct"],
                    "HR_ci95_low":  o["hit_rate_ci95_low"],
                    "HR_ci95_high": o["hit_rate_ci95_high"],
                    "ROI": o["roi_pct"],
                    "ROI_ci95_low":  o["roi_ci95_low"],
                    "ROI_ci95_high": o["roi_ci95_high"],
                    "P_and_L": o["profit_units"],
                    "balanced_score": recommended["balanced_score"],
                    "devig_grd":      buckets["devig"].get("n_graded"),
                    "devig_roi":      buckets["devig"].get("roi_pct"),
                    "onesided_std_grd": buckets["one_sided_std"].get("n_graded"),
                    "onesided_std_roi": buckets["one_sided_std"].get("roi_pct"),
                    "over_grd":   side_split["over_only"].get("n_graded"),
                    "over_roi":   side_split["over_only"].get("roi_pct"),
                    "under_grd":  side_split["under_only"].get("n_graded"),
                    "under_roi":  side_split["under_only"].get("roi_pct"),
                    "daily_consistency_score":
                        daily.get("consistency_score"),
                    "daily_graded_days": daily.get("graded_days"),
                    "outlier_top1_pct_of_net":
                        outlier.get("top1_pct_of_net"),
                    "outlier_top5_pct_of_net":
                        outlier.get("top5_pct_of_net"),
                    "is_outlier_dependent":
                        outlier.get("is_outlier_dependent"),
                    "corr_hr20_vs_profit":
                        sharpness.get("corr_hit_rate_l20_vs_profit"),
                    "corr_edge_vs_profit":
                        sharpness.get("corr_edge_pct_vs_profit"),
                    "corr_cv_vs_profit":
                        sharpness.get("corr_cv_vs_profit"),
                    "corr_tp_vs_profit":
                        sharpness.get("corr_tp_vs_profit"),
                    "artifact_json": str(json_path),
                })

            # Console-print key findings
            if recommended is not None:
                o = recommended["overall"]
                print(f"    → recommended={recommended['combo_label']}  "
                      f"grd={o['n_graded']}  HR={o['hit_rate_pct']}  "
                      f"ROI={o['roi_pct']}  [CI {o['roi_ci95_low']},"
                      f"{o['roi_ci95_high']}]  P&L={o['profit_units']}  "
                      f"daily_consistency={daily.get('consistency_score')}  "
                      f"outlier_top1%={outlier.get('top1_pct_of_net')}")

    # ── Master summary CSV ────────────────────────────────────────
    master_csv = artifact_dir / (
        f"gss_master_summary_{sport}_{date_start}_{date_end}_{stamp}.csv"
    )
    master_cols = [
        "tier", "stat_family", "status", "recommended_combo",
        "n_pool", "graded_n", "ungraded_n", "wins", "losses", "pushes",
        "HR", "HR_ci95_low", "HR_ci95_high",
        "ROI", "ROI_ci95_low", "ROI_ci95_high",
        "P_and_L", "balanced_score",
        "devig_grd", "devig_roi",
        "onesided_std_grd", "onesided_std_roi",
        "over_grd", "over_roi", "under_grd", "under_roi",
        "daily_consistency_score", "daily_graded_days",
        "outlier_top1_pct_of_net", "outlier_top5_pct_of_net",
        "is_outlier_dependent",
        "corr_hr20_vs_profit", "corr_edge_vs_profit",
        "corr_cv_vs_profit", "corr_tp_vs_profit",
        "artifact_json",
    ]
    with master_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(master_cols)
        # Sort: ok rows by descending balanced_score, then by ROI;
        #       skipped rows at the bottom.
        def _sort_key(r):
            if r.get("status") != "ok":
                return (1, 0, 0)
            return (
                0,
                -(r.get("balanced_score") or 0),
                -(r.get("ROI") or -999),
            )
        for r in sorted(master_rows, key=_sort_key):
            w.writerow([r.get(c) for c in master_cols])

    # Master JSON
    master_json = artifact_dir / (
        f"gss_master_summary_{sport}_{date_start}_{date_end}_{stamp}.json"
    )
    master_json.write_text(json.dumps({
        "audit_kind": "stat_family_grid_search_master",
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "sport": sport, "tiers": tiers,
        "stat_families_requested": stat_families,
        "date_start": date_start, "date_end": date_end,
        "snapshot_time": snapshot_time,
        "allow_one_sided": allow_one_sided,
        "min_graded_sample_size": min_graded_sample_size,
        "max_combinations": max_combinations,
        "pipeline_version": PIPELINE_VERSION,
        "pool_summaries": pool_summaries,
        "master_rows": master_rows,
        "per_sf_artifacts": per_sf_artifacts,
    }, indent=2, default=str))

    # ── Console master print ──────────────────────────────────────
    print(f"\n\n{'='*132}")
    print("  MASTER SUMMARY")
    print(f"{'='*132}")
    print(f"  {'tier':<11s} {'stat_family':<22s} {'status':<22s} "
          f"{'grd':>5s} {'HR':>5s} {'ROI':>6s} "
          f"{'ROIci95':>14s} {'P&L':>8s} {'BAL':>5s} "
          f"{'consist':>7s} {'outl%':>6s}")
    for r in sorted(master_rows, key=lambda r: (
            r.get("tier", ""),
            0 if r.get("status") == "ok" else 1,
            -(r.get("balanced_score") or 0))):
        if r.get("status") != "ok":
            print(f"  {r.get('tier','?'):<11s} "
                  f"{r.get('stat_family','?'):<22s} "
                  f"{r.get('status',''):<22s} "
                  f"{r.get('graded_n', 0):>5d}")
            continue
        roi_ci = f"[{r['ROI_ci95_low']},{r['ROI_ci95_high']}]"
        print(f"  {r['tier']:<11s} {r['stat_family']:<22s} "
              f"{r['recommended_combo'][:22]:<22s} "
              f"{r['graded_n']:>5d} {str(r['HR']):>5s} "
              f"{str(r['ROI']):>6s} {roi_ci:>14s} "
              f"{r['P_and_L']:>8.3f} "
              f"{str(r['balanced_score']):>5s} "
              f"{str(r['daily_consistency_score']):>7s} "
              f"{str(r['outlier_top1_pct_of_net']):>6s}")

    print(f"\n[master-csv]  {master_csv}")
    print(f"[master-json] {master_json}")
    print(f"[per-sf artifacts] {len(per_sf_artifacts)} files in "
          f"{artifact_dir}")

    client.close()
    return {
        "master_csv": str(master_csv),
        "master_json": str(master_json),
        "n_sf_artifacts": len(per_sf_artifacts),
        "master_rows": master_rows,
    }


# ──────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sport", default="mlb")
    p.add_argument("--tiers", default="safe_haven,front_lines,war_zone")
    p.add_argument("--stat-families",
                    default=",".join(REQUIRED_STAT_FAMILIES))
    p.add_argument("--date-start", required=True)
    p.add_argument("--date-end",   required=True)
    p.add_argument("--snapshot-time", default="11:00:00Z")
    p.add_argument("--allow-one-sided", action="store_true", default=True)
    p.add_argument("--min-graded-sample-size", type=int, default=50)
    p.add_argument("--max-combinations", type=int, default=250)
    p.add_argument("--disable-all-gates", action="store_true",
                    default=False,
                    help=("TEST ONLY: drop EVERY gate failure after the "
                          "engine eval and force gate_pass=True. The "
                          "tier_odds_bucket_fail routing short-circuit "
                          "still applies — pool stays scoped to the "
                          "target tier."))
    return p.parse_args()


async def _main():
    args = _parse_args()
    await run_sweep(
        sport=args.sport,
        tiers=args.tiers.split(","),
        stat_families=args.stat_families.split(","),
        date_start=args.date_start, date_end=args.date_end,
        snapshot_time=args.snapshot_time,
        allow_one_sided=args.allow_one_sided,
        min_graded_sample_size=args.min_graded_sample_size,
        max_combinations=args.max_combinations,
        disable_all_gates_for_accuracy_test=args.disable_all_gates,
    )


if __name__ == "__main__":
    asyncio.run(_main())
