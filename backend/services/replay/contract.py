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


__all__ = [
    "REPLAY_RECOMPUTE_KWARGS",
    "COMPLIANT_REPLAY_ENGINES",
]
