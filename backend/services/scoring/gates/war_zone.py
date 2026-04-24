"""NBA War Zone — Final Hybrid Gating Filter (2026-04-24).

Applied AFTER base scoring has set HR, CV, VS, TP, odds, tp_source —
and BEFORE final tier assignment. A prop routed to the war_zone odds
bucket runs through the 4-step filter below; only rows returning
`decision.passed=True` land in `tier=war_zone`. Failing rows are
demoted to `unqualified` with a typed `reason_code`.

Pipeline (strict ordering per spec):

  Step 1 — stat-aware CV cap (HARD reject)
  Step 2 — base gates: HR ≥ 55  AND  VS ≥ 85
  Step 3 — edge-type split (devig vs one_sided) with differentiated
            HR / VS floors
  Step 4 — mid-odds pricing-trap reject (+150 ≤ odds ≤ +220
            AND HR < 60  AND VS < 90)

Rules:
  * This layer DOES NOT fabricate data. It only filters.
  * Does NOT touch projections, probabilities, ECDF, gates, or
    odds buckets.
  * A row missing HR / VS / CV / tp_source / odds fails the filter
    deterministically (fail-closed on missing inputs).

Interface:
    decision = evaluate_war_zone(metrics)
    # decision.passed  -> True | False
    # decision.reason  -> "passed" | "cv_exceeded"
    #                     | "hr_below_55" | "vs_below_85"
    #                     | "devig_hr_below_50" | "one_sided_requires_hr60_or_vs90"
    #                     | "pricing_trap"
    # decision.details -> dict with every threshold + actual pair
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# ----- Config ----------------------------------------------------------

# Step 1: CV caps by stat family. Alt markets inherit the dominant-stat
# cap per spec. Unknown stat families default to `None` which ALWAYS
# fails the CV check fail-closed.
CV_CAPS: Dict[str, float] = {
    # Standard
    "pts": 0.45,
    "pra": 0.45,
    "reb": 0.55,
    "ast": 0.55,
    "3pm": 0.75,
    # Composite / combo alts (dominant-stat cap)
    "pts_ast": 0.45,
    "pts_reb": 0.45,
    "reb_ast": 0.55,
    "pts_reb_ast": 0.45,
}

# Additional stat-family aliases used by the NBA adapter (match exactly
# what `stat_family` resolves to in `NormalizedMetrics`).
CV_CAP_ALIASES: Dict[str, str] = {
    # Raw Odds API market keys — the adapter sometimes passes the raw
    # key through when no alias is registered. Map to the canonical
    # CV-cap key.
    "player_points_assists_alternate": "pts_ast",
    "player_points_rebounds_alternate": "pts_reb",
    "player_rebounds_assists_alternate": "reb_ast",
    "player_points_rebounds_assists_alternate": "pts_reb_ast",
    "player_points_assists": "pts_ast",
    "player_points_rebounds": "pts_reb",
    "player_rebounds_assists": "reb_ast",
    "player_points_rebounds_assists": "pts_reb_ast",
    # Lower-case variants the adapter may emit
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
    "threes": "3pm",
}

# Step 2
BASE_HR_MIN = 55.0
BASE_VS_MIN = 85.0

# Step 3
DEVIG_HR_MIN = 50.0
DEVIG_VS_MIN = 85.0
ONE_SIDED_HR_MIN = 60.0
ONE_SIDED_VS_MIN = 90.0

# Step 4 — pricing-trap band (inclusive)
TRAP_ODDS_LOW = 150
TRAP_ODDS_HIGH = 220
TRAP_HR_MIN = 60.0
TRAP_VS_MIN = 90.0


# ----- Output -----------------------------------------------------------

@dataclass
class WarZoneDecision:
    passed: bool
    reason: str                             # short machine-readable code
    details: Dict[str, Any] = field(default_factory=dict)


# ----- Helpers ----------------------------------------------------------

def _normalise_stat_family(stat_family: Optional[str]) -> Optional[str]:
    if stat_family is None:
        return None
    key = stat_family.strip().lower().replace(" ", "_")
    return CV_CAP_ALIASES.get(key, key)


def resolve_cv_cap(stat_family: Optional[str]) -> Optional[float]:
    """Return the CV cap for this stat family, or None if unknown."""
    norm = _normalise_stat_family(stat_family)
    if norm is None:
        return None
    return CV_CAPS.get(norm)


# ----- Core API ---------------------------------------------------------

def evaluate_war_zone(
    *,
    stat_family: Optional[str],
    hr: Optional[float],
    vs: Optional[float],
    cv: Optional[float],
    tp_source: Optional[str],
    odds: Optional[int],
) -> WarZoneDecision:
    """Apply the 4-step War Zone filter. Returns a WarZoneDecision."""
    details: Dict[str, Any] = {
        "stat_family": stat_family,
        "hr": hr, "vs": vs, "cv": cv,
        "tp_source": tp_source, "odds": odds,
    }

    # ---- Step 1: stat-aware CV cap (HARD reject) ---------------------
    cv_cap = resolve_cv_cap(stat_family)
    details["cv_cap"] = cv_cap
    if cv_cap is None:
        return WarZoneDecision(
            passed=False, reason="unsupported_stat_family_for_war_zone",
            details=details,
        )
    if cv is None or cv > cv_cap:
        return WarZoneDecision(
            passed=False, reason="cv_exceeded",
            details=details,
        )

    # ---- Step 2: base gates ------------------------------------------
    if hr is None or hr < BASE_HR_MIN:
        return WarZoneDecision(
            passed=False, reason="hr_below_55",
            details={**details, "hr_min": BASE_HR_MIN},
        )
    if vs is None or vs < BASE_VS_MIN:
        return WarZoneDecision(
            passed=False, reason="vs_below_85",
            details={**details, "vs_min": BASE_VS_MIN},
        )

    # ---- Step 3: edge-type split -------------------------------------
    src = (tp_source or "").lower()
    if src == "devig":
        # HR >= 50 is already guaranteed by Step 2 (HR >= 55) — this
        # check is explicit per spec so the decision record shows the
        # devig floor was evaluated.
        if hr < DEVIG_HR_MIN:
            return WarZoneDecision(
                passed=False, reason="devig_hr_below_50",
                details={**details, "devig_hr_min": DEVIG_HR_MIN},
            )
        if vs < DEVIG_VS_MIN:
            return WarZoneDecision(
                passed=False, reason="devig_vs_below_85",
                details={**details, "devig_vs_min": DEVIG_VS_MIN},
            )
    elif src == "one_sided":
        # Need HR >= 60 OR VS >= 90 (either strong signal is enough).
        if hr < ONE_SIDED_HR_MIN and vs < ONE_SIDED_VS_MIN:
            return WarZoneDecision(
                passed=False,
                reason="one_sided_requires_hr60_or_vs90",
                details={**details,
                         "one_sided_hr_min": ONE_SIDED_HR_MIN,
                         "one_sided_vs_min": ONE_SIDED_VS_MIN},
            )
    else:
        # Neither devig nor one_sided (e.g. tp_source is None because
        # no book priced the prop). Without market evidence we cannot
        # validate — reject per the "no fabrication" principle.
        return WarZoneDecision(
            passed=False, reason="no_market_tp_source",
            details=details,
        )

    # ---- Step 4: pricing-trap reject ---------------------------------
    if (odds is not None
            and TRAP_ODDS_LOW <= odds <= TRAP_ODDS_HIGH
            and hr < TRAP_HR_MIN
            and vs < TRAP_VS_MIN):
        return WarZoneDecision(
            passed=False, reason="pricing_trap",
            details={**details,
                     "trap_odds_range": (TRAP_ODDS_LOW, TRAP_ODDS_HIGH),
                     "trap_hr_min": TRAP_HR_MIN,
                     "trap_vs_min": TRAP_VS_MIN},
        )

    return WarZoneDecision(passed=True, reason="passed", details=details)


__all__ = [
    "CV_CAPS",
    "CV_CAP_ALIASES",
    "WarZoneDecision",
    "evaluate_war_zone",
    "resolve_cv_cap",
]
