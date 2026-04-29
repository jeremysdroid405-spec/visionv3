"""Top-50 OVER + Top-50 UNDER MLB Front Lines audit (gates disabled mode).

Pulls all FL-routed MLB props (currently every routed-FL prop lands in
`tier=front_lines` because gates are disabled per
`MLB_FRONT_LINES_GATES_DISABLED=True`), sorts by edge_pct DESC, splits
OVER and UNDER, takes top 50 of each.

For each pick it evaluates the FROZEN pre-audit FL thresholds
(`_MLB_FRONT_LINES`) and prints ✅/❌ per gate, plus an overall
"would-pass" indicator showing which props would survive if FL gates
were re-enabled today.

Output:
  - Console table (Top 50 OVER + Top 50 UNDER)
  - JSON snapshot at /app/backend/data/snapshots/mlb_top50_fl_audit_<ts>.json
"""
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from services.scoring.gates.thresholds import _MLB_FRONT_LINES

# `final-mlb-rt-rt` is the latest scoring run that includes both OVER and
# UNDER recommendations. Override via CLI arg if you need a different tag.
VERSION = sys.argv[1] if len(sys.argv) > 1 else "final-mlb-rt-rt"


def _num(v):
    return float(v) if isinstance(v, (int, float)) else float("-inf")


def _eval_frozen_fl(pick: dict, side: str) -> dict:
    """Evaluate the frozen pre-audit FL thresholds against a pick.

    Returns dict with per-gate ✅/❌ and overall pass/fail.
    """
    family = pick.get("stat_family") or "_default"
    cfg = _MLB_FRONT_LINES.get(family, _MLB_FRONT_LINES["_default"])

    cv = pick.get("cv")
    edge = pick.get("edge_pct")
    tp = pick.get("tp")
    if side == "OVER":
        hr = pick.get("hit_rate_over")
    else:
        hr = pick.get("hit_rate_under")

    def cmp(name, val, thresh, op):
        if val is None or not isinstance(val, (int, float)):
            return {"passed": None, "value": val, "thresh": thresh}
        ok = (val <= thresh) if op == "<=" else (val >= thresh)
        return {"passed": bool(ok), "value": val, "thresh": thresh}

    gates = {
        "cv":   cmp("cv",       cv,   cfg["cv_max"],   "<="),
        "hr":   cmp("hit_rate", hr,   cfg["hr_min"],   ">="),
        "edge": cmp("edge_pct", edge, cfg["edge_min"], ">="),
        "tp":   cmp("tp",       tp,   cfg["tp_min"],   ">="),
    }
    overall = all(g["passed"] is True for g in gates.values())
    return {"gates": gates, "overall_pass": overall, "thresholds": cfg}


def _fail_list(eval_result: dict) -> str:
    fails = [g for g, x in eval_result["gates"].items() if x["passed"] is False]
    return ",".join(fails) if fails else "—"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # Pull all FL-routed MLB props. With gates disabled every
    # FL-routed prop lands in tier="front_lines", so filter on tier.
    raw = await db.mlb_prop_scores.find(
        {"version_tag": VERSION, "routed_tier": "front_lines"},
        {"_id": 0},
    ).to_list(length=20000)

    # Split OVER / UNDER pools, sort by edge_pct DESC.
    overs = [r for r in raw if (r.get("recommendation") or "").upper() == "OVER"]
    unders = [r for r in raw if (r.get("recommendation") or "").upper() == "UNDER"]

    overs.sort(key=lambda r: (-_num(r.get("edge_pct")), -_num(r.get("vision_score")),
                               r.get("canonical_key") or ""))
    unders.sort(key=lambda r: (-_num(r.get("edge_pct")), -_num(r.get("vision_score")),
                                r.get("canonical_key") or ""))

    over_top = overs[:50]
    under_top = unders[:50]

    # Pull live odds context for the picks we display.
    keys = list({r["canonical_key"] for r in (over_top + under_top)
                 if r.get("canonical_key")})
    odds_by_key = {}
    if keys:
        cursor = db.mlb_live_props.find(
            {"canonical_key": {"$in": keys}},
            {"_id": 0, "canonical_key": 1, "is_starter": 1,
             "lineup_status": 1, "expected_pa": 1, "expected_outs_pitched": 1,
             "is_alternate_market": 1},
        )
        for d in await cursor.to_list(length=5000):
            odds_by_key[d["canonical_key"]] = d

    def _row(rank, p, side):
        ev = _eval_frozen_fl(p, side)
        ox = odds_by_key.get(p.get("canonical_key"), {})
        proj = p.get("model_projection") or p.get("vk2_projection")
        return {
            "rank": rank,
            "player": p.get("player_name"),
            "team": p.get("team"),
            "opp": p.get("opponent_team"),
            "stat": p.get("stat_type"),
            "stat_family": p.get("stat_family"),
            "line": p.get("line"),
            "side": side,
            "is_alt": bool(p.get("is_alternate_market") or ox.get("is_alternate_market")),
            "ref_odds": p.get("tier_reference_odds"),
            "ref_book": p.get("tier_reference_book"),
            "edge_pct": p.get("edge_pct"),
            "tp": p.get("tp"),
            "fair_prob": p.get("fair_prob"),
            "p_model_pct": p.get("p_model_pct"),
            "cv": p.get("cv"),
            "hit_rate": (p.get("hit_rate_over") if side == "OVER"
                         else p.get("hit_rate_under")),
            "l20_n": p.get("hit_rate_sample_size"),
            "vision": p.get("vision_score"),
            "projection": round(float(proj), 3) if isinstance(proj, (int, float)) else None,
            "model_sigma": p.get("model_sigma"),
            "book_count": p.get("book_count"),
            "frozen_fl_eval": ev,
            "would_pass_fl": "✅" if ev["overall_pass"] else "❌",
            "failed_gates": _fail_list(ev),
            "context": {
                "is_starter": ox.get("is_starter"),
                "lineup_status": ox.get("lineup_status"),
                "expected_pa": ox.get("expected_pa"),
                "expected_outs_pitched": ox.get("expected_outs_pitched"),
            },
        }

    over_rows = [_row(i + 1, p, "OVER") for i, p in enumerate(over_top)]
    under_rows = [_row(i + 1, p, "UNDER") for i, p in enumerate(under_top)]

    # Aggregate fail-gate distribution across the FULL FL-routed pools
    full_over_fail = Counter()
    full_under_fail = Counter()
    for p in overs:
        ev = _eval_frozen_fl(p, "OVER")
        for g, x in ev["gates"].items():
            if x["passed"] is False:
                full_over_fail[g] += 1
    for p in unders:
        ev = _eval_frozen_fl(p, "UNDER")
        for g, x in ev["gates"].items():
            if x["passed"] is False:
                full_under_fail[g] += 1

    over_pass_count = sum(1 for p in overs
                          if _eval_frozen_fl(p, "OVER")["overall_pass"])
    under_pass_count = sum(1 for p in unders
                           if _eval_frozen_fl(p, "UNDER")["overall_pass"])

    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/mlb_top50_fl_audit_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "mlb",
        "version_tag": VERSION,
        "filter": {"routed_tier": "front_lines"},
        "sort": ["edge_pct DESC", "vision_score DESC", "canonical_key ASC"],
        "frozen_fl_thresholds": _MLB_FRONT_LINES,
        "totals": {
            "fl_routed_over": len(overs),
            "fl_routed_under": len(unders),
            "over_would_pass_frozen_fl": over_pass_count,
            "under_would_pass_frozen_fl": under_pass_count,
        },
        "fail_gate_distribution_full_pool": {
            "over": dict(full_over_fail),
            "under": dict(full_under_fail),
        },
        "top_50_over": over_rows,
        "top_50_under": under_rows,
    }
    with open(path, "w") as f:
        json.dump(payload, f, default=str, indent=2)

    # ---- Console rendering -----------------------------------------------
    def _print_table(title, rows):
        print()
        print(f"=== {title} ===")
        print(f"{'#':>2} {'Pass':<5} {'Player':<22} {'TM/OPP':<10} {'Stat':<22} "
              f"{'L':>5} {'Alt':<3} {'RefOdds':<10} {'Edge':>6} {'TP%':>6} "
              f"{'CV':>6} {'HR':>5} {'L20n':>5} {'Vis':>5} {'Proj':>7} {'Failed':<24}")
        print("-" * 175)
        for r in rows:
            tm_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
            ref = f"{r['ref_odds']}" if r["ref_odds"] is not None else "—"
            alt = "Y" if r["is_alt"] else ""
            cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
            hr = f"{r['hit_rate']:.0f}" if isinstance(r["hit_rate"], (int, float)) else "—"
            edge = f"{r['edge_pct']:>+5.1f}" if isinstance(r["edge_pct"], (int, float)) else "—"
            tp = f"{r['tp']:>5.1f}" if isinstance(r["tp"], (int, float)) else "—"
            vis = f"{r['vision']:.1f}" if isinstance(r["vision"], (int, float)) else "—"
            proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
            ln = f"{r['line']}" if r["line"] is not None else "—"
            l20 = f"{r['l20_n']}" if r["l20_n"] is not None else "—"
            print(f"{r['rank']:>2} {r['would_pass_fl']:<5} "
                  f"{(r['player'] or '?')[:22]:<22} {tm_opp:<10} "
                  f"{(r['stat'] or '?')[:22]:<22} {ln:>5} {alt:<3} "
                  f"{ref:<10} {edge:>6} {tp:>6} {cv:>6} {hr:>5} {l20:>5} "
                  f"{vis:>5} {proj:>7} {r['failed_gates']:<24}")

    print(f"\nMLB Front Lines audit (FL gates DISABLED in production)")
    print(f"version_tag = {VERSION}")
    print(f"FL-routed pool: {len(overs)} OVER + {len(unders)} UNDER "
          f"= {len(overs) + len(unders)} total")
    print(f"Would-pass frozen FL thresholds: "
          f"{over_pass_count}/{len(overs)} OVER, "
          f"{under_pass_count}/{len(unders)} UNDER")

    _print_table(f"Top 50 OVER (of {len(overs)} FL-routed OVERs)", over_rows)
    _print_table(f"Top 50 UNDER (of {len(unders)} FL-routed UNDERs)", under_rows)

    print()
    print("=== Frozen FL fail-gate distribution (full FL-routed pool) ===")
    print(f"OVER  pool ({len(overs)}):")
    for g, n in full_over_fail.most_common():
        print(f"  {n:>4}  {g}")
    print(f"UNDER pool ({len(unders)}):")
    for g, n in full_under_fail.most_common():
        print(f"  {n:>4}  {g}")

    print()
    print("Frozen FL thresholds applied (per stat_family):")
    for fam, cfg in _MLB_FRONT_LINES.items():
        print(f"  {fam:<22} cv≤{cfg['cv_max']:.2f}  hr≥{cfg['hr_min']:.0f}  "
              f"edge≥{cfg['edge_min']:.1f}  tp≥{cfg['tp_min']:.0f}")

    print()
    print(f"ARTIFACT: {path}")


if __name__ == "__main__":
    asyncio.run(main())
