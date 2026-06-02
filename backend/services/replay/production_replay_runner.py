"""Universal Production Replay Runner — Phase 2c orchestrator.

This module is the sport-agnostic orchestrator that wires the
`UniversalReplayProvider` (Phase 1) and the `SportReplayAdapter`
(Phase 1+2b) into the existing production-grade Layer-3 (model replay)
and Layer-4 (gate eval + grading) engines, and persists the canonical
audit-pinned schemas:

    {sport}_production_replay_runs        ← one doc per run
    {sport}_production_replay_outputs     ← one doc per (event, prop, side, book)

Phase 2c boundaries (per user directive — "Phase 2c only, stop before Phase 3"):
 - Top-N / per-game dedup card rendering is DEFERRED to Phase 3. This
   runner does NOT touch `picks_getter_service.py` and does NOT write
   `{sport}_production_replay_cards`. That collection lands in Phase 3
   when card-logic is surgically extracted from `picks_getter_service.py`
   into a pure function.
 - Gate evaluation routes through the existing Layer-4 War-Zone gate
   spec (`mlb_replay_gate_eval.evaluate_gates`) for output-shape parity
   with the prior 15-day sweep. Phase 4 will swap this for
   `evaluate_tier_with_overrides(metrics, feature_provider=...)` once
   Phase 3 ships the NormalizedMetrics builder for replay rows.

Design guarantees (Phase 2c hard constraints):
 - **Zero live-pipeline edits.** This is a NEW module. No imports into
   `recompute.py` / `picks_getter_service.py`. The byte-identical Phase
   2a + 2b regressions still hold.
 - **Read-only on legacy collections.** We re-read Layer-3 outputs
   (`mlb_replay_model_outputs`) but never mutate them.
 - **Audit-pinned runs.** Every run captures pipeline + adapter + cache
   versions, git SHA, input-collection counts, and a per-tier serial.
 - **Sport-agnostic.** Switch adapter → switch sport. MLB is the first
   adapter; NBA/NFL adapters wire in with no orchestrator changes.

Output contract:
    await run_production_replay(
        db, sport="mlb",
        game_date="2026-05-06", snapshot_iso="2026-05-06T11:00:00Z",
        tier="war_zone",
    )  → { "serial": "MLB-PRODREPLAY-20260506-WZ-1100UTC-00001",
          "rows_scanned": int, "rows_qualified": int,
          "wins": int, "losses": int, "pushes": int,
          "hit_rate_pct": float, "roi_pct": float,
          "profit_units": float, "elapsed_s": float }
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil
from pymongo import ASCENDING, UpdateOne

from services.replay.providers import (
    MLBReplayAdapter,
    NBAReplayAdapter,
    NFLReplayAdapter,
    SportReplayAdapter,
    build_universal_historical_provider,
    compute_production_pipeline_version,
    snapshot_input_collection_versions,
    next_replay_serial,
    git_commit_sha,
    utc_now,
    runs_collection_name,
    outputs_collection_name,
)
from services.replay.providers.audit import cards_collection_name
from services.replay.providers.schemas import (
    ProductionReplayRun, ProductionReplayOutput, InputCollectionPin,
    ProductionReplayCard,
)
from services.picks.card_builder import (
    build_production_cards,
    DEFAULT_DEDUPE_KEYS, DEFAULT_ORDER_BY,
    DEFAULT_SLATE_TOP_K, DEFAULT_PER_GAME_TOP_N,
)
from services.replay.mlb_replay_engine import (
    replay_date as mlb_layer3_replay_date,
    OUT_COLL as MLB_LAYER3_OUT_COLL,
    SCORING_CONFIG_VERSION as MLB_SCORING_CFG_V,
    SOURCE_VERSION as MLB_LAYER3_SRC_V,
)
from services.replay.mlb_replay_engine import _american_to_implied
from services.replay.nba_replay_engine import (
    replay_date as nba_layer3_replay_date,
    OUT_COLL as NBA_LAYER3_OUT_COLL,
    SCORING_CONFIG_VERSION as NBA_SCORING_CFG_V,
    SOURCE_VERSION as NBA_LAYER3_SRC_V,
)
from services.replay.mlb_replay_gate_eval import (
    evaluate_gates as mlb_layer4_evaluate_gates,
    GATE_CONFIG_VERSION as MLB_GATE_CFG_V,
)
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as MLB_FEATURE_CACHE_V,
)
# Phase 4 — production-gate-engine path. When `gate_path="universal"`,
# the runner builds NormalizedMetrics from each replay row and routes
# through the SAME `evaluate_tier_with_overrides` that live serving
# uses (no duplicated thresholds). The Phase 2c WZ-only spec
# (`mlb_replay_gate_eval.evaluate_gates`) is preserved as the default
# for byte-identical replay of historical runs.
from services.scoring.tier_evaluator import evaluate_tier_with_overrides
from services.scoring.gates.thresholds import resolve_thresholds
from services.scoring.odds_bucket_router import (
    get_odds_bucket, TIER_ODDS_BUCKET_FAIL,
)
from services.replay.replay_metrics_builder import build_metrics_from_replay_row
from services.replay.replay_field_hydrators import (
    load_book_inventory, load_player_game_logs_as_of,
)
from services.replay.reference_odds_loader import load_reference_odds_for_snapshot
from services.canonical.canonical_prop import build_canonical_props
from services.canonical.market_normalizer import normalize_market
from dataclasses import replace as _dc_replace
import hashlib
import json as _json

logger = logging.getLogger(__name__)


# Canonical Prop Engine version pin. Bumped only when the
# canonical-collapse semantics change (e.g. ladder collapse,
# additional dedup keys). The default canonical_path=False keeps
# legacy replay byte-identical.
CANONICAL_ENGINE_VERSION = "canonical_v2_phase4_2026_05_17"

_ADAPTER_REGISTRY = {
    "mlb": MLBReplayAdapter,
    "nba": NBAReplayAdapter,
    "nfl": NFLReplayAdapter,
}


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _resolve_adapter(db, sport: str) -> SportReplayAdapter:
    sport = (sport or "").lower()
    cls = _ADAPTER_REGISTRY.get(sport)
    if cls is None:
        raise ValueError(
            f"Unsupported sport={sport!r}; known: {sorted(_ADAPTER_REGISTRY)}"
        )
    return cls(db)


def _resolve_scoring_versions(adapter: SportReplayAdapter) -> Dict[str, str]:
    """Per-sport scoring/cache/gate version pins. Extend per sport."""
    if adapter.SPORT == "mlb":
        return {
            "scoring_config_version": MLB_SCORING_CFG_V,
            "feature_cache_version": MLB_FEATURE_CACHE_V,
            "gate_config_version": MLB_GATE_CFG_V,
            "layer3_source_version": MLB_LAYER3_SRC_V,
        }
    if adapter.SPORT == "nba":
        # NBA reuses the universal gate engine (`compute_scoring_stack`
        # → `tier_evaluator.evaluate_tier_with_overrides`) live serving
        # uses, so `gate_config_version` is the SAME pin MLB carries
        # when `gate_path="universal"`. `feature_cache_version` is
        # absent (NBA reads master_hub directly), recorded as
        # "nba_no_feature_cache".
        return {
            "scoring_config_version": NBA_SCORING_CFG_V,
            "feature_cache_version": "nba_no_feature_cache",
            "gate_config_version": MLB_GATE_CFG_V,
            "layer3_source_version": NBA_LAYER3_SRC_V,
        }
    return {
        "scoring_config_version": f"{adapter.SPORT}_unimplemented",
        "feature_cache_version": f"{adapter.SPORT}_unimplemented",
        "gate_config_version": f"{adapter.SPORT}_unimplemented",
        "layer3_source_version": f"{adapter.SPORT}_unimplemented",
    }


def _resolve_model_versions(adapter: SportReplayAdapter) -> Dict[str, str]:
    """Map stat_family → model module SHA. MLB uses one shared model."""
    if adapter.SPORT == "mlb":
        return {"mlb_high_friction_model": adapter.adapter_version()}
    if adapter.SPORT == "nba":
        # NBA scoring pulls μ from legacy VK + VK2 artefacts. The
        # adapter's own SHA covers the integration code path; the
        # per-artefact SHAs are loaded lazily inside
        # `NBAScoringAdapter` so we don't enumerate them here. Bumped
        # implicitly via `production_pipeline_version` whenever the
        # underlying model file or scorer changes.
        return {"nba_scoring_adapter": adapter.adapter_version()}
    return {}


def _build_canonical_eval_rows(
    raw_rows: List[Dict[str, Any]], *, sport: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Phase 6 Phase 2 — Collapse Layer-3 raw book rows into one
    `canonical evaluation row` per (canonical_prop × side).

    Pure (no DB / no mutation of `raw_rows`). Returns
    `(eval_rows, summary)` where `summary` carries the canonical-engine
    audit counters. Each eval row carries `__canonical_prop__` attached
    (CanonicalProp instance) which the runner consumes downstream to
    override `book_count` + `tp` + `tp_source` on NormalizedMetrics and
    stamp canonical audit fields on the persisted output doc.

    Caller is responsible for filtering `__canonical_prop__` from any
    schema-validated output projection.
    """
    cps = build_canonical_props(raw_rows, sport=sport)
    rows_by_canonical: Dict[
        Tuple[str, str, str, float], List[Dict[str, Any]]
    ] = {}
    for r in raw_rows:
        fam, _root, _alt = normalize_market(sport, r.get("market"))
        if fam is None or r.get("line") is None:
            continue
        try:
            ln = round(float(r["line"]), 2)
        except (TypeError, ValueError):
            continue
        k = (str(r.get("event_id") or ""),
             str(r.get("player_name_normalized") or ""),
             fam, ln)
        rows_by_canonical.setdefault(k, []).append(r)
    eval_rows: List[Dict[str, Any]] = []
    for cp in cps:
        key = (cp.event_id, cp.player_name_normalized,
               cp.stat_family, cp.canonical_line)
        candidate_rows = rows_by_canonical.get(key, [])
        for side, prices, best_price, best_book in (
            ("OVER", cp.over_prices, cp.best_over_price, cp.best_over_book),
            ("UNDER", cp.under_prices, cp.best_under_price, cp.best_under_book),
        ):
            if not prices or best_price is None or best_book is None:
                continue
            rep: Optional[Dict[str, Any]] = None
            for cand in candidate_rows:
                if ((cand.get("side") or "").upper() == side and
                        (cand.get("book") or "").lower() == best_book):
                    rep = cand
                    break
            if rep is None:
                for cand in candidate_rows:
                    if (cand.get("side") or "").upper() == side:
                        rep = cand
                        break
            if rep is None:
                continue
            ev = dict(rep)
            ev["market"] = cp.canonical_market_key
            ev["is_alternate"] = False
            ev["book"] = best_book
            ev["odds"] = int(best_price)
            ev["side"] = side
            ev["__canonical_prop__"] = cp
            eval_rows.append(ev)
    summary = {
        "canonical_engine_version": CANONICAL_ENGINE_VERSION,
        "raw_rows_collapsed_from": len(raw_rows),
        "canonical_props_built": len(cps),
        "canonical_eval_rows": len(eval_rows),
    }
    return eval_rows, summary


async def _run_layer3(adapter: SportReplayAdapter, db, *,
                       game_date: str, snapshot_iso: str,
                       mem_limit_mb: int, force: bool,
                       odds_collection: Optional[str] = None) -> Dict[str, Any]:
    """Delegate to the sport's Layer-3 model-replay engine.

    `odds_collection` lets the SSOT historical replay redirect Layer-3
    reads to `sgo_replay_alt_odds_raw` instead of the live
    `mlb_historical_alt_odds_raw`. Defaults to the adapter's configured
    odds collection (which the SGO script monkey-patches at startup),
    falling back to whatever the engine itself defaults to.
    """
    if adapter.SPORT == "mlb":
        oc = odds_collection or adapter.config.odds_collection
        return await mlb_layer3_replay_date(
            db, game_date,
            snapshot_iso=snapshot_iso,
            mem_limit_mb=mem_limit_mb,
            force=force,
            odds_collection=oc,
        )
    if adapter.SPORT == "nba":
        oc = odds_collection or adapter.config.odds_collection
        return await nba_layer3_replay_date(
            db, game_date,
            snapshot_iso=snapshot_iso,
            mem_limit_mb=mem_limit_mb,
            force=force,
            odds_collection=oc,
        )
    raise NotImplementedError(
        f"Layer-3 not yet implemented for sport={adapter.SPORT!r}"
    )


def _layer3_outputs_collection_name(adapter: SportReplayAdapter) -> str:
    if adapter.SPORT == "mlb":
        return MLB_LAYER3_OUT_COLL
    if adapter.SPORT == "nba":
        return NBA_LAYER3_OUT_COLL
    raise NotImplementedError(adapter.SPORT)


async def _ensure_indexes(db, *, adapter: SportReplayAdapter,
                            output_namespace: str = "production_replay") -> None:
    out_coll = outputs_collection_name(adapter, output_namespace=output_namespace)
    await db[out_coll].create_index(
        [("replay_serial", ASCENDING),
         ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING),
         ("line", ASCENDING),
         ("side", ASCENDING),
         ("book", ASCENDING)],
        name=f"{output_namespace}_outputs_compound_unique", unique=True,
    )
    await db[out_coll].create_index("replay_serial")
    await db[out_coll].create_index([("sport", ASCENDING),
                                      ("game_date", ASCENDING)])

    runs_coll = runs_collection_name(adapter, output_namespace=output_namespace)
    await db[runs_coll].create_index("serial", unique=True)
    await db[runs_coll].create_index([("sport", ASCENDING),
                                       ("game_date", ASCENDING),
                                       ("tier", ASCENDING)])

    # Phase 3 — card collection. `replay_serial + rank` is the only
    # natural unique key; cards are write-once per run.
    cards_coll = cards_collection_name(adapter, output_namespace=output_namespace)
    await db[cards_coll].create_index(
        [("replay_serial", ASCENDING), ("rank", ASCENDING)],
        name=f"{output_namespace}_cards_serial_rank_unique", unique=True,
    )
    await db[cards_coll].create_index("replay_serial")
    await db[cards_coll].create_index([("sport", ASCENDING),
                                        ("tier", ASCENDING)])


def _project_layer3_to_output(
    row: Dict[str, Any], *,
    serial: str, sport: str, tier: str,
    gate_pass: bool, failed_gates: List[str],
    gate_config_version: str,
) -> Dict[str, Any]:
    """Project a Layer-3 row into a ProductionReplayOutput-shaped dict.

    Schema validation done up-front via Pydantic to guarantee shape;
    the dict is what gets persisted (extra audit fields kept).
    """
    implied = row.get("implied_probability")
    if implied is None and row.get("odds") is not None:
        implied = _american_to_implied(int(row["odds"]))
    fair = row.get("fair_probability")
    edge = row.get("edge")
    if edge is None and fair is not None and implied is not None:
        edge = float(fair) - float(implied)

    schema_dict = ProductionReplayOutput(
        replay_serial=serial,
        sport=sport,
        game_date=row["game_date"],
        snapshot_iso=row["snapshot_iso"],
        event_id=row["event_id"],
        home_team=row.get("home_team"),
        away_team=row.get("away_team"),
        commence_time=str(row.get("commence_time") or ""),
        player_name=row.get("player_name"),
        player_name_normalized=row["player_name_normalized"],
        stat_family=row["stat_family"],
        market=row["market"],
        is_alternate=bool(row.get("is_alternate")),
        line=float(row["line"]),
        side=row["side"],
        book=row.get("book") or "",
        odds=int(row["odds"]),
        projection_mu=float(row.get("projection_mu") or 0.0),
        sigma=float(row.get("sigma") or 0.0),
        model_probability=float(row.get("model_probability") or 0.0),
        fair_probability=float(fair or 0.0),
        implied_probability=float(implied or 0.0),
        edge=float(edge or 0.0),
        hit_rate_l5=row.get("hit_rate_l5"),
        hit_rate_l10=row.get("hit_rate_l10"),
        hit_rate_l20=row.get("hit_rate_l20"),
        cv=row.get("cv"),
        tier=tier,
        gate_pass=bool(gate_pass),
        failed_gates=list(failed_gates),
        gate_config_version=gate_config_version,
    ).model_dump()
    return schema_dict


# ──────────────────────────────────────────────────────────────────────
async def run_production_replay(
    db, *,
    sport: str,
    game_date: str,
    snapshot_iso: Optional[str] = None,
    tier: str = "war_zone",
    mem_limit_mb: int = 3_500,
    force_layer3: bool = False,
    dry_run: bool = False,
    notes: Optional[str] = None,
    gate_path: str = "legacy_wz",
    canonical_path: bool = False,
    output_namespace: str = "production_replay",
    eligibility_predicate: Optional[Any] = None,
    audit_envelope: Optional[Dict[str, Any]] = None,
    serial_override: Optional[str] = None,
    allow_one_sided_for_accuracy_test: bool = False,
    sh_tp_gate_min_override: Optional[float] = None,
    sh_edge_gate_min_override: Optional[float] = None,
    sh_hit_rate_gate_min_override: Optional[float] = None,
    sh_cv_gate_max_override: Optional[float] = None,
    disable_all_gates_for_accuracy_test: bool = False,
    odds_collection: Optional[str] = None,
    research_mode: bool = False,
) -> Dict[str, Any]:
    """End-to-end Phase 2c orchestrator.

    Args:
        db: motor AsyncIOMotorDatabase.
        sport: "mlb" | "nba" | "nfl".
        game_date: ISO YYYY-MM-DD.
        snapshot_iso: ISO instant for the odds snapshot to replay. If
            None, defaults to {game_date}T11:00:00Z (matches Layer 3).
        tier: tier name from `adapter.config.tier_short_codes`
            (default "war_zone" — the only currently-graded tier).
        mem_limit_mb: hard ceiling for Layer-3 RSS before bail.
        force_layer3: if True, re-run Layer-3 even if `status=completed`.
        dry_run: if True, do not persist run / output docs.
        notes: free-form note pinned on the run doc.
        gate_path: "legacy_wz" (Phase 2c default — WZ-only gate spec,
            byte-identical to prior sweeps) or "universal" (Phase 4 —
            routes through the production `evaluate_tier_with_overrides`
            for any tier; required for honest SH/FL evaluation).
        canonical_path: Phase 6 Phase 2 (default False). When True,
            collapse Layer-3 raw book rows into ONE CanonicalProp per
            (event, player, stat_family, canonical_line), then evaluate
            each canonical prop ONCE per side (OVER/UNDER) instead of
            once per raw book row. Eliminates SH starvation caused by
            fragmented per-book rows. Implies `gate_path="universal"`
            (the only valid combination — legacy WZ gate spec cannot
            consume canonical metrics).
        output_namespace: Universal Pipeline Phase B (2026-05-17). When
            set to anything other than the default
            `"production_replay"`, runs are written to
            `{sport}_{output_namespace}_{runs,outputs,cards}`
            collections instead of the historical replay collections.
            Used by the universal runner's `mode="historical"` +
            `output_namespace="test"` combination to route test
            outputs to `{sport}_test_runs/outputs/cards`. The default
            keeps every legacy replay caller byte-identical.
        eligibility_predicate: Phase B. Optional `callable(row) -> bool`
            invoked on every raw Layer-3 row BEFORE canonical collapse
            and BEFORE gate evaluation. Rows where the predicate
            returns False are skipped entirely (counted in
            `eligibility_rejects`). Used by the universal runner to
            enforce production eligibility (filter_pp_playable etc.)
            on historical inputs.
        audit_envelope: Phase B. Optional dict of pipeline-version /
            git_sha / config_hash / source_collections / etc.
            stamped on the run doc as `audit_envelope`. Caller is
            expected to supply this; the runner does not synthesize.
        serial_override: Phase B. Optional explicit serial — when
            present, used verbatim instead of incrementing the
            sport-prefixed counter. Used by the universal runner to
            pass through its own test-id format
            (`{SPORT}-{MODE}-{YYYYMMDD}-{HHMMUTC}-{NNNNN}`).

    Returns the run-summary dict (same shape as the persisted run doc
    minus heavy fields).
    """
    # Canonical path implies the universal gate engine. Legacy WZ-only
    # spec is row-based and cannot consume collapsed canonical metrics.
    if canonical_path and gate_path != "universal":
        gate_path = "universal"
    adapter = _resolve_adapter(db, sport)
    if snapshot_iso is None:
        snapshot_iso = f"{game_date}T11:00:00Z"

    started_at = utc_now()
    rss0 = _rss_mb()

    # ── Audit pins ──────────────────────────────────────────────────
    if serial_override is not None:
        serial = serial_override
    else:
        serial = await next_replay_serial(
            db, adapter=adapter,
            date=game_date, tier=tier, snapshot_iso=snapshot_iso,
        )
    pipeline_ver = compute_production_pipeline_version(adapter)
    adapter_ver = adapter.adapter_version()
    versions = _resolve_scoring_versions(adapter)
    model_versions = _resolve_model_versions(adapter)
    input_pins_raw = await snapshot_input_collection_versions(
        db, adapter=adapter,
        game_date=game_date, snapshot_iso=snapshot_iso,
        odds_collection_override=odds_collection,
    )
    input_pins = {
        k: InputCollectionPin(**v).model_dump() for k, v in input_pins_raw.items()
    }

    run_doc_in = ProductionReplayRun(
        serial=serial, sport=adapter.SPORT,
        game_date=game_date, snapshot_iso=snapshot_iso, tier=tier,
        production_pipeline_version=pipeline_ver,
        scoring_config_version=versions["scoring_config_version"],
        gate_config_version=versions["gate_config_version"],
        model_versions=model_versions,
        feature_cache_version=versions["feature_cache_version"],
        adapter_version=adapter_ver,
        git_commit_sha=git_commit_sha(),
        input_collection_versions={
            k: InputCollectionPin(**v) for k, v in input_pins_raw.items()
        },
        replay_started_at=started_at,
        mode="historical", dry_run=dry_run, notes=notes,
    ).model_dump()
    run_doc_in["input_collection_versions"] = input_pins
    # ── Phase B audit envelope passthrough ──────────────────────────
    if audit_envelope is not None:
        run_doc_in["audit_envelope"] = audit_envelope
    run_doc_in["output_namespace"] = output_namespace

    # ── Persist initial run doc ─────────────────────────────────────
    if not dry_run:
        await _ensure_indexes(db, adapter=adapter,
                                 output_namespace=output_namespace)
        await db[runs_collection_name(
            adapter, output_namespace=output_namespace,
        )].update_one(
            {"serial": serial}, {"$set": run_doc_in}, upsert=True,
        )

    # ── Build the universal provider (for identity + future hooks) ──
    _provider = build_universal_historical_provider(  # noqa: F841 — used by Phase 3+
        db, adapter=adapter,
        game_date=game_date, snapshot_iso=snapshot_iso,
    )

    # ── Layer 3 — run model replay (or short-circuit) ───────────────
    layer3 = await _run_layer3(
        adapter, db,
        game_date=game_date, snapshot_iso=snapshot_iso,
        mem_limit_mb=mem_limit_mb, force=force_layer3,
        odds_collection=odds_collection,
    )

    # ── Layer 4 — fetch actuals + gate eval + grading ───────────────
    actuals = await adapter.fetch_actuals(game_date=game_date)

    layer3_coll = _layer3_outputs_collection_name(adapter)
    cursor = db[layer3_coll].find(
        {"sport": adapter.SPORT, "game_date": game_date,
         "snapshot_iso": snapshot_iso},
        projection={"_id": 0},
    )

    out_coll = outputs_collection_name(adapter, output_namespace=output_namespace)
    out_buffer: List[Dict[str, Any]] = []
    rows_scanned = 0
    rows_qualified = 0
    eligibility_rejects = 0
    wins = losses = pushes = ungraded = 0
    stake_total = profit_total = 0.0
    rss_peak = rss0
    # Accuracy-test telemetry — counts per bypass type. Only populated
    # when `allow_one_sided_for_accuracy_test=True`. Stamped on the
    # summary so the caller can audit how many rows actually had a
    # bypass applied without re-querying the outputs collection.
    accuracy_bypass_total = 0
    accuracy_bypass_tp_source = 0
    accuracy_bypass_market_structure = 0
    # SH tp_gate min override telemetry — test-only knob. Production
    # threshold dict is NOT mutated; this lowers `tp_gate` post-eval
    # only when caller explicitly supplies the override value.
    tp_gate_override_count = 0
    edge_gate_override_count = 0
    hit_rate_gate_override_count = 0
    cv_gate_override_count = 0

    # ── Phase 4 — preload field hydrators (universal gate path) ──────
    book_inventory: Dict[Any, Any] = {}
    player_game_logs: Dict[str, Any] = {}
    universal_gate_cfg_versions: Dict[str, str] = {}
    # ── Phase 4b — preload tier_reference_odds map for the snapshot.
    # This mirrors the live `_pick_reference_odds(...)` chain so the
    # universal odds-bucket router can route replay props using the
    # SAME ref_odds live serving sees. None when `gate_path=="legacy_wz"`.
    ref_odds_map: Dict[Any, Any] = {}
    if gate_path == "universal":
        if adapter.SPORT == "mlb":
            book_inventory = await load_book_inventory(
                db, sport=adapter.SPORT,
                game_date=game_date, snapshot_iso=snapshot_iso,
            )
            player_game_logs = await load_player_game_logs_as_of(
                db, game_date=game_date,
            )
            ref_odds_map = await load_reference_odds_for_snapshot(
                db, sport=adapter.SPORT,
                game_date=game_date, snapshot_iso=snapshot_iso,
            )
            logger.info(
                "[prod_replay_runner][phase4] hydrators loaded: "
                "book_inventory=%d keys, player_game_logs=%d players, "
                "ref_odds_map=%d (prop,side) keys",
                len(book_inventory), len(player_game_logs), len(ref_odds_map),
            )
        elif adapter.SPORT == "nba":
            # NBA Layer-3 rows are produced by the PRODUCTION scoring
            # stack (`nba_replay_engine.replay_date` →
            # `recompute_sport(db, "nba", dry_run=True)`), so the
            # universal gate decisions (`tier`, `gate_pass`,
            # `vision_score`, `tp`, `tp_source`, `edge_pct`, `cv`,
            # `hit_rate_l5/10/20`) are ALREADY stamped on each row.
            # No second metrics builder / second gate-engine pass —
            # consume the pre-computed decisions verbatim. This is
            # the only valid contract for NBA replay: same production
            # scorer, same gates, historical inputs.
            logger.info(
                "[prod_replay_runner][nba] universal gate path uses "
                "PRODUCTION-stamped row fields (tier/gate_pass/"
                "vision_score/tp/edge_pct/cv/hit_rate_*). No second "
                "gate-engine pass."
            )
        else:
            raise NotImplementedError(
                f"gate_path=universal not yet implemented for "
                f"sport={adapter.SPORT!r}"
            )
    if gate_path == "universal" and adapter.SPORT == "mlb":
        # Backwards-compat: keep the explicit log line that existed
        # before the NBA branch was added.
        pass

    def _resolve_universal_gate_cfg_version(stat_family: str, side: str) -> str:
        cache_key = f"{adapter.SPORT}|{tier}|{stat_family}|{side}"
        cached = universal_gate_cfg_versions.get(cache_key)
        if cached is not None:
            return cached
        cfg = resolve_thresholds(adapter.SPORT, tier, stat_family, side=side)
        # Deterministic SHA — same cfg dict always produces the same
        # version pin. Stripped to 16 chars to stay readable in run docs.
        canonical = _json.dumps(cfg, sort_keys=True, default=str)
        sha = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        ver = f"{adapter.SPORT}_{tier}_universal_{sha}"
        universal_gate_cfg_versions[cache_key] = ver
        return ver

    async def _flush():
        nonlocal out_buffer
        if not out_buffer or dry_run:
            out_buffer.clear()
            return
        ops = []
        for r in out_buffer:
            key = {
                "replay_serial": r["replay_serial"],
                "event_id": r["event_id"],
                "player_name_normalized": r["player_name_normalized"],
                "market": r["market"],
                "line": r["line"],
                "side": r["side"],
                "book": r["book"],
            }
            ops.append(UpdateOne(key, {"$set": r}, upsert=True))
        try:
            await db[out_coll].bulk_write(ops, ordered=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("[prod_replay_runner] outputs bulk_write failed: %s",
                          exc)
        out_buffer.clear()

    # ── Phase 6 Phase 2 — Canonical Prop Engine (flag-gated) ────────
    # When `canonical_path=True`, collapse raw Layer-3 book rows into
    # ONE CanonicalProp per (event, player, stat_family, canonical_line),
    # then evaluate each canonical prop ONCE per side (OVER/UNDER) using
    # the best-book price and aggregated cross-book book_count + devig.
    # Replaces the per-book-row iteration; downstream loop body is
    # unchanged so the universal gate path runs verbatim on the
    # canonical-derived rows.
    canonical_summary: Dict[str, Any] = {}
    canonical_eval_rows: List[Dict[str, Any]] = []
    if canonical_path:
        raw_rows_buffer: List[Dict[str, Any]] = []
        async for r in cursor:
            # Phase B — eligibility filter at the ingest seam. The
            # predicate is called on every raw Layer-3 row BEFORE
            # canonical collapse so the canonical universe is only
            # built from production-eligible rows (PP-playable etc.).
            if eligibility_predicate is not None:
                if not eligibility_predicate(r):
                    eligibility_rejects += 1
                    continue
            raw_rows_buffer.append(r)
        canonical_eval_rows, canonical_summary = _build_canonical_eval_rows(
            raw_rows_buffer, sport=adapter.SPORT,
        )
        logger.info(
            "[prod_replay_runner][canonical_v1] eligibility_rejects=%d "
            "collapsed %d raw rows → %d canonical props → %d eval rows "
            "(1 per cp × side)",
            eligibility_rejects,
            canonical_summary["raw_rows_collapsed_from"],
            canonical_summary["canonical_props_built"],
            canonical_summary["canonical_eval_rows"],
        )

    # Closure-state for non-canonical eligibility rejects (legacy WZ
    # path counts here; canonical path increments `eligibility_rejects`
    # in its build loop above).
    _legacy_rejects: List[int] = []

    async def _row_iter():
        if canonical_path:
            for r in canonical_eval_rows:
                yield r
        else:
            async for r in cursor:
                # Phase B — eligibility filter (non-canonical path).
                # Same contract as the canonical-path branch above.
                if eligibility_predicate is not None:
                    if not eligibility_predicate(r):
                        _legacy_rejects.append(1)
                        continue
                yield r

    async for row in _row_iter():
        rows_scanned += 1
        # Gate evaluation — two paths:
        #   • "legacy_wz"  : Phase 2c default. WZ-only spec. Byte-
        #     identical to historical runs.
        #   • "universal"  : Phase 4. Builds NormalizedMetrics from
        #     the replay row + hydrated context, then routes through
        #     the SAME `evaluate_tier_with_overrides` the live serving
        #     path uses. Per-tier `gate_config_version` is stamped per
        #     (stat_family, side) deterministically from the resolved
        #     threshold cfg SHA.
        row_gate_cfg_version = versions["gate_config_version"]
        ref_odds_for_row: Optional[int] = None
        ref_book_for_row: Optional[str] = None
        routed_tier_for_row: Optional[str] = None
        # Fix 2 (2026-05-17): pre-declare so the post-projection stamp
        # block can safely reference these on every code path (legacy_wz,
        # routing short-circuit, universal-pass). `metrics` and
        # `gate_result` remain None when no gate engine ran (e.g.
        # tier_odds_bucket_fail short-circuit), in which case the audit
        # stamp leaves the relevant fields None.
        metrics = None
        gate_result = None
        # Accuracy-test bypass telemetry per row — empty when no bypass
        # fired (the default for all production code paths).
        accuracy_bypass_gates: List[str] = []
        # SH tp_gate override telemetry per row — defaults to False so
        # the audit stamp is consistent across every code path.
        tp_gate_override_applied_row = False
        edge_gate_override_applied_row = False
        hit_rate_gate_override_applied_row = False
        cv_gate_override_applied_row = False
        if gate_path == "universal":
            # ── Phase 4b — Universal odds-bucket routing ────────────
            # Look up the tier_reference_odds the live path WOULD have
            # computed for this prop at this snapshot. Then call the
            # universal router. If the row's odds-bucket does not match
            # the tier we are evaluating, reject with
            # `tier_odds_bucket_fail` BEFORE the gate stack fires —
            # exactly as live serving does (scoring_stack.compute_tier,
            # 2026-04-29 hard contract: "each pick evaluated ONLY
            # within its routed odds tier; failing routed tier → REJECTED").
            cp_for_routing = row.get("__canonical_prop__")
            if adapter.SPORT == "nba":
                # NBA: the production scorer's `tier` decision already
                # combines odds-bucket routing AND gate pass status
                # (live serving uses the same universal gate engine that
                # ran inside `recompute_sport`). Use that decision as
                # the routed tier so the runner's per-tier filter
                # (`routed_tier == tier`) reproduces the production
                # routing exactly. Use the per-book row.odds as the
                # ref_odds for audit visibility.
                routed_tier_for_row = (row.get("tier") or "").lower() or None
                if row.get("odds") is not None:
                    try:
                        ref_odds_for_row = int(row["odds"])
                    except (TypeError, ValueError):
                        ref_odds_for_row = None
                ref_book_for_row = row.get("book")
            elif cp_for_routing is not None:
                # Canonical path: route on the canonical best price for
                # this side. ref_book is the best-book the canonical
                # engine picked; ref_odds is its American price.
                if (row.get("side") or "OVER").upper() == "OVER":
                    ref_odds_for_row = cp_for_routing.best_over_price
                    ref_book_for_row = cp_for_routing.best_over_book
                else:
                    ref_odds_for_row = cp_for_routing.best_under_price
                    ref_book_for_row = cp_for_routing.best_under_book
                routed_tier_for_row = get_odds_bucket(ref_odds_for_row)
            else:
                ref_key = (
                    str(row.get("event_id")),
                    str(row.get("player_name_normalized")),
                    str(row.get("market")),
                    float(row.get("line")) if row.get("line") is not None else None,
                    (row.get("side") or "OVER").upper(),
                )
                ref_pair = ref_odds_map.get(ref_key)
                if ref_pair:
                    ref_odds_for_row, ref_book_for_row = ref_pair
                routed_tier_for_row = get_odds_bucket(ref_odds_for_row)
            # 2026-05-22 RESEARCH MODE: in research_mode we never reject
            # rows by odds-bucket routing. The bucket becomes a LABEL
            # ({safe_haven,front_lines,war_zone,unknown_odds}_candidate)
            # that the grid sweep uses for cohort routing, NOT a hard
            # gate. The metrics + gate engine still fire so every
            # downstream field (tp, edge, cv, hr_l5/10/20, gate_pass)
            # is populated — but the row is preserved regardless of
            # whether routed_tier == tier.
            if (not research_mode) and routed_tier_for_row != tier:
                gate_pass = False
                failed = [TIER_ODDS_BUCKET_FAIL]
                # Don't build metrics / call gate engine — short-circuit.
            elif adapter.SPORT == "nba":
                # ── NBA universal gate path ───────────────────────────
                # Layer-3 row carries the PRODUCTION-decided tier (from
                # `compute_scoring_stack` invoked inside the NBA replay
                # engine's `recompute_sport` call). The row qualifies
                # for THIS tier iff `row.tier == tier`. No second gate
                # eval is run — that would double-evaluate the same
                # production decision. Per-test override knobs operate
                # against the row's already-stamped tp / edge_pct /
                # hit_rate_l20 / cv fields.
                prod_tier = (row.get("tier") or "").lower()
                prod_gate_pass = bool(row.get("gate_pass"))
                gate_pass = (prod_tier == tier) and prod_gate_pass
                failed = []
                if not gate_pass:
                    # Stamp a single canonical failure reason so the
                    # output's `failed_gates` field carries semantic
                    # signal (matches MLB's `failed_gates` list shape).
                    if prod_tier == "rejected" or prod_tier == "":
                        failed = ["production_tier_rejected"]
                    elif prod_tier != tier:
                        failed = [f"production_tier_is_{prod_tier}"]
                    else:
                        failed = ["production_gate_pass_false"]
                # SH tp_gate min override — operate on row.tp directly.
                # Row.tp is on the 0..100 pp scale (per
                # `_project_score_doc_to_layer3_row`). Drop the
                # `production_tier_*` failure tag when override floor
                # is met.
                row_tp = row.get("tp")
                if (sh_tp_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and failed
                        and isinstance(row_tp, (int, float))
                        and float(row_tp) >= float(sh_tp_gate_min_override)):
                    failed = []
                    gate_pass = True
                    tp_gate_override_applied_row = True
                    tp_gate_override_count += 1
                row_edge_pct = row.get("edge_pct")
                if (sh_edge_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and failed
                        and isinstance(row_edge_pct, (int, float))
                        and float(row_edge_pct) >= float(sh_edge_gate_min_override)):
                    failed = []
                    gate_pass = True
                    edge_gate_override_applied_row = True
                    edge_gate_override_count += 1
                row_hr = row.get("hit_rate_l20")
                if (sh_hit_rate_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and failed
                        and isinstance(row_hr, (int, float))
                        and float(row_hr) >= float(sh_hit_rate_gate_min_override)):
                    failed = []
                    gate_pass = True
                    hit_rate_gate_override_applied_row = True
                    hit_rate_gate_override_count += 1
                row_cv = row.get("cv")
                if (sh_cv_gate_max_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and failed
                        and isinstance(row_cv, (int, float))
                        and float(row_cv) <= float(sh_cv_gate_max_override)):
                    failed = []
                    gate_pass = True
                    cv_gate_override_applied_row = True
                    cv_gate_override_count += 1
                # All-gates accuracy-test bypass.
                if disable_all_gates_for_accuracy_test:
                    if failed:
                        for g in failed:
                            if g not in accuracy_bypass_gates:
                                accuracy_bypass_gates.append(g)
                        failed = []
                    gate_pass = True
                # Per-row gate_config_version: NBA universal carries one
                # cfg pin for the whole run (matches MLB single-tier
                # behaviour when only one (family,side) is evaluated).
                row_gate_cfg_version = versions["gate_config_version"]
            else:
                metrics = build_metrics_from_replay_row(
                    row, tier=tier, sport=adapter.SPORT,
                    book_inventory=book_inventory,
                    player_game_logs=player_game_logs,
                )
                # ── Canonical override: when canonical_path=True the
                # row carries an attached CanonicalProp; we override
                # book_count + tp + tp_source on the metrics so the
                # universal gate engine sees the same aggregated
                # market view live serving sees per canonical prop
                # (NOT the fragmented per-book inventory).
                cp_attached = row.get("__canonical_prop__")
                if cp_attached is not None:
                    if (row.get("side") or "").upper() == "OVER":
                        side_book_count = cp_attached.book_count_over
                        devig_p = cp_attached.devig_over_probability
                    else:
                        side_book_count = cp_attached.book_count_under
                        devig_p = cp_attached.devig_under_probability
                    tp_pp = (round(float(devig_p) * 100.0, 4)
                             if devig_p is not None else metrics.tp)
                    # Map canonical `devig_method` → live `tp_source`:
                    #   same_book / cross_book → "devig"
                    #   one_sided              → "one_sided"
                    #   None                   → preserve metrics.tp_source
                    if cp_attached.devig_method in ("same_book", "cross_book"):
                        tp_src = "devig"
                    elif cp_attached.devig_method == "one_sided":
                        tp_src = "one_sided"
                    else:
                        tp_src = metrics.tp_source
                    metrics = _dc_replace(
                        metrics,
                        book_count=int(side_book_count) if side_book_count else metrics.book_count,
                        tp=tp_pp,
                        tp_source=tp_src,
                    )
                gate_result = evaluate_tier_with_overrides(metrics)
                gate_pass = bool(gate_result.passed)
                failed = list(gate_result.failed_gates)
                # ── Accuracy-test bypass (HISTORICAL ONLY) ───────────
                # When the caller explicitly sets
                # `allow_one_sided_for_accuracy_test=True`, drop the
                # gates whose entire purpose is to enforce "must have
                # devig / must not be one_sided alt" from `failed`,
                # so we can measure raw model accuracy on one-sided
                # props. PP-illegality, non-playable, missing-odds
                # rejections happen UPSTREAM in `apply_production_eligibility`
                # — they are NOT affected by this flag. All other gates
                # (hit_rate, cv, edge, tp, direction, margin, projection,
                # market_trap, vision_score) remain active.
                if (allow_one_sided_for_accuracy_test
                        and metrics.tp_source == "one_sided"):
                    if "tp_source_gate" in failed:
                        failed.remove("tp_source_gate")
                        accuracy_bypass_gates.append("tp_source_gate")
                    # `market_structure_gate` can hold multiple
                    # reject rules. Only bypass when this run's
                    # rejection was specifically the one_sided rule
                    # (inspected via the gate detail's threshold dict).
                    ms_detail = gate_result.gate_details.get(
                        "market_structure_gate"
                    )
                    if (ms_detail is not None
                            and not ms_detail.passed
                            and "market_structure_gate" in failed):
                        thr = ms_detail.threshold or {}
                        rules = (
                            thr.get("reject_when")
                            if isinstance(thr, dict) else None
                        ) or {}
                        if rules.get("tp_source") == "one_sided":
                            failed.remove("market_structure_gate")
                            accuracy_bypass_gates.append(
                                "market_structure_gate"
                            )
                    if accuracy_bypass_gates:
                        gate_pass = (len(failed) == 0)
                        accuracy_bypass_total += 1
                        if "tp_source_gate" in accuracy_bypass_gates:
                            accuracy_bypass_tp_source += 1
                        if "market_structure_gate" in accuracy_bypass_gates:
                            accuracy_bypass_market_structure += 1
                # ── SH tp_gate min override (HISTORICAL TEST ONLY) ──
                # Production threshold dict is NEVER mutated. After the
                # gate engine evaluates the canonical `tp_gate.min` floor,
                # if the caller supplied `sh_tp_gate_min_override` AND
                # we are on the safe_haven tier AND the gate detail's
                # actual `p_model_pct` clears the override floor, drop
                # `tp_gate` from `failed` and recompute `gate_pass`.
                # All other gates remain authoritative.
                tp_gate_override_applied_row = False
                if (sh_tp_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and "tp_gate" in failed
                        and gate_result is not None):
                    tp_detail = gate_result.gate_details.get("tp_gate")
                    if (tp_detail is not None
                            and tp_detail.actual is not None
                            and float(tp_detail.actual)
                                >= float(sh_tp_gate_min_override)):
                        failed.remove("tp_gate")
                        tp_gate_override_applied_row = True
                        tp_gate_override_count += 1
                        gate_pass = (len(failed) == 0)
                # ── SH edge_gate min override (HISTORICAL TEST ONLY) ──
                # Lower `edge_gate.min` floor. Drop edge_gate from
                # `failed` when actual edge_pct >= override.
                if (sh_edge_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and "edge_gate" in failed
                        and gate_result is not None):
                    e_detail = gate_result.gate_details.get("edge_gate")
                    if (e_detail is not None
                            and e_detail.actual is not None
                            and float(e_detail.actual)
                                >= float(sh_edge_gate_min_override)):
                        failed.remove("edge_gate")
                        edge_gate_override_applied_row = True
                        edge_gate_override_count += 1
                        gate_pass = (len(failed) == 0)
                # ── SH hit_rate_gate min override (TEST ONLY) ──
                # Lower hit-rate floor. Drop hit_rate_gate when actual
                # hit rate (in pp) meets the override floor.
                if (sh_hit_rate_gate_min_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and "hit_rate_gate" in failed
                        and gate_result is not None):
                    hr_detail = gate_result.gate_details.get(
                        "hit_rate_gate"
                    )
                    if (hr_detail is not None
                            and hr_detail.actual is not None
                            and float(hr_detail.actual)
                                >= float(sh_hit_rate_gate_min_override)):
                        failed.remove("hit_rate_gate")
                        hit_rate_gate_override_applied_row = True
                        hit_rate_gate_override_count += 1
                        gate_pass = (len(failed) == 0)
                # ── SH cv_gate max override (TEST ONLY) ──
                # Raise the CV ceiling. Drop cv_gate when actual cv
                # is at or below the override cap. NOTE: when the
                # engine swapped cv_gate → margin_gate (line == 0.5),
                # this knob does NOT apply (margin_gate failures are
                # left alone — that's a binary-line construct, not a
                # CV ceiling).
                if (sh_cv_gate_max_override is not None
                        and tier in ("safe_haven", "front_lines", "war_zone")
                        and "cv_gate" in failed
                        and gate_result is not None):
                    cv_detail = gate_result.gate_details.get("cv_gate")
                    if (cv_detail is not None
                            and cv_detail.actual is not None
                            and float(cv_detail.actual)
                                <= float(sh_cv_gate_max_override)):
                        failed.remove("cv_gate")
                        cv_gate_override_applied_row = True
                        cv_gate_override_count += 1
                        gate_pass = (len(failed) == 0)
                row_gate_cfg_version = _resolve_universal_gate_cfg_version(
                    metrics.stat_family, metrics.side,
                )
                # ── ALL-GATES bypass (HISTORICAL TEST ONLY) ────────
                # When `disable_all_gates_for_accuracy_test=True`, drop
                # EVERY gate failure from `failed` (after the engine
                # has already populated `gate_result.gate_details` for
                # audit) and force `gate_pass=True`. The `tier_odds_bucket_fail`
                # short-circuit ABOVE this block is NOT affected —
                # rows that route to a different tier still get
                # rejected (otherwise "Safe Haven candidate pool"
                # would be meaningless). Used to enumerate the
                # complete set of props within the tier's odds
                # bucket with zero gate filtering.
                if disable_all_gates_for_accuracy_test:
                    if failed:
                        # stamp every failure on accuracy_bypass_gates
                        # so the output doc audit shows exactly which
                        # gates were bypassed for this row.
                        for g in list(failed):
                            if g not in accuracy_bypass_gates:
                                accuracy_bypass_gates.append(g)
                        failed = []
                    gate_pass = True
        else:
            gate_pass, failed = mlb_layer4_evaluate_gates(row) \
                if adapter.SPORT == "mlb" else (False, ["unsupported_sport"])

        # Project to the schema-conformant output shape.
        out_doc = _project_layer3_to_output(
            row, serial=serial, sport=adapter.SPORT, tier=tier,
            gate_pass=gate_pass, failed_gates=failed,
            gate_config_version=row_gate_cfg_version,
        )
        # Stamp routing audit on the output doc (universal path only).
        if gate_path == "universal":
            out_doc["tier_reference_odds"] = ref_odds_for_row
            out_doc["tier_reference_book"] = ref_book_for_row
            out_doc["routed_tier"] = routed_tier_for_row
            # 2026-05-22 RESEARCH MODE candidate label. Always stamped
            # (research and prod), so the grid sweep can cohort rows by
            # routed bucket regardless of whether the row passed gates.
            # For null odds we use the explicit "odds_na" sentinel so
            # rows are never silently dropped from cohort analysis.
            if routed_tier_for_row:
                out_doc["odds_bucket_candidate"] = f"{routed_tier_for_row}_candidate"
            else:
                out_doc["odds_bucket_candidate"] = "odds_na_candidate"
                out_doc["odds_na_flag"] = True
            # Researchers need to distinguish a real production-gate-pass
            # from a research_mode-attached metric. This flag is purely
            # advisory — gate_pass still reflects ONLY the gate outcome.
            out_doc["research_mode"] = bool(research_mode)
        # Fix 2 (2026-05-17) — Stamp the SSOT decision metrics on every
        # output doc so post-hoc audits don't see `None` for fields the
        # gate engine actually evaluated against. NO threshold or gate
        # behavior changes — this is a pure output-mapping fix.
        #   • tp                 — pp scale (0-100), post-canonical override
        #   • tp_source          — "devig" | "one_sided" | None
        #   • edge_pct           — pp scale (0-100), as seen by gates
        #   • is_alternate_market — bool, mirrors metrics.is_alt
        #   • devig_method        — canonical devig method ("same_book" |
        #                           "cross_book" | "one_sided" | None)
        #   • canonical_edge      — pp edge using the canonical TP
        #                           (metrics.tp − implied_pp), only when
        #                           canonical_path attached the prop
        #   • gate_failed_reasons — {gate_type: reason_code} for every
        #                           gate that failed (from gate_details)
        if metrics is not None:
            out_doc["tp"] = metrics.tp
            out_doc["tp_source"] = metrics.tp_source
            out_doc["edge_pct"] = metrics.edge_pct
            out_doc["is_alternate_market"] = (
                bool(metrics.is_alt) if metrics.is_alt is not None
                else bool(row.get("is_alternate"))
            )
        elif adapter.SPORT == "nba" and gate_path == "universal":
            # NBA universal path: inherit the SSOT scoring fields the
            # PRODUCTION scorer already stamped on the Layer-3 row. No
            # metrics object exists (no second eval pass).
            out_doc["tp"] = row.get("tp")
            out_doc["tp_source"] = row.get("tp_source")
            out_doc["edge_pct"] = row.get("edge_pct")
            out_doc["is_alternate_market"] = bool(row.get("is_alternate"))
            # SSOT mirror fields read by the downstream mirror in
            # `scripts.sgo.historical_full_pipeline_replay._mirror_to_legacy`.
            # These come straight from the production scorer
            # (`recompute_sport` → `compute_scoring_stack`); the runner
            # passes them through verbatim so the legacy collection
            # contains the same fields live serving uses.
            out_doc["vision_score"] = row.get("vision_score")
            out_doc["vision_score_raw"] = row.get("vision_score_raw")
            out_doc["p_model"] = row.get("model_probability")
            out_doc["p_true_active"] = row.get("p_true_active")
            out_doc["p_true_method"] = row.get("p_true_method")
            # Override the runner-tier stamp (= caller's `tier` arg) with
            # the production-decided tier from the Layer-3 row. The
            # `gate_pass` reflects whether `row.tier == tier`, but the
            # `tier` field stays the production decision so audit tools
            # can see what tier the prop ACTUALLY landed in.
            prod_tier = row.get("tier")
            if prod_tier:
                out_doc["tier"] = prod_tier
            # devig_method passes through from the score doc.
            out_doc["devig_method"] = row.get("devig_method")
        else:
            out_doc["tp"] = None
            out_doc["tp_source"] = None
            out_doc["edge_pct"] = None
            out_doc["is_alternate_market"] = bool(row.get("is_alternate"))
        # devig_method default (None for non-canonical); overwritten in
        # the canonical block below when cp_attached_doc is present.
        out_doc["devig_method"] = None
        # canonical_edge — only meaningful when canonical path computed
        # the canonical TP into metrics.tp.
        out_doc["canonical_edge"] = None
        if (metrics is not None
                and row.get("__canonical_prop__") is not None
                and metrics.tp is not None
                and row.get("odds") is not None):
            implied_pp = _american_to_implied(int(row["odds"])) * 100.0
            out_doc["canonical_edge"] = round(
                float(metrics.tp) - float(implied_pp), 4
            )
        # gate_failed_reasons — {gate_type: reason_code} per failed gate.
        if gate_result is not None:
            out_doc["gate_failed_reasons"] = {
                gt: (d.reason_code if d is not None else None)
                for gt, d in gate_result.gate_details.items()
                if d is not None and not d.passed
            }
        else:
            # legacy_wz path or short-circuit: surface the failed gate
            # names as keys with None reason codes so the schema field
            # is always present in audits.
            out_doc["gate_failed_reasons"] = {gt: None for gt in failed}
        # Accuracy-test mode audit fields — always stamped so callers
        # can filter outputs by mode without re-querying the run doc.
        out_doc["accuracy_test_mode_active"] = bool(
            allow_one_sided_for_accuracy_test
        )
        out_doc["accuracy_test_bypass_applied"] = bool(accuracy_bypass_gates)
        out_doc["accuracy_test_bypass_gates"] = list(accuracy_bypass_gates)
        # SH tp_gate min override audit (test-only).
        out_doc["tp_gate_override_value"] = (
            float(sh_tp_gate_min_override)
            if sh_tp_gate_min_override is not None else None
        )
        out_doc["tp_gate_override_applied"] = bool(
            tp_gate_override_applied_row
        )
        # SH edge / hit_rate / cv override audit fields (test-only).
        out_doc["edge_gate_override_value"] = (
            float(sh_edge_gate_min_override)
            if sh_edge_gate_min_override is not None else None
        )
        out_doc["edge_gate_override_applied"] = bool(
            edge_gate_override_applied_row
        )
        out_doc["hit_rate_gate_override_value"] = (
            float(sh_hit_rate_gate_min_override)
            if sh_hit_rate_gate_min_override is not None else None
        )
        out_doc["hit_rate_gate_override_applied"] = bool(
            hit_rate_gate_override_applied_row
        )
        out_doc["cv_gate_override_value"] = (
            float(sh_cv_gate_max_override)
            if sh_cv_gate_max_override is not None else None
        )
        out_doc["cv_gate_override_applied"] = bool(
            cv_gate_override_applied_row
        )
        # Stamp canonical-prop audit on the output doc (canonical path).
        cp_attached_doc = row.get("__canonical_prop__")
        if cp_attached_doc is not None:
            out_doc["canonical_path"] = True
            out_doc["canonical_engine_version"] = CANONICAL_ENGINE_VERSION
            out_doc["canonical_market_key"] = cp_attached_doc.canonical_market_key
            out_doc["canonical_source_rows"] = cp_attached_doc.source_rows_count
            out_doc["canonical_source_market_keys"] = list(
                cp_attached_doc.source_market_keys
            )
            out_doc["canonical_book_count_over"] = cp_attached_doc.book_count_over
            out_doc["canonical_book_count_under"] = cp_attached_doc.book_count_under
            out_doc["canonical_book_count_either_side"] = (
                cp_attached_doc.book_count_either_side_any_book
            )
            out_doc["canonical_best_over_price"] = cp_attached_doc.best_over_price
            out_doc["canonical_best_over_book"] = cp_attached_doc.best_over_book
            out_doc["canonical_best_under_price"] = cp_attached_doc.best_under_price
            out_doc["canonical_best_under_book"] = cp_attached_doc.best_under_book
            out_doc["canonical_devig_over_prob"] = (
                cp_attached_doc.devig_over_probability
            )
            out_doc["canonical_devig_under_prob"] = (
                cp_attached_doc.devig_under_probability
            )
            out_doc["canonical_has_cross_book_devig"] = (
                cp_attached_doc.has_cross_book_devig
            )
            out_doc["canonical_has_same_book_devig"] = (
                cp_attached_doc.has_same_book_devig
            )
        # 2026-05-18 — canonicalise `stat_family` on every output doc
        # so audits and downstream consumers always read SSOT names.
        from services.scoring.canonical_stats import canonical_family
        out_doc["stat_family"] = canonical_family(
            adapter.SPORT, out_doc.get("stat_family"))
        if cp_attached_doc is not None:
            # ── Phase 6 Phase 4 audit fields ──────────────────────
            out_doc["devig_method"] = cp_attached_doc.devig_method
            out_doc["same_book_pair_count"] = cp_attached_doc.same_book_pair_count
            out_doc["cross_book_pair_count"] = cp_attached_doc.cross_book_pair_count
            out_doc["books_used"] = list(cp_attached_doc.books_used)
            out_doc["over_books"] = cp_attached_doc.over_books
            out_doc["under_books"] = cp_attached_doc.under_books
            out_doc["same_book_devig_over_prob"] = (
                cp_attached_doc.same_book_devig_over_probability
            )
            out_doc["same_book_devig_under_prob"] = (
                cp_attached_doc.same_book_devig_under_probability
            )
            out_doc["cross_book_devig_over_prob"] = (
                cp_attached_doc.cross_book_devig_over_probability
            )
            out_doc["cross_book_devig_under_prob"] = (
                cp_attached_doc.cross_book_devig_under_probability
            )

        # Grade only the qualified picks (Layer 4 contract).
        # 2026-05-22 RESEARCH MODE: also grade rows where research_mode
        # is True AND we have outcomes to grade — so the grid sweep can
        # see W/L outcomes for EVERY scored row, not only the production-
        # gate-pass subset.
        actual_val: Optional[float] = None
        grade: Dict[str, Any]
        should_grade = gate_pass or (
            research_mode and row.get("player_name_normalized") in actuals
        )
        if should_grade:
            pdoc = actuals.get(row["player_name_normalized"]) or {}
            from services.scoring.canonical_stats import canonical_family
            if adapter.SPORT == "mlb":
                # 2026-05-18 — canonicalise the family AND fall back through
                # both family-key and statcast-field-key actuals shapes so
                # batter_strikeouts / walks_allowed grade correctly.
                from services.replay.mlb_feature_cache import _STAT_FIELD_MAP
                stat_fam = canonical_family("mlb", row["stat_family"])
                if stat_fam in pdoc:
                    actual_val = pdoc[stat_fam]
                else:
                    _fld = _STAT_FIELD_MAP.get(stat_fam, stat_fam)
                    actual_val = pdoc.get(_fld)
            else:
                # NBA / other sports: the adapter's `fetch_actuals` is
                # keyed by the canonical family name directly. No
                # statcast-field fallback needed.
                stat_fam = canonical_family(adapter.SPORT, row["stat_family"])
                actual_val = pdoc.get(stat_fam)
                if actual_val is None:
                    actual_val = pdoc.get(row["stat_family"])
            grade = adapter.grade_outcome(
                actual=actual_val,
                line=float(row["line"]),
                side=row["side"],
                odds=int(row["odds"]),
                stake=1.0,
            )
            st = grade["status"]
            if st == "win":   wins += 1
            elif st == "loss": losses += 1
            elif st == "push": pushes += 1
            else:              ungraded += 1
            # Only count toward rows_qualified when the row actually
            # passed the production gates (this preserves the canonical
            # "qualified" metric definition). Research-mode-only grades
            # are visible via grade_status but excluded from qualified-
            # rate denominators.
            if gate_pass:
                rows_qualified += 1
                stake_total += float(grade["stake_units"])
                profit_total += float(grade["profit_units"])
            out_doc["grade_status"] = st
            out_doc["actual_value"] = grade.get("actual")
            out_doc["profit_units"] = float(grade["profit_units"])
            out_doc["stake_units"]  = float(grade["stake_units"])
        else:
            out_doc["grade_status"] = "not_qualified"
            out_doc["actual_value"] = None
            out_doc["profit_units"] = 0.0
            out_doc["stake_units"]  = 0.0

        out_buffer.append(out_doc)
        if len(out_buffer) >= 500:
            await _flush()
            rss = _rss_mb()
            if rss > rss_peak: rss_peak = rss

    await _flush()

    # Reconcile non-canonical eligibility rejects into the counter
    # (canonical path already accumulated in its build loop).
    if not canonical_path and _legacy_rejects:
        eligibility_rejects += len(_legacy_rejects)

    # ── Phase 3 — Build & persist displayed cards ───────────────────
    # Pure builder; sport-agnostic; deterministic. Reads back the rows
    # we just wrote (lets the function operate on what's persisted, so
    # idempotent re-runs produce identical cards).
    card_docs: List[Dict[str, Any]] = []
    cards_displayed = 0
    if not dry_run:
        out_rows_for_cards: List[Dict[str, Any]] = []
        async for r in db[out_coll].find(
            {"replay_serial": serial, "gate_pass": True},
            projection={"_id": 0},
        ):
            out_rows_for_cards.append(r)
        card_docs = build_production_cards(
            out_rows_for_cards,
            tier=tier,
            replay_serial=serial,
            sport=adapter.SPORT,
            per_game_top_n_value=DEFAULT_PER_GAME_TOP_N,
            slate_top_k=DEFAULT_SLATE_TOP_K,
            dedupe_keys=DEFAULT_DEDUPE_KEYS,
            order_by=DEFAULT_ORDER_BY,
            require_gate_pass=True,
        )
        # Validate each card against the Pydantic schema before write —
        # one bad row should crash the run, not silently corrupt cards.
        validated = [ProductionReplayCard(**c).model_dump() for c in card_docs]
        if validated:
            cards_coll = cards_collection_name(
                adapter, output_namespace=output_namespace,
            )
            # Idempotent rewrite — drop prior cards for this serial first
            await db[cards_coll].delete_many({"replay_serial": serial})
            await db[cards_coll].insert_many(validated, ordered=False)
        cards_displayed = len(validated)
        logger.info(
            "[prod_replay_runner] cards built: %d displayed "
            "(qualified pool=%d) serial=%s",
            cards_displayed, len(out_rows_for_cards), serial,
        )

    # ── Finalise run doc ────────────────────────────────────────────
    finished_at = utc_now()
    elapsed = (finished_at - started_at).total_seconds()
    decided = wins + losses
    hit_rate_pct = (100.0 * wins / decided) if decided else 0.0
    roi_pct = (100.0 * profit_total / stake_total) if stake_total else 0.0

    summary = {
        "serial": serial,
        "sport": adapter.SPORT,
        "game_date": game_date,
        "snapshot_iso": snapshot_iso,
        "tier": tier,
        "gate_path": gate_path,
        "output_namespace": output_namespace,
        "rows_scanned": rows_scanned,
        "rows_qualified": rows_qualified,
        "eligibility_rejects": eligibility_rejects,
        "cards_displayed": cards_displayed,
        "wins": wins, "losses": losses, "pushes": pushes,
        "ungraded": ungraded,
        "hit_rate_pct": round(hit_rate_pct, 2),
        "roi_pct": round(roi_pct, 2),
        "profit_units": round(profit_total, 4),
        "stake_units": round(stake_total, 4),
        "elapsed_s": round(elapsed, 2),
        "rss_mb_peak": round(rss_peak, 1),
        "layer3_summary": layer3,
        "production_pipeline_version": pipeline_ver,
        "adapter_version": adapter_ver,
        "feature_cache_version": versions["feature_cache_version"],
        "gate_config_version": versions["gate_config_version"],
        "scoring_config_version": versions["scoring_config_version"],
        "universal_gate_cfg_versions": dict(universal_gate_cfg_versions),
        "canonical_path": bool(canonical_path),
        "canonical_engine_version": (
            CANONICAL_ENGINE_VERSION if canonical_path else None
        ),
        "canonical_summary": canonical_summary if canonical_path else None,
        "accuracy_test_mode_active": bool(allow_one_sided_for_accuracy_test),
        "accuracy_test_bypass_total": accuracy_bypass_total,
        "accuracy_test_bypass_tp_source_gate": accuracy_bypass_tp_source,
        "accuracy_test_bypass_market_structure_gate": (
            accuracy_bypass_market_structure
        ),
        "sh_tp_gate_min_override": (
            float(sh_tp_gate_min_override)
            if sh_tp_gate_min_override is not None else None
        ),
        "tp_gate_override_count": tp_gate_override_count,
        "sh_edge_gate_min_override": (
            float(sh_edge_gate_min_override)
            if sh_edge_gate_min_override is not None else None
        ),
        "edge_gate_override_count": edge_gate_override_count,
        "sh_hit_rate_gate_min_override": (
            float(sh_hit_rate_gate_min_override)
            if sh_hit_rate_gate_min_override is not None else None
        ),
        "hit_rate_gate_override_count": hit_rate_gate_override_count,
        "sh_cv_gate_max_override": (
            float(sh_cv_gate_max_override)
            if sh_cv_gate_max_override is not None else None
        ),
        "cv_gate_override_count": cv_gate_override_count,
        "audit_envelope": audit_envelope,
    }

    if not dry_run:
        await db[runs_collection_name(
            adapter, output_namespace=output_namespace,
        )].update_one(
            {"serial": serial},
            {"$set": {
                "replay_completed_at": finished_at,
                "elapsed_s": elapsed,
                "rss_mb_peak": round(rss_peak, 1),
                "rows_scanned": rows_scanned,
                "rows_qualified": rows_qualified,
                "eligibility_rejects": eligibility_rejects,
                "cards_displayed": cards_displayed,
                "wins": wins, "losses": losses, "pushes": pushes,
                "ungraded": ungraded,
                "hit_rate_pct": round(hit_rate_pct, 2),
                "roi_pct": round(roi_pct, 2),
                "profit_units": round(profit_total, 4),
                "gate_path": gate_path,
                "universal_gate_cfg_versions": dict(universal_gate_cfg_versions),
                "canonical_path": bool(canonical_path),
                "canonical_engine_version": (
                    CANONICAL_ENGINE_VERSION if canonical_path else None
                ),
                "canonical_summary": canonical_summary if canonical_path else None,
                "audit_envelope": audit_envelope,
            }},
        )

    return summary


__all__ = [
    "run_production_replay",
    "_build_canonical_eval_rows",
    "CANONICAL_ENGINE_VERSION",
]
