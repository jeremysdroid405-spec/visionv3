"""Smoke tests for GET /api/v3/admin/calibration-stats (2026-04-23).

Mirrors the pattern used by `test_minutes_composition_stats_endpoint.py` —
hits the running backend through `REACT_APP_BACKEND_URL` so the route
is exercised end-to-end (ingress + router wiring + auth helper + db).
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest
import requests


def _load_backend_url() -> str:
    env_val = os.environ.get("REACT_APP_BACKEND_URL")
    if env_val:
        return env_val.rstrip("/")
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
ENDPOINT = f"{BACKEND_URL}/api/v3/admin/calibration-stats"


@pytest.fixture
def headers() -> Dict[str, str]:
    if not ADMIN_TOKEN:
        pytest.skip("ADMIN_DEBUG_TOKEN not configured in env")
    return {"X-Admin-Token": ADMIN_TOKEN}


def test_returns_200_with_valid_token(headers):
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"


def test_requires_token():
    r = requests.get(ENDPOINT, timeout=10)
    # 401 when ADMIN_DEBUG_TOKEN is set, 503 when unset. Both are acceptable
    # "denied without credentials" responses.
    assert r.status_code in (401, 503), f"{r.status_code} {r.text[:300]}"


def test_rejects_wrong_token():
    r = requests.get(
        ENDPOINT, headers={"X-Admin-Token": "definitely-wrong"}, timeout=10,
    )
    assert r.status_code == 401


def test_response_shape(headers):
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    assert r.status_code == 200
    body: Any = r.json()
    assert isinstance(body, dict)

    # Top-level keys
    for key in ("sport", "version_tag", "totals",
                "intercept_delta_summary", "p_over_delta_summary",
                "by_stat_family", "top_probability_corrections",
                "top_edge_changes", "flags", "notes"):
        assert key in body, f"missing top-level key: {key}"

    # Totals — all the counters + percentages the spec asked for
    totals = body["totals"]
    for key in ("total_scored_docs",
                "projection_intercept_applied_count",
                "probability_calibration_applied_count",
                "projection_intercept_applied_pct",
                "probability_calibration_applied_pct",
                "probability_method_counts"):
        assert key in totals, f"missing totals.{key}"
    # Numeric for scalar keys; dict for method counts
    for key in ("total_scored_docs",
                "projection_intercept_applied_count",
                "probability_calibration_applied_count",
                "projection_intercept_applied_pct",
                "probability_calibration_applied_pct"):
        assert isinstance(totals[key], (int, float)), (
            f"totals.{key} not numeric"
        )
    assert isinstance(totals["probability_method_counts"], dict)
    for m in ("gaussian", "isotonic", "ecdf"):
        assert m in totals["probability_method_counts"], (
            f"missing totals.probability_method_counts.{m}"
        )

    # Per-stat breakdown contains every canonical NBA stat and the
    # four summary fields the spec called out.
    for st in ("PTS", "REB", "AST", "3PM", "PRA"):
        assert st in body["by_stat_family"], f"missing stat: {st}"
        sub = body["by_stat_family"][st]
        for k in ("intercept_applied_count",
                  "prob_calibration_applied_count",
                  "avg_intercept_delta", "avg_p_over_delta"):
            assert k in sub, f"missing by_stat_family.{st}.{k}"

    # Delta summaries
    for section in ("intercept_delta_summary", "p_over_delta_summary"):
        for k in ("count", "avg", "median", "min", "max"):
            assert k in body[section], f"missing {section}.{k}"

    # Top-N limits
    assert len(body["top_probability_corrections"]) <= 20
    assert len(body["top_edge_changes"]) <= 20

    # Flags block
    for flag in ("VK2_CALIBRATION_ENABLED",
                 "VK2_PROB_CALIBRATION_ENABLED",
                 "VK2_PROB_CALIBRATION_STATS",
                 "VK2_ECDF_PROBABILITY_ENABLED",
                 "VK2_ECDF_PROBABILITY_STATS"):
        assert flag in body["flags"], f"missing flag: {flag}"
        assert "raw" in body["flags"][flag]
        assert "effective" in body["flags"][flag]

    # Notes present and non-empty
    assert isinstance(body["notes"], list) and body["notes"]


def test_sport_query_param(headers):
    # Non-NBA sport should not 500; calibration is NBA-only so
    # zero-count aggregates are expected.
    r = requests.get(
        ENDPOINT, headers=headers, params={"sport": "mlb"}, timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sport"] == "mlb"
    # totals must still be present and numeric
    assert isinstance(
        body["totals"]["projection_intercept_applied_count"], int
    )
