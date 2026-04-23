"""Universal Gate Engine package (2026-04-22 Hard Consolidation follow-on).

Single gate evaluator for every sport, every tier, every stat family.

  schema.py     — normalized inputs / outputs / reason codes
  thresholds.py — sport → tier → stat_family → threshold config
  engine.py     — UniversalGateEngine.evaluate

Sport adapters are responsible only for producing `NormalizedMetrics`.
The engine does NOT know about sport-specific schemas; it reads
thresholds from config and evaluates each gate uniformly.

Adding a new sport = drop a config block in `thresholds.py` and a
metric-normalization method on the sport scoring adapter. No new
framework code.
"""

from .schema import (
    CANONICAL_GATE_TYPES,
    GateDetail,
    GateEvalResult,
    NormalizedMetrics,
    ReasonCode,
)
from .thresholds import (
    STAT_FAMILY_ALIASES,
    TIERS_ORDERED,
    THRESHOLDS,
    resolve_stat_family,
    resolve_thresholds,
)
from .engine import UniversalGateEngine, get_engine

__all__ = [
    "CANONICAL_GATE_TYPES",
    "GateDetail",
    "GateEvalResult",
    "NormalizedMetrics",
    "ReasonCode",
    "STAT_FAMILY_ALIASES",
    "TIERS_ORDERED",
    "THRESHOLDS",
    "UniversalGateEngine",
    "get_engine",
    "resolve_stat_family",
    "resolve_thresholds",
]
