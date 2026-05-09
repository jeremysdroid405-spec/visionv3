#!/usr/bin/env python3
"""
PropVision Replay — Partial-Parity Test Report.

Reads `replay_evaluations` × `replay_outcomes` for one run_id and
produces a structured Markdown + JSON report. READ-ONLY against
replay collections; no live mutation.

This report is explicitly labeled PARTIAL-PARITY because production
VK2 / injury / matchup features are not yet wired into replay
(see `audit_reports/replay_phase25_30day_FINAL.md`). Confidence
labels are surfaced per section.

USAGE
-----
    python /app/backend/scripts/run_replay_report.py \\
        --run-id a1aeb71a6ef046baae4fb56deef06667 \\
        --snapshot-window t-30m \\
        --sport nba \\
        --feature-completeness partial \\
        --include-unqualified true \\
        --output /app/audit_reports/replay_partial_report.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

EVALS = "replay_evaluations"
OUTS  = "replay_outcomes"

# ---------------- helpers ----------------
def _amer_to_implied(o: Optional[int]) -> Optional[float]:
    if o is None:
        return None
    if o > 0:
        return 100.0 / (o + 100.0)
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return None


def _pct(n, d):
    return (round(100.0 * n / d, 2) if d else 0.0)


def _odds_bucket(o: Optional[int]) -> str:
    if o is None:           return "<missing>"
    if o < 0:               return "neg"
    if o < 150:             return "+100..+149"
    if o < 200:             return "+150..+199"
    if o < 300:             return "+200..+299"
    if o < 500:             return "+300..+499"
    return "+500+"


# ---------------- aggregations ----------------
async def section_dataset(db, *, run_id: str) -> Dict[str, Any]:
    n_eval = await db[EVALS].count_documents({"replay_run_id": run_id})
    n_out  = await db[OUTS].count_documents({"replay_run_id": run_id})

    sample = await db[EVALS].find_one({"replay_run_id": run_id}, {
        "_id": 0, "snapshot_label": 1, "commence_time": 1,
    })
    snap = sample.get("snapshot_label") if sample else None

    rng_pipe = [
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": None,
                     "min_ct": {"$min": "$commence_time"},
                     "max_ct": {"$max": "$commence_time"}}},
    ]
    rng = [d async for d in db[EVALS].aggregate(rng_pipe)]
    min_ct = rng[0]["min_ct"] if rng else None
    max_ct = rng[0]["max_ct"] if rng else None

    out_dist: Dict[str, int] = {"hit": 0, "miss": 0, "push": 0,
                                  "void_dnp": 0}
    async for d in db[OUTS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}}},
    ]):
        out_dist[d["_id"]] = d["n"]

    fc_dist: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$feature_completeness", "n": {"$sum": 1}}},
    ]):
        fc_dist[d["_id"] or "<none>"] = d["n"]

    return {
        "replay_run_id":     run_id,
        "snapshot_window":   snap,
        "date_range":        {"min": min_ct, "max": max_ct},
        "evaluations_total": n_eval,
        "outcomes_total":    n_out,
        "outcome_breakdown": out_dist,
        "feature_completeness_distribution": fc_dist,
        "known_parity_gaps": [
            "VK2 historical projection not wired (PARITY-TODO P5)",
            "Injury timeline not ingested (PARITY-TODO P4)",
            "Matchup/pace as-of-time not wired (PARITY-TODO P3)",
        ],
        "confidence": {
            "level":  "high",
            "reason": "Counts are direct DB reads; no inference involved.",
        },
    }


async def section_gate_dist(db, *, run_id: str) -> Dict[str, Any]:
    by_reason: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 25},
    ]):
        by_reason[d["_id"] or "<none>"] = d["n"]

    by_family: Dict[str, Dict[str, int]] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": {"f": "$stat_family", "r": "$tier_reason"},
                     "n": {"$sum": 1}}},
    ]):
        f = d["_id"]["f"] or "<none>"
        r = d["_id"]["r"] or "<none>"
        by_family.setdefault(f, {})[r] = d["n"]

    by_book: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$bookmaker", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        by_book[d["_id"] or "<none>"] = d["n"]

    return {
        "by_reason_top25":  by_reason,
        "by_stat_family":   by_family,
        "by_bookmaker":     by_book,
        "confidence": {
            "level":  "high",
            "reason": "Direct count of production gate-engine outputs.",
        },
    }


# ---------------- counterfactual rule sets ----------------
def _rule_predicate(rule: str):
    """Return a Mongo $match predicate for a counterfactual rule.

    All rules operate on `replay_outcomes` rows so we get hit/miss/PnL
    directly. Each rule is INTENTIONALLY simple — the goal is fast,
    interpretable signal.
    """
    if rule == "production_gates":
        # Production gates currently classify all 503k as unqualified
        # — we still report this for completeness.
        return {}
    if rule == "relaxed_direction":
        # Same as production but ignoring direction match — pick OVER
        # whenever rolling μ ≥ line × 0.95, or UNDER whenever μ ≤ line × 1.05
        # Implemented as: tp >= 50 (TP-led).
        return {"p_true_active": {"$gte": 50}}
    if rule == "ev_only_longshot":
        return {"odds_american": {"$gte": 200},
                 "edge_vs_fair":  {"$gte": 5}}
    if rule == "hr_cv_gate":
        return {"feature_set.hit_rate_l20": {"$gte": 0.65},
                 "feature_set.cv":           {"$lte": 0.35}}
    if rule == "tp_edge_gate":
        return {"p_true_active": {"$gte": 55},
                 "edge_vs_fair":  {"$gte": 3}}
    if rule == "war_zone_longshot_proposal":
        # Lifted CV ladder + edge floor + odds band
        return {"odds_american": {"$gte": 150, "$lte": 500},
                 "feature_set.cv": {"$gte": 0.30, "$lte": 0.55},
                 "edge_vs_fair":   {"$gte": 4}}
    raise ValueError(f"unknown rule: {rule}")


_RULES = [
    "production_gates",
    "relaxed_direction",
    "ev_only_longshot",
    "hr_cv_gate",
    "tp_edge_gate",
    "war_zone_longshot_proposal",
]


async def _ruleset_summary(db, *, run_id: str, rule: str) -> Dict[str, Any]:
    pred = _rule_predicate(rule)
    match = {"replay_run_id": run_id, **pred}

    # Roll up
    pipe = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "n":   {"$sum": 1},
            "hits":{"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":{"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "void":{"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
            "push":{"$sum": {"$cond": [{"$eq": ["$outcome", "push"]}, 1, 0]}},
            "pnl": {"$sum": "$pnl_units"},
            "avg_odds": {"$avg": "$odds_american"},
            "avg_tp":   {"$avg": "$p_true_active"},
            "avg_edge": {"$avg": "$edge_vs_fair"},
        }},
    ]
    agg = [d async for d in db[OUTS].aggregate(pipe)]
    if not agg:
        return {"rule": rule, "n": 0, "hits": 0, "miss": 0, "void": 0,
                 "push": 0, "hit_rate": None, "roi_per_unit": None,
                 "pnl_units": 0.0, "avg_odds": None, "avg_tp": None,
                 "avg_edge": None,
                 "top_25": [], "worst_25": []}
    a = agg[0]
    decided = a["hits"] + a["miss"]
    hr = (a["hits"] / decided) if decided else None
    roi = (a["pnl"] / a["n"]) if a["n"] else None

    top = await db[OUTS].find(
        match, projection={"_id": 0, "scoring_payload": 0},
    ).sort([("pnl_units", -1)]).limit(25).to_list(length=25)
    worst = await db[OUTS].find(
        match, projection={"_id": 0, "scoring_payload": 0},
    ).sort([("pnl_units", 1)]).limit(25).to_list(length=25)

    return {
        "rule":          rule,
        "n":             a["n"],
        "hits":          a["hits"], "miss": a["miss"],
        "void":          a["void"], "push": a["push"],
        "hit_rate":      round(hr, 4) if hr is not None else None,
        "roi_per_unit":  round(roi, 4) if roi is not None else None,
        "pnl_units":     round(a["pnl"], 2),
        "avg_odds":      round(a["avg_odds"], 2) if a["avg_odds"] else None,
        "avg_tp":        round(a["avg_tp"], 2)   if a["avg_tp"]   else None,
        "avg_edge":      round(a["avg_edge"], 2) if a["avg_edge"] else None,
        "top_25":        top,
        "worst_25":      worst,
    }


async def section_counterfactuals(db, *, run_id: str) -> Dict[str, Any]:
    rules = {}
    for r in _RULES:
        rules[r] = await _ruleset_summary(db, run_id=run_id, rule=r)
    return {
        "rules": rules,
        "confidence": {
            "level":  "medium",
            "reason": "Hit/PnL math is exact; the rule predicates are "
                      "heuristics (not VK2-aware), so 'profitable' here "
                      "means historically profitable for THIS rule, NOT "
                      "for current production tiers.",
        },
    }


# ---------------- splits ----------------
async def _split_summary(db, *, run_id: str,
                          group_expr: Any, label: str) -> Dict[str, Any]:
    pipe = [
        {"$match": {"replay_run_id": run_id}},
        {"$group": {
            "_id": group_expr,
            "n":   {"$sum": 1},
            "hits":{"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":{"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "void":{"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
            "pnl": {"$sum": "$pnl_units"},
            "avg_odds": {"$avg": "$odds_american"},
        }},
        {"$sort": {"n": -1}},
    ]
    rows: List[Dict[str, Any]] = []
    async for d in db[OUTS].aggregate(pipe):
        decided = d["hits"] + d["miss"]
        rows.append({
            label:        d["_id"],
            "n":          d["n"],
            "hits":       d["hits"],
            "miss":       d["miss"],
            "hit_rate":   (round(d["hits"] / decided, 4)
                           if decided else None),
            "roi_per_unit": (round(d["pnl"] / d["n"], 4)
                              if d["n"] else None),
            "pnl_units":  round(d["pnl"], 2),
            "avg_odds":   (round(d["avg_odds"], 1)
                            if d["avg_odds"] else None),
        })
    return rows


async def section_alt_vs_standard(db, *, run_id: str) -> Dict[str, Any]:
    return {
        "by_alternate_flag": await _split_summary(
            db, run_id=run_id, group_expr="$is_alternate",
            label="is_alternate"),
        "by_combo_flag": await _split_summary(
            db, run_id=run_id, group_expr="$is_combo",
            label="is_combo"),
        "by_stat_family": await _split_summary(
            db, run_id=run_id, group_expr="$stat_family",
            label="stat_family"),
        "confidence": {"level": "high", "reason":
            "Direct hit/PnL by group; no inference."},
    }


async def section_odds_buckets(db, *, run_id: str) -> Dict[str, Any]:
    # Manual bucket assignment in-pipeline.
    pipe = [
        {"$match": {"replay_run_id": run_id}},
        {"$addFields": {
            "bucket": {"$switch": {
                "branches": [
                    {"case": {"$lt":  ["$odds_american", 0]},     "then": "neg"},
                    {"case": {"$lt":  ["$odds_american", 150]},   "then": "+100..+149"},
                    {"case": {"$lt":  ["$odds_american", 200]},   "then": "+150..+199"},
                    {"case": {"$lt":  ["$odds_american", 300]},   "then": "+200..+299"},
                    {"case": {"$lt":  ["$odds_american", 500]},   "then": "+300..+499"},
                ],
                "default": "+500+",
            }},
        }},
        {"$group": {
            "_id": "$bucket",
            "n":   {"$sum": 1},
            "hits":{"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":{"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "pnl": {"$sum": "$pnl_units"},
            "avg_tp":   {"$avg": "$p_true_active"},
            "avg_edge": {"$avg": "$edge_vs_fair"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = []
    async for d in db[OUTS].aggregate(pipe):
        decided = d["hits"] + d["miss"]
        rows.append({
            "odds_bucket": d["_id"],
            "n":           d["n"],
            "hit_rate":    (round(d["hits"] / decided, 4)
                            if decided else None),
            "roi_per_unit":(round(d["pnl"] / d["n"], 4)
                            if d["n"] else None),
            "pnl_units":   round(d["pnl"], 2),
            "avg_tp":      (round(d["avg_tp"], 2) if d["avg_tp"] else None),
            "avg_edge":    (round(d["avg_edge"], 2) if d["avg_edge"] else None),
        })
    return {
        "rows": rows,
        "confidence": {"level": "medium", "reason":
            "ROI by odds bucket is mathematically clean, BUT relies on "
            "the partial-parity feature set; a fully-featured run could "
            "shift bucket compositions."},
    }


async def section_timing(db, *, run_id: str) -> Dict[str, Any]:
    rows = await _split_summary(
        db, run_id=run_id, group_expr="$snapshot_label",
        label="snapshot_label")
    return {
        "rows": rows,
        "confidence": {"level": "medium", "reason":
            "If the run was filtered to a single snapshot (e.g. t-30m), "
            "this section will report 1 row — re-run engine across more "
            "windows to populate."},
    }


async def section_direction_fail(db, *, run_id: str) -> Dict[str, Any]:
    """Props where production gate said 'direction fail' but TP/edge says
    profitable. Useful diagnostic of where μ-only-rolling fails."""
    pipe = [
        {"$match": {
            "replay_run_id":     run_id,
            "tier_reason":       {"$regex": "gate_direction_fail$"},
            "edge_vs_fair":      {"$gte": 3},
            "p_true_active":     {"$gte": 53},
        }},
        {"$lookup": {
            "from": OUTS,
            "let":  {"ck": "$canonical_key", "sl": "$snapshot_label",
                     "bk": "$bookmaker", "sd": "$side", "rid": "$replay_run_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$replay_run_id", "$$rid"]},
                    {"$eq": ["$canonical_key", "$$ck"]},
                    {"$eq": ["$snapshot_label", "$$sl"]},
                    {"$eq": ["$bookmaker", "$$bk"]},
                    {"$eq": ["$side", "$$sd"]},
                ]}}},
                {"$limit": 1},
            ],
            "as": "out",
        }},
        {"$unwind": {"path": "$out", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": None,
            "n":   {"$sum": 1},
            "hits":{"$sum": {"$cond": [{"$eq": ["$out.outcome", "hit"]}, 1, 0]}},
            "miss":{"$sum": {"$cond": [{"$eq": ["$out.outcome", "miss"]}, 1, 0]}},
            "pnl": {"$sum": {"$ifNull": ["$out.pnl_units", 0]}},
            "avg_odds": {"$avg": "$odds_american"},
            "avg_tp":   {"$avg": "$p_true_active"},
            "avg_edge": {"$avg": "$edge_vs_fair"},
        }},
    ]
    res = [d async for d in db[EVALS].aggregate(pipe, allowDiskUse=True)]
    if not res:
        return {"n": 0, "confidence": {"level": "low",
            "reason": "No props matched the direction-fail-but-profitable predicate."}}
    a = res[0]
    decided = a["hits"] + a["miss"]
    return {
        "n":            a["n"],
        "hits":         a["hits"], "miss": a["miss"],
        "hit_rate":     (round(a["hits"] / decided, 4) if decided else None),
        "roi_per_unit": (round(a["pnl"] / a["n"], 4) if a["n"] else None),
        "pnl_units":    round(a["pnl"], 2),
        "avg_odds":     (round(a["avg_odds"], 2) if a["avg_odds"] else None),
        "avg_tp":       (round(a["avg_tp"], 2)   if a["avg_tp"]   else None),
        "avg_edge":     (round(a["avg_edge"], 2) if a["avg_edge"] else None),
        "confidence": {"level": "medium",
            "reason": "Probes whether production direction gate is too "
                      "strict for partial-feature replay; not a "
                      "production-tier verdict."},
    }


async def section_proxy_tiers(db, *, run_id: str) -> Dict[str, Any]:
    """proxy_* rules — clearly labeled, NOT official tiers."""
    proxies = {
        "proxy_safe_haven":  {"odds_american": {"$lte": -180},
                                "p_true_active": {"$gte": 65},
                                "edge_vs_fair": {"$gte": 2}},
        "proxy_front_lines": {"odds_american": {"$gte": -149,
                                                  "$lte": +120},
                                "p_true_active": {"$gte": 53},
                                "edge_vs_fair": {"$gte": 3}},
        "proxy_war_zone":    {"odds_american": {"$gte": 121, "$lte": 400},
                                "edge_vs_fair": {"$gte": 5},
                                "feature_set.cv": {"$lte": 0.55}},
        "proxy_war_zone_longshot": {"odds_american": {"$gte": 200, "$lte": 800},
                                "edge_vs_fair": {"$gte": 8},
                                "p_true_active": {"$gte": 25}},
    }
    out = {}
    for name, pred in proxies.items():
        match = {"replay_run_id": run_id, **pred}
        pipe = [
            {"$match": match},
            {"$group": {
                "_id": None,
                "n":   {"$sum": 1},
                "hits":{"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
                "miss":{"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
                "void":{"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
                "pnl": {"$sum": "$pnl_units"},
                "avg_odds": {"$avg": "$odds_american"},
            }},
        ]
        a = [d async for d in db[OUTS].aggregate(pipe)]
        if not a:
            out[name] = {"n": 0, "hits": 0, "miss": 0, "void": 0,
                          "hit_rate": None, "roi_per_unit": None,
                          "pnl_units": 0.0, "avg_odds": None}
            continue
        ag = a[0]
        decided = ag["hits"] + ag["miss"]
        out[name] = {
            "n":          ag["n"],
            "hits":       ag["hits"], "miss": ag["miss"], "void": ag["void"],
            "hit_rate":   (round(ag["hits"] / decided, 4) if decided else None),
            "roi_per_unit": (round(ag["pnl"] / ag["n"], 4) if ag["n"] else None),
            "pnl_units":  round(ag["pnl"], 2),
            "avg_odds":   (round(ag["avg_odds"], 2) if ag["avg_odds"] else None),
        }
    out["confidence"] = {"level": "low",
        "reason": "These are HEURISTIC proxy rules, not production tiers. "
                  "Without VK2/injury/matchup, they cannot be claimed as "
                  "production-faithful tier definitions."}
    return out


# ---------------- Markdown formatting ----------------
def _fmt_table(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    if not rows:
        return "_(no rows)_\n"
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        line = "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |"
        lines.append(line)
    return "\n".join(lines) + "\n"


def render_markdown(report: Dict[str, Any]) -> str:
    md = []
    md.append(f"# Replay Partial-Parity Test Report\n")
    md.append(f"_Generated_: {report['generated_at_utc']}\n\n")
    md.append("> ⚠️ **PARTIAL-PARITY**. This report is a TEST REPORT, not "
              "production sign-off. VK2 / injury / matchup features are "
              "stubbed; see PRD changelog 2026-05-09 for full gap matrix.\n\n")

    s = report["1_dataset"]
    md.append("## 1. Dataset summary\n")
    md.append(_fmt_table([{
        "run_id":     s["replay_run_id"],
        "snapshot":   s["snapshot_window"],
        "min_ct":     s["date_range"]["min"],
        "max_ct":     s["date_range"]["max"],
        "evals":      s["evaluations_total"],
        "outcomes":   s["outcomes_total"],
    }], ["run_id", "snapshot", "min_ct", "max_ct", "evals", "outcomes"]))
    md.append(f"\nOutcome breakdown: {s['outcome_breakdown']}\n")
    md.append(f"\nFeature completeness: {s['feature_completeness_distribution']}\n")
    md.append(f"\nKnown parity gaps: {s['known_parity_gaps']}\n")
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["2_gates"]
    md.append("## 2. Gate distribution\n")
    md.append("### Top rejection reasons\n")
    md.append(_fmt_table(
        [{"reason": k, "n": v} for k, v in s["by_reason_top25"].items()],
        ["reason", "n"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["3_counterfactuals"]
    md.append("## 3. Counterfactual rule sets\n")
    rows = [{"rule": r, **{k: v.get(k) for k in
            ("n", "hits", "miss", "hit_rate", "roi_per_unit", "pnl_units",
             "avg_odds", "avg_tp", "avg_edge")}}
            for r, v in s["rules"].items()]
    md.append(_fmt_table(rows, ["rule", "n", "hits", "miss",
        "hit_rate", "roi_per_unit", "pnl_units",
        "avg_odds", "avg_tp", "avg_edge"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["4_alt_vs_standard"]
    md.append("## 4. Standard vs alternate vs combo\n")
    md.append("### By is_alternate\n")
    md.append(_fmt_table(s["by_alternate_flag"],
        ["is_alternate", "n", "hits", "miss", "hit_rate",
         "roi_per_unit", "pnl_units", "avg_odds"]))
    md.append("\n### By is_combo\n")
    md.append(_fmt_table(s["by_combo_flag"],
        ["is_combo", "n", "hits", "miss", "hit_rate",
         "roi_per_unit", "pnl_units", "avg_odds"]))
    md.append("\n### By stat_family\n")
    md.append(_fmt_table(s["by_stat_family"],
        ["stat_family", "n", "hits", "miss", "hit_rate",
         "roi_per_unit", "pnl_units", "avg_odds"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["5_odds_buckets"]
    md.append("## 5. Odds-bucket performance\n")
    md.append(_fmt_table(s["rows"], ["odds_bucket", "n", "hit_rate",
        "roi_per_unit", "pnl_units", "avg_tp", "avg_edge"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["6_timing"]
    md.append("## 6. Snapshot-timing performance\n")
    md.append(_fmt_table(s["rows"], ["snapshot_label", "n", "hits", "miss",
        "hit_rate", "roi_per_unit", "pnl_units", "avg_odds"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["7_direction_fail"]
    md.append("## 7. Direction-fail-but-profitable (μ vs TP probe)\n")
    md.append(f"`{json.dumps({k: v for k, v in s.items() if k != 'confidence'}, default=str)}`\n")
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["8_proxy_tiers"]
    md.append("## 8. Proxy tiers (HEURISTIC — NOT official tiers)\n")
    rows = [{"proxy": k, **{kk: vv for kk, vv in v.items()
                              if kk != "confidence"}}
            for k, v in s.items() if k != "confidence"]
    md.append(_fmt_table(rows, ["proxy", "n", "hits", "miss",
        "hit_rate", "roi_per_unit", "pnl_units", "avg_odds"]))
    md.append(f"\n_Confidence_: **{s['confidence']['level']}** — "
              f"{s['confidence']['reason']}\n\n")

    s = report["10_final_answer"]
    md.append("## 10. Final answer\n")
    for k, v in s.items():
        md.append(f"- **{k}**: {v}\n")

    return "".join(md)


def _fmt_signed(x, spec: str = "+.4f") -> str:
    """Null-safe format helper.

    Returns the literal string ``"n/a"`` when ``x`` is ``None`` so that
    callers can interpolate freely without guarding every variable. We
    do this because some replay-derived metrics are legitimately
    ``None`` when the underlying subset has zero settled rows (no hits
    + miss), and crashing the report there would force a re-run."""
    if x is None:
        return "n/a"
    try:
        return format(x, spec)
    except (TypeError, ValueError):
        return str(x)


def section_final_answer(report: Dict[str, Any]) -> Dict[str, str]:
    """Synthesize a one-line answer per category from the data above."""
    cf = report["3_counterfactuals"]["rules"]
    proxies = report["8_proxy_tiers"]
    odds = report["5_odds_buckets"]["rows"]

    # Best counterfactual rule
    rule_rows = [(name, v.get("roi_per_unit"), v.get("n"))
                  for name, v in cf.items() if (v.get("n") or 0) > 100]
    rule_rows = [r for r in rule_rows if r[1] is not None]
    rule_rows.sort(key=lambda x: x[1], reverse=True)
    best_rule = rule_rows[0] if rule_rows else None

    # Worst odds bucket
    odds_sorted = sorted(
        [r for r in odds if r["roi_per_unit"] is not None],
        key=lambda r: r["roi_per_unit"])

    # Longshot signal
    longshot_n   = (cf.get("ev_only_longshot") or {}).get("n", 0)
    longshot_roi = (cf.get("ev_only_longshot") or {}).get("roi_per_unit")
    longshot_proposal_roi = (
        cf.get("war_zone_longshot_proposal") or {}).get("roi_per_unit")

    # WZ proxy
    wz_roi = (proxies.get("proxy_war_zone") or {}).get("roi_per_unit")
    wz_n   = (proxies.get("proxy_war_zone") or {}).get("n", 0)
    wz_ls_roi = (proxies.get("proxy_war_zone_longshot") or {}).get("roi_per_unit")
    wz_ls_n   = (proxies.get("proxy_war_zone_longshot") or {}).get("n", 0)

    return {
        "what_we_can_trust_today":
            "Replay infrastructure, leakage gates, TP math, ref-odds chain, "
            "outcome settlement (134k unique rows). Production gate "
            "execution path is faithful.",
        "what_we_cannot_trust_yet":
            "Tier ROI (100% unqualified due to missing VK2/injury/matchup). "
            "Direction signal (μ is BDL-rolling only). Production tier "
            "claims of any kind.",
        "best_counterfactual_rule":
            (f"{best_rule[0]} → ROI {_fmt_signed(best_rule[1])} on n={best_rule[2]}"
             if best_rule else "no rule had >100 picks settled"),
        "obviously_bad_areas":
            (f"odds buckets with worst ROI: " +
             ", ".join(f"{r['odds_bucket']}({_fmt_signed(r['roi_per_unit'], '+.3f')}, n={r['n']})"
                        for r in odds_sorted[:3])
             if odds_sorted else "no odds-bucket data"),
        "longshot_mode_signal":
            (f"ev_only_longshot ROI={_fmt_signed(longshot_roi)} on n={longshot_n} | "
             f"wz_longshot_proposal ROI={_fmt_signed(longshot_proposal_roi)}"
             if (longshot_n or 0) > 0 or (longshot_roi is not None)
             else "no longshot data"),
        "war_zone_concept_status":
            (f"proxy_war_zone ROI={_fmt_signed(wz_roi)} on n={wz_n} | "
             f"proxy_war_zone_longshot ROI={_fmt_signed(wz_ls_roi)} on n={wz_ls_n} "
             f"— DIRECTIONAL ONLY (proxy rules ≠ production tiers)"
             if (wz_n or 0) > 0 or (wz_ls_n or 0) > 0
             else "insufficient data; rerun with full features to verify"),
        "headline":
            "PARTIAL-PARITY: replay infrastructure works; ROI claims "
            "require VK2/injury/matchup wiring before deployment.",
    }


# ---------------- driver ----------------
async def amain():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--snapshot-window", default=None)  # for log only
    p.add_argument("--sport", default="nba")
    p.add_argument("--feature-completeness", default="partial")
    p.add_argument("--include-unqualified", default="true")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    started = datetime.now(timezone.utc)
    report: Dict[str, Any] = {
        "generated_at_utc": started.isoformat(),
        "args":             vars(args),
    }
    report["1_dataset"]          = await section_dataset(db, run_id=args.run_id)
    report["2_gates"]            = await section_gate_dist(db, run_id=args.run_id)
    report["3_counterfactuals"]  = await section_counterfactuals(db, run_id=args.run_id)
    report["4_alt_vs_standard"]  = await section_alt_vs_standard(db, run_id=args.run_id)
    report["5_odds_buckets"]     = await section_odds_buckets(db, run_id=args.run_id)
    report["6_timing"]           = await section_timing(db, run_id=args.run_id)
    report["7_direction_fail"]   = await section_direction_fail(db, run_id=args.run_id)
    report["8_proxy_tiers"]      = await section_proxy_tiers(db, run_id=args.run_id)
    report["10_final_answer"]    = section_final_answer(report)

    finished = datetime.now(timezone.utc)
    report["wallclock_seconds"]  = (finished - started).total_seconds()

    out_path = Path(args.output)
    out_path.write_text(render_markdown(report))
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    print(f"wrote {json_path}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
