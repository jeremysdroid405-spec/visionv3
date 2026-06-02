"""
Replay Infrastructure Contract — SSOT for the testing pipeline policy.
======================================================================

This module is the single source of truth for two cross-sport
invariants every replay engine MUST honor. Adding a new sport
adapter (NFL / NCAAF / WNBA / NHL / …) without complying with this
contract is a P0 bug.

────────────────────────────────────────────────────────────────────
INVARIANT 1 — ELIGIBILITY BYPASS
────────────────────────────────────────────────────────────────────

The replay/testing pipeline must NOT pre-filter rows by today's
production eligibility chain:

    score → persist → optimize          ✅ replay contract
    eligibility → score → optimize       ❌ violates contract

Specifically the chain `apply_production_eligibility` runs:
  - `filter_priceable`   (drops `book_count == 0` PP-only props)
  - `filter_pp_playable` (drops `playable_on_pp == False`)

Both are CORRECT for live serving (don't surface unplayable props
on the production board) but FATAL for historical replay — they
silently drop the majority of historical universe (in NBA, ~90%+
of props ship PP-only). The optimizer's job is to find BETTER
thresholds than today's production gates, so its input must contain
every scored prop with `coverage_class` / `tier` / `gate_pass` kept
as METADATA, never as a row drop.

  ✅ Every replay engine MUST score every prop it can.
  ✅ Tier / gate_pass / failed_gates / coverage_class are METADATA.
  ❌ No replay engine may pre-filter rows by today's gates.

Per-engine compliance audit:

  ┌──────────┬─────────────────────────┬──────────────────────────┐
  │ Sport    │ Replay engine           │ Eligibility-bypass path  │
  ├──────────┼─────────────────────────┼──────────────────────────┤
  │ MLB      │ mlb_replay_engine.py    │ ✅ Does NOT call         │
  │          │                         │ `recompute_sport`. Has   │
  │          │                         │ its own scoring path     │
  │          │                         │ (mu/sigma from feature   │
  │          │                         │ cache, no eligibility    │
  │          │                         │ filter applied).         │
  │ NBA      │ nba_replay_engine.py    │ ✅ Calls                 │
  │          │                         │ `recompute_sport(..,     │
  │          │                         │  bypass_eligibility=     │
  │          │                         │  True)`.                 │
  │ NFL      │ (future)                │ ⏳ When wired, MUST      │
  │          │                         │ pass `bypass_           │
  │          │                         │ eligibility=True`.       │
  │ NCAAF    │ (future)                │ ⏳ Same.                 │
  │ Teams    │ team_xgb_loader         │ ✅ Pure model inference, │
  │          │                         │ no eligibility filter.   │
  └──────────┴─────────────────────────┴──────────────────────────┘

────────────────────────────────────────────────────────────────────
INVARIANT 2 — RESEARCH MODE IS THE TESTING-PIPELINE DEFAULT
────────────────────────────────────────────────────────────────────

The Layer-4 runner (`production_replay_runner.run_production_replay`)
must be invoked with `research_mode=True` for every testing-pipeline
call site. This is enforced at the orchestrator level
(`scripts/sgo/historical_full_pipeline_replay.py`, which defaults
`--research-mode` to True since 2026-06-02). The runner itself
still accepts `research_mode=False` for live-serving callers, but
no replay/testing callsite may pass it.

────────────────────────────────────────────────────────────────────
USAGE
────────────────────────────────────────────────────────────────────

Replay engines that go through `services.scoring.recompute.
recompute_sport` (NBA today; future NFL/NCAAF/WNBA/NHL) MUST import
this contract and call:

    from services.replay.contract import REPLAY_RECOMPUTE_KWARGS
    ...
    await recompute_sport(
        db, sport=..., version_tag=..., props=...,
        **REPLAY_RECOMPUTE_KWARGS,
    )

The constant collapses (`dry_run=True`, `write_mode="upsert"`,
`bypass_eligibility=True`) into one immutable call shape so any
deviation is a code-review red flag.
"""
from __future__ import annotations
from types import MappingProxyType

# ── Recompute kwargs for the replay path. Immutable. ─────────────
# Read by:
#   - services/replay/nba_replay_engine.py
#   - (future) services/replay/nfl_replay_engine.py
#   - (future) services/replay/ncaaf_replay_engine.py
#
# The mapping is intentionally a MappingProxyType — any attempt to
# mutate it at runtime fails loudly, so a future caller cannot
# accidentally flip `bypass_eligibility=False` for replay.
REPLAY_RECOMPUTE_KWARGS = MappingProxyType({
    # Caller (the replay engine) captures `score_docs` from the
    # return value directly — production `*_prop_scores` is NEVER
    # mutated by a replay run.
    "dry_run": True,
    # Score docs returned in the result dict (required by the
    # replay engine's per-(book, side) fan-out).
    "write_mode": "upsert",
    # CONTRACT INVARIANT 1 — never drop rows by today's gates.
    "bypass_eligibility": True,
})


# ── Per-prop replay flags (stamped during reshape). ──────────────
# Replay engines that consume historical odds rows MUST stamp these
# on every prop dict they pass into `recompute_sport(props=...)`.
# Same contract MLB / NBA / NFL / NCAAF / future sports.
#
# `disable_availability_guard = True`:
#   Live production runs an availability heuristic on each player's
#   recent minutes trend to detect OUT / DNP / load-managed states.
#   In replay, the heuristic operates on the SAME log window we're
#   scoring against — leaving it on means the guard double-counts
#   the target game's own restriction signal. Replay turns it OFF
#   deterministically. Per-sport scoring adapters honor the flag
#   via an early-return in their availability-guard hook.
#
# `disable_live_only_features = True` (reserved):
#   For future use when individual live-only feature paths need
#   per-prop opt-out without a full bypass kwarg.
REPLAY_PROP_FLAGS = MappingProxyType({
    "disable_availability_guard": True,
})


# ── Sport adapter registration for compliance audit. ─────────────
# When a new replay engine for a sport is added, register the sport
# here. Lint test in `tests/test_replay_infrastructure_contract.py`
# verifies every registered sport's engine module declares its
# compliance via `REPLAY_CONTRACT_COMPLIANT = True`.
COMPLIANT_REPLAY_ENGINES: dict[str, str] = {
    # sport_code → replay engine module path (dotted)
    "mlb":   "services.replay.mlb_replay_engine",
    "nba":   "services.replay.nba_replay_engine",
    # Teams use a different scoring path (`team_xgb_loader`); they
    # bypass eligibility by construction (no eligibility filter in
    # the team scoring code). Registered here for compliance audit.
    "team":  "services.team_xgb_loader",
    # When NFL / NCAAF replay engines land, append them here AND
    # ensure they pass `**REPLAY_RECOMPUTE_KWARGS` (or otherwise
    # bypass the eligibility chain).
}


# ── Pipeline registry (locked 2026-06-02). ─────────────────────
# PropVision has EXACTLY two pipelines: PLAYER and TEAM. Each has a
# LIVE and a BACKTEST mode. Both modes share the same predictor and
# Vision pipeline; only the input collection and output sink change.
#
# See `/app/memory/ARCHITECTURE.md` for the full contract.
#
# Adding a new pipeline (e.g. parlay scoring, combo cards) is a
# design red flag — new surfaces MUST consume the output of one of
# these two pipelines, not introduce a third.
PIPELINE_REGISTRY = MappingProxyType({
    "player": MappingProxyType({
        "live": MappingProxyType({
            "input_collection_template":  "{sport}_live_props",
            "predictor_entry_point":      "services.scoring.recompute.recompute_sport",
            "output_collection_template": "{sport}_prop_scores",
            "surface":                    "ferrari player tier endpoints + /api/v3/player-with-badges/{name}",
        }),
        "backtest": MappingProxyType({
            "input_collection_template":  "{sport}_historical_props (SGO archive)",
            "predictor_entry_point":      "services.scoring.recompute.recompute_sport",
            "predictor_kwargs":           "REPLAY_RECOMPUTE_KWARGS",
            "output_sink":                "optimizer dataset (mirror_player_replay_to_unified.py)",
            "engines":                    ("services.replay.nba_replay_engine",
                                            "services.replay.mlb_replay_engine"),
        }),
    }),
    "team": MappingProxyType({
        "live": MappingProxyType({
            "input_collection":           "team_live_props",
            "predictor_entry_point":      "services.team_live_xgb_scorer.score_team_live_props",
            "output_collection":          "team_prop_scores",
            "surface":                    "ferrari team tier endpoints + /api/v3/team-with-badges/{team_id}",
        }),
        "backtest": MappingProxyType({
            "input_script":               "scripts/sgo/reshape_team_props_to_replay.py",
            "predictor_entry_point":      "services.team_xgb_loader.score_team_props_batch",
            "output_sink":                "optimizer dataset (TODO: mirror_team_replay_to_unified.py)",
            "status":                     "P1 — orchestrator + mirror script pending",
        }),
    }),
})


# Convenience: list of every pipeline-mode pair for audit/iteration.
PIPELINE_MODES: tuple[tuple[str, str], ...] = (
    ("player", "live"),
    ("player", "backtest"),
    ("team",   "live"),
    ("team",   "backtest"),
)


__all__ = [
    "REPLAY_RECOMPUTE_KWARGS",
    "REPLAY_PROP_FLAGS",
    "COMPLIANT_REPLAY_ENGINES",
    "PIPELINE_REGISTRY",
    "PIPELINE_MODES",
]
