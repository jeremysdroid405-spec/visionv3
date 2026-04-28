"""
Top-20 NBA Safe Haven Rejects — Determinism Check
=================================================

Acquires the same delta-blocking locks as snapshot_safe_haven_candidates.py,
captures the top-20 rejects (tier != safe_haven, ref_odds <= -240) sorted
by edge_pct DESC with a stable tie-breaker, sleeps 60 s, captures again,
and prints both side-by-side with a hash + diff.

This script is read-only. No model / scoring / gate / threshold touched.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from services.sync_lock import acquire, release  # noqa: E402

VERSION_TAG = "final-nba-rt"
SH_REF_ODDS_CEILING = -240
TOP_N = 20
SLEEP_SECONDS = 60
LOCK_TTL = 240
LOCK_KEYS = ["sync:nba", "recompute:nba"]


async def fetch_top_20(db) -> List[Dict[str, Any]]:
    """Top-20 rejects, sorted by the canonical deterministic tuple:

        edge_pct        DESC
        vision_score    DESC
        canonical_key   ASC

    Sort is performed in Python after fetch so None values are handled
    consistently (treated as -inf for DESC fields) and ties cannot reorder
    across re-queries — even if MongoDB's natural order changes.
    """
    cursor = db.nba_prop_scores.find(
        {
            "version_tag":         VERSION_TAG,
            "tier_reference_odds": {"$lte": SH_REF_ODDS_CEILING},
            "tier":                {"$ne": "safe_haven"},
        },
        {
            "_id": 0,
            "canonical_key": 1, "player_name": 1, "stat_type": 1, "line": 1,
            "recommendation": 1, "tier": 1, "tier_reason": 1,
            "tier_reference_odds": 1, "tier_reference_book": 1,
            "hit_rate_over": 1, "hit_rate_under": 1,
            "cv": 1, "tp": 1, "edge_pct": 1,
            "vk2_projection": 1, "model_projection": 1,
            "vision_score": 1, "vision_score_raw": 1,
            "pp_multiplier_label": 1,
        },
    )
    raw = await cursor.to_list(length=10000)

    def _num(v: Any) -> float:
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    raw.sort(key=lambda r: (
        -_num(r.get("edge_pct")),
        -_num(r.get("vision_score")),
        r.get("canonical_key") or "",
    ))
    rows = raw[:TOP_N]

    out: List[Dict[str, Any]] = []
    for r in rows:
        side = (r.get("recommendation") or "").upper()
        hit = r.get("hit_rate_over") if side == "OVER" else r.get("hit_rate_under")
        proj = r.get("vk2_projection") or r.get("model_projection")
        out.append({
            "canonical_key": r.get("canonical_key"),
            "player": r.get("player_name"),
            "side":   side,
            "stat":   r.get("stat_type"),
            "line":   r.get("line"),
            "ref_odds": r.get("tier_reference_odds"),
            "ref_book": r.get("tier_reference_book"),
            "proj":   round(float(proj), 2) if isinstance(proj, (int, float)) else None,
            "tp":     r.get("tp"),
            "edge":   r.get("edge_pct"),
            "hit":    hit,
            "cv":     round(r.get("cv") or 0, 3),
            "vis":    round(float(r["vision_score"]), 1) if isinstance(r.get("vision_score"), (int, float)) else None,
            "gate":   (r.get("tier_reason") or "").replace("safe_haven_failed: ", ""),
            "pp":     r.get("pp_multiplier_label") or "-",
        })
    return out


def fmt_table(label: str, rows: List[Dict[str, Any]], digest: str, ts: str) -> str:
    out = [f"\n{'=' * 110}",
           f"{label}  captured_at={ts}  sha256={digest[:12]}…",
           "=" * 110,
           f"{'#':>2} | {'Player':<22} | {'Side':<5} | {'Stat':<32} | {'Line':<5} "
           f"| {'Odds':<11} | {'Proj':>7} | {'TP%':>5} | {'Edge%':>5} "
           f"| {'Hit%':>5} | {'CV':>5} | {'Vis':>5} | {'Failed Gate':<25} | PP",
           "-" * 110]
    for i, r in enumerate(rows, 1):
        odds = f"{r['ref_odds']} ({r['ref_book']})"
        proj = f"{r['proj']:>7.2f}" if r['proj'] is not None else f"{'—':>7}"
        vis = f"{r['vis']:>5.1f}" if r['vis'] is not None else f"{'—':>5}"
        out.append(
            f"{i:>2} | {r['player']:<22} | {r['side']:<5} | {r['stat']:<32} "
            f"| {str(r['line']):<5} | {odds:<11} | {proj} | {r['tp']:>5} "
            f"| {r['edge']:>5} | {r['hit']:>5} | {r['cv']:>5} | {vis} "
            f"| {r['gate']:<25} | {r['pp']}"
        )
    return "\n".join(out)


async def main() -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    handles = []
    for k in LOCK_KEYS:
        h = await acquire(db, k, ttl_seconds=LOCK_TTL,
                          holder="top20_reject_check")
        if h is None:
            for hh in handles:
                await release(db, hh)
            print(f"FAILED to acquire {k!r} — another writer active.")
            return 2
        handles.append(h)
    print(f"[LOCKS] held: {[h.lock_key for h in handles]}\n")

    try:
        ts1 = datetime.now(timezone.utc).isoformat()
        snap1 = await fetch_top_20(db)
        d1 = hashlib.sha256(json.dumps(snap1, sort_keys=True, default=str).encode()).hexdigest()
        print(fmt_table("SNAPSHOT 1 (locks held, delta blocked)", snap1, d1, ts1))

        print(f"\n[SLEEP] {SLEEP_SECONDS}s with delta engine blocked …")
        await asyncio.sleep(SLEEP_SECONDS)

        ts2 = datetime.now(timezone.utc).isoformat()
        snap2 = await fetch_top_20(db)
        d2 = hashlib.sha256(json.dumps(snap2, sort_keys=True, default=str).encode()).hexdigest()
        print(fmt_table("SNAPSHOT 2 (locks held, delta blocked)", snap2, d2, ts2))

        print("\n" + "=" * 110)
        print(f"[VERIFY] snap1.sha256 = {d1}")
        print(f"[VERIFY] snap2.sha256 = {d2}")
        print(f"[VERIFY] identical    = {d1 == d2}")
        if d1 != d2:
            print("\n[DIFFS]")
            for i, (a, b) in enumerate(zip(snap1, snap2), 1):
                if a != b:
                    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b)
                            if a.get(k) != b.get(k)}
                    print(f"  row #{i}: {diff}")
        return 0 if d1 == d2 else 1
    finally:
        for h in handles:
            await release(db, h)
        print(f"\n[LOCKS] released: {[h.lock_key for h in handles]}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
