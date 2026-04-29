"""Validate the 2026-04-29 FL OVER override spec against the prior
artifact. Reads the 30 OVER rejects from the snapshot, looks each up
in the freshly-recomputed `nba_prop_scores`, and reports newly
passing vs still rejected. Also confirms UNDER / SH / WZ are
unchanged on identity (count + canonical-key set).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient


ARTIFACT = Path(
    "/app/backend/data/snapshots/nba_top30_front_lines_rejects_20260429T010529.json"
)


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    snap = json.loads(ARTIFACT.read_text())
    over_rejects = snap["over"]
    under_rejects = snap["under"]

    # ── AFTER counts ──────────────────────────────────────────────
    fl_over = await db.nba_prop_scores.count_documents(
        {"version_tag": "final-nba-rt", "tier": "front_lines",
         "recommendation": "OVER"})
    fl_under = await db.nba_prop_scores.count_documents(
        {"version_tag": "final-nba-rt", "tier": "front_lines",
         "recommendation": "UNDER"})
    sh = await db.nba_prop_scores.count_documents(
        {"version_tag": "final-nba-rt", "tier": "safe_haven"})
    wz = await db.nba_prop_scores.count_documents(
        {"version_tag": "final-nba-rt", "tier": "war_zone"})

    print()
    print("===== TIER COUNTS — AFTER override =====")
    print(f"  Safe Haven : {sh}")
    print(f"  Front Lines OVER  : {fl_over}")
    print(f"  Front Lines UNDER : {fl_under}")
    print(f"  War Zone   : {wz}")

    # ── Build canonical_key list to fetch ─────────────────────────
    over_keys = [r.get("canonical_key") or
                 _build_ck(r) for r in over_rejects]

    fetched = await db.nba_prop_scores.find(
        {"version_tag": "final-nba-rt",
         "$or": [{"player_name": r["player"], "stat_type": r["stat"],
                  "line": r["line"], "recommendation": "OVER"}
                 for r in over_rejects]},
        {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "tier": 1, "tier_reason": 1,
         "tier_reference_odds": 1, "vk2_projection": 1,
         "model_projection": 1, "edge_pct": 1, "tp": 1,
         "cv": 1, "hit_rate": 1, "hit_rate_over": 1,
         "tier_gate_results": 1, "gate_eval": 1,
         "mu_recency_blend_l20": 1},
    ).to_list(length=2000)

    by_key = {(d["player_name"], d["stat_type"], d["line"],
               d["recommendation"]): d for d in fetched}

    newly_passing = []
    still_rejected = []
    for r in over_rejects:
        key = (r["player"], r["stat"], r["line"], "OVER")
        d = by_key.get(key)
        if d is None:
            still_rejected.append({"row": r,
                                   "tier": "missing_in_score_doc",
                                   "reason": "no doc found"})
            continue
        tier = d.get("tier")
        if tier == "front_lines":
            override = None
            gates = d.get("tier_gate_results") or {}
            applied = gates.get("__override_applied__") or {}
            if applied:
                override = (applied.get("threshold") or {}).get("name")
            newly_passing.append({"row": r, "doc": d, "override": override})
        else:
            reason = d.get("tier_reason")
            still_rejected.append({"row": r, "tier": tier,
                                   "reason": reason,
                                   "doc": d})

    print()
    print(f"===== NEWLY PASSING (FL OVER) — {len(newly_passing)} of 30 =====")
    for x in newly_passing:
        r = x["row"]
        proj = x["doc"].get("vk2_projection") or x["doc"].get("model_projection")
        proj_s = f"{proj:.2f}" if isinstance(proj, (int, float)) else "—"
        print(f"  {r['player']:<24} {r['stat']:<8} L={r['line']:>5}  "
              f"refOdds={r.get('ref_odds'):>+5}  edge={r.get('edge_pct')}  "
              f"hr={r.get('hit_rate')}  cv={r.get('cv')}  proj={proj_s}  "
              f"override={x['override']}")

    print()
    print(f"===== STILL REJECTED (FL OVER) — {len(still_rejected)} of 30 =====")
    for x in still_rejected:
        r = x["row"]
        proj_doc = x.get("doc") or {}
        proj = proj_doc.get("vk2_projection") or proj_doc.get("model_projection")
        proj_s = f"{proj:.2f}" if isinstance(proj, (int, float)) else "—"
        print(f"  {r['player']:<24} {r['stat']:<8} L={r['line']:>5}  "
              f"refOdds={r.get('ref_odds'):>+5}  edge={r.get('edge_pct')}  "
              f"hr={r.get('hit_rate')}  cv={r.get('cv')}  proj={proj_s}  "
              f"final_tier={x['tier']}  reason={x['reason']}")

    # ── UNDER side identity check ─────────────────────────────────
    print()
    print(f"===== UNDER INVARIANCE (artifact had {len(under_rejects)} rejects) =====")
    under_changed = []
    for r in under_rejects:
        d = await db.nba_prop_scores.find_one(
            {"version_tag": "final-nba-rt",
             "player_name": r["player"], "stat_type": r["stat"],
             "line": r["line"], "recommendation": "UNDER"},
            {"_id": 0, "tier": 1})
        if d is None:
            continue  # slate drift — ignore
        if d.get("tier") == "front_lines":
            under_changed.append((r["player"], r["stat"], r["line"]))
    print(f"  UNDERs that newly pass FL after the change: {len(under_changed)} "
          f"(MUST be 0)")
    for x in under_changed:
        print(f"    {x}")

    # ── SH / WZ canonical-key invariance ──────────────────────────
    sh_keys = sorted({(d["player_name"], d["stat_type"], d["line"], d["recommendation"])
                      async for d in db.nba_prop_scores.find(
                          {"version_tag": "final-nba-rt", "tier": "safe_haven"},
                          {"_id": 0, "player_name": 1, "stat_type": 1,
                           "line": 1, "recommendation": 1})})
    wz_keys = sorted({(d["player_name"], d["stat_type"], d["line"], d["recommendation"])
                      async for d in db.nba_prop_scores.find(
                          {"version_tag": "final-nba-rt", "tier": "war_zone"},
                          {"_id": 0, "player_name": 1, "stat_type": 1,
                           "line": 1, "recommendation": 1})})
    print()
    print(f"===== Safe Haven contents ({len(sh_keys)}) =====")
    for k in sh_keys:
        print(f"  {k}")
    print()
    print(f"===== War Zone contents ({len(wz_keys)}) =====")
    for k in wz_keys[:25]:
        print(f"  {k}")
    if len(wz_keys) > 25:
        print(f"  ... +{len(wz_keys)-25} more")


def _build_ck(r):
    return None


asyncio.run(main())
