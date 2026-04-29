"""Top-20 MLB Safe Haven rejects — props routed to SH that failed gates.

Routes:
  Safe Haven : ref_odds <= -300

Notes on MLB gating:
  - SH MLB uses per-stat-family gates (cv_gate, hit_rate_gate, edge_gate,
    tp_gate). See `_MLB_SAFE_HAVEN` in services/scoring/gates/thresholds.py.
  - Stat-family-specific HR floors (e.g. Hits ≥ 80, HRR ≥ 85, BSO ≥ 85).
  - Fail reasons captured per pick via `tier_gate_results`.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from collections import Counter
from motor.motor_asyncio import AsyncIOMotorClient

VERSION = "final-mlb-rt"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # Fetch all SH-routed picks that did not land in safe_haven.
    raw = await db.mlb_prop_scores.find(
        {"version_tag": VERSION,
         "tier_reference_odds": {"$lte": -300},
         "tier": {"$ne": "safe_haven"}},
        {"_id": 0},
    ).to_list(length=10000)

    def num(v):
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    raw.sort(key=lambda r: (
        -num(r.get("edge_pct")),
        -num(r.get("vision_score")),
        r.get("canonical_key") or "",
    ))
    rejects = raw[:20]

    keys = [r["canonical_key"] for r in rejects if r.get("canonical_key")]
    odds_by_key = {}
    cursor = db.mlb_live_props.find(
        {"canonical_key": {"$in": keys}},
        {"_id": 0, "canonical_key": 1,
         "dk_line": 1, "dk_odds": 1, "dk_odds_opp": 1,
         "fd_line": 1, "fd_odds": 1, "fd_odds_opp": 1,
         "mgm_line": 1, "mgm_odds": 1, "mgm_odds_opp": 1,
         "bol_line": 1, "bol_odds": 1, "bol_odds_opp": 1,
         "pp_line": 1, "pp_odds": 1, "pp_layer": 1,
         "sharp_book": 1, "sharp_line": 1, "sharp_odds": 1,
         "team": 1, "opponent_team": 1, "is_alternate_market": 1,
         "is_starter": 1, "lineup_status": 1, "batting_order_position": 1,
         "park_factor": 1, "expected_pa": 1, "expected_outs_pitched": 1,
         "is_home": 1, "starting_pitcher": 1},
    )
    for d in await cursor.to_list(length=5000):
        odds_by_key[d["canonical_key"]] = d

    # Fail-gate distribution (full SH-routed reject pool, not just top 20)
    full_failgate = Counter()
    for r in raw:
        gates = r.get("tier_gate_results") or {}
        for g, x in gates.items():
            if isinstance(x, dict) and x.get("passed") is False:
                full_failgate[g] += 1

    rows = []
    for i, r in enumerate(rejects, 1):
        side = (r.get("recommendation") or "").upper()
        proj = r.get("model_projection") or r.get("vk2_projection")
        ck = r.get("canonical_key")
        ox = odds_by_key.get(ck, {})
        gates = r.get("tier_gate_results") or {}

        def fmt(prefix):
            ln = ox.get(f"{prefix}_line")
            od = ox.get(f"{prefix}_odds")
            opp = ox.get(f"{prefix}_odds_opp")
            if ln is None and od is None:
                return "—"
            if opp is not None:
                return f"L{ln} O{od:>+5} U{opp:>+5}"
            return f"L{ln} {od}"

        def gpv(g):
            x = gates.get(g) or {}
            if not isinstance(x, dict):
                return "—"
            p = "✅" if x.get("passed") is True else ("❌" if x.get("passed") is False else "—")
            v = x.get("value")
            return f"{p}({v})" if v not in (None, {}) else p

        rows.append({
            "rank": i,
            "player": r.get("player_name"),
            "team": r.get("team") or ox.get("team"),
            "opp": r.get("opponent_team") or ox.get("opponent_team"),
            "stat": r.get("stat_type"),
            "stat_family": r.get("stat_family"),
            "line": r.get("line"),
            "side": side,
            "is_alt": bool(r.get("is_alternate_market") or ox.get("is_alternate_market")),
            "tier_reason": r.get("tier_reason"),
            "ref_odds": r.get("tier_reference_odds"),
            "ref_book": r.get("tier_reference_book"),
            "tp_source": r.get("tp_source"),
            "tp_books": r.get("tp_books_used"),
            "edge_pct": r.get("edge_pct"),
            "tp": r.get("tp"),
            "fair_prob": r.get("fair_prob"),
            "p_true": r.get("p_true_active"),
            "cv": r.get("cv"),
            "hit_rate": r.get("hit_rate_over") if side == "OVER" else r.get("hit_rate_under"),
            "l20_n": r.get("hit_rate_sample_size"),
            "vision": r.get("vision_score"),
            "ceiling_rate": r.get("ceiling_rate"),
            "projection": round(float(proj), 3) if isinstance(proj, (int, float)) else None,
            "model_sigma": r.get("model_sigma"),
            "avg_hit_margin": r.get("avg_hit_margin"),
            "book_count": r.get("book_count"),
            "odds": {
                "dk":  fmt("dk"),
                "fd":  fmt("fd"),
                "mgm": fmt("mgm"),
                "bol": fmt("bol"),
                "pp":  (f"L{ox.get('pp_line')} {ox.get('pp_odds')} layer={ox.get('pp_layer') or '-'}"
                        if ox.get("pp_line") is not None else "—"),
                "sharp": (f"{ox.get('sharp_book')}: L{ox.get('sharp_line')} @ {ox.get('sharp_odds')}"
                          if ox.get("sharp_book") else "—"),
            },
            "context": {
                "is_starter": ox.get("is_starter"),
                "lineup_status": ox.get("lineup_status"),
                "batting_order": ox.get("batting_order_position"),
                "park_factor": ox.get("park_factor"),
                "expected_pa": ox.get("expected_pa"),
                "expected_outs": ox.get("expected_outs_pitched"),
                "is_home": ox.get("is_home"),
                "starting_pitcher": ox.get("starting_pitcher"),
            },
            "gates": {
                "coverage":  gpv("coverage_gate"),
                "cv":        gpv("cv_gate"),
                "hit_rate":  gpv("hit_rate_gate"),
                "tp":        gpv("tp_gate"),
                "edge":      gpv("edge_gate"),
                "vision":    gpv("vision_score_gate"),
                "margin":    gpv("margin_gate"),
                "ceiling":   gpv("ceiling_gate"),
            },
        })

    os.makedirs("/app/backend/data/snapshots", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"/app/backend/data/snapshots/mlb_top20_safe_haven_rejects_{stamp}.json"
    with open(path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sport": "mlb", "version_tag": VERSION,
            "filter": {"safe_haven_band": "<= -300", "exclude_tier": "safe_haven"},
            "sort": ["edge_pct DESC", "vision_score DESC", "canonical_key ASC"],
            "total_routed_to_sh": len(raw),
            "fail_gate_distribution_full_pool": dict(full_failgate),
            "rejects_count": len(rows),
            "rejects": rows,
        }, f, default=str, indent=2)

    # Print compact table
    print()
    print(f"=== MLB Safe Haven Rejects (Top 20 of {len(raw)} SH-routed rejects) ===")
    print(f"version_tag = {VERSION}")
    print(f"Reference: ref_odds ≤ -300, tier ≠ safe_haven")
    print()
    print(f"{'#':>2} {'Player':<22} {'TM/OPP':<10} {'Side':<5} {'Stat':<22} {'L':>4} {'Alt':<3} "
          f"{'RefOdds(book)':<14} {'Edge':>5} {'TP%':>5} {'CV':>6} {'HR':>4} {'Vis':>5} {'Proj':>6} {'Failed Gate':<22}")
    print("-" * 165)
    for r in rows:
        team_opp = f"{(r['team'] or '?')}/{(r['opp'] or '?')}"[:10]
        ref = f"{r['ref_odds']}({r['ref_book']})"
        alt = "Y" if r["is_alt"] else ""
        proj = f"{r['projection']:.2f}" if r["projection"] is not None else "—"
        cv = f"{r['cv']:.3f}" if isinstance(r["cv"], (int, float)) else "—"
        vis = f"{r['vision']:.1f}" if isinstance(r["vision"], (int, float)) else "—"
        gate_short = (r["tier_reason"] or "").replace("safe_haven_failed: ", "").replace("gate_", "").replace("_fail", "")[:22]
        print(f"{r['rank']:>2} {r['player'][:22]:<22} {team_opp:<10} {r['side']:<5} "
              f"{r['stat'][:22]:<22} {str(r['line']):>4} {alt:<3} {ref:<14} "
              f"{r['edge_pct']:>5} {r['tp']:>5} {cv:>6} {r['hit_rate']:>4} {vis:>5} {proj:>6} {gate_short:<22}")

    print()
    print("=== Fail-gate distribution (full SH-routed reject pool, not just top 20) ===")
    for g, n in full_failgate.most_common():
        print(f"  {n:>4}  {g}")

    print()
    print(f"ARTIFACT: {path}")


if __name__ == "__main__":
    asyncio.run(main())
