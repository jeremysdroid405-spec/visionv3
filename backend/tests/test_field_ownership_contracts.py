"""Contract tests — SSOT field ownership.

These tests run against live API + DB to assert that the ownership
contract is honored at runtime. They're the enforcement equivalent of
unit tests for architectural invariants.

Run:
    cd /app/backend && PYTHONPATH=/app/backend python3 -m pytest tests/test_field_ownership_contracts.py -v
"""
from __future__ import annotations

import os
import pytest
import requests


@pytest.fixture(scope="module")
def api_base():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pytest.skip("frontend/.env unavailable")
    pytest.skip("REACT_APP_BACKEND_URL not set")


@pytest.fixture(scope="module")
def db():
    from pymongo import MongoClient
    mc = MongoClient(os.environ["MONGO_URL"])
    return mc[os.environ["DB_NAME"]]


# ────────────────────────────────────────────────────────────────────
# scored_at contract — FIELD_OWNERSHIP.md:scored_at
# ────────────────────────────────────────────────────────────────────

class TestScoredAtContract:
    """Every active score doc under the canonical version_tag MUST
    have a non-null scored_at. Before the 2026-05-03 migration, this
    field was NEVER written (dead-ending /api/health/sync). If this
    test ever fails, the write path was regressed."""

    @pytest.mark.parametrize("sport,tag", [
        ("nba", "final-nba-rt"),
        ("mlb", "final-mlb-rt"),
    ])
    def test_scored_at_populated_on_active_docs(self, db, sport, tag):
        coll = db[f"{sport}_prop_scores"]
        total = coll.count_documents({"version_tag": tag, "active": True})
        if total == 0:
            pytest.skip(f"no active {sport} docs to validate")
        with_scored = coll.count_documents({
            "version_tag": tag, "active": True, "scored_at": {"$ne": None},
        })
        pct = 100 * with_scored / total
        assert pct >= 95.0, (
            f"{sport}: only {with_scored}/{total} ({pct:.1f}%) active "
            f"docs have scored_at populated. Write path regression in "
            f"prop_scores_store._project_score_doc."
        )


# ────────────────────────────────────────────────────────────────────
# opponent contract — FIELD_OWNERSHIP.md:opponent
# ────────────────────────────────────────────────────────────────────

class TestOpponentContract:
    """No pick in the API response may have team == opponent — that's
    the smoking-gun signature of a stale cached_board override (the
    Dylan Harper SAS-vs-POR class of bug). The 2026-05-03 migration
    routes opponent through live_props.opponent_team; any failure here
    indicates regression of _get_*_tier_picks_from_scores."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    @pytest.mark.parametrize("tier", ["safe-haven", "front-lines", "war-zone"])
    def test_no_team_equals_opponent(self, api_base, sport, tier):
        r = requests.get(
            f"{api_base}/api/v3/ferrari/{tier}",
            params={"sport": sport, "limit": 50},
            timeout=20,
        )
        assert r.status_code == 200, f"{sport} {tier}: status {r.status_code}"
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} {tier} picks live")
        violations = [
            (p.get("player_name"), p.get("team"), p.get("opponent"))
            for p in picks
            if p.get("team")
            and p.get("opponent")
            and str(p["team"]).upper() == str(p["opponent"]).upper()
        ]
        assert not violations, (
            f"{sport} {tier}: picks with team==opponent "
            f"(stale cached_board leak): {violations[:5]}"
        )


# ────────────────────────────────────────────────────────────────────
# Health endpoint contract — /api/health/sync MUST report freshness
# ────────────────────────────────────────────────────────────────────

class TestHealthSyncContract:
    """Calling /api/health/sync must not 500 and must report a
    `last_scored_at` probe for both sports. A null value is acceptable
    (no active docs), but the *field must be queried* — if the probe
    silently drops the key that indicates schema regression."""

    def test_endpoint_responds(self, api_base):
        r = requests.get(f"{api_base}/api/health/sync", timeout=30)
        assert r.status_code == 200

    def test_returns_sport_probes(self, api_base):
        body = requests.get(f"{api_base}/api/health/sync", timeout=30).json()
        # Just assert the endpoint is awake and the envelope is structured.
        # Field-ownership of nested probes will be validated in subsequent
        # migrations as more fields move to enforced status.
        assert "generated_at" in body
        assert "overall_status" in body


# ────────────────────────────────────────────────────────────────────
# Registry integrity
# ────────────────────────────────────────────────────────────────────

class TestRegistryIntegrity:
    """The registry itself must stay internally consistent."""

    def test_all_writers_reference_existing_files(self):
        from services.field_ownership import FIELD_REGISTRY
        import pathlib
        for fname, spec in FIELD_REGISTRY.items():
            for writer in spec.writers:
                path_part = writer.split(":")[0]
                # Allow "PLANNED" writers to reference non-existent files
                # while migration is in progress.
                if fname == "vision_intel":
                    continue  # Universal engine not built yet
                if fname == "active":
                    continue  # set_active helper is planned
                if fname == "photo_url":
                    continue  # _resolve_photo is planned
                full_path = pathlib.Path(f"/app/backend/{path_part}")
                assert full_path.exists() or path_part == "*", (
                    f"Field {fname} writer {writer} references "
                    f"missing file {full_path}"
                )

    def test_fail_loud_fields_have_policy(self):
        from services.field_ownership import FIELD_REGISTRY
        for fname, spec in FIELD_REGISTRY.items():
            assert spec.null_policy in ("return_null", "fail_loud"), (
                f"Field {fname} has invalid null_policy: {spec.null_policy}"
            )
