"""Universal Production Pipeline Runner (Phase B).

`run_pipeline(sport, mode, snapshot_time, output_namespace, test_id)`
orchestrates the pipeline:

  1. resolve `IInputProvider` from (sport, mode).
  2. resolve `IOutputWriter` from output_namespace.
  3. provider.load_props(db).
  4. apply_production_eligibility(props, sport,
       use_pp_registry_fallback=(mode == "historical")).
       For mode=="live", the LiveInputProvider already invoked the
       SSOT inside the adapter — we skip the second call to avoid
       double-filtering (and a double counter-tag in the log).
  5. Build an `(event_id, player_normalized, stat_family, line, side)`
     allow-set from the eligibility result. This is the universal
     filter handed down to `run_production_replay`.
  6. Compose audit envelope.
  7. Delegate to `run_production_replay(canonical_path=True,
       output_namespace=output_namespace,
       eligibility_predicate=..., audit_envelope=...,
       serial_override=test_id)`.

This runner does NOT duplicate any production logic. It only:
  • picks the input source (live adapter vs historical alt-odds)
  • picks the output destination (production vs test)
  • injects an eligibility allow-set as a per-row predicate
  • passes through the audit envelope

Every gate, route, canonical, card-builder decision happens
inside the SAME production functions live serving uses.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from services.pipeline.eligibility import apply_production_eligibility
from services.pipeline.audit_envelope import build_audit_envelope
from services.pipeline.providers import (
    LiveInputProvider, MLBHistoricalInputProvider,
    ProductionOutputWriter, TestOutputWriter,
)
from services.replay.production_replay_runner import run_production_replay

logger = logging.getLogger(__name__)


# ── Provider / writer registries ───────────────────────────────────
SUPPORTED_TIERS: Tuple[str, ...] = ("safe_haven", "front_lines", "war_zone")


def _resolve_input_provider(sport: str, mode: str, *,
                              game_date: Optional[str] = None,
                              snapshot_time: Optional[str] = None):
    sport = sport.lower()
    mode = mode.lower()
    if mode == "live":
        return LiveInputProvider(sport=sport)
    if mode == "historical":
        if sport == "mlb":
            assert game_date is not None and snapshot_time is not None
            return MLBHistoricalInputProvider(
                game_date=game_date, snapshot_iso=snapshot_time,
            )
        if sport == "nba":
            # Scaffold — fail closed with an actionable message.
            # NBA historical replay already exists at
            # `services/replay/nba/...` but its output shape doesn't
            # match the live-prop contract this provider must return.
            # Wire-up is Phase D scope; the registry deliberately
            # exposes the gap rather than silently falling back to
            # MLB or live.
            raise NotImplementedError(
                "NBAHistoricalInputProvider not implemented (Phase D "
                "scope). To wire NBA historical mode, port the NBA "
                "replay engine's normalised prop output to the "
                "live-prop schema expected by "
                "`apply_production_eligibility` and register it here."
            )
        if sport == "nfl":
            raise NotImplementedError(
                "NFL historical mode not implemented (scaffold only)."
            )
    raise ValueError(f"Unknown mode: {mode!r}")


def _resolve_output_writer(output_namespace: str):
    output_namespace = output_namespace.lower()
    if output_namespace == "test":
        return TestOutputWriter()
    if output_namespace in ("production", "production_replay"):
        return ProductionOutputWriter()
    raise ValueError(
        f"Unknown output_namespace: {output_namespace!r}"
    )


# ── Tier short codes (back-compat with `next_replay_serial`'s table) ─
def _resolve_tier_for_run(tier: str) -> str:
    """Pass-through. Phase B emits all three tiers in one call when
    `tier='*'` — NOT YET; current contract is one tier per run.
    Default to safe_haven for the validation scope."""
    return tier


# ── Eligibility predicate builder ──────────────────────────────────
def _build_allow_set(
    eligible_props: List[Dict[str, Any]],
) -> Set[Tuple[str, str, str, float, str]]:
    """Build the (event, player, stat_family, line, side) key set
    used to filter raw Layer-3 rows BEFORE canonical collapse."""
    out: Set[Tuple[str, str, str, float, str]] = set()
    for p in eligible_props:
        try:
            line = round(float(p.get("line") or 0.0), 2)
        except (TypeError, ValueError):
            continue
        side = (p.get("side") or p.get("recommendation") or "").upper()
        out.add((
            str(p.get("event_id") or ""),
            str(p.get("player_name_normalized") or ""),
            str(p.get("stat_family") or ""),
            line,
            side,
        ))
    return out


def _make_eligibility_predicate(
    allow_set: Set[Tuple[str, str, str, float, str]],
) -> Callable[[Dict[str, Any]], bool]:
    def _pred(row: Dict[str, Any]) -> bool:
        try:
            line = round(float(row.get("line") or 0.0), 2)
        except (TypeError, ValueError):
            return False
        key = (
            str(row.get("event_id") or ""),
            str(row.get("player_name_normalized") or ""),
            str(row.get("stat_family") or ""),
            line,
            (row.get("side") or "").upper(),
        )
        return key in allow_set
    return _pred


# ── Public entry point ─────────────────────────────────────────────
async def run_pipeline(
    db, *,
    sport: str,
    mode: str,
    snapshot_time: Optional[str] = None,
    output_namespace: str = "test",
    test_id: str,
    tier: Any = "safe_haven",
    game_date: Optional[str] = None,
    canonical_path: bool = True,
    dry_run: bool = False,
    notes: Optional[str] = None,
    allow_one_sided_for_accuracy_test: bool = False,
) -> Dict[str, Any]:
    """Run one universal pipeline pass.

    Args:
        sport: "mlb" | "nba" | "nfl".
        mode: "live" | "historical".
        snapshot_time: ISO instant for the snapshot to replay
            (historical mode). Required for historical.
        output_namespace: "test" | "production" | "production_replay".
        test_id: Audit identifier. Format
            `{SPORT}-{MODE}-{YYYYMMDD}-{HHMMUTC}-{NNNNN}`.
            For multi-tier runs the runner appends a `-{TIER_CODE}`
            suffix per-tier so each tier writes its own run doc.
        tier: tier name ("safe_haven" / "front_lines" / "war_zone"),
            the literal string "all" (runs all three tiers), or a
            list of tier names.
        game_date: ISO YYYY-MM-DD. If None, derived from snapshot_time.
        canonical_path: Phase 6 canonical engine (default True for
            the universal pipeline — replay parity).
        dry_run: when True, no persistence.
        notes: free-form note on the run doc.

    Returns:
        Single-tier call → run summary dict (same shape
            `run_production_replay` returns + `audit_envelope`).
        Multi-tier call → `{"multi_tier": True, "tiers": [...],
            "summaries": {tier: summary}}` where each summary has
            its own audit_envelope.
    """
    # ── Resolve tier(s) to run ────────────────────────────────────
    if tier == "all":
        tiers_to_run: List[str] = list(SUPPORTED_TIERS)
    elif isinstance(tier, (list, tuple)):
        tiers_to_run = [str(t) for t in tier]
    else:
        tiers_to_run = [str(tier)]

    if mode == "historical" and snapshot_time is None:
        raise ValueError("snapshot_time required for mode=historical")
    if game_date is None and snapshot_time is not None:
        game_date = snapshot_time[:10]

    # Single-tier path — fast-path keeps existing return shape
    # byte-identical so existing callers (Phase B SH validation,
    # tests) are unaffected.
    if len(tiers_to_run) == 1:
        return await _run_one_tier(
            db, sport=sport, mode=mode,
            snapshot_time=snapshot_time,
            output_namespace=output_namespace,
            test_id=test_id, tier=tiers_to_run[0],
            game_date=game_date,
            canonical_path=canonical_path,
            dry_run=dry_run, notes=notes,
            allow_one_sided_for_accuracy_test=(
                allow_one_sided_for_accuracy_test
            ),
        )

    # ── Multi-tier path: provider + eligibility computed ONCE ───
    # The provider load + eligibility chain are tier-agnostic;
    # only the tier-spec evaluation differs. We run them once and
    # invoke `_run_one_tier_with_preloaded` per tier so we don't
    # hit the input source 3× for the same snapshot.
    provider = _resolve_input_provider(
        sport, mode,
        game_date=game_date, snapshot_time=snapshot_time,
    )
    writer = _resolve_output_writer(output_namespace)
    props = await provider.load_props(db)
    raw_count = len(props)

    if mode == "live":
        eligible_props = props
        coverage_stats: Dict[str, Any] = {
            "note": "skipped — adapter applied SSOT inside load_live_props"
        }
        pp_stats: Dict[str, Any] = {"note": "skipped — see coverage_stats"}
        after_priceable = raw_count
        registry_n = 0
    else:
        elig = apply_production_eligibility(
            props, sport=sport, run_id=test_id,
            use_pp_registry_fallback=(mode == "historical"),
        )
        eligible_props = elig.props
        coverage_stats = elig.coverage_stats
        pp_stats = elig.pp_playable_stats
        after_priceable = coverage_stats.get("total_props_remaining")
        registry_n = elig.pp_registry_fallback_applied

    allow_set = _build_allow_set(eligible_props)
    eligibility_predicate = _make_eligibility_predicate(allow_set)
    provider_describe = provider.describe_source()
    writer_describe = writer.describe()

    summaries: Dict[str, Dict[str, Any]] = {}
    for t in tiers_to_run:
        per_tier_test_id = f"{test_id}-{_tier_code(t)}"
        envelope = build_audit_envelope(
            test_id=per_tier_test_id,
            sport=sport, mode=mode,
            snapshot_time=snapshot_time or "",
            output_namespace=output_namespace,
            input_provider_describe=provider_describe,
            output_writer_describe=writer_describe,
            raw_row_count=raw_count,
            normalized_prop_count=raw_count,
            after_priceable_count=after_priceable,
            after_pp_playable_count=len(eligible_props),
            extra={
                "eligibility_allow_set_size": len(allow_set),
                "coverage_stats": coverage_stats,
                "pp_playable_stats": pp_stats,
                "pp_registry_fallback_stamped": registry_n,
                "multi_tier_parent_test_id": test_id,
            },
        )
        summary = await run_production_replay(
            db, sport=sport, game_date=game_date or "",
            snapshot_iso=snapshot_time, tier=t,
            canonical_path=canonical_path,
            output_namespace=writer.output_namespace,
            eligibility_predicate=eligibility_predicate,
            audit_envelope=envelope,
            serial_override=per_tier_test_id,
            dry_run=dry_run,
            notes=notes or f"Universal pipeline run {per_tier_test_id}",
            allow_one_sided_for_accuracy_test=(
                allow_one_sided_for_accuracy_test
            ),
        )
        envelope["stage_counts"]["cards_written"] = summary.get(
            "cards_displayed", 0
        )
        envelope["stage_counts"]["canonical_props"] = (
            (summary.get("canonical_summary") or {}).get(
                "canonical_props_built"
            )
            if summary.get("canonical_summary") else None
        )
        summary["audit_envelope"] = envelope
        summaries[t] = summary

    return {
        "multi_tier": True,
        "test_id_root": test_id,
        "tiers": tiers_to_run,
        "summaries": summaries,
    }


_TIER_CODE = {
    "safe_haven":  "SH",
    "front_lines": "FL",
    "war_zone":    "WZ",
}


def _tier_code(tier: str) -> str:
    return _TIER_CODE.get(tier, tier.upper()[:3])


async def _run_one_tier(
    db, *, sport, mode, snapshot_time, output_namespace, test_id,
    tier, game_date, canonical_path, dry_run, notes,
    allow_one_sided_for_accuracy_test: bool = False,
) -> Dict[str, Any]:
    """Pre-existing single-tier orchestrator (verbatim — keeps Phase
    B validation byte-identical). The new multi-tier path above
    duplicates the meat of this function inline so it can share
    the provider load + eligibility across tiers."""
    if mode == "historical" and snapshot_time is None:
        raise ValueError("snapshot_time required for mode=historical")
    if game_date is None and snapshot_time is not None:
        game_date = snapshot_time[:10]

    # ── Step 1: resolve provider + writer ─────────────────────────
    provider = _resolve_input_provider(
        sport, mode,
        game_date=game_date, snapshot_time=snapshot_time,
    )
    writer = _resolve_output_writer(output_namespace)

    # ── Step 2: load props from the provider ──────────────────────
    props = await provider.load_props(db)
    raw_count = len(props)
    logger.info(
        "[universal_pipeline][%s] loaded %d props from %s",
        test_id, raw_count, provider.name,
    )

    # ── Step 3: apply production eligibility ──────────────────────
    # Live mode: the LiveInputProvider already invoked the SSOT
    # inside `adapter.load_live_props`. Re-running here would
    # double-stamp coverage stats and re-filter PP. Skip.
    if mode == "live":
        eligible_props = props
        coverage_stats: Dict[str, Any] = {
            "note": "skipped — adapter applied SSOT inside load_live_props"
        }
        pp_stats: Dict[str, Any] = {"note": "skipped — see coverage_stats"}
        after_priceable = raw_count
        after_pp = raw_count
        registry_n = 0
    else:
        elig = apply_production_eligibility(
            props, sport=sport,
            run_id=test_id,
            use_pp_registry_fallback=(mode == "historical"),
        )
        eligible_props = elig.props
        coverage_stats = elig.coverage_stats
        pp_stats = elig.pp_playable_stats
        after_priceable = coverage_stats.get("total_props_remaining")
        after_pp = pp_stats.get("remaining")
        _ = after_pp  # `after_pp_playable_filter` count uses
                       # `len(eligible_props)` below (same value).
        registry_n = elig.pp_registry_fallback_applied

    logger.info(
        "[universal_pipeline][%s] eligibility: raw=%d → "
        "after_priceable=%s → after_pp_playable=%d "
        "(registry_fallback_stamped=%d)",
        test_id, raw_count, after_priceable, len(eligible_props),
        registry_n,
    )

    # ── Step 4: build the eligibility allow-set predicate ──────────
    allow_set = _build_allow_set(eligible_props)
    eligibility_predicate = _make_eligibility_predicate(allow_set)

    # ── Step 5: compose audit envelope ────────────────────────────
    envelope = build_audit_envelope(
        test_id=test_id,
        sport=sport,
        mode=mode,
        snapshot_time=snapshot_time or "",
        output_namespace=output_namespace,
        input_provider_describe=provider.describe_source(),
        output_writer_describe=writer.describe(),
        raw_row_count=raw_count,
        normalized_prop_count=raw_count,
        after_priceable_count=after_priceable,
        after_pp_playable_count=len(eligible_props),
        extra={
            "eligibility_allow_set_size": len(allow_set),
            "coverage_stats": coverage_stats,
            "pp_playable_stats": pp_stats,
            "pp_registry_fallback_stamped": registry_n,
        },
    )

    # ── Step 6: delegate to the production replay runner ──────────
    # The runner does: canonical collapse → universal odds bucket
    # routing → universal gate engine (tier_evaluator) → card
    # builder. Identical to live serving downstream of eligibility.
    # `output_namespace` flips writes to the test collections.
    # `serial_override=test_id` uses the directive's test-id format
    # verbatim.
    summary = await run_production_replay(
        db, sport=sport, game_date=game_date or "",
        snapshot_iso=snapshot_time,
        tier=tier,
        canonical_path=canonical_path,
        output_namespace=writer.output_namespace,
        eligibility_predicate=eligibility_predicate,
        audit_envelope=envelope,
        serial_override=test_id,
        dry_run=dry_run,
        notes=notes or f"Universal pipeline run {test_id}",
        allow_one_sided_for_accuracy_test=allow_one_sided_for_accuracy_test,
    )

    # Stamp final card_count back into the envelope for completeness.
    envelope["stage_counts"]["cards_written"] = summary.get(
        "cards_displayed", 0
    )
    envelope["stage_counts"]["canonical_props"] = (
        (summary.get("canonical_summary") or {}).get(
            "canonical_props_built"
        )
        if summary.get("canonical_summary") else None
    )
    summary["audit_envelope"] = envelope
    return summary


__all__ = ["run_pipeline"]
