"""OneSidedPolicy dataclass + registry + decide() — Phase A-2.

The numbers in this file are RE-DERIVED FROM the live config:
  • `services/scoring/gates/thresholds.py`
  • `services/scoring/gates/engine.py`
  • `services/scoring/vision_v2.py`

Any change here without a corresponding change to those files (or
vice versa) MUST be caught by:
  tests/market_structure_policy/test_registry_mirrors_live_config.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple


POLICY_VERSION = "market_structure_policy_v1_phase_a2_2026_05_17"


# ── Elite-binary override (MLB SH `tp_source_gate.one_sided_override`) ──
@dataclass(frozen=True)
class EliteBinaryOverride:
    """Mirrors MLB SH `tp_source_gate.one_sided_override`.

    Source: `services/scoring/gates/thresholds.py:551-560` and
    `services/scoring/gates/engine.py:_passes_one_sided_safe_haven_override`.
    """
    allowed_stat_families: FrozenSet[str]
    hr_l20_min: float
    hr_l5_min:  float
    min_edge_pp: float       # edge_pct ≥ 5pp ≡ fair − implied ≥ 0.05
    cv_max: float


@dataclass(frozen=True)
class OneSidedPolicy:
    """Per-sport-per-tier one-sided handling spec.

    Behaviour contract today, restated:
      • If `reject_alt_one_sided` is True, every prop with
        `is_alternate_market=True AND tp_source="one_sided"` is
        rejected by the gate engine (NBA SH today via
        `market_structure_gate`).
      • If `reject_standard_one_sided` is True, every prop with
        `tp_source="one_sided"` is rejected UNLESS the
        `elite_binary_override` rescue path passes (MLB SH today via
        `tp_source_gate`).
      • `vision_confidence_multiplier` is the dial multiplied with
        the one-sided market-confidence component inside Vision v2
        (NBA today: 0.5; the value lives hardcoded in vision_v2.py).
        We re-expose it here for audit and the future unification.

    Notes about `reject_alt_one_sided` vs `reject_standard_one_sided`:
      • The two are independent flags. NBA today is
        (reject_alt=True, reject_standard=False) → only alt-line
        one-sided dies.
      • MLB SH today is (reject_alt=True, reject_standard=True with
        elite override) → ALL one-sided dies unless override fires.
        (Note: MLB SH's `tp_source_gate` is broader than NBA's
        `market_structure_gate` — it rejects regardless of `is_alt`.)
      • FL and WZ on both sports today have NEITHER gate → both
        flags False, no override.
    """
    sport: str
    tier: str
    reject_alt_one_sided: bool
    reject_standard_one_sided: bool
    elite_binary_override: Optional[EliteBinaryOverride]
    vision_confidence_multiplier: float
    # Audit metadata
    source_gate_in_config: str = ""       # "market_structure_gate" / "tp_source_gate" / ""
    notes: str = ""


# ── Outcome of a decide() call ─────────────────────────────────────
@dataclass(frozen=True)
class OneSidedDecision:
    """Returned by `decide_one_sided`.

    Mirrors the kinds of decisions the production engine records
    today, so a regression test can compare equality with the live
    `failed_gates` array.
    """
    passes_market_structure: bool   # True = NOT rejected by alt-side rule
    passes_tp_source:        bool   # True = NOT rejected by standard-side rule (or override saved it)
    via_elite_override:      bool   # True = passed tp_source ONLY because the rescue path fired
    audit_reason:            str    # see module docstring for stable strings


# ── Registry — exact mirror of TODAY's per-sport-per-tier behavior ──
# Source pins (re-check on any thresholds.py / engine.py change):
#   NBA SH:    services/scoring/gates/thresholds.py:137-139, 246-248
#   NBA FL/WZ: services/scoring/gates/thresholds.py:274-373
#   MLB SH:    services/scoring/gates/thresholds.py:549-561 (via _mlb_thresholds)
#   MLB FL:    services/scoring/gates/thresholds.py:532 (`if not front_lines`)
#   MLB WZ:    services/scoring/gates/thresholds.py:415-442 (full rewrite, no MS gate)
#   Vision:    services/scoring/vision_v2.py:226 (0.5 multiplier hardcoded)

_MLB_SH_ELITE_OVERRIDE = EliteBinaryOverride(
    allowed_stat_families=frozenset({
        "hits", "hits_runs_rbis", "runs", "rbis",
        "batter_strikeouts", "stolen_bases", "batter_walks",
    }),
    hr_l20_min=90.0,
    hr_l5_min=80.0,
    min_edge_pp=5.0,
    cv_max=0.70,
)


POLICY_REGISTRY: Dict[Tuple[str, str], OneSidedPolicy] = {
    # ── NBA ────────────────────────────────────────────────────────
    ("nba", "safe_haven"): OneSidedPolicy(
        sport="nba", tier="safe_haven",
        reject_alt_one_sided=True,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=0.5,
        source_gate_in_config="market_structure_gate",
        notes="reject_when={is_alt:True, tp_source:one_sided}",
    ),
    ("nba", "front_lines"): OneSidedPolicy(
        sport="nba", tier="front_lines",
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=0.5,
        source_gate_in_config="",
        notes="No market_structure_gate; one-sided allowed.",
    ),
    ("nba", "war_zone"): OneSidedPolicy(
        sport="nba", tier="war_zone",
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=0.5,
        source_gate_in_config="",
        notes="No market_structure_gate; one-sided allowed.",
    ),
    # ── MLB ────────────────────────────────────────────────────────
    ("mlb", "safe_haven"): OneSidedPolicy(
        sport="mlb", tier="safe_haven",
        # MLB SH today rejects ALL one_sided via `tp_source_gate`
        # `required_source="devig"` regardless of is_alt — the
        # rejection is NOT scoped to alts. So both flags are True.
        reject_alt_one_sided=True,
        reject_standard_one_sided=True,
        elite_binary_override=_MLB_SH_ELITE_OVERRIDE,
        # MLB live serving does NOT call vision_v2._market_confidence_component
        # today (live path uses v1 percentile). We expose 1.0 to make
        # the audit explicit: no penalty applied today on MLB.
        vision_confidence_multiplier=1.0,
        source_gate_in_config="tp_source_gate",
        notes=("required_source=devig; one_sided_override rescues "
                "elite binary props (HR_L20>=90 etc)."),
    ),
    ("mlb", "front_lines"): OneSidedPolicy(
        sport="mlb", tier="front_lines",
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=1.0,
        source_gate_in_config="",
        notes=("FL one_sided allowed (per thresholds.py:532 "
                "`if not front_lines`)."),
    ),
    ("mlb", "war_zone"): OneSidedPolicy(
        sport="mlb", tier="war_zone",
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=1.0,
        source_gate_in_config="",
        notes=("WZ 2026-05-16 rewrite explicitly removed "
                "market_structure_gate and tp_source_gate."),
    ),
    # ── NFL — scaffold only ────────────────────────────────────────
    ("nfl", "safe_haven"): OneSidedPolicy(
        sport="nfl", tier="safe_haven",
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=1.0,
        source_gate_in_config="",
        notes="NFL not live; placeholder.",
    ),
}


# ── Lookup + decision helpers ──────────────────────────────────────
def policy_for(sport: str, tier: str) -> OneSidedPolicy:
    """Registry lookup. Falls closed on unknown (sport, tier) — i.e.
    returns a permissive policy (no rejection, multiplier=1.0) so
    downstream consumers behave conservatively if a future tier is
    added without registry entry. This module is read-only today so
    'fail closed' has no behavior impact."""
    key = (sport.lower(), tier.lower())
    if key in POLICY_REGISTRY:
        return POLICY_REGISTRY[key]
    return OneSidedPolicy(
        sport=key[0], tier=key[1],
        reject_alt_one_sided=False,
        reject_standard_one_sided=False,
        elite_binary_override=None,
        vision_confidence_multiplier=1.0,
        source_gate_in_config="",
        notes="unknown_sport_tier_fallback",
    )


def _passes_elite_binary_override(
    metrics: Dict, override: EliteBinaryOverride,
) -> bool:
    """Mirror of `engine._passes_one_sided_safe_haven_override`.

    Inputs come from a `NormalizedMetrics`-shaped dict so the helper
    can be called from both production code (later) and from
    regression tests that pass live `nba_prop_scores` /
    `mlb_prop_scores` documents directly.

    Required dict keys: `stat_family`, `hit_rate_l20`, `hit_rate_l5`,
    `edge_pct`, `cv`. Missing values fail-closed (return False).
    """
    fam = metrics.get("stat_family")
    if fam not in override.allowed_stat_families:
        return False
    hr_l20 = metrics.get("hit_rate_l20")
    if hr_l20 is None or float(hr_l20) < override.hr_l20_min:
        return False
    hr_l5 = metrics.get("hit_rate_l5")
    if hr_l5 is None or float(hr_l5) < override.hr_l5_min:
        return False
    edge = metrics.get("edge_pct")
    if edge is None or float(edge) < override.min_edge_pp:
        return False
    cv = metrics.get("cv")
    if cv is None or float(cv) > override.cv_max:
        return False
    return True


def decide_one_sided(
    metrics: Dict, policy: OneSidedPolicy,
) -> OneSidedDecision:
    """Pure decision function. Returns the (would-be) gate verdict
    for a single prop, given the policy for its (sport, tier).

    Inputs (NormalizedMetrics-shaped dict):
      - tp_source              ("devig" | "one_sided" | None)
      - is_alt                 (bool, optional)
      - stat_family, hit_rate_l20, hit_rate_l5, edge_pct, cv
        (required ONLY when checking the elite override path)

    Output:
      OneSidedDecision with `audit_reason` from the stable string
      set defined in the module docstring.
    """
    tp_source = metrics.get("tp_source")
    if tp_source != "one_sided":
        return OneSidedDecision(
            passes_market_structure=True,
            passes_tp_source=True,
            via_elite_override=False,
            audit_reason="not_one_sided",
        )

    is_alt = bool(metrics.get("is_alt") or metrics.get("is_alternate_market"))

    # 1) market_structure_gate analog — NBA-style
    passes_ms = True
    if policy.reject_alt_one_sided and is_alt:
        passes_ms = False

    # 2) tp_source_gate analog — MLB-style
    passes_tps = True
    via_override = False
    if policy.reject_standard_one_sided:
        # Default reject; elite-binary override can rescue.
        if policy.elite_binary_override is not None and (
            _passes_elite_binary_override(
                metrics, policy.elite_binary_override
            )
        ):
            passes_tps = True
            via_override = True
        else:
            passes_tps = False

    # Audit reason resolution (priority: alt rejection > tp_source
    # rejection > elite override > pass)
    if not passes_ms:
        reason = "one_sided_alt_blocked_market_structure"
    elif not passes_tps:
        reason = "one_sided_standard_blocked_tp_source"
    elif via_override:
        reason = "one_sided_elite_binary_override"
    else:
        reason = "one_sided_pass_not_blocked"

    return OneSidedDecision(
        passes_market_structure=passes_ms,
        passes_tp_source=passes_tps,
        via_elite_override=via_override,
        audit_reason=reason,
    )


def should_reject_due_to_one_sided(
    metrics: Dict, *, sport: str, tier: str,
) -> bool:
    """Convenience boolean. True iff the policy would reject this
    prop on one-sided grounds (either gate would fire)."""
    policy = policy_for(sport, tier)
    dec = decide_one_sided(metrics, policy)
    return not (dec.passes_market_structure and dec.passes_tp_source)


__all__ = [
    "OneSidedPolicy", "OneSidedDecision", "EliteBinaryOverride",
    "POLICY_REGISTRY", "policy_for", "decide_one_sided",
    "should_reject_due_to_one_sided", "POLICY_VERSION",
]
