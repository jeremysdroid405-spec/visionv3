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
    edge_pct DESC, vision_score DESC, canonical_key ASC). When
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug/snapshots", tags=["Debug Snapshots"])

_db = None  # Injected at startup

# Reuse the snapshot config from the script for parity.
SH_REF_ODDS_CEILING = -240
DEFAULT_VERSION_TAG = {"nba": "final-nba-rt", "mlb": "final-mlb-rt"}
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
    hit = doc.get("hit_rate_over") if side == "OVER" else doc.get("hit_rate_under")
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
        "hit_rate_over":       doc.get("hit_rate_over"),
        "hit_rate_under":      doc.get("hit_rate_under"),
        "hit_rate":            hit,
        "cv":                  doc.get("cv"),
        "tp":                  doc.get("tp"),
        "edge_pct":            doc.get("edge_pct"),
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
    raw.sort(key=lambda r: (
        -_num(r.get("edge_pct")),
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

    Sort key tuple: `edge_pct DESC, vision_score DESC, canonical_key ASC`.
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
            "sort_key":           ["edge_pct DESC", "vision_score DESC",
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
