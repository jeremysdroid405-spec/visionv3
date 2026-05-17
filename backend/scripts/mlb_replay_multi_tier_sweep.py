"""CLI: Layer-4 multi-tier sweep (SH / FL / WZ on the same replay universe)."""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_engine import SCORING_CONFIG_VERSION
from services.replay.mlb_replay_multi_tier_eval import (
    TIER_CONFIGS, run_multi_tier_for_date,
)
from services.replay.mlb_replay_gate_eval import DEFAULT_MEM_LIMIT_MB

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s %(message)s")


def _fmt_pct(x):
    return f"{x:+.1f}%" if x is not None else "  --  "


def _fmt_hr(x):
    return f"{x:.1f}%" if x is not None else "  --  "


def _fmt_num(x, w=6, p=2):
    if x is None: return "  --  "
    return f"{x:{w}.{p}f}"


def _summary_row(label: str, s: Dict[str, Any]):
    n = s.get("total", 0)
    if n == 0:
        print(f"  {label:<28} (none)"); return
    print(f"  {label:<28} n={n:>5}  W/L/P/U={s['wins']}/{s['losses']}/"
          f"{s['pushes']}/{s['ungraded']}  HR={_fmt_hr(s['hit_rate_pct']):>7}  "
          f"ROI={_fmt_pct(s['roi_pct']):>8}  u={s['profit_units']:+.2f}")


def _print_tier_block(tier: str, td: Dict[str, Any]):
    print(f"\n━━━━━━━━━━ TIER: {tier.upper()}  ({td['gate_config_version']}) ━━━━━━━━━━")
    print(f"  gate_pass / gate_fail        {td['gate_pass']:,} / {td['gate_fail']:,}")
    print(f"  failed_gate_breakdown        {td['failed_gate_breakdown']}")
    o = td["overall"]
    print(f"  qualified picks              {o['total']:,}")
    print(f"  W/L/P/Ungraded               {o['wins']}/{o['losses']}/"
          f"{o['pushes']}/{o['ungraded']}")
    print(f"  hit rate                     {_fmt_hr(o['hit_rate_pct'])}")
    print(f"  profit / stake (units)       {o['profit_units']:+.2f} / "
          f"{o['stake_units']:.2f}")
    print(f"  ROI                          {_fmt_pct(o['roi_pct'])}")
    print(f"  avg odds  / median odds      {_fmt_num(o['avg_odds'], 7, 1)} / "
          f"{_fmt_num(o['median_odds'], 7, 1)}")
    print(f"  avg edge                     {_fmt_num(o['avg_edge'], 7, 4)}"
          f"  ({(o['avg_edge'] or 0)*100:+.2f}pp)")
    print(f"  avg CV                       {_fmt_num(o['avg_cv'], 7, 4)}")
    print(f"  avg (μ−line) for OVER /      {_fmt_num(o['avg_mu_minus_line'], 7, 3)}")
    print(f"      (line−μ) for UNDER")
    print(f"\n  --- by_market_type ---")
    for k, v in td["by_market_type"].items():  _summary_row(k, v)
    print(f"\n  --- by_stat_family ---")
    for k, v in sorted(td["by_stat_family"].items(),
                       key=lambda kv: -(kv[1].get("total") or 0)):
        _summary_row(k, v)
    print(f"\n  --- by_edge_bucket ---")
    for k in ["edge_05_10", "edge_10_20", "edge_20_30", "edge_30p"]:
        v = td["by_edge_bucket"].get(k)
        if v: _summary_row(k, v)
    print(f"\n  --- by_odds_bucket ---")
    for k in ["odds_lt_-200", "odds_-200_-100", "odds_-100_-0",
              "odds_+0_+150", "odds_+150_+300", "odds_+300p"]:
        v = td["by_odds_bucket"].get(k)
        if v: _summary_row(k, v)
    print(f"\n  --- by_cv_bucket ---")
    for k in ["cv_lt50", "cv_50_75", "cv_75_100", "cv_100_110"]:
        v = td["by_cv_bucket"].get(k)
        if v: _summary_row(k, v)
    print(f"\n  --- by_hr_bucket (L20) ---")
    for k in ["hr_70_75", "hr_75_85", "hr_85_95", "hr_95p"]:
        v = td["by_hr_bucket"].get(k)
        if v: _summary_row(k, v)
    print(f"\n  --- by_book ---")
    for k, v in sorted(td["by_book"].items(),
                       key=lambda kv: -(kv[1].get("total") or 0)):
        _summary_row(k, v)


def _print_comparison_table(out: Dict[str, Any]):
    print("\n━━━━━━━━━━━━━━━━━━━━ TIER COMPARISON ━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'Tier':<14} {'Picks':>6}  {'HR':>7}  {'ROI':>8}  "
          f"{'Avg Odds':>9}  {'Avg Edge':>9}  {'Avg CV':>7}  "
          f"{'μ-Line':>7}  {'Profit u':>9}")
    print(f"  {'-'*14} {'-'*6}  {'-'*7}  {'-'*8}  {'-'*9}  {'-'*9}  "
          f"{'-'*7}  {'-'*7}  {'-'*9}")
    for tier in ("safe_haven", "front_lines", "war_zone"):
        td = out["tiers"][tier]; o = td["overall"]
        if o["total"] == 0:
            print(f"  {tier:<14} {o['total']:>6}  (no qualified picks)")
            continue
        print(f"  {tier:<14} {o['total']:>6}  "
              f"{_fmt_hr(o['hit_rate_pct']):>7}  "
              f"{_fmt_pct(o['roi_pct']):>8}  "
              f"{(o['avg_odds'] or 0):>9.1f}  "
              f"{((o['avg_edge'] or 0)*100):>+8.2f}%  "
              f"{(o['avg_cv'] or 0):>7.4f}  "
              f"{(o['avg_mu_minus_line'] or 0):>+7.3f}  "
              f"{o['profit_units']:>+9.2f}")


def _print_overlap(out: Dict[str, Any]):
    ov = out["overlap"]
    sh, fl, wz = ov["sh_size"], ov["fl_size"], ov["wz_size"]
    print(f"\n━━━━━━━━━━━━━━━━━━━━ TIER OVERLAP ━━━━━━━━━━━━━━━━━━━━")
    print(f"  qualified set sizes          SH={sh}  FL={fl}  WZ={wz}")
    print(f"  Safe Haven ∩ Front Lines    {ov['sh_∩_fl']}")
    print(f"  Front Lines ∩ War Zone      {ov['fl_∩_wz']}")
    print(f"  Safe Haven ∩ War Zone       {ov['sh_∩_wz']}")
    print(f"  SH ∩ FL ∩ WZ                 {ov['sh_∩_fl_∩_wz']}")
    print(f"  Unique to SH only            {ov['safe_haven_only']}")
    print(f"  Unique to FL only            {ov['front_lines_only']}")
    print(f"  Unique to WZ only            {ov['war_zone_only']}")

    def pct(num, den): return f"{(num/den*100):.1f}%" if den else "  --"
    print(f"\n  Nested containment:")
    print(f"    SH ⊂ FL : {pct(ov['sh_∩_fl'], sh)} of Safe Haven also qualifies FL")
    print(f"    SH ⊂ WZ : {pct(ov['sh_∩_wz'], sh)} of Safe Haven also qualifies WZ")
    print(f"    FL ⊂ WZ : {pct(ov['fl_∩_wz'], fl)} of Front Lines also qualifies WZ")
    print(f"    FL ⊃ SH : {pct(ov['sh_∩_fl'], fl)} of Front Lines were in Safe Haven")
    print(f"    WZ ⊃ FL : {pct(ov['fl_∩_wz'], wz)} of War Zone were in Front Lines")
    print(f"    WZ ⊃ SH : {pct(ov['sh_∩_wz'], wz)} of War Zone were in Safe Haven")


async def _top_qualifiers(db, tier_version: str, game_date: str,
                          snapshot_iso: str, limit: int = 25) -> List[Dict]:
    cursor = db.mlb_replay_gate_results.find(
        {"game_date": game_date, "snapshot_iso": snapshot_iso,
         "gate_config_version": tier_version, "gate_pass": True},
        {"_id": 0, "player_name": 1, "production_family": 1, "stat_family": 1,
         "market": 1, "line": 1, "side": 1, "book": 1, "odds": 1,
         "projection_mu": 1, "edge": 1, "cv": 1,
         "hit_rate_l5": 1, "hit_rate_l10": 1, "hit_rate_l20": 1,
         "model_probability": 1, "grade_status": 1, "actual": 1,
         "profit_units": 1},
    ).sort([("hit_rate_l10", -1), ("hit_rate_l20", -1),
            ("hit_rate_l5", -1), ("edge", -1)]).limit(limit)
    return await cursor.to_list(limit)


def _print_top_picks(label: str, picks: List[Dict]):
    print(f"\n━━━━━━━━━━ TOP {len(picks)} QUALIFIERS — {label} ━━━━━━━━━━")
    if not picks:
        print("  (none)"); return
    print(f"  {'#':>2}  {'Player':<22} {'Stat':<12} {'L':>5}"
          f" {'Side':<5} {'Book':<14} {'Odds':>6} {'μ':>6} {'Edge':>7}"
          f" {'CV':>6} {'L5/L10/L20':>14} {'TP%':>6} {'Result':>9} {'+u':>6}")
    for i, p in enumerate(picks, 1):
        hrs = (f"{int(p.get('hit_rate_l5') or 0)}/"
               f"{int(p.get('hit_rate_l10') or 0)}/"
               f"{int(p.get('hit_rate_l20') or 0)}")
        actual = p.get("actual")
        result = p.get("grade_status") or "?"
        if actual is not None:
            result = f"{result}({actual:g})"
        print(f"  {i:>2}  {(p.get('player_name') or '?')[:22]:<22}"
              f" {p.get('production_family','')[:12]:<12}"
              f" {p['line']:>5.1f} {p['side']:<5} {p['book'][:14]:<14}"
              f" {int(p['odds']):>+6d} {p['projection_mu']:>6.2f}"
              f" {(p['edge']*100):>+6.2f}% {p['cv']:>6.3f}"
              f" {hrs:>14} {(p.get('model_probability') or 0)*100:>6.1f}"
              f" {result:>9}"
              f" {p.get('profit_units',0):>+6.2f}")


def _roi_curves_by_edge(out: Dict[str, Any]):
    print(f"\n━━━━━━━━━━━━━━━━ ROI CURVES BY EDGE ━━━━━━━━━━━━━━━━")
    print(f"  {'edge bucket':<14} {'tier':<14} {'n':>5}  {'HR':>7} {'ROI':>8} {'u':>8}")
    for bucket in ["edge_05_10", "edge_10_20", "edge_20_30", "edge_30p"]:
        for tier in ("safe_haven", "front_lines", "war_zone"):
            td = out["tiers"][tier]
            v = td["by_edge_bucket"].get(bucket)
            if not v or not v.get("total"): continue
            print(f"  {bucket:<14} {tier:<14} {v['total']:>5}  "
                  f"{_fmt_hr(v['hit_rate_pct']):>7} "
                  f"{_fmt_pct(v['roi_pct']):>8} "
                  f"{v['profit_units']:>+8.2f}")


def _recommendation_observations(out: Dict[str, Any]):
    print(f"\n━━━━━━━━━━━━━━━ RECOMMENDATION OBSERVATIONS ━━━━━━━━━━━━━━━")
    print(f"  (Observational only — NO thresholds changed.)")
    tiers = out["tiers"]
    sizes = {t: tiers[t]["overall"]["total"] for t in ("safe_haven","front_lines","war_zone")}
    rois  = {t: tiers[t]["overall"]["roi_pct"] for t in ("safe_haven","front_lines","war_zone")}
    hrs   = {t: tiers[t]["overall"]["hit_rate_pct"] for t in ("safe_haven","front_lines","war_zone")}
    print(f"  • Selectivity curve (qualified picks): "
          f"SH={sizes['safe_haven']}  FL={sizes['front_lines']}  WZ={sizes['war_zone']}")
    ordered_sel = sorted(sizes.items(), key=lambda kv: kv[1])
    print(f"    → ordering (tightest→loosest): "
          + " → ".join(f"{t}({n})" for t, n in ordered_sel))

    print(f"  • Hit-rate curve: "
          f"SH={_fmt_hr(hrs['safe_haven'])}  FL={_fmt_hr(hrs['front_lines'])}  "
          f"WZ={_fmt_hr(hrs['war_zone'])}")
    print(f"  • ROI curve: "
          f"SH={_fmt_pct(rois['safe_haven'])}  FL={_fmt_pct(rois['front_lines'])}  "
          f"WZ={_fmt_pct(rois['war_zone'])}")

    # Profitability curve direction
    valid_roi = [(t, r) for t, r in rois.items() if r is not None]
    if valid_roi:
        best = max(valid_roi, key=lambda kv: kv[1])
        worst = min(valid_roi, key=lambda kv: kv[1])
        print(f"  • Highest ROI tier: {best[0]} ({best[1]:+.1f}%); "
              f"lowest: {worst[0]} ({worst[1]:+.1f}%)")
    # Risk curve via avg edge / cv
    for tier in ("safe_haven", "front_lines", "war_zone"):
        o = tiers[tier]["overall"]
        if o["total"] == 0: continue
        print(f"  • {tier:<12}  avg_edge={(o['avg_edge'] or 0)*100:+.2f}pp  "
              f"avg_cv={o['avg_cv'] or 0:.3f}  "
              f"avg_odds={(o['avg_odds'] or 0):.0f}  "
              f"avg_mu_gap={(o['avg_mu_minus_line'] or 0):+.3f}")

    ov = out["overlap"]
    if ov["sh_size"] and ov["sh_∩_fl"] < ov["sh_size"]:
        leak = ov["sh_size"] - ov["sh_∩_fl"]
        print(f"  • {leak} Safe Haven picks do NOT qualify Front Lines "
              f"({leak/ov['sh_size']*100:.1f}%) — non-nested. Check whether "
              f"FL stat-family CV caps are TIGHTER than SH for some families.")
    if ov["fl_size"] and ov["fl_∩_wz"] < ov["fl_size"]:
        leak = ov["fl_size"] - ov["fl_∩_wz"]
        print(f"  • {leak} Front Lines picks do NOT qualify War Zone "
              f"({leak/ov['fl_size']*100:.1f}%) — non-nested. WZ requires "
              f"hr_l20≥70 AND hr_l5≥60 AND edge≥0.05; some FL picks miss these.")

    print(f"  • No threshold changes recommended yet — collect multi-date "
          f"sweep before tuning.")


async def amain(args):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    snapshot_iso = args.snapshot_iso or f"{args.date}T11:00:00Z"
    scoring_version = args.scoring_version or SCORING_CONFIG_VERSION

    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║ MLB REPLAY LAYER 4 — MULTI-TIER SWEEP                        ║")
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║ game_date      : {args.date:<46}║")
    print(f"║ snapshot_iso   : {snapshot_iso:<46}║")
    print(f"║ scoring_version: {scoring_version:<46}║")
    print(f"║ tier configs   : {', '.join([TIER_CONFIGS[t]['version'] for t in ('safe_haven','front_lines','war_zone')]):<46}║")
    print(f"╚══════════════════════════════════════════════════════════════╝")

    out = await run_multi_tier_for_date(
        db, args.date,
        snapshot_iso=snapshot_iso,
        scoring_config_version=scoring_version,
        mem_limit_mb=args.mem_limit,
    )

    print(f"\n  rows_scanned     {out['rows_scanned']:,}")
    print(f"  elapsed          {out['elapsed_s']:.2f}s")
    print(f"  RSS start/peak/end  "
          f"{out['rss_mb_start']}/{out['rss_mb_peak']}/{out['rss_mb_end']} MB")

    _print_comparison_table(out)
    _print_overlap(out)

    for tier in ("safe_haven", "front_lines", "war_zone"):
        _print_tier_block(tier, out["tiers"][tier])

    _roi_curves_by_edge(out)

    # Top 25 qualifiers per tier (sorted hit_rate_l10 desc per user prefs)
    for tier in ("safe_haven", "front_lines", "war_zone"):
        picks = await _top_qualifiers(
            db, TIER_CONFIGS[tier]["version"], args.date, snapshot_iso, 25,
        )
        _print_top_picks(tier.upper(), picks)

    _recommendation_observations(out)
    cli.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--snapshot-iso", default=None)
    p.add_argument("--scoring-version", default=None)
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
