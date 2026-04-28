"""
MLB Identity-Resolution Audit
==============================
Diagnoses the join failures between live props, master hub, and
Statcast features. Read-only — does not modify any collection.

Outputs:
  * summary counts (live → matched / unmatched by method)
  * top 50 unmatched players by live-prop count
  * top 50 high-value unmatched (would-be picks by edge × vision_raw)
  * per-row breakdown of WHY each unmatched player fails:
      - accent/punctuation/suffix mismatch identified by replaying the
        raw form through normalize_player_name()
      - closest Statcast neighbor + similarity score

Run:
    python -m scripts.audit_mlb_identity_resolution
    python -m scripts.audit_mlb_identity_resolution --top 100
"""
from __future__ import annotations

import argparse, asyncio, importlib.util, os, sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/tmp")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.mlb.identity import (
    normalize_player_name, apply_alias, string_similarity,
)
# Engine import — only used to pull the in-process candidate stream so
# we can compute "high-value unmatched" via real edges/vision_raw.
spec = importlib.util.spec_from_file_location(
    "mlb_pv", "/tmp/mlb_propvision_total_bases.py")
mlb_pv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mlb_pv)


async def _live_player_counts(db) -> Dict[str, Dict[str, Any]]:
    """For each unique normalized live-prop player, count rows + grab
    one raw form for display."""
    out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "raw": None, "n_props": 0, "team": None,
    })
    async for d in db.mlb_live_props.find(
        {"stat_type": "Total Bases"},
        {"_id": 0, "player_name": 1, "team": 1}):
        raw = d.get("player_name")
        nn = apply_alias(normalize_player_name(raw))
        if not nn: continue
        slot = out[nn]
        slot["n_props"] += 1
        slot["raw"]      = slot["raw"] or raw
        slot["team"]     = slot["team"] or d.get("team")
    return out


async def _statcast_norm_keys(db) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    async for d in db.mlb_statcast_player_features.find(
        {}, {"_id": 0, "player_id": 1, "player_name": 1}):
        nn = apply_alias(normalize_player_name(d.get("player_name")))
        if not nn: continue
        out.setdefault(nn, {
            "statcast_id": d.get("player_id"),
            "statcast_name": d.get("player_name"),
        })
    return out


async def _hub_by_norm(db) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    async for d in db.mlb_master_hub_2026.find(
        {"is_batter": True},
        {"_id": 0, "player_name": 1, "display_name": 1, "bdl_id": 1,
         "team": 1}):
        raw = d.get("display_name") or d.get("player_name")
        nn = apply_alias(normalize_player_name(raw))
        if not nn: continue
        out.setdefault(nn, {"bdl_id": d.get("bdl_id"), "raw": raw,
                              "team": d.get("team")})
    return out


def _diagnose_mismatch(raw_live: str, sc_keys: List[str],
                         hub_keys: List[str]) -> Dict[str, Any]:
    """For a player that did not match Statcast, walk the normalization
    pipeline step-by-step to surface the exact suspect cause + closest
    Statcast neighbor."""
    if not raw_live:
        return {"cause": "—", "closest": None, "score": 0.0}

    nn_live = normalize_player_name(raw_live) or ""
    causes = []
    if any(c for c in raw_live if ord(c) > 127):
        causes.append("accent")
    if any(c in raw_live for c in ".'`-"):
        causes.append("punctuation")
    low = raw_live.lower().strip()
    if any(low.endswith(s) for s in (" jr.", " sr.", " jr", " sr",
                                       " ii", " iii", " iv")):
        causes.append("suffix")

    # Closest Statcast neighbor — best similarity vs all sc keys.
    best = (None, 0.0)
    for s in sc_keys:
        sc = string_similarity(nn_live, s)
        if sc > best[1]: best = (s, sc)
    in_hub = nn_live in hub_keys
    return {"cause": ",".join(causes) or "no_data",
             "closest": best[0], "score": round(best[1], 3),
             "in_hub": in_hub, "norm": nn_live}


async def _high_value_unmatched(db, sc_keys: set) -> Dict[str, Dict[str, Any]]:
    """Re-run the engine in-process up through candidate build, return
    {normalized_name → {edge, vision_raw}} for any candidate whose
    Statcast lookup MISSED."""
    out: Dict[str, Dict[str, Any]] = {}
    by_name = await mlb_pv.load_player_logs(db)
    statcast_by_pd = await mlb_pv.load_statcast_features(db)
    raw_props = await mlb_pv.load_total_bases_props(db)
    # Build per-prop μ/σ/edge using the engine's pipeline; track every
    # (player, date) where _statcast_for() returned None.
    seen = set()
    for p in raw_props:
        nm = mlb_pv._player_key(p)
        date = mlb_pv._slate_date(p)
        if not nm or not date: continue
        key = (nm, date)
        if key in seen: continue
        seen.add(key)
        plogs = by_name.get(nm) or []
        prior = [lg for lg in plogs if lg["date"] < date]
        if len(prior) < 10: continue
        sc_row = mlb_pv._statcast_for(statcast_by_pd, nm, date)
        nn = apply_alias(normalize_player_name(nm))
        # Definition of "unmatched": engine had to fall back because
        # the live player_name couldn't be located in statcast.
        if sc_row is None and nn not in sc_keys:
            mu, sigma, _ = mlb_pv.predict_mu_sigma(
                prior_logs=prior,
                batting_order=mlb_pv._i(p.get("batting_order")),
                statcast=None)
            if mu is None: continue
            line = mlb_pv._f(p.get("line"))
            if line is None: continue
            z = (line - mu) / sigma
            p_over = 1.0 - mlb_pv._norm_cdf(z)
            edge_proxy = abs(p_over - 0.5) * 100  # rough "would-be" edge
            slot = out.get(nn)
            if not slot or edge_proxy > slot["edge"]:
                out[nn] = {"raw": p.get("player_name"),
                            "team": p.get("team"),
                            "edge": edge_proxy,
                            "mu": mu, "sigma": sigma,
                            "n_props": 1}
            else:
                slot["n_props"] += 1
    return out


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=50)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    live = await _live_player_counts(db)
    sc   = await _statcast_norm_keys(db)
    hub  = await _hub_by_norm(db)
    sc_keys_set = set(sc)

    n_live   = len(live)
    n_sc_match = sum(1 for nn in live if nn in sc_keys_set)
    # ID-based path is impossible without a live-side MLBAM id (which we
    # don't have). Surfaced explicitly so the report is honest.
    print("=" * 80); print(" MLB IDENTITY-RESOLUTION AUDIT"); print("=" * 80)
    print(f"\n LIVE PROP PLAYERS (Total Bases stat_type)")
    print(f"   total unique normalized      : {n_live:>4}")
    print(f"   match Statcast by ID         :    0   (no shared id col available)")
    print(f"   match Statcast by norm-name  : {n_sc_match:>4}  "
          f"({n_sc_match/max(n_live,1)*100:.1f}%)")
    print(f"   fall back to bdl_proxy       : {n_live-n_sc_match:>4}  "
          f"({(n_live-n_sc_match)/max(n_live,1)*100:.1f}%)")

    unmatched = [nn for nn in live if nn not in sc_keys_set]
    print(f"\n TOP {args.top} UNMATCHED PLAYERS BY PROP COUNT")
    rows = sorted([(nn, live[nn]) for nn in unmatched],
                    key=lambda x: -x[1]["n_props"])[:args.top]
    print(f"   {'#':>3}  {'live name':30s}  {'team':5s}  {'#props':>6}  "
          f"{'cause':22s}  {'closest sc':25s}  {'sim':>5}  {'in_hub':>6}")
    for i, (nn, info) in enumerate(rows, start=1):
        diag = _diagnose_mismatch(info["raw"], list(sc_keys_set), set(hub))
        closest = diag["closest"] or "—"
        print(f"   {i:>3}  {(info['raw'] or '')[:30]:30s}  "
              f"{(info['team'] or '—'):5s}  {info['n_props']:>6}  "
              f"{diag['cause'][:22]:22s}  {closest[:25]:25s}  "
              f"{diag['score']:>5.2f}  {('Y' if diag['in_hub'] else 'n'):>6}")

    print(f"\n HIGH-VALUE UNMATCHED (would-be picks by |p_over - 0.5|×100)")
    hv = await _high_value_unmatched(db, sc_keys_set)
    if not hv:
        print("   (none)")
    else:
        rows = sorted(hv.items(), key=lambda x: -x[1]["edge"])[:args.top]
        print(f"   {'#':>3}  {'live name':28s}  {'team':5s}  "
              f"{'#props':>6}  {'mu':>5}  {'sig':>5}  {'edge':>5}  "
              f"{'cause':22s}  {'closest sc':25s}  {'sim':>5}")
        for i, (nn, v) in enumerate(rows, start=1):
            diag = _diagnose_mismatch(v["raw"], list(sc_keys_set), set(hub))
            print(f"   {i:>3}  {(v['raw'] or '')[:28]:28s}  "
                  f"{(v['team'] or '—'):5s}  {v['n_props']:>6}  "
                  f"{v['mu']:>5.2f}  {v['sigma']:>5.2f}  "
                  f"{v['edge']:>5.1f}  {diag['cause'][:22]:22s}  "
                  f"{(diag['closest'] or '—')[:25]:25s}  "
                  f"{diag['score']:>5.2f}")

    print()
    print("[AUDIT] DONE — read-only.")


if __name__ == "__main__":
    asyncio.run(main())
