"""Top-50 OVER + Top-50 UNDER MLB Front Lines audit (gates disabled mode).

Pulls all FL-routed MLB props for a given `version_tag`, sorts by
`edge_pct DESC`, splits OVER and UNDER, and prints Top 50 of each.

In addition, fetches the **10 picks currently in the live FL tier**
(via `services.board.reader.get_board`, sort=vision_score DESC, capped
at 10 per side) and marks each Top-50 entry with `★` if it is one of
the props currently surfacing on the production board.

For each pick it evaluates the FROZEN pre-audit FL thresholds
(`_MLB_FRONT_LINES`) and prints ✅/❌ per gate, plus an overall
"would-pass" indicator showing which props would survive if FL gates
were re-enabled today.

Usage:
    python -m scripts.mlb_top50_front_lines_audit            # default audit_tag = final-mlb-rt
    python -m scripts.mlb_top50_front_lines_audit final-mlb-rt-rt
"""
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from services.board.reader import get_board
from services.scoring.gates.thresholds import _MLB_FRONT_LINES

# `final-mlb-rt` is the live production tag (what users see on the
# board today). Override via CLI arg if you need to audit a different
# version_tag.
AUDIT_VERSION = sys.argv[1] if len(sys.argv) > 1 else "final-mlb-rt"
LIVE_VERSION = "final-mlb-rt"


def _num(v):
    return float(v) if isinstance(v, (int, float)) else float("-inf")


def _eval_frozen_fl(pick: dict, side: str) -> dict:
    family = pick.get("stat_family") or "_default"
    cfg = _MLB_FRONT_LINES.get(family, _MLB_FRONT_LINES["_default"])

    cv = pick.get("cv")
    edge = pick.get("edge_pct")
    tp = pick.get("tp")
    hr = pick.get("hit_rate_over") if side == "OVER" else pick.get("hit_rate_under")

    def cmp(val, thresh, op):
        if val is None or not isinstance(val, (int, float)):
            return {"passed": None, "value": val, "thresh": thresh}
        ok = (val <= thresh) if op == "<=" else (val >= thresh)
        return {"passed": bool(ok), "value": val, "thresh": thresh}

    gates = {
        "cv":   cmp(cv,   cfg["cv_max"],   "<="),
        "hr":   cmp(hr,   cfg["hr_min"],   ">="),
        "edge": cmp(edge, cfg["edge_min"], ">="),
        "tp":   cmp(tp,   cfg["tp_min"],   ">="),
    }
    overall = all(g["passed"] is True for g in gates.values())
    return {"gates": gates, "overall_pass": overall, "thresholds": cfg}


def _fail_list(eval_result: dict) -> str:
    fails = [g for g, x in eval_result["gates"].items() if x["passed"] is False]
    return ",".join(fails) if fails else "—"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # ---- Live board pull (what's currently surfacing) -------------------
    # `get_board` returns up to 10 deduped picks sorted by vision_score
    # DESC. The MLB capacity is 10 per tier (services/board/adapters/
    # base.py:capacity_for_tier). We split by recommendation post-hoc.
    live_board = await get_board(db, sport="mlb", tier="front_lines", limit=10)
    live_keys_over = {p.get("canonical_key") for p in live_board
                       if (p.get("recommendation") or "").upper() == "OVER"
                       and p.get("canonical_key")}
    live_keys_under = {p.get("canonical_key") for p in live_board
                        if (p.get("recommendation") or "").upper() == "UNDER"
                        and p.get("canonical_key")}

    # ---- Audit pool pull -------------------------------------------------
    raw = await db.mlb_prop_scores.find(
        {"version_tag": AUDIT_VERSION, "routed_tier": "front_lines"},
        {"_id": 0},
    ).to_list(length=20000)

    overs = [r for r in raw if (r.get("recommendation") or "").upper() == "OVER"]
    unders = [r for r in raw if (r.get("recommendation") or "").upper() == "UNDER"]

    sort_mode = (os.environ.get("FL_AUDIT_SORT") or "edge").lower()

    def _sort_key(r):
        if sort_mode == "hr":
            side = (r.get("recommendation") or "").upper()
            hr = (r.get("hit_rate_under") if side == "UNDER"
                  else r.get("hit_rate_over"))
            # secondary: edge_pct DESC; tertiary: canonical_key
            return (-_num(hr), -_num(r.get("edge_pct")),
                    r.get("canonical_key") or "")
        # default: edge_pct DESC
        return (-_num(r.get("edge_pct")), -_num(r.get("vision_score")),
                r.get("canonical_key") or "")

    overs.sort(key=_sort_key)
    unders.sort(key=_sort_key)

    over_top = overs[:50]
    under_top = unders[:50]

    def _row(rank, p, side, in_tier_set):
        ev = _eval_frozen_fl(p, side)
        proj = p.get("model_projection") or p.get("vk2_projection")
        ck = p.get("canonical_key")
        return {
            "rank": rank,
            "in_tier": ck in in_tier_set,
            "player": p.get("player_name"),
            "team": p.get("team"),
            "opp": p.get("opponent_team"),
            "stat": p.get("stat_type"),
            "stat_family": p.get("stat_family"),
            "line": p.get("line"),
            "side": side,
            "is_alt": bool(p.get("is_alternate_market")),
            "ref_odds": p.get("tier_reference_odds"),
            "ref_book": p.get("tier_reference_book"),
            "edge_pct": p.get("edge_pct"),
            "tp": p.get("tp"),
            "p_model_pct": p.get("p_model_pct"),
            "cv": p.get("cv"),
            "hit_rate": (p.get("hit_rate_over") if side == "OVER"
                         else p.get("hit_rate_under")),
            "l20_n": p.get("hit_rate_sample_size"),
            "vision": p.get("vision_score"),
            "projection": round(float(proj), 3) if isinstance(proj, (int, float)) else None,
            "model_sigma": p.get("model_sigma"),
            "frozen_fl_eval": ev,
            "would_pass_fl": "PASS" if ev["overall_pass"] else "FAIL",
            "failed_gates": _fail_list(ev),
        }

    over_rows = [_row(i + 1, p, "OVER", live_keys_over) for i, p in enumerate(over_top)]
    under_rows = [_row(i + 1, p, "UNDER", live_keys_under) for i, p in enumerate(under_top)]

    # ---- Live in-tier list (the actual 10 displayed) --------------------
    live_over_rows = []
    live_under_rows = []
    for i, p in enumerate(
        [x for x in live_board if (x.get("recommendation") or "").upper() == "OVER"], 1
    ):
        live_over_rows.append(_row(i, p, "OVER", live_keys_over))
    for i, p in enumerate(
        [x for x in live_board if (x.get("recommendation") or "").upper() == "UNDER"], 1
    ):
        live_under_rows.append(_row(i, p, "UNDER", live_keys_under))

    # ---- Fail-gate distribution across the FULL FL-routed pools ---------
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
    over_pass_count = sum(1 for p in overs if _eval_frozen_fl(p, "OVER")["overall_pass"])
    under_pass_count = sum(1 for p in unders if _eval_frozen_fl(p, "UNDER")["overall_pass"])

    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/mlb_top50_fl_audit_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "mlb",
        "audit_version": AUDIT_VERSION,
        "live_version": LIVE_VERSION,
        "filter": {"routed_tier": "front_lines"},
        "frozen_fl_thresholds": _MLB_FRONT_LINES,
        "totals": {
            "fl_routed_over": len(overs),
            "fl_routed_under": len(unders),
            "over_would_pass_frozen_fl": over_pass_count,
            "under_would_pass_frozen_fl": under_pass_count,
            "live_tier_over_count": len(live_over_rows),
            "live_tier_under_count": len(live_under_rows),
        },
        "fail_gate_distribution_full_pool": {
            "over": dict(full_over_fail),
            "under": dict(full_under_fail),
        },
        "live_tier_top_10_over": live_over_rows,
        "live_tier_top_10_under": live_under_rows,
        "top_50_over": over_rows,
        "top_50_under": under_rows,
    }
    with open(path, "w") as f:
        json.dump(payload, f, default=str, indent=2)

    # ---- Console rendering ---------------------------------------------
    def _print_table(title, rows, *, show_intier_marker=True):
        print()
        print(f"=== {title} ===")
        print(f"{'#':>2} {'★':<2} {'Pass':<5} {'Player':<22} "
              f"{'Stat':<22} {'L':>5} {'Alt':<3} {'RefOdds':<10} "
              f"{'Edge':>6} {'TP%':>6} {'CV':>6} {'HR':>5} {'L20n':>5} "
              f"{'Vis':>5} {'Proj':>7} {'Failed':<24}")
        print("-" * 165)
        for r in rows:
            star = "★" if (show_intier_marker and r["in_tier"]) else ""
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
            pass_marker = "✅" if r["would_pass_fl"] == "PASS" else "❌"
            print(f"{r['rank']:>2} {star:<2} {pass_marker:<5} "
                  f"{(r['player'] or '?')[:22]:<22} "
                  f"{(r['stat'] or '?')[:22]:<22} {ln:>5} {alt:<3} "
                  f"{ref:<10} {edge:>6} {tp:>6} {cv:>6} {hr:>5} {l20:>5} "
                  f"{vis:>5} {proj:>7} {r['failed_gates']:<24}")

    print(f"\nMLB Front Lines audit  (FL gates DISABLED in production)")
    print(f"  audit pool : version_tag = {AUDIT_VERSION}")
    print(f"  live board : version_tag = {LIVE_VERSION}")
    print(f"FL-routed audit pool: {len(overs)} OVER + {len(unders)} UNDER "
          f"= {len(overs) + len(unders)} total")
    print(f"Would-pass frozen FL thresholds: "
          f"{over_pass_count}/{len(overs)} OVER, "
          f"{under_pass_count}/{len(unders)} UNDER")
    print(f"Live FL tier currently displaying "
          f"{len(live_over_rows)} OVER + {len(live_under_rows)} UNDER "
          f"(★ marker on Top-50 lists below).")

    # 10-currently-in-tier sections
    _print_table(
        f"10 currently displayed in FL tier — OVER ({len(live_over_rows)})",
        live_over_rows, show_intier_marker=False,
    )
    _print_table(
        f"10 currently displayed in FL tier — UNDER ({len(live_under_rows)})",
        live_under_rows, show_intier_marker=False,
    )

    # Top 50 audit lists with ★ marker
    _print_table(
        f"Top 50 OVER (of {len(overs)} FL-routed OVERs in {AUDIT_VERSION})",
        over_rows,
    )
    _print_table(
        f"Top 50 UNDER (of {len(unders)} FL-routed UNDERs in {AUDIT_VERSION})",
        under_rows,
    )

    print()
    print("=== Frozen FL fail-gate distribution (full FL-routed pool) ===")
    print(f"OVER  pool ({len(overs)}):")
    for g, n in full_over_fail.most_common():
        print(f"  {n:>4}  {g}")
    print(f"UNDER pool ({len(unders)}):")
    for g, n in full_under_fail.most_common():
        print(f"  {n:>4}  {g}")

    print()
    print("Frozen FL thresholds (per stat_family):")
    for fam, cfg in _MLB_FRONT_LINES.items():
        print(f"  {fam:<22} cv≤{cfg['cv_max']:.2f}  hr≥{cfg['hr_min']:.0f}  "
              f"edge≥{cfg['edge_min']:.1f}  tp≥{cfg['tp_min']:.0f}")

    print()
    print(f"ARTIFACT: {path}")


if __name__ == "__main__":
    asyncio.run(main())
