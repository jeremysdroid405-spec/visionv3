"""Before/after comparison of the blend_bench composition on live NBA board.

Pulls current NBA PTS and PRA live props, runs them through the adapter's
_predict_vk2_prob_over twice:
  1. with composition enabled (current production)
  2. with composition forcibly disabled (baseline simulation)

and reports:
  * props scored (baseline vs composed)
  * how many props flipped to composition (bench regime, PTS/PRA)
  * mean abs delta, max delta, top-10 material changes
  * whether Ferrari endpoints are serving 200s

Usage: python scripts/compare_minutes_composition.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main():
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    adapter = NBAScoringAdapter()
    # Preload adv map + game-log cache so VK2 features are complete.
    await adapter._preload_vk2_adv_map(db)
    await adapter._preload_game_logs(db)
    adapter._load_vk2_models()
    adapter._load_min_model()
    # Query live NBA props for PTS and PRA with a resolved bdl_player_id.
    props = await db["nba_live_props"].find(
        {
            "stat_type": {"$in": ["PTS", "PRA"]},
            "bdl_player_id": {"$ne": None},
            "active": {"$ne": False},
        },
        {"_id": 0, "player_name": 1, "bdl_player_id": 1, "stat_type": 1,
         "line": 1, "direction": 1, "sharp_market": 1}
    ).limit(2000).to_list(length=None)
    print(f"[compare] fetched {len(props)} PTS/PRA live props with bdl_id", flush=True)

    # Drop the minutes-composition for baseline simulation.
    orig_compose = adapter._compose_minutes_adjusted_projection
    adapter._compose_minutes_adjusted_projection = lambda **kw: {
        "projection": kw["baseline_projection"],
        "composition_applied": False,
        "composed_from_minutes": None, "per_min_rate": None,
        "min_played_L10_mean": kw["feats"].get("min_played_L10_mean", 0.0),
        "error": None,
    }

    baselines = {}
    for p in props:
        bdl = p.get("bdl_player_id")
        line = float(p.get("line") or 0.0)
        st = p.get("stat_type")
        if bdl is None:
            continue
        r = adapter._predict_vk2_prob_over(bdl_player_id=bdl, stat_type=st, line=line)
        if r.get("projection") is None:
            continue
        baselines[(bdl, st, line)] = r["projection"]

    # Restore and re-run with composition.
    adapter._compose_minutes_adjusted_projection = orig_compose
    composed = {}
    composition_meta = {}
    for p in props:
        bdl = p.get("bdl_player_id")
        line = float(p.get("line") or 0.0)
        st = p.get("stat_type")
        if bdl is None:
            continue
        r = adapter._predict_vk2_prob_over(bdl_player_id=bdl, stat_type=st, line=line)
        if r.get("projection") is None:
            continue
        key = (bdl, st, line)
        composed[key] = r["projection"]
        if r.get("minutes_composition_applied"):
            composition_meta[key] = {
                "baseline_proj": r.get("baseline_projection"),
                "composed_proj": r.get("projection"),
                "composed_from_minutes": r.get("composed_from_minutes"),
                "per_min_rate": r.get("per_min_rate"),
                "min_played_L10_mean": r.get("min_played_L10_mean"),
            }

    # Diff metrics
    keys = set(baselines) & set(composed)
    deltas = [
        (k, composed[k] - baselines[k])
        for k in keys
    ]
    applied = len(composition_meta)
    print(f"[compare] scored props: {len(keys)}", flush=True)
    print(f"[compare] composition applied (bench regime): {applied} "
          f"({100*applied/max(len(keys),1):.1f}%)", flush=True)
    if deltas:
        abs_deltas = [abs(d) for _, d in deltas]
        print(f"[compare] mean |Δ proj|: {sum(abs_deltas)/len(abs_deltas):.4f}", flush=True)
        print(f"[compare] max |Δ proj|:  {max(abs_deltas):.4f}", flush=True)

    # Top-10 material changes — largest downward shifts (the bench
    # regime should pull projections DOWN towards expected_minutes).
    material = sorted(
        [(k, d) for k, d in deltas if abs(d) > 0.01],
        key=lambda x: x[1]
    )
    # Map back to player names
    name_by_bdl = {p.get("bdl_player_id"): p.get("player_name") for p in props}
    print("\n[compare] TOP-10 DOWNWARD SHIFTS (composition pulled projection down):")
    print("=" * 92)
    print(f"{'Player':24s} {'Stat':4s} {'Line':>5s} {'Base':>7s} {'Composed':>9s} {'Δ':>6s} "
          f"{'PredMin':>7s} {'Rate':>5s} {'L10Min':>6s}")
    print("-" * 92)
    for k, d in material[:10]:
        bdl, st, line = k
        meta = composition_meta.get(k, {})
        print(
            f"{(name_by_bdl.get(bdl) or str(bdl))[:24]:24s} "
            f"{st:4s} {line:5.1f} {baselines[k]:7.2f} "
            f"{composed[k]:9.2f} {d:+6.2f} "
            f"{(meta.get('composed_from_minutes') or 0):7.2f} "
            f"{(meta.get('per_min_rate') or 0):5.2f} "
            f"{(meta.get('min_played_L10_mean') or 0):6.2f}"
        )
    # Upward shifts (rare — only if baseline under-predicted a bench player)
    print("\n[compare] TOP-5 UPWARD SHIFTS:")
    print("-" * 92)
    for k, d in sorted(material, key=lambda x: -x[1])[:5]:
        bdl, st, line = k
        meta = composition_meta.get(k, {})
        print(
            f"{(name_by_bdl.get(bdl) or str(bdl))[:24]:24s} "
            f"{st:4s} {line:5.1f} {baselines[k]:7.2f} "
            f"{composed[k]:9.2f} {d:+6.2f} "
            f"{(meta.get('composed_from_minutes') or 0):7.2f} "
            f"{(meta.get('per_min_rate') or 0):5.2f} "
            f"{(meta.get('min_played_L10_mean') or 0):6.2f}"
        )

    # Adapter observability counters
    print(f"\n[compare] Adapter counters after run:")
    print(f"  composition applied:        {adapter._min_composition_applied}")
    print(f"  skipped (not bench):        {adapter._min_composition_skipped_not_bench}")
    print(f"  skipped (no per-min rate):  {adapter._min_composition_skipped_no_rate}")
    print(f"  errors:                     {adapter._min_composition_errors}")

    mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
