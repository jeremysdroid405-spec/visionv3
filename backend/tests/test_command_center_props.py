"""Regression + universality tests for the universal Command Center
prop source.

These tests exercise the live mongo/HTTP stack — they are *integration*
tests by design. The Command Center contract requires:

    1. NBA player rows come from `nba_prop_scores[final-nba-rt]`.
    2. MLB player rows come from `mlb_prop_scores[final-mlb-rt]`.
    3. Per-line `hit_rate_l10` varies (no smearing across alt lines).
    4. Response carries canonical fields only — no `h5_rate`,
       `h10_rate`, `hit_rate`, `hit_rates`.
    5. Sport validation rejects unknown sports.
    6. canonical_key lookup returns exactly one row.
"""
from __future__ import annotations

import os
import asyncio

import httpx
import pytest


BASE_URL = os.environ.get("COMMAND_TEST_BASE_URL") or "http://localhost:8001"

LEGACY_KEYS = ("h5_rate", "h10_rate", "hit_rate", "hit_rates")


def _get(path: str) -> httpx.Response:
    with httpx.Client(timeout=30.0) as c:
        return c.get(f"{BASE_URL}{path}")


@pytest.mark.asyncio
async def test_nba_alt_lines_have_varying_hit_rate_l10():
    """Brunson AST has 20+ alt lines with varying per-line L10. No
    smearing — the original Command Center bug."""
    r = _get("/api/command/props?sport=nba&player_name=Jalen%20Brunson")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["sport"] == "nba"
    assert body["version_tag"] == "final-nba-rt"

    ast = [p for p in body["props"] if p.get("stat_type") == "AST"]
    # Multiple alt lines must be present.
    assert len(ast) >= 5, f"expected ≥5 AST alt rows, got {len(ast)}"
    l10s = {p["hit_rate_l10"] for p in ast if p.get("hit_rate_l10") is not None}
    assert len(l10s) >= 3, (
        f"expected ≥3 distinct L10 hit rates across alt lines, got {l10s}"
    )


@pytest.mark.asyncio
async def test_no_legacy_aliases_anywhere_in_response():
    r = _get("/api/command/props?sport=nba&player_name=Jalen%20Brunson")
    assert r.status_code == 200
    for prop in r.json()["props"]:
        for k in LEGACY_KEYS:
            assert k not in prop, f"prop carried legacy alias {k}: {prop}"


@pytest.mark.asyncio
async def test_canonical_field_set_present():
    r = _get("/api/command/props?sport=nba&player_name=Jalen%20Brunson")
    assert r.status_code == 200
    p = r.json()["props"][0]
    required = {
        "canonical_key", "sport", "player_name", "stat_type", "line",
        "recommendation", "direction",
        "hit_rate_l5", "hit_rate_l10", "hit_rate_l20",
        "hit_rate_over", "hit_rate_under",
        "p_true_active", "edge_vs_fair", "vision_score", "cv",
        "team", "opponent", "tier", "tier_reason",
        "pp_odds", "dk_odds", "fd_odds", "bol_odds", "mgm_odds",
        "tier_reference_book", "tier_reference_odds",
    }
    missing = required - set(p.keys())
    assert not missing, f"canonical fields missing from response: {missing}"


@pytest.mark.asyncio
async def test_mlb_player_returns_canonical_rows():
    r = _get("/api/command/props?sport=mlb&player_name=Willson%20Contreras")
    assert r.status_code == 200
    body = r.json()
    assert body["sport"] == "mlb"
    assert body["version_tag"] == "final-mlb-rt"
    assert len(body["props"]) > 0
    # Every row carries canonical canonical_key prefix.
    for p in body["props"]:
        assert isinstance(p.get("canonical_key"), str) and p["canonical_key"].startswith("mlb|")


@pytest.mark.asyncio
async def test_invalid_sport_rejected_400():
    r = _get("/api/command/props?sport=xfl&player_name=anyone")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_missing_both_params_rejected_400():
    r = _get("/api/command/props?sport=nba")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unknown_player_404():
    r = _get("/api/command/props?sport=nba&player_name=Definitely%20Not%20A%20Player")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_canonical_key_lookup_returns_single_row():
    # First fetch any one canonical_key off Brunson, then re-query by it.
    r = _get("/api/command/props?sport=nba&player_name=Jalen%20Brunson")
    assert r.status_code == 200
    rows = r.json()["props"]
    assert rows
    target_ck = rows[0]["canonical_key"]
    r2 = _get(f"/api/command/props?sport=nba&canonical_key={target_ck}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["props"]) == 1
    assert body2["props"][0]["canonical_key"] == target_ck
