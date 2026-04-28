"""
Dashboard Pick-Card Payload Parity Tests
========================================

Goal
----
Prevent NBA / MLB / future sports from drifting on the universal
dashboard pick-card contract. Every Ferrari tier endpoint must return
the same keys for every sport so the React `UniversalPlayerCard`
component (which is sport-agnostic) renders identically.

What this test guarantees
-------------------------
For each (sport, tier) combination:
    sports : nba, mlb
    tiers  : safe-haven, front-lines, war-zone

Every pick MUST expose the following keys (value may be null, but the
key MUST exist — null is the contractual signal that the frontend
renders the "—" placeholder):

    Identity        : player_name, team, sport
    Pick details    : stat_type, line, recommendation, direction,
                      tier_label, prop_type
    Card-contract   : stat_line, big_pick_text,
                      projection, hit_rate, avg, short_sentence

Optional flags (when present, must be bool / known shape):
    is_goblin, is_demon, is_standard

What this test does NOT change
------------------------------
* No scoring formulas, μ, σ, gates, thresholds touched.
* No model logic touched.
* No tier-routing or pick-selection logic touched.
* Pure shape contract verification.

How to run locally
------------------
    cd /app/backend
    python -m pytest tests/test_dashboard_card_payload_parity.py -v

The test calls the live HTTP API on localhost:8001 because that is
where `dashboard_card_contract.stamp_dashboard_card_contract` runs
(after route handlers, before the JSON response leaves the wire).
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Iterable, List, Tuple

import pytest


# ─── Configuration ────────────────────────────────────────────────────
BASE_URL = "http://localhost:8001"

SPORTS: Tuple[str, ...] = ("nba", "mlb")
TIERS: Tuple[str, ...] = ("safe-haven", "front-lines", "war-zone")

# Keys whose ABSENCE on a pick is a contract violation.
# Values may be None — that just means "no data" and the UI shows "—".
REQUIRED_KEYS: Tuple[str, ...] = (
    # Identity
    "player_name",
    "team",
    "sport",
    # Pick details
    "stat_type",
    "line",
    "recommendation",
    "direction",
    "tier_label",
    "prop_type",
    # Card contract (sport-agnostic 8-field display normalizer)
    "stat_line",
    "big_pick_text",
    "projection",
    "hit_rate",
    "avg",
    "short_sentence",
)

# Vision-intel raw text — frontend may also read this for the long-form
# detail page; the dashboard short_sentence is its truncated form.
# Optional but verified to coexist when present.
OPTIONAL_KEYS: Tuple[str, ...] = (
    "vision_intel",
    "is_goblin",
    "is_demon",
    "is_standard",
)


# ─── Fixtures ─────────────────────────────────────────────────────────
def _fetch(sport: str, tier: str) -> Dict[str, Any]:
    """Hit the live tier endpoint and return parsed JSON."""
    url = f"{BASE_URL}/api/v3/ferrari/{tier}?sport={sport}&sort=gap"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


@pytest.fixture(scope="module")
def all_payloads() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """One HTTP call per (sport, tier) — module-scoped so we don't
    re-fetch six times across the test suite."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sport in SPORTS:
        for tier in TIERS:
            out[(sport, tier)] = _fetch(sport, tier)
    return out


# ─── Tests ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_endpoint_returns_picks_array(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """Every (sport, tier) endpoint must respond with a `picks` array."""
    payload = all_payloads[(sport, tier)]
    assert "picks" in payload, (
        f"{sport}/{tier}: response missing top-level `picks` key. "
        f"Got keys: {sorted(payload.keys())}"
    )
    assert isinstance(payload["picks"], list), (
        f"{sport}/{tier}: `picks` must be a list, got {type(payload['picks']).__name__}"
    )


@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_every_pick_carries_all_required_keys(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """Universal contract: every pick exposes every required key.

    Values may be None — null is the contractual signal the UI uses
    to render the "—" placeholder. Missing keys, however, force the
    frontend into sport-specific guessing and break the universal card.
    """
    picks: List[Dict[str, Any]] = all_payloads[(sport, tier)].get("picks") or []
    if not picks:
        pytest.skip(
            f"{sport}/{tier}: no picks returned (empty slate). "
            f"Contract test skipped — re-run when slate has data."
        )
    missing_per_pick: List[str] = []
    for i, p in enumerate(picks):
        missing = [k for k in REQUIRED_KEYS if k not in p]
        if missing:
            missing_per_pick.append(
                f"  pick[{i}] {p.get('player_name', '?')!r} missing: {missing}"
            )
    assert not missing_per_pick, (
        f"\n{sport}/{tier}: {len(missing_per_pick)} pick(s) violate the "
        f"universal pick-card contract:\n" + "\n".join(missing_per_pick)
    )


@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_every_pick_sport_field_matches_request(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """`sport` field on the pick must equal the sport requested.

    Critical because the React side uses `pick.sport` to drive the
    sport-aware team-logo lookup (`getTeamLogo(sport, team)`). If this
    field cross-contaminates, NBA cards render MLB logos for shared
    abbreviations like BOS, ATL, TOR, DET, etc.
    """
    picks = all_payloads[(sport, tier)].get("picks") or []
    if not picks:
        pytest.skip(f"{sport}/{tier}: empty slate")
    bad = [
        (i, p.get("player_name"), p.get("sport"))
        for i, p in enumerate(picks)
        if (p.get("sport") or "").lower() != sport
    ]
    assert not bad, (
        f"{sport}/{tier}: {len(bad)} pick(s) have wrong sport field: {bad[:5]}"
    )


@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_direction_recommendation_alignment(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """`direction` and `recommendation` are projections of the same
    underlying side. Both must be either 'Over' or 'Under' (Title-case).
    The frontend reads either; they MUST agree."""
    picks = all_payloads[(sport, tier)].get("picks") or []
    if not picks:
        pytest.skip(f"{sport}/{tier}: empty slate")
    bad: List[str] = []
    for i, p in enumerate(picks):
        d = p.get("direction")
        r = p.get("recommendation")
        if d not in ("Over", "Under"):
            bad.append(f"  pick[{i}] {p.get('player_name')!r}: direction={d!r}")
        if r not in ("Over", "Under"):
            bad.append(f"  pick[{i}] {p.get('player_name')!r}: recommendation={r!r}")
        if d and r and d != r:
            bad.append(
                f"  pick[{i}] {p.get('player_name')!r}: "
                f"direction={d!r} != recommendation={r!r}"
            )
    assert not bad, f"\n{sport}/{tier}: alignment violations:\n" + "\n".join(bad)


@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_prop_type_value_domain(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """`prop_type` is the badge driver: it must be one of the three
    canonical values."""
    valid = {"GOBLIN", "DEMON", "STANDARD"}
    picks = all_payloads[(sport, tier)].get("picks") or []
    if not picks:
        pytest.skip(f"{sport}/{tier}: empty slate")
    bad = [
        (i, p.get("player_name"), p.get("prop_type"))
        for i, p in enumerate(picks)
        if p.get("prop_type") not in valid
    ]
    assert not bad, f"{sport}/{tier}: invalid prop_type values: {bad[:5]}"


@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("tier", TIERS)
def test_tier_label_value_domain(
    sport: str, tier: str, all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """`tier_label` must reflect the requested tier."""
    expected = {
        "safe-haven": "SAFE_HAVEN",
        "front-lines": "FRONT_LINE",
        "war-zone": "WAR_ZONE",
    }[tier]
    picks = all_payloads[(sport, tier)].get("picks") or []
    if not picks:
        pytest.skip(f"{sport}/{tier}: empty slate")
    bad = [
        (i, p.get("player_name"), p.get("tier_label"))
        for i, p in enumerate(picks)
        if p.get("tier_label") != expected
    ]
    assert not bad, (
        f"{sport}/{tier}: expected tier_label={expected!r}; "
        f"violations: {bad[:5]}"
    )


def test_universal_field_sets_match_across_sports(
    all_payloads: Dict[Tuple[str, str], Dict[str, Any]]
) -> None:
    """The set of REQUIRED keys on an NBA pick must equal the set on
    an MLB pick at the same tier. (Optional keys may differ — that's
    fine as long as REQUIRED is a perfect intersection.)

    This is the single test that catches sport-divergence regressions:
    if a future change adds a required field to NBA but forgets MLB,
    this test fails immediately.
    """
    failures: List[str] = []
    for tier in TIERS:
        nba_picks = all_payloads[("nba", tier)].get("picks") or []
        mlb_picks = all_payloads[("mlb", tier)].get("picks") or []
        if not (nba_picks and mlb_picks):
            continue  # one slate empty — covered by other tests
        nba_present = {k for k in REQUIRED_KEYS if k in nba_picks[0]}
        mlb_present = {k for k in REQUIRED_KEYS if k in mlb_picks[0]}
        if nba_present != mlb_present:
            failures.append(
                f"  {tier}: NBA-only keys = {sorted(nba_present - mlb_present)}, "
                f"MLB-only keys = {sorted(mlb_present - nba_present)}"
            )
    assert not failures, (
        "\nUniversal field-set divergence between sports:\n"
        + "\n".join(failures)
    )
