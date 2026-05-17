"""Phase 5c — Read-only SHADOW replay reconstruction for SH on 2026-05-05.

Builds an ephemeral in-memory `shadow_book_inventory` that mimics the
LIVE `universal_odds_sync.py:1403-1425` opposite-side cross-stamping:
within a single book, treat the standard market and its `_alternate`
twin as ONE source of book pricing per (event, player, stat_root, line)
— so a book that quoted alt-OVER and std-UNDER for the same prop is
now correctly marked as "paired" / devig-capable.

Then re-evaluates the Phase 4b SH path FOR THE SAME DATE under both
inventories and reports the delta.

NO writes. NO patches. NO threshold/gate/routing change.
Optional output: one audit JSON.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
import asyncio, json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.replay_field_hydrators import (
    load_book_inventory, load_player_game_logs_as_of,
    resolve_canonical_stat_family,
)
from services.replay.replay_metrics_builder import build_metrics_from_replay_row
from services.replay.reference_odds_loader import load_reference_odds_for_snapshot
from services.replay.providers.mlb_adapter import _resolve_mlb_family, _MLB_STAT_FIELD_MAP
from services.replay.mlb_feature_cache import normalize_player_name
from services.scoring.odds_bucket_router import get_odds_bucket
from services.scoring.tier_evaluator import evaluate_tier_with_overrides
from services.picks.card_builder import (
    build_production_cards, DEFAULT_DEDUPE_KEYS, DEFAULT_ORDER_BY,
    DEFAULT_SLATE_TOP_K, DEFAULT_PER_GAME_TOP_N,
)


GAME_DATE = "2026-05-05"
SNAP = "2026-05-05T11:00:00Z"
SPORT = "mlb"
TIER = "safe_haven"


# ── Shadow cross-stamping ─────────────────────────────────────────────
def _market_root(market: str) -> str:
    """Strip `_alternate` suffix to canonicalize std+alt as one stat."""
    m = (market or "").lower().strip()
    return m[:-len("_alternate")] if m.endswith("_alternate") else m


async def load_shadow_book_inventory(
    db, *, sport: str, game_date: str, snapshot_iso: str,
) -> Tuple[
    Dict[Tuple[str, str, str, float], Dict[str, Set[str]]],
    Dict[Tuple[str, str, str, float], Dict[str, Set[str]]],
    Dict[Tuple[str, str, str, float], Dict[str, Set[str]]],
]:
    """Returns (orig_inv_per_market, shadow_inv_per_market, bridge_inv_per_root).

    orig_inv:  current production behaviour. Key is (event, player,
               market, line). Std and alt are separate keys.
    bridge_inv: shadow — key is (event, player, market_root, line)
               where market_root strips `_alternate`. Per book, both
               OVER and UNDER from EITHER market (std or alt) at the
               same line and player are unioned.
    shadow_inv: per-market view that DELEGATES coverage queries to
               the bridge. We materialize it so the existing
               `resolve_book_coverage(key=(…, market, …))` API works
               without modification — by mapping each market key to
               the SAME underlying bridge bucket.
    """
    coll = "mlb_historical_alt_odds_raw" if sport == "mlb" else None
    assert coll, f"shadow inventory not implemented for sport={sport!r}"
    cursor = db[coll].find(
        {"sport": sport, "game_date": game_date,
         "snapshot_iso": snapshot_iso},
        projection={"_id": 0, "event_id": 1, "player_name_normalized": 1,
                     "market": 1, "line": 1, "side": 1, "book": 1},
    )
    orig: Dict[Tuple, Dict[str, Set[str]]] = {}
    bridge: Dict[Tuple, Dict[str, Set[str]]] = {}
    markets_per_root: Dict[Tuple, Set[str]] = defaultdict(set)
    async for r in cursor:
        line = r.get("line")
        side = (r.get("side") or "").upper()
        book = (r.get("book") or "").strip().lower()
        market = (r.get("market") or "").strip().lower()
        if line is None or side not in ("OVER", "UNDER") or not book:
            continue
        k_orig = (str(r["event_id"]),
                   str(r["player_name_normalized"]),
                   market, float(line))
        b_orig = orig.setdefault(k_orig, {"OVER": set(), "UNDER": set()})
        b_orig[side].add(book)
        # Bridge key uses the market root (std/alt unified).
        root = _market_root(market)
        k_bridge = (str(r["event_id"]),
                     str(r["player_name_normalized"]),
                     root, float(line))
        b_bridge = bridge.setdefault(k_bridge, {"OVER": set(), "UNDER": set()})
        b_bridge[side].add(book)
        markets_per_root[k_bridge].add(market)

    # Materialize shadow_inv as a per-market view that points back at
    # the SAME bridge bucket — so `resolve_book_coverage(market=…)` works
    # unchanged. Both `batter_hits` and `batter_hits_alternate` keys
    # resolve to the union bucket.
    shadow: Dict[Tuple, Dict[str, Set[str]]] = {}
    for k_bridge, bucket in bridge.items():
        ev, pn, root, ln = k_bridge
        markets = markets_per_root[k_bridge]
        for m in markets:
            shadow[(ev, pn, m, ln)] = bucket
    return orig, shadow, bridge


# ── Layer-3 row loader (input population for runner) ─────────────────
async def load_layer3_rows(db, *, game_date, snapshot_iso, sport):
    cursor = db.mlb_replay_model_outputs.find(
        {"sport": sport, "game_date": game_date, "snapshot_iso": snapshot_iso},
        projection={"_id": 0},
    )
    return [r async for r in cursor]


# ── Single-row evaluation under a given inventory ────────────────────
def evaluate_row(row, *, inventory, player_game_logs, ref_odds_map, tier):
    """Returns (gate_pass, failed_gates, metrics, ref_odds, routed_tier).
    Routing is applied first (universal odds-bucket router)."""
    ref_key = (
        str(row.get("event_id")),
        str(row.get("player_name_normalized")),
        str(row.get("market")),
        float(row.get("line")) if row.get("line") is not None else None,
        (row.get("side") or "OVER").upper(),
    )
    ref_pair = ref_odds_map.get(ref_key)
    ref_odds = ref_pair[0] if ref_pair else None
    routed = get_odds_bucket(ref_odds)
    if routed != tier:
        return False, ["tier_odds_bucket_fail"], None, ref_odds, routed
    metrics = build_metrics_from_replay_row(
        row, tier=tier, sport=SPORT,
        book_inventory=inventory, player_game_logs=player_game_logs,
    )
    res = evaluate_tier_with_overrides(metrics)
    return res.passed, list(res.failed_gates), metrics, ref_odds, routed


# ── BDL grade for shadow-qualified cards ─────────────────────────────
def _parse(s):
    return datetime.fromisoformat(s.replace("Z","+00:00")) if s else None


def _best_log_for(logs, *, game_date, commence_time):
    if not logs: return None
    try: d0 = datetime.strptime(game_date, "%Y-%m-%d").date()
    except Exception: return None
    window = {(d0-timedelta(days=1)).isoformat(),
               d0.isoformat(), (d0+timedelta(days=1)).isoformat()}
    ct = _parse(commence_time)
    cands = []
    for g in logs:
        dp = (g.get("date") or "")[:10]
        if dp not in window: continue
        if ct is None:
            if dp == d0.isoformat(): cands.append((0.0, g))
            continue
        lt = _parse(g.get("date") or g.get("game_date"))
        if lt is None: continue
        dt = abs((lt-ct).total_seconds())/3600.0
        if dt > 18.0: continue
        cands.append((dt, g))
    if not cands: return None
    cands.sort(key=lambda kv: kv[0])
    return cands[0][1]


def _grade(actual, *, line, side, odds):
    if actual is None: return ("push", 0.0, 1.0, None)  # void→push
    payout = (odds/100.0) if odds > 0 else (100.0/-odds)
    if side == "OVER":
        if actual > line: return ("win", payout, 1.0, actual)
        if actual < line: return ("loss", -1.0, 1.0, actual)
        return ("push", 0.0, 1.0, actual)
    if actual < line: return ("win", payout, 1.0, actual)
    if actual > line: return ("loss", -1.0, 1.0, actual)
    return ("push", 0.0, 1.0, actual)


def _resolve_actual(stats, fam):
    if fam == "hits_runs_rbis":
        return None if any(stats.get(k) is None for k in ("hits","runs","rbis")) else (
            sum(stats.get(k, 0) for k in ("hits","runs","rbis")))
    field = _MLB_STAT_FIELD_MAP.get(fam)
    if field is None: return None
    v = stats.get(field)
    if v is None:
        if field == "pitcher_outs":
            ip = stats.get("innings_pitched")
            return float(ip)*3.0 if ip is not None else None
        return None
    try: return float(v)
    except Exception: return None


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    print(f"\n=== Phase 5c — SHADOW replay parity for SH ({GAME_DATE} {SNAP}) ===\n")

    # 1. Load inputs
    print("[1/5] loading layer-3 rows, inventories, ref_odds, game logs ...")
    rows = await load_layer3_rows(db, game_date=GAME_DATE, snapshot_iso=SNAP, sport=SPORT)
    print(f"   layer-3 rows: {len(rows):,}")

    orig_inv, shadow_inv, bridge_inv = await load_shadow_book_inventory(
        db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAP)
    print(f"   orig book_inventory keys (per-market): {len(orig_inv):,}")
    print(f"   shadow book_inventory keys (per-market view): {len(shadow_inv):,}")
    print(f"   bridge book_inventory keys (per-stat-root): {len(bridge_inv):,}")

    plogs = await load_player_game_logs_as_of(db, game_date=GAME_DATE)
    print(f"   player_game_logs: {len(plogs):,} players")
    ref_odds_map = await load_reference_odds_for_snapshot(
        db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAP)
    print(f"   ref_odds_map: {len(ref_odds_map):,} (prop,side) keys")

    # 2. Evaluate every row under both inventories. Tier=safe_haven.
    print(f"\n[2/5] evaluating {len(rows):,} rows under both inventories ...")
    orig_summary = {"routed_in": 0, "qualified": 0,
                     "tp_source_gate_fail": 0,
                     "tp_source": Counter(), "failed_gates_first": Counter()}
    shadow_summary = {"routed_in": 0, "qualified": 0,
                       "tp_source_gate_fail": 0,
                       "tp_source": Counter(), "failed_gates_first": Counter()}
    orig_qualified_rows: List[Dict[str, Any]] = []
    shadow_qualified_rows: List[Dict[str, Any]] = []
    flips_one_sided_to_devig: List[Dict[str, Any]] = []
    flips_rejected_to_qualified: List[Dict[str, Any]] = []
    for row in rows:
        ok_orig, fails_orig, m_orig, ref_o, routed_o = evaluate_row(
            row, inventory=orig_inv, player_game_logs=plogs,
            ref_odds_map=ref_odds_map, tier=TIER)
        ok_shadow, fails_shadow, m_shadow, _, _ = evaluate_row(
            row, inventory=shadow_inv, player_game_logs=plogs,
            ref_odds_map=ref_odds_map, tier=TIER)
        if routed_o == TIER:
            orig_summary["routed_in"] += 1
            shadow_summary["routed_in"] += 1
            if m_orig is not None:
                orig_summary["tp_source"][m_orig.tp_source or "_none"] += 1
            if m_shadow is not None:
                shadow_summary["tp_source"][m_shadow.tp_source or "_none"] += 1
            if "tp_source_gate" in fails_orig:
                orig_summary["tp_source_gate_fail"] += 1
            if "tp_source_gate" in fails_shadow:
                shadow_summary["tp_source_gate_fail"] += 1
            if fails_orig: orig_summary["failed_gates_first"][fails_orig[0]] += 1
            if fails_shadow: shadow_summary["failed_gates_first"][fails_shadow[0]] += 1
            if ok_orig:
                orig_summary["qualified"] += 1
                orig_qualified_rows.append(row)
            if ok_shadow:
                shadow_summary["qualified"] += 1
                shadow_qualified_rows.append(row)
            # Track flips
            if (m_orig is not None and m_shadow is not None
                    and m_orig.tp_source == "one_sided"
                    and m_shadow.tp_source == "devig"):
                flips_one_sided_to_devig.append({
                    "player": row.get("player_name"),
                    "market": row.get("market"), "line": row.get("line"),
                    "side": row.get("side"), "book": row.get("book"),
                    "odds": row.get("odds"), "ref_odds": ref_o,
                    "orig_book_count": m_orig.book_count,
                    "shadow_book_count": m_shadow.book_count,
                })
            if not ok_orig and ok_shadow:
                flips_rejected_to_qualified.append({
                    "player": row.get("player_name"),
                    "market": row.get("market"), "line": row.get("line"),
                    "side": row.get("side"), "book": row.get("book"),
                    "odds": row.get("odds"), "ref_odds": ref_o,
                    "orig_first_fail": fails_orig[0] if fails_orig else None,
                    "orig_tp_source": m_orig.tp_source if m_orig else None,
                    "shadow_tp_source": m_shadow.tp_source if m_shadow else None,
                })

    # 3. Build cards under shadow qualified pool
    print(f"\n[3/5] building displayed cards from {len(shadow_qualified_rows)} shadow-qualified rows ...")
    # `build_production_cards` needs row dicts with the same shape as
    # `mlb_production_replay_outputs`. The layer-3 row already carries
    # `edge`, `fair_probability`, `model_probability`, etc. Pass through.
    shadow_qual_with_pass = [{**r, "gate_pass": True} for r in shadow_qualified_rows]
    orig_qual_with_pass = [{**r, "gate_pass": True} for r in orig_qualified_rows]
    shadow_cards = build_production_cards(
        shadow_qual_with_pass,
        sport=SPORT, tier=TIER,
        replay_serial="SHADOW-SH-20260505-1100Z",
        dedupe_keys=DEFAULT_DEDUPE_KEYS,
        order_by=DEFAULT_ORDER_BY,
        slate_top_k=DEFAULT_SLATE_TOP_K,
        per_game_top_n_value=DEFAULT_PER_GAME_TOP_N,
    )
    orig_cards = build_production_cards(
        orig_qual_with_pass,
        sport=SPORT, tier=TIER,
        replay_serial="ORIG-SH-20260505-1100Z",
        dedupe_keys=DEFAULT_DEDUPE_KEYS,
        order_by=DEFAULT_ORDER_BY,
        slate_top_k=DEFAULT_SLATE_TOP_K,
        per_game_top_n_value=DEFAULT_PER_GAME_TOP_N,
    )
    print(f"   orig displayed cards   : {len(orig_cards)}")
    print(f"   shadow displayed cards : {len(shadow_cards)}")

    # 4. Grade shadow cards (read-only, in-memory)
    print(f"\n[4/5] grading shadow cards via BDL game logs ...")
    # Load BDL logs for the shadow card players
    needed_players = {c.get("player_name_normalized") for c in shadow_cards}
    needed_players |= {c.get("player_name_normalized") for c in orig_cards}
    bdl = {}
    cursor = db.mlb_master_hub_2026.find(
        {"bdl_game_logs.0":{"$exists":True}},
        projection={"_id":0,"player_name":1,"display_name":1,
                     "mlb_full_name":1,"bdl_game_logs":1})
    async for hub in cursor:
        canon = hub.get("display_name") or hub.get("player_name") or hub.get("mlb_full_name") or ""
        nk = normalize_player_name(canon)
        if nk and nk in needed_players:
            logs = hub.get("bdl_game_logs") or []
            prior = bdl.get(nk)
            if prior is None or len(logs) > len(prior):
                bdl[nk] = logs
    def grade_cards(cards):
        w=l=p=u=0; stake=profit=0.0
        for c in cards:
            market = (c.get("market") or "").lower()
            fam = _resolve_mlb_family(market, c.get("stat_family"))
            logs = bdl.get(c.get("player_name_normalized") or "", [])
            log = _best_log_for(logs, game_date=GAME_DATE,
                                 commence_time=c.get("commence_time"))
            actual = _resolve_actual(log, fam) if log is not None else None
            st, pr, sk, av = _grade(actual, line=float(c.get("line")),
                                     side=(c.get("side") or "OVER").upper(),
                                     odds=int(c.get("odds")))
            if st == "win": w+=1
            elif st == "loss": l+=1
            elif st == "push": p+=1
            else: u+=1
            stake += sk; profit += pr
        dec = w+l
        hr = (100*w/dec) if dec else 0.0
        roi = (100*profit/stake) if stake else 0.0
        return {"n":len(cards),"w":w,"l":l,"p":p,"u":u,
                "stake":stake,"profit":profit,
                "hr_pct":round(hr,4),"roi_pct":round(roi,4)}
    orig_grade = grade_cards(orig_cards)
    shadow_grade = grade_cards(shadow_cards)

    # 5. Report
    print(f"\n[5/5] REPORT\n" + "="*100)
    print(f"\n──── (1) SH ROUTED ROWS")
    print(f"  orig   : {orig_summary['routed_in']}")
    print(f"  shadow : {shadow_summary['routed_in']}    (no change — routing layer untouched)")

    print(f"\n──── (2)+(3) tp_source distribution on routed-in rows")
    print(f"  {'source':<12}{'orig':>10}{'shadow':>10}{'Δ':>10}")
    sources = sorted(set(orig_summary['tp_source']) | set(shadow_summary['tp_source']))
    for s in sources:
        o = orig_summary['tp_source'][s]; sh = shadow_summary['tp_source'][s]
        print(f"  {s:<12}{o:>10}{sh:>10}{sh-o:>+10}")

    print(f"\n──── (4) tp_source_gate failures")
    print(f"  orig   : {orig_summary['tp_source_gate_fail']}")
    print(f"  shadow : {shadow_summary['tp_source_gate_fail']}    (Δ {shadow_summary['tp_source_gate_fail']-orig_summary['tp_source_gate_fail']:+d})")

    print(f"\n──── (5) qualified rows")
    print(f"  orig   : {orig_summary['qualified']}")
    print(f"  shadow : {shadow_summary['qualified']}    (Δ {shadow_summary['qualified']-orig_summary['qualified']:+d})")

    print(f"\n──── (6) displayed cards (top-K via prod card builder)")
    print(f"  orig   : {len(orig_cards)}")
    print(f"  shadow : {len(shadow_cards)}    (Δ {len(shadow_cards)-len(orig_cards):+d})")

    print(f"\n──── (7)+(8)+(9) graded results")
    print(f"  {'':<12}{'orig':>10}{'shadow':>10}")
    for k in ("n","w","l","p","u","stake","profit","hr_pct","roi_pct"):
        print(f"  {k:<12}{orig_grade[k]:>10}{shadow_grade[k]:>10}")

    print(f"\n──── (10) flip examples")
    print(f"  one_sided → devig flips (rows): {len(flips_one_sided_to_devig)}")
    for f in flips_one_sided_to_devig[:15]:
        print(f"     {(f['player'] or '')[:22]:<22} {(f['market'] or '')[:32]:<32} "
              f"L={f['line']} S={f['side']:<5} book={f['book']:<14} odds={f['odds']:>5} "
              f"ref={f['ref_odds']:>5}  book_count {f['orig_book_count']}→{f['shadow_book_count']}")
    print(f"\n  rejected → qualified flips (rows): {len(flips_rejected_to_qualified)}")
    for f in flips_rejected_to_qualified[:15]:
        print(f"     {(f['player'] or '')[:22]:<22} {(f['market'] or '')[:32]:<32} "
              f"L={f['line']} S={f['side']:<5} book={f['book']:<14} odds={f['odds']:>5} "
              f"ref={f['ref_odds']:>5}  fail={f['orig_first_fail']}  "
              f"tp {f['orig_tp_source']}→{f['shadow_tp_source']}")

    print(f"\n──── (11) ASSUMPTIONS USED IN CROSS-STAMPING")
    print("""
  A1. A `_alternate` market and its base market share the same
      stat_type and line space — verified against
      `historical_alt_odds_ingest.DEFAULT_MLB_MARKETS` (every
      `_alternate` is `base + '_alternate'` literally).
  A2. Within a single book at a single snapshot, a prop priced on
      both std and alt at the same line is equivalent to "this book
      quoted both sides" — same mechanism live serving uses via
      `universal_odds_sync.py:1403-1425`.
  A3. Cross-book bridging is NOT done. The shadow only unions sides
      that come from the SAME book. This preserves live parity.
  A4. Odds values are NOT invented. The shadow only changes
      `book_count` and `tp_source`. The `tp` field carried on the
      replay row (model probability / fair probability) is untouched.
  A5. Bridge applies to ALL stat families. No family-specific carve-out.
""")
    print(f"  ──── (12) DOES SHADOW NOW RESEMBLE LIVE BEHAVIOUR?")
    # Live SH 5-book pair distribution: 51.6% DK paired, etc.
    print("  Earlier audit measured live REF-book pair rate at 51.6% (DK), "
          "50.5% (MGM), 40.6% (CSR), 34.1% (BOL), 1.4% (FD). After the "
          "shadow bridge, replay's per-book pair rate should move toward "
          "that range. Below is the per-book pair rate on the shadow "
          "bridge inventory for this snapshot:")
    REF = ["draftkings","fanduel","betmgm","williamhill_us","betonlineag"]
    per_book = {b: {"present":0,"paired":0} for b in REF}
    for k, bucket in bridge_inv.items():
        ov = bucket.get("OVER", set())
        un = bucket.get("UNDER", set())
        all_books = ov | un
        for b in REF:
            if b in all_books:
                per_book[b]["present"] += 1
            if b in ov and b in un:
                per_book[b]["paired"] += 1
    print(f"     {'book':<18}{'present':>8}{'paired':>8}{'pair_rate':>10}{'live_was':>10}")
    LIVE = {"draftkings":"51.6%","fanduel":"1.4%","betmgm":"50.5%",
             "williamhill_us":"40.6%","betonlineag":"34.1%"}
    for b in REF:
        pr = per_book[b]["present"]; op = per_book[b]["paired"]
        rate = (100*op/pr) if pr else 0
        print(f"     {b:<18}{pr:>8}{op:>8}{rate:>9.2f}%{LIVE[b]:>10}")

    # Artifact
    art = "/app/backend/audits/phase5c_shadow_sh_2026_05_05.json"
    out = {
        "game_date": GAME_DATE, "snapshot_iso": SNAP, "tier": TIER,
        "orig": orig_summary, "shadow": shadow_summary,
        "orig_displayed_cards": len(orig_cards),
        "shadow_displayed_cards": len(shadow_cards),
        "orig_graded": orig_grade, "shadow_graded": shadow_grade,
        "flips_one_sided_to_devig_count": len(flips_one_sided_to_devig),
        "flips_one_sided_to_devig_sample": flips_one_sided_to_devig[:50],
        "flips_rejected_to_qualified_count": len(flips_rejected_to_qualified),
        "flips_rejected_to_qualified_sample": flips_rejected_to_qualified[:50],
        "shadow_pair_rates_per_book": per_book,
    }
    # Counters → ints
    for k in ("tp_source","failed_gates_first"):
        out["orig"][k] = dict(out["orig"][k])
        out["shadow"][k] = dict(out["shadow"][k])
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")
    cli.close()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
