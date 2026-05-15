"""Top-30 MLB FL OVER rejects — comprehensive tuning-grade view.

Sorted by hit_rate_l20 (desc). Restricted to:
  active=True, recommendation='OVER', routed_tier='front_lines',
  batter stats only, projection_model_version='MLB_HF_v3.1_phase2a'.

Per row dumps ALL fields relevant to gate-tuning decisions:
  • identity + matchup
  • hit-rate stack (L5 / L10 / L20)
  • projection stack (raw HF / EB-shrunk / final mu / sigma / CV)
  • margin / direction / vision score
  • probability stack (p_model / fair / tp / market / best book)
  • edge stack (edge_vs_fair / total_edge / best_book_edge / shopping_edge)
  • odds stack (DK / FD / MGM / consensus / book_count / ref book)
  • EVERY gate evaluated (pass + fail + actual vs threshold)
"""
from __future__ import annotations
import asyncio, json, os, sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


BATTER_STATS = {
    "Hits", "Total Bases", "RBIs", "Runs", "Home Runs", "Doubles",
    "Walks", "Singles", "Hits+Runs+RBIs", "Stolen Bases",
    "Batter Strikeouts",
}


def _f(v, d=2, default="—"):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return f"{v:.{d}f}"
    return str(v)


def _odds(v):
    if v is None:
        return "—"
    try:
        return f"{int(v):+d}"
    except Exception:
        return str(v)


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ["DB_NAME"]
    ]
    cursor = db.mlb_prop_scores.find(
        {
            "active": True,
            "recommendation": "OVER",
            "routed_tier": "front_lines",
            "tier": "unqualified",
            "stat_type": {"$in": list(BATTER_STATS)},
            "projection_model_version": "MLB_HF_v3.1_phase2a",
            "hit_rate_l20": {"$ne": None},
        },
        {"_id": 0},
    ).sort([("hit_rate_l20", -1), ("edge_vs_fair", -1)])

    rows = await cursor.to_list(length=200)
    # Dedupe (player, stat, line).
    seen = set(); unique = []
    for r in rows:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen:
            continue
        seen.add(k); unique.append(r)
        if len(unique) >= 30:
            break

    print("=" * 100)
    print("TOP 30 MLB FL OVER REJECTS — COMPREHENSIVE TUNING VIEW")
    print(" Sorted: hit_rate_l20 desc, then edge_vs_fair desc")
    print(" Filter:  active + FL routing + unqualified + batter + v3.1_phase2a")
    print("=" * 100)

    for i, r in enumerate(unique, 1):
        # Per-gate detail
        gr = r.get("tier_gate_results") or {}
        # Pre-build gate detail strings
        gate_lines = []
        for gname in ("coverage_gate", "direction_gate", "cv_gate",
                       "hit_rate_gate", "edge_gate", "tp_gate",
                       "tp_source_gate"):
            g = gr.get(gname)
            if not g:
                continue
            passed = "✓" if g.get("passed") else "✗"
            val = g.get("value")
            thr = g.get("threshold")
            reason = g.get("reason_code") or ""
            gate_lines.append(
                f"      [{passed}] {gname:<18} value={val}  threshold={thr}  {reason}"
            )

        # Recommendation devig vs implied (informational)
        evf = r.get("edge_vs_fair")
        fair_prob = r.get("fair_prob")
        tp = r.get("tp")
        market_prob = r.get("market_probability") or r.get(
            "consensus_implied_prob")

        print()
        print(f"╔════ #{i:>2}  {r['player_name']:<22}  "
               f"{r['stat_type']:<18}  Line={r.get('line')}  "
               f"{r.get('recommendation')}  "
               f"({r.get('team') or '?'} {('vs' if r.get('is_home_team')==1 else '@')} "
               f"{r.get('opponent') or '?'})")
        print(f"║   commence: {r.get('commence_time')}  | "
               f"updated: {str(r.get('updated_at'))[:19]}  | "
               f"books: {r.get('book_count')}  | "
               f"ref: {r.get('tier_reference_book')} @ {_odds(r.get('tier_reference_odds'))}")

        # Matchup
        opp_pn = r.get("opp_pitcher_name") or "—"
        opp_pt = r.get("opp_pitcher_throws") or "?"
        print(f"║")
        print(f"║   MATCHUP")
        print(f"║     batter_hand          : {r.get('batter_hand') or '?'} "
               f"| batting_order: {r.get('batting_order')} "
               f"| lineup_confirmed: {r.get('lineup_confirmed')} "
               f"| venue: {r.get('venue')}")
        print(f"║     opp_pitcher          : {opp_pn} ({opp_pt})  "
               f"id={r.get('opp_pitcher_id')}  "
               f"ERA={_f(r.get('opp_pitcher_era'),2)}  "
               f"WHIP={_f(r.get('opp_pitcher_whip'),2)}  "
               f"K/9={_f(r.get('opp_pitcher_k9'),2)}")
        print(f"║     handedness           : same_hand={r.get('same_hand_matchup')}  "
               f"opp_hand={r.get('opposite_hand_matchup')}")
        print(f"║     expected_pa          : {_f(r.get('expected_pa'),1)} "
               f"| tempo_mod: {_f(r.get('tempo_modifier'),2)} "
               f"| park_team: {r.get('park_team') or '?'}")

        # Hit-rate stack
        print(f"║")
        print(f"║   HIT RATE  (sample size: {r.get('hit_rate_sample_size')})")
        print(f"║     hit_rate_l5  / l10  / l20  : "
               f"{_f(r.get('hit_rate_l5'),0):>5} / "
               f"{_f(r.get('hit_rate_l10'),0):>5} / "
               f"{_f(r.get('hit_rate_l20'),0):>5}")
        print(f"║     avg_hit_margin / avg_miss   : "
               f"{_f(r.get('avg_hit_margin'),2)} / "
               f"{_f(r.get('avg_miss_margin'),2)}")

        # Projection stack
        print(f"║")
        print(f"║   PROJECTION")
        print(f"║     raw_hf_projection           : {_f(r.get('raw_hf_projection'),3)}")
        print(f"║     eb_shrunk_projection        : {_f(r.get('eb_shrunk_projection'),3)}")
        print(f"║     mu_raw_model_projection     : {_f(r.get('mu_raw_model_projection'),3)}")
        print(f"║     distribution_effective_mu   : {_f(r.get('distribution_effective_mu'),3)}")
        print(f"║     FINAL  model_projection     : {_f(r.get('model_projection'),3)}  "
               f"(line {r.get('line')}, margin = {_f((r.get('model_projection') or 0) - (r.get('line') or 0), 3)})")
        print(f"║     model_sigma                 : {_f(r.get('model_sigma'),3)}  "
               f"| distribution_sigma: {_f(r.get('distribution_sigma'),3)}  "
               f"({r.get('distribution_sigma_source')})")
        print(f"║     CV                          : {_f(r.get('cv'),3)}  "
               f"(status: {r.get('cv_status')})")
        print(f"║     distribution_kind           : {r.get('distribution_kind')}  "
               f"({r.get('distribution_selector_reason')})")
        print(f"║     EB shrinkage applied        : {r.get('eb_skip_reason') is None}  "
               f"(skip_reason: {r.get('eb_skip_reason')})")

        # Probability + edge stack
        print(f"║")
        print(f"║   PROBABILITY / EDGE  (Universal Edge SSOT)")
        print(f"║     p_model (recon)             : "
               f"{_f((fair_prob+evf) if (fair_prob and evf) else None,4)} "
               f"(= fair_prob + edge_vs_fair)")
        print(f"║     p_distribution              : {_f(r.get('p_distribution'),4)}")
        print(f"║     p_true_active / method      : {_f(r.get('p_true_active'),4)} "
               f"/ {r.get('p_true_method')}")
        print(f"║     fair_prob                   : {_f(fair_prob,4)}  "
               f"| tp: {_f(tp,2)}%  | market_prob: {_f(market_prob,4)}")
        print(f"║     best_book                   : {r.get('best_book')} @ "
               f"{_odds(r.get('best_book_odds'))} "
               f"(implied={_f(r.get('best_book_implied_probability'),4)}, "
               f"devig={_f(r.get('best_book_devig_probability'),4)})")
        print(f"║     EDGES")
        print(f"║       edge_vs_fair (UI = gate)   : "
               f"{_f((evf*100) if evf is not None else None,2)} pp "
               f"({_f(evf,4)} decimal)")
        print(f"║       total_edge (vs best book) : {_f(r.get('total_edge'),4)}")
        print(f"║       best_book_edge            : {_f(r.get('best_book_edge'),4)}")
        print(f"║       shopping_edge_source      : {r.get('shopping_edge_source')}")

        # Odds stack
        print(f"║")
        print(f"║   ODDS")
        print(f"║     DK / FD / MGM / Pinnacle / PrizePicks : "
               f"{_odds(r.get('dk_odds'))} / "
               f"{_odds(r.get('fd_odds'))} / "
               f"{_odds(r.get('mgm_odds'))} / "
               f"{_odds(r.get('pin_odds'))} / "
               f"{_odds(r.get('pp_odds'))}")
        print(f"║     consensus_odds              : {_odds(r.get('consensus_odds'))} "
               f"(implied {_f(r.get('consensus_implied_prob'),4)})")
        print(f"║     tp_source / books_used       : {r.get('tp_source')} / "
               f"{r.get('tp_books_used')} "
               f"(books: {r.get('tp_books_list')})")

        # Vision + ranking
        print(f"║")
        print(f"║   VISION / RANK")
        print(f"║     vision_score / v2 / dir_gate : {_f(r.get('vision_score'),2)} / "
               f"{_f(r.get('vision_score_v2'),2)} / {_f(r.get('vision_v2_dir_gate'),2)}")
        print(f"║     direction_margin             : {_f(r.get('vision_v2_direction_margin'),3)} "
               f"(strength: {_f(r.get('vision_v2_direction_strength'),3)})")
        print(f"║     ranking_score_v2             : {_f(r.get('ranking_score_v2'),4)}")
        print(f"║     stability                    : {_f(r.get('stability'),4)}  "
               f"volatility_penalty: {_f(r.get('vision_volatility_penalty'),3)}")

        # Verdict + every gate
        print(f"║")
        print(f"║   VERDICT: {r.get('tier')} — {r.get('tier_reason')}")
        print(f"║   GATE RESULTS")
        for gl in gate_lines:
            print("║" + gl)

        # PP playability
        if r.get("pp_available"):
            print(f"║")
            print(f"║   PRIZEPICKS: line={r.get('pp_line')}  odds={_odds(r.get('pp_odds'))}  "
                   f"playable={r.get('pp_playable')}  utility={_f(r.get('pp_utility'),1)} "
                   f"({r.get('pp_utility_category')})")

        print(f"╚{'═'*98}")


if __name__ == "__main__":
    asyncio.run(main())
