"""Shadow evaluation of the MLB EB-shrinkage feature (read-only).

Replays the empirical-Bayes shrinkage formula over the current live
MLB board (`mlb_prop_scores@final-mlb-rt`) WITHOUT touching Mongo or
rescoring. Reports the 8 success criteria requested:

  1. projection mean vs actual mean (before vs after shrinkage)
  2. bias reduction by stat
  3. top-20 biggest projection reductions
  4. effect on ECDF p_over
  5. effect on edge_pct
  6. effect on gate pass/fail (0.55 OVER / 0.45 UNDER)
  7. no projection goes negative
  8. no stat family outside the whitelist changes

Writes `reports/mlb_eb_shrinkage_shadow_eval.md` and emits a
KEEP / REJECT recommendation + suggested alternate weights if the
initial ones overcorrect.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

from services.scoring import mlb_eb_shrinkage as ebs
from services.probability import get_universal_ecdf

VERSION_TAG = "final-mlb-rt"
REPORT_PATH = "/app/backend/reports/mlb_eb_shrinkage_shadow_eval.md"
WHITELIST = {"home_runs", "rbis", "total_bases", "hits+runs+rbis"}
GATE_OVER = 0.55
GATE_UNDER = 0.45


def _actual_from_log(log: Dict[str, Any], stat: str) -> Optional[float]:
    if stat == "hits+runs+rbis":
        vals = [log.get("hits"), log.get("runs"), log.get("rbis")]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))
    v = log.get(stat)
    return float(v) if v is not None else None


async def main(force_flag_on: bool = True):
    # For the shadow eval we need the shrinkage code path to run even
    # though the production flag stays off. Toggle in-process only.
    if force_flag_on:
        os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Pre-load all referenced player hub rows in ONE query for speed.
    # (The helper caches by player, but per-player find_one across
    # 2k+ props adds up.)
    pids = set()
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "bdl_player_id": {"$ne": None}},
        {"_id": 0, "bdl_player_id": 1},
    ):
        try:
            pids.add(int(d["bdl_player_id"]))
        except (TypeError, ValueError):
            pass

    hubs = {}
    async for d in db.mlb_master_hub_2026.find(
        {"$or": [
            {"bdl_player_id": {"$in": list(pids)}},
            {"bdl_id": {"$in": list(pids)}},
        ]},
        {"_id": 0, "bdl_player_id": 1, "bdl_id": 1, "bdl_game_logs": 1},
    ):
        pid = d.get("bdl_player_id") or d.get("bdl_id")
        if pid is not None:
            hubs[int(pid)] = d

    class _SyncHub:
        """Fake sync pymongo collection backed by the pre-fetched hub dict
        so we don't hit Mongo from the helper."""

        def find_one(self, q, proj=None):
            ors = q.get("$or") or [q]
            for clause in ors:
                for k, v in clause.items():
                    if isinstance(v, dict):  # $in / operators — skip
                        continue
                    if k in ("bdl_player_id", "bdl_id") and int(v) in hubs:
                        return hubs[int(v)]
            return None

    sync_hub = _SyncHub()

    uni = get_universal_ecdf()

    # Pull every currently-scored live MLB doc we need.
    rows: List[Dict[str, Any]] = []
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "model_projection": {"$ne": None}},
        {"_id": 0, "canonical_key": 1, "player_name": 1, "stat_type": 1,
         "bdl_player_id": 1, "line": 1, "recommendation": 1,
         "model_projection": 1, "model_sigma": 1,
         "p_true_model": 1, "tp": 1, "edge_pct": 1, "tier": 1},
    ):
        rows.append(d)

    # Actuals per stat (for criterion 1 + 2).
    actuals: Dict[str, List[float]] = {s: [] for s in WHITELIST}
    for pid, hub in hubs.items():
        for log in hub.get("bdl_game_logs") or []:
            abs_ = log.get("at_bats")
            pa_ = log.get("plate_appearances")
            if not ((abs_ and abs_ > 0) or (pa_ and pa_ > 0)):
                continue
            for stat in WHITELIST:
                v = _actual_from_log(log, stat)
                if v is not None:
                    actuals[stat].append(v)

    # ------------------------------------------------------------------
    # Replay shrinkage.
    # ------------------------------------------------------------------
    ebs.reset_cache()

    per_stat_proj_before: Dict[str, List[float]] = defaultdict(list)
    per_stat_proj_after: Dict[str, List[float]] = defaultdict(list)
    per_stat_skipped: Dict[str, Counter] = defaultdict(Counter)
    whitelisted_changes: List[Dict[str, Any]] = []
    non_whitelist_changes = 0  # should stay 0
    negative_projections = 0   # should stay 0

    prob_before_after = []
    edge_before_after = []
    gate_movement_over: Dict[str, int] = Counter()
    gate_movement_under: Dict[str, int] = Counter()

    for d in rows:
        raw_stat = (d.get("stat_type") or "").lower().replace(" ", "_")
        canon = ebs._normalize_stat(raw_stat)
        proj_before = float(d["model_projection"])
        pid = d.get("bdl_player_id")
        line = d.get("line")
        side = (d.get("recommendation") or "OVER").upper()
        tp = d.get("tp")
        p_true_before = d.get("p_true_model")

        shrunk, audit = ebs.apply_eb_shrinkage(
            sync_hub, int(pid) if pid is not None else None,
            raw_stat, proj_before,
        )
        proj_after = float(shrunk) if shrunk is not None else proj_before
        if shrunk is not None and proj_after < 0:
            negative_projections += 1

        # No stat outside WHITELIST should change.
        if canon not in WHITELIST and shrunk is not None:
            non_whitelist_changes += 1

        per_stat_proj_before[canon].append(proj_before)
        per_stat_proj_after[canon].append(proj_after)
        if not audit["eb_shrinkage_applied"]:
            per_stat_skipped[canon][audit["eb_skip_reason"] or "unknown"] += 1

        # ECDF p_over before vs after.
        if canon in WHITELIST and line is not None:
            pred_before = uni.predict_over_probability(
                sport="mlb", stat_family=canon,
                projection=float(proj_before), line=float(line),
            )
            pred_after = uni.predict_over_probability(
                sport="mlb", stat_family=canon,
                projection=float(proj_after), line=float(line),
            )
            p_over_before = pred_before.p_over if pred_before is not None else None
            p_over_after = pred_after.p_over if pred_after is not None else None

            # Side-flip to p_true.
            if p_over_before is not None:
                p_true_shadow_before = (
                    p_over_before if "OVER" in side else 1.0 - p_over_before
                )
            else:
                p_true_shadow_before = None
            if p_over_after is not None:
                p_true_shadow_after = (
                    p_over_after if "OVER" in side else 1.0 - p_over_after
                )
            else:
                p_true_shadow_after = None

            if p_over_before is not None and p_over_after is not None:
                prob_before_after.append({
                    "stat": canon, "line": line, "player": d.get("player_name"),
                    "side": side,
                    "proj_before": proj_before, "proj_after": proj_after,
                    "p_over_before": p_over_before, "p_over_after": p_over_after,
                    "p_true_before": p_true_shadow_before,
                    "p_true_after": p_true_shadow_after,
                })

                # Edge movement.
                if tp is not None and p_true_shadow_before is not None \
                        and p_true_shadow_after is not None:
                    edge_before = p_true_shadow_before * 100.0 - float(tp)
                    edge_after = p_true_shadow_after * 100.0 - float(tp)
                    edge_before_after.append({
                        "stat": canon, "side": side,
                        "edge_before": edge_before, "edge_after": edge_after,
                    })

                # Gate movement (criterion 6) — OVER side.
                if "OVER" in side:
                    if p_over_before >= GATE_OVER and p_over_after < GATE_OVER:
                        gate_movement_over["lost_over"] += 1
                    elif p_over_before < GATE_OVER and p_over_after >= GATE_OVER:
                        gate_movement_over["gained_over"] += 1
                    else:
                        gate_movement_over["unchanged"] += 1
                else:
                    p_under_before = 1.0 - p_over_before
                    p_under_after = 1.0 - p_over_after
                    if p_under_before > (1.0 - GATE_UNDER) and p_under_after <= (1.0 - GATE_UNDER):
                        gate_movement_under["lost_under"] += 1
                    elif p_under_before <= (1.0 - GATE_UNDER) and p_under_after > (1.0 - GATE_UNDER):
                        gate_movement_under["gained_under"] += 1
                    else:
                        gate_movement_under["unchanged"] += 1

        if canon in WHITELIST and audit["eb_shrinkage_applied"]:
            whitelisted_changes.append({
                "player": d.get("player_name"),
                "stat": canon, "line": line, "side": side,
                "proj_before": proj_before, "proj_after": proj_after,
                "delta": proj_after - proj_before,
                "career_mean": audit["eb_player_career_mean"],
                "tier": d.get("tier"),
            })

    # ------------------------------------------------------------------
    # Render report.
    # ------------------------------------------------------------------
    def _fmt(x, nd=3):
        if x is None: return "-"
        return f"{x:.{nd}f}"

    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    md = [
        "# MLB Empirical-Bayes Shrinkage — Shadow Evaluation",
        f"Generated: {now}  •  source: `mlb_prop_scores@{VERSION_TAG}` "
        f"({len(rows):,} active docs with `model_projection`)",
        "",
        "Read-only simulation. Production flag `MLB_HF_EB_SHRINKAGE_ENABLED` "
        "is unchanged. ECDF, projections, sigmas, gates are unmodified.",
        "",
        "## 1 + 2. Bias reduction per stat (proj mean vs actual mean)",
        "",
        "| stat | n projs | actual mean | proj mean (before) | proj mean (after) "
        "| bias before | bias after | bias reduction |",
        "|------|--------:|------------:|-------------------:|-------------------:"
        "|------------:|-----------:|---------------:|",
    ]
    for stat in ("home_runs", "rbis", "total_bases", "hits+runs+rbis"):
        pb = np.array(per_stat_proj_before.get(stat) or [])
        pa = np.array(per_stat_proj_after.get(stat) or [])
        acts = np.array(actuals.get(stat) or [])
        if not len(pb) or not len(acts):
            md.append(f"| {stat} | 0 | - | - | - | - | - | - |")
            continue
        bias_before = float(pb.mean() - acts.mean())
        bias_after = float(pa.mean() - acts.mean())
        reduction = bias_before - bias_after
        md.append(
            f"| `{stat}` | {len(pb)} | {_fmt(acts.mean())} | "
            f"**{_fmt(pb.mean())}** | **{_fmt(pa.mean())}** | "
            f"{bias_before:+.3f} | {bias_after:+.3f} | "
            f"**{reduction:+.3f}** ({100.0 * reduction / max(abs(bias_before), 1e-9):+.1f}%) |"
        )
    md.append("")

    md.append("## 3. Top-20 biggest projection reductions (whitelisted stats only)")
    md.append("")
    md.append("| # | player | stat | line | side | proj before | proj after | Δ | career mean | tier |")
    md.append("|---|--------|------|-----:|------|------------:|-----------:|-----:|------------:|------|")
    top20 = sorted(whitelisted_changes, key=lambda r: r["delta"])[:20]
    for i, r in enumerate(top20, 1):
        md.append(
            f"| {i} | {r['player']} | {r['stat']} | {r['line']} | {r['side']} | "
            f"{r['proj_before']:.2f} | {r['proj_after']:.2f} | "
            f"{r['delta']:+.2f} | {r['career_mean']:.2f} | {r['tier']} |"
        )
    md.append("")

    # (4) ECDF p_over effect.
    md.append("## 4. Effect on ECDF p_over (whitelisted props only)")
    md.append("")
    if prob_before_after:
        deltas = np.array([r["p_over_after"] - r["p_over_before"]
                           for r in prob_before_after])
        md.append(f"- props considered: {len(prob_before_after):,}")
        md.append(f"- Δp_over (ecdf): mean={deltas.mean():+.4f}  "
                  f"median={np.median(deltas):+.4f}  "
                  f"p5/p95={np.percentile(deltas, 5):+.4f} / "
                  f"{np.percentile(deltas, 95):+.4f}  "
                  f"max|Δ|={np.max(np.abs(deltas)):.4f}")
        # per-stat mean.
        per = defaultdict(list)
        for r in prob_before_after:
            per[r["stat"]].append(r["p_over_after"] - r["p_over_before"])
        md.append("")
        md.append("| stat | n | mean Δp_over | median Δp_over |")
        md.append("|------|--:|-------------:|---------------:|")
        for s, arr in per.items():
            a = np.array(arr)
            md.append(f"| `{s}` | {len(a)} | {a.mean():+.4f} | {np.median(a):+.4f} |")
    md.append("")

    # (5) Edge effect.
    md.append("## 5. Effect on edge_pct")
    md.append("")
    if edge_before_after:
        eb = np.array([r["edge_before"] for r in edge_before_after])
        ea = np.array([r["edge_after"] for r in edge_before_after])
        delta = ea - eb
        md.append(f"- props with a tp anchor: {len(edge_before_after):,}")
        md.append(f"- edge_pct: mean before = {eb.mean():+.2f}pp  "
                  f"mean after = {ea.mean():+.2f}pp  "
                  f"mean Δ = {delta.mean():+.2f}pp")
        per = defaultdict(list)
        for r in edge_before_after:
            per[r["stat"]].append(r["edge_after"] - r["edge_before"])
        md.append("")
        md.append("| stat | n | mean Δedge |")
        md.append("|------|--:|-----------:|")
        for s, arr in per.items():
            a = np.array(arr)
            md.append(f"| `{s}` | {len(a)} | {a.mean():+.2f}pp |")
    else:
        md.append("- _(no priced props with tp — no edge to evaluate)_")
    md.append("")

    # (6) Gate movement.
    md.append("## 6. Gate pass/fail movement")
    md.append("")
    md.append("OVER side (0.55 threshold):")
    md.append("")
    md.append(
        f"- **Lost OVER gates** (was ≥ 0.55, now < 0.55): "
        f"{gate_movement_over.get('lost_over', 0):,}")
    md.append(
        f"- **Gained OVER gates** (was < 0.55, now ≥ 0.55): "
        f"{gate_movement_over.get('gained_over', 0):,}")
    md.append(
        f"- Unchanged: {gate_movement_over.get('unchanged', 0):,}")
    md.append("")
    md.append("UNDER side (0.45 threshold):")
    md.append("")
    md.append(
        f"- **Lost UNDER gates** (was p_over ≤ 0.45, now > 0.45): "
        f"{gate_movement_under.get('lost_under', 0):,}")
    md.append(
        f"- **Gained UNDER gates** (was p_over > 0.45, now ≤ 0.45): "
        f"{gate_movement_under.get('gained_under', 0):,}")
    md.append(
        f"- Unchanged: {gate_movement_under.get('unchanged', 0):,}")
    md.append("")

    # (7+8) Invariants.
    md.append("## 7 + 8. Invariant checks")
    md.append("")
    md.append(f"- Negative projections produced: **{negative_projections}** (must be 0)")
    md.append(f"- Non-whitelisted stats whose projection changed: "
              f"**{non_whitelist_changes}** (must be 0)")
    md.append("")

    # Skip-reason breakdown for whitelist.
    md.append("### Skip reasons by whitelisted stat")
    md.append("")
    md.append("| stat | applied | skipped (and why) |")
    md.append("|------|--------:|-------------------|")
    for stat in sorted(WHITELIST):
        pb = per_stat_proj_before.get(stat) or []
        pa = per_stat_proj_after.get(stat) or []
        changed = sum(1 for a, b in zip(pb, pa) if a != b)
        skips = per_stat_skipped.get(stat) or Counter()
        md.append(
            f"| `{stat}` | {changed} | "
            + " / ".join(f"{k}={v}" for k, v in skips.most_common())
            + " |"
        )
    md.append("")

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------
    md.append("## Recommendation")
    md.append("")
    recs = []
    best_weights = {}
    for stat in ("home_runs", "rbis", "total_bases", "hits+runs+rbis"):
        pb = np.array(per_stat_proj_before.get(stat) or [])
        pa = np.array(per_stat_proj_after.get(stat) or [])
        acts = np.array(actuals.get(stat) or [])
        if not len(pb) or not len(acts):
            continue
        bias_before = float(pb.mean() - acts.mean())
        bias_after = float(pa.mean() - acts.mean())
        # Grid-search the optimal w_model in 0.1 increments.
        best = None
        for w in np.arange(0.0, 1.01, 0.1):
            # Blend is model*w + player*(1-w). We don't have per-row
            # career_mean here, so approximate by solving at the mean:
            # new_mean = w * pb.mean() + (1 - w) * actuals_mean (proxy
            # for "player career"). This gives an upper-bound estimate
            # because in reality player career means are heterogeneous.
            # Refine by replaying against actual per-row career_means
            # if available in whitelisted_changes.
            rel_rows = [c for c in whitelisted_changes if c["stat"] == stat]
            if not rel_rows:
                continue
            projs = np.array([c["proj_before"] for c in rel_rows])
            cms = np.array([c["career_mean"] for c in rel_rows])
            mix = w * projs + (1.0 - w) * cms
            bias = float(mix.mean() - acts.mean())
            score = abs(bias)
            if best is None or score < best[0]:
                best = (score, w, bias)
        if best is not None:
            best_weights[stat] = best[1]
            recs.append(
                f"- `{stat}`: best w_model ≈ **{best[1]:.2f}** "
                f"(residual bias {best[2]:+.3f}); initial was "
                f"{ebs.weights_for(stat)[0]}.")
    md.extend(recs)
    md.append("")

    # Keep/reject heuristic.
    keeps = 0
    rejects = 0
    reasons = []
    for stat in ("home_runs", "rbis", "total_bases", "hits+runs+rbis"):
        pb = np.array(per_stat_proj_before.get(stat) or [])
        pa = np.array(per_stat_proj_after.get(stat) or [])
        acts = np.array(actuals.get(stat) or [])
        if not len(pb) or not len(acts):
            continue
        bias_before = float(pb.mean() - acts.mean())
        bias_after = float(pa.mean() - acts.mean())
        if abs(bias_after) < abs(bias_before) and abs(bias_after) < 0.25 * max(abs(bias_before), 1e-9):
            keeps += 1
            reasons.append(f"`{stat}`: bias {bias_before:+.3f} → {bias_after:+.3f} KEEP")
        elif abs(bias_after) >= abs(bias_before):
            rejects += 1
            reasons.append(f"`{stat}`: bias {bias_before:+.3f} → {bias_after:+.3f} REJECT (no improvement)")
        else:
            keeps += 1
            reasons.append(f"`{stat}`: bias {bias_before:+.3f} → {bias_after:+.3f} PARTIAL KEEP")
    verdict = "KEEP" if keeps > rejects and negative_projections == 0 and non_whitelist_changes == 0 else "REJECT"
    md.append(f"### Verdict: **{verdict}**")
    md.append("")
    for r in reasons:
        md.append(f"- {r}")
    md.append("")

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(md))

    print(f"=== MLB EB SHRINKAGE SHADOW EVAL ({now}) ===")
    print(f"rows: {len(rows):,}  whitelisted changes: {len(whitelisted_changes):,}")
    print(f"negative projs: {negative_projections}   non-whitelist changes: {non_whitelist_changes}")
    print(f"OVER gate: lost={gate_movement_over.get('lost_over', 0)} "
          f"gained={gate_movement_over.get('gained_over', 0)}")
    print(f"UNDER gate: lost={gate_movement_under.get('lost_under', 0)} "
          f"gained={gate_movement_under.get('gained_under', 0)}")
    print(f"verdict: {verdict}")
    print(f"best weights: {best_weights}")
    print(f"report → {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
