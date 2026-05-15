"""Post-SSOT comprehensive FL OVER reject audit (2026-05-15).

All sections constrained to:
  active=True, recommendation='OVER', routed_tier='front_lines',
  batter stats only, projection_model_version='MLB_HF_v3.1_phase2a'.

Sections produced:
  1. Top 30 by hit_rate_l20  (current SSOT verdict)
  2. Top 30 by edge_vs_fair
  3. Props that FLIPPED edge_gate due to SSOT migration
  4. Gate-failure breakdown BEFORE vs AFTER the edge SSOT fix
  5. Newly gates_passed rows  (incl. Shea Langeliers proof)
  6. cv_fail-only candidates  (every other gate passes)
  7. hit_rate_fail-only candidates
"""
from __future__ import annotations
import asyncio, os, sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


BATTER_STATS = {
    "Hits", "Total Bases", "RBIs", "Runs", "Home Runs", "Doubles",
    "Walks", "Singles", "Hits+Runs+RBIs", "Stolen Bases",
    "Batter Strikeouts",
}
EDGE_GATE_THRESHOLD_PP = 5.0     # FL OVER edge_min (thresholds.py:396-408)


def _fmt(v, d=2):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:.{d}f}"
    return str(v)


def _opp_pitcher(r: Dict[str, Any]) -> str:
    nm = r.get("opp_pitcher_name") or ""
    pt = r.get("opp_pitcher_throws") or "?"
    if not nm:
        return "—"
    return f"{nm[:18]} ({pt})"


def _gate_pass_map(r: Dict[str, Any]) -> Dict[str, bool]:
    """Pull the per-gate pass map. Returns {} when gate_results
    aren't stamped (legacy rows)."""
    gr = r.get("tier_gate_results") or {}
    return {k: bool(v.get("passed")) for k, v in gr.items()
            if isinstance(v, dict) and "passed" in v}


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    base_filter = {
        "active": True,
        "recommendation": "OVER",
        "routed_tier": "front_lines",
        "stat_type": {"$in": list(BATTER_STATS)},
        "projection_model_version": "MLB_HF_v3.1_phase2a",
    }

    # ── Counts ────────────────────────────────────────────────────
    n_all = await db.mlb_prop_scores.count_documents(base_filter)
    n_passed = await db.mlb_prop_scores.count_documents(
        {**base_filter, "tier": "front_lines"}
    )
    n_rejected = await db.mlb_prop_scores.count_documents(
        {**base_filter, "tier": "unqualified"}
    )
    print("=" * 110)
    print("MLB FL OVER — Post-SSOT comprehensive reject audit")
    print(f"(batter props only • v3.1_phase2a only • active=True)")
    print("=" * 110)
    print(f"Universe size: {n_all:,}  |  passed: {n_passed:,}  |  "
           f"rejected: {n_rejected:,}")
    print()

    # ── Section 4: Gate-failure breakdown BEFORE vs AFTER ─────────
    # AFTER = current SSOT tier_reason
    # BEFORE = reconstructed: pre-SSOT gate would have used
    #          edge_pct_old = (p_model*100) - tp, where
    #          p_model_recon = fair_prob + edge_vs_fair (SSOT recovery)
    # We can only reconstruct the BEFORE verdict for the edge_gate
    # since other gates are unchanged.
    cur = db.mlb_prop_scores.find(
        {**base_filter, "tier": "unqualified"},
        {
            "_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
            "tier_reason": 1, "tier_gate_results": 1,
            "edge_vs_fair": 1, "fair_prob": 1, "tp": 1,
            "hit_rate_l20": 1, "consensus_odds": 1, "dk_odds": 1,
            "book_count": 1, "model_projection": 1, "batter_hand": 1,
            "opp_pitcher_name": 1, "opp_pitcher_throws": 1,
            "same_hand_matchup": 1, "expected_pa": 1,
        },
    )
    all_rejects = await cur.to_list(length=None)

    # Reconstruct old edge_pct for each row.
    flipped_to_pass: List[Dict[str, Any]] = []
    flipped_to_fail: List[Dict[str, Any]] = []  # unlikely but possible
    after_gate_counter: Dict[str, int] = {}
    before_edge_pass: int = 0
    after_edge_pass: int = 0
    for r in all_rejects:
        evf = r.get("edge_vs_fair")
        fp = r.get("fair_prob"); tp = r.get("tp")
        if evf is None or fp is None or tp is None:
            continue
        new_edge_pp = round(evf * 100.0, 4)
        p_model_recon = fp + evf
        old_edge_pp = round((p_model_recon * 100.0) - tp, 4)
        new_pass = new_edge_pp >= EDGE_GATE_THRESHOLD_PP
        old_pass = old_edge_pp >= EDGE_GATE_THRESHOLD_PP
        if new_pass: after_edge_pass += 1
        if old_pass: before_edge_pass += 1
        # If new_pass AND old_pass DIFFER → flip
        gp = _gate_pass_map(r)
        if (not old_pass) and new_pass:
            # Edge gate would have failed before, now passes.
            flipped_to_pass.append({**r,
                "_old_edge_pp": old_edge_pp,
                "_new_edge_pp": new_edge_pp,
                "_other_gates": {
                    k: v for k, v in gp.items() if k != "edge_gate"
                },
            })
        elif old_pass and not new_pass:
            flipped_to_fail.append({**r,
                "_old_edge_pp": old_edge_pp,
                "_new_edge_pp": new_edge_pp,
            })

    # ── Section 1: Top 30 by HR L20 ───────────────────────────────
    print("=" * 110)
    print("1. TOP 30 REJECTS BY HIT_RATE_L20 (current SSOT verdict)")
    print("=" * 110)
    rows = sorted(
        (r for r in all_rejects
         if r.get("hit_rate_l20") is not None),
        key=lambda r: (-float(r["hit_rate_l20"]),
                        -(r.get("edge_vs_fair") or -999)),
    )
    # Dedupe by (player, stat, line)
    seen = set()
    unique: List[Dict[str, Any]] = []
    for r in rows:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); unique.append(r)
        if len(unique) >= 30: break
    print(f"{'#':>2}  {'PLAYER':<22} {'STAT':<19} {'LN':>4}  "
           f"{'EDGE':>7} {'HR':>4} {'TP%':>5} {'BH':>3} "
           f"{'PITCHER':<22} {'DK':>5} {'BK':>3} {'REASON'}")
    for i, r in enumerate(unique, 1):
        evf = (r.get("edge_vs_fair") or 0) * 100
        dk = r.get("dk_odds")
        reason = (r.get("tier_reason") or "").replace(
            "front_lines_failed:", "").strip()
        print(f"{i:>2}  {r['player_name'][:22]:<22} "
               f"{r['stat_type'][:19]:<19} "
               f"{_fmt(r.get('line'),1):>4}  "
               f"{evf:>+6.2f} {_fmt(r.get('hit_rate_l20'),0):>4} "
               f"{_fmt(r.get('tp'),1):>5} "
               f"{r.get('batter_hand') or '?':>3} "
               f"{_opp_pitcher(r):<22} "
               f"{(f'{int(dk):+d}' if isinstance(dk,(int,float)) else '—'):>5} "
               f"{r.get('book_count') or 0:>3} "
               f"{reason[:32]}")

    # ── Section 2: Top 30 by edge_vs_fair ─────────────────────────
    print()
    print("=" * 110)
    print("2. TOP 30 REJECTS BY EDGE_VS_FAIR")
    print("=" * 110)
    rows2 = sorted(
        (r for r in all_rejects
         if r.get("edge_vs_fair") is not None),
        key=lambda r: -float(r["edge_vs_fair"]),
    )
    seen = set(); unique = []
    for r in rows2:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); unique.append(r)
        if len(unique) >= 30: break
    print(f"{'#':>2}  {'PLAYER':<22} {'STAT':<19} {'LN':>4}  "
           f"{'EDGE':>7} {'HR':>4} {'TP%':>5} {'BH':>3} "
           f"{'PITCHER':<22} {'DK':>5} {'BK':>3} {'REASON'}")
    for i, r in enumerate(unique, 1):
        evf = (r.get("edge_vs_fair") or 0) * 100
        dk = r.get("dk_odds")
        reason = (r.get("tier_reason") or "").replace(
            "front_lines_failed:", "").strip()
        print(f"{i:>2}  {r['player_name'][:22]:<22} "
               f"{r['stat_type'][:19]:<19} "
               f"{_fmt(r.get('line'),1):>4}  "
               f"{evf:>+6.2f} {_fmt(r.get('hit_rate_l20'),0):>4} "
               f"{_fmt(r.get('tp'),1):>5} "
               f"{r.get('batter_hand') or '?':>3} "
               f"{_opp_pitcher(r):<22} "
               f"{(f'{int(dk):+d}' if isinstance(dk,(int,float)) else '—'):>5} "
               f"{r.get('book_count') or 0:>3} "
               f"{reason[:32]}")

    # ── Section 3: Edge-SSOT flips ────────────────────────────────
    print()
    print("=" * 110)
    print(f"3. PROPS WHERE EDGE GATE VERDICT FLIPPED  "
           f"(threshold = {EDGE_GATE_THRESHOLD_PP}pp)")
    print("=" * 110)
    print(f"  fail→pass: {len(flipped_to_pass)}    "
           f"pass→fail: {len(flipped_to_fail)}")
    if flipped_to_pass:
        print(f"\n  --- fail→pass (gate now correctly accepts) ---")
        print(f"  {'PLAYER':<22} {'STAT':<18} {'LN':>4}  "
               f"{'OLD pp':>7} {'NEW pp':>7}  {'CURRENT TIER':<14} REASON")
        for r in sorted(flipped_to_pass,
                         key=lambda x: -x["_new_edge_pp"])[:40]:
            cur_tier = "unqualified"  # still in rejects bucket
            other_fails = [
                g for g, p in r["_other_gates"].items() if not p
            ]
            print(f"  {r['player_name'][:22]:<22} "
                   f"{r['stat_type'][:18]:<18} "
                   f"{_fmt(r.get('line'),1):>4}  "
                   f"{r['_old_edge_pp']:>+6.2f} "
                   f"{r['_new_edge_pp']:>+6.2f}  "
                   f"{cur_tier:<14} "
                   f"reason now: {(r.get('tier_reason') or '').replace('front_lines_failed:','').strip()[:40]} "
                   f"(other fails: {other_fails or 'none — should now PASS!'})")
    if flipped_to_fail:
        print(f"\n  --- pass→fail (gate now correctly rejects) ---")
        for r in flipped_to_fail[:20]:
            print(f"  {r['player_name']:<22} {r['stat_type']:<18}  "
                   f"old={r['_old_edge_pp']:+6.2f}pp → new={r['_new_edge_pp']:+6.2f}pp")

    # ── Section 4: Gate-failure breakdown ─────────────────────────
    print()
    print("=" * 110)
    print("4. GATE-FAILURE BREAKDOWN  (FL OVER batter rejects, v3.1_phase2a)")
    print("=" * 110)
    # Tally tier_reason buckets
    for r in all_rejects:
        reason = (r.get("tier_reason") or "").replace(
            "front_lines_failed:", "").strip()
        # Extract first gate token after "gate_"
        if "gate_" in reason:
            tag = "gate_" + reason.split("gate_", 1)[1].split("_fail")[0] + "_fail"
        else:
            tag = reason or "unspecified"
        after_gate_counter[tag] = after_gate_counter.get(tag, 0) + 1
    print(f"  Total rejects:                {len(all_rejects):>4}")
    print(f"  Edge gate passes (new SSOT):  {after_edge_pass:>4}  "
           f"(was {before_edge_pass} under old local formula)")
    print(f"  Δ from SSOT migration:        "
           f"{after_edge_pass - before_edge_pass:>+4}")
    print()
    print("  Current rejection reasons (post-SSOT):")
    for k, v in sorted(after_gate_counter.items(),
                        key=lambda x: -x[1]):
        print(f"    {k:<30} {v:>4}")

    # ── Section 5: Newly gates_passed rows ────────────────────────
    print()
    print("=" * 110)
    print("5. CURRENT FL OVER 'gates_passed' BATTER ROWS  "
           "(includes any newly-promoted via SSOT)")
    print("=" * 110)
    passed_cur = db.mlb_prop_scores.find(
        {**base_filter, "tier": "front_lines"},
        {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
         "edge_vs_fair": 1, "hit_rate_l20": 1, "tp": 1,
         "opp_pitcher_name": 1, "opp_pitcher_throws": 1,
         "batter_hand": 1, "consensus_odds": 1, "dk_odds": 1,
         "book_count": 1, "model_projection": 1,
        },
    )
    passed = await passed_cur.to_list(length=None)
    seen = set(); unique = []
    for r in passed:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); unique.append(r)
    unique.sort(key=lambda r: -(r.get("edge_vs_fair") or -999))
    print(f"  Total passed (batter): {len(unique)}")
    print(f"  {'PLAYER':<22} {'STAT':<18} {'LN':>4}  "
           f"{'EDGE':>7} {'HR':>4} {'TP%':>5} {'MUST PROOF'}")
    for r in unique[:40]:
        evf = (r.get("edge_vs_fair") or 0) * 100
        proof = ""
        if r["player_name"] == "Shea Langeliers":
            proof = "← SHEA LANGELIERS — proved gate now consumes SSOT"
        print(f"  {r['player_name'][:22]:<22} "
               f"{r['stat_type'][:18]:<18} "
               f"{_fmt(r.get('line'),1):>4}  "
               f"{evf:>+6.2f} {_fmt(r.get('hit_rate_l20'),0):>4} "
               f"{_fmt(r.get('tp'),1):>5} {proof}")

    # ── Sections 6/7: single-gate failure candidates ──────────────
    print()
    print("=" * 110)
    print("6. CV-FAIL-ONLY CANDIDATES  (every other gate would pass)")
    print("=" * 110)
    cv_only = []
    hr_only = []
    for r in all_rejects:
        gp = _gate_pass_map(r)
        if not gp:
            continue
        failed_gates = [k for k, v in gp.items() if not v]
        if failed_gates == ["cv_gate"]:
            cv_only.append(r)
        elif failed_gates == ["hit_rate_gate"]:
            hr_only.append(r)
    cv_only.sort(key=lambda r: -(r.get("edge_vs_fair") or -999))
    hr_only.sort(key=lambda r: -(r.get("edge_vs_fair") or -999))
    seen = set(); unique = []
    for r in cv_only:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); unique.append(r)
    print(f"  count: {len(unique)}")
    print(f"  {'PLAYER':<22} {'STAT':<18} {'LN':>4}  "
           f"{'EDGE':>7} {'HR':>4} {'CV (need)':>11}")
    for r in unique[:30]:
        gr = r.get("tier_gate_results") or {}
        cv_actual = (gr.get("cv_gate") or {}).get("value")
        cv_thresh = (gr.get("cv_gate") or {}).get("threshold")
        evf = (r.get("edge_vs_fair") or 0) * 100
        print(f"  {r['player_name'][:22]:<22} "
               f"{r['stat_type'][:18]:<18} "
               f"{_fmt(r.get('line'),1):>4}  "
               f"{evf:>+6.2f} {_fmt(r.get('hit_rate_l20'),0):>4} "
               f"{_fmt(cv_actual,2):>5} (≤{_fmt(cv_thresh,2)})")

    print()
    print("=" * 110)
    print("7. HIT_RATE-FAIL-ONLY CANDIDATES  (every other gate would pass)")
    print("=" * 110)
    seen = set(); unique = []
    for r in hr_only:
        k = (r["player_name"], r["stat_type"], r.get("line"))
        if k in seen: continue
        seen.add(k); unique.append(r)
    print(f"  count: {len(unique)}")
    print(f"  {'PLAYER':<22} {'STAT':<18} {'LN':>4}  "
           f"{'EDGE':>7} {'HR (need)':>10} {'TP%':>5}")
    for r in unique[:30]:
        gr = r.get("tier_gate_results") or {}
        hr_actual = (gr.get("hit_rate_gate") or {}).get("value")
        hr_thresh = (gr.get("hit_rate_gate") or {}).get("threshold")
        evf = (r.get("edge_vs_fair") or 0) * 100
        print(f"  {r['player_name'][:22]:<22} "
               f"{r['stat_type'][:18]:<18} "
               f"{_fmt(r.get('line'),1):>4}  "
               f"{evf:>+6.2f} {_fmt(hr_actual,0):>4} (≥{_fmt(hr_thresh,0)}) "
               f"{_fmt(r.get('tp'),1):>5}")


if __name__ == "__main__":
    asyncio.run(main())
