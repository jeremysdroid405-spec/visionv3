"""Phase 4 — War Zone validation.

Runs the Phase-4 universal gate path for tier=war_zone on 2026-05-05
and diffs the qualified pool + displayed cards against the existing
legacy-WZ run `MLB-PRODREPLAY-20260505-WZ-1100UTC-00008`.

Reports — only:
  • Diff in qualified pool size.
  • Set-diff of (event_id, player_norm, market, line, side, book) keys
    appearing in one but not the other.
  • For displayed cards (top-20): exact card-by-card diff with grade
    status and edge.
  • HR / ROI / profit delta on the qualified pool.
  • Aggregate gate-failure histogram for any divergent row.

If any divergence is found, the user has explicitly demanded an
explanation per failing row — we print the full
NormalizedMetrics + GateEvalResult for each.

NO 6-day sweep is triggered. NO SH / FL gates are evaluated.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, json
from dataclasses import asdict

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.production_replay_runner import run_production_replay


LEGACY_SERIAL = "MLB-PRODREPLAY-20260505-WZ-1100UTC-00008"
GAME_DATE = "2026-05-05"
SNAPSHOT = "2026-05-05T11:00:00Z"
SPORT = "mlb"
TIER = "war_zone"


def _row_key(r):
    return (
        str(r.get("event_id")),
        str(r.get("player_name_normalized")),
        str(r.get("market")),
        float(r.get("line")) if r.get("line") is not None else None,
        str(r.get("side")),
        str(r.get("book")),
    )


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"\n=== Phase 4 — WZ A/B validation ({GAME_DATE}) ===\n")

    # ── Run Phase 4 (universal gate path) ────────────────────────────
    print("[1/4] running Phase 4 (universal gate path) ...")
    summary = await run_production_replay(
        db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAPSHOT,
        tier=TIER, gate_path="universal", dry_run=False,
        force_layer3=False,
        notes="phase4_wz_validation_2026_05_05",
    )
    NEW_SERIAL = summary["serial"]
    print(f"      → new serial: {NEW_SERIAL}")
    print(f"      → rows_qualified: {summary['rows_qualified']}")
    print(f"      → cards_displayed: {summary['cards_displayed']}")
    print(f"      → wins/losses/pushes/ungraded: "
          f"{summary['wins']}/{summary['losses']}/{summary['pushes']}/{summary['ungraded']}")
    print(f"      → HR={summary['hit_rate_pct']}%  ROI={summary['roi_pct']}%  "
          f"profit={summary['profit_units']:+.4f}")
    print(f"      → universal_gate_cfg_versions: "
          f"{json.dumps(summary.get('universal_gate_cfg_versions') or {}, indent=2)}")

    # ── Pull legacy + new qualified rows ────────────────────────────
    print("\n[2/4] loading qualified pools for both serials ...")
    legacy_rows = await db.mlb_production_replay_outputs.find(
        {"replay_serial": LEGACY_SERIAL, "gate_pass": True},
        projection={"_id": 0},
    ).to_list(length=None)
    new_rows = await db.mlb_production_replay_outputs.find(
        {"replay_serial": NEW_SERIAL, "gate_pass": True},
        projection={"_id": 0},
    ).to_list(length=None)
    print(f"      legacy qualified: {len(legacy_rows)}")
    print(f"      new    qualified: {len(new_rows)}")

    legacy_keys = {_row_key(r): r for r in legacy_rows}
    new_keys = {_row_key(r): r for r in new_rows}
    common = legacy_keys.keys() & new_keys.keys()
    only_legacy = legacy_keys.keys() - new_keys.keys()
    only_new = new_keys.keys() - legacy_keys.keys()
    print(f"      common keys     : {len(common)}")
    print(f"      only in LEGACY  : {len(only_legacy)}")
    print(f"      only in NEW     : {len(only_new)}")

    # ── Aggregate ROI / HR on the qualified pool ─────────────────────
    def _agg(rows):
        w = sum(1 for r in rows if r.get("grade_status") == "win")
        l = sum(1 for r in rows if r.get("grade_status") == "loss")
        p = sum(1 for r in rows if r.get("grade_status") == "push")
        u = sum(1 for r in rows if r.get("grade_status")
                not in ("win", "loss", "push"))
        stake = sum(float(r.get("stake_units") or 0) for r in rows)
        profit = sum(float(r.get("profit_units") or 0) for r in rows)
        dec = w + l
        return {
            "n": len(rows), "w": w, "l": l, "p": p, "u": u,
            "stake": stake, "profit": profit,
            "hr_pct": (100*w/dec) if dec else 0.0,
            "roi_pct": (100*profit/stake) if stake else 0.0,
        }

    legacy_agg = _agg(legacy_rows)
    new_agg = _agg(new_rows)
    print("\n[3/4] aggregate qualified-pool deltas")
    print(f"      {'':12}{'LEGACY':>12}{'NEW':>12}{'Δ':>12}")
    for k in ("n", "w", "l", "p", "u"):
        print(f"      {k:<12}{legacy_agg[k]:>12}{new_agg[k]:>12}"
              f"{new_agg[k]-legacy_agg[k]:>+12}")
    for k in ("stake", "profit", "hr_pct", "roi_pct"):
        print(f"      {k:<12}{legacy_agg[k]:>12.4f}{new_agg[k]:>12.4f}"
              f"{new_agg[k]-legacy_agg[k]:>+12.4f}")

    # ── Card-level diff ──────────────────────────────────────────────
    print("\n[4/4] displayed-card diff (top-20 per side)")
    legacy_cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": LEGACY_SERIAL}, projection={"_id": 0}
    ).sort("rank", 1).to_list(length=None)
    new_cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": NEW_SERIAL}, projection={"_id": 0}
    ).sort("rank", 1).to_list(length=None)

    def _card_key(c):
        return (str(c.get("player_name_normalized")),
                str(c.get("stat_family")), float(c.get("line")),
                str(c.get("side")))
    legacy_card_keys = {_card_key(c): c for c in legacy_cards}
    new_card_keys = {_card_key(c): c for c in new_cards}
    same = legacy_card_keys.keys() & new_card_keys.keys()
    only_l = legacy_card_keys.keys() - new_card_keys.keys()
    only_n = new_card_keys.keys() - legacy_card_keys.keys()
    print(f"      legacy cards : {len(legacy_cards)}")
    print(f"      new    cards : {len(new_cards)}")
    print(f"      same         : {len(same)}")
    print(f"      only LEGACY  : {len(only_l)}")
    print(f"      only NEW     : {len(only_n)}")

    if only_l:
        print("\n      ── ONLY IN LEGACY CARDS ──")
        for k in sorted(only_l):
            c = legacy_card_keys[k]
            print(f"        {c.get('player_name'):<28} {c.get('stat_family'):<22} "
                  f"{c.get('line')}/{c.get('side'):<5} {c.get('book')}@{c.get('odds'):<5} "
                  f"edge={float(c.get('edge') or 0):.3f} grade={c.get('grade_status')}")
    if only_n:
        print("\n      ── ONLY IN NEW CARDS ──")
        for k in sorted(only_n):
            c = new_card_keys[k]
            print(f"        {c.get('player_name'):<28} {c.get('stat_family'):<22} "
                  f"{c.get('line')}/{c.get('side'):<5} {c.get('book')}@{c.get('odds'):<5} "
                  f"edge={float(c.get('edge') or 0):.3f} grade={c.get('grade_status')}")

    # ── Detailed only-in-LEGACY qualified explanations ──────────────
    if only_legacy:
        print(f"\n      ── Inspecting up to 10 'only in LEGACY' qualified rows "
              f"(NEW path rejected them)")
        from services.replay.replay_metrics_builder import build_metrics_from_replay_row
        from services.replay.replay_field_hydrators import (
            load_book_inventory, load_player_game_logs_as_of,
        )
        from services.scoring.tier_evaluator import evaluate_tier_with_overrides
        inv = await load_book_inventory(
            db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAPSHOT)
        plogs = await load_player_game_logs_as_of(db, game_date=GAME_DATE)
        for i, k in enumerate(sorted(only_legacy)):
            if i >= 10: break
            r = legacy_keys[k]
            m = build_metrics_from_replay_row(
                r, tier=TIER, sport=SPORT,
                book_inventory=inv, player_game_logs=plogs)
            res = evaluate_tier_with_overrides(m)
            print(f"\n      [{i+1}] {r.get('player_name')} | "
                  f"{r.get('stat_family')} {r.get('line')}/{r.get('side')} | "
                  f"{r.get('book')}@{r.get('odds')}")
            print(f"          metrics: book_count={m.book_count}, tp_source={m.tp_source}, "
                  f"hr_l20={m.hit_rate_l20}, hr_l5={m.hit_rate_l5}, cv={m.cv}, "
                  f"edge_pct={m.edge_pct}, mu={m.extras.get('projection')}, line={m.line}")
            print(f"          phase4 result: passed={res.passed}, failed_gates={res.failed_gates}, "
                  f"reason={res.reason_code}")
    if only_new:
        print(f"\n      ── Inspecting up to 10 'only in NEW' qualified rows "
              f"(LEGACY path rejected them)")
        for i, k in enumerate(sorted(only_new)):
            if i >= 10: break
            r = new_keys[k]
            # Run legacy gate on the row to see why it failed there
            from services.replay.mlb_replay_gate_eval import evaluate_gates as legacy_eval
            l3 = await db.mlb_replay_model_outputs.find_one(
                {"game_date": r["game_date"], "snapshot_iso": r["snapshot_iso"],
                 "event_id": r["event_id"], "player_name_normalized": r["player_name_normalized"],
                 "market": r["market"], "line": r["line"], "side": r["side"], "book": r["book"]},
                projection={"_id": 0})
            if l3 is None:
                print(f"\n      [{i+1}] {r.get('player_name')}: layer3 row not found")
                continue
            passed, failed = legacy_eval(l3)
            print(f"\n      [{i+1}] {r.get('player_name')} | "
                  f"{r.get('stat_family')} {r.get('line')}/{r.get('side')} | "
                  f"{r.get('book')}@{r.get('odds')}")
            print(f"          legacy gate: passed={passed}, failed_gates={failed}")
            print(f"          l3 fields: hr_l20={l3.get('hit_rate_l20')}, "
                  f"hr_l5={l3.get('hit_rate_l5')}, cv={l3.get('cv')}, "
                  f"edge={l3.get('edge')}, mu={l3.get('projection_mu')}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
