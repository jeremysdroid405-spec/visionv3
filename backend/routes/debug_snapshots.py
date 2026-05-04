"""
Debug Snapshots — Tuning-only frozen views
==========================================

Analysis-only endpoints used during threshold tuning. Held tightly to
two rules:

1. **Live tier endpoints stay unchanged.** This module never imports or
   patches `routes/ferrari_tiers.py`. The user-facing dashboard always
   sees fresh, lock-free data.

2. **Lock-protected endpoints are admin-gated.** `ADMIN_DEBUG_TOKEN`
   must match the `X-Admin-Token` header. If the env var is unset, the
   endpoint replies 503 (disabled by default in any environment that
   hasn't opted in). This prevents accidental dashboard hits from
   blocking real-time recompute.

3. **Locks are always released.** Try/finally around every acquire.

4. **Read-only.** Zero writes to `nba_prop_scores` / model state.
   No scoring / gates / thresholds / tier-routing touched.

Endpoints
---------
GET /api/debug/snapshots/safe-haven-rejects?sport=nba&top=20&freeze=true
    Returns the deterministic top-N safe-haven rejects (sorted by
    edge_vs_fair DESC, vision_score DESC, canonical_key ASC). When
    freeze=true (default) the recompute / sync locks are held for
    the duration of the read.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response

from config.version_tags import LIVE_TAG_BY_SPORT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug/snapshots", tags=["Debug Snapshots"])

_db = None  # Injected at startup

# Reuse the snapshot config from the script for parity.
SH_REF_ODDS_CEILING = -240
DEFAULT_VERSION_TAG = dict(LIVE_TAG_BY_SPORT)
LOCK_TTL_SECONDS = 60   # tight TTL — analysis reads are quick
MAX_TOP = 100


def set_db(db) -> None:
    global _db
    _db = db


def _require_admin_debug_token(provided: Optional[str]) -> None:
    expected = os.environ.get("ADMIN_DEBUG_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_DEBUG_TOKEN not configured; debug endpoint disabled",
        )
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else float("-inf")


def _normalize_reject(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Project to the analysis fields. Same shape the script produces."""
    side = (doc.get("recommendation") or "").upper()
    # SSOT Tier F (2026-05-04): canonical L20 OVER read is `hit_rate_l20`;
    # legacy `hit_rate_over` retained as fallback for pre-dual-write docs.
    if side == "OVER":
        hit = doc.get("hit_rate_l20") or doc.get("hit_rate_over")
    else:
        hit = doc.get("hit_rate_under")
    proj = doc.get("vk2_projection") or doc.get("model_projection")
    gates = doc.get("tier_gate_results") or {}

    def _g(k):
        g = gates.get(k) or {}
        return {
            "passed":      g.get("passed"),
            "value":       g.get("value"),
            "threshold":   g.get("threshold"),
            "reason_code": g.get("reason_code"),
        }

    return {
        "canonical_key":       doc.get("canonical_key"),
        "player_name":         doc.get("player_name"),
        "team":                doc.get("team"),
        "stat_type":           doc.get("stat_type"),
        "line":                doc.get("line"),
        "recommendation":      side,
        "tier":                doc.get("tier"),
        "tier_reason":         doc.get("tier_reason"),
        "tier_reference_book": doc.get("tier_reference_book"),
        "tier_reference_odds": doc.get("tier_reference_odds"),
        "vision_score":        doc.get("vision_score"),
        "vision_score_raw":    doc.get("vision_score_raw"),
        "model_projection":    doc.get("model_projection"),
        "vk2_projection":      doc.get("vk2_projection"),
        "projection":          proj,
        "p_true_active":       doc.get("p_true_active"),
        "fair_prob":           doc.get("fair_prob"),
        "hit_rate_over":       doc.get("hit_rate_over"),       # legacy alias surfaced for back-compat
        "hit_rate_l20":        doc.get("hit_rate_l20") or doc.get("hit_rate_over"),  # canonical
        "hit_rate_under":      doc.get("hit_rate_under"),
        "hit_rate":            hit,
        "cv":                  doc.get("cv"),
        "tp":                  doc.get("tp"),
        # SSOT Tier F #2 (2026-05-04): canonical `edge_vs_fair` only;
        # legacy `edge_pct` surfaced-alias stamp removed.
        "edge_vs_fair":        doc.get("edge_vs_fair"),
        "tp_books_used":       doc.get("tp_books_used"),
        "book_count":          doc.get("book_count"),
        "coverage_class":      doc.get("coverage_class"),
        "playable_on_pp":      doc.get("playable_on_pp"),
        "pp_multiplier_label": doc.get("pp_multiplier_label"),
        "pp_utility":          doc.get("pp_utility"),
        "gates": {
            "hit_rate_gate":       _g("hit_rate_gate"),
            "vision_score_gate":   _g("vision_score_gate"),
            "cv_gate":             _g("cv_gate"),
            "market_structure_gate": _g("market_structure_gate"),
        },
    }


async def _query_rejects(db, sport: str, top: int) -> List[Dict[str, Any]]:
    version_tag = DEFAULT_VERSION_TAG[sport]
    cursor = db[f"{sport}_prop_scores"].find(
        {
            "version_tag":         version_tag,
            "tier_reference_odds": {"$lte": SH_REF_ODDS_CEILING},
            "tier":                {"$ne": "safe_haven"},
        },
        {"_id": 0},
    )
    raw = await cursor.to_list(length=10000)
    # SSOT Tier F #2 (2026-05-04): sort on canonical `edge_vs_fair`.
    raw.sort(key=lambda r: (
        -_num(r.get("edge_vs_fair")),
        -_num(r.get("vision_score")),
        r.get("canonical_key") or "",
    ))
    return [_normalize_reject(d) for d in raw[:top]]


@router.get("/safe-haven-rejects")
async def safe_haven_rejects(
    response: Response,
    sport: str = Query("nba", description="Sport: nba or mlb"),
    top: int = Query(20, ge=1, le=MAX_TOP, description="Top-N to return"),
    freeze: bool = Query(
        True,
        description=(
            "When true (default), holds `recompute:{sport}` and "
            "`sync:{sport}` locks for the duration of the read so the "
            "result is reproducible. Tight TTL (~60s) — does NOT block "
            "the user dashboard's normal live read paths."
        ),
    ),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """Top-N Safe Haven rejects, deterministic, admin-gated, opt-in freeze.

    Sort key tuple: `edge_vs_fair DESC, vision_score DESC, canonical_key ASC`.
    Returns SHA-256 of the sorted payload so two consecutive calls can be
    compared trivially.
    """
    _require_admin_debug_token(x_admin_token)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialized")
    sport = sport.lower()
    if sport not in ("nba", "mlb"):
        raise HTTPException(status_code=400, detail="sport must be nba or mlb")

    handles = []
    if freeze:
        # Acquire locks for the duration of the read. TTL is tight to
        # avoid impacting recompute if this handler hangs.
        from services.sync_lock import acquire, release
        for k in (f"sync:{sport}", f"recompute:{sport}"):
            h = await acquire(_db, k, ttl_seconds=LOCK_TTL_SECONDS,
                              holder="debug_snapshot_safe_haven_rejects")
            if h is None:
                # Roll back partial locks so a busy slot doesn't strand
                # the resource.
                for hh in handles:
                    await release(_db, hh)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"could not acquire {k!r} lock — a writer is "
                        "active. Retry shortly or pass ?freeze=false."
                    ),
                )
            handles.append(h)

    try:
        captured_at = datetime.now(timezone.utc).isoformat()
        rows = await _query_rejects(_db, sport, top)
        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode()
        ).hexdigest()

        return {
            "sport":              sport,
            "version_tag":        DEFAULT_VERSION_TAG[sport],
            "filter":             {"tier_reference_odds_lte": SH_REF_ODDS_CEILING,
                                   "exclude_tier":            "safe_haven"},
            "sort_key":           ["edge_vs_fair DESC", "vision_score DESC",
                                   "canonical_key ASC"],
            "freeze":             freeze,
            "delta_locks_held":   [h.lock_key for h in handles],
            "captured_at":        captured_at,
            "count":              len(rows),
            "sha256":             digest,
            "rejects":            rows,
        }
    finally:
        if handles:
            from services.sync_lock import release
            for h in handles:
                await release(_db, h)


# ===================================================================== #
# Shadow Board (Vision v2) — read-only comparison vs production         #
# ===================================================================== #
def _is_under_side(rec: Optional[str]) -> bool:
    return "UNDER" in (rec or "").upper()


def _bucket_from_state(rows: List[Dict[str, Any]],
                       cfg) -> Dict[str, List[str]]:
    """Group canonical_keys by side for a given board state slice."""
    out: Dict[str, List[str]] = {"OVER": [], "UNDER": [], "combined": []}
    for r in rows:
        side = r.get("side")
        if side in (None, "combined"):
            out["combined"].append(r.get("canonical_key"))
        elif "UNDER" in (side or ""):
            out["UNDER"].append(r.get("canonical_key"))
        else:
            out["OVER"].append(r.get("canonical_key"))
    return out


@router.get("/shadow_board/compare")
async def shadow_board_compare(
    response: Response,
    sport: str = Query("nba", description="Sport (only nba supported today)"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """Compare production `board_state` vs shadow `board_state_shadow`
    (v2 ranking) per (tier, side). Read-only; never writes.

    Returns per-bucket:
        prod_keys, shadow_keys, overlap_pct,
        added_by_v2 (in shadow but not prod),
        removed_by_v2 (in prod but not shadow),
        prod_metrics  : {avg p, avg edge, avg align, wrong_side_count}
        shadow_metrics: same for shadow board.
    """
    _require_admin_debug_token(x_admin_token)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        raise HTTPException(status_code=500, detail="db not initialized")

    sport = sport.lower()
    if sport != "nba":
        raise HTTPException(status_code=400,
                            detail="shadow currently NBA-only")

    from services.board.publisher import (
        TIER_CONFIG, get_published_board,
    )
    from services.board.shadow_publisher import get_shadow_board

    # Pull score docs once for metric joining.
    score_docs: Dict[str, Dict[str, Any]] = {}
    cursor = _db[f"{sport}_prop_scores"].find(
        {"version_tag": DEFAULT_VERSION_TAG[sport]},
        {"_id": 0, "canonical_key": 1, "player_name": 1, "stat_type": 1,
         "line": 1, "recommendation": 1, "tier": 1, "p_true_active": 1,
         # SSOT Tier F #2: canonical edge field is `edge_vs_fair`.
         "edge_vs_fair": 1, "tp": 1, "vision_score": 1, "vision_score_v2": 1,
         "vision_direction_alignment": 1},
    )
    async for d in cursor:
        ck = d.get("canonical_key")
        if ck:
            score_docs[ck] = d

    def _agg(keys: List[str]) -> Dict[str, Any]:
        ds = [score_docs.get(k) for k in keys if score_docs.get(k)]
        if not ds:
            return {"n": 0}
        ps = [d.get("p_true_active") for d in ds if isinstance(d.get("p_true_active"), (int, float))]
        # SSOT Tier F #2: canonical edge field is `edge_vs_fair`.
        es = [d.get("edge_vs_fair") for d in ds if isinstance(d.get("edge_vs_fair"), (int, float))]
        als = [d.get("vision_direction_alignment") for d in ds
               if isinstance(d.get("vision_direction_alignment"), (int, float))]
        wrong = sum(1 for d in ds
                    if isinstance(d.get("vision_direction_alignment"), (int, float))
                    and d["vision_direction_alignment"] < 0)
        return {
            "n":                   len(ds),
            "avg_p_true_active":   round(sum(ps) / len(ps), 4) if ps else None,
            "avg_edge_vs_fair":    round(sum(es) / len(es), 4) if es else None,
            "avg_direction_alignment":
                                  round(sum(als) / len(als), 4) if als else None,
            "wrong_side_count":    wrong,
        }

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport":        sport,
        "version_tag":  DEFAULT_VERSION_TAG[sport],
        "rank_tuple_prod":   ["ranking_score DESC", "vision_score DESC",
                              "edge_vs_fair DESC", "canonical_key ASC"],
        "rank_tuple_shadow": ["ranking_score DESC", "vision_score_v2 DESC",
                              "edge_vs_fair DESC", "canonical_key ASC"],
        "buckets": [],
    }

    overall_top20_diffs: List[Dict[str, Any]] = []

    for tier, cfg in TIER_CONFIG.items():
        sides = ("OVER", "UNDER") if cfg["split_by_side"] else (None,)
        for side in sides:
            prod = await get_published_board(_db, sport, tier, side=side)
            shadow = await get_shadow_board(_db, sport, tier, side=side)
            prod_keys = [r.get("canonical_key") for r in prod]
            shadow_keys = [r.get("canonical_key") for r in shadow]

            common = set(prod_keys) & set(shadow_keys)
            denom = max(len(prod_keys), len(shadow_keys), 1)
            overlap_pct = round(len(common) / denom * 100.0, 1)

            added = [k for k in shadow_keys if k not in set(prod_keys)]
            removed = [k for k in prod_keys if k not in set(shadow_keys)]

            # Rank-order diff: same membership, different positions.
            prod_rank_by_key = {ck: i + 1 for i, ck in enumerate(prod_keys)}
            shadow_rank_by_key = {ck: i + 1 for i, ck in enumerate(shadow_keys)}
            rank_changes: List[Dict[str, Any]] = []
            for ck in common:
                p = prod_rank_by_key.get(ck)
                s = shadow_rank_by_key.get(ck)
                if p != s:
                    d = score_docs.get(ck) or {}
                    rank_changes.append({
                        "canonical_key": ck,
                        "player_name":   d.get("player_name"),
                        "stat_type":     d.get("stat_type"),
                        "line":          d.get("line"),
                        "recommendation": d.get("recommendation"),
                        "prod_rank":     p,
                        "shadow_rank":   s,
                        "delta":         (p - s) if (p and s) else None,
                        "vision_score":     d.get("vision_score"),
                        "vision_score_v2":  d.get("vision_score_v2"),
                        "vision_direction_alignment":
                                          d.get("vision_direction_alignment"),
                    })
            rank_changes.sort(key=lambda r: -abs(r.get("delta") or 0))

            bucket = {
                "tier":        tier,
                "side":        side or "combined",
                "capacity":    cfg["capacity_per_side"],
                "prod_count":  len(prod_keys),
                "shadow_count": len(shadow_keys),
                "overlap_pct": overlap_pct,
                "rank_changes_count": len(rank_changes),
                "rank_changes": rank_changes,
                "added_by_v2": [
                    {
                        "canonical_key":          ck,
                        "player_name":           (score_docs.get(ck) or {}).get("player_name"),
                        "stat_type":             (score_docs.get(ck) or {}).get("stat_type"),
                        "line":                  (score_docs.get(ck) or {}).get("line"),
                        "side":                  (score_docs.get(ck) or {}).get("recommendation"),
                        "vision_score":          (score_docs.get(ck) or {}).get("vision_score"),
                        "vision_score_v2":       (score_docs.get(ck) or {}).get("vision_score_v2"),
                        "vision_direction_alignment":
                                                (score_docs.get(ck) or {}).get("vision_direction_alignment"),
                    }
                    for ck in added[:50]
                ],
                "removed_by_v2": [
                    {
                        "canonical_key":          ck,
                        "player_name":           (score_docs.get(ck) or {}).get("player_name"),
                        "stat_type":             (score_docs.get(ck) or {}).get("stat_type"),
                        "line":                  (score_docs.get(ck) or {}).get("line"),
                        "side":                  (score_docs.get(ck) or {}).get("recommendation"),
                        "vision_score":          (score_docs.get(ck) or {}).get("vision_score"),
                        "vision_score_v2":       (score_docs.get(ck) or {}).get("vision_score_v2"),
                        "vision_direction_alignment":
                                                (score_docs.get(ck) or {}).get("vision_direction_alignment"),
                    }
                    for ck in removed[:50]
                ],
                "prod_metrics":   _agg(prod_keys),
                "shadow_metrics": _agg(shadow_keys),
            }
            out["buckets"].append(bucket)

            # Top differences for the headline summary.
            for ck in added:
                d = score_docs.get(ck) or {}
                overall_top20_diffs.append({
                    "kind": "added_by_v2",
                    "tier": tier, "side": side or "combined",
                    **{k: d.get(k) for k in
                       ("player_name", "stat_type", "line", "recommendation",
                        "vision_score", "vision_score_v2",
                        "vision_direction_alignment", "edge_vs_fair",
                        "p_true_active")},
                })
            for ck in removed:
                d = score_docs.get(ck) or {}
                overall_top20_diffs.append({
                    "kind": "removed_by_v2",
                    "tier": tier, "side": side or "combined",
                    **{k: d.get(k) for k in
                       ("player_name", "stat_type", "line", "recommendation",
                        "vision_score", "vision_score_v2",
                        "vision_direction_alignment", "edge_vs_fair",
                        "p_true_active")},
                })

    # Sort top differences by absolute v2 - v1 vision delta when both exist.
    def _delta(d):
        v1 = d.get("vision_score") or 0
        v2 = d.get("vision_score_v2") or 0
        return -abs(v2 - v1)

    overall_top20_diffs.sort(key=_delta)
    out["top_20_differences"] = overall_top20_diffs[:20]
    return out
