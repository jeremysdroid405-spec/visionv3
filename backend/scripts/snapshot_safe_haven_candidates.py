"""
Safe Haven Candidate Snapshot — Frozen + Determinism Verified
=============================================================

User contract:

    1. Lock version_tag = final-nba-rt
    2. Capture timestamp
    3. Disable delta updates for the query
    4. Return ALL Safe Haven candidates (tier_reference_odds <= -240)
       with which gate failed + full metrics
    5. Re-run the SAME query 60s later → verify identical results

How "disable delta updates" works
---------------------------------
We acquire two cross-process advisory locks via `services.sync_lock`:

    * sync:nba       — blocks any full-sync writer
    * recompute:nba  — blocks the delta-engine recompute writer

Both are held for the full ~120 s window (snapshot + 60 s sleep + re-run +
header). The delta engine respects these locks (verified in
`services/delta_engine.py` — it `clean-skips` ticks while the recompute
lock is held). After the second snapshot completes, the locks are
released.

The query itself is read-only against `nba_prop_scores`. With writers
blocked, the underlying documents cannot mutate between snapshot 1 and
snapshot 2. We hash both snapshots and assert byte-for-byte equality on
every document. Mismatches are surfaced as a per-pick diff report.

Output
------
    /app/backend/data/snapshots/safe_haven_candidates_<ts>.json
    /app/backend/data/snapshots/safe_haven_candidates_<ts>.md   (table)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from services.sync_lock import acquire, release  # noqa: E402

# ---------------------------------------------------------------------
# Snapshot config — KEEP STABLE for determinism
# ---------------------------------------------------------------------
VERSION_TAG = "final-nba-rt"
SH_REF_ODDS_CEILING = -240   # Safe Haven gate: ref_odds <= -240
SLEEP_SECONDS = 60
LOCK_TTL = 240               # 4 min — covers query + sleep + query + slack
LOCK_KEYS = ["sync:nba", "recompute:nba"]

OUT_DIR = Path("/app/backend/data/snapshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
def _flatten_gate_results(g: Dict[str, Any]) -> Dict[str, Any]:
    """Pull each gate's pass/fail + value into a flat dict."""
    if not isinstance(g, dict):
        return {}
    flat: Dict[str, Any] = {}
    for gate_name, payload in g.items():
        if not isinstance(payload, dict):
            continue
        flat[f"{gate_name}__passed"]      = payload.get("passed")
        flat[f"{gate_name}__value"]       = payload.get("value")
        flat[f"{gate_name}__threshold"]   = payload.get("threshold")
        flat[f"{gate_name}__reason_code"] = payload.get("reason_code")
    return flat


def _normalize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Project to only the metrics we want; sort keys for stable hashing."""
    side = (doc.get("recommendation") or "").upper()
    hit_rate = (
        doc.get("hit_rate_over") if side == "OVER"
        else doc.get("hit_rate_under")
    )
    out = {
        # Identity (deterministic ordering key)
        "canonical_key":         doc.get("canonical_key"),
        "player_name":           doc.get("player_name"),
        "stat_type":             doc.get("stat_type"),
        "line":                  doc.get("line"),
        "recommendation":        side,
        "event_id":              doc.get("event_id"),
        # Tier outcome
        "tier":                  doc.get("tier"),
        "tier_reason":           doc.get("tier_reason"),
        "tier_reference_book":   doc.get("tier_reference_book"),
        "tier_reference_odds":   doc.get("tier_reference_odds"),
        # Vision / scoring
        "vision_score":          doc.get("vision_score"),
        "vision_score_raw":      doc.get("vision_score_raw"),
        # Probabilities / projection
        "model_projection":      doc.get("model_projection"),
        "vk2_projection":        doc.get("vk2_projection"),
        "p_true_active":         doc.get("p_true_active"),
        "p_true_method":         doc.get("p_true_method"),
        "fair_prob":             doc.get("fair_prob"),
        # Hit rates / volatility
        "hit_rate_over":         doc.get("hit_rate_over"),
        "hit_rate_under":        doc.get("hit_rate_under"),
        "hit_rate":              hit_rate,
        "hit_rate_sample_size":  doc.get("hit_rate_sample_size"),
        "cv":                    doc.get("cv"),
        # Devig + edge
        "tp":                    doc.get("tp"),
        "edge_pct":              doc.get("edge_pct"),
        "edge_vs_fair":          doc.get("edge_vs_fair"),
        "tp_books_used":         doc.get("tp_books_used"),
        "tp_books_list":         doc.get("tp_books_list"),
        "tp_method":             doc.get("tp_method"),
        # Coverage
        "book_count":            doc.get("book_count"),
        "coverage_class":        doc.get("coverage_class"),
        "playable_on_pp":        doc.get("playable_on_pp"),
        # PP layer
        "pp_multiplier_label":   doc.get("pp_multiplier_label"),
        "pp_utility":            doc.get("pp_utility"),
        # Gate audit (flattened)
        **_flatten_gate_results(doc.get("tier_gate_results") or {}),
    }
    return {k: out[k] for k in sorted(out)}


async def _fetch_snapshot(db) -> List[Dict[str, Any]]:
    """ALL Safe Haven candidates from final-nba-rt, sorted deterministically."""
    cursor = db.nba_prop_scores.find(
        {
            "version_tag":         VERSION_TAG,
            "tier_reference_odds": {"$lte": SH_REF_ODDS_CEILING},
        },
        {"_id": 0},
    )
    docs: List[Dict[str, Any]] = []
    async for d in cursor:
        docs.append(_normalize_doc(d))

    # Deterministic order: canonical_key (None last), then player + stat + line.
    def _sort_key(d: Dict[str, Any]) -> Tuple[int, str, str, str, float]:
        ck = d.get("canonical_key")
        return (
            1 if ck is None else 0,
            ck or "",
            d.get("player_name") or "",
            d.get("stat_type") or "",
            float(d.get("line") or 0),
        )

    docs.sort(key=_sort_key)
    return docs


def _digest(snap: List[Dict[str, Any]]) -> str:
    payload = json.dumps(snap, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _per_doc_diff(a: List[Dict[str, Any]],
                  b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If digests differ, surface ROW-level deltas for forensic analysis."""
    diffs: List[Dict[str, Any]] = []
    a_by = {d.get("canonical_key") or
            f"{d.get('player_name')}|{d.get('stat_type')}|{d.get('line')}|{d.get('recommendation')}": d
            for d in a}
    b_by = {d.get("canonical_key") or
            f"{d.get('player_name')}|{d.get('stat_type')}|{d.get('line')}|{d.get('recommendation')}": d
            for d in b}
    only_a = sorted(set(a_by) - set(b_by))
    only_b = sorted(set(b_by) - set(a_by))
    for k in only_a:
        diffs.append({"key": k, "kind": "only_in_snap1"})
    for k in only_b:
        diffs.append({"key": k, "kind": "only_in_snap2"})
    for k in sorted(set(a_by) & set(b_by)):
        if a_by[k] != b_by[k]:
            field_diffs = {}
            for f in set(a_by[k]) | set(b_by[k]):
                if a_by[k].get(f) != b_by[k].get(f):
                    field_diffs[f] = (a_by[k].get(f), b_by[k].get(f))
            diffs.append({"key": k, "kind": "value_diff", "fields": field_diffs})
    return diffs


def _render_markdown(snap: List[Dict[str, Any]],
                     captured_at: str,
                     digest: str) -> str:
    lines: List[str] = [
        f"# Safe Haven Candidate Snapshot — frozen {captured_at}",
        "",
        f"- `version_tag`: **{VERSION_TAG}**",
        f"- Filter: `tier_reference_odds ≤ {SH_REF_ODDS_CEILING}`",
        f"- Total candidates: **{len(snap)}**",
        f"- Snapshot SHA-256: `{digest}`",
        "",
        ("| # | Player | Side | Stat | Line | Ref Odds (book) | Tier | Failed Gate "
         "| Proj | TP% | Edge% | Hit% | CV | Vision "
         "| HitGate | VisGate | CVGate | MktGate |"),
        "|---|--------|------|------|------|-----------------|------|-------------"
        "|------|-----|-------|------|-----|--------"
        "|---------|---------|--------|---------|",
    ]
    for i, d in enumerate(snap, 1):
        proj = d.get("vk2_projection") or d.get("model_projection") or 0
        proj = f"{float(proj):.2f}" if proj else "—"
        ref = f"{d.get('tier_reference_odds')} ({d.get('tier_reference_book')})"
        fail = (d.get("tier_reason") or "").replace("safe_haven_failed: ", "") or "—"
        gates = (d.get("hit_rate_gate__passed"),
                 d.get("vision_score_gate__passed"),
                 d.get("cv_gate__passed"),
                 d.get("market_structure_gate__passed"))
        gate_marks = ["✅" if g is True else ("❌" if g is False else "—") for g in gates]
        vis = d.get("vision_score")
        vis_s = f"{vis:.1f}" if isinstance(vis, (int, float)) else "—"
        lines.append(
            f"| {i} | {d.get('player_name')} | {d.get('recommendation')} "
            f"| {d.get('stat_type')} | {d.get('line')} | {ref} | "
            f"{d.get('tier') or '—'} | {fail} "
            f"| {proj} | {d.get('tp')} | {d.get('edge_pct')} "
            f"| {d.get('hit_rate')} | {round(d.get('cv') or 0, 3)} | {vis_s} "
            f"| {gate_marks[0]} | {gate_marks[1]} | {gate_marks[2]} | {gate_marks[3]} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------
async def main() -> int:
    mongo_url = os.environ["MONGO_URL"]
    db_name   = os.environ["DB_NAME"]
    db = AsyncIOMotorClient(mongo_url)[db_name]

    # 1. Acquire delta-blocking locks
    handles = []
    for k in LOCK_KEYS:
        h = await acquire(db, k, ttl_seconds=LOCK_TTL,
                          holder="snapshot_safe_haven_candidates")
        if h is None:
            # Roll back any acquired locks before bailing.
            for hh in handles:
                await release(db, hh)
            print(f"[SNAPSHOT] FAILED to acquire {k!r} — another writer "
                  f"is active. Retry in a few seconds.")
            return 2
        handles.append(h)
    print(f"[SNAPSHOT] locks held: {[h.lock_key for h in handles]}")

    try:
        # 2. Capture timestamp + first snapshot
        ts1 = datetime.now(timezone.utc).isoformat()
        snap1 = await _fetch_snapshot(db)
        digest1 = _digest(snap1)
        print(f"[SNAPSHOT 1] captured_at={ts1} count={len(snap1)} sha256={digest1}")

        # 3. Sleep — delta engine remains blocked by sync_locks
        print(f"[SNAPSHOT] sleeping {SLEEP_SECONDS}s before re-query …")
        await asyncio.sleep(SLEEP_SECONDS)

        # 4. Re-query
        ts2 = datetime.now(timezone.utc).isoformat()
        snap2 = await _fetch_snapshot(db)
        digest2 = _digest(snap2)
        print(f"[SNAPSHOT 2] captured_at={ts2} count={len(snap2)} sha256={digest2}")

        # 5. Verify identical
        identical = (digest1 == digest2)
        print(f"[VERIFY] identical={identical}")
        diffs: List[Dict[str, Any]] = []
        if not identical:
            diffs = _per_doc_diff(snap1, snap2)
            print(f"[VERIFY] {len(diffs)} per-doc diff(s) detected.")
        else:
            print("[VERIFY] PASS — both snapshots are byte-identical.")

        # 6. Persist artifacts
        stamp = ts1.replace(":", "").replace("-", "").split(".")[0]
        json_path = OUT_DIR / f"safe_haven_candidates_{stamp}.json"
        md_path   = OUT_DIR / f"safe_haven_candidates_{stamp}.md"
        artifact = {
            "version_tag":         VERSION_TAG,
            "filter":              {"tier_reference_odds_lte": SH_REF_ODDS_CEILING},
            "delta_locks_held":    LOCK_KEYS,
            "snapshot_1": {
                "captured_at": ts1,
                "count":       len(snap1),
                "sha256":      digest1,
            },
            "snapshot_2": {
                "captured_at": ts2,
                "count":       len(snap2),
                "sha256":      digest2,
            },
            "identical":           identical,
            "diffs":               diffs,
            "candidates":          snap1,
        }
        json_path.write_text(json.dumps(artifact, indent=2, default=str))
        md_path.write_text(_render_markdown(snap1, ts1, digest1))
        print(f"[ARTIFACT] {json_path}")
        print(f"[ARTIFACT] {md_path}")
        return 0 if identical else 1
    finally:
        # 7. Always release locks even on exception
        for h in handles:
            await release(db, h)
        print(f"[SNAPSHOT] locks released: {[h.lock_key for h in handles]}")


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
