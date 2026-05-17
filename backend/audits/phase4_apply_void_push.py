"""Phase 4 — Apply MLB industry-standard void→push rule to the 20
remaining ungraded cards in the 6-day × 3-tier cohort.

Per user directive (2026-05-17): bucket A (DNP — game played but
carded player not in box) AND bucket B (PPD — neither team logged
the slate) both resolve to industry-standard `push` (stake refund,
$0 P&L).

Re-uses the join + teammate-evidence logic so each pushed card is
tagged with its inferred reason. No model / gate / card-identity
changes.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
import asyncio, json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne


SERIALS = [
  'MLB-PRODREPLAY-20260501-SH-1100UTC-00018','MLB-PRODREPLAY-20260501-FL-1100UTC-00019','MLB-PRODREPLAY-20260501-WZ-1100UTC-00020',
  'MLB-PRODREPLAY-20260502-SH-1100UTC-00021','MLB-PRODREPLAY-20260502-FL-1100UTC-00022','MLB-PRODREPLAY-20260502-WZ-1100UTC-00023',
  'MLB-PRODREPLAY-20260503-SH-1100UTC-00024','MLB-PRODREPLAY-20260503-FL-1100UTC-00025','MLB-PRODREPLAY-20260503-WZ-1100UTC-00026',
  'MLB-PRODREPLAY-20260504-SH-1100UTC-00027','MLB-PRODREPLAY-20260504-FL-1100UTC-00028','MLB-PRODREPLAY-20260504-WZ-1100UTC-00029',
  'MLB-PRODREPLAY-20260505-SH-1100UTC-00030','MLB-PRODREPLAY-20260505-FL-1100UTC-00031','MLB-PRODREPLAY-20260505-WZ-1100UTC-00032',
  'MLB-PRODREPLAY-20260506-SH-1100UTC-00033','MLB-PRODREPLAY-20260506-FL-1100UTC-00034','MLB-PRODREPLAY-20260506-WZ-1100UTC-00035',
]


def _parse(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("\n=== Phase 4 — Apply void→push to remaining 20 ungraded ===\n")

    # Pull all ungraded cards in the cohort
    ungraded = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": SERIALS},
         "grade_status": {"$nin": ["win", "loss", "push"]}},
        projection={"_id": 0},
    ).to_list(length=None)
    print(f"ungraded cards in cohort: {len(ungraded)}")

    # Pull matching output rows once (we need home/away/commence_time)
    keys_needed = {(c["replay_serial"], c["player_name_normalized"],
                    c["market"], c["line"], c["side"], c["book"])
                   for c in ungraded}
    out_rows = await db.mlb_production_replay_outputs.find(
        {"replay_serial": {"$in": SERIALS}, "gate_pass": True},
        projection={"_id": 0, "replay_serial": 1, "player_name_normalized": 1,
                     "market": 1, "line": 1, "side": 1, "book": 1,
                     "event_id": 1, "commence_time": 1, "game_date": 1,
                     "home_team": 1, "away_team": 1},
    ).to_list(length=None)
    by_key = {(r["replay_serial"], r["player_name_normalized"],
               r["market"], r["line"], r["side"], r["book"]): r
              for r in out_rows}

    # For each ungraded card, classify as bucket A (DNP) vs bucket B
    # (PPD) via teammate-log presence within ±18h of commence_time.
    bulk: List[UpdateOne] = []
    detail: List[Dict[str, Any]] = []
    for c in ungraded:
        k = (c["replay_serial"], c["player_name_normalized"],
             c["market"], c["line"], c["side"], c["book"])
        r = by_key.get(k)
        if r is None:
            reason = "void_unknown_no_output_row"
        else:
            ct = _parse(r.get("commence_time"))
            d0 = datetime.strptime(r["game_date"], "%Y-%m-%d").date()
            window = {(d0-timedelta(days=1)).isoformat(),
                       d0.isoformat(),
                       (d0+timedelta(days=1)).isoformat()}
            evidence = None
            for team in (r.get("home_team"), r.get("away_team")):
                if not team: continue
                hub = await db.mlb_master_hub_2026.find_one(
                    {"bdl_game_logs": {"$elemMatch": {
                        "team_name": team,
                    }}}, projection={"_id": 0, "player_name": 1,
                                     "bdl_game_logs": 1})
                if not hub: continue
                # Scan that hub's logs for an entry on the team within window
                for lg in (hub.get("bdl_game_logs") or []):
                    if lg.get("team_name") != team: continue
                    dp = (lg.get("date") or "")[:10]
                    if dp not in window: continue
                    lt = _parse(lg.get("date"))
                    if lt is None or ct is None: continue
                    if abs((lt - ct).total_seconds())/3600 <= 18:
                        evidence = (hub["player_name"], lg.get("date"))
                        break
                if evidence: break
            reason = ("void_dnp_player_scratched" if evidence
                       else "void_postponed_or_no_slate_data")
        bulk.append(UpdateOne(
            {"replay_serial": c["replay_serial"], "rank": c.get("rank")},
            {"$set": {
                "grade_status": "push",
                "actual_value": None,
                "profit_units": 0.0,
                "stake_units": 1.0,
                "regrade_method": "phase4_void_push_v1_2026_05_17",
                "regrade_reason": reason,
            }},
        ))
        detail.append({
            "replay_serial": c["replay_serial"],
            "rank": c.get("rank"),
            "player_name": c.get("player_name"),
            "market": c.get("market"),
            "line": c.get("line"), "side": c.get("side"),
            "book": c.get("book"), "odds": c.get("odds"),
            "stat_family": c.get("stat_family"),
            "game_date": (r or {}).get("game_date"),
            "commence_time": (r or {}).get("commence_time"),
            "home_team": (r or {}).get("home_team"),
            "away_team": (r or {}).get("away_team"),
            "void_reason": reason,
            "teammate_evidence": (evidence if reason
                                    == "void_dnp_player_scratched" else None),
        })

    if bulk:
        res = await db.mlb_production_replay_cards.bulk_write(
            bulk, ordered=False)
        print(f"bulk write: matched={res.matched_count} "
              f"modified={res.modified_count}")

    # Final aggregates
    cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": SERIALS}}, projection={"_id": 0}
    ).to_list(length=None)
    w = sum(1 for c in cards if c.get("grade_status") == "win")
    l = sum(1 for c in cards if c.get("grade_status") == "loss")
    p = sum(1 for c in cards if c.get("grade_status") == "push")
    u = sum(1 for c in cards if c.get("grade_status")
            not in ("win", "loss", "push"))
    stake = sum(float(c.get("stake_units") or 0) for c in cards)
    profit = sum(float(c.get("profit_units") or 0) for c in cards)
    dec = w + l
    hr = (100*w/dec) if dec else 0.0
    roi = (100*profit/stake) if stake else 0.0
    print(f"\n──── FINAL (after void→push)")
    print(f"  cards   : {len(cards)}")
    print(f"  W/L/P/U : {w}/{l}/{p}/{u}")
    print(f"  stake   : ${stake:.2f}")
    print(f"  profit  : ${profit:+.4f}")
    print(f"  HR      : {hr:.4f}%   (W/L denominator)")
    print(f"  ROI     : {roi:+.4f}%   (profit / stake)")
    print(f"  ungraded%: {100*u/len(cards):.4f}%")

    # Per-tier and per-date summaries
    by_tier = {"safe_haven":{}, "front_lines":{}, "war_zone":{}}
    by_date = {}
    for c in cards:
        # tier from serial (-SH-/-FL-/-WZ-)
        sr = c["replay_serial"]
        if "-SH-" in sr: t = "safe_haven"
        elif "-FL-" in sr: t = "front_lines"
        elif "-WZ-" in sr: t = "war_zone"
        else: t = "unknown"
        date_part = sr.split("-")[2]  # YYYYMMDD
        d_iso = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
        for bucket in (by_tier[t], by_date.setdefault(d_iso, {})):
            st = c.get("grade_status")
            bucket["n"] = bucket.get("n", 0) + 1
            bucket["w"] = bucket.get("w", 0) + (1 if st == "win" else 0)
            bucket["l"] = bucket.get("l", 0) + (1 if st == "loss" else 0)
            bucket["p"] = bucket.get("p", 0) + (1 if st == "push" else 0)
            bucket["u"] = bucket.get("u", 0) + (1 if st not in ("win","loss","push") else 0)
            bucket["stake"] = bucket.get("stake", 0.0) + float(c.get("stake_units") or 0)
            bucket["profit"] = bucket.get("profit", 0.0) + float(c.get("profit_units") or 0)

    print(f"\n──── PER-TIER FINAL")
    print(f"  {'tier':<13}{'n':>5}{'W':>4}{'L':>4}{'P':>4}{'U':>4}"
          f"{'stake':>9}{'profit':>10}{'HR%':>8}{'ROI%':>8}")
    for t in ("safe_haven","front_lines","war_zone"):
        e = by_tier[t]
        dec = e["w"]+e["l"]
        hr_t = (100*e["w"]/dec) if dec else 0.0
        roi_t = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {t:<13}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['p']:>4}{e['u']:>4}"
              f"{e['stake']:>9.2f}{e['profit']:>+10.4f}{hr_t:>7.2f}%{roi_t:>+7.2f}%")

    print(f"\n──── PER-DATE FINAL")
    print(f"  {'date':>11}{'n':>5}{'W':>4}{'L':>4}{'P':>4}{'U':>4}"
          f"{'stake':>9}{'profit':>10}{'HR%':>8}{'ROI%':>8}")
    for d in sorted(by_date):
        e = by_date[d]
        dec = e["w"]+e["l"]
        hr_d = (100*e["w"]/dec) if dec else 0.0
        roi_d = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {d:>11}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['p']:>4}{e['u']:>4}"
              f"{e['stake']:>9.2f}{e['profit']:>+10.4f}{hr_d:>7.2f}%{roi_d:>+7.2f}%")

    # Persist artifact (cumulative; supersedes the previous one)
    art = "/app/backend/audits/phase4_regrade_6day_final_2026_05_17.json"
    out = {
        "regrade_method": "phase4_regrade_v1+void_push_v1_2026_05_17",
        "serials": SERIALS,
        "void_push_applied": len(bulk),
        "void_push_detail": detail,
        "final_aggregate": {
            "n": len(cards), "w": w, "l": l, "p": p, "u": u,
            "stake": stake, "profit": profit,
            "hr_pct": round(hr, 4), "roi_pct": round(roi, 4),
            "ungraded_pct": round(100*u/len(cards), 4),
        },
        "by_tier": by_tier, "by_date": by_date,
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
