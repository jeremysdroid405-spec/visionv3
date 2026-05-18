"""SH gate-loosening sensitivity grid.

Runs the universal pipeline N times on the **same** historical
snapshot, varying ONE knob at a time (and a final all-loose combo)
so we can attribute each gain in qualified picks / win-rate / ROI
to the SPECIFIC gate that was loosened.

NO production thresholds touched. All loosening happens via the
post-eval override knobs added to `run_production_replay` (see
`production_replay_runner.py`).

Knobs in scope (all SH-only, historical-only):
  • `allow_one_sided_for_accuracy_test` — bypass `tp_source_gate`
    and the alt one-sided `market_structure_gate` for one_sided rows.
  • `sh_tp_gate_min_override`            — lower model-prob floor.
  • `sh_edge_gate_min_override`          — lower edge floor (pp).
  • `sh_hit_rate_gate_min_override`      — lower L20 hit-rate floor.
  • `sh_cv_gate_max_override`            — raise CV ceiling.

Output:
  • Console grid showing per-run candidates / qualified / cards /
    W-L-P / HR / ROI / per-bucket performance.
  • JSON artifact at
    `/app/backend/audits/sh_gate_sensitivity_grid.json`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.pipeline import run_pipeline, PIPELINE_VERSION


SPORT = "mlb"
SNAPSHOT = "2026-05-05T11:00:00Z"
TIER = "safe_haven"
ARTIFACT_PATH = Path(
    "/app/backend/audits/sh_gate_sensitivity_grid.json"
)


# ── Grid definition ─────────────────────────────────────────────────
# Each row: human-readable label + knob payload. The harness runs
# them sequentially and persists everything keyed by `test_id`.
GRID: List[Dict[str, Any]] = [
    {
        "label": "P0 — baseline (production policy)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00001",
        "knobs": {},
    },
    {
        "label": "P1 — allow one-sided (tp_source bypass)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00002",
        "knobs": {"allow_one_sided_for_accuracy_test": True},
    },
    {
        "label": "P2 — P1 + tp_gate >= 50",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00003",
        "knobs": {
            "allow_one_sided_for_accuracy_test": True,
            "sh_tp_gate_min_override": 50.0,
        },
    },
    {
        "label": "P3 — P2 + edge_gate >= 0.0 pp",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00004",
        "knobs": {
            "allow_one_sided_for_accuracy_test": True,
            "sh_tp_gate_min_override": 50.0,
            "sh_edge_gate_min_override": 0.0,
        },
    },
    {
        "label": "P4 — P3 + hit_rate_gate >= 60",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00005",
        "knobs": {
            "allow_one_sided_for_accuracy_test": True,
            "sh_tp_gate_min_override": 50.0,
            "sh_edge_gate_min_override": 0.0,
            "sh_hit_rate_gate_min_override": 60.0,
        },
    },
    {
        "label": "P5 — P4 + cv_gate <= 1.50 (very loose)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00006",
        "knobs": {
            "allow_one_sided_for_accuracy_test": True,
            "sh_tp_gate_min_override": 50.0,
            "sh_edge_gate_min_override": 0.0,
            "sh_hit_rate_gate_min_override": 60.0,
            "sh_cv_gate_max_override": 1.50,
        },
    },
    # Isolated single-knob comparisons (vs baseline) so we can
    # attribute each lever individually rather than only cumulatively.
    {
        "label": "S1 — ONLY edge_gate >= 0.0 (all others prod)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00007",
        "knobs": {"sh_edge_gate_min_override": 0.0},
    },
    {
        "label": "S2 — ONLY hit_rate_gate >= 60 (all others prod)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00008",
        "knobs": {"sh_hit_rate_gate_min_override": 60.0},
    },
    {
        "label": "S3 — ONLY cv_gate <= 1.50 (all others prod)",
        "test_id": "MLB-SHGRID-20260505-1100UTC-00009",
        "knobs": {"sh_cv_gate_max_override": 1.50},
    },
]


def _classify(row: Dict[str, Any]) -> str:
    ts = row.get("tp_source")
    if ts == "devig":
        return "devig"
    if ts == "one_sided":
        return "one_sided"
    return "unknown"


def _alt_or_std(row: Dict[str, Any]) -> str:
    return "alt" if bool(row.get("is_alternate_market")) else "std"


def _grade_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = losses = pushes = ungraded = 0
    stake = profit = 0.0
    for r in rows:
        st = r.get("grade_status")
        stake += float(r.get("stake_units") or 0.0)
        profit += float(r.get("profit_units") or 0.0)
        if st == "win":
            wins += 1
        elif st == "loss":
            losses += 1
        elif st == "push":
            pushes += 1
        else:
            ungraded += 1
    decided = wins + losses
    hr = (100.0 * wins / decided) if decided else None
    roi = (100.0 * profit / stake) if stake else None
    return {
        "n": len(rows), "wins": wins, "losses": losses,
        "pushes": pushes, "ungraded": ungraded,
        "hit_rate_pct": round(hr, 2) if hr is not None else None,
        "roi_pct": round(roi, 2) if roi is not None else None,
        "profit_units": round(profit, 4),
        "stake_units": round(stake, 4),
    }


def _build_breakdown(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = [r for r in rows if r.get("tp_source") is not None]
    qualified = [r for r in rows if r.get("gate_pass") is True]
    by_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in qualified:
        b = _classify(r)
        by_bucket[b].append(r)
        if b == "one_sided":
            by_bucket[f"one_sided_{_alt_or_std(r)}"].append(r)
    cand_bucket: Counter = Counter()
    for r in candidates:
        b = _classify(r)
        cand_bucket[b] += 1
        if b == "one_sided":
            cand_bucket[f"one_sided_{_alt_or_std(r)}"] += 1
    bucket_perf: Dict[str, Any] = {}
    for b in ("devig", "one_sided",
              "one_sided_std", "one_sided_alt"):
        rb = by_bucket.get(b, [])
        bucket_perf[b] = {
            "candidates": cand_bucket.get(b, 0),
            "qualified": len(rb),
            **_grade_metrics(rb),
        }
    # Override-attribution: how many qualifications were enabled by
    # each override (a row qualified only because the override fired).
    override_attribution: Dict[str, int] = {
        "tp_gate_override":       0,
        "edge_gate_override":     0,
        "hit_rate_gate_override": 0,
        "cv_gate_override":       0,
        "accuracy_test_bypass":   0,
    }
    for r in qualified:
        if r.get("tp_gate_override_applied"):
            override_attribution["tp_gate_override"] += 1
        if r.get("edge_gate_override_applied"):
            override_attribution["edge_gate_override"] += 1
        if r.get("hit_rate_gate_override_applied"):
            override_attribution["hit_rate_gate_override"] += 1
        if r.get("cv_gate_override_applied"):
            override_attribution["cv_gate_override"] += 1
        if r.get("accuracy_test_bypass_applied"):
            override_attribution["accuracy_test_bypass"] += 1
    # Top losing archetypes by (stat_family, side).
    arch: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {"losses": 0, "wins": 0, "pushes": 0,
                 "profit_units": 0.0, "stake_units": 0.0}
    )
    for r in qualified:
        key = (r.get("stat_family") or "?", r.get("side") or "?")
        st = r.get("grade_status")
        if st == "win":
            arch[key]["wins"] += 1
        elif st == "loss":
            arch[key]["losses"] += 1
        elif st == "push":
            arch[key]["pushes"] += 1
        arch[key]["profit_units"] += float(r.get("profit_units") or 0.0)
        arch[key]["stake_units"] += float(r.get("stake_units") or 0.0)
    arch_rows = []
    for (fam, side), s in arch.items():
        decided = s["wins"] + s["losses"]
        arch_rows.append({
            "stat_family": fam, "side": side,
            "wins": s["wins"], "losses": s["losses"],
            "pushes": s["pushes"], "decided": decided,
            "hit_rate_pct": (round(100.0 * s["wins"] / decided, 2)
                             if decided else None),
            "profit_units": round(s["profit_units"], 4),
            "stake_units": round(s["stake_units"], 4),
            "roi_pct": (round(100.0 * s["profit_units"]
                              / s["stake_units"], 2)
                        if s["stake_units"] else None),
        })
    arch_rows.sort(
        key=lambda x: (-(x["losses"] or 0), (x["profit_units"] or 0.0))
    )
    return {
        "sh_candidates": len(candidates),
        "sh_qualified":  len(qualified),
        "overall":       _grade_metrics(qualified),
        "by_bucket":     bucket_perf,
        "override_attribution": override_attribution,
        "top_losing_archetypes": arch_rows[:10],
    }


async def _load_outputs(db, serial: str) -> List[Dict[str, Any]]:
    return [
        r async for r in db["mlb_test_outputs"].find(
            {"replay_serial": serial},
            projection={"_id": 0},
        )
    ]


async def _count_cards(db, serial: str) -> int:
    return await db["mlb_test_cards"].count_documents(
        {"replay_serial": serial}
    )


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print("=" * 72)
    print("  SH GATE-LOOSENING SENSITIVITY GRID")
    print(f"  snapshot   = {SNAPSHOT}")
    print(f"  tier       = {TIER}")
    print(f"  pipeline   = {PIPELINE_VERSION}")
    print(f"  grid_size  = {len(GRID)}")
    print("=" * 72)

    runs: List[Dict[str, Any]] = []
    for entry in GRID:
        label = entry["label"]
        test_id = entry["test_id"]
        knobs = entry["knobs"]
        t0 = datetime.now(timezone.utc)
        summary = await run_pipeline(
            db, sport=SPORT, mode="historical",
            snapshot_time=SNAPSHOT,
            output_namespace="test",
            test_id=test_id,
            tier=TIER,
            notes=f"SH grid: {label}",
            **knobs,
        )
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        rows = await _load_outputs(db, summary["serial"])
        cards = await _count_cards(db, summary["serial"])
        breakdown = _build_breakdown(rows)
        runs.append({
            "label": label,
            "test_id": test_id,
            "knobs": knobs,
            "elapsed_s": round(elapsed, 2),
            "serial": summary["serial"],
            "run_summary": {
                k: summary.get(k) for k in (
                    "rows_scanned", "rows_qualified",
                    "eligibility_rejects", "cards_displayed",
                    "wins", "losses", "pushes", "ungraded",
                    "hit_rate_pct", "roi_pct",
                    "profit_units", "stake_units",
                    "accuracy_test_bypass_total",
                    "sh_tp_gate_min_override",
                    "tp_gate_override_count",
                    "sh_edge_gate_min_override",
                    "edge_gate_override_count",
                    "sh_hit_rate_gate_min_override",
                    "hit_rate_gate_override_count",
                    "sh_cv_gate_max_override",
                    "cv_gate_override_count",
                )
            },
            "cards_in_db": cards,
            "breakdown": breakdown,
        })
        ovr = breakdown["overall"]
        print(
            f"\n[{label}]  elapsed={elapsed:.1f}s "
            f"serial={summary['serial']}\n"
            f"  cand={breakdown['sh_candidates']} "
            f"qual={breakdown['sh_qualified']} "
            f"cards={cards}  W/L/P={ovr['wins']}/{ovr['losses']}/{ovr['pushes']} "
            f"ung={ovr['ungraded']} "
            f"HR={ovr['hit_rate_pct']} ROI={ovr['roi_pct']} "
            f"profit={ovr['profit_units']}/{ovr['stake_units']}u"
        )
        # Buckets
        bb = breakdown["by_bucket"]
        for b in ("devig", "one_sided", "one_sided_std", "one_sided_alt"):
            bp = bb[b]
            if bp["candidates"] == 0 and bp["qualified"] == 0:
                continue
            print(
                f"    {b:<18s} "
                f"cand={bp['candidates']:>4d} qual={bp['qualified']:>3d} "
                f"W/L/P={bp['wins']}/{bp['losses']}/{bp['pushes']} "
                f"HR={bp['hit_rate_pct']} ROI={bp['roi_pct']}"
            )
        # Override attribution
        att = breakdown["override_attribution"]
        if any(v for v in att.values()):
            print("    bypass attribution (qualified rows that used the "
                  f"override): {att}")

    # ── Side-by-side grid summary ─────────────────────────────────
    print("\n" + "=" * 110)
    print(
        f"  {'label':<48s} {'cand':>4s} {'qual':>4s} {'crd':>3s} "
        f"{'W':>3s} {'L':>3s} {'P':>3s} {'HR':>6s} {'ROI':>7s} "
        f"{'profit':>8s}"
    )
    print("=" * 110)
    for r in runs:
        bd = r["breakdown"]
        ovr = bd["overall"]
        print(
            f"  {r['label']:<48s} "
            f"{bd['sh_candidates']:>4d} {bd['sh_qualified']:>4d} "
            f"{r['cards_in_db']:>3d} "
            f"{ovr['wins']:>3d} {ovr['losses']:>3d} {ovr['pushes']:>3d} "
            f"{str(ovr['hit_rate_pct']):>6s} "
            f"{str(ovr['roi_pct']):>7s} "
            f"{ovr['profit_units']:>8.3f}"
        )

    # ── Persist artifact ──────────────────────────────────────────
    payload = {
        "audit_kind": "sh_gate_sensitivity_grid",
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "snapshot": SNAPSHOT,
        "tier": TIER,
        "pipeline_version": PIPELINE_VERSION,
        "runs": runs,
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[artifact] wrote {ARTIFACT_PATH}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
