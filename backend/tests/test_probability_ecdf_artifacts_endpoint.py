"""Smoke tests for GET /api/v3/admin/probability/ecdf/artifacts
(2026-04-24). Hits the running backend through REACT_APP_BACKEND_URL."""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest
import requests


def _backend_url() -> str:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _token() -> str | None:
    try:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("ADMIN_DEBUG_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("ADMIN_DEBUG_TOKEN")


BACKEND = _backend_url()
TOKEN = _token()
ENDPOINT = f"{BACKEND}/api/v3/admin/probability/ecdf/artifacts"


@pytest.fixture
def headers() -> Dict[str, str]:
    if not TOKEN:
        pytest.skip("ADMIN_DEBUG_TOKEN not configured")
    return {"X-Admin-Token": TOKEN}


def test_returns_200(headers):
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"


def test_requires_token():
    r = requests.get(ENDPOINT, timeout=10)
    assert r.status_code in (401, 503)


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
    for key in ("root_path", "version_expected", "total_artifacts",
                "totals_by_sport", "missing_expected_nba", "artifacts",
                "notes"):
        assert key in body, f"missing top-level key: {key}"
    assert body["version_expected"] == "UNIVERSAL_ECDF_v1"
    assert isinstance(body["artifacts"], list)
    assert isinstance(body["totals_by_sport"], dict)
    assert isinstance(body["missing_expected_nba"], list)


def test_nba_artifacts_are_present(headers):
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    body = r.json()
    nba_artifacts = [a for a in body["artifacts"] if a["sport"] == "nba"]
    nba_families = {a["stat_family"] for a in nba_artifacts
                    if a["loaded_available"]}
    assert {"pts", "reb", "ast", "3pm", "pra"}.issubset(nba_families), (
        f"expected all 5 NBA stat families; got {nba_families}"
    )
    assert body["missing_expected_nba"] == []
    # Each NBA row must carry the required metadata
    for a in nba_artifacts:
        for key in ("sport", "stat_family", "version",
                    "source_model_version", "sample_count", "min_bucket_n",
                    "bucket_count", "trained_at", "artifact_path",
                    "loaded_available"):
            assert key in a, f"missing field {key} on NBA artifact {a}"
        if a["loaded_available"]:
            assert a["version"] == "UNIVERSAL_ECDF_v1"
            assert a["sample_count"] > 0
            assert a["min_bucket_n"] > 0
            assert a["bucket_count"] == 10


def test_scaffold_sports_do_not_crash(headers):
    """MLB / NFL directories contain only README.md today. They must
    not appear as artifacts but must also not cause any error."""
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    body = r.json()
    # Confirm the artifact rows don't include README.md-derived entries
    for a in body["artifacts"]:
        assert not a["stat_family"].lower().endswith(".md")
    # Totals-by-sport may include mlb/nfl with 0 or may omit them —
    # both are acceptable as long as the endpoint returned 200.


def test_totals_by_sport_sums_match_artifacts(headers):
    r = requests.get(ENDPOINT, headers=headers, timeout=10)
    body = r.json()
    summed = sum(body["totals_by_sport"].values())
    loaded_count = sum(
        1 for a in body["artifacts"] if a["loaded_available"]
    )
    assert summed == loaded_count
    assert body["total_artifacts"] == len(body["artifacts"])
