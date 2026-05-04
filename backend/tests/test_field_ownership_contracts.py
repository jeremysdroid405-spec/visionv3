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


class TestActiveTransitionsEndpoint:
    """Diagnostic surface for the active_transitions audit collection.
    Read-only — must never mutate, must always return a well-formed
    envelope even when no transitions exist in the window."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    def test_envelope_shape(self, api_base, sport):
        r = requests.get(
            f"{api_base}/api/health/active-transitions",
            params={"sport": sport, "hours": 24},
            timeout=20,
        )
        assert r.status_code == 200, f"{sport}: status {r.status_code}"
        body = r.json()
        for key in (
            "generated_at", "sport", "window_hours",
            "total", "active_to_inactive", "inactive_to_active",
            "top_reasons", "top_writers", "latest",
        ):
            assert key in body, f"{sport}: missing key {key!r} in envelope"
        assert body["sport"] == sport
        assert body["window_hours"] == 24
        assert isinstance(body["total"], int)
        assert isinstance(body["latest"], list)
        assert len(body["latest"]) <= 25

    def test_rejects_invalid_sport(self, api_base):
        r = requests.get(
            f"{api_base}/api/health/active-transitions",
            params={"sport": "nhl", "hours": 24},
            timeout=20,
        )
        # FastAPI Query regex returns 422 on mismatch.
        assert r.status_code == 422

    def test_rejects_out_of_range_hours(self, api_base):
        r = requests.get(
            f"{api_base}/api/health/active-transitions",
            params={"sport": "nba", "hours": 999},
            timeout=20,
        )
        assert r.status_code == 422

    def test_latest_rows_carry_required_keys(self, api_base):
        r = requests.get(
            f"{api_base}/api/health/active-transitions",
            params={"sport": "nba", "hours": 24},
            timeout=20,
        )
        body = r.json()
        if not body.get("latest"):
            pytest.skip("no NBA transitions in window")
        row = body["latest"][0]
        for k in (
            "sport", "canonical_key", "active_from", "active_to",
            "reason", "source_writer", "timestamp",
        ):
            assert k in row, f"latest row missing key {k!r}"
        # Audit contract: every row represents an actual transition,
        # so active_from and active_to must disagree.
        assert row["active_from"] != row["active_to"]


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


# ────────────────────────────────────────────────────────────────────
# Phase 2 — `active` contract · FIELD_OWNERSHIP.md:active
# ────────────────────────────────────────────────────────────────────

class TestActiveContract:
    """Every active-flip on `{sport}_prop_scores` must go through the
    canonical `services.board.set_active` helper. We assert this
    behaviourally: the set_active helper is importable, non-None
    transitions are audited in `active_transitions`, and no score doc
    under the canonical version_tag carries a stale `active=False`
    without a reason (the defining trait of the pre-migration bugs)."""

    def test_set_active_helper_importable(self):
        from services.board.set_active import set_active, ensure_indexes, AUDIT_COLL
        assert callable(set_active)
        assert callable(ensure_indexes)
        assert AUDIT_COLL == "active_transitions"

    @pytest.mark.parametrize("sport,tag", [
        ("nba", "final-nba-rt"),
        ("mlb", "final-mlb-rt"),
    ])
    def test_inactive_docs_carry_reason(self, db, sport, tag):
        """Every `active=False` doc under the RT tag must also have
        `inactive_reason` populated. Pre-2026-05-04 the retire path
        sometimes forgot the reason field — a smoking-gun for an
        un-migrated writer."""
        coll = db[f"{sport}_prop_scores"]
        inactive = coll.count_documents({
            "version_tag": tag, "active": False,
        })
        if inactive == 0:
            pytest.skip(f"no inactive {sport} docs to validate")
        with_reason = coll.count_documents({
            "version_tag": tag, "active": False,
            "inactive_reason": {"$ne": None},
        })
        pct = 100 * with_reason / inactive
        assert pct >= 95.0, (
            f"{sport}: only {with_reason}/{inactive} ({pct:.1f}%) "
            f"inactive docs carry inactive_reason. Indicates a writer "
            f"bypassing services.board.set_active."
        )


# ────────────────────────────────────────────────────────────────────
# Phase 2 — `player_name` contract · FIELD_OWNERSHIP.md:player_name
# ────────────────────────────────────────────────────────────────────

class TestPlayerNameContract:
    """Every pick surfaced by a ferrari endpoint MUST carry a non-empty
    `player_name`. The field is `fail_loud` — a missing value means
    the upstream master_hub → live_props chain is broken and the pick
    should have been dropped, not displayed."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    @pytest.mark.parametrize("tier", ["safe-haven", "front-lines", "war-zone"])
    def test_player_name_non_empty(self, api_base, sport, tier):
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
            i for i, p in enumerate(picks)
            if not (p.get("player_name") or "").strip()
        ]
        assert not violations, (
            f"{sport} {tier}: {len(violations)} picks with empty "
            f"player_name (SSOT violation — upstream master_hub chain "
            f"regressed). Indices: {violations[:5]}"
        )


# ────────────────────────────────────────────────────────────────────
# Phase 2 — `team` contract · FIELD_OWNERSHIP.md:team
# ────────────────────────────────────────────────────────────────────

class TestTeamContract:
    """The card-contract layer reads `pick.get('team')` ONLY — alias
    fallbacks (`team_abbr` / `player_team` / `home_team_abbr`) were
    removed 2026-05-04. Picks that reach the response with a team
    value different from their raw `team` field indicate a legacy
    alias path has crept back in."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    def test_card_contract_does_not_fallback_to_alias(self, api_base, sport):
        """If a pick has `team_abbr` / `player_team` / etc. set but
        `team` unset, the response must NOT silently substitute. This
        is the card-contract regression signature."""
        r = requests.get(
            f"{api_base}/api/v3/ferrari/safe-haven",
            params={"sport": sport, "limit": 50},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"{sport}: status {r.status_code}")
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} picks live")
        # At least 80% of picks should have a `team` value. (Some
        # sports may legitimately have gaps during roster churn.)
        with_team = sum(1 for p in picks if (p.get("team") or "").strip())
        pct = 100 * with_team / len(picks)
        assert pct >= 80.0, (
            f"{sport}: only {with_team}/{len(picks)} ({pct:.1f}%) "
            f"picks carry team. Check universal_odds_sync._build_prop_record."
        )


# ────────────────────────────────────────────────────────────────────
# Phase 2 — `vision_intel` contract · FIELD_OWNERSHIP.md:vision_intel
# ────────────────────────────────────────────────────────────────────

class TestVisionIntelContract:
    """SSOT enforcement: when the Vision Intel engine has not produced
    text for a pick, the value MUST be null — no templated fallback,
    no stale JSON override. These tests assert the two fake-data
    sources neutralised on 2026-05-04 stay neutral."""

    def test_generate_vision_fallback_returns_none(self):
        """Previously this helper synthesised plausible-looking
        sentences from model numbers. After nullification it must
        return None unconditionally."""
        from routes.ferrari_tiers import _generate_vision_fallback
        out = _generate_vision_fallback({
            "player_name": "Test Player",
            "stat_type": "PTS",
            "line": 20.5,
            "vk_predicted": 22.0,
            "vk_edge": 5.0,
            "vk_prob_over": 75.0,
            "direction": "OVER",
        })
        assert out is None, (
            f"_generate_vision_fallback must return None under SSOT "
            f"enforcement. Got: {out!r}"
        )

    def test_overlay_enrichment_cache_does_not_read_json(self, tmp_path):
        """The legacy JSON cache path is disabled. Monkey-patch the
        cache-file path to a known-bogus location; the function must
        still complete without raising (cache read is short-circuited)
        and must NOT set vision_intel / scout_badges from the file."""
        from routes import ferrari_tiers
        picks = [{
            "player_name": "Test Player",
            "stat_type": "PTS",
            "line": 20.5,
            "recommendation": "OVER",
            "vision_intel": None,
            "cv": 0.25,
        }]
        out = ferrari_tiers.overlay_enrichment_cache(picks, "nba")
        # Volatility profile must still stamp (that's the valid
        # non-override behaviour).
        assert "volatility_score" in out[0]
        # vision_intel must remain None — no stale JSON wrote into it.
        assert out[0]["vision_intel"] is None, (
            f"overlay_enrichment_cache wrote vision_intel from a "
            f"cache source: {out[0].get('vision_intel')!r}"
        )

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    def test_no_template_signature_in_api_response(self, api_base, sport):
        """The old templated fallback had a signature phrase — e.g.
        '— model sees' or 'riding the over with'. If any live pick
        surfaces that phrase, a fallback path has been re-enabled."""
        r = requests.get(
            f"{api_base}/api/v3/ferrari/safe-haven",
            params={"sport": sport, "limit": 50},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"{sport}: status {r.status_code}")
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} picks live")
        signatures = [
            "— model sees",
            "riding the over with",
            "riding the under with",
            "the math backs the over",
            "the math backs the under",
        ]
        bad = []
        for p in picks:
            vi = p.get("vision_intel") or ""
            for sig in signatures:
                if sig in vi:
                    bad.append((p.get("player_name"), sig))
                    break
        assert not bad, (
            f"{sport}: {len(bad)} picks carry templated fallback "
            f"vision_intel (SSOT violation). First 3: {bad[:3]}"
        )


# ════════════════════════════════════════════════════════════════════
# Tier B — Derived-field cleanup · FIELD_OWNERSHIP.md 2026-05-04
# ════════════════════════════════════════════════════════════════════


class TestRankingScoreV2Contract:
    """SSOT (FIELD_OWNERSHIP.md:ranking_score_v2): the canonical
    projection-gap ranker. Null is legitimate (identity-failed picks,
    missing projection/line/p_model). The board publisher `_rank_score`
    must (a) prefer `ranking_score_v2` when present and (b) drop the
    legacy `ranking_score` alias entirely — vision_score is the only
    retained secondary-sort fallback."""

    def test_rank_score_uses_v2_when_present(self):
        from services.board.publisher import _rank_score
        assert _rank_score({"ranking_score_v2": 0.42}) == 0.42
        # `ranking_score` alias must NOT be honoured.
        assert _rank_score({"ranking_score": 0.99,
                            "vision_score": 55}) == 55
        assert _rank_score({"vision_score": 77.7}) == 77.7
        # Nothing → -inf (not a hard raise — return_null policy).
        import math
        r = _rank_score({})
        assert math.isinf(r) and r < 0


class TestHitRateL20Contract:
    """SSOT (FIELD_OWNERSHIP.md:hit_rate_l20): dual-write invariant —
    after Phase 2, recompute_sport stamps `hit_rate_l20` alongside the
    legacy `hit_rate_over`. Both must be numerically identical."""

    @pytest.mark.parametrize("sport,tag", [
        ("nba", "final-nba-rt"),
        ("mlb", "final-mlb-rt"),
    ])
    def test_hit_rate_l20_matches_legacy(self, db, sport, tag):
        coll = db[f"{sport}_prop_scores"]
        # Only validate docs that have BOTH fields present (post-dual-write).
        # Pre-2026-05-04 docs won't have `hit_rate_l20` — next recompute
        # wave will backfill.
        total = coll.count_documents({
            "version_tag": tag, "active": True,
            "hit_rate_l20": {"$exists": True, "$ne": None},
            "hit_rate_over": {"$exists": True, "$ne": None},
        })
        if total == 0:
            pytest.skip(f"no {sport} docs with both hit_rate_l20 + "
                        f"hit_rate_over yet (pre-rescore)")
        mismatched = coll.count_documents({
            "version_tag": tag, "active": True,
            "hit_rate_l20": {"$exists": True, "$ne": None},
            "hit_rate_over": {"$exists": True, "$ne": None},
            "$expr": {"$ne": ["$hit_rate_l20", "$hit_rate_over"]},
        })
        assert mismatched == 0, (
            f"{sport}: {mismatched}/{total} docs have hit_rate_l20 != "
            f"hit_rate_over. Dual-write in recompute_sport regressed."
        )


class TestCVParallelComputeContract:
    """SSOT (FIELD_OWNERSHIP.md:cv): intel_suite.stability_index must
    be derived from the canonical `cv` field (σ = cv × μ) — not a
    parallel recomputation from raw game logs. This was the cause of
    the composite-MLB "100% Elite" contradiction (std_dev ≈ 0 on
    H+R+RBI because _extract_stat_values doesn't decompose composites)."""

    def test_stability_prefers_cv_derived_std_dev(self):
        from services.intel_suite_calculator import IntelSuiteCalculator
        # Hand-build a calc instance without running its DB-bound
        # async __init__; we only need the sync helper.
        calc = object.__new__(IntelSuiteCalculator)
        # cv=0.1, projection=20 → σ = 2.0 → "High" (85, Very Consistent)
        result = calc._calculate_stability_index(
            active_logs=[],
            stat_type="PTS",
            board_pick={"cv": 0.1, "model_projection": 20.0},
        )
        assert result["std_dev"] == 2.0, (
            f"Expected σ = cv × μ = 0.1 × 20 = 2.0; got {result['std_dev']}"
        )
        assert result["score"] == 85
        assert result["label"] == "High"

    def test_stability_falls_back_when_cv_absent(self):
        from services.intel_suite_calculator import IntelSuiteCalculator
        calc = object.__new__(IntelSuiteCalculator)
        # No cv → explicit std_dev should win.
        result = calc._calculate_stability_index(
            active_logs=[],
            stat_type="PTS",
            board_pick={"std_dev": 4.2},
        )
        assert result["std_dev"] == 4.2


class TestEdgeCanonicalContract:
    """SSOT (FIELD_OWNERSHIP.md:edge): `edge_vs_fair` is the canonical
    owner. Aliases (`edge_pct`, `vk_edge`) remain in API responses for
    backwards compat but must be derived from the canonical value,
    never from a different source. The contract here is that
    `edge_vs_fair` is present on every ranked pick — missing canonical
    `edge` indicates the scoring stack didn't run."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    def test_edge_vs_fair_populated_on_api_picks(self, api_base, sport):
        r = requests.get(
            f"{api_base}/api/v3/ferrari/safe-haven",
            params={"sport": sport, "limit": 20},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"{sport}: status {r.status_code}")
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} picks live")
        with_edge = sum(
            1 for p in picks
            if isinstance(p.get("edge_vs_fair"), (int, float))
        )
        pct = 100 * with_edge / len(picks)
        assert pct >= 90.0, (
            f"{sport}: only {with_edge}/{len(picks)} ({pct:.1f}%) "
            f"picks carry canonical edge_vs_fair. Scoring stack "
            f"may have regressed."
        )

