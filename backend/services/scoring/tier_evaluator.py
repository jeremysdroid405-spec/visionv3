"""Single tier-evaluation entry point.

Both the first-pass tiering (`scoring_stack.compute_tier`) and the
post-vision re-evaluation (`recompute._reevaluate_tiers_post_vision`)
must apply the SAME post-engine overrides to a given prop.

The MLB Safe Haven goblin-line override was removed 2026-05 per the
"Remove MLB goblin-line override completely" directive. MLB gates now
use only the visible `_MLB_*` threshold tables in `gates/thresholds.py`
with no hidden line-based patching.

Sport-agnostic at the call site. No threshold tuning. No new gates.

2026-05-17 — Phase 2a (Production Replay Harness):
Added optional `feature_provider` keyword. The function does not read
features (it operates on a pre-built `NormalizedMetrics`), so this
parameter is currently a wire-only seam — accepted at the boundary so
historical-mode callers can pass an `IFeatureProvider` through future
inner refactors (Phase 2b) without changing this function's signature
again. When `feature_provider=None` (default) behavior is identical
to the pre-2a function — verified byte-identical via smoke test.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from services.scoring.gates import get_engine
from services.scoring.gates.schema import GateEvalResult
from services.scoring.gates import NormalizedMetrics

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from services.replay.providers.base import IFeatureProvider


def evaluate_tier_with_overrides(
    metrics: NormalizedMetrics,
    *,
    feature_provider: "Optional[IFeatureProvider]" = None,
) -> GateEvalResult:
    """Single tier-evaluator. Runs the UniversalGateEngine and returns
    a canonical `GateEvalResult` that callers can serialize directly.

    Args:
        metrics: Pre-built NormalizedMetrics for the prop.
        feature_provider: Optional provider for as-of-date feature
            lookups. Accepted but unused at this layer — reserved for
            Phase 2b inner refactor. Passing a provider here has NO
            effect on the gate decision; this function evaluates only
            the metrics already on the record.
    """
    # NOTE: `feature_provider` intentionally unused at this layer.
    # The function operates on `metrics` only. Live-path callers pass
    # `feature_provider=None` (default) and get pre-2a behavior.
    del feature_provider  # explicit silencing of unused-arg linters
    return get_engine().evaluate(metrics)


__all__ = ["evaluate_tier_with_overrides"]
