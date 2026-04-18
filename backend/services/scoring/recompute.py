"""
Sport-Agnostic Scoring Recompute Orchestrator
==============================================
Rebuilds `{sport}_prop_scores` from existing live prop collections
WITHOUT triggering any sportsbook sync or mutating cached boards.

Entry points:
  recompute(db, sports, version_tag, dry_run=False, limit=None,
            override_config=None) → dict

Constraints enforced:
 - Read-only access to live props (find only; no updates/inserts)
 - Read-only access to cached_board (used only for leak-check audit)
 - Scoring-stack fields live ONLY in {sport}_prop_scores
 - Sport-specific ScoringAdapter provides the ScoringContext
"""
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import logging
logger = logging.getLogger(__name__)

from services.scoring.scoring_stack import compute_scoring_stack
from services.scoring.adapters import (
    SCORING_ADAPTERS, SUPPORTED_SPORTS, get_scoring_adapter,
)
from services.scoring.prop_scores_store import write_versioned_scores


def _default_version_tag() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"recompute-{ts}-{uuid.uuid4().hex[:6]}"


def _apply_vision_score_normalization(score_docs: List[Dict[str, Any]]) -> None:
    """Populate `vision_score` (0-100) via percentile rank of vision_score_raw.
    Props with quality_source='insufficient_market' keep vision_score=None."""
    raw = sorted([
        d["vision_score_raw"] for d in score_docs
        if d.get("vision_score_raw") is not None and d["vision_score_raw"] > 0
    ])
    if not raw:
        for d in score_docs:
            d["vision_score"] = (
                None if d.get("quality_source") == "insufficient_market" else 0.0
            )
        return

    for d in score_docs:
        if d.get("quality_source") == "insufficient_market":
            d["vision_score"] = None
            continue
        vr = d.get("vision_score_raw")
        if vr is None or vr <= 0:
            d["vision_score"] = 0.0
        else:
            rank = sum(1 for s in raw if s <= vr)
            d["vision_score"] = round((rank / len(raw)) * 100.0, 1)


async def recompute_sport(
    db,
    sport: str,
    version_tag: str,
    dry_run: bool = False,
    limit: Optional[int] = None,
    override_config: Optional[Dict[str, Any]] = None,
    write_mode: str = "replace",
    props: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Recompute scoring stack for a single sport.

    `write_mode`:
      - "replace" (default, used by the hourly full recompute): wipes
        every doc with `version_tag` then bulk-inserts the new docs.
      - "upsert" (Step 5 real-time scoped ingest): per-doc upsert keyed
        on `(canonical_key, version_tag)`. Never destroys existing rows
        outside the supplied key set.

    `props`: optional pre-loaded list of raw live props. When supplied,
    `adapter.load_live_props` is bypassed entirely — the caller is
    responsible for filtering. Used by `services/board/engine.py` for
    scoped real-time ingest.
    """
    t0 = time.monotonic()
    adapter = get_scoring_adapter(sport)
    config = {
        "version_tag": version_tag,
        "limit": limit,
        "override_config": override_config or {},
    }

    # Snapshot cached_board BEFORE recompute (for leak-check audit).
    cached_coll = db[adapter.cached_board_collection]
    cached_before_count = await cached_coll.count_documents({})
    cached_before_sample = await cached_coll.find_one({}, {"_id": 0})

    # 1. Load live props (read-only) — unless caller supplied them
    if props is None:
        props = await adapter.load_live_props(db, limit=limit)

    # 2. Build scoring contexts + compute stack
    sorter = adapter.get_sorter(db)
    if sorter is None and hasattr(adapter, "_build_sorter"):
        sorter = adapter._build_sorter(config)
    score_docs: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []
    skipped = 0

    for prop in props:
        ctx = await adapter.build_context(db, prop, config)
        if ctx is None:
            skipped += 1
            continue
        # Get up-to-date sorter (NBA may have rebuilt it with config overrides)
        sorter = adapter.get_sorter(db) or sorter

        # Run the three independent scoring functions through the composed entry.
        stack = compute_scoring_stack(
            prop={
                "pp_layer": ctx.pp_layer, "dk_layer": ctx.dk_layer,
                "mgm_layer": ctx.mgm_layer, "sharp_layer": ctx.sharp_layer,
                # multiplier hints for pp_utility
                "pp_combo_multiplier": ctx.pp_combo_multiplier,
                "pp_label": ctx.pp_label,
                "pp_multiplier_model": ctx.pp_multiplier_model,
                # raw fields used by MLBTierSorter gate evaluation
                **ctx.raw_prop,
                # Authoritative direction for side-aware gate paths — placed
                # AFTER the raw_prop splat so nothing can shadow it.
                "recommendation": ctx.recommendation,
            },
            p_model=ctx.p_model,
            cv=ctx.cv,
            hit_rate=ctx.hit_rate,
            edge_pct=ctx.edge_pct,
            tp=ctx.tp,
            ceiling_rate=ctx.ceiling_rate,
            books_available_count=ctx.books_available_count,
            sorter=sorter,
        )
        # -------- Universal pool fields (multi-sport lifecycle) --------
        # Every {sport}_prop_scores document carries the same universal
        # lifecycle fields so the board engine and scanner can operate on
        # any sport without sport-specific branches. `game_start_utc` is
        # pulled from the raw prop's commence_time so the 60s scanner can
        # flip tipped-off props to active=False via an indexed update.
        raw = ctx.raw_prop or {}
        commence = raw.get("commence_time") or raw.get("event_start_utc") or raw.get("game_time")
        if isinstance(commence, str):
            try:
                game_start_utc = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            except Exception:
                game_start_utc = None
        elif isinstance(commence, datetime):
            game_start_utc = commence if commence.tzinfo else commence.replace(tzinfo=timezone.utc)
        else:
            game_start_utc = None

        doc = {
            "canonical_key": ctx.canonical_key,
            "sport": ctx.sport,
            "event_id": ctx.event_id,
            "player_name": ctx.player_name,
            "stat_type": ctx.stat_type,
            "line": ctx.line,
            "recommendation": ctx.recommendation,
            # ---- Universal pool lifecycle fields ----
            "active": True,
            "inactive_reason": None,
            "active_changed_at": None,
            "game_start_utc": game_start_utc,
            # p_true diagnostic panel (all paths always computed)
            "p_true_active": ctx.p_model,
            "p_true_method": ctx.p_true_method,
            "p_true_hit_rate": ctx.p_true_hit_rate,
            "p_true_model": ctx.p_true_model,
            "model_projection": ctx.model_projection,
            "model_sigma": ctx.model_sigma,
            "p_true_vk2": ctx.p_true_vk2,
            "vk2_projection": ctx.vk2_projection,
            "vk2_sigma": ctx.vk2_sigma,
            "vk2_error": ctx.vk2_error,
            "hit_rate_over": ctx.hit_rate_over,
            "hit_rate_under": ctx.hit_rate_under,
            **stack,
        }
        score_docs.append(doc)

    # 3. Percentile-normalize vision_score across the sport's slate.
    _apply_vision_score_normalization(score_docs)

    # 4. Persist (unless dry_run)
    write_result = await write_versioned_scores(
        db=db, sport=sport, score_docs=score_docs,
        version_tag=version_tag, dry_run=dry_run,
        mode=write_mode,
    )

    # 5. Leak-check: cached_board must NOT be mutated AND must not contain
    #    recompute-produced scoring data (check for non-null scoring values).
    cached_after_count = await cached_coll.count_documents({})
    cached_after_sample = await cached_coll.find_one({}, {"_id": 0})
    leakage_fields = []
    if cached_after_sample:
        candidate = cached_after_sample
        if isinstance(candidate.get("props"), list) and candidate["props"]:
            candidate = candidate["props"][0]
        scoring_field_names = (
            "vision_score", "vision_score_raw", "tier_reason", "tier_gate_results",
            "pp_utility", "pp_utility_components", "quality_source",
        )
        # Only flag as leakage if the field is present AND carries a non-null
        # value — empty pre-existing keys set to None by legacy pipelines
        # are NOT recompute leakage.
        leakage_fields = [
            f for f in scoring_field_names
            if f in candidate and candidate[f] is not None and candidate[f] != {}
        ]

    # Sample outputs for response
    for d in score_docs[:3]:
        samples.append({k: v for k, v in d.items() if k != "_id"})

    # Tier distribution + top samples for simulation / diagnostic use
    tier_distribution: Dict[str, int] = {}
    quality_distribution: Dict[str, int] = {}
    pp_category_distribution: Dict[str, int] = {}
    tier_canonical_keys: Dict[str, List[str]] = {}
    for d in score_docs:
        t = d.get("tier") or "unknown"
        tier_distribution[t] = tier_distribution.get(t, 0) + 1
        q = d.get("quality_source") or "unknown"
        quality_distribution[q] = quality_distribution.get(q, 0) + 1
        c = d.get("pp_utility_category") or "unknown"
        pp_category_distribution[c] = pp_category_distribution.get(c, 0) + 1
        ck = d.get("canonical_key")
        if ck:
            tier_canonical_keys.setdefault(t, []).append(ck)

    # Top 10 by vision_score (nulls sorted last)
    def _vs_key(d):
        v = d.get("vision_score")
        return (0 if v is None else 1, v if v is not None else -1.0)
    top_samples = sorted(score_docs, key=_vs_key, reverse=True)[:10]
    top_samples = [{k: v for k, v in d.items() if k != "_id"} for d in top_samples]

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "sport": sport,
        "processed": len(props),
        "written": write_result["written"],
        "skipped": skipped,
        "replaced": write_result.get("replaced", 0),
        "collection": write_result["collection"],
        "version_tag": version_tag,
        "dry_run": dry_run,
        "duration_ms": duration_ms,
        "cached_board_before_count": cached_before_count,
        "cached_board_after_count": cached_after_count,
        "cached_board_mutated": cached_before_count != cached_after_count
            or cached_before_sample != cached_after_sample,
        "cached_board_leakage_fields": leakage_fields,
        "tier_distribution": tier_distribution,
        "quality_source_distribution": quality_distribution,
        "pp_category_distribution": pp_category_distribution,
        "tier_canonical_keys": tier_canonical_keys,
        "samples": samples,
        "top_samples": top_samples,
    }


async def recompute(
    db,
    sports: Optional[List[str]] = None,
    version_tag: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    override_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Orchestrate recompute across one or more sports."""
    t0 = time.monotonic()
    if not sports:
        sports = list(SUPPORTED_SPORTS)
    # Validate sport list
    unknown = [s for s in sports if s not in SCORING_ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown sports: {unknown}. Supported: {SUPPORTED_SPORTS}")

    version_tag = version_tag or _default_version_tag()

    per_sport: Dict[str, Dict[str, Any]] = {}
    for sport in sports:
        try:
            per_sport[sport] = await recompute_sport(
                db=db, sport=sport, version_tag=version_tag,
                dry_run=dry_run, limit=limit,
                override_config=override_config,
            )
        except Exception as e:
            logger.exception(f"[RECOMPUTE] {sport} failed: {e}")
            per_sport[sport] = {
                "sport": sport, "error": str(e),
                "processed": 0, "written": 0, "skipped": 0,
                "version_tag": version_tag, "dry_run": dry_run,
            }

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "status": "success",
        "sports_processed": sports,
        "processed": {s: per_sport[s].get("processed", 0) for s in sports},
        "written": {s: per_sport[s].get("written", 0) for s in sports},
        "skipped": {s: per_sport[s].get("skipped", 0) for s in sports},
        "version_tag": version_tag,
        "duration_ms": duration_ms,
        "dry_run": dry_run,
        "per_sport": per_sport,
        "samples": {s: per_sport[s].get("samples", []) for s in sports},
    }
