"""WZ OVER Slate-Health Monitor — multi-slate observability snapshot.

Per 2026-05-09 directive: collect 3 successive slates of WZ OVER metrics
WITHOUT tuning. Re-run this script on each slate refresh; it appends a
new row to `/app/audit_reports/wz_slate_monitor.jsonl` and prints a
human-readable summary.

Metrics captured (per slate, per spec):
    1. WZ qualified count
    2. WZ rejects by failed gate
    3. HR ≥ 70 AND CV > 0.75 candidates (potential ladder rescues)
    4. HR ∈ [50, 55) candidates (lifted by HR-floor drop)
    5. Ladder rescues observed (gate_eval rule = war_zone:hr_expansion_*)
    6. Top 20 added props (vs baseline snapshot, if available)

If `WZ qualified` stays under 8–10 across all 3 slates, this is the
trigger to revisit remaining bottlenecks (per directive).

Usage:
    python3 backend/scripts/wz_slate_monitor.py [--label "slate-N"]
"""
import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

WZ_LO = 150
LOG_PATH = Path("/app/audit_reports/wz_slate_monitor.jsonl")
BASELINE_PATH = Path("/app/audit_reports/wz_slate_monitor_baseline.json")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def collect_metrics(db) -> dict:
    """Pull the WZ OVER observability metrics for the current slate."""
    docs = await db.nba_prop_scores.find(
        {"version_tag": "final-nba-rt",
         "tier_reference_odds": {"$gte": WZ_LO},
         "recommendation": "OVER"},
        {"_id": 0, "canonical_key": 1, "player_name": 1, "stat_type": 1,
         "line": 1, "tier": 1, "tier_reason": 1, "tier_reference_odds": 1,
         "hit_rate_over": 1, "cv": 1, "vision_score": 1, "vision_score_v2": 1,
         "edge_vs_fair": 1, "model_projection": 1, "vk2_projection": 1,
         "gate_eval.failed_gates": 1, "gate_eval.gate_details": 1,
         "tier_gate_results": 1, "book_count": 1}
    ).to_list(20000)

    qualified = [d for d in docs if d.get("tier") == "war_zone"]
    rejected = [d for d in docs if d.get("tier") != "war_zone"]

    # Failed-gate set distribution
    failed_gate_dist = Counter()
    for d in rejected:
        fg = (d.get("gate_eval") or {}).get("failed_gates") or []
        failed_gate_dist[",".join(sorted(fg)) or "no_failed_gates_set"] += 1

    # Reason-code distribution
    reason_dist = Counter(d.get("tier_reason") or "—" for d in rejected)

    # HR ≥ 70 + CV > 0.75 candidates among rejects (would benefit from ladder)
    high_hr_high_cv = []
    for d in rejected:
        hr = _f(d.get("hit_rate_over"))
        cv = _f(d.get("cv"))
        if hr is not None and cv is not None and hr >= 70.0 and cv > 0.75:
            high_hr_high_cv.append(d)

    # HR ∈ [50, 55) candidates — newly eligible after the floor drop
    hr_50_55 = []
    for d in docs:
        hr = _f(d.get("hit_rate_over"))
        if hr is not None and 50.0 <= hr < 55.0:
            hr_50_55.append(d)

    # Ladder rescues actually observed (gate_eval notes contain
    # `war_zone_override:hr_expansion`).
    ladder_rescues = []
    for d in qualified:
        details = (d.get("gate_eval") or {}).get("gate_details") or {}
        cv_detail = details.get("cv_gate") or {}
        note = cv_detail.get("note") or ""
        if "war_zone_override:hr_expansion" in note:
            ladder_rescues.append({
                "canonical_key": d.get("canonical_key"),
                "player": d.get("player_name"),
                "stat": d.get("stat_type"),
                "line": d.get("line"),
                "hr": _f(d.get("hit_rate_over")),
                "cv": _f(d.get("cv")),
                "edge_pct": (_f(d.get("edge_vs_fair")) or 0) * 100,
                "ref_odds": d.get("tier_reference_odds"),
                "rule_note": note,
            })

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "wz_routed": len(docs),
        "wz_qualified": len(qualified),
        "wz_rejected": len(rejected),
        "failed_gate_distribution": dict(failed_gate_dist.most_common()),
        "reason_distribution": dict(reason_dist.most_common()),
        "high_hr_high_cv_candidate_count": len(high_hr_high_cv),
        "hr_50_55_candidate_count": len(hr_50_55),
        "ladder_rescues": ladder_rescues,
        "qualified_keys": [d.get("canonical_key") for d in qualified],
        "qualified_summary": [
            {"player": d.get("player_name"),
             "stat": d.get("stat_type"),
             "line": d.get("line"),
             "hr": _f(d.get("hit_rate_over")),
             "cv": _f(d.get("cv")),
             "vs": _f(d.get("vision_score")),
             "vs_v2": _f(d.get("vision_score_v2")),
             "ref_odds": d.get("tier_reference_odds")}
            for d in qualified
        ],
    }


def _load_baseline():
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text())
    return None


def _save_baseline(metrics: dict):
    BASELINE_PATH.write_text(json.dumps(metrics, indent=2, default=str))


def _diff_added_props(prev_keys: set, curr_qualified: list, top_n: int = 20):
    """Return up to `top_n` props newly qualifying since the previous slate."""
    added = [d for d in curr_qualified if d["canonical_key"] not in prev_keys]
    return added[:top_n]


def _print_summary(label: str, m: dict, prev: dict | None):
    print(f"\n=== WZ SLATE MONITOR — {label} ({m['ts']}) ===")
    print(f"  WZ-routed OVER : {m['wz_routed']}")
    print(f"  WZ qualified   : {m['wz_qualified']}")
    print(f"  WZ rejected    : {m['wz_rejected']}")
    print("\nFailed-gate distribution:")
    for k, v in list(m["failed_gate_distribution"].items())[:10]:
        print(f"  {k:60} : {v}")
    print(f"\nHR ≥ 70 AND CV > 0.75 candidates : {m['high_hr_high_cv_candidate_count']}")
    print(f"HR ∈ [50, 55) candidates         : {m['hr_50_55_candidate_count']}")
    print(f"Ladder rescues observed          : {len(m['ladder_rescues'])}")
    if m["ladder_rescues"]:
        for r in m["ladder_rescues"]:
            print(f"   - {r['player']} {r['stat']} L{r['line']} HR={r['hr']} CV={r['cv']} edge={r['edge_pct']:.2f} odds={r['ref_odds']}")

    if prev is not None:
        prev_keys = set(prev.get("qualified_keys", []))
        added = _diff_added_props(prev_keys, m["qualified_summary"], top_n=20)
        print(f"\nTop {min(20,len(added))} ADDED props (vs previous slate):")
        for d in added:
            print(f"   + {d['player']} {d['stat']} L{d['line']} HR={d['hr']} CV={d['cv']} VS={d['vs']} odds={d['ref_odds']}")
    else:
        print("\n(no previous slate snapshot to diff against — this run is the baseline)")

    if m["wz_qualified"] < 8:
        print(f"\nNOTE: WZ qualified ({m['wz_qualified']}) is below the 8–10 review threshold.")
        print("      Per directive: do NOT tune yet — accumulate at least 3 normal slates first.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=None,
                        help="optional label for the slate (default: ISO timestamp)")
    args = parser.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    metrics = await collect_metrics(db)
    label = args.label or metrics["ts"]
    metrics["label"] = label

    prev = _load_baseline()

    # Append to history log
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(metrics, default=str) + "\n")

    # Update baseline (latest) so next run knows the prev qualified set
    _save_baseline(metrics)

    _print_summary(label, metrics, prev)


if __name__ == "__main__":
    asyncio.run(main())
