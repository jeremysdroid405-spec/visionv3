"""Regression tests for GET /api/v3/admin/minutes-composition-stats."""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest
import requests


def _load_backend_url() -> str:
    env_val = os.environ.get("REACT_APP_BACKEND_URL")
    if env_val:
        return env_val.rstrip("/")
    # Fallback: read from the frontend .env so the test works whether
    # pytest is invoked standalone or through the combined harness.
    try:
        with open("/app/frontend/.env", "r") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return "http://localhost:8001"


def _load_admin_token() -> str | None:
    env_val = os.environ.get("ADMIN_DEBUG_TOKEN")
    if env_val:
        return env_val
    try:
        with open("/app/backend/.env", "r") as f:
            for line in f:
                if line.startswith("ADMIN_DEBUG_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


BACKEND_URL = _load_backend_url()
ADMIN_TOKEN = _load_admin_token()
ENDPOINT = f"{BACKEND_URL}/api/v3/admin/minutes-composition-stats"


@pytest.fixture
def headers() -> Dict[str, str]:
    if not ADMIN_TOKEN:
        pytest.skip("ADMIN_DEBUG_TOKEN not configured in env")
    return {"X-Admin-Token": ADMIN_TOKEN}


def test_requires_admin_token():
    resp = requests.get(ENDPOINT, params={"sport": "nba"}, timeout=10)
    assert resp.status_code == 401


def test_rejects_wrong_admin_token():
    resp = requests.get(
        ENDPOINT,
        params={"sport": "nba"},
        headers={"X-Admin-Token": "definitely-wrong"},
        timeout=10,
    )
    assert resp.status_code == 401


def test_returns_200_with_correct_token(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    assert resp.status_code == 200


def test_response_shape_has_all_required_sections(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    body: Dict[str, Any] = resp.json()
    assert body["sport"] == "nba"
    assert body["version_tag"] == "final-nba-rt"
    for section in ("global", "directional", "regime", "by_stat_family",
                    "top_positive_delta", "top_negative_delta"):
        assert section in body, f"missing section: {section}"


def test_global_section_has_required_fields(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    g = resp.json()["global"]
    for k in ("total_props", "composed_props_count", "composed_pct",
              "avg_projection_delta", "median_projection_delta",
              "max_positive_delta", "max_negative_delta"):
        assert k in g, f"missing global field: {k}"
    assert isinstance(g["total_props"], int)
    assert isinstance(g["composed_props_count"], int)
    assert g["composed_props_count"] <= g["total_props"]


def test_directional_section_has_required_fields(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    d = resp.json()["directional"]
    for k in ("count_upward_adjustments", "count_downward_adjustments",
              "avg_upward_delta", "avg_downward_delta"):
        assert k in d, f"missing directional field: {k}"
    assert isinstance(d["count_upward_adjustments"], int)
    assert isinstance(d["count_downward_adjustments"], int)


def test_regime_section_reports_bench_and_starter(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    r = resp.json()["regime"]
    for k in ("bench_count", "starter_count",
              "avg_delta_bench", "avg_delta_starters"):
        assert k in r, f"missing regime field: {k}"
    # Starters by construction have zero projection delta.
    if r["starter_count"] > 0:
        assert r["avg_delta_starters"] == 0.0


def test_by_stat_family_includes_pts_and_pra(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    by = resp.json()["by_stat_family"]
    assert "PTS" in by and "PRA" in by
    for stat in ("PTS", "PRA"):
        for k in ("composed_count", "avg_delta",
                  "upward_count", "downward_count"):
            assert k in by[stat], f"{stat} missing {k}"


def test_top_rows_capped_at_10(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    body = resp.json()
    assert len(body["top_positive_delta"]) <= 10
    assert len(body["top_negative_delta"]) <= 10


def test_top_rows_contain_required_fields(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    body = resp.json()
    for bucket in ("top_positive_delta", "top_negative_delta"):
        for row in body[bucket]:
            for k in ("player_name", "stat_type", "line", "side",
                      "baseline_projection", "composed_projection",
                      "delta", "predicted_minutes", "per_min_rate"):
                assert k in row, f"{bucket} row missing {k}"


def test_top_positive_ordering_is_descending(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    rows = resp.json()["top_positive_delta"]
    if len(rows) < 2:
        pytest.skip("not enough composed rows to check ordering")
    for a, b in zip(rows, rows[1:]):
        assert a["delta"] >= b["delta"]


def test_top_negative_ordering_is_ascending(headers):
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    rows = resp.json()["top_negative_delta"]
    if len(rows) < 2:
        pytest.skip("not enough composed rows to check ordering")
    for a, b in zip(rows, rows[1:]):
        assert a["delta"] <= b["delta"]


def test_stat_family_composed_counts_sum_to_global(headers):
    """Invariant: PTS composed + PRA composed == global composed."""
    resp = requests.get(ENDPOINT, params={"sport": "nba"},
                        headers=headers, timeout=10)
    body = resp.json()
    pts = body["by_stat_family"]["PTS"]["composed_count"]
    pra = body["by_stat_family"]["PRA"]["composed_count"]
    assert pts + pra == body["global"]["composed_props_count"]


def test_endpoint_is_read_only_idempotent(headers):
    """Calling twice back-to-back must return identical global counts.
    Guards against accidental recompute/mutation side effects."""
    r1 = requests.get(ENDPOINT, params={"sport": "nba"},
                      headers=headers, timeout=10).json()
    r2 = requests.get(ENDPOINT, params={"sport": "nba"},
                      headers=headers, timeout=10).json()
    assert r1["global"]["total_props"] == r2["global"]["total_props"]
    assert r1["global"]["composed_props_count"] == r2["global"]["composed_props_count"]


def test_mlb_returns_zero_composed(headers):
    """Composition is NBA-only; MLB should return zero-composed payload."""
    resp = requests.get(ENDPOINT, params={"sport": "mlb"},
                        headers=headers, timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sport"] == "mlb"
    assert body["version_tag"] == "final-mlb-rt"
    assert body["global"]["composed_props_count"] == 0
