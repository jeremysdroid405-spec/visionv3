"""Universal Market Structure Policy — Phase A-2 (2026-05-17).

PASSIVE SSOT module. Centralizes the per-sport-per-tier policy for
**one-sided** prop handling (the structural case where no book
quotes both sides of a market and `tp_source="one_sided"` results).

Important contract:
  • This module is read-only with respect to current production
    behavior in this first cut. It documents, exposes, and
    machine-verifies the existing per-sport policy.
  • The actual enforcement still lives in:
        - `services/scoring/gates/thresholds.py`
            (NBA `market_structure_gate` cfg + MLB `tp_source_gate`
             cfg with `one_sided_override`)
        - `services/scoring/gates/engine.py`
            (`_eval_market_structure`, `_eval_tp_source`,
             `_passes_one_sided_safe_haven_override`)
        - `services/scoring/tp_engine.py`
            (one-sided TP fallback formula — already universal)
        - `services/scoring/vision_v2.py`
            (one-sided market-confidence multiplier — currently
             a hardcoded 0.5 inside `_market_confidence_component`)
        - `services/scoring/best_book.py`
            (source-tag emission for `*_edge_source` fields —
             already universal)

  • Phase A-2 ships ONLY this module + regression tests proving the
    module's `decide(...)` function returns the SAME gate-level
    decision that production currently records on score docs. No
    code path is rewired to consume this module yet — that's a
    follow-on phase (clearly enumerated at the bottom of this
    docstring).

Public API:
  • `OneSidedPolicy` — dataclass holding all per-sport-per-tier
    one-sided decisions.
  • `POLICY_REGISTRY` — `{(sport, tier): OneSidedPolicy}` map,
    populated to match TODAY's behavior exactly.
  • `policy_for(sport, tier)` — registry lookup helper.
  • `decide_one_sided(metrics, policy)` — pure function returning
    `OneSidedDecision` (rejected / passed / via_override) along
    with an audit reason.
  • Convenience `should_reject_due_to_one_sided(metrics, sport, tier)`
    for callers that only need the bool.

Audit reason strings (stable, used by regression tests):
  • "one_sided_pass_not_blocked"        — policy allows one-sided
  • "one_sided_alt_blocked_market_structure"
                                        — NBA alt+one_sided rejection
  • "one_sided_standard_blocked_tp_source"
                                        — MLB tp_source_gate rejection
                                          when override didn't fire
  • "one_sided_elite_binary_override"   — MLB override rescue path

Future unification (NOT in Phase A-2 scope):
  Wire `gates/engine.py::_eval_market_structure` and
  `_eval_tp_source` to consult this module instead of the per-tier
  cfg dict. Wire `vision_v2._market_confidence_component` to read
  `policy.vision_confidence_multiplier`. Both refactors require an
  exact byte-identical regression sweep across a 30-day historical
  cohort BEFORE flipping the consumer side.
"""

from services.scoring.market_structure_policy.policy import (
    OneSidedPolicy,
    OneSidedDecision,
    EliteBinaryOverride,
    POLICY_REGISTRY,
    policy_for,
    decide_one_sided,
    should_reject_due_to_one_sided,
    POLICY_VERSION,
)

__all__ = [
    "OneSidedPolicy",
    "OneSidedDecision",
    "EliteBinaryOverride",
    "POLICY_REGISTRY",
    "policy_for",
    "decide_one_sided",
    "should_reject_due_to_one_sided",
    "POLICY_VERSION",
]
