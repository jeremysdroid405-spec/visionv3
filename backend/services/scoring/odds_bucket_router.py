"""Universal odds-bucket tier router (SSOT).

Public interface for the cross-sport odds-tier routing layer that NBA
already uses in live serving. This module is a THIN FACADE over the
existing constants and resolver in
`services.scoring.gates.thresholds`. It does NOT introduce new
thresholds — it re-exports them and adds the small set of helpers the
master prompt requires:

  * `get_odds_bucket(american_odds)` — sport-agnostic tier name
  * `tier_allows_odds(tier, american_odds)` — boolean predicate
  * `explain_odds_bucket(american_odds)` — diagnostic string
  * `get_tier_odds_contract(sport=None)` — current contract as a dict

Design rules (per user 2026-05-17 directive):

  * NBA is the SOT for routing behaviour. We MUST NOT redefine
    boundaries here. All numeric constants come from
    `services.scoring.gates.thresholds` so any future change is
    made in exactly one place.
  * Sport-agnostic. The constants are universal (NBA = MLB = NFL);
    the optional `sport` parameter is accepted for backwards-compat
    and validation but is otherwise ignored. This matches the live
    `resolve_target_tier(sport, ref_odds)` contract.
  * No silent defaults. When `american_odds is None` the bucket is
    `None` and the caller is expected to fail loudly.

Behavioural parity with live serving is enforced by
`tests/scoring/test_odds_bucket_router.py`.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

# Single source of truth — re-export, never redefine.
from services.scoring.gates.thresholds import (
    UNIVERSAL_SAFE_HAVEN_MAX,
    UNIVERSAL_WAR_ZONE_MIN,
    ODDS_BUCKETS,
    resolve_target_tier,
)

# Sentinel reason code emitted when a prop's routed odds-tier
# does not match the tier it was evaluated against. Surfaced on
# the run output rows so audits can distinguish routing rejects
# from gate-stack rejects.
TIER_ODDS_BUCKET_FAIL: str = "tier_odds_bucket_fail"

VALID_TIERS = ("safe_haven", "front_lines", "war_zone")


def get_odds_bucket(american_odds: Optional[int]) -> Optional[str]:
    """Return the routed tier for an American-odds value.

    Identical contract to live `resolve_target_tier(sport, ref_odds)`:

        ref_odds <= UNIVERSAL_SAFE_HAVEN_MAX (-300)  → "safe_haven"
        ref_odds >= UNIVERSAL_WAR_ZONE_MIN  (+150)    → "war_zone"
        otherwise                                      → "front_lines"
        ref_odds is None                               → None

    `american_odds is None` returns `None` rather than defaulting —
    callers MUST treat a missing odds value as a hard failure.
    """
    if american_odds is None:
        return None
    return resolve_target_tier(None, int(american_odds))


def tier_allows_odds(tier: str, american_odds: Optional[int]) -> bool:
    """Boolean predicate: does `tier` accept this prop's odds?

    Returns False when `american_odds is None` (no silent allow).
    Returns False when `tier` is not one of the three canonical tiers.
    """
    if american_odds is None:
        return False
    if tier not in VALID_TIERS:
        return False
    return get_odds_bucket(american_odds) == tier


def explain_odds_bucket(american_odds: Optional[int]) -> str:
    """Diagnostic string explaining the routing decision."""
    if american_odds is None:
        return ("no_reference_odds: cannot route — caller must treat "
                "as fail-closed (no silent default)")
    o = int(american_odds)
    if o <= UNIVERSAL_SAFE_HAVEN_MAX:
        return (f"american_odds={o} <= UNIVERSAL_SAFE_HAVEN_MAX="
                f"{UNIVERSAL_SAFE_HAVEN_MAX} → safe_haven")
    if o >= UNIVERSAL_WAR_ZONE_MIN:
        return (f"american_odds={o} >= UNIVERSAL_WAR_ZONE_MIN="
                f"{UNIVERSAL_WAR_ZONE_MIN} → war_zone")
    return (f"american_odds={o} in "
            f"({UNIVERSAL_SAFE_HAVEN_MAX}, {UNIVERSAL_WAR_ZONE_MIN}) "
            f"→ front_lines")


def get_tier_odds_contract(sport: Optional[str] = None) -> Dict[str, Any]:
    """Return the current odds-tier contract as a structured dict.

    `sport` is accepted but ignored — boundaries are universal across
    NBA / MLB / NFL per the 2026-04-25 cutover. Passing a sport just
    surfaces that sport's `ODDS_BUCKETS` alias (which equals the
    universal constants in every entry).
    """
    sport_lc = (sport or "").lower() if sport else None
    sport_block = ODDS_BUCKETS.get(sport_lc) if sport_lc else None
    return {
        "universal_safe_haven_max": UNIVERSAL_SAFE_HAVEN_MAX,
        "universal_war_zone_min":   UNIVERSAL_WAR_ZONE_MIN,
        "boundary_rule": (
            f"safe_haven  : ref_odds <= {UNIVERSAL_SAFE_HAVEN_MAX}\n"
            f"front_lines : {UNIVERSAL_SAFE_HAVEN_MAX + 1} "
            f"<= ref_odds <= {UNIVERSAL_WAR_ZONE_MIN - 1}\n"
            f"war_zone    : ref_odds >= {UNIVERSAL_WAR_ZONE_MIN}\n"
            "ref_odds is None → unqualified (caller responsibility)"
        ),
        "sport_block": sport_block,
        "valid_tiers": list(VALID_TIERS),
        "fail_reason_code": TIER_ODDS_BUCKET_FAIL,
        "source_of_truth": "services.scoring.gates.thresholds",
    }


__all__ = [
    "UNIVERSAL_SAFE_HAVEN_MAX",
    "UNIVERSAL_WAR_ZONE_MIN",
    "TIER_ODDS_BUCKET_FAIL",
    "VALID_TIERS",
    "get_odds_bucket",
    "tier_allows_odds",
    "explain_odds_bucket",
    "get_tier_odds_contract",
]
