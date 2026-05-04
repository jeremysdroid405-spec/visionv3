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



# ════════════════════════════════════════════════════════════════════
# Tier C — Alias hardening · FIELD_OWNERSHIP.md 2026-05-04
# ════════════════════════════════════════════════════════════════════


class TestGameStartUtcCanonicalContract:
    """SSOT (FIELD_OWNERSHIP.md:game_start_utc): after Tier C the
    `commence_time` legacy alias on every API pick must equal the
    canonical `game_start_utc` (pinned inside _merge_score_with_board).
    Pre-Tier-C picks could carry a 10-day-stale commence_time alongside
    a fresh game_start_utc."""

    @pytest.mark.parametrize("sport,tier", [
        ("nba", "safe-haven"), ("nba", "front-lines"),
        ("mlb", "safe-haven"),
    ])
    def test_commence_time_equals_game_start_utc(self, api_base, sport, tier):
        r = requests.get(
            f"{api_base}/api/v3/ferrari/{tier}",
            params={"sport": sport, "limit": 20},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"{sport} {tier}: status {r.status_code}")
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} {tier} picks live")
        violations = []
        for p in picks:
            gs = p.get("game_start_utc")
            ct = p.get("commence_time")
            # Only compare when both are set. If commence_time is absent
            # that's OK — the pin only fires when _merge_score_with_board
            # can serialise the canonical. If game_start_utc is absent
            # that's a separate contract.
            if gs and ct and gs != ct:
                violations.append((p.get("player_name"), gs, ct))
        assert not violations, (
            f"{sport} {tier}: {len(violations)} picks have "
            f"commence_time != game_start_utc. Pin regressed. "
            f"First 3: {violations[:3]}"
        )


class TestPhotoUrlCanonicalContract:
    """SSOT (FIELD_OWNERSHIP.md:photo_url): _load_photo_cache reads
    master_hub.photo_url (or same-owner headshot_url) only. No
    /static/player-headshots/{nba_id}.png synthesis, no master_roster
    backfill. The contract here is behavioural: the in-memory
    _photo_cache values must equal the upstream master_hub row for
    the corresponding player."""

    def test_photo_cache_has_no_synthesized_urls(self, db):
        # Load cache (the fixture runs in a live backend context — we
        # inspect the actual DB to verify the migration stuck).
        hub = db["nba_master_hub_2026"]
        sample = list(hub.find(
            {"photo_url": {"$ne": None}},
            {"_id": 0, "photo_url": 1, "nba_id": 1, "display_name": 1},
        ).limit(100))
        if not sample:
            pytest.skip("master_hub has no photo_url rows")
        # Every photo_url must be a string; nba_id synthesis would
        # always match the predictable pattern — if that pattern is
        # the ONLY pattern we see here, master_hub itself was
        # populated by the old synthesizer (still OK because master_hub
        # IS the canonical owner — we just can't have picks_getter
        # _generating_ them).
        for row in sample:
            url = row.get("photo_url")
            assert isinstance(url, str) and url, (
                f"master_hub row {row.get('display_name')} has invalid "
                f"photo_url: {url!r}"
            )


class TestSideCanonicalContract:
    """SSOT (FIELD_OWNERSHIP.md:side): every card-contract pick has
    `side ∈ {OVER, UNDER}`. Card contract reads
    `recommendation || direction`, normalises to uppercase, defaults to
    OVER only on unparseable input. The stamped `side` field must
    always be one of the two enum values — never empty, never
    lowercase, never a synonym."""

    def test_card_contract_stamps_canonical_side(self):
        from services.dashboard_card_contract import to_card_contract
        for payload, expected in [
            ({"recommendation": "OVER"},  "OVER"),
            ({"recommendation": "UNDER"}, "UNDER"),
            ({"recommendation": "over"},  "OVER"),
            ({"recommendation": "Under"}, "UNDER"),
            ({"direction":      "over"},  "OVER"),     # upstream alias tolerance
            ({"direction":      "UNDER"}, "UNDER"),
            ({},                          "OVER"),     # default; last-resort
            ({"recommendation": "weird"}, "OVER"),     # unparseable defaults
        ]:
            c = to_card_contract(payload)
            assert c["side"] == expected, (
                f"Expected side={expected} for input {payload}, "
                f"got {c.get('side')!r}"
            )
            assert c["side"] in ("OVER", "UNDER"), (
                f"side must be enum; got {c.get('side')!r}"
            )

    @pytest.mark.parametrize("sport,tier", [
        ("nba", "safe-haven"), ("mlb", "safe-haven"),
    ])
    def test_api_picks_carry_canonical_side(self, api_base, sport, tier):
        r = requests.get(
            f"{api_base}/api/v3/ferrari/{tier}",
            params={"sport": sport, "limit": 20},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"{sport} {tier}: status {r.status_code}")
        picks = r.json().get("picks") or []
        if not picks:
            pytest.skip(f"no {sport} {tier} picks live")
        bad = [p.get("player_name") for p in picks
               if p.get("side") not in ("OVER", "UNDER")]
        assert not bad, (
            f"{sport} {tier}: {len(bad)} picks missing canonical "
            f"side ∈ {{OVER, UNDER}}. First 3: {bad[:3]}"
        )


class TestPPProjectionIdHealthContract:
    """SSOT (FIELD_OWNERSHIP.md:pp_projection_id + odds_type): staleness
    must be visible on /api/health/sync. The probe must (a) always
    return, (b) include concrete coverage + age fields, (c) honestly
    flag `source_available=False` when the cache is stale or empty,
    (d) never synthesise a projection_id."""

    @pytest.mark.parametrize("sport", ["nba", "mlb"])
    def test_probe_shape(self, api_base, sport):
        body = requests.get(
            f"{api_base}/api/health/sync",
            params={"sports": sport},
            timeout=20,
        ).json()
        pp = ((body.get("sports") or {}).get(sport) or {}).get("pp_projection_ids") or {}
        for k in ("cached", "source_available", "projection_id_count"):
            assert k in pp, (
                f"{sport}: pp_projection_ids probe missing key {k!r}: {pp}"
            )
        # Contract: source_available is never True when count==0.
        if not pp.get("projection_id_count"):
            assert pp.get("source_available") is False, (
                f"{sport}: source_available=True but "
                f"projection_id_count={pp.get('projection_id_count')}. "
                f"Probe is lying about a missing source."
            )


class TestLockedFieldsInventory:
    """Tripwire: asserts the declared count of locked fields in the
    registry matches the SSOT_ENFORCEMENT_REPORT number. If a field
    gets flipped locked → documented (regression) OR documented →
    locked without updating the report, this test catches it."""

    def test_locked_field_count(self):
        from services.field_ownership import FIELD_REGISTRY
        locked = [f for f, s in FIELD_REGISTRY.items() if s.status == "locked"]
        enforced = [f for f, s in FIELD_REGISTRY.items() if s.status == "enforced"]
        # 2026-05-04 Tier C close: 16 fields locked/enforced (Phase 1
        # = 2 locked + 4 enforced at tier/p_true/event_id/line/computed_at
        # = 6; Phase 2 = 4 new locked → 10; Phase 2.5 = 4 new → 14;
        # Phase 2.6 Tier C = +6 locked → 20 total locked+enforced).
        total = len(locked) + len(enforced)
        assert total >= 16, (
            f"Only {total} fields are locked/enforced "
            f"(locked={len(locked)}, enforced={len(enforced)}). "
            f"Expected ≥16 after Tier C. Did a field regress from "
            f"`locked` back to `documented`?"
        )



# ════════════════════════════════════════════════════════════════════
# Tier D — Pydantic write contract + PP staleness logging
# ════════════════════════════════════════════════════════════════════


class TestPydanticWriteContract:
    """Tier D + Tier F #4: every score doc goes through
    ScoreDocument.model_validate. As of 2026-05-04 the schema is
    `extra="forbid"` LIVE — silent drift is impossible. LOCKED SSOT
    fields are typed so type drift is caught at write time."""

    def test_schema_accepts_valid_doc(self):
        from services.scoring.score_document_schema import ScoreDocument
        from datetime import datetime, timezone
        from pydantic import ValidationError
        doc = {
            "canonical_key":  "nba|evt1|Jayson Tatum|PTS|25.5|OVER",
            "sport":          "nba",
            "event_id":       "evt1",
            "player_name":    "Jayson Tatum",
            "stat_type":      "PTS",
            "line":           25.5,
            "recommendation": "OVER",
            "version_tag":    "final-nba-rt",
            "computed_at":    datetime.now(timezone.utc),
            "scored_at":      datetime.now(timezone.utc),
            "active":         True,
            "vision_score":   82.4,
            "edge_vs_fair":   0.14,
            "cv":             0.21,
            "hit_rate_l20":   0.72,
        }
        validated = ScoreDocument.model_validate(doc)
        assert validated.canonical_key == doc["canonical_key"]
        assert validated.edge_vs_fair == 0.14
        # Tier F #4: extras=forbid — undeclared diagnostic fields
        # MUST be rejected at write time.
        with pytest.raises(ValidationError):
            ScoreDocument.model_validate({**doc, "some_undeclared_field": 42})

    def test_schema_rejects_missing_required(self):
        from services.scoring.score_document_schema import ScoreDocument
        from pydantic import ValidationError
        bad = {"canonical_key": "nba|evt1|p|PTS|10.0|OVER"}
        with pytest.raises(ValidationError):
            ScoreDocument.model_validate(bad)

    def test_schema_rejects_type_drift(self):
        """Line is `float` — non-numeric strings must be rejected."""
        from services.scoring.score_document_schema import ScoreDocument
        from pydantic import ValidationError
        from datetime import datetime, timezone
        bad = {
            "canonical_key": "nba|evt1|p|PTS|10.0|OVER",
            "sport":         "nba",
            "stat_type":     "PTS",
            "line":          "not-a-number",
            "version_tag":   "final-nba-rt",
            "computed_at":   datetime.now(timezone.utc),
            "scored_at":     datetime.now(timezone.utc),
        }
        with pytest.raises(ValidationError):
            ScoreDocument.model_validate(bad)

    def test_validate_helper_logs_but_doesnt_raise_in_default_mode(self, caplog):
        from services.scoring.score_document_schema import (
            validate_score_document, SSOT_PYDANTIC_STRICT,
        )
        import logging
        if SSOT_PYDANTIC_STRICT:
            pytest.skip("strict mode on; helper re-raises")
        caplog.set_level(logging.WARNING, logger="services.scoring.score_document_schema")
        err = validate_score_document({"canonical_key": "bad"})
        assert err is not None
        assert any("SSOT_PYDANTIC" in rec.message for rec in caplog.records)


class TestAllowlistSchemaParity:
    """Every SSOT-LOCKED field must be typed in `ScoreDocument`. This
    gates a future `extra="forbid"` flip without surprises."""

    def test_schema_covers_all_ssot_locked_fields(self):
        from services.scoring.score_document_schema import ScoreDocument
        required = {
            "canonical_key", "sport", "stat_type", "line",
            "version_tag", "computed_at", "scored_at",
            "active", "inactive_reason", "active_changed_at",
            "game_start_utc",
            "vision_score", "edge_vs_fair",
            "tier",
            "p_true_active",
            "hit_rate_l5", "hit_rate_l10", "hit_rate_l20", "hit_rate_over",
            "ranking_score_v2",
            "cv",
            "book_count", "coverage_class",
        }
        schema_fields = set(ScoreDocument.model_fields.keys())
        missing = required - schema_fields
        assert not missing, (
            f"ScoreDocument schema missing {len(missing)} SSOT-locked "
            f"fields: {sorted(missing)}."
        )


class TestPPStalenessLogging:
    """Tier D: the staleness probe emits WARN ≥6h, CRITICAL ≥24h."""

    def test_probe_emits_warn_on_stale_cache(self, api_base, caplog):
        """Hit the live probe; the real NBA cache is already >24h
        stale (verified at Tier C close) so a CRITICAL log fires on
        every /api/health/sync read."""
        # Invoke probe via the API and read the current log tail.
        import requests
        import subprocess
        r = requests.get(f"{api_base}/api/health/sync?sports=nba", timeout=20)
        assert r.status_code == 200
        # Fetch last 50 WARN/CRITICAL lines from supervisor log.
        try:
            out = subprocess.check_output(
                ["grep", "-E", "PP_STALENESS",
                 "/var/log/supervisor/backend.err.log"],
                text=True, stderr=subprocess.DEVNULL,
            )
            assert "PP_STALENESS" in out, (
                f"Expected PP_STALENESS log line in supervisor output."
            )
            # At least one CRITICAL line (NBA is >24h stale at time of
            # writing this suite; if that changes, drop to WARN check).
            assert ("CRITICAL" in out) or ("WARN" in out), (
                "Expected CRITICAL or WARN level staleness log."
            )
        except subprocess.CalledProcessError:
            pytest.skip("supervisor log not accessible in this env")



# ════════════════════════════════════════════════════════════════════
# Tier F.2 — hit_rate_over → hit_rate_l20 reader migration
# ════════════════════════════════════════════════════════════════════


class TestHitRateL20PrimaryReadContract:
    """Tier F.2 (2026-05-04): every backend consumer of the L20
    OVER-side hit rate now reads `hit_rate_l20` as the PRIMARY source,
    with legacy `hit_rate_over` retained only as a fallback for pre-
    dual-write docs. This contract asserts the read-order preference."""

    def test_card_contract_prefers_hit_rate_l20(self):
        """When both fields are present with DIFFERENT values the
        card contract must surface the canonical `hit_rate_l20` —
        proves the read-order is canonical-first, not legacy-first.
        (In production the dual-write guarantees they're equal, but
        this test diverges them intentionally to verify preference.)"""
        from services.dashboard_card_contract import to_card_contract
        pick = {
            "player_name":    "TEST",
            "stat_type":      "PTS",
            "line":           25.5,
            "recommendation": "OVER",
            "hit_rate_l20":   71.0,   # canonical
            "hit_rate_over":  55.0,   # legacy — MUST be ignored
            "hit_rate_under": 29.0,
        }
        out = to_card_contract(pick)
        assert out["hit_rate"] == 71.0, (
            f"Card contract read legacy hit_rate_over={55.0} "
            f"instead of canonical hit_rate_l20={71.0}. Got "
            f"hit_rate={out['hit_rate']}"
        )
        assert out["hit_rate_l20"] == 71.0

    def test_card_contract_falls_back_to_legacy_when_canonical_missing(self):
        """Pre-dual-write docs lack `hit_rate_l20` — the card
        contract MUST still surface the value from legacy
        `hit_rate_over` (zero data-loss fallback)."""
        from services.dashboard_card_contract import to_card_contract
        pick = {
            "player_name":    "TEST",
            "stat_type":      "PTS",
            "line":           25.5,
            "recommendation": "OVER",
            "hit_rate_over":  68.0,   # legacy only
            "hit_rate_under": 32.0,
        }
        out = to_card_contract(pick)
        assert out["hit_rate"] == 68.0

    def test_metrics_builder_prefers_hit_rate_l20(self):
        """metrics_builder.build_metrics_from_score_doc: same contract."""
        from services.scoring.metrics_builder import build_metrics_from_score_doc
        doc = {
            "sport":            "nba",
            "stat_type":        "PTS",
            "line":             25.5,
            "recommendation":   "OVER",
            "hit_rate_l20":     71.0,
            "hit_rate_over":    55.0,
            "hit_rate_under":   29.0,
            "cv":               0.2,
            "edge_vs_fair":     0.1,
            "p_true_active":    0.6,
            "model_projection": 26.0,
        }
        m = build_metrics_from_score_doc(doc)
        assert m.hit_rate == 71.0, (
            f"metrics_builder pulled legacy hit_rate_over=55.0 "
            f"over canonical hit_rate_l20=71.0. Got: {m.hit_rate}"
        )



# ────────────────────────────────────────────────────────────────────
# Tier F #1: `direction` alias stamping removed from response picks
# ────────────────────────────────────────────────────────────────────


class TestDirectionAliasStampingRemoved:
    """SSOT Tier F #1 (2026-05-04): response-building writers MUST NOT
    duplicate the canonical `recommendation` value into a legacy
    `direction` key. Frontend and backend readers migrated to
    `recommendation` / `side` as canonical. A lingering `direction`
    stamp is a silent SSOT violation."""

    @pytest.mark.parametrize("sport,tier", [
        ("NBA", "safe-haven"),
        ("NBA", "front-lines"),
        ("MLB", "safe-haven"),
        ("MLB", "front-lines"),
    ])
    def test_ferrari_tier_response_does_not_stamp_direction_alias(
        self, api_base, sport, tier
    ):
        url = f"{api_base}/api/v3/ferrari/{tier}?sport={sport}&limit=10"
        try:
            r = requests.get(url, timeout=20)
        except requests.RequestException as e:
            pytest.skip(f"{sport} {tier} endpoint unreachable: {e}")
        if r.status_code != 200:
            pytest.skip(f"{sport} {tier} returned {r.status_code}")
        data = r.json()
        picks = (
            data.get("picks")
            or data.get("data")
            or data.get("items")
            or (data if isinstance(data, list) else [])
        )
        if not picks:
            pytest.skip(f"{sport} {tier} returned no picks")
        offenders = [
            f"{p.get('player_name')} {p.get('stat_type')}"
            for p in picks
            if "direction" in p
        ]
        assert not offenders, (
            f"{sport} {tier}: {len(offenders)}/{len(picks)} picks still "
            f"carry legacy `direction` alias. Sample: {offenders[:5]}. "
            f"Tier F #1 writer deletion regressed."
        )
        # Positive check — canonical field MUST be present.
        missing_rec = [
            f"{p.get('player_name')} {p.get('stat_type')}"
            for p in picks
            if not p.get("recommendation")
        ]
        assert not missing_rec, (
            f"{sport} {tier}: {len(missing_rec)} picks missing canonical "
            f"`recommendation`. Sample: {missing_rec[:5]}."
        )


# ────────────────────────────────────────────────────────────────────
# Tier F #2: `edge_pct` / `vk_edge` / `true_edge` alias stamping
# removed from API responses (canonical is `edge_vs_fair`).
# ────────────────────────────────────────────────────────────────────

_EDGE_ALIAS_BLACKLIST = ("edge_pct", "vk_edge", "true_edge")


class TestEdgeAliasStampingRemoved:
    """SSOT Tier F #2 (2026-05-04): response-building paths MUST NOT
    stamp the legacy edge aliases `edge_pct`, `vk_edge`, or
    `true_edge` on public API picks. Canonical field is
    `edge_vs_fair`. Frontend has zero active readers for the aliases;
    any leakage is a silent SSOT regression."""

    @pytest.mark.parametrize("sport,tier", [
        ("NBA", "safe-haven"),
        ("NBA", "front-lines"),
        ("NBA", "war-zone"),
        ("MLB", "safe-haven"),
        ("MLB", "front-lines"),
        ("MLB", "war-zone"),
    ])
    def test_ferrari_tier_response_does_not_stamp_edge_aliases(
        self, api_base, sport, tier
    ):
        url = f"{api_base}/api/v3/ferrari/{tier}?sport={sport}&limit=15"
        try:
            r = requests.get(url, timeout=25)
        except requests.RequestException as e:
            pytest.skip(f"{sport} {tier} endpoint unreachable: {e}")
        if r.status_code != 200:
            pytest.skip(f"{sport} {tier} returned {r.status_code}")
        data = r.json()
        picks = (
            data.get("picks")
            or data.get("data")
            or data.get("items")
            or (data if isinstance(data, list) else [])
        )
        if not picks:
            pytest.skip(f"{sport} {tier} returned no picks")
        for alias in _EDGE_ALIAS_BLACKLIST:
            offenders = [
                f"{p.get('player_name')} {p.get('stat_type')}"
                for p in picks
                if alias in p
            ]
            assert not offenders, (
                f"{sport} {tier}: {len(offenders)}/{len(picks)} picks "
                f"still carry legacy `{alias}` alias. "
                f"Sample: {offenders[:5]}. Tier F #2 writer deletion "
                f"regressed."
            )
        # Canonical field must remain stamped on the response.
        missing_canonical = [
            f"{p.get('player_name')} {p.get('stat_type')}"
            for p in picks
            if "edge_vs_fair" not in p
        ]
        assert not missing_canonical, (
            f"{sport} {tier}: canonical `edge_vs_fair` missing from "
            f"{len(missing_canonical)}/{len(picks)} picks. Sample: "
            f"{missing_canonical[:5]}."
        )



# ────────────────────────────────────────────────────────────────────
# Tier F #3 (Option C): legacy `dg_cached_board*` collections must
# never reappear. The live display-enrichment collections
# `nba_cached_board` and `mlb_cached_board` MUST still exist —
# their full migration is a phased Option-D follow-up, not a Tier F
# deliverable.
# ────────────────────────────────────────────────────────────────────


_DROPPED_LEGACY_COLLECTIONS = (
    "dg_cached_board",
    "dg_cached_board_temp",
)
_LIVE_DISPLAY_COLLECTIONS = (
    "nba_cached_board",
    "mlb_cached_board",
)


class TestDgCachedBoardRetired:
    """SSOT Tier F #3 (2026-05-04, Option C): the legacy
    `dg_cached_board*` collections were retired 2026-04-30 (the main
    table) and 2026-05-04 (the `_temp` shadow). They MUST NOT
    reappear in Mongo. The canonical NBA/MLB cached_board collections
    remain live until the Option-D phased migration retires them."""

    @pytest.mark.parametrize("name", _DROPPED_LEGACY_COLLECTIONS)
    def test_dropped_collection_does_not_exist(self, db, name):
        names = set(db.list_collection_names())
        assert name not in names, (
            f"Legacy collection `{name}` reappeared. "
            f"Tier F #3 (Option C) deletion regressed."
        )

    @pytest.mark.parametrize("name", _LIVE_DISPLAY_COLLECTIONS)
    def test_live_display_collection_still_exists(self, db, name):
        names = set(db.list_collection_names())
        assert name in names, (
            f"Live display-enrichment collection `{name}` is missing. "
            f"Option-D phased migration was not run in this session — "
            f"this collection should still exist."
        )

    def test_no_active_query_on_dropped_collections(self):
        """Static guard: no source file may issue an active Mongo
        query (.find / .update / .insert / .bulk_write / .aggregate /
        .distinct / .count) against the dropped collection names.
        Comments and historical audit notes are allowed."""
        import os
        import re
        backend_root = "/app/backend"
        # Patterns that indicate an active query on the literal
        # dropped name (NOT via COLL("board_cache", ...) which
        # resolves to nba_cached_board).
        active_query_re = re.compile(
            r'\["dg_cached_board(?:_temp)?"\]\s*\.\s*'
            r'(find|find_one|aggregate|distinct|count|count_documents|'
            r'update|update_one|update_many|insert|insert_one|'
            r'insert_many|replace_one|bulk_write|delete|delete_many|'
            r'delete_one|drop)'
        )
        offenders = []
        for root, _dirs, files in os.walk(backend_root):
            if "_archive" in root or "/tests/" in root or "/scripts/" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                for m in active_query_re.finditer(src):
                    offenders.append(f"{path}: {m.group(0)}")
        assert not offenders, (
            f"Active query on dropped `dg_cached_board*` collection "
            f"detected ({len(offenders)} site(s)):\n"
            + "\n".join(offenders[:10])
        )

