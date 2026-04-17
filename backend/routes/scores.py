"""
Scoring recompute + query API routes
====================================
Recompute rebuilds {sport}_prop_scores from live props without triggering odds syncs.
Query reads directly from {sport}_prop_scores (read-only, filtered) for QA.

Endpoints:
  POST /api/scores/recompute
  POST /api/scores/recompute/{sport}
  GET  /api/scores/supported-sports
  GET  /api/scores/{sport}
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Body, Query, Path
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

from services.scoring.adapters import SUPPORTED_SPORTS
from services.scoring.recompute import recompute
from services.scoring.calibration_store import write_snapshot

router = APIRouter(prefix="/api/scores", tags=["scoring"])


class RecomputeRequest(BaseModel):
    sports: Optional[List[str]] = None
    version_tag: Optional[str] = None
    dry_run: bool = False
    limit: Optional[int] = None
    override_config: Optional[Dict[str, Any]] = Field(default=None)


_client: Optional[AsyncIOMotorClient] = None


def _get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client[os.environ["DB_NAME"]]


def _format_system_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through format with top-level system summary."""
    return {
        "status": result.get("status", "success"),
        "sports_processed": result.get("sports_processed", []),
        "processed": result.get("processed", {}),
        "written": result.get("written", {}),
        "skipped": result.get("skipped", {}),
        "version_tag": result.get("version_tag"),
        "duration_ms": result.get("duration_ms", 0),
        "dry_run": result.get("dry_run", False),
        "samples": result.get("samples", {}),
        "per_sport": result.get("per_sport", {}),
    }


def _format_single_sport_response(
    result: Dict[str, Any], sport: str
) -> Dict[str, Any]:
    """Single-sport endpoint returns just that sport's section."""
    ps = (result.get("per_sport") or {}).get(sport, {})
    return {
        "status": "success" if "error" not in ps else "error",
        "sport": sport,
        "processed": ps.get("processed", 0),
        "written": ps.get("written", 0),
        "skipped": ps.get("skipped", 0),
        "replaced": ps.get("replaced", 0),
        "version_tag": result.get("version_tag"),
        "duration_ms": result.get("duration_ms", 0),
        "dry_run": result.get("dry_run", False),
        "collection": ps.get("collection"),
        "cached_board_mutated": ps.get("cached_board_mutated"),
        "cached_board_leakage_fields": ps.get("cached_board_leakage_fields", []),
        "samples": ps.get("samples", []),
        "error": ps.get("error"),
    }


@router.post("/recompute")
async def recompute_all(req: RecomputeRequest = Body(default=None)):
    """System-level recompute. Defaults to all supported sports when
    no `sports` list is provided."""
    req = req or RecomputeRequest()
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=req.sports or list(SUPPORTED_SPORTS),
            version_tag=req.version_tag,
            dry_run=bool(req.dry_run),
            limit=req.limit,
            override_config=req.override_config,
        )
        return _format_system_response(result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/recompute/{sport}")
async def recompute_one(
    sport: str, req: RecomputeRequest = Body(default=None)
):
    """Sport-level recompute. Ignores request `sports` array."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    req = req or RecomputeRequest()
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=[sport],
            version_tag=req.version_tag,
            dry_run=bool(req.dry_run),
            limit=req.limit,
            override_config=req.override_config,
        )
        return _format_single_sport_response(result, sport)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/{sport}/diff")
async def diff_versions(
    sport: str,
    a: str = Query(..., description="Baseline version_tag"),
    b: str = Query(..., description="Comparison version_tag"),
    player_name: Optional[str] = Query(default=None),
    stat_type: Optional[str] = Query(default=None),
    min_delta: Optional[float] = Query(
        default=None, ge=0, description="Only include movers with |vision_score delta| >= this"
    ),
    limit: int = Query(default=20, ge=1, le=500),
):
    """Compare two version_tags in the same sport's score collection.

    Returns per-prop vision_score delta, tier migration matrix, distribution
    shift, tier gain/loss counts, and top-N biggest movers. Read-only."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    if a == b:
        raise HTTPException(status_code=400, detail="a and b must be distinct version_tags")

    db = _get_db()
    coll = db[f"{sport}_prop_scores"]

    # Verify both versions exist
    a_count = await coll.count_documents({"version_tag": a})
    b_count = await coll.count_documents({"version_tag": b})
    if a_count == 0:
        raise HTTPException(status_code=404, detail=f"version_tag '{a}' has 0 docs")
    if b_count == 0:
        raise HTTPException(status_code=404, detail=f"version_tag '{b}' has 0 docs")

    # Optional filter — applied to BOTH versions symmetrically
    filt: Dict[str, Any] = {}
    if player_name:
        filt["player_name"] = {"$regex": player_name, "$options": "i"}
    if stat_type:
        filt["stat_type"] = stat_type

    proj = {
        "_id": 0, "canonical_key": 1, "player_name": 1, "stat_type": 1,
        "line": 1, "recommendation": 1,
        "vision_score": 1, "tier": 1, "pp_utility": 1,
        "pp_utility_category": 1, "quality_source": 1,
    }
    a_docs = await coll.find({**filt, "version_tag": a}, proj).to_list(length=None)
    b_docs = await coll.find({**filt, "version_tag": b}, proj).to_list(length=None)

    a_idx = {d["canonical_key"]: d for d in a_docs if d.get("canonical_key")}
    b_idx = {d["canonical_key"]: d for d in b_docs if d.get("canonical_key")}

    only_in_a = sorted(set(a_idx) - set(b_idx))
    only_in_b = sorted(set(b_idx) - set(a_idx))
    shared = sorted(set(a_idx) & set(b_idx))

    # Tier migration matrix: from-tier -> to-tier counts (shared props only)
    migration: Dict[str, Dict[str, int]] = {}
    tier_before: Dict[str, int] = {}
    tier_after: Dict[str, int] = {}
    vision_deltas: List[Dict[str, Any]] = []
    dist_before: List[float] = []
    dist_after: List[float] = []

    for ck in shared:
        ad = a_idx[ck]; bd = b_idx[ck]
        ta = ad.get("tier") or "unknown"
        tb = bd.get("tier") or "unknown"
        migration.setdefault(ta, {})
        migration[ta][tb] = migration[ta].get(tb, 0) + 1
        tier_before[ta] = tier_before.get(ta, 0) + 1
        tier_after[tb] = tier_after.get(tb, 0) + 1

        va = ad.get("vision_score")
        vb = bd.get("vision_score")
        if va is not None: dist_before.append(va)
        if vb is not None: dist_after.append(vb)
        if va is not None and vb is not None:
            delta = round(vb - va, 2)
            if min_delta is None or abs(delta) >= min_delta:
                vision_deltas.append({
                    "canonical_key": ck,
                    "player_name": ad.get("player_name"),
                    "stat_type": ad.get("stat_type"),
                    "line": ad.get("line"),
                    "recommendation": ad.get("recommendation"),
                    "vision_score_a": va,
                    "vision_score_b": vb,
                    "vision_score_delta": delta,
                    "tier_a": ta, "tier_b": tb,
                    "pp_utility_a": ad.get("pp_utility"),
                    "pp_utility_b": bd.get("pp_utility"),
                })

    # Top movers (abs delta)
    vision_deltas.sort(key=lambda x: abs(x["vision_score_delta"]), reverse=True)
    top_movers = vision_deltas[:limit]

    # Tier gain/loss (B - A)
    tier_gained_lost: Dict[str, Dict[str, int]] = {}
    all_tiers = set(tier_before) | set(tier_after) | {
        "safe_haven", "front_lines", "war_zone", "unqualified"
    }
    for t in all_tiers:
        a_n = tier_before.get(t, 0)
        b_n = tier_after.get(t, 0)
        tier_gained_lost[t] = {"before": a_n, "after": b_n, "delta": b_n - a_n}

    # Distribution percentiles
    def _pct(arr, p):
        if not arr: return None
        arr = sorted(arr)
        i = max(0, min(len(arr) - 1, int(round((p / 100.0) * (len(arr) - 1)))))
        return round(arr[i], 2)

    dist_shift = {
        "a_count": len(dist_before), "b_count": len(dist_after),
        "a_p25": _pct(dist_before, 25), "b_p25": _pct(dist_after, 25),
        "a_p50": _pct(dist_before, 50), "b_p50": _pct(dist_after, 50),
        "a_p75": _pct(dist_before, 75), "b_p75": _pct(dist_after, 75),
        "a_p95": _pct(dist_before, 95), "b_p95": _pct(dist_after, 95),
        "a_mean": round(sum(dist_before) / len(dist_before), 2) if dist_before else None,
        "b_mean": round(sum(dist_after) / len(dist_after), 2) if dist_after else None,
    }

    return {
        "sport": sport,
        "version_a": a, "version_b": b,
        "counts": {
            "a_total": a_count, "b_total": b_count,
            "shared": len(shared),
            "only_in_a": len(only_in_a),
            "only_in_b": len(only_in_b),
        },
        "filters_applied": {
            "player_name": player_name, "stat_type": stat_type,
            "min_delta": min_delta, "limit": limit,
        },
        "tier_migration_matrix": migration,
        "tier_gained_lost": tier_gained_lost,
        "vision_score_distribution_shift": dist_shift,
        "top_movers": top_movers,
        "movers_matching_min_delta": len(vision_deltas),
    }


@router.get("/supported-sports")
async def supported_sports():
    return {"supported_sports": list(SUPPORTED_SPORTS)}


# =============================================================================
# Simulation endpoints — pure read-only threshold experiments
# No persistence, no version_tag docs, no mutation.
# =============================================================================

def _simulate_payload(per_sport_result: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape a recompute `per_sport` entry into a simulation summary."""
    return {
        "sport": per_sport_result.get("sport"),
        "processed": per_sport_result.get("processed", 0),
        "skipped": per_sport_result.get("skipped", 0),
        "duration_ms": per_sport_result.get("duration_ms", 0),
        "tier_distribution": per_sport_result.get("tier_distribution", {}),
        "quality_source_distribution": per_sport_result.get(
            "quality_source_distribution", {}
        ),
        "pp_category_distribution": per_sport_result.get(
            "pp_category_distribution", {}
        ),
        "top_samples": per_sport_result.get("top_samples", []),
        "cached_board_mutated": per_sport_result.get("cached_board_mutated"),
        "cached_board_leakage_fields": per_sport_result.get(
            "cached_board_leakage_fields", []
        ),
        "error": per_sport_result.get("error"),
    }


@router.post("/simulate")
async def simulate_all(req: RecomputeRequest = Body(default=None)):
    """Read-only simulation across one or more sports. No persistence,
    no version_tag, no mutation of live props or cached_board."""
    req = req or RecomputeRequest()
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=req.sports or list(SUPPORTED_SPORTS),
            version_tag="simulate",  # ignored by dry_run persist path
            dry_run=True,
            limit=req.limit,
            override_config=req.override_config,
        )
        per_sport = result.get("per_sport") or {}
        return {
            "status": "success",
            "mode": "simulation",
            "persisted": False,
            "sports_processed": result.get("sports_processed", []),
            "processed": result.get("processed", {}),
            "duration_ms": result.get("duration_ms", 0),
            "override_config": req.override_config or {},
            "per_sport": {s: _simulate_payload(per_sport[s]) for s in per_sport},
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/simulate/{sport}")
async def simulate_one(
    sport: str,
    req: Dict[str, Any] = Body(default=None),
):
    """Read-only per-sport simulation. No persistence.
    Dispatches `sport='compare'` to the compare handler for route-ordering
    compatibility (FastAPI matches /simulate/{sport} before /simulate/compare)."""
    sport = (sport or "").lower()
    # Route-collision guard: /simulate/compare uses CompareRequest body shape.
    if sport == "compare":
        cmp_req = CompareRequest(**(req or {}))
        return await simulate_compare_all(cmp_req)
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    # Re-validate body against RecomputeRequest for the standard simulate path
    body = RecomputeRequest(**(req or {}))
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=[sport],
            version_tag="simulate",
            dry_run=True,
            limit=body.limit,
            override_config=body.override_config,
        )
        per_sport = result.get("per_sport") or {}
        s_payload = _simulate_payload(per_sport.get(sport, {}))
        return {
            "status": "success",
            "mode": "simulation",
            "persisted": False,
            "sport": sport,
            "processed": s_payload["processed"],
            "duration_ms": s_payload["duration_ms"],
            "override_config": body.override_config or {},
            "tier_distribution": s_payload["tier_distribution"],
            "quality_source_distribution": s_payload["quality_source_distribution"],
            "pp_category_distribution": s_payload["pp_category_distribution"],
            "top_samples": s_payload["top_samples"],
            "cached_board_mutated": s_payload["cached_board_mutated"],
            "cached_board_leakage_fields": s_payload["cached_board_leakage_fields"],
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# =============================================================================
# Multi-variant compare — side-by-side simulation
# =============================================================================


class CompareVariant(BaseModel):
    name: str
    override_config: Optional[Dict[str, Any]] = None


class CompareRequest(BaseModel):
    sports: Optional[List[str]] = None
    limit: Optional[int] = None
    variants: List[CompareVariant]


class CalibrationSnapshotRequest(BaseModel):
    sports: Optional[List[str]] = None
    limit: Optional[int] = None
    variants: List[CompareVariant]
    label: Optional[str] = None
    notes: Optional[str] = None


_ALL_TIERS = ("safe_haven", "front_lines", "war_zone", "unqualified")


async def _run_variants(
    db, sports: List[str], limit: Optional[int], variants: List[CompareVariant]
) -> Dict[str, Dict[str, Any]]:
    """Run each variant via `recompute(dry_run=True)` and return
    `{variant_name: per_sport_dict}`."""
    names = [v.name for v in variants]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="variant names must be unique")

    out: Dict[str, Dict[str, Any]] = {}
    for v in variants:
        res = await recompute(
            db=db, sports=sports, version_tag=f"compare-{v.name}",
            dry_run=True, limit=limit,
            override_config=v.override_config or {},
        )
        out[v.name] = res.get("per_sport") or {}
    return out


def _compare_sport(
    sport: str,
    per_variant: Dict[str, Dict[str, Any]],
    baseline_name: str,
) -> Dict[str, Any]:
    """Build side-by-side comparison for a single sport."""
    # Per-variant tier data
    per_variant_data: Dict[str, Dict[str, Any]] = {}
    tier_sets: Dict[str, Dict[str, set]] = {t: {} for t in _ALL_TIERS}
    top_sample_keys: Dict[str, set] = {}

    for name, data in per_variant.items():
        sport_data = data.get(sport, {}) or {}
        per_variant_data[name] = {
            "processed": sport_data.get("processed", 0),
            "skipped": sport_data.get("skipped", 0),
            "duration_ms": sport_data.get("duration_ms", 0),
            "tier_distribution": sport_data.get("tier_distribution", {}),
            "quality_source_distribution": sport_data.get(
                "quality_source_distribution", {}
            ),
            "pp_category_distribution": sport_data.get(
                "pp_category_distribution", {}
            ),
            "top_samples": sport_data.get("top_samples", []),
            "cached_board_mutated": sport_data.get("cached_board_mutated"),
            "cached_board_leakage_fields": sport_data.get(
                "cached_board_leakage_fields", []
            ),
        }
        tier_keys = sport_data.get("tier_canonical_keys") or {}
        for t in _ALL_TIERS:
            tier_sets[t][name] = set(tier_keys.get(t, []))
        top_sample_keys[name] = {
            s.get("canonical_key")
            for s in (sport_data.get("top_samples") or [])
            if s.get("canonical_key")
        }

    variant_names = list(per_variant.keys())

    # Side-by-side tier counts table (rows = tier, cols = variant)
    tier_counts_table: Dict[str, Dict[str, int]] = {}
    for t in _ALL_TIERS:
        tier_counts_table[t] = {
            n: per_variant_data[n]["tier_distribution"].get(t, 0)
            for n in variant_names
        }

    # Per-tier deltas vs baseline
    tier_deltas_vs_baseline: Dict[str, Dict[str, int]] = {}
    if baseline_name in variant_names:
        base_row = tier_counts_table
        for t in _ALL_TIERS:
            base_n = base_row[t].get(baseline_name, 0)
            tier_deltas_vs_baseline[t] = {
                n: base_row[t].get(n, 0) - base_n
                for n in variant_names
                if n != baseline_name
            }

    # Canonical-key overlap per tier across variants (pairs vs baseline)
    tier_overlap: Dict[str, Dict[str, Dict[str, int]]] = {}
    if baseline_name in variant_names:
        for t in _ALL_TIERS:
            base_set = tier_sets[t].get(baseline_name, set())
            tier_overlap[t] = {}
            for n in variant_names:
                if n == baseline_name:
                    continue
                v_set = tier_sets[t].get(n, set())
                tier_overlap[t][n] = {
                    "baseline_size": len(base_set),
                    "variant_size": len(v_set),
                    "shared": len(base_set & v_set),
                    "only_in_baseline": len(base_set - v_set),
                    "only_in_variant": len(v_set - base_set),
                    "jaccard": round(
                        len(base_set & v_set) / max(1, len(base_set | v_set)), 4
                    ),
                }

    # War-zone entering/leaving between EACH pair of variants (variant vs baseline)
    war_zone_movers: Dict[str, Dict[str, List[str]]] = {}
    if baseline_name in variant_names:
        base_wz = tier_sets["war_zone"].get(baseline_name, set())
        for n in variant_names:
            if n == baseline_name:
                continue
            v_wz = tier_sets["war_zone"].get(n, set())
            war_zone_movers[n] = {
                "entered": sorted(v_wz - base_wz)[:50],
                "left": sorted(base_wz - v_wz)[:50],
                "entered_count": len(v_wz - base_wz),
                "left_count": len(base_wz - v_wz),
            }

    # Top-sample overlap counts (vs baseline)
    top_sample_overlap: Dict[str, Dict[str, int]] = {}
    if baseline_name in variant_names:
        base_top = top_sample_keys.get(baseline_name, set())
        for n in variant_names:
            if n == baseline_name:
                continue
            v_top = top_sample_keys.get(n, set())
            top_sample_overlap[n] = {
                "baseline_top_size": len(base_top),
                "variant_top_size": len(v_top),
                "shared_top": len(base_top & v_top),
                "jaccard_top": round(
                    len(base_top & v_top) / max(1, len(base_top | v_top)), 4
                ),
            }

    # Summary flags
    summary: Dict[str, Any] = {}
    if baseline_name in variant_names:
        base_unqual = per_variant_data[baseline_name]["tier_distribution"].get(
            "unqualified", 0
        )

        most_adds_tier = None  # variant that adds most qualified (reduces unqualified)
        most_adds_qty = -1
        most_removes_qty = -1
        most_removes_tier = None
        max_overlap_score = -1.0
        max_overlap_name = None
        clean_migration_variants: List[str] = []

        for n in variant_names:
            if n == baseline_name:
                continue
            v_unqual = per_variant_data[n]["tier_distribution"].get("unqualified", 0)
            qualified_delta = base_unqual - v_unqual  # positive = added qualifiers
            if qualified_delta > most_adds_qty:
                most_adds_qty = qualified_delta
                most_adds_tier = n
            if -qualified_delta > most_removes_qty:
                most_removes_qty = -qualified_delta
                most_removes_tier = n

            # Overlap: average jaccard across tiers (weighted by baseline size)
            scores = []
            weights = []
            for t in _ALL_TIERS:
                if tier_overlap.get(t, {}).get(n):
                    j = tier_overlap[t][n]["jaccard"]
                    w = tier_overlap[t][n]["baseline_size"]
                    scores.append(j * w)
                    weights.append(w)
            avg_jac = sum(scores) / max(1, sum(weights))
            if avg_jac > max_overlap_score:
                max_overlap_score = avg_jac
                max_overlap_name = n

            # "Clean migration" = non-unqualified tiers unchanged vs baseline
            clean = True
            for t in ("safe_haven", "front_lines", "war_zone"):
                b = per_variant_data[baseline_name]["tier_distribution"].get(t, 0)
                v = per_variant_data[n]["tier_distribution"].get(t, 0)
                base_set = tier_sets[t].get(baseline_name, set())
                v_set = tier_sets[t].get(n, set())
                # If the count is unchanged AND the set is identical, counts as clean
                if b != v or base_set != v_set:
                    # Check specifically: does the variant REMOVE any baseline pick
                    # from the existing tier? (cannibalization)
                    if base_set - v_set:
                        clean = False
                        break
            if clean:
                clean_migration_variants.append(n)

        summary = {
            "baseline": baseline_name,
            "most_adds_qualified_variant": most_adds_tier,
            "most_adds_qualified_count": most_adds_qty,
            "most_removes_qualified_variant": most_removes_tier,
            "most_removes_qualified_count": most_removes_qty,
            "highest_overlap_with_baseline_variant": max_overlap_name,
            "highest_overlap_score": round(max_overlap_score, 4),
            "clean_migration_variants": clean_migration_variants,
        }

    # Mutation audit
    any_mutation = any(
        pv.get("cached_board_mutated") for pv in per_variant_data.values()
    )
    any_leakage = any(
        pv.get("cached_board_leakage_fields") for pv in per_variant_data.values()
    )

    return {
        "sport": sport,
        "variant_results": per_variant_data,
        "tier_counts_table": tier_counts_table,
        "tier_deltas_vs_baseline": tier_deltas_vs_baseline,
        "tier_canonical_key_overlap_vs_baseline": tier_overlap,
        "war_zone_movers_vs_baseline": war_zone_movers,
        "top_sample_overlap_vs_baseline": top_sample_overlap,
        "summary": summary,
        "persisted": False,
        "cached_board_mutated_any": any_mutation,
        "cached_board_leakage_any": any_leakage,
    }


@router.post("/simulate/compare")
async def simulate_compare_all(req: CompareRequest = Body(...)):
    """Run multiple named variants across sports; return side-by-side comparison.
    Read-only. No persistence. No live-prop or cached_board mutation."""
    if not req.variants:
        raise HTTPException(status_code=400, detail="at least one variant required")
    sports = [s.lower() for s in (req.sports or list(SUPPORTED_SPORTS))]
    unknown = [s for s in sports if s not in SUPPORTED_SPORTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sports: {unknown}. Supported: {list(SUPPORTED_SPORTS)}",
        )

    db = _get_db()
    baseline_name = req.variants[0].name  # first variant is the baseline
    per_variant = await _run_variants(db, sports, req.limit, req.variants)

    comparison: Dict[str, Any] = {}
    for sport in sports:
        comparison[sport] = _compare_sport(sport, per_variant, baseline_name)

    return {
        "status": "success",
        "mode": "simulation_compare",
        "persisted": False,
        "sports_processed": sports,
        "baseline_variant": baseline_name,
        "variants": [v.name for v in req.variants],
        "per_sport": comparison,
    }


@router.post("/simulate/compare/{sport}")
async def simulate_compare_one(
    sport: str, req: CompareRequest = Body(...)
):
    """Per-sport compare. Ignores request `sports` array."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    if not req.variants:
        raise HTTPException(status_code=400, detail="at least one variant required")

    db = _get_db()
    baseline_name = req.variants[0].name
    per_variant = await _run_variants(db, [sport], req.limit, req.variants)
    comparison = _compare_sport(sport, per_variant, baseline_name)
    return {
        "status": "success",
        "mode": "simulation_compare",
        "persisted": False,
        "sport": sport,
        "baseline_variant": baseline_name,
        "variants": [v.name for v in req.variants],
        **comparison,
    }


# =============================================================================
# Calibration Snapshot Persistence — audit trail for scoring experiments
# =============================================================================

async def _run_and_compare(
    db, sports: List[str], limit: Optional[int], variants: List[CompareVariant]
) -> Dict[str, Any]:
    """Run variants read-only + compare. Returns {sport: comparison_dict} and
    {variant_name: override_config}."""
    per_variant = await _run_variants(db, sports, limit, variants)
    baseline_name = variants[0].name
    comparisons: Dict[str, Any] = {}
    for sport in sports:
        comparisons[sport] = _compare_sport(sport, per_variant, baseline_name)
    variant_overrides = {v.name: (v.override_config or {}) for v in variants}
    return comparisons, variant_overrides


@router.post("/calibration-snapshot")
async def calibration_snapshot_all(req: CalibrationSnapshotRequest = Body(...)):
    """Run variants read-only, persist a lean summary to each sport's
    `{sport}_calibration_runs` collection. Creates one snapshot doc per sport."""
    if not req.variants:
        raise HTTPException(status_code=400, detail="at least one variant required")
    names = [v.name for v in req.variants]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="variant names must be unique")

    sports = [s.lower() for s in (req.sports or list(SUPPORTED_SPORTS))]
    unknown = [s for s in sports if s not in SUPPORTED_SPORTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sports: {unknown}. Supported: {list(SUPPORTED_SPORTS)}",
        )

    db = _get_db()
    comparisons, variant_overrides = await _run_and_compare(
        db, sports, req.limit, req.variants
    )

    snapshots = {}
    for sport in sports:
        doc = await write_snapshot(
            db=db, sport=sport,
            comparison=comparisons[sport],
            variant_overrides=variant_overrides,
            label=req.label, notes=req.notes, limit=req.limit,
        )
        snapshots[sport] = {
            "snapshot_id": doc["snapshot_id"],
            "created_at": doc["created_at"],
            "summary": doc["summary"],
            "source_timestamps": doc["source_timestamps"],
            "collection": f"{sport}_calibration_runs",
        }

    return {
        "status": "success",
        "persisted": True,
        "sports_processed": sports,
        "snapshots": snapshots,
    }


@router.post("/calibration-snapshot/{sport}")
async def calibration_snapshot_one(
    sport: str, req: CalibrationSnapshotRequest = Body(...)
):
    """Per-sport calibration snapshot. Ignores request `sports` array."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    if not req.variants:
        raise HTTPException(status_code=400, detail="at least one variant required")
    names = [v.name for v in req.variants]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="variant names must be unique")

    db = _get_db()
    comparisons, variant_overrides = await _run_and_compare(
        db, [sport], req.limit, req.variants
    )
    doc = await write_snapshot(
        db=db, sport=sport,
        comparison=comparisons[sport],
        variant_overrides=variant_overrides,
        label=req.label, notes=req.notes, limit=req.limit,
    )
    return {
        "status": "success",
        "persisted": True,
        "sport": sport,
        "snapshot_id": doc["snapshot_id"],
        "created_at": doc["created_at"],
        "collection": f"{sport}_calibration_runs",
        "summary": doc["summary"],
        "source_timestamps": doc["source_timestamps"],
        "label": doc["label"],
        "notes": doc["notes"],
    }


@router.get("/calibration-snapshots/{sport}")
async def list_calibration_snapshots(
    sport: str,
    label_contains: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(
        default=None, description="ISO8601 UTC lower bound (inclusive)"
    ),
    end_date: Optional[str] = Query(
        default=None, description="ISO8601 UTC upper bound (inclusive)"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List calibration snapshots for a sport with optional filters."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    db = _get_db()
    coll = db[f"{sport}_calibration_runs"]

    q: Dict[str, Any] = {}
    if label_contains:
        q["label"] = {"$regex": label_contains, "$options": "i"}
    if start_date or end_date:
        date_q: Dict[str, Any] = {}
        if start_date:
            date_q["$gte"] = start_date
        if end_date:
            date_q["$lte"] = end_date
        q["created_at"] = date_q

    total = await coll.count_documents(q)
    # Lean projection for list view
    proj = {
        "_id": 0, "snapshot_id": 1, "sport": 1, "created_at": 1,
        "label": 1, "notes": 1, "baseline_variant": 1,
        "source_timestamps": 1, "summary": 1,
        "tier_counts_table": 1, "limit_applied": 1,
    }
    cursor = (
        coll.find(q, proj).sort([("created_at", -1)]).skip(offset).limit(limit)
    )
    results = await cursor.to_list(length=limit)
    return {
        "sport": sport,
        "total_matching": total,
        "returned": len(results),
        "filters": {
            "label_contains": label_contains,
            "start_date": start_date, "end_date": end_date,
            "limit": limit, "offset": offset,
        },
        "results": results,
    }


@router.get("/calibration-snapshots/{sport}/{snapshot_id}")
async def get_calibration_snapshot(sport: str, snapshot_id: str):
    """Fetch a full calibration snapshot by id."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    db = _get_db()
    coll = db[f"{sport}_calibration_runs"]
    doc = await coll.find_one({"snapshot_id": snapshot_id}, {"_id": 0})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot '{snapshot_id}' not found in {sport}_calibration_runs",
        )
    return doc


# =============================================================================
# Query endpoint — read-only QA inspection of {sport}_prop_scores
# =============================================================================

_VALID_SORTS = {
    "vision_score", "vision_score_raw", "pp_utility", "tier",
    "edge_vs_fair", "fair_prob", "computed_at", "player_name", "stat_type",
}


def _summarize(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summary counts by tier + pp_utility_category."""
    tiers: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    quality_sources: Dict[str, int] = {}
    vision_null = 0
    for d in docs:
        t = d.get("tier") or "unknown"
        tiers[t] = tiers.get(t, 0) + 1
        c = d.get("pp_utility_category") or "unknown"
        categories[c] = categories.get(c, 0) + 1
        q = d.get("quality_source") or "unknown"
        quality_sources[q] = quality_sources.get(q, 0) + 1
        if d.get("vision_score") is None:
            vision_null += 1
    return {
        "by_tier": tiers,
        "by_pp_utility_category": categories,
        "by_quality_source": quality_sources,
        "vision_score_null": vision_null,
    }


@router.get("/{sport}")
async def query_scores(
    sport: str,
    version_tag: Optional[str] = Query(
        default=None, description="If omitted, uses the latest version for the sport."
    ),
    min_vision: Optional[float] = Query(default=None, ge=0, le=100),
    max_vision: Optional[float] = Query(default=None, ge=0, le=100),
    tier: Optional[str] = Query(default=None),
    pp_utility_category: Optional[str] = Query(default=None),
    quality_source: Optional[str] = Query(default=None),
    player_name: Optional[str] = Query(default=None),
    stat_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="vision_score"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    """Read-only query against `{sport}_prop_scores` for QA inspection."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    if sort_by not in _VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by '{sort_by}'. Allowed: {sorted(_VALID_SORTS)}",
        )

    db = _get_db()
    coll = db[f"{sport}_prop_scores"]

    # Resolve latest version_tag if not provided
    if not version_tag:
        pipeline = [
            {"$group": {"_id": "$version_tag", "computed_at": {"$max": "$computed_at"}}},
            {"$sort": {"computed_at": -1}},
            {"$limit": 1},
        ]
        cursor = coll.aggregate(pipeline)
        async for doc in cursor:
            version_tag = doc["_id"]
            break
        if not version_tag:
            return {
                "sport": sport, "version_tag": None,
                "filters_applied": {}, "total_matching": 0,
                "returned": 0, "summary": _summarize([]), "results": [],
            }

    # Build filter
    q: Dict[str, Any] = {"version_tag": version_tag}
    if min_vision is not None or max_vision is not None:
        vrange: Dict[str, Any] = {}
        if min_vision is not None: vrange["$gte"] = min_vision
        if max_vision is not None: vrange["$lte"] = max_vision
        q["vision_score"] = vrange
    if tier:
        q["tier"] = tier
    if pp_utility_category:
        q["pp_utility_category"] = pp_utility_category
    if quality_source:
        q["quality_source"] = quality_source
    if player_name:
        q["player_name"] = {"$regex": player_name, "$options": "i"}
    if stat_type:
        q["stat_type"] = stat_type

    # Totals + summary computed over ALL matching docs
    total_matching = await coll.count_documents(q)

    # Summary: aggregate over full filter (cap at 20k for safety)
    summary_cursor = coll.find(q, {
        "_id": 0, "tier": 1, "pp_utility_category": 1,
        "quality_source": 1, "vision_score": 1,
    }).limit(20000)
    summary_docs = await summary_cursor.to_list(length=20000)
    summary = _summarize(summary_docs)

    # Paged result set — lean projection
    sort_spec = [(sort_by, 1 if sort_dir == "asc" else -1)]
    cursor = (
        coll.find(q, {"_id": 0})
        .sort(sort_spec)
        .skip(offset)
        .limit(limit)
    )
    results = await cursor.to_list(length=limit)

    return {
        "sport": sport,
        "version_tag": version_tag,
        "filters_applied": {
            "min_vision": min_vision, "max_vision": max_vision,
            "tier": tier, "pp_utility_category": pp_utility_category,
            "quality_source": quality_source,
            "player_name": player_name, "stat_type": stat_type,
            "sort_by": sort_by, "sort_dir": sort_dir,
            "limit": limit, "offset": offset,
        },
        "total_matching": total_matching,
        "returned": len(results),
        "summary": summary,
        "results": results,
    }
