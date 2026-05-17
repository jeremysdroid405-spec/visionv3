"""Phase 4 — Loser archetype audit for Safe Haven, 6-day cohort.

Read-only. Pulls every Safe Haven displayed card (all 6 SH serials),
joins back to the output row to recover model fields (TP, CV, edge,
HR_L5/10/20, projection_mu, fair_probability, model_probability,
sigma, implied_probability, etc.), and the Layer-3 doc to recover
handedness/matchup context (bat_side, opp_pitcher_throws, park_factor).

Reports:
  (1) Every losing graded card with the requested 16 fields.
  (2) Aggregations: by stat_family, line, odds_bucket, CV bucket,
      edge bucket, books_count, tp_source, side, handedness/platoon.
  (3) Side-by-side loser vs winner bucket comparison
      (n / HR% / win-rate / sample weight) per dimension.

Goal: surface structural loser archetypes inside SH without globally
tightening gates.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
import asyncio, json
from collections import defaultdict
from typing import Any, Dict, List, Tuple, Optional, Set
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.replay_field_hydrators import load_book_inventory, resolve_book_coverage


# 6-day SH serials
SH_SERIALS = [
    "MLB-PRODREPLAY-20260501-SH-1100UTC-00018",
    "MLB-PRODREPLAY-20260502-SH-1100UTC-00021",
    "MLB-PRODREPLAY-20260503-SH-1100UTC-00024",
    "MLB-PRODREPLAY-20260504-SH-1100UTC-00027",
    "MLB-PRODREPLAY-20260505-SH-1100UTC-00030",
    "MLB-PRODREPLAY-20260506-SH-1100UTC-00033",
]

DATES = ["2026-05-01","2026-05-02","2026-05-03","2026-05-04","2026-05-05","2026-05-06"]
SNAPSHOTS = [f"{d}T11:00:00Z" for d in DATES]


def _bkt_odds(o):
    if o is None: return "_unknown"
    o = int(o)
    if o >= 200: return "plus_high"
    if 100 <= o < 200: return "plus_med"
    if 0 < o < 100: return "plus_low"
    if o == 100: return "even"
    if -110 < o < 0: return "minus_low"
    if -150 < o <= -110: return "minus_med"
    if -250 < o <= -150: return "minus_heavy"
    return "minus_xx"


def _bkt_cv(cv):
    if cv is None: return "_unknown"
    cv = float(cv)
    if cv < 0.4: return "ultra_tight_<0.40"
    if cv < 0.6: return "tight_0.40-0.60"
    if cv < 0.8: return "mid_0.60-0.80"
    if cv < 1.0: return "loose_0.80-1.00"
    return "very_loose_>=1.00"


def _bkt_edge(e):
    if e is None: return "_unknown"
    e = float(e) * 100  # pp
    if e < 5: return "low_<5pp"
    if e < 10: return "med_5-10pp"
    if e < 15: return "high_10-15pp"
    if e < 25: return "very_high_15-25pp"
    return "trap_>=25pp"


def _bkt_books(b):
    if b is None: return "_unknown"
    if b == 1: return "1_book"
    if b == 2: return "2_books"
    if 3 <= b <= 5: return "3-5_books"
    if 6 <= b <= 9: return "6-9_books"
    return "10+_books"


def _bkt_line(line):
    if line is None: return "_unknown"
    line = float(line)
    if line == 0.5: return "0.5"
    if line == 1.5: return "1.5"
    if line == 2.5: return "2.5"
    if line < 1.0:  return f"<1 (={line})"
    if line < 3.0:  return f"1-3 (={line})"
    return f">=3 (={line})"


def _platoon(bat_side: Optional[str], opp_throws: Optional[str]) -> str:
    bs = (bat_side or "").upper()[:1]
    pt = (opp_throws or "").upper()[:1]
    if not bs or not pt:
        return "_unknown"
    if bs == "S": return "switch_vs_" + pt  # switch hitter
    if bs == pt:
        return "platoon_disadv_same_handed"  # L vs L or R vs R
    return "platoon_adv_opposite_handed"


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("\n=== SH 6-day loser-archetype audit ===\n")

    # ── Cards ────────────────────────────────────────────────────────
    cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": SH_SERIALS}}, projection={"_id": 0}
    ).to_list(length=None)
    print(f"SH cards (6 days): {len(cards)}")

    # ── Output rows (model fields) ───────────────────────────────────
    outs = await db.mlb_production_replay_outputs.find(
        {"replay_serial": {"$in": SH_SERIALS}, "gate_pass": True},
        projection={"_id": 0},
    ).to_list(length=None)
    out_idx = {(o["replay_serial"], o["player_name_normalized"],
                o["market"], o["line"], o["side"], o["book"]): o
               for o in outs}

    # ── Layer-3 rows (handedness/matchup) ────────────────────────────
    # Pull by (game_date, snapshot_iso, player_norm, market, line, side, book).
    # Build query OR by serial → date+snap mapping.
    serial_to_date = {s: f"2026-05-0{i+1}" for i, s in enumerate(SH_SERIALS)}
    l3_idx: Dict[Tuple, Dict[str, Any]] = {}
    # Faster: load only the unique (date,player,market,line,side,book) needed
    need_keys = set()
    for c in cards:
        sr = c["replay_serial"]
        d = serial_to_date[sr]
        need_keys.add((d, c["player_name_normalized"], c["market"],
                        c["line"], c["side"], c["book"]))
    # Single $or query
    or_list = [{"game_date": d, "player_name_normalized": pn,
                 "market": m, "line": ln, "side": sd, "book": bk}
                for (d, pn, m, ln, sd, bk) in need_keys]
    if or_list:
        cursor = db.mlb_replay_model_outputs.find(
            {"$or": or_list},
            projection={"_id": 0, "game_date": 1, "player_name_normalized": 1,
                         "market": 1, "line": 1, "side": 1, "book": 1,
                         "bat_side": 1, "park_factor": 1, "is_away_team": 1,
                         "opponent": 1, "team": 1, "raw_prediction": 1},
        )
        async for r in cursor:
            k = (r["game_date"], r["player_name_normalized"], r["market"],
                 r["line"], r["side"], r["book"])
            l3_idx[k] = r
    print(f"Layer-3 rows joined: {len(l3_idx)}")

    # ── Feature cache (opp pitcher handedness) ───────────────────────
    # Source: mlb_replay_feature_cache keyed on (game_date, player_name_normalized,
    # market or stat_family).
    fc_idx: Dict[Tuple, Dict[str, Any]] = {}
    fc_keys = {(c["replay_serial"], c["player_name_normalized"]) for c in cards}
    pn_set = {pn for (_, pn) in fc_keys}
    date_set = set(serial_to_date.values())
    if pn_set:
        cursor = db.mlb_replay_feature_cache.find(
            {"game_date": {"$in": list(date_set)},
             "player_name_normalized": {"$in": list(pn_set)}},
            projection={"_id": 0, "game_date": 1, "player_name_normalized": 1,
                         "stat_family": 1, "opp_pitcher_throws": 1,
                         "opp_pitcher_name": 1, "bat_side": 1,
                         "position": 1, "throws": 1, "is_pitcher": 1},
        )
        async for r in cursor:
            # Key by (date, player). Multiple stat_families per player —
            # keep the first non-None opp_pitcher_throws.
            k = (r["game_date"], r["player_name_normalized"])
            cur = fc_idx.get(k)
            if cur is None:
                fc_idx[k] = r
            else:
                # Prefer entries with non-null opp_pitcher_throws
                if cur.get("opp_pitcher_throws") is None and r.get("opp_pitcher_throws") is not None:
                    fc_idx[k] = r
    print(f"Feature-cache rows joined: {len(fc_idx)}")

    # ── Book inventory per date (for books_count + tp_source) ───────
    inv_by_date: Dict[str, Dict[Tuple, Dict[str, Set[str]]]] = {}
    for d, snap in zip(DATES, SNAPSHOTS):
        inv_by_date[d] = await load_book_inventory(
            db, sport="mlb", game_date=d, snapshot_iso=snap)
    print(f"Book inventories loaded for {len(inv_by_date)} dates")

    # ── Enrich every SH card ─────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    for c in cards:
        sr = c["replay_serial"]
        d = serial_to_date[sr]
        ok = (sr, c["player_name_normalized"], c["market"], c["line"],
              c["side"], c["book"])
        o = out_idx.get(ok, {})
        l3 = l3_idx.get((d, c["player_name_normalized"], c["market"],
                          c["line"], c["side"], c["book"]), {})
        fc = fc_idx.get((d, c["player_name_normalized"]), {})
        # books_count + tp_source from snapshot inventory
        bc, tp_src = resolve_book_coverage(
            inv_by_date[d],
            event_id=o.get("event_id") or "",
            player_norm=c["player_name_normalized"],
            market=c["market"], line=float(c["line"]),
            side=(c["side"] or "OVER").upper(),
        )
        mu = c.get("projection_mu") or o.get("projection_mu")
        delta = None
        if mu is not None and c.get("line") is not None:
            delta = round(float(mu) - float(c["line"]), 3)
        # Handedness sources (prefer l3.bat_side then fc.bat_side)
        bat_side = (l3.get("bat_side") or fc.get("bat_side")
                     or fc.get("throws") if fc.get("is_pitcher") else None)
        opp_throws = fc.get("opp_pitcher_throws")
        rows.append({
            "serial": sr, "rank": c.get("rank"), "date": d,
            "player": c.get("player_name"),
            "player_norm": c.get("player_name_normalized"),
            "stat_family": c.get("stat_family"),
            "market": c.get("market"),
            "line": c.get("line"),
            "side": c.get("side"),
            "odds": c.get("odds"),
            "book": c.get("book"),
            "is_alt": c.get("is_alternate"),
            "tp_pct": round(float(o.get("fair_probability") or 0) * 100, 3)
                       if o.get("fair_probability") is not None else None,
            "tp_source": tp_src,
            "books_count": bc,
            "edge_pct": round(float(o.get("edge") or 0) * 100, 3)
                         if o.get("edge") is not None else None,
            "model_p_pct": round(float(o.get("model_probability") or 0) * 100, 3)
                           if o.get("model_probability") is not None else None,
            "implied_p_pct": round(float(o.get("implied_probability") or 0) * 100, 3)
                              if o.get("implied_probability") is not None else None,
            "cv": round(float(o.get("cv")), 4) if o.get("cv") is not None else None,
            "sigma": round(float(o.get("sigma")), 4) if o.get("sigma") is not None else None,
            "hr_l5":  o.get("hit_rate_l5"),
            "hr_l10": o.get("hit_rate_l10"),
            "hr_l20": o.get("hit_rate_l20"),
            "projection_mu": round(float(mu), 4) if mu is not None else None,
            "delta_vs_line": delta,
            "bat_side": bat_side,
            "opp_pitcher_throws": opp_throws,
            "platoon": _platoon(bat_side, opp_throws),
            "park_factor": l3.get("park_factor"),
            "is_away": l3.get("is_away_team"),
            "grade_status": c.get("grade_status"),
            "actual": c.get("actual_value"),
            "stake": c.get("stake_units"), "profit": c.get("profit_units"),
        })

    losers = [r for r in rows if r["grade_status"] == "loss"]
    winners = [r for r in rows if r["grade_status"] == "win"]
    pushes = [r for r in rows if r["grade_status"] == "push"]
    print(f"\nSH cards   : total={len(rows)}  W={len(winners)}  L={len(losers)}  P={len(pushes)}")

    # ── (1) Every losing graded card ─────────────────────────────────
    print(f"\n{'='*120}\n  (1) EVERY LOSING SH CARD ({len(losers)} cards)\n{'='*120}\n")
    hdr = ("date", "player", "stat", "line", "side", "odds", "book",
           "edge", "TP", "CV",
           "L5/10/20", "μ", "Δμ", "books", "tp_src", "platoon",
           "actual")
    print(f"  {hdr[0]:<12} {hdr[1]:<24} {hdr[2]:<22} {hdr[3]:>4} {hdr[4]:<5} "
          f"{hdr[5]:>5} {hdr[6]:<13} {hdr[7]:>7} {hdr[8]:>7} {hdr[9]:>7} "
          f"{hdr[10]:<17} {hdr[11]:>6} {hdr[12]:>6} {hdr[13]:>5} "
          f"{hdr[14]:<10} {hdr[15]:<28} {hdr[16]:>6}")
    for r in sorted(losers, key=lambda x: (x["date"], x["player"])):
        print(f"  {r['date']:<12} {(r['player'] or '')[:24]:<24} "
              f"{(r['stat_family'] or '')[:22]:<22} {r['line']:>4} {r['side']:<5} "
              f"{r['odds']:>5} {(r['book'] or '')[:13]:<13} "
              f"{(str(r['edge_pct'])+'%' if r['edge_pct'] is not None else '_'):>7} "
              f"{(str(r['tp_pct'])+'%' if r['tp_pct'] is not None else '_'):>7} "
              f"{r['cv'] if r['cv'] is not None else '_':>7} "
              f"{(str(r['hr_l5'])+'/'+str(r['hr_l10'])+'/'+str(r['hr_l20'])):<17} "
              f"{r['projection_mu'] if r['projection_mu'] is not None else '_':>6} "
              f"{r['delta_vs_line'] if r['delta_vs_line'] is not None else '_':>6} "
              f"{r['books_count'] if r['books_count'] is not None else '_':>5} "
              f"{(r['tp_source'] or '_')[:10]:<10} "
              f"{r['platoon']:<28} "
              f"{r['actual'] if r['actual'] is not None else '_':>6}")

    # ── (2)+(3) Aggregations + winner-comparison ─────────────────────
    def _aggregate(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, float]]:
        agg: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"n":0,"w":0,"l":0,"p":0,"stake":0.0,"profit":0.0})
        for r in rows:
            k = key_fn(r)
            e = agg[k]
            e["n"] += 1
            st = r["grade_status"]
            if st == "win": e["w"] += 1
            elif st == "loss": e["l"] += 1
            elif st == "push": e["p"] += 1
            e["stake"] += float(r.get("stake") or 0)
            e["profit"] += float(r.get("profit") or 0)
        # Add derived metrics
        for k, e in agg.items():
            dec = e["w"] + e["l"]
            e["hr_pct"] = round(100*e["w"]/dec, 2) if dec else None
            e["roi_pct"] = round(100*e["profit"]/e["stake"], 2) if e["stake"] else None
        return agg

    dims = [
        ("stat_family", lambda r: r["stat_family"] or "_unknown"),
        ("line", lambda r: _bkt_line(r["line"])),
        ("odds_bucket", lambda r: _bkt_odds(r["odds"])),
        ("cv_bucket", lambda r: _bkt_cv(r["cv"])),
        ("edge_bucket", lambda r: _bkt_edge((r["edge_pct"] or 0)/100 if r["edge_pct"] is not None else None)),
        ("books_count_bucket", lambda r: _bkt_books(r["books_count"])),
        ("tp_source", lambda r: r["tp_source"] or "_unknown"),
        ("side", lambda r: r["side"] or "_unknown"),
        ("platoon", lambda r: r["platoon"]),
        ("is_alt", lambda r: "alt" if r["is_alt"] else "standard"),
    ]

    print(f"\n{'='*120}\n  (2)+(3) PER-BUCKET WIN/LOSS BREAKDOWN — losers vs winners\n{'='*120}")
    for name, fn in dims:
        all_agg = _aggregate(rows, fn)
        print(f"\n──── DIMENSION: {name}")
        print(f"  {name:<32}{'n':>5}{'W':>5}{'L':>5}{'P':>4}{'HR%':>9}{'ROI%':>9}{'profit':>10}")
        # Order by loss count descending (highlight loser archetypes)
        order = sorted(all_agg.items(), key=lambda kv: (-kv[1]["l"], -kv[1]["n"]))
        for k, e in order:
            hr = f"{e['hr_pct']:.2f}%" if e["hr_pct"] is not None else "—"
            roi = f"{e['roi_pct']:+.2f}%" if e["roi_pct"] is not None else "—"
            print(f"  {str(k)[:32]:<32}{e['n']:>5}{e['w']:>5}{e['l']:>5}{e['p']:>4}"
                  f"{hr:>9}{roi:>9}{e['profit']:>+10.4f}")

    # ── Persist JSON artifact ───────────────────────────────────────
    art = "/app/backend/audits/phase4_sh_loser_archetypes_2026_05_17.json"
    out = {
        "serials": SH_SERIALS,
        "totals": {"n": len(rows), "w": len(winners), "l": len(losers), "p": len(pushes)},
        "losers": losers,
        "winners_summary_count": len(winners),
        "aggregates": {
            name: {str(k): v for k, v in _aggregate(rows, fn).items()}
            for (name, fn) in dims
        },
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
