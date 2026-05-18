"""SH accuracy test — measure raw one-sided model accuracy.

Runs the universal pipeline **twice** on the same slate / snapshot:

  • baseline (production policy)
      └─ `allow_one_sided_for_accuracy_test=False`
      └─ `tp_source_gate` enforced (one_sided rejected unless the
         narrow elite-binary override fires).
      └─ `market_structure_gate` enforced (alt one_sided rejected).
  • accuracy test (one-sided allowed through gates)
      └─ `allow_one_sided_for_accuracy_test=True`
      └─ `tp_source_gate` bypassed on one_sided rows.
      └─ `market_structure_gate` bypassed on rows whose rejection
         was specifically the `tp_source: one_sided` rule.

Everything else is **identical** between the two runs:
  - PP-illegal / non-playable / no-odds filtering (eligibility layer)
  - canonical engine (best-price routing, devig, book counts)
  - HR / CV / TP / projection / direction / margin gates
  - card builder (top-N, dedupe, ordering)
  - grading + ROI (per-row sportsbook odds)

After both runs complete, we read the two serials' outputs from
`mlb_test_outputs` and produce a JSON breakdown:

  1. SH candidates before/after
  2. SH qualified before/after
  3. displayed cards
  4. W / L / P
  5. HR (hit rate %)
  6. ROI (using actual sportsbook odds)
  7. devig vs one_sided performance
  8. alt one_sided vs standard one_sided performance
  9. top losing archetypes (stat_family + side)
 10. JSON artifact path

NO production gates are touched. The accuracy flag only affects
HISTORICAL test runs invoked with that argument set to True.
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
BASELINE_ID = "MLB-HIST-20260505-1100UTC-00005"
ACCURACY_ID  = "MLB-HIST-20260505-1100UTC-00006"
TIER = "safe_haven"
# Test-only SH tp_gate min override (model-probability floor in pp).
# Production thresholds unchanged. Applied only to the accuracy run.
SH_TP_GATE_MIN_OVERRIDE = 50.0
ARTIFACT_PATH = Path(
    "/app/backend/audits/sh_accuracy_test_one_sided.json"
)


def _classify(row: Dict[str, Any]) -> str:
    """Return the audit bucket label for a single output row.

    Buckets:
      devig          — tp_source == "devig" (two-sided market)
      one_sided      — tp_source == "one_sided"
                       (further split into alt / standard below)
      unknown        — tp_source is None (no metric build,
                       e.g. tier_odds_bucket_fail short-circuit)
    """
    ts = row.get("tp_source")
    if ts == "devig":
        return "devig"
    if ts == "one_sided":
        return "one_sided"
    return "unknown"


def _alt_or_std(row: Dict[str, Any]) -> str:
    return "alt" if bool(row.get("is_alternate_market")) else "std"


def _grade_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate W/L/P, HR, ROI for a list of QUALIFIED output rows."""
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
        elif st == "not_qualified":
            # Should never appear here — caller passes gate_pass=True
            # rows only. Counted for safety so totals reconcile.
            ungraded += 1
        else:
            ungraded += 1
    decided = wins + losses
    hr = (100.0 * wins / decided) if decided else None
    roi = (100.0 * profit / stake) if stake else None
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "ungraded": ungraded,
        "hit_rate_pct": round(hr, 2) if hr is not None else None,
        "roi_pct": round(roi, 2) if roi is not None else None,
        "profit_units": round(profit, 4),
        "stake_units": round(stake, 4),
    }


async def _load_outputs(
    db, serial: str,
) -> List[Dict[str, Any]]:
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


def _build_breakdown(rows: List[Dict[str, Any]],
                     *, accuracy_mode: bool) -> Dict[str, Any]:
    # Candidates = all rows that reached gate evaluation
    # (tp_source is not None). Rows with tp_source=None were
    # short-circuited via tier_odds_bucket_fail BEFORE any gate ran.
    candidates = [r for r in rows if r.get("tp_source") is not None]
    qualified = [r for r in rows if r.get("gate_pass") is True]

    # Bucket split
    by_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in qualified:
        b = _classify(r)
        by_bucket[b].append(r)
        if b == "one_sided":
            by_bucket[f"one_sided_{_alt_or_std(r)}"].append(r)

    # Candidate-side bucketing (so we can show acceptance rate
    # per bucket).
    cand_bucket: Counter = Counter()
    for r in candidates:
        b = _classify(r)
        cand_bucket[b] += 1
        if b == "one_sided":
            cand_bucket[f"one_sided_{_alt_or_std(r)}"] += 1

    bucket_perf: Dict[str, Any] = {}
    for b in ("devig", "one_sided",
              "one_sided_std", "one_sided_alt"):
        rows_b = by_bucket.get(b, [])
        bucket_perf[b] = {
            "candidates": cand_bucket.get(b, 0),
            "qualified": len(rows_b),
            **_grade_metrics(rows_b),
        }

    # Top losing archetypes — (stat_family, side) pairs ranked by
    # loss count, then by net-units lost (most negative).
    losses_by_arch: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {"losses": 0, "wins": 0, "pushes": 0,
                 "profit_units": 0.0, "stake_units": 0.0}
    )
    for r in qualified:
        key = (r.get("stat_family") or "?", r.get("side") or "?")
        st = r.get("grade_status")
        if st == "win":
            losses_by_arch[key]["wins"] += 1
        elif st == "loss":
            losses_by_arch[key]["losses"] += 1
        elif st == "push":
            losses_by_arch[key]["pushes"] += 1
        losses_by_arch[key]["profit_units"] += float(
            r.get("profit_units") or 0.0
        )
        losses_by_arch[key]["stake_units"] += float(
            r.get("stake_units") or 0.0
        )
    arch_rows = []
    for (fam, side), s in losses_by_arch.items():
        decided = s["wins"] + s["losses"]
        arch_rows.append({
            "stat_family": fam,
            "side": side,
            "wins": s["wins"],
            "losses": s["losses"],
            "pushes": s["pushes"],
            "decided": decided,
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
    top_losing = arch_rows[:10]

    return {
        "accuracy_mode": accuracy_mode,
        "sh_candidates": len(candidates),
        "sh_qualified": len(qualified),
        "overall": _grade_metrics(qualified),
        "by_bucket": bucket_perf,
        "top_losing_archetypes": top_losing,
    }


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print("=== SH Accuracy Test — one-sided bypass ===")
    print(f"  snapshot       = {SNAPSHOT}")
    print(f"  tier           = {TIER}")
    print(f"  baseline_id    = {BASELINE_ID}")
    print(f"  accuracy_id    = {ACCURACY_ID}")
    print(f"  pipeline_ver   = {PIPELINE_VERSION}")

    # ── Run baseline (production policy) ───────────────────────────
    t0 = datetime.now(timezone.utc)
    baseline = await run_pipeline(
        db, sport=SPORT, mode="historical",
        snapshot_time=SNAPSHOT,
        output_namespace="test",
        test_id=BASELINE_ID,
        tier=TIER,
        notes="SH accuracy test — BASELINE (production policy)",
        allow_one_sided_for_accuracy_test=False,
    )
    t_baseline = (datetime.now(timezone.utc) - t0).total_seconds()
    print(f"\n[baseline] done in {t_baseline:.1f}s — "
          f"scanned={baseline['rows_scanned']} "
          f"qualified={baseline['rows_qualified']} "
          f"cards={baseline['cards_displayed']} "
          f"elig_rej={baseline['eligibility_rejects']} "
          f"bypass_total={baseline.get('accuracy_test_bypass_total', 0)}")

    # ── Run accuracy test (one-sided allowed + SH tp_gate to 50) ──
    t0 = datetime.now(timezone.utc)
    accuracy = await run_pipeline(
        db, sport=SPORT, mode="historical",
        snapshot_time=SNAPSHOT,
        output_namespace="test",
        test_id=ACCURACY_ID,
        tier=TIER,
        notes=("SH accuracy test — ACCURACY MODE "
               "(one-sided allowed; sh_tp_gate_min=50)"),
        allow_one_sided_for_accuracy_test=True,
        sh_tp_gate_min_override=SH_TP_GATE_MIN_OVERRIDE,
    )
    t_accuracy = (datetime.now(timezone.utc) - t0).total_seconds()
    print(f"[accuracy] done in {t_accuracy:.1f}s — "
          f"scanned={accuracy['rows_scanned']} "
          f"qualified={accuracy['rows_qualified']} "
          f"cards={accuracy['cards_displayed']} "
          f"elig_rej={accuracy['eligibility_rejects']} "
          f"bypass_total={accuracy.get('accuracy_test_bypass_total', 0)} "
          f"(tp_source={accuracy.get('accuracy_test_bypass_tp_source_gate', 0)}, "
          f"market_structure={accuracy.get('accuracy_test_bypass_market_structure_gate', 0)}) "
          f"tp_gate_overrides={accuracy.get('tp_gate_override_count', 0)} "
          f"(sh_tp_gate_min={accuracy.get('sh_tp_gate_min_override')})")

    # ── Load both serials' outputs and grade ───────────────────────
    baseline_rows = await _load_outputs(db, baseline["serial"])
    accuracy_rows = await _load_outputs(db, accuracy["serial"])
    baseline_card_n = await _count_cards(db, baseline["serial"])
    accuracy_card_n = await _count_cards(db, accuracy["serial"])
    print(f"\n[loaded] baseline rows={len(baseline_rows)} "
          f"cards={baseline_card_n}; "
          f"accuracy rows={len(accuracy_rows)} cards={accuracy_card_n}")

    baseline_breakdown = _build_breakdown(
        baseline_rows, accuracy_mode=False)
    accuracy_breakdown = _build_breakdown(
        accuracy_rows, accuracy_mode=True)

    # ── Console summary ───────────────────────────────────────────
    def _print_block(label, brk, run_summary, n_cards):
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  serial            = {run_summary['serial']}")
        print(f"  sh_candidates     = {brk['sh_candidates']}")
        print(f"  sh_qualified      = {brk['sh_qualified']}")
        print(f"  cards_displayed   = {n_cards}")
        ovr = brk["overall"]
        print(f"  W/L/P             = "
              f"{ovr['wins']}/{ovr['losses']}/{ovr['pushes']} "
              f"(ungraded={ovr['ungraded']})")
        print(f"  hit_rate_pct      = {ovr['hit_rate_pct']}")
        print(f"  roi_pct           = {ovr['roi_pct']}")
        print(f"  profit / stake    = "
              f"{ovr['profit_units']} / {ovr['stake_units']}")
        print("\n  Per-bucket breakdown:")
        for b in ("devig", "one_sided",
                  "one_sided_std", "one_sided_alt"):
            bb = brk["by_bucket"][b]
            print(f"    {b:<18s} "
                  f"cand={bb['candidates']:>4d} "
                  f"qual={bb['qualified']:>3d} "
                  f"W/L/P={bb['wins']}/{bb['losses']}/{bb['pushes']} "
                  f"HR={bb['hit_rate_pct']} "
                  f"ROI={bb['roi_pct']}")
        print("\n  Top losing archetypes:")
        for a in brk["top_losing_archetypes"][:5]:
            print(f"    {a['stat_family']:<22s} {a['side']:<6s} "
                  f"L={a['losses']} W={a['wins']} P={a['pushes']} "
                  f"HR={a['hit_rate_pct']} ROI={a['roi_pct']} "
                  f"profit={a['profit_units']}")

    _print_block("BASELINE (production policy)",
                 baseline_breakdown, baseline, baseline_card_n)
    _print_block("ACCURACY (one-sided allowed)",
                 accuracy_breakdown, accuracy, accuracy_card_n)

    # ── Delta table ───────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  DELTA (accuracy − baseline)")
    print(f"{'='*60}")
    print(f"  sh_candidates  : {baseline_breakdown['sh_candidates']} "
          f"→ {accuracy_breakdown['sh_candidates']} "
          f"(Δ={accuracy_breakdown['sh_candidates'] - baseline_breakdown['sh_candidates']})")
    print(f"  sh_qualified   : {baseline_breakdown['sh_qualified']} "
          f"→ {accuracy_breakdown['sh_qualified']} "
          f"(Δ={accuracy_breakdown['sh_qualified'] - baseline_breakdown['sh_qualified']})")
    print(f"  cards          : {baseline_card_n} → {accuracy_card_n} "
          f"(Δ={accuracy_card_n - baseline_card_n})")

    # ── Persist artifact ──────────────────────────────────────────
    payload = {
        "audit_kind": "sh_accuracy_test_one_sided",
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "snapshot": SNAPSHOT,
        "tier": TIER,
        "pipeline_version": PIPELINE_VERSION,
        "elapsed_s_baseline": round(t_baseline, 2),
        "elapsed_s_accuracy": round(t_accuracy, 2),
        "baseline": {
            "serial": baseline["serial"],
            "run_summary": {
                k: baseline.get(k) for k in (
                    "rows_scanned", "rows_qualified",
                    "eligibility_rejects", "cards_displayed",
                    "wins", "losses", "pushes", "ungraded",
                    "hit_rate_pct", "roi_pct",
                    "profit_units", "stake_units",
                    "accuracy_test_mode_active",
                    "accuracy_test_bypass_total",
                    "accuracy_test_bypass_tp_source_gate",
                    "accuracy_test_bypass_market_structure_gate",
                )
            },
            "cards_in_db": baseline_card_n,
            "breakdown": baseline_breakdown,
        },
        "accuracy": {
            "serial": accuracy["serial"],
            "sh_tp_gate_min_override_applied": SH_TP_GATE_MIN_OVERRIDE,
            "run_summary": {
                k: accuracy.get(k) for k in (
                    "rows_scanned", "rows_qualified",
                    "eligibility_rejects", "cards_displayed",
                    "wins", "losses", "pushes", "ungraded",
                    "hit_rate_pct", "roi_pct",
                    "profit_units", "stake_units",
                    "accuracy_test_mode_active",
                    "accuracy_test_bypass_total",
                    "accuracy_test_bypass_tp_source_gate",
                    "accuracy_test_bypass_market_structure_gate",
                    "sh_tp_gate_min_override",
                    "tp_gate_override_count",
                )
            },
            "cards_in_db": accuracy_card_n,
            "breakdown": accuracy_breakdown,
        },
        "deltas": {
            "sh_candidates": (accuracy_breakdown["sh_candidates"]
                              - baseline_breakdown["sh_candidates"]),
            "sh_qualified":  (accuracy_breakdown["sh_qualified"]
                              - baseline_breakdown["sh_qualified"]),
            "cards":         accuracy_card_n - baseline_card_n,
        },
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[artifact] wrote {ARTIFACT_PATH}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
