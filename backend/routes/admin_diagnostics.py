"""Admin diagnostics — duplicate prop detection + probability trace.

Endpoints (2026-05-14):
  - GET /api/v3/admin/duplicate-props?sport=mlb
  - GET /api/v3/admin/probability-trace?sport=mlb&player=...&stat=...&line=...&side=...

Both are read-only and unauthenticated by spec (project has no auth
yet). Light-weight: hit the live scored collection directly.

DB is injected at startup via `set_db(db)` — matches the pattern used
by `routes.health_sync` and `routes.debug_snapshots`. Don't pull from
`request.app.state` (that path isn't populated by server.py).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/admin", tags=["admin-diagnostics"])

_VALID_SPORTS = {"mlb", "nba"}

_db = None  # Injected at startup via set_db()


def set_db(db) -> None:
    global _db
    _db = db


def _require_db():
    if _db is None:
        raise HTTPException(503, "diagnostics db not initialized")
    return _db


@router.get("/duplicate-props")
async def duplicate_props(
    sport: str = Query("mlb"),
    limit: int = Query(50, ge=1, le=500),
):
    """Return all active duplicate canonical-prop clusters.

    Duplicate definition (per spec):
        sport + game_id + player + stat_family + line + side
        must be unique across the active pool. Multiple `active=True`
        rows under different `version_tag` for the same canonical key
        are duplicates.

    Response includes:
        - duplicate_key tuple
        - cluster size (count)
        - player, stat, line, side
        - ref_books involved (set)
        - version_tags involved (set)
        - source collection (always {sport}_prop_scores)
        - p_model / model_projection per row (to surface staleness)
    """
    sport = (sport or "").lower()
    if sport not in _VALID_SPORTS:
        raise HTTPException(400, f"unsupported sport: {sport}")

    db = _require_db()
    coll = db[f"{sport}_prop_scores"]

    groups: dict = defaultdict(list)
    cursor = coll.find(
        {"active": True},
        {
            "_id": 0,
            "canonical_key": 1,
            "event_id": 1,
            "player_name": 1,
            "stat_type": 1,
            "line": 1,
            "recommendation": 1,
            "version_tag": 1,
            "tier": 1,
            "tier_reference_book": 1,
            "tier_reference_odds": 1,
            "model_projection": 1,
            "p_true_active": 1,
            "computed_at": 1,
            "market_key": 1,
            "is_alternate_market": 1,
        },
    )
    total_active = 0
    async for d in cursor:
        total_active += 1
        key = (
            d.get("event_id"),
            d.get("player_name"),
            d.get("stat_type"),
            d.get("line"),
            (d.get("recommendation") or "").upper(),
        )
        groups[key].append(d)

    dups = {k: v for k, v in groups.items() if len(v) > 1}
    clusters_payload = []
    for key, rows in sorted(dups.items(), key=lambda kv: -len(kv[1]))[:limit]:
        eid, pl, st, ln, sd = key
        clusters_payload.append({
            "duplicate_key": {
                "sport": sport,
                "event_id": eid,
                "player_name": pl,
                "stat_type": st,
                "line": ln,
                "side": sd,
            },
            "count": len(rows),
            "version_tags_involved": sorted({r.get("version_tag") for r in rows}),
            "ref_books_involved": sorted(
                {r.get("tier_reference_book") for r in rows if r.get("tier_reference_book")}
            ),
            "market_keys_involved": sorted(
                {r.get("market_key") for r in rows if r.get("market_key")},
                key=lambda x: x or "",
            ),
            "source_collection": f"{sport}_prop_scores",
            "rows": [
                {
                    "version_tag": r.get("version_tag"),
                    "canonical_key": r.get("canonical_key"),
                    "tier": r.get("tier"),
                    "tier_reference_book": r.get("tier_reference_book"),
                    "tier_reference_odds": r.get("tier_reference_odds"),
                    "model_projection": r.get("model_projection"),
                    "p_true_active": r.get("p_true_active"),
                    "computed_at": (
                        r.get("computed_at").isoformat()
                        if hasattr(r.get("computed_at"), "isoformat")
                        else r.get("computed_at")
                    ),
                }
                for r in rows
            ],
        })

    return {
        "sport": sport,
        "source_collection": f"{sport}_prop_scores",
        "active_rows_total": total_active,
        "distinct_keys": len(groups),
        "duplicate_clusters_total": len(dups),
        "redundant_rows_total": sum(len(v) - 1 for v in dups.values()),
        "clusters_returned": len(clusters_payload),
        "limit": limit,
        "clusters": clusters_payload,
    }


@router.get("/probability-trace")
async def probability_trace(
    sport: str = Query("mlb"),
    player: str = Query(...),
    stat: str = Query(...),
    line: float = Query(...),
    side: str = Query("OVER"),
    version_tag: Optional[str] = Query(None),
):
    """Return the full probability-derivation chain for a single prop.

    Pulls from the live tag (default `final-{sport}-rt`) unless an
    explicit `version_tag` query is provided. Returns:
      - projection chain (raw → EB → final)
      - sigma + distribution kind
      - p_model (live) + all shadow probabilities (ECDF / Gaussian)
      - TP + market consensus + best-book + all 3 edges
      - failed gates
    """
    sport = (sport or "").lower()
    if sport not in _VALID_SPORTS:
        raise HTTPException(400, f"unsupported sport: {sport}")

    side = side.upper()
    if side not in ("OVER", "UNDER"):
        raise HTTPException(400, f"side must be OVER or UNDER, got: {side}")

    db = _require_db()
    coll = db[f"{sport}_prop_scores"]
    tag = version_tag or f"final-{sport}-rt"

    docs = await coll.find(
        {
            "active": True,
            "version_tag": tag,
            "player_name": player,
            "stat_type": stat,
            "line": line,
            "recommendation": side,
        },
        {"_id": 0},
    ).to_list(length=10)

    if not docs:
        raise HTTPException(
            404,
            f"no active row for player={player!r} stat={stat!r} "
            f"line={line} side={side} tag={tag!r}",
        )

    trace = []
    for d in docs:
        gates = d.get("tier_gate_results") or {}
        failed = []
        if isinstance(gates, dict):
            for gname, gval in gates.items():
                if isinstance(gval, dict) and gval.get("passed") is False:
                    failed.append({
                        "gate": gname,
                        "actual": gval.get("actual"),
                        "threshold": gval.get("threshold"),
                        "reason": gval.get("reason"),
                    })

        trace.append({
            "player": d.get("player_name"),
            "stat_family": d.get("stat_type"),
            "line": d.get("line"),
            "side": d.get("recommendation"),
            "event_id": d.get("event_id"),
            "version_tag": d.get("version_tag"),
            "canonical_key": d.get("canonical_key"),
            # ── projection chain ──
            "projection_raw_hf":          d.get("raw_hf_projection"),
            "projection_post_eb":         d.get("eb_shrunk_projection"),
            "projection_final":           d.get("model_projection"),
            "projection_method":          d.get("projection_method"),
            "eb_shrinkage_applied":       d.get("eb_shrinkage_applied"),
            "eb_player_career_mean":      d.get("eb_player_career_mean"),
            "eb_weight_model":            d.get("eb_weight_model"),
            "eb_weight_player":           d.get("eb_weight_player"),
            "mu_raw_model_projection":    d.get("mu_raw_model_projection"),
            # ── distribution layer ──
            "distribution_used":          d.get("distribution_kind"),
            "distribution_selector":      d.get("distribution_selector_reason"),
            "distribution_effective_mu":  d.get("distribution_effective_mu"),
            "distribution_sigma":         d.get("distribution_sigma"),
            "distribution_sigma_source":  d.get("distribution_sigma_source"),
            "distribution_mu_floor_applied":  d.get("distribution_mu_floor_applied"),
            "distribution_mu_floor_capped":   d.get("distribution_mu_floor_capped"),
            "distribution_cv_floor_applied":  d.get("distribution_cv_floor_applied"),
            "distribution_clamped":           d.get("distribution_clamped"),
            "distribution_p_over":            d.get("distribution_p_over"),
            "distribution_p_under":           d.get("distribution_p_under"),
            "model_sigma_hf":             d.get("model_sigma"),
            # ── probability outputs ──
            "p_model_live":               d.get("p_true_active"),
            "p_model_method":             d.get("p_true_method"),
            "probability_method":         d.get("probability_method"),
            "raw_gaussian_p_over_shadow": d.get("raw_gaussian_p_over"),
            "ecdf_p_over_shadow":         d.get("ecdf_p_over"),
            "ecdf_bucket":                d.get("ecdf_bucket"),
            "ecdf_bucket_n":              d.get("ecdf_bucket_n"),
            # ── hit-rate context ──
            "cv":         d.get("cv"),
            "cv_status":  d.get("cv_status"),
            "hr_l20":     d.get("hit_rate_l20"),
            "hr_l10":     d.get("hit_rate_l10"),
            "hr_l5":      d.get("hit_rate_l5"),
            "hit_rate_over":  d.get("hit_rate_over"),
            "hit_rate_under": d.get("hit_rate_under"),
            # ── market + edges ──
            "tp":              d.get("tp"),
            "tp_source":       d.get("tp_source"),
            "tp_method":       d.get("tp_method"),
            "tp_books_used":   d.get("tp_books_used"),
            "best_book":       d.get("best_book"),
            "best_book_odds":  d.get("best_book_odds"),
            "best_book_implied_probability": d.get("best_book_implied_probability"),
            "model_edge":      d.get("edge_vs_fair"),
            "shopping_edge":   d.get("best_book_edge"),
            "total_edge":      d.get("total_edge"),
            # ── tier outcome ──
            "tier":         d.get("tier"),
            "tier_reason":  d.get("tier_reason"),
            "gate_failures": failed,
        })

    return {
        "sport": sport,
        "source_collection": f"{sport}_prop_scores",
        "version_tag_queried": tag,
        "rows_found": len(trace),
        "rows": trace,
    }



# ─────────────────────────────────────────────────────────────────────
# Best-bet edge audit (2026-05-14)
# ─────────────────────────────────────────────────────────────────────
@router.get("/best-bet-edge-audit")
async def best_bet_edge_audit(
    sport: str = Query("mlb"),
    top_n: int = Query(25, ge=1, le=100),
):
    """Consensus Edge + Best Bet Edge slate audit (universal, 2026-05-14).

    Reads the existing canonical fields — NO new schema, NO new
    aliases. The math has been in place since 2026-05-13:

        Consensus Edge   = `edge_vs_fair`  (p_model − consensus_fair)
        Best Bet Edge    = `total_edge`    (p_model − best_book_implied)
        Best Bet Book    = `best_book`     + `best_book_odds`

    Returns the slate snapshots the user asked for:
      • top N props by Best Bet Edge
      • top N props where Consensus Edge is modest (|x|<3%) but
        Best Bet Edge is strong (≥5%) — shopping carries the bet
      • best_book frequency distribution
      • market_spread_label distribution
      • count of props missing a best-book selection
    """
    sport = (sport or "").lower()
    if sport not in _VALID_SPORTS:
        raise HTTPException(400, f"unsupported sport: {sport}")

    db = _require_db()
    coll = db[f"{sport}_prop_scores"]
    base_filter = {"active": True, "version_tag": f"final-{sport}-rt"}

    proj = {
        "_id": 0,
        "player_name": 1, "stat_type": 1, "line": 1,
        "recommendation": 1, "tier": 1,
        "edge_vs_fair": 1,
        "total_edge": 1,
        "best_book": 1, "best_book_odds": 1,
        "best_book_implied_probability": 1,
        "best_book_edge": 1,
        "market_spread": 1, "market_spread_label": 1,
        "books_available_count": 1,
    }

    rows: list = []
    async for d in coll.find(base_filter, proj):
        rows.append({
            "player":         d.get("player_name"),
            "stat":           d.get("stat_type"),
            "side":           d.get("recommendation"),
            "line":           d.get("line"),
            "tier":           d.get("tier"),
            # Universal display labels mapped to existing fields:
            "consensus_edge": d.get("edge_vs_fair"),
            "best_bet_edge":  d.get("total_edge"),
            "best_bet_book":  d.get("best_book"),
            "best_bet_odds":  d.get("best_book_odds"),
            "best_bet_implied_probability":
                d.get("best_book_implied_probability"),
            "shopping_edge":  d.get("best_book_edge"),
            "market_spread":  d.get("market_spread"),
            "market_spread_label": d.get("market_spread_label"),
            "books_available_count": d.get("books_available_count"),
        })

    top_by_best_bet = sorted(
        (r for r in rows if r["best_bet_edge"] is not None),
        key=lambda r: r["best_bet_edge"],
        reverse=True,
    )[:top_n]

    # Shopping carries the bet: consensus_edge near zero but
    # best_bet_edge strong (≥+5%). These are the "the model agrees
    # with the market, but ONE book is priced too generously" plays.
    modest_consensus_strong_best_bet = sorted(
        (
            r for r in rows
            if r["consensus_edge"] is not None
            and r["best_bet_edge"] is not None
            and abs(r["consensus_edge"]) < 0.03
            and r["best_bet_edge"] >= 0.05
        ),
        key=lambda r: r["best_bet_edge"],
        reverse=True,
    )[:top_n]

    from collections import Counter
    book_counts = Counter(
        r["best_bet_book"] for r in rows if r["best_bet_book"]
    )
    spread_counts = Counter(
        r["market_spread_label"] for r in rows if r["market_spread_label"]
    )
    missing_best_book = sum(1 for r in rows if not r["best_bet_book"])

    return {
        "sport": sport,
        "source_collection": f"{sport}_prop_scores",
        "active_rows_total": len(rows),
        "top_by_best_bet_edge": top_by_best_bet,
        "modest_consensus_strong_best_bet": modest_consensus_strong_best_bet,
        "best_bet_book_distribution":
            sorted(book_counts.items(), key=lambda kv: -kv[1]),
        "market_spread_distribution": dict(spread_counts),
        "missing_best_book_count": missing_best_book,
        "missing_best_book_pct":   round(
            100.0 * missing_best_book / max(1, len(rows)), 2
        ),
    }
