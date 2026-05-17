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
from services.replay.mlb_replay_gate_eval import (
    evaluate_gates as mlb_layer4_evaluate_gates,
    GATE_CONFIG_VERSION as MLB_GATE_CFG_V,
)
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as MLB_FEATURE_CACHE_V,
)

logger = logging.getLogger(__name__)

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
    return {}


async def _run_layer3(adapter: SportReplayAdapter, db, *,
                       game_date: str, snapshot_iso: str,
                       mem_limit_mb: int, force: bool) -> Dict[str, Any]:
    """Delegate to the sport's Layer-3 model-replay engine."""
    if adapter.SPORT == "mlb":
        return await mlb_layer3_replay_date(
            db, game_date,
            snapshot_iso=snapshot_iso,
            mem_limit_mb=mem_limit_mb,
            force=force,
        )
    raise NotImplementedError(
        f"Layer-3 not yet implemented for sport={adapter.SPORT!r}"
    )


def _layer3_outputs_collection_name(adapter: SportReplayAdapter) -> str:
    if adapter.SPORT == "mlb":
        return MLB_LAYER3_OUT_COLL
    raise NotImplementedError(adapter.SPORT)


async def _ensure_indexes(db, *, adapter: SportReplayAdapter) -> None:
    out_coll = outputs_collection_name(adapter)
    await db[out_coll].create_index(
        [("replay_serial", ASCENDING),
         ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING),
         ("line", ASCENDING),
         ("side", ASCENDING),
         ("book", ASCENDING)],
        name="prod_replay_outputs_compound_unique", unique=True,
    )
    await db[out_coll].create_index("replay_serial")
    await db[out_coll].create_index([("sport", ASCENDING),
                                      ("game_date", ASCENDING)])

    runs_coll = runs_collection_name(adapter)
    await db[runs_coll].create_index("serial", unique=True)
    await db[runs_coll].create_index([("sport", ASCENDING),
                                       ("game_date", ASCENDING),
                                       ("tier", ASCENDING)])

    # Phase 3 — card collection. `replay_serial + rank` is the only
    # natural unique key; cards are write-once per run.
    cards_coll = cards_collection_name(adapter)
    await db[cards_coll].create_index(
        [("replay_serial", ASCENDING), ("rank", ASCENDING)],
        name="prod_replay_cards_serial_rank_unique", unique=True,
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
    mem_limit_mb: int = 1_500,
    force_layer3: bool = False,
    dry_run: bool = False,
    notes: Optional[str] = None,
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

    Returns the run-summary dict (same shape as the persisted run doc
    minus heavy fields).
    """
    adapter = _resolve_adapter(db, sport)
    if snapshot_iso is None:
        snapshot_iso = f"{game_date}T11:00:00Z"

    started_at = utc_now()
    rss0 = _rss_mb()

    # ── Audit pins ──────────────────────────────────────────────────
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

    # ── Persist initial run doc ─────────────────────────────────────
    if not dry_run:
        await _ensure_indexes(db, adapter=adapter)
        await db[runs_collection_name(adapter)].update_one(
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
    )

    # ── Layer 4 — fetch actuals + gate eval + grading ───────────────
    actuals = await adapter.fetch_actuals(game_date=game_date)

    layer3_coll = _layer3_outputs_collection_name(adapter)
    cursor = db[layer3_coll].find(
        {"sport": adapter.SPORT, "game_date": game_date,
         "snapshot_iso": snapshot_iso},
        projection={"_id": 0},
    )

    out_coll = outputs_collection_name(adapter)
    out_buffer: List[Dict[str, Any]] = []
    rows_scanned = 0
    rows_qualified = 0
    wins = losses = pushes = ungraded = 0
    stake_total = profit_total = 0.0
    rss_peak = rss0

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

    async for row in cursor:
        rows_scanned += 1
        # Run gates (Phase 2c keeps the existing Layer-4 WZ spec; Phase 4
        # will swap to the production gate engine via NormalizedMetrics).
        gate_pass, failed = mlb_layer4_evaluate_gates(row) \
            if adapter.SPORT == "mlb" else (False, ["unsupported_sport"])

        # Project to the schema-conformant output shape.
        out_doc = _project_layer3_to_output(
            row, serial=serial, sport=adapter.SPORT, tier=tier,
            gate_pass=gate_pass, failed_gates=failed,
            gate_config_version=versions["gate_config_version"],
        )

        # Grade only the qualified picks (Layer 4 contract).
        actual_val: Optional[float] = None
        grade: Dict[str, Any]
        if gate_pass:
            rows_qualified += 1
            pdoc = actuals.get(row["player_name_normalized"]) or {}
            stat_fam = row["stat_family"]
            actual_val = pdoc.get(stat_fam)
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
            cards_coll = cards_collection_name(adapter)
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
        "rows_scanned": rows_scanned,
        "rows_qualified": rows_qualified,
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
    }

    if not dry_run:
        await db[runs_collection_name(adapter)].update_one(
            {"serial": serial},
            {"$set": {
                "replay_completed_at": finished_at,
                "elapsed_s": elapsed,
                "rss_mb_peak": round(rss_peak, 1),
                "rows_scanned": rows_scanned,
                "rows_qualified": rows_qualified,
                "cards_displayed": cards_displayed,
                "wins": wins, "losses": losses, "pushes": pushes,
                "ungraded": ungraded,
                "hit_rate_pct": round(hit_rate_pct, 2),
                "roi_pct": round(roi_pct, 2),
                "profit_units": round(profit_total, 4),
            }},
        )

    return summary


__all__ = ["run_production_replay"]
