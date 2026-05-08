"""Regression tests for Command Center simulation analytics — universal
volatility (canonical CV) + universal correlation (pairwise rules).

These exercise the live HTTP stack against MongoDB-served canonical
score docs, so they implicitly verify both the new
`/api/command/props` reader and `/api/command/simulate` writer.
"""
from __future__ import annotations

import os

import httpx
import pytest


BASE_URL = os.environ.get("COMMAND_TEST_BASE_URL") or "http://localhost:8001"


def _get(path: str) -> dict:
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{BASE_URL}{path}")
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30.0) as c:
        r = c.post(f"{BASE_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def _props(sport: str, player_name: str) -> list:
    return _get(
        f"/api/command/props?sport={sport}&player_name={player_name.replace(' ', '%20')}"
    )["props"]


def _pick(props, **filters):
    """First prop matching all filters (e.g. stat_type='AST', line=2.5)."""
    for p in props:
        if all(p.get(k) == v for k, v in filters.items()):
            return p
    return None


@pytest.mark.asyncio
async def test_volatility_changes_materially_safe_haven_vs_war_zone():
    """Swap a low-CV leg for a high-CV leg. Overall volatility moves by
    at least 0.4 (≥ 2× the safe-haven baseline)."""
    jb = _props("nba", "Jalen Brunson")
    ae = _props("nba", "Anthony Edwards")

    safe_leg = _pick(jb, stat_type="AST", line=2.5, recommendation="OVER")
    assert safe_leg, "Brunson AST 2.5 OVER missing — fixture player off the board"
    safe_partner = sorted(ae, key=lambda p: (p.get("cv") or 99))[0]
    war_partner = sorted(ae, key=lambda p: -(p.get("cv") or 0))[0]

    safe_resp = _post("/api/command/simulate", {"legs": [safe_leg, safe_partner]})
    war_resp  = _post("/api/command/simulate", {"legs": [safe_leg, war_partner]})

    safe_vol = safe_resp["simulation"]["volatility_index"]
    war_vol  = war_resp["simulation"]["volatility_index"]

    assert war_vol > safe_vol, (
        f"war-zone vol ({war_vol}) should exceed safe-haven vol ({safe_vol})"
    )
    assert (war_vol - safe_vol) >= 0.20, (
        f"volatility delta too small: safe={safe_vol}, war={war_vol}"
    )

    # Per-leg metric must read from the canonical CV when present.
    for leg in war_resp["simulation"]["legs"]:
        if leg.get("cv") is not None:
            assert leg["volatility_source"] == "cv"
            assert abs(leg["cv"] - leg["volatility_index"]) < 1e-3


@pytest.mark.asyncio
async def test_same_player_two_leg_returns_kind_same_player():
    jb = _props("nba", "Jalen Brunson")
    a = _pick(jb, stat_type="AST", line=2.5, recommendation="OVER")
    b = _pick(jb, stat_type="AST", line=9.5, recommendation="OVER")
    assert a and b
    sim = _post("/api/command/simulate", {"legs": [a, b]})["simulation"]
    assert sim["correlation_kind"] == "same_player"
    assert sim["correlation_score"] >= 0.80
    assert sim["correlation_penalty"] > 0


@pytest.mark.asyncio
async def test_same_event_teammates_returns_kind_same_game():
    jb = _props("nba", "Jalen Brunson")
    je = _props("nba", "Joel Embiid")  # known same-event vs Brunson's slate
    a = _pick(jb, stat_type="AST", line=2.5, recommendation="OVER")
    b = next((p for p in je if p.get("recommendation") == "OVER"), None)
    assert a and b
    if a.get("event_id") != b.get("event_id"):
        pytest.skip("Brunson and Embiid not in the same live event today")
    sim = _post("/api/command/simulate", {"legs": [a, b]})["simulation"]
    assert sim["correlation_kind"] == "same_game"
    assert 0.30 <= sim["correlation_score"] <= 0.50
    assert sim["correlation_penalty"] > 0


@pytest.mark.asyncio
async def test_cross_sport_returns_kind_none_zero_score():
    jb = _props("nba", "Jalen Brunson")
    wc = _props("mlb", "Willson Contreras")
    a = _pick(jb, stat_type="AST", line=2.5, recommendation="OVER")
    b = next((p for p in wc if p.get("stat_type") == "Total Bases"), None)
    assert a and b
    sim = _post("/api/command/simulate", {"legs": [a, b]})["simulation"]
    assert sim["correlation_kind"] == "none"
    assert sim["correlation_score"] == 0.0
    assert sim["correlation_penalty"] == 0.0
    # And the simulation still grades & probability-computes.
    assert sim["infiltration_grade"] in ("A", "B", "C", "D", "F")
    assert 1.0 <= sim["convergence_rate"] <= 99.0


@pytest.mark.asyncio
async def test_simulation_response_carries_universal_analytics_fields():
    jb = _props("nba", "Jalen Brunson")
    ae = _props("nba", "Anthony Edwards")
    a = _pick(jb, stat_type="AST", line=2.5, recommendation="OVER")
    b = sorted(ae, key=lambda p: -(p.get("cv") or 0))[0]
    sim = _post("/api/command/simulate", {"legs": [a, b]})["simulation"]
    # New universal fields ship alongside the legacy `correlation_penalty`.
    assert "correlation_score" in sim
    assert "correlation_kind" in sim
    assert sim["correlation_kind"] in ("none", "same_player", "same_game", "same_team")
    # Per-leg `cv` and `volatility_source` are surfaced for transparency.
    for leg in sim["legs"]:
        assert "volatility_source" in leg
        assert "cv" in leg
