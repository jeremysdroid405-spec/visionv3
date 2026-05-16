"""
Top 30 War Zone candidates across all sports.

Reads from {sport}_prop_scores where tier == 'war_zone', ranked by
vision_score (the production WZ ranking signal). Most-recent computed_at
per canonical_key_v2 wins. Output includes all odds, stats, edges,
distribution params, and gate trace.
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

SPORTS = ["mlb"]
LIMIT = 25
SINCE_HOURS = 2


def _f(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{n}f}"
    return str(v)


def main():
    since = (datetime.now(timezone.utc) - timedelta(hours=SINCE_HOURS)).isoformat()

    # Aggregate latest doc per canonical_key_v2 with tier=war_zone
    rows = []
    for sport in SPORTS:
        coll = f"{sport}_prop_scores"
        if coll not in db.list_collection_names():
            continue
        pipeline = [
            {"$match": {"tier": "war_zone", "computed_at": {"$gte": since}}},
            {"$sort": {"computed_at": -1}},
            {"$group": {
                "_id": "$canonical_key_v2",
                "doc": {"$first": "$$ROOT"},
            }},
            {"$replaceRoot": {"newRoot": "$doc"}},
        ]
        for d in db[coll].aggregate(pipeline, allowDiskUse=True):
            d.pop("_id", None)
            rows.append(d)

    # Rank by L10 hit-rate desc, tie-break on L20 hit-rate desc, then vision_score
    def _sort_key(d):
        return (
            -(d.get("hit_rate_l10") or 0.0),
            -(d.get("hit_rate_l20") or 0.0),
            -(d.get("hit_rate_l5") or 0.0),
            -(d.get("vision_score") or 0.0),
        )

    rows.sort(key=_sort_key)
    top = rows[:LIMIT]

    # Pretty print
    print(f"\n=== TOP {LIMIT} WAR ZONE CANDIDATES (across {','.join(SPORTS).upper()}) ===")
    print(f"=== last {SINCE_HOURS}h, ranked by L10 hit-rate (tie: L20, L5, vision) ===\n")

    def _odds(d, key):
        ly = d.get(key) or {}
        v = ly.get("odds")
        if v is None:
            return "—"
        return f"{int(v):+d}" if isinstance(v, (int, float)) else str(v)

    # Section 1: ranked summary with inline per-book odds
    print(f"{'#':<3} {'Sport':<4} {'Player':<22} {'Stat':<22} {'L':<5} {'Side':<5} "
          f"{'HR_L5':>6} {'HR_L10':>7} {'HR_L20':>7} {'CV':>6} {'VS':>6} {'TP%':>6} "
          f"{'FairP':>6} {'Edge':>6} {'μ':>6} {'σ':>5} {'P̂':>6} "
          f"{'DK':>6} {'FD':>6} {'EB':>6} {'HRB':>6} {'CSR':>6} {'MGM':>6} "
          f"{'BRV':>6} {'PRX':>6} {'BLY':>6} {'FLF':>6} {'PP':>6}")
    print("-" * 257)
    for i, d in enumerate(top, 1):
        line = d.get("line")
        line_s = f"{line:.1f}" if line is not None else "—"
        p_true = d.get("p_true_active") or d.get("p_distribution")
        print(
            f"{i:<3} {d.get('sport','?').upper():<4} "
            f"{(d.get('player_name') or '')[:22]:<22} "
            f"{(d.get('stat_type') or '')[:22]:<22} "
            f"{line_s:<5} {d.get('side','—'):<5} "
            f"{_f(d.get('hit_rate_l5'),1):>6} "
            f"{_f(d.get('hit_rate_l10'),1):>7} "
            f"{_f(d.get('hit_rate_l20'),1):>7} "
            f"{_f(d.get('cv'),3):>6} "
            f"{_f(d.get('vision_score'),1):>6} "
            f"{_f(d.get('tp'),1):>6} "
            f"{_f(d.get('fair_prob'),3):>6} "
            f"{_f(d.get('total_edge'),3):>6} "
            f"{_f(d.get('model_projection'),2):>6} "
            f"{_f(d.get('model_sigma'),2):>5} "
            f"{_f(p_true,3):>6} "
            f"{_odds(d,'dk_layer'):>6} "
            f"{_odds(d,'fd_layer'):>6} "
            f"{_odds(d,'eb_layer'):>6} "
            f"{_odds(d,'hrb_layer'):>6} "
            f"{_odds(d,'csr_layer'):>6} "
            f"{_odds(d,'mgm_layer'):>6} "
            f"{_odds(d,'brv_layer'):>6} "
            f"{_odds(d,'prx_layer'):>6} "
            f"{_odds(d,'bly_layer'):>6} "
            f"{_odds(d,'flf_layer'):>6} "
            f"{_odds(d,'pp_layer'):>6}"
        )

    # Section 2: detailed per-row breakdown
    print("\n\n=== DETAILED BREAKDOWN ===")
    for i, d in enumerate(top, 1):
        print(f"\n──── #{i}  {d.get('player_name')} — {d.get('stat_type')} {d.get('side')} {d.get('line')} "
              f"({d.get('sport','?').upper()}) ────")
        print(f"  Match: {d.get('team')} {'@' if d.get('is_away_team') else 'vs'} "
              f"{d.get('opponent') or d.get('opponent_team')} | start {d.get('commence_time') or d.get('game_start_utc')}")
        if d.get("opp_pitcher_name"):
            print(f"  Opp Pitcher: {d['opp_pitcher_name']} (throws {d.get('opp_pitcher_throws')}, "
                  f"K/9 {d.get('opp_pitcher_k9')}, ERA {d.get('opp_pitcher_era')}, WHIP {d.get('opp_pitcher_whip')})")
        if d.get("batter_hand"):
            print(f"  Batter hand: {d['batter_hand']}  "
                  f"same={d.get('same_hand_matchup')}  opp={d.get('opposite_hand_matchup')}")

        # Odds across all books
        print(f"  Odds (per-book):")
        for label, layer_key in [
            ("DraftKings", "dk_layer"), ("FanDuel", "fd_layer"),
            ("ESPNBet", "eb_layer"), ("HardRock", "hrb_layer"),
            ("WilliamHill (CSR)", "csr_layer"), ("BetMGM", "mgm_layer"),
            ("Caesars", "cae_layer"), ("PrizePicks", "pp_layer"),
        ]:
            ly = d.get(layer_key)
            if ly:
                print(f"    {label:<20} line={ly.get('line')} odds={ly.get('odds')}  fetched={ly.get('fetched_at')}")
        # Books used in devig
        print(f"  TP/Devig: tp={_f(d.get('tp'),2)}%  fair_prob={_f(d.get('fair_prob'),4)}  "
              f"books_used={d.get('tp_books_used')}  books_list={d.get('tp_books_list')}  "
              f"method={d.get('tp_method')}")
        print(f"  Anchor: best_book={d.get('best_book')} odds={d.get('best_book_odds')} "
              f"edge={_f(d.get('best_book_edge'),4)}  total_edge={_f(d.get('total_edge'),4)}  "
              f"shopping_edge_source={d.get('shopping_edge_source')}")

        # Model + distribution
        print(f"  Model: μ_raw={_f(d.get('mu_raw_model_projection'),4)}  μ_final={_f(d.get('model_projection'),4)}  "
              f"σ={_f(d.get('model_sigma'),4)}  version={d.get('projection_model_version')}")
        if d.get("eb_shrinkage_applied"):
            print(f"  EB shrink: shrunk={_f(d.get('eb_shrunk_projection'),3)}  "
                  f"player_career_mean={_f(d.get('eb_player_career_mean'),3)}  "
                  f"player_n={d.get('eb_career_sample_n')}  "
                  f"weights model={d.get('eb_weight_model')} player={d.get('eb_weight_player')}")
        print(f"  Distribution: kind={d.get('distribution_kind')} λ={d.get('distribution_lambda')} "
              f"thresh={d.get('distribution_threshold')}  p_over={_f(d.get('distribution_p_over'),4)}  "
              f"selector={d.get('distribution_selector_reason')}")
        print(f"  Shadow probs: lom={_f(d.get('lom_p_over'),4)}  ecdf={_f(d.get('ecdf_p_over'),4)} "
              f"(bucket={d.get('ecdf_bucket')} n={d.get('ecdf_bucket_n')})  raw_gauss={_f(d.get('raw_gaussian_p_over'),4)}")

        # Hit rate / form
        print(f"  Form: HR_L5={_f(d.get('hit_rate_l5'),1)}%  HR_L10={_f(d.get('hit_rate_l10'),1)}%  "
              f"HR_L20={_f(d.get('hit_rate_l20'),1)}%  HR_over={_f(d.get('hit_rate_over'),1)}%  "
              f"n_games={d.get('hit_rate_sample_size')}")
        print(f"  Margins: avg_hit={_f(d.get('avg_hit_margin'),2)}  avg_miss={_f(d.get('avg_miss_margin'),2)}  "
              f"ceiling={_f(d.get('ceiling_rate'),1)}%  CV={_f(d.get('cv'),3)}  "
              f"stability={_f(d.get('stability'),3)}")

        # Vision components
        print(f"  Vision: SCORE={_f(d.get('vision_score'),2)} | "
              f"prob={_f(d.get('vision_probability_component'),3)}  "
              f"proj={_f(d.get('vision_projection_component'),3)}  "
              f"edge={_f(d.get('vision_edge_component'),3)}  "
              f"consist={_f(d.get('vision_consistency_component'),3)}  "
              f"context={_f(d.get('vision_context_component'),3)}  "
              f"mkt_conf={_f(d.get('vision_market_confidence_component'),3)}  "
              f"dir_align={_f(d.get('vision_direction_alignment'),3)}  "
              f"vol_penalty={_f(d.get('vision_volatility_penalty'),3)}")

        # Tier / gates
        gates = d.get("tier_gate_results") or {}
        gate_results = []
        for gname, gv in (gates.items() if isinstance(gates, dict) else []):
            passed = gv.get("passed") if isinstance(gv, dict) else None
            val = gv.get("value") if isinstance(gv, dict) else None
            thresh = gv.get("threshold") if isinstance(gv, dict) else None
            gate_results.append(f"{gname}={'✓' if passed else '✗'}({val} vs {thresh})")
        print(f"  Gates: {' | '.join(gate_results) if gate_results else '(no gate trace)'}")
        print(f"  Tier: {d.get('tier')}  routed={d.get('routed_tier')}  reason={d.get('tier_reason')}")

        # Intel
        intel = d.get("intel_suite") or {}
        if isinstance(intel, dict) and intel:
            tempo = (intel.get("tempo") or {}).get("display")
            if tempo:
                print(f"  Tempo: {tempo} ({(intel.get('tempo') or {}).get('tempo_label')})")
        injury = d.get("injury_context") or {}
        if isinstance(injury, dict) and injury.get("team"):
            t = injury["team"]
            print(f"  Injury: team out={t.get('out_count')} dtd={t.get('dtd_count')}  "
                  f"key_out={t.get('out_players')}")
        print(f"  Feature health: {d.get('feature_health')}")
        print(f"  Computed: {d.get('computed_at')}")


if __name__ == "__main__":
    main()
