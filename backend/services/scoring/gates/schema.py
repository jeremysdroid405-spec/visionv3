"""Universal Gate Engine — normalized I/O schema + reason codes.

Sport-agnostic. Every sport scoring adapter converts raw props into a
`NormalizedMetrics` record; the engine returns a `GateEvalResult`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------
# Canonical gate type registry
# --------------------------------------------------------------------------
# These are the ONLY gate types known to the engine. A threshold dict may
# activate any subset of them; unknown keys are ignored.
CANONICAL_GATE_TYPES = (
    "coverage_gate",         # book_count >= min_books
    "hit_rate_gate",         # hit_rate_l20 (or configured window) >= min pp
    "tp_gate",               # multi-book de-vig TP >= min pp
    "cv_gate",               # cv <= max (or stat-family-keyed `caps` map)
    "margin_gate",           # avg_hit_margin >= min (binary 0.5-line replacement for cv_gate, MLB)
    "edge_gate",             # edge_pct >= min
    "ceiling_gate",          # ceiling_rate >= min pp
    "context_gate",          # no blowout / injury / lineup veto
    "vision_score_gate",     # vision_score >= min (optional per-tp_source floors)
    "market_trap_gate",      # reject mid-odds weak signals (pricing trap)
    "market_structure_gate", # reject when all listed structural conditions match
)


# --------------------------------------------------------------------------
# Reason codes — shared across sports
# --------------------------------------------------------------------------
class ReasonCode:
    """Canonical reason codes for gate outcomes. Sport-agnostic."""

    # Success
    GATES_PASSED = "gates_passed"

    # Pre-gate vetoes
    NO_REFERENCE_MARKET = "no_reference_market"
    MODEL_CONFIDENCE_LOW = "gate_model_confidence_fail"
    ANCHOR_CONTRADICTED = "gate_anchor_contradicted"

    # Gate-specific failures
    COVERAGE_FAIL = "gate_coverage_fail"
    HIT_RATE_FAIL = "gate_hit_rate_fail"
    TP_FAIL = "gate_tp_fail"
    TP_UNAVAILABLE = "gate_tp_unavailable"
    CV_FAIL = "gate_cv_fail"
    MARGIN_FAIL = "gate_margin_fail"
    EDGE_FAIL = "gate_edge_fail"
    CEILING_FAIL = "gate_ceiling_fail"
    CONTEXT_FAIL = "gate_context_fail"
    VISION_SCORE_FAIL = "gate_vision_score_fail"
    MARKET_TRAP_FAIL = "gate_market_trap_fail"
    MARKET_STRUCTURE_FAIL = "gate_market_structure_fail"

    _PER_GATE_FAIL: Dict[str, str] = {
        "coverage_gate": COVERAGE_FAIL,
        "hit_rate_gate": HIT_RATE_FAIL,
        "tp_gate": TP_FAIL,
        "cv_gate": CV_FAIL,
        "margin_gate": MARGIN_FAIL,
        "edge_gate": EDGE_FAIL,
        "ceiling_gate": CEILING_FAIL,
        "context_gate": CONTEXT_FAIL,
        "vision_score_gate": VISION_SCORE_FAIL,
        "market_trap_gate": MARKET_TRAP_FAIL,
        "market_structure_gate": MARKET_STRUCTURE_FAIL,
    }

    @classmethod
    def for_gate(cls, gate_type: str) -> str:
        return cls._PER_GATE_FAIL.get(gate_type, f"gate_{gate_type}_fail")


# --------------------------------------------------------------------------
# Input — NormalizedMetrics
# --------------------------------------------------------------------------
@dataclass
class NormalizedMetrics:
    """Every sport adapter emits this exact record.

    Fields absent from the adapter (e.g. NBA has no ``ceiling_rate_l15``)
    are left None — gates that require them will fail with a specific
    reason code rather than silently pass.

    Side-awareness (OVER vs UNDER) is handled by the adapter — the
    engine evaluates already-resolved metric values.
    """

    # Identity
    sport: str
    tier: str           # one of "safe_haven" | "front_lines" | "war_zone" (target tier)
    stat_family: str    # normalized stat family (e.g. "pts", "total_bases")
    side: str = "OVER"  # "OVER" | "UNDER"

    # Reference book — drives the odds-bucket routing OUTSIDE the engine
    reference_book: Optional[str] = None
    reference_odds: Optional[int] = None

    # Universal market-coverage metric
    book_count: Optional[int] = None  # number of standard sportsbooks quoting this prop

    # Probability signals (0 – 1 scale for p_*, 0 – 100 for tp/rate metrics)
    tp: Optional[float] = None                 # multi-book de-vig true prob, 0-100 pp
    hit_rate: Optional[float] = None           # default window hit-rate (0-100)
    hit_rate_l20: Optional[float] = None
    hit_rate_l10: Optional[float] = None
    hit_rate_l5: Optional[float] = None
    # HR sample size telemetry (2026-04-25, HR v3). Number of valid
    # game logs the picked-side hit_rate was computed over. Gate
    # engine reads this on every hit_rate_gate evaluation:
    #   n < 10            → INSUFFICIENT_SAMPLE (fail)
    #   10 <= n < 20      → small-sample penalty (require hr >= hr_min + 5)
    #   n >= 20 (or None) → standard hr >= hr_min
    # NBA leaves this None (always 20 by construction); MLB sets it.
    hit_rate_sample_size: Optional[int] = None
    ceiling_rate: Optional[float] = None       # war-zone upside rate (0-100)

    # Volatility / edge
    cv: Optional[float] = None                 # L20 coefficient of variation (unitless)
    edge_pct: Optional[float] = None           # model edge vs market (pp)

    # Line + 0.5-line margin metrics (2026-05, MLB only). For binary
    # 0.5 props the engine swaps `cv_gate` → `margin_gate`. NBA leaves
    # all three fields None — its threshold tables don't reference
    # margins so behaviour is unchanged.
    line: Optional[float] = None
    avg_hit_margin: Optional[float] = None     # mean(value - line) over hit games
    avg_miss_margin: Optional[float] = None    # mean(line - value) over miss games

    # Vision score — slate-percentile model-confidence signal (0-100).
    # Populated AFTER per-prop scoring in the slate-level pass (see
    # `recompute.py::_apply_vision_score_normalization`). Used by
    # `vision_score_gate` and `market_trap_gate`.
    vision_score: Optional[float] = None

    # TP source flag — "devig" | "one_sided" | None. Lets gates apply
    # differentiated floors to rigorous-de-vig vs single-side-implied
    # market anchors.
    tp_source: Optional[str] = None

    # Alternate-market flag — True iff the raw stat_type is an
    # alternate line (e.g. `player_points_alternate`). Set by the
    # adapter; `market_structure_gate` reads this to discriminate
    # standard vs alt markets in structural reject rules.
    is_alt: Optional[bool] = None

    # Model-confidence floor (side-aware: for UNDER picks the adapter
    # pipes p_model_pct through here so tp_gate can use it directly).
    p_model_pct: Optional[float] = None

    # Context
    blowout_risk: Optional[bool] = None
    lineup_confirmed: Optional[bool] = None
    injury_flag: Optional[bool] = None
    context_vetoes: List[str] = field(default_factory=list)

    # Raw escape hatch for adapter-specific audit fields. Not consumed
    # by the engine — round-tripped for diagnostics only.
    extras: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Output — GateEvalResult + GateDetail
# --------------------------------------------------------------------------
@dataclass
class GateDetail:
    """Per-gate evaluation record — uniform across sports."""

    gate_type: str                    # e.g. "hit_rate_gate"
    threshold: Any                    # numeric or structured threshold used
    actual: Any                       # value evaluated against threshold
    passed: bool
    comparator: str                   # ">=" | "<=" | "!=" | "custom"
    reason_code: Optional[str] = None # populated on failure
    note: Optional[str] = None        # diagnostic only (e.g. "CV cap override for REB")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_type": self.gate_type,
            "threshold": self.threshold,
            "actual": self.actual,
            "passed": self.passed,
            "comparator": self.comparator,
            "reason_code": self.reason_code,
            "note": self.note,
        }


@dataclass
class GateEvalResult:
    """Engine output. Uniform across sports."""

    sport: str
    tier: str
    stat_family: str

    gate_summary: str              # "PASS" | "FAIL"
    passed: bool
    reason_code: str               # primary reason code

    passed_gates: List[str] = field(default_factory=list)
    failed_gates: List[str] = field(default_factory=list)
    gate_details: Dict[str, GateDetail] = field(default_factory=dict)

    # Echo back identity so downstream can explain a pick anywhere.
    reference_book: Optional[str] = None
    reference_odds: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sport": self.sport,
            "tier": self.tier,
            "stat_family": self.stat_family,
            "gate_summary": self.gate_summary,
            "passed": self.passed,
            "reason_code": self.reason_code,
            "passed_gates": list(self.passed_gates),
            "failed_gates": list(self.failed_gates),
            "gate_details": {k: v.to_dict() for k, v in self.gate_details.items()},
            "reference_book": self.reference_book,
            "reference_odds": self.reference_odds,
        }
