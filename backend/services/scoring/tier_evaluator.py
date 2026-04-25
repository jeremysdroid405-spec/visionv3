"""Single tier-evaluation entry point.

Both the first-pass tiering (`scoring_stack.compute_tier`) and the
post-vision re-evaluation (`recompute._reevaluate_tiers_post_vision`)
must apply the SAME post-engine overrides to a given prop.

The MLB Safe Haven goblin-line override was removed 2026-05 per the
"Remove MLB goblin-line override completely" directive. MLB gates now
use only the visible `_MLB_*` threshold tables in `gates/thresholds.py`
with no hidden line-based patching.

Sport-agnostic at the call site. No threshold tuning. No new gates.
"""
from __future__ import annotations

from services.scoring.gates import get_engine
from services.scoring.gates.schema import GateEvalResult
from services.scoring.gates import NormalizedMetrics


def evaluate_tier_with_overrides(metrics: NormalizedMetrics) -> GateEvalResult:
    """Single tier-evaluator. Runs the UniversalGateEngine and returns
    a canonical `GateEvalResult` that callers can serialize directly.
    """
    return get_engine().evaluate(metrics)


__all__ = ["evaluate_tier_with_overrides"]
