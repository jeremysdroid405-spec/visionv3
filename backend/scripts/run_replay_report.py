#!/usr/bin/env python3
"""
PropVision Replay — $1-Flat-Bet Publication Simulation Report.

Simulates the question:

    "If we had ingested these historical Odds-API props as live props
     today, what would PropVision have published, and what would have
     happened if we bet $1 on every published pick?"

This is NOT a candidate-pool ROI report. The 503k+ replay_evaluations
are CANDIDATES, not bets. Bets are the subset whose tier is exactly
one of:

    safe_haven | front_lines | war_zone

ROI / hit-rate / odds breakdowns ONLY apply to that qualified subset.

If zero candidates pass the production gates (the current state of
the partial-parity replay run), the report says so loudly and refuses
to invent a number. Proxy/counterfactual rule sets are placed in a
clearly-separated EXPERIMENTAL section and explicitly NOT presented
as production performance.

USAGE
-----
    python /app/backend/scripts/run_replay_report.py \\
        --run-id a1aeb71a6ef046baae4fb56deef06667 \\
        --output /app/audit_reports/replay_publication_report.md
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

# Tiers we treat as PUBLISHED. Anything else (including `unqualified`,
# `None`, gate-failed reasons) is a candidate that did NOT become a
# bet. Mirrors `services.scoring.gates.thresholds.THRESHOLDS["nba"]`
# tier names.
PUBLISHED_TIERS = ("safe_haven", "front_lines", "war_zone")


# ---------------- helpers ----------------
def _fmt_signed(x, spec: str = "+.4f") -> str:
    """Null-safe numeric formatter — used so `None` from empty subsets
    never crashes the markdown render."""
    if x is None:
        return "n/a"
    try:
        return format(x, spec)
    except (TypeError, ValueError):
        return str(x)


def _pct(n, d) -> float:
    return (round(100.0 * n / d, 4) if d else 0.0)


# ---------------- core sections ----------------
async def section_dataset(db, *, run_id: str) -> Dict[str, Any]:
    """Just describes the candidate pool — no ROI claims here."""
    n_eval = await db[EVALS].count_documents({"replay_run_id": run_id})
    n_out  = await db[OUTS].count_documents({"replay_run_id": run_id})

    # Date range from the candidate pool.
    rng = [d async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": None,
                     "min_ct": {"$min": "$commence_time"},
                     "max_ct": {"$max": "$commence_time"}}},
    ])]
    min_ct = rng[0]["min_ct"] if rng else None
    max_ct = rng[0]["max_ct"] if rng else None

    snap_sample = await db[EVALS].find_one(
        {"replay_run_id": run_id}, {"_id": 0, "snapshot_label": 1},
    )
    snap = snap_sample.get("snapshot_label") if snap_sample else None

    # Outcome breakdown over the SETTLED candidate pool (still not a
    # bet table — this is just data-quality signal).
    out_dist: Dict[str, int] = {"hit": 0, "miss": 0,
                                  "push": 0, "void_dnp": 0}
    async for d in db[OUTS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}}},
    ]):
        out_dist[d["_id"]] = d["n"]

    return {
        "replay_run_id":       run_id,
        "snapshot_window":     snap,
        "candidate_date_range": {"min": min_ct, "max": max_ct},
        "candidates_total":     n_eval,
        "settled_candidates":   n_out,
        "settled_outcome_breakdown": out_dist,
    }


async def section_publication_simulation(
    db, *, run_id: str,
) -> Dict[str, Any]:
    """The publication simulation.

    For every settled outcome we look up its tier (stored on
    `replay_outcomes.tier_at_eval`) and only count it as a bet if
    `tier_at_eval ∈ PUBLISHED_TIERS`. Each qualifying row contributes
    $1 stake and `pnl_units` to the bankroll.

    Per-tier rollup includes:
        n  hits  miss  void  push  hit_rate  roi_per_unit
        pnl_units  avg_odds  avg_tp  avg_edge
    """
    # --- Per-tier rollup over settled qualified picks. ---
    pipe = [
        {"$match": {"replay_run_id": run_id,
                     "tier_at_eval": {"$in": list(PUBLISHED_TIERS)}}},
        {"$group": {
            "_id": "$tier_at_eval",
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
    by_tier: Dict[str, Dict[str, Any]] = {
        t: {"tier": t, "n": 0, "hits": 0, "miss": 0,
            "void": 0, "push": 0,
            "hit_rate": None, "roi_per_unit": None,
            "pnl_units": 0.0, "avg_odds": None,
            "avg_tp": None, "avg_edge": None}
        for t in PUBLISHED_TIERS
    }
    async for d in db[OUTS].aggregate(pipe):
        decided = d["hits"] + d["miss"]
        by_tier[d["_id"]] = {
            "tier":         d["_id"],
            "n":            d["n"],
            "hits":         d["hits"], "miss": d["miss"],
            "void":         d["void"], "push": d["push"],
            "hit_rate":     (round(d["hits"] / decided, 4)
                              if decided else None),
            "roi_per_unit": (round(d["pnl"] / d["n"], 4)
                              if d["n"] else None),
            "pnl_units":    round(d["pnl"], 4),
            "avg_odds":     (round(d["avg_odds"], 2)
                              if d["avg_odds"] is not None else None),
            "avg_tp":       (round(d["avg_tp"], 2)
                              if d["avg_tp"] is not None else None),
            "avg_edge":     (round(d["avg_edge"], 2)
                              if d["avg_edge"] is not None else None),
        }

    # --- Combined qualified rollup. ---
    combined_pipe = [
        {"$match": {"replay_run_id": run_id,
                     "tier_at_eval": {"$in": list(PUBLISHED_TIERS)}}},
        {"$group": {
            "_id": None,
            "n":   {"$sum": 1},
            "hits":{"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":{"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "void":{"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
            "push":{"$sum": {"$cond": [{"$eq": ["$outcome", "push"]}, 1, 0]}},
            "pnl": {"$sum": "$pnl_units"},
            "avg_odds": {"$avg": "$odds_american"},
        }},
    ]
    cagg = [d async for d in db[OUTS].aggregate(combined_pipe)]
    if cagg:
        c = cagg[0]
        decided = c["hits"] + c["miss"]
        combined = {
            "n":            c["n"],
            "hits":         c["hits"], "miss": c["miss"],
            "void":         c["void"], "push": c["push"],
            "hit_rate":     (round(c["hits"] / decided, 4)
                              if decided else None),
            "roi_per_unit": (round(c["pnl"] / c["n"], 4)
                              if c["n"] else None),
            "pnl_units":    round(c["pnl"], 4),
            "avg_odds":     (round(c["avg_odds"], 2)
                              if c["avg_odds"] is not None else None),
        }
    else:
        combined = {"n": 0, "hits": 0, "miss": 0, "void": 0, "push": 0,
                     "hit_rate": None, "roi_per_unit": None,
                     "pnl_units": 0.0, "avg_odds": None}

    # --- Candidate-pool sizing. ---
    candidates = await db[EVALS].count_documents({"replay_run_id": run_id})
    qualified_candidates = await db[EVALS].count_documents(
        {"replay_run_id": run_id,
         "tier": {"$in": list(PUBLISHED_TIERS)}})

    return {
        "thesis":                 ("This report simulates a $1 flat bet "
                                    "on every prop PropVision would have "
                                    "published."),
        "candidates_evaluated":   candidates,
        "candidates_qualified":   qualified_candidates,
        "qualified_pct":          _pct(qualified_candidates, candidates),
        "settled_qualified_total": combined["n"],
        "by_tier":                by_tier,
        "combined":               combined,
    }


async def section_unqualified_reasons(db, *, run_id: str) -> Dict[str, Any]:
    """Why candidates didn't become bets — informative, not ROI."""
    by_reason: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier": {"$nin": list(PUBLISHED_TIERS)}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 25},
    ]):
        by_reason[d["_id"] or "<none>"] = d["n"]
    n_total = sum(by_reason.values())
    return {
        "unqualified_total": n_total,
        "top_reasons":       by_reason,
    }


# ---------------- experimental (clearly partitioned) ----------------
async def section_experimental_proxies(
    db, *, run_id: str,
) -> Dict[str, Any]:
    """HEURISTIC counterfactual rule sets — labeled experimental and
    NOT production performance. Kept so we can see whether a different
    rule set would have produced bets when the production stack
    produced none. None of these numbers may be presented as
    PropVision publication ROI."""
    rules = {
        "tp_edge_gate": {
            "p_true_active": {"$gte": 55},
            "edge_vs_fair":  {"$gte": 3},
        },
        "ev_only_longshot": {
            "odds_american": {"$gte": 200},
            "edge_vs_fair":  {"$gte": 5},
        },
        "proxy_safe_haven": {
            "odds_american": {"$lte": -180},
            "p_true_active": {"$gte": 65},
            "edge_vs_fair":  {"$gte": 2},
        },
    }
    out: Dict[str, Any] = {}
    for name, pred in rules.items():
        match = {"replay_run_id": run_id, **pred}
        agg = [d async for d in db[OUTS].aggregate([
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
        ])]
        if not agg:
            out[name] = {"n": 0}
            continue
        a = agg[0]
        decided = a["hits"] + a["miss"]
        out[name] = {
            "n":            a["n"],
            "hits":         a["hits"], "miss": a["miss"],
            "void":         a["void"],
            "hit_rate":     (round(a["hits"] / decided, 4)
                              if decided else None),
            "roi_per_unit": (round(a["pnl"] / a["n"], 4) if a["n"] else None),
            "pnl_units":    round(a["pnl"], 4),
            "avg_odds":     (round(a["avg_odds"], 2)
                              if a["avg_odds"] is not None else None),
        }
    out["_disclaimer"] = (
        "Heuristic counterfactual rule sets. NOT a measurement of "
        "PropVision publication ROI. Presented only to prove the "
        "candidate pool contains usable signal — does NOT imply any "
        "of these rules should be deployed."
    )
    return out


# ---------------- markdown ----------------
def _fmt_table(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    if not rows:
        return "_(no rows)_\n"
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(report: Dict[str, Any]) -> str:
    md: List[str] = []
    sim = report["2_publication_simulation"]
    md.append("# PropVision Replay — $1 Flat-Bet Publication Simulation\n\n")
    md.append(f"_Generated_: {report['generated_at_utc']}\n\n")
    md.append(f"> {sim['thesis']}\n\n")

    qualified_n = sim["combined"]["n"]
    if qualified_n == 0:
        md.append("## ⚠️ Replay tier parity is incomplete\n\n")
        md.append(
            "**No props reached production tiers in this run.** "
            "Every candidate failed at least one production gate "
            "(VK2/injury/matchup features are stubbed in the partial-"
            "parity dataset, so the direction / hit-rate gates "
            "over-reject). PropVision would have published **zero "
            "picks** from this slate. We refuse to report a "
            "publication ROI on an empty bet log.\n\n"
        )

    s = report["1_dataset"]
    md.append("## 1. Candidate pool (NOT bets)\n")
    md.append(_fmt_table([{
        "run_id":     s["replay_run_id"],
        "snapshot":   s["snapshot_window"],
        "min_ct":     s["candidate_date_range"]["min"],
        "max_ct":     s["candidate_date_range"]["max"],
        "candidates": s["candidates_total"],
        "settled":    s["settled_candidates"],
    }], ["run_id", "snapshot", "min_ct", "max_ct",
          "candidates", "settled"]))
    md.append(f"\nSettled outcome breakdown: "
              f"`{s['settled_outcome_breakdown']}` — "
              "this is data-quality signal on the candidate pool, "
              "NOT a P&L log.\n\n")

    md.append("## 2. Publication simulation (the answer)\n")
    md.append(_fmt_table([{
        "candidates":              sim["candidates_evaluated"],
        "qualified":               sim["candidates_qualified"],
        "qualified_pct":           f"{sim['qualified_pct']}%",
        "settled_qualified":       sim["settled_qualified_total"],
    }], ["candidates", "qualified", "qualified_pct",
          "settled_qualified"]))

    md.append("\n### Per-tier results — $1 flat bet on each published pick\n")
    rows = []
    for t in PUBLISHED_TIERS:
        v = sim["by_tier"][t]
        rows.append({
            "tier":         t,
            "n":            v["n"],
            "hits":         v["hits"],
            "miss":         v["miss"],
            "void":         v["void"],
            "hit_rate":     _fmt_signed(v["hit_rate"], ".4f"),
            "roi_per_unit": _fmt_signed(v["roi_per_unit"], "+.4f"),
            "pnl_units":    _fmt_signed(v["pnl_units"], "+.2f"),
            "avg_odds":     _fmt_signed(v["avg_odds"], ".1f"),
            "avg_tp":       _fmt_signed(v["avg_tp"], ".1f"),
            "avg_edge":     _fmt_signed(v["avg_edge"], "+.2f"),
        })
    md.append(_fmt_table(rows, [
        "tier", "n", "hits", "miss", "void", "hit_rate",
        "roi_per_unit", "pnl_units", "avg_odds", "avg_tp", "avg_edge",
    ]))

    md.append("\n### Combined qualified ROI (all three tiers)\n")
    c = sim["combined"]
    md.append(_fmt_table([{
        "n":            c["n"],
        "hits":         c["hits"],
        "miss":         c["miss"],
        "void":         c["void"],
        "hit_rate":     _fmt_signed(c["hit_rate"], ".4f"),
        "roi_per_unit": _fmt_signed(c["roi_per_unit"], "+.4f"),
        "pnl_units":    _fmt_signed(c["pnl_units"], "+.2f"),
        "avg_odds":     _fmt_signed(c["avg_odds"], ".1f"),
    }], ["n", "hits", "miss", "void",
          "hit_rate", "roi_per_unit", "pnl_units", "avg_odds"]))

    u = report["3_unqualified_reasons"]
    md.append("\n## 3. Why candidates were NOT published\n")
    md.append(f"Unqualified candidates: **{u['unqualified_total']:,}**\n\n")
    md.append(_fmt_table(
        [{"reason": k, "n": v} for k, v in u["top_reasons"].items()],
        ["reason", "n"]))

    e = report["4_experimental_proxies"]
    md.append("\n## 4. Experimental — heuristic rule probes\n")
    md.append(f"> {e['_disclaimer']}\n\n")
    rows = []
    for name in ("tp_edge_gate", "ev_only_longshot", "proxy_safe_haven"):
        v = e.get(name) or {}
        rows.append({
            "rule":         name,
            "n":            v.get("n", 0),
            "hits":         v.get("hits", 0),
            "miss":         v.get("miss", 0),
            "hit_rate":     _fmt_signed(v.get("hit_rate"), ".4f"),
            "roi_per_unit": _fmt_signed(v.get("roi_per_unit"), "+.4f"),
            "pnl_units":    _fmt_signed(v.get("pnl_units"), "+.2f"),
            "avg_odds":     _fmt_signed(v.get("avg_odds"), ".1f"),
        })
    md.append(_fmt_table(rows, [
        "rule", "n", "hits", "miss", "hit_rate",
        "roi_per_unit", "pnl_units", "avg_odds",
    ]))

    md.append("\n## 5. Final answer\n")
    if qualified_n == 0:
        md.append(
            "- **headline**: PropVision would have published **0 picks** "
            "from this 30-day NBA candidate pool.\n"
            "- **publication_roi**: not reportable (no bets).\n"
            "- **why**: 100% of candidates failed at least one "
            "production gate. Replay tier parity is incomplete because "
            "no props reached production tiers — direction / hit-rate "
            "gates need historical VK2 / injury / matchup features that "
            "are not yet wired.\n"
            "- **next**: wire historical VK2 (Phase 2.5 step 1) and "
            "re-run; report will then carry real publication ROI.\n"
        )
    else:
        md.append(
            f"- **headline**: PropVision would have published "
            f"**{qualified_n} picks**.\n"
            f"- **combined_roi_per_unit**: "
            f"{_fmt_signed(c['roi_per_unit'], '+.4f')}\n"
            f"- **combined_pnl_units**: "
            f"{_fmt_signed(c['pnl_units'], '+.2f')}\n"
            f"- **combined_hit_rate**: "
            f"{_fmt_signed(c['hit_rate'], '.4f')}\n"
        )

    return "".join(md)


# ---------------- driver ----------------
async def amain():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--snapshot-window", default=None)  # log-only
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
    report["1_dataset"]                 = await section_dataset(
        db, run_id=args.run_id)
    report["2_publication_simulation"]  = await section_publication_simulation(
        db, run_id=args.run_id)
    report["3_unqualified_reasons"]     = await section_unqualified_reasons(
        db, run_id=args.run_id)
    report["4_experimental_proxies"]    = await section_experimental_proxies(
        db, run_id=args.run_id)

    finished = datetime.now(timezone.utc)
    report["wallclock_seconds"] = (finished - started).total_seconds()

    out_path = Path(args.output)
    out_path.write_text(render_markdown(report))
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    print(f"wrote {json_path}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
