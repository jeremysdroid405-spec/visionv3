"""One-off: top-30 NBA Safe Haven rejects with full odds + filtering metadata."""
import asyncio
import json
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    raw = await db.nba_prop_scores.find(
        {
            "version_tag": "final-nba-rt",
            "tier_reference_odds": {"$lte": -240},
            "tier": {"$ne": "safe_haven"},
        },
        {"_id": 0},
    ).to_list(length=10000)

    def num(v):
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    raw.sort(key=lambda r: (
        -num(r.get("edge_pct")),
        -num(r.get("vision_score")),
        r.get("canonical_key") or "",
    ))
    rejects = raw[:30]
    keys = [r["canonical_key"] for r in rejects]

    odds_by_key = {}
    cursor = db.nba_live_props.find(
        {"canonical_key": {"$in": keys}},
        {"_id": 0, "canonical_key": 1,
         "dk_line": 1, "dk_odds": 1, "dk_odds_opp": 1,
         "fd_line": 1, "fd_odds": 1, "fd_odds_opp": 1,
         "mgm_line": 1, "mgm_odds": 1, "mgm_odds_opp": 1,
         "bol_line": 1, "bol_odds": 1, "bol_odds_opp": 1,
         "pp_line": 1, "pp_odds": 1, "pp_layer": 1,
         "sharp_book": 1, "sharp_line": 1, "sharp_odds": 1,
         "is_alternate_market": 1, "is_b2b": 1, "rest_days": 1, "starter": 1,
         "live_injury_count": 1, "team_injury_count": 1, "team_out_count": 1,
         "usage_vacuum_factor": 1, "expected_minutes": 1,
         "game_total": 1, "team_total": 1, "team": 1, "opponent_team": 1},
    )
    for d in await cursor.to_list(length=5000):
        odds_by_key[d["canonical_key"]] = d

    out = []
    for i, r in enumerate(rejects, 1):
        side = (r.get("recommendation") or "").upper()
        hit = r.get("hit_rate_over") if side == "OVER" else r.get("hit_rate_under")
        proj = r.get("vk2_projection") or r.get("model_projection")
        gate = (r.get("tier_reason") or "").replace("safe_haven_failed: ", "")
        ck = r["canonical_key"]
        ox = odds_by_key.get(ck, {})

        def fmt(prefix):
            ln = ox.get(f"{prefix}_line")
            od = ox.get(f"{prefix}_odds")
            opp = ox.get(f"{prefix}_odds_opp")
            if ln is None and od is None:
                return "—"
            if opp is not None:
                return f"L{ln} O{od:>+5} U{opp:>+5}"
            return f"L{ln} {od}"

        gate_results = r.get("tier_gate_results") or {}

        def gpv(g):
            x = gate_results.get(g) or {}
            p = "✅" if x.get("passed") is True else ("❌" if x.get("passed") is False else "—")
            v = x.get("value")
            return f"{p}({v})" if v is not None else p

        out.append({
            "rank": i,
            "player": r.get("player_name"),
            "team": r.get("team") or ox.get("team"),
            "opp": r.get("opponent_team") or ox.get("opponent_team"),
            "side": side,
            "stat": r.get("stat_type"),
            "line": r.get("line"),
            "is_alt": bool(r.get("is_alternate_market") or ox.get("is_alternate_market")),
            "pp_label": r.get("pp_multiplier_label") or "—",
            "pp_playable": r.get("playable_on_pp"),
            "tier_reason": gate,
            "ref_odds": r.get("tier_reference_odds"),
            "ref_book": r.get("tier_reference_book"),
            "edge_pct": r.get("edge_pct"),
            "tp": r.get("tp"),
            "p_true": r.get("p_true_active"),
            "p_true_method": r.get("p_true_method"),
            "fair_prob": r.get("fair_prob"),
            "edge_vs_fair": r.get("edge_vs_fair"),
            "cv": r.get("cv"),
            "hit_rate": hit,
            "l20_n": r.get("hit_rate_sample_size"),
            "vision": r.get("vision_score"),
            "vision_raw": r.get("vision_score_raw"),
            "projection": round(float(proj), 2) if isinstance(proj, (int, float)) else None,
            "book_count": r.get("book_count"),
            "tp_books": r.get("tp_books_used"),
            "tp_books_list": r.get("tp_books_list"),
            "tp_method": r.get("tp_method"),
            "tp_source": r.get("tp_source"),
            "anchor_book": r.get("anchor_book"),
            "odds": {
                "dk": fmt("dk"),
                "fd": fmt("fd"),
                "mgm": fmt("mgm"),
                "bol": fmt("bol"),
                "pp": (f"L{ox.get('pp_line')} {ox.get('pp_odds')} layer={ox.get('pp_layer') or '-'}"
                       if ox.get("pp_line") is not None else "—"),
                "sharp": (f"{ox.get('sharp_book')}: L{ox.get('sharp_line')} @ {ox.get('sharp_odds')}"
                          if ox.get("sharp_book") else "—"),
            },
            "context": {
                "rest_days": ox.get("rest_days"),
                "is_b2b": ox.get("is_b2b"),
                "starter": ox.get("starter"),
                "expected_min": ox.get("expected_minutes"),
                "team_out": ox.get("team_out_count"),
                "team_inj": ox.get("team_injury_count"),
                "live_inj": ox.get("live_injury_count"),
                "usage_vac": ox.get("usage_vacuum_factor"),
                "game_total": ox.get("game_total"),
                "team_total": ox.get("team_total"),
            },
            "gates": {
                "hit_rate":      gpv("hit_rate_gate"),
                "vision":        gpv("vision_score_gate"),
                "cv":            gpv("cv_gate"),
                "market_struct": gpv("market_structure_gate"),
            },
        })

    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/nba_top30_safe_haven_rejects_{stamp}.json"
    with open(path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sport": "nba",
            "version_tag": "final-nba-rt",
            "count": len(out),
            "sort": ["edge_pct DESC", "vision_score DESC", "canonical_key ASC"],
            "rejects": out,
        }, f, default=str, indent=2)
    print(f"ARTIFACT: {path}")

    print()
    print("=" * 138)
    print("TOP 30 NBA SAFE HAVEN REJECTS  (final-nba-rt)  |  sort: edge% desc, vision desc, canonical_key asc")
    print("=" * 138)
    print(f"{'#':>2} {'Player':<22} {'TM/OPP':<10} {'Side':<5} {'Stat':<6} {'Line':<5} "
          f"{'Alt':<3} {'PP':<7} {'RefOdds(book)':<13} {'Edge':>5} {'TP%':>5} "
          f"{'L20':>4} {'CV':>6} {'Vis':>5} {'Proj':>6} {'Failed':<14}")
    print("-" * 138)
    for r in out:
        team_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
        ref = f"{r['ref_odds']}({r['ref_book']})"
        alt = "Y" if r["is_alt"] else ""
        vis = f"{r['vision']:.1f}" if isinstance(r["vision"], (int, float)) else "—"
        proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
        pp = (r["pp_label"] or "—")[:7]
        cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
        gate_short = (r["tier_reason"] or "").replace("gate_", "").replace("_fail", "")[:14]
        print(f"{r['rank']:>2} {r['player'][:22]:<22} {team_opp:<10} {r['side']:<5} "
              f"{r['stat'][:6]:<6} {str(r['line']):<5} {alt:<3} {pp:<7} {ref:<13} "
              f"{r['edge_pct']:>5} {r['tp']:>5} {r['hit_rate']:>4} {cv:>6} {vis:>5} {proj:>6} {gate_short:<14}")
    print()
    print("=" * 138)
    print("PER-BOOK ODDS BREAKDOWN (DK / FD / MGM / BOL / PP / Sharp)  |  L=line  O=over odds  U=under odds")
    print("=" * 138)
    for r in out:
        odds = r["odds"]
        print(f"#{r['rank']:>2} {r['player'][:22]:<22} {r['stat']:<5} {r['line']:>4} {r['side']:<5}")
        print(f"      DK : {odds['dk']}")
        print(f"      FD : {odds['fd']}")
        print(f"      MGM: {odds['mgm']}")
        print(f"      BOL: {odds['bol']}")
        print(f"      PP : {odds['pp']}")
        print(f"      SHP: {odds['sharp']}")
        c = r["context"]
        print(f"      ctx: rest={c['rest_days']} b2b={c['is_b2b']} starter={c['starter']} "
              f"min={c['expected_min']} team_out={c['team_out']} usage_vac={c['usage_vac']} "
              f"game_total={c['game_total']} team_total={c['team_total']}")
        g = r["gates"]
        print(f"      gates: hit_rate={g['hit_rate']} vision={g['vision']} cv={g['cv']} mkt_struct={g['market_struct']}")
        print(f"      tp_books={r['tp_books_list']} method={r['tp_method']} src={r['tp_source']} "
              f"book_count={r['book_count']} fair_prob={r['fair_prob']} p_true={r['p_true']} "
              f"edge_vs_fair={r['edge_vs_fair']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
