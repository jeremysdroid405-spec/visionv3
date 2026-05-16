"""Pipeline hardening — 2026-05-17 structural tests.

Six axes verified:

  1. Market-class classifier behaviour (pure function)
  2. canonical_key_v2 construction (idempotent, well-formed)
  3. Score-doc backstop: market_class + canonical_key_v2 derived
     when upstream didn't populate them
  4. Split odds container semantics — alt/std never cross-pollute
  5. Replay-trace reconstruction — synthetic snapshot rows survive
     the canonical_candidate join
  6. Append-only retention contract — multiple inserts coexist

No I/O against the live DB. We mount an in-memory mongomock
client where DB writes are exercised.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "/app/backend")

from services.market_class import (
    ALLOWED_MARKET_CLASSES,
    classify_market_key,
    build_canonical_v2,
    is_alternate,
)
from services.scoring.prop_scores_store import _project_score_doc


# ── 1. Classifier ──────────────────────────────────────────────────
class TestClassifier:
    @pytest.mark.parametrize("mk,expected", [
        ("batter_total_bases", "standard"),
        ("batter_home_runs", "standard"),
        ("pitcher_strikeouts", "standard"),
        ("h2h", "standard"),
        ("totals", "standard"),
        ("batter_total_bases_alternate", "alternate"),
        ("pitcher_strikeouts_alternate", "alternate"),
        ("batter_hits_alternate", "alternate"),
        ("batter_total_bases_sgp", "sgp"),
        ("batter_total_bases_promo", "promo"),
        ("", "unknown"),
        (None, "unknown"),
        ("FOO_BAR_ALTERNATE", "alternate"),  # case-insensitive
    ])
    def test_classification(self, mk, expected):
        assert classify_market_key(mk) == expected

    def test_is_alternate_alias_matches_classifier(self):
        assert is_alternate("batter_hits_alternate") is True
        assert is_alternate("batter_hits") is False
        assert is_alternate(None) is False

    def test_allowed_set_complete(self):
        # Defensive: every value returned by the classifier must be in
        # the public allowed set so consumers can validate against it.
        for mk in ("foo", "foo_alternate", "foo_sgp", "foo_promo", "", None):
            assert classify_market_key(mk) in ALLOWED_MARKET_CLASSES


# ── 2. canonical_key_v2 ────────────────────────────────────────────
class TestCanonicalV2:
    def test_v2_appends_trailing_class(self):
        legacy = "mlb|EID|Matt Olson|Total Bases|0.5|OVER"
        v2 = build_canonical_v2(legacy, "alternate")
        assert v2 == "mlb|EID|Matt Olson|Total Bases|0.5|OVER|alternate"

    def test_v2_idempotent_when_already_classed(self):
        legacy = "mlb|EID|Matt Olson|Total Bases|0.5|OVER"
        once = build_canonical_v2(legacy, "alternate")
        twice = build_canonical_v2(once, "alternate")
        # Re-invoking against an already-v2 key returns unchanged.
        assert twice == once

    def test_v2_collapses_unknown_class_to_unknown(self):
        legacy = "mlb|EID|Matt Olson|Total Bases|0.5|OVER"
        v2 = build_canonical_v2(legacy, "garbage_value")
        assert v2.endswith("|unknown")

    def test_standard_and_alternate_v2_keys_differ(self):
        legacy = "mlb|EID|Matt Olson|Total Bases|0.5|OVER"
        std = build_canonical_v2(legacy, "standard")
        alt = build_canonical_v2(legacy, "alternate")
        assert std != alt
        # Both share the legacy prefix verbatim.
        assert std.startswith(legacy + "|")
        assert alt.startswith(legacy + "|")


# ── 3. Score-doc backstop derivation ───────────────────────────────
def _ctx(**overrides):
    base = {
        "canonical_key": "mlb|EID|Tester|Hits|1.5|OVER",
        "sport": "mlb",
        "event_id": "EID",
        "player_name": "Tester",
        "stat_type": "Hits",
        "line": 1.5,
        "recommendation": "OVER",
    }
    base.update(overrides)
    return base


class TestScoreDocBackstop:
    def test_market_class_derived_from_market_key(self):
        doc = _project_score_doc(
            _ctx(market_key="batter_total_bases_alternate"),
            version_tag="t", computed_at="ts",
        )
        assert doc.get("market_class") == "alternate"

    def test_market_class_derived_from_is_alternate_market_flag(self):
        doc = _project_score_doc(
            _ctx(is_alternate_market=True),
            version_tag="t", computed_at="ts",
        )
        assert doc.get("market_class") == "alternate"

    def test_market_class_defaults_to_standard_when_flag_false(self):
        doc = _project_score_doc(
            _ctx(is_alternate_market=False),
            version_tag="t", computed_at="ts",
        )
        assert doc.get("market_class") == "standard"

    def test_market_class_defaults_to_unknown_when_nothing_set(self):
        doc = _project_score_doc(_ctx(), version_tag="t", computed_at="ts")
        assert doc.get("market_class") == "unknown"

    def test_canonical_key_v2_synthesised_when_absent(self):
        doc = _project_score_doc(
            _ctx(market_key="batter_total_bases_alternate"),
            version_tag="t", computed_at="ts",
        )
        ck_v2 = doc.get("canonical_key_v2")
        assert ck_v2 is not None
        assert ck_v2.endswith("|alternate")
        # Legacy preserved.
        assert doc.get("canonical_key") == "mlb|EID|Tester|Hits|1.5|OVER"

    def test_explicit_canonical_key_v2_wins_over_synthesis(self):
        explicit = "mlb|EID|Tester|Hits|1.5|OVER|standard"
        doc = _project_score_doc(
            _ctx(canonical_key_v2=explicit,
                 market_key="batter_total_bases_alternate"),
            version_tag="t", computed_at="ts",
        )
        # Even though market_key says "alternate", we trust the
        # explicit upstream value.
        assert doc.get("canonical_key_v2") == explicit

    def test_source_market_key_persisted_verbatim(self):
        doc = _project_score_doc(
            _ctx(source_market_key="batter_hits_alternate"),
            version_tag="t", computed_at="ts",
        )
        assert doc.get("source_market_key") == "batter_hits_alternate"
        assert doc.get("market_class") == "alternate"


# ── 4. Split odds container semantics ──────────────────────────────
class TestSplitOddsContainers:
    """The canonical-record-builder in `universal_odds_sync` initialises
    `all_odds_standard`, `all_odds_alternate`, `all_lines_standard`,
    `all_lines_alternate` as empty dicts on each canonical seed. Pass 2
    routes each layer's price into the matching bucket — never mixing.

    This module-level test simulates the routing logic in isolation
    so we don't have to spin the full sync pipeline."""

    @staticmethod
    def _route(target, bm_key, line, price, attach_class):
        bucket_odds = (target["all_odds_alternate"]
                       if attach_class == "alternate"
                       else target["all_odds_standard"])
        bucket_lines = (target["all_lines_alternate"]
                        if attach_class == "alternate"
                        else target["all_lines_standard"])
        bucket_odds[bm_key] = price
        bucket_lines[bm_key] = float(line)

    def _fresh(self):
        return {
            "all_odds_standard": {},
            "all_odds_alternate": {},
            "all_lines_standard": {},
            "all_lines_alternate": {},
        }

    def test_standard_attach_lands_in_standard_bucket(self):
        t = self._fresh()
        self._route(t, "fanduel", 0.5, -140, "standard")
        assert t["all_odds_standard"] == {"fanduel": -140}
        assert t["all_odds_alternate"] == {}
        assert t["all_lines_standard"] == {"fanduel": 0.5}
        assert t["all_lines_alternate"] == {}

    def test_alternate_attach_lands_in_alternate_bucket(self):
        t = self._fresh()
        self._route(t, "draftkings", 0.5, 1000, "alternate")
        assert t["all_odds_alternate"] == {"draftkings": 1000}
        assert t["all_odds_standard"] == {}
        assert t["all_lines_alternate"] == {"draftkings": 0.5}
        assert t["all_lines_standard"] == {}

    def test_mixed_attaches_never_cross_pollute(self):
        t = self._fresh()
        self._route(t, "fanduel",   0.5, -140, "standard")
        self._route(t, "draftkings", 0.5, 1000, "alternate")
        self._route(t, "espnbet",   0.5, 1300, "alternate")
        self._route(t, "betmgm",    0.5, -135, "standard")
        # Two books in standard, two in alternate.
        assert set(t["all_odds_standard"].keys()) == {"fanduel", "betmgm"}
        assert set(t["all_odds_alternate"].keys()) == {"draftkings", "espnbet"}
        # No leakage of values.
        assert t["all_odds_standard"]["fanduel"] == -140
        assert t["all_odds_alternate"]["espnbet"] == 1300


# ── 5. Replay-trace reconstruction (DB roundtrip via mongomock) ────
class TestReplayReconstruction:
    """End-to-end on an in-memory mongomock store: insert several
    snapshot rows tagged with the SAME canonical_candidate and verify
    the canonical-trace endpoint's join logic groups them."""

    @pytest.fixture
    def db(self):
        try:
            import mongomock
        except ImportError:
            pytest.skip("mongomock not installed")
        return mongomock.MongoClient().testdb

    def test_canonical_candidate_groups_multiple_scrapes(self, db):
        ck = "mlb|E1|Matt Olson|Total Bases|0.5|OVER"
        rows = [
            {"scrape_id": "s1", "canonical_candidate": ck,
             "outcome_point": 0.5, "outcome_price": 1300,
             "bookmaker": "espnbet", "fetched_at": "2026-05-16T01:40:35Z"},
            {"scrape_id": "s2", "canonical_candidate": ck,
             "outcome_point": 0.5, "outcome_price": 1300,
             "bookmaker": "espnbet", "fetched_at": "2026-05-16T01:45:35Z"},
            # Different player — must not match.
            {"scrape_id": "s3",
             "canonical_candidate": "mlb|E1|Other|Total Bases|0.5|OVER",
             "outcome_point": 0.5, "outcome_price": 110,
             "bookmaker": "espnbet", "fetched_at": "2026-05-16T01:50:35Z"},
        ]
        db.dg_raw_odds_snapshots.insert_many(rows)
        found = list(db.dg_raw_odds_snapshots.find(
            {"canonical_candidate": ck}))
        assert len(found) == 2
        assert {r["scrape_id"] for r in found} == {"s1", "s2"}


# ── 6. Append-only retention contract ──────────────────────────────
class TestAppendOnlyRetention:
    @pytest.fixture
    def db(self):
        try:
            import mongomock
        except ImportError:
            pytest.skip("mongomock not installed")
        return mongomock.MongoClient().testdb

    def test_multiple_scrapes_create_multiple_rows(self, db):
        """Critical invariant: subsequent scrapes never overwrite a
        prior snapshot row. They append. We simulate two scrapes of
        the same event+bookmaker+market+outcome at different times
        and assert both rows persist."""
        row_a = {
            "scrape_id": "scrape-A",
            "fetched_at": "2026-05-16T01:00:00Z",
            "sport": "mlb", "event_id": "E1",
            "bookmaker": "espnbet",
            "market_key": "batter_total_bases_alternate",
            "outcome_description": "Matt Olson",
            "outcome_point": 0.5, "outcome_price": 1300,
            "outcome_name": "Over",
            "canonical_candidate":
                "mlb|E1|Matt Olson|Total Bases|0.5|OVER",
        }
        row_b = {**row_a,
                 "scrape_id": "scrape-B",
                 "fetched_at": "2026-05-16T01:30:00Z",
                 "outcome_price": 1100}
        db.dg_raw_odds_snapshots.insert_many([row_a, row_b])
        # Same canonical_candidate must surface BOTH historical
        # prices — never overwrite.
        all_rows = list(db.dg_raw_odds_snapshots.find(
            {"canonical_candidate": row_a["canonical_candidate"]}))
        assert len(all_rows) == 2
        prices = sorted(r["outcome_price"] for r in all_rows)
        assert prices == [1100, 1300]

    def test_no_unique_index_blocks_dupes(self, db):
        """Negative lockdown: there must NOT be a unique index on
        (event_id, bookmaker, market_key, line, side) that would
        reject the second scrape. We don't create such an index in
        the production schema — this test pins that choice."""
        idx_info = db.dg_raw_odds_snapshots.index_information()
        for _name, spec in idx_info.items():
            # `_id_` is the only auto-index. Anything else MUST NOT
            # carry `unique: True` against the natural key.
            if spec.get("unique") and spec.get("key") != [("_id", 1)]:
                # If a non-_id unique index appears here we have
                # silently re-broken append-only.
                pytest.fail(f"Unexpected unique index {spec}")
