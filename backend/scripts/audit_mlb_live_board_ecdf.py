"""MLB live-board audit: pre/post ECDF behaviour on the current scored slate.

Shadow-computes what `p_true_model` WOULD have been under the Gaussian
assumption for every live MLB scored doc and compares it to the
persisted `p_true_model` (which is already the ECDF-overridden value for
props whose stat has an ECDF artifact on disk). Requires no rescore.

Produces a markdown report: `reports/mlb_live_board_ecdf_audit.md`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter, defaultdict
from math import erf, sqrt
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient
from services.probability import get_universal_ecdf

_STAT_ALIASES = {
    'k': 'pitcher_strikeouts', 'ks': 'pitcher_strikeouts',
    'pitcher_k': 'pitcher_strikeouts', 'pitcher_strikeouts': 'pitcher_strikeouts',
    'tb': 'total_bases', 'rbi': 'rbis', 'sb': 'stolen_bases',
    'hr': 'home_runs', 'h': 'hits', 'r': 'runs',
    'hrr': 'hits+runs+rbis', 'hits+runs+rbi': 'hits+runs+rbis',
    'batter_strikeouts': 'strikeouts',
    'batter_walks': 'walks',
    'walks_allowed': 'pitcher_walks',
    'pitcher_outs': 'pitcher_strikeouts',
    'pitching_outs': 'pitcher_strikeouts',
}


def _normalize_stat(stat_type: str) -> str:
    s = (stat_type or "").lower().replace(' ', '_')
    return _STAT_ALIASES.get(s, s)

VERSION_TAG = "final-mlb-rt"
REPORT_PATH = "/app/backend/reports/mlb_live_board_ecdf_audit.md"

GATE_OVER = 0.55
GATE_UNDER = 0.45


def _gaussian_p_over(proj: float, sigma: float, line: float) -> float:
    if sigma is None or sigma <= 0:
        return 0.5
    z = (proj - line) / sigma
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    coll = db.mlb_prop_scores

    # 1. Current MLB tier counts
    tier_counts: Counter = Counter()
    async for d in coll.find(
        {"version_tag": VERSION_TAG, "active": True},
        {"tier": 1, "_id": 0},
    ):
        tier_counts[d.get("tier") or "unassigned"] += 1

    # 2+3+4+5+6+7 — walk the full slate once, collect everything.
    uni = get_universal_ecdf()

    rows: List[Dict[str, Any]] = []
    prob_method_counter: Counter = Counter()
    missing_ecdf_counter: Counter = Counter()
    false_over_candidates: List[Dict[str, Any]] = []
    half_line_props: List[Dict[str, Any]] = []
    zero_heavy_inflated: List[Dict[str, Any]] = []

    # Zero-heavy stats (from the MLB cutover report — zeros dominate the .5 bucket).
    ZERO_HEAVY = {
        "home_runs", "walks", "rbis", "runs", "stolen_bases",
        "singles", "doubles", "triples", "total_bases",
    }

    cursor = coll.find(
        {"version_tag": VERSION_TAG, "active": True},
        {
            "_id": 0, "canonical_key": 1, "player_name": 1, "stat_type": 1,
            "line": 1, "recommendation": 1, "p_true_model": 1,
            "p_true_method": 1, "model_projection": 1, "model_sigma": 1,
            "tier": 1, "edge_pct": 1, "tp": 1, "ranking_score_v2": 1,
            "book_count": 1, "coverage_class": 1,
        },
    )

    async for d in cursor:
        stat = (d.get("stat_type") or "").lower()
        # Canonicalise via hf normaliser (same mapping used at scoring time).
        canon = _normalize_stat(stat)
        proj = d.get("model_projection")
        sigma = d.get("model_sigma")
        line = d.get("line")
        if proj is None or sigma is None or line is None:
            continue

        side = (d.get("recommendation") or "OVER").upper()
        gauss_p_over = _gaussian_p_over(float(proj), float(sigma), float(line))
        gauss_p_true_model = gauss_p_over if "OVER" in side else 1.0 - gauss_p_over

        ecdf_pred = uni.predict_over_probability(
            sport="mlb", stat_family=canon,
            projection=float(proj), line=float(line),
        )
        if ecdf_pred is None:
            prob_method_counter["gaussian"] += 1
            missing_ecdf_counter[canon] += 1
            ecdf_p_over = None
            ecdf_p_true_model = None
        else:
            prob_method_counter["ecdf"] += 1
            ecdf_p_over = float(ecdf_pred.p_over)
            ecdf_p_true_model = ecdf_p_over if "OVER" in side else 1.0 - ecdf_p_over

        persisted_p = d.get("p_true_model")

        row = {
            "player": d.get("player_name"),
            "stat": stat,
            "canon": canon,
            "line": float(line),
            "side": side,
            "proj": float(proj),
            "sigma": float(sigma),
            "gauss_p_over": round(gauss_p_over, 4),
            "gauss_p_true_model": round(gauss_p_true_model, 4),
            "ecdf_p_over": round(ecdf_p_over, 4) if ecdf_p_over is not None else None,
            "ecdf_p_true_model": round(ecdf_p_true_model, 4) if ecdf_p_true_model is not None else None,
            "persisted_p_true_model": persisted_p,
            "tier": d.get("tier"),
            "edge_pct": d.get("edge_pct"),
            "rs2": d.get("ranking_score_v2"),
        }
        rows.append(row)

        # .5 lines (all)
        if abs(float(line) - round(float(line))) > 0.4:  # line ends in .5
            tier = d.get("tier")
            if tier in ("safe_haven", "front_lines", "war_zone"):
                half_line_props.append(row)

        # false OVER candidates: Gaussian triggered OVER-gate, ECDF did not.
        if ecdf_p_over is not None and "OVER" in side:
            if gauss_p_over >= GATE_OVER and ecdf_p_over < GATE_OVER:
                false_over_candidates.append(row)

        # zero-heavy inflated: stat in ZERO_HEAVY, OVER side, ecdf_p_over still >= 0.55
        if canon in ZERO_HEAVY and "OVER" in side:
            if ecdf_p_over is not None and ecdf_p_over >= 0.55:
                zero_heavy_inflated.append(row)

    # 2. Top-20 picks after ECDF — by ranking_score_v2 then edge_pct
    tiered = [r for r in rows if r["tier"] in ("safe_haven", "front_lines", "war_zone")]
    tiered_sorted = sorted(
        tiered,
        key=lambda r: ((r.get("rs2") or 0), (r.get("edge_pct") or 0)),
        reverse=True,
    )[:20]

    # 6. Edge distribution before/after ECDF — buckets
    def _bucket(p):
        if p is None:
            return "none"
        if p < 0.10: return "<10"
        if p < 0.30: return "10-30"
        if p < 0.45: return "30-45"
        if p < 0.55: return "45-55"
        if p < 0.70: return "55-70"
        if p < 0.90: return "70-90"
        return ">=90"

    dist_before: Counter = Counter()
    dist_after: Counter = Counter()
    for r in rows:
        dist_before[_bucket(r["gauss_p_true_model"])] += 1
        dist_after[_bucket(r["ecdf_p_true_model"])] += 1

    # ===== Render report =====
    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    md = [
        "# MLB Live-Board ECDF Audit",
        f"Generated: {now}  •  version_tag=`{VERSION_TAG}`  •  "
        f"scanned docs: {len(rows):,}",
        "",
        "Shadow simulation: for every active MLB scored doc, recomputes "
        "what `p_true_model` WOULD have been under the pre-ECDF Gaussian "
        "assumption (using persisted `model_projection` / `model_sigma`) "
        "and compares it to the ECDF output from the live artifact layer. "
        "Projections and gates are unchanged.",
        "",
        "## 1. Current MLB tier counts",
        "",
        "| tier | count |",
        "|------|------:|",
    ]
    for t in ("safe_haven", "front_lines", "war_zone", "unqualified", "unassigned"):
        md.append(f"| {t} | {tier_counts.get(t, 0):,} |")
    md.append("")

    md.extend([
        "## 2. Top-20 MLB picks after ECDF (ranked by `ranking_score_v2`)",
        "",
        "| # | player | stat | line | side | proj | sigma | gauss p_model | ecdf p_model | tier | edge_pct | rs2 |",
        "|---|--------|------|-----:|------|-----:|------:|-------------:|-------------:|------|-------:|----:|",
    ])
    for i, r in enumerate(tiered_sorted, 1):
        md.append(
            f"| {i} | {r['player']} | {r['canon']} | {r['line']} | "
            f"{r['side']} | {r['proj']:.2f} | {r['sigma']:.2f} | "
            f"{r['gauss_p_true_model']:.3f} | "
            f"{(r['ecdf_p_true_model'] if r['ecdf_p_true_model'] is not None else -1):.3f} | "
            f"{r['tier']} | "
            f"{(r['edge_pct'] if r['edge_pct'] is not None else 0):+.1f} | "
            f"{(r['rs2'] if r['rs2'] is not None else 0):+.3f} |"
        )
    md.append("")

    md.extend([
        f"## 3. Downgraded / removed false-OVER candidates ({len(false_over_candidates):,})",
        "",
        "Props where the pre-ECDF Gaussian `p_over` ≥ 0.55 (would have "
        "triggered the OVER gate) but the ECDF `p_over` fell below 0.55. "
        "These are exactly the false-OVER calls the cutover is designed "
        "to eliminate.",
        "",
        "| player | stat | line | proj | gauss p_over | ecdf p_over | Δ | tier |",
        "|--------|------|-----:|-----:|------------:|-----------:|-----:|------|",
    ])
    # Sort by biggest downgrade magnitude first.
    for r in sorted(false_over_candidates,
                    key=lambda r: (r["gauss_p_over"] - (r["ecdf_p_over"] or 0)),
                    reverse=True)[:40]:
        delta = (r["ecdf_p_over"] or 0) - r["gauss_p_over"]
        md.append(
            f"| {r['player']} | {r['canon']} | {r['line']} | {r['proj']:.2f} | "
            f"{r['gauss_p_over']:.3f} | "
            f"{(r['ecdf_p_over'] or 0):.3f} | {delta:+.3f} | {r['tier']} |"
        )
    if len(false_over_candidates) > 40:
        md.append(f"| … | … | … | … | … | … | … | … |")
        md.append(f"| _({len(false_over_candidates) - 40} more rows truncated)_ |")
    md.append("")

    md.extend([
        f"## 4. All .5-line props that still pass (tiered) ({len(half_line_props):,})",
        "",
        "| player | stat | line | side | proj | ecdf p_model | tier | edge |",
        "|--------|------|-----:|------|-----:|------------:|------|-----:|",
    ])
    for r in sorted(half_line_props,
                    key=lambda r: (r.get("ecdf_p_true_model") or 0),
                    reverse=True):
        md.append(
            f"| {r['player']} | {r['canon']} | {r['line']} | {r['side']} | "
            f"{r['proj']:.2f} | "
            f"{(r['ecdf_p_true_model'] if r['ecdf_p_true_model'] is not None else -1):.3f} | "
            f"{r['tier']} | "
            f"{(r['edge_pct'] if r['edge_pct'] is not None else 0):+.1f} |"
        )
    md.append("")

    md.extend([
        "## 5. probability_method counts (shadow — reflects what a rescore would write)",
        "",
        "| method | count | share |",
        "|--------|------:|------:|",
    ])
    total = sum(prob_method_counter.values()) or 1
    for m, c in prob_method_counter.most_common():
        md.append(f"| {m} | {c:,} | {100.0 * c / total:.1f}% |")
    md.append("")
    if missing_ecdf_counter:
        md.append("**stat_families falling back to Gaussian (no ECDF artifact):**")
        md.append("")
        for c, n in missing_ecdf_counter.most_common():
            md.append(f"- `{c}` — {n:,}")
        md.append("")

    md.extend([
        "## 6. Edge / probability distribution (before vs after ECDF)",
        "",
        "| bucket | before (gauss) | after (ecdf) | Δ |",
        "|--------|---------------:|-------------:|-----:|",
    ])
    all_buckets = ["<10", "10-30", "30-45", "45-55", "55-70", "70-90", ">=90", "none"]
    for b in all_buckets:
        a = dist_before.get(b, 0)
        c = dist_after.get(b, 0)
        md.append(f"| {b} | {a:,} | {c:,} | {c - a:+,} |")
    md.append("")

    md.extend([
        f"## 7. Zero-heavy props still showing inflated OVER probability",
        "",
        f"Zero-heavy stat families checked: {', '.join(sorted(ZERO_HEAVY))}.",
        "",
        f"**Count of OVER props with ECDF `p_over` ≥ 0.55: "
        f"{len(zero_heavy_inflated):,}**",
        "",
    ])
    if zero_heavy_inflated:
        md.append(
            "These are not necessarily bugs — a big slugger vs a weak pitcher "
            "CAN legitimately carry `p_over ≥ 0.55` on a 0.5 total_bases line. "
            "Listed for spot-check:")
        md.append("")
        md.extend([
            "| player | stat | line | proj | ecdf p_over | tier | edge |",
            "|--------|------|-----:|-----:|-----------:|------|-----:|",
        ])
        for r in sorted(zero_heavy_inflated,
                        key=lambda r: r["ecdf_p_over"] or 0, reverse=True)[:40]:
            md.append(
                f"| {r['player']} | {r['canon']} | {r['line']} | {r['proj']:.2f} | "
                f"{(r['ecdf_p_over'] or 0):.3f} | {r['tier']} | "
                f"{(r['edge_pct'] if r['edge_pct'] is not None else 0):+.1f} |"
            )
        if len(zero_heavy_inflated) > 40:
            md.append(f"| _({len(zero_heavy_inflated) - 40} more rows truncated)_ |")
    else:
        md.append(
            "_No OVER props on any zero-heavy stat pass the 0.55 threshold._")
    md.append("")

    # Stat-level summary of inflation risk
    md.append("### Zero-heavy OVER probability by stat family")
    md.append("")
    md.append("| stat | total OVERs | ecdf ≥ 0.55 | share | max ecdf p_over |")
    md.append("|------|-------------:|-------------:|-------:|----------------:|")
    zh_by_stat = defaultdict(lambda: {"total": 0, "hi": 0, "max": 0.0})
    for r in rows:
        if r["canon"] in ZERO_HEAVY and "OVER" in r["side"] and r["ecdf_p_over"] is not None:
            zh_by_stat[r["canon"]]["total"] += 1
            zh_by_stat[r["canon"]]["max"] = max(
                zh_by_stat[r["canon"]]["max"], r["ecdf_p_over"],
            )
            if r["ecdf_p_over"] >= 0.55:
                zh_by_stat[r["canon"]]["hi"] += 1
    for stat, s in sorted(zh_by_stat.items()):
        share = 100.0 * s["hi"] / s["total"] if s["total"] else 0
        md.append(
            f"| {stat} | {s['total']:,} | {s['hi']:,} | "
            f"{share:.1f}% | {s['max']:.3f} |"
        )
    md.append("")

    md.extend([
        "## Summary",
        "",
        f"- **Board playable: {'YES' if len(tiered) > 0 else 'NO — no tiered picks'}**. "
        f"{len(tiered):,} tiered MLB picks on the slate.",
        f"- **ECDF coverage: "
        f"{100.0 * prob_method_counter.get('ecdf', 0) / max(total, 1):.1f}%** "
        f"of live scored props run through ECDF. Remaining "
        f"{prob_method_counter.get('gaussian', 0):,} fall back to Gaussian "
        f"(stat_family with no artifact).",
        f"- **False-OVER corrections live on the board: "
        f"{len(false_over_candidates):,}**. Each is a prop whose Gaussian "
        f"p_over would have cleared the 0.55 OVER gate but ECDF pulled it "
        f"back below. These would have been bad bets on the pre-ECDF board.",
        f"- **Zero-heavy OVER inflation: "
        f"{'NONE' if not zero_heavy_inflated else str(len(zero_heavy_inflated)) + ' candidates (see §7)'}.** "
        "Listed rows are candidates for manual review, not automatic rejects.",
        "",
        "Projections, sigmas, tier gates, and the 0-book exclusion are "
        "all unchanged. Only `p_true_model` changes under ECDF.",
    ])

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(md))

    # Console TL;DR
    print(f"=== MLB LIVE-BOARD ECDF AUDIT ({now}) ===")
    print(f"rows scanned: {len(rows):,}   tiered: {len(tiered):,}")
    print(f"tier_counts:  {dict(tier_counts)}")
    print(f"probability_method (shadow): {dict(prob_method_counter)}")
    print(f"false_over_candidates: {len(false_over_candidates):,}")
    print(f"half_line_tiered:      {len(half_line_props):,}")
    print(f"zero_heavy_inflated:   {len(zero_heavy_inflated):,}")
    print(f"report written → {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
