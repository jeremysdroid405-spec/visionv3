"""
Phase 1.A.0 skeleton tests — pure, no-DB, no-API.

Pins the architectural invariants from
`/app/memory/TEAM_PROPS_ARCHITECTURE.md` §§4.3, 11, 12, 14.5 so the
forward-compat surface doesn't drift before subsequent Phase 1.A
slices land.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")


# ─────────────────────────────────────────────────────────────────
# Test 1 — Shared book policy: BLOCKED_BOOKS and REFERENCE_ONLY_BOOKS
# referenced by the team namespace MUST be the SAME Python object
# as the authoring location. (§14.5 identity invariant)
# ─────────────────────────────────────────────────────────────────
def test_blocked_and_reference_only_books_are_same_object_as_canonical() -> None:
    from services import team_policy
    from scripts.sgo import reshape_sgo_to_replay_odds as rsr

    assert team_policy.BLOCKED_BOOKS is rsr.BLOCKED_BOOKS, (
        "BLOCKED_BOOKS must be the SAME object on both code paths. "
        "Phase 2 optimizer integration relies on this identity — see "
        "§14.5 of the architecture doc."
    )
    assert team_policy.REFERENCE_ONLY_BOOKS is rsr.REFERENCE_ONLY_BOOKS, (
        "REFERENCE_ONLY_BOOKS must be the SAME object on both code "
        "paths. See §14.5."
    )
    # And the id() check explicitly called out in §14.5
    assert id(team_policy.BLOCKED_BOOKS) == id(rsr.BLOCKED_BOOKS)
    assert id(team_policy.REFERENCE_ONLY_BOOKS) == id(rsr.REFERENCE_ONLY_BOOKS)


# ─────────────────────────────────────────────────────────────────
# Test 2 — TeamProjectionAdapter ABC contract: returns the locked
# `TeamProjection` shape with a supported distribution. (§2.3 + §11)
# ─────────────────────────────────────────────────────────────────
def test_team_projection_adapter_abc_contract() -> None:
    from services.team_projections import (
        SUPPORTED_DISTRIBUTIONS,
        TeamProjection,
        TeamProjectionAdapter,
    )

    # Cannot instantiate without implementing project()
    with pytest.raises(TypeError):
        TeamProjectionAdapter()  # type: ignore[abstract]

    # Supported distributions are the four locked in §2.3
    assert SUPPORTED_DISTRIBUTIONS == frozenset(
        {"normal", "poisson", "nbinom", "mixture"}
    )

    # A valid projection (Normal w/ sigma) constructs cleanly
    p = TeamProjection(distribution="normal", mu=4.5, sigma=2.1,
                       dispersion_k=None, model_version="test_v1",
                       confidence_metric=0.7)
    assert p.distribution == "normal"
    assert p.mu == 4.5

    # Normal without sigma raises
    with pytest.raises(ValueError, match="sigma"):
        TeamProjection(distribution="normal", mu=4.5, sigma=None,
                       dispersion_k=None, model_version="v1",
                       confidence_metric=0.5)

    # NegBinom without dispersion_k raises
    with pytest.raises(ValueError, match="dispersion_k"):
        TeamProjection(distribution="nbinom", mu=4.5, sigma=None,
                       dispersion_k=None, model_version="v1",
                       confidence_metric=0.5)

    # Unsupported distribution raises
    with pytest.raises(ValueError, match="SUPPORTED_DISTRIBUTIONS"):
        TeamProjection(distribution="exponential", mu=1.0, sigma=None,
                       dispersion_k=None, model_version="v1",
                       confidence_metric=0.5)

    # confidence_metric out of [0,1] raises
    with pytest.raises(ValueError, match="confidence_metric"):
        TeamProjection(distribution="poisson", mu=1.0, sigma=None,
                       dispersion_k=None, model_version="v1",
                       confidence_metric=1.5)


# ─────────────────────────────────────────────────────────────────
# Test 3 — TeamTPAdapter ABC + TeamTPResult invariants. (§11.9)
# Hard invariants on probability scale and tp_source provenance —
# echoes the player-side TP-scale lesson learned 2026-06-02.
# ─────────────────────────────────────────────────────────────────
def test_team_tp_adapter_abc_and_result_invariants() -> None:
    from services.team_tp import TP_SOURCES, TeamTPAdapter, TeamTPResult

    assert TP_SOURCES == frozenset({"model", "blend", "market"})

    with pytest.raises(TypeError):
        TeamTPAdapter()  # type: ignore[abstract]

    # Valid result constructs
    r = TeamTPResult(tp=0.55, tp_source="blend",
                     model_probability=0.6, fair_probability=0.5,
                     implied_probability=0.5, edge=0.05,
                     n_books_for_devig=5, n_reference_only_skipped=0,
                     alpha_used=0.5)
    assert r.tp == 0.55

    # tp out of [0,1] (the player-side percent-scale bug we just fixed)
    with pytest.raises(ValueError, match="scale-mix"):
        TeamTPResult(tp=55.0, tp_source="blend",
                     model_probability=0.5, fair_probability=0.5,
                     implied_probability=0.5, edge=0.0,
                     n_books_for_devig=3, n_reference_only_skipped=0,
                     alpha_used=0.5)

    # Unknown tp_source rejected
    with pytest.raises(ValueError, match="tp_source"):
        TeamTPResult(tp=0.5, tp_source="vibes",
                     model_probability=0.5, fair_probability=0.5,
                     implied_probability=0.5, edge=0.0,
                     n_books_for_devig=3, n_reference_only_skipped=0,
                     alpha_used=0.5)

    # alpha outside [0.2, 0.8] rejected (§11.9 bound)
    with pytest.raises(ValueError, match=r"\[0\.2, 0\.8\]"):
        TeamTPResult(tp=0.5, tp_source="blend",
                     model_probability=0.5, fair_probability=0.5,
                     implied_probability=0.5, edge=0.0,
                     n_books_for_devig=3, n_reference_only_skipped=0,
                     alpha_used=0.95)


# ─────────────────────────────────────────────────────────────────
# Test 4 — GateAdapter ABC + tier names are the canonical three.
# (§4.3 hybrid scoring — shared CONTRACT, separate IMPLEMENTATION)
# ─────────────────────────────────────────────────────────────────
def test_gate_adapter_abc_and_tier_names() -> None:
    from services.team_scoring.gates import (
        TIER_NAMES,
        GateAdapter,
        GateDecision,
    )

    assert TIER_NAMES == ("safe_haven", "front_lines", "war_zone")

    with pytest.raises(TypeError):
        GateAdapter()  # type: ignore[abstract]

    d = GateDecision(safe_haven_pass=True, front_lines_pass=False,
                     war_zone_pass=False, selected_tier="safe_haven",
                     failed_gates=[])
    assert d.selected_tier == "safe_haven"

    with pytest.raises(ValueError, match="selected_tier"):
        GateDecision(safe_haven_pass=False, front_lines_pass=False,
                     war_zone_pass=False, selected_tier="fast_lane",
                     failed_gates=["edge_too_low"])

    # None selected_tier (no tier passed) is valid
    d2 = GateDecision(safe_haven_pass=False, front_lines_pass=False,
                      war_zone_pass=False, selected_tier=None,
                      failed_gates=["edge_too_low", "vision_score_too_low"])
    assert d2.selected_tier is None


# ─────────────────────────────────────────────────────────────────
# Test 5 — Master Hub seed JSON: structural validation.
# (§1.2 collection schema + §12.7 checklist precondition)
# ─────────────────────────────────────────────────────────────────
SEED_PATH = Path("/app/backend/data/team_master_hub_seed.json")


def test_master_hub_seed_structural_validation() -> None:
    assert SEED_PATH.exists(), f"missing seed file: {SEED_PATH}"
    with SEED_PATH.open() as f:
        doc = json.load(f)

    # Top-level required keys
    for k in ("seed_version", "teams", "counts", "frozen_at"):
        assert k in doc, f"seed missing required top-level key: {k}"

    # Counts match expected league sizes
    assert doc["counts"] == {"mlb": 30, "nba": 30, "nfl": 32, "total": 92}, (
        f"unexpected counts: {doc['counts']}"
    )

    # Per-team schema compliance
    valid_sports = {"mlb", "nba", "nfl"}
    valid_leagues = {"MLB", "NBA", "NFL"}
    seen_team_ids: set[str] = set()
    for t in doc["teams"]:
        for required in ("team_id", "sport", "league_id", "external_ids",
                          "display_names", "colors", "division", "conference"):
            assert required in t, f"team missing field {required}: {t}"
        assert t["sport"] in valid_sports, f"bad sport: {t['sport']}"
        assert t["league_id"] in valid_leagues, f"bad league_id: {t['league_id']}"
        # team_id must start with the sport prefix
        assert t["team_id"].startswith(t["sport"] + "_"), (
            f"team_id {t['team_id']!r} must start with '{t['sport']}_'"
        )
        # Uniqueness
        assert t["team_id"] not in seen_team_ids, (
            f"duplicate team_id: {t['team_id']}"
        )
        seen_team_ids.add(t["team_id"])
        # external_ids carries the 4 expected provider keys
        ext = t["external_ids"]
        for prov in ("sgo", "oddsapi", "espn", "statsapi"):
            assert prov in ext, f"external_ids missing provider: {prov}"
        # display_names carries the 4 expected variants
        names = t["display_names"]
        for nk in ("full", "short", "abbrev", "market"):
            assert nk in names and names[nk], (
                f"display_names missing or empty: {nk} for {t['team_id']}"
            )

    # Sorted by team_id (deterministic order)
    team_ids = [t["team_id"] for t in doc["teams"]]
    assert team_ids == sorted(team_ids), (
        "teams must be sorted by team_id for byte-stable seed"
    )
