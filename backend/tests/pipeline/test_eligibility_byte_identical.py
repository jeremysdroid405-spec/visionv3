"""Pipeline Phase A — Byte-identical regression tests.

Goal: PROVE that the SSOT `apply_production_eligibility` produces
exactly the same output as the previous inline eligibility chain
(`filter_priceable` + `build_companion_map` + `filter_pp_playable`)
that lived directly inside `MLBScoringAdapter.load_live_props` and
`NBAScoringAdapter.load_live_props`.

Any behavioural drift between the SSOT and the inline chain is a
regression — both code paths MUST be bit-identical on every prop
field that downstream scoring / gates / cards read.
"""
from __future__ import annotations
import copy
import sys

sys.path.insert(0, "/app/backend")

import pytest

from services.scoring.coverage_filter import (
    filter_priceable, filter_pp_playable,
)
from services.scoring.tp_engine import build_companion_map
from services.pipeline.eligibility import (
    apply_production_eligibility,
)


def _live_prop(**kw):
    """Minimal prop shape matching `{sport}_live_props` rows.

    Sportsbook anchors use the legacy flat-field names the
    `coverage_filter._BOOK_FIELDS` table inspects
    (`draftkings_price`, `fanduel_price`, etc).
    """
    base = dict(
        sport="mlb",
        player_name="Matt Olson",
        player_name_normalized="matt olson",
        event_id="evt-2026-05-17-NYM-ATL",
        stat_type="Total Bases",
        stat_family="total_bases",
        recommendation="OVER",
        side="OVER",
        line=1.5,
        playable_on_pp=True,
        pp_layer={"book": "prizepicks", "line": 1.5, "odds": 100},
        # Anchors (legacy flat field names per _BOOK_FIELDS):
        draftkings_price=-180,
        fanduel_price=-175,
    )
    base.update(kw)
    return base


# ── Helper: stand-alone inline reproduction of the prior chain ──
def _inline_eligibility_chain(props, *, sport):
    """The exact previous inline chain (verbatim) — used as the
    golden reference for the SSOT regression."""
    priceable, coverage_stats = filter_priceable(props, sport=sport)
    companion_map = build_companion_map(props)
    pp_playable, pp_stats = filter_pp_playable(priceable, sport=sport)
    return pp_playable, coverage_stats, pp_stats, companion_map


# ── Byte-identical regression: every output must match the inline ──
@pytest.mark.parametrize("sport", ["mlb", "nba"])
def test_eligibility_byte_identical_to_inline_chain(sport):
    raw = [
        _live_prop(player_name="A", player_name_normalized="a",
                    playable_on_pp=True),
        _live_prop(player_name="B", player_name_normalized="b",
                    side="UNDER", recommendation="UNDER",
                    playable_on_pp=True,
                    draftkings_price=+170, fanduel_price=None),
        _live_prop(player_name="C", player_name_normalized="c",
                    playable_on_pp=False, pp_layer=None),    # PP-dropped
        _live_prop(player_name="D", player_name_normalized="d",
                    draftkings_price=None, fanduel_price=None,
                    sharp_market=None,
                    pp_layer={"book": "prizepicks",
                              "line": 1.5, "odds": 100}),    # pp_only → priceable drop
    ]
    # The filters mutate in place. Use deepcopy so the golden and SSOT
    # runs operate on independent objects.
    golden_input = copy.deepcopy(raw)
    ssot_input = copy.deepcopy(raw)

    golden_props, golden_cov, golden_pp, golden_cm = _inline_eligibility_chain(
        golden_input, sport=sport,
    )
    ssot_result = apply_production_eligibility(
        ssot_input, sport=sport, use_pp_registry_fallback=False,
    )

    # Same surviving prop set (identity preserved, in-place stamps OK)
    assert len(ssot_result.props) == len(golden_props)
    # Field-by-field equality — every key must match.
    for s, g in zip(ssot_result.props, golden_props):
        assert s == g, f"row drift between SSOT and inline: {s} vs {g}"

    # Coverage / PP stats dicts must match.
    assert ssot_result.coverage_stats == golden_cov
    assert ssot_result.pp_playable_stats == golden_pp

    # Companion map must match.
    assert ssot_result.companion_map == golden_cm


def test_eligibility_input_props_mutated_inplace_like_inline():
    """The legacy inline chain MUTATED input props in place
    (`book_count`, `coverage_class`, `books_anchored` stamped on every
    survivor). The SSOT MUST preserve this behaviour because
    downstream code reads these fields directly from the surviving
    list."""
    raw = [_live_prop()]
    raw_copy = copy.deepcopy(raw)

    apply_production_eligibility(raw, sport="mlb")

    # After the call, the survivor in `raw` must carry the coverage
    # stamps. (The list still IS raw — same object identity.)
    survivor = raw[0]
    assert "book_count" in survivor
    assert "coverage_class" in survivor
    assert "books_anchored" in survivor
    # The original input (deep-copied) does NOT carry the stamps.
    assert "book_count" not in raw_copy[0]


def test_pp_registry_fallback_only_runs_when_flag_set():
    """Live callers pass `use_pp_registry_fallback=False`. The
    registry MUST NOT stamp anything on live props (which already
    carry `playable_on_pp` set by the sync)."""
    raw = [_live_prop(playable_on_pp=True)]
    result = apply_production_eligibility(
        raw, sport="mlb", use_pp_registry_fallback=False,
    )
    assert result.pp_registry_fallback_applied == 0


def test_pp_registry_fallback_stamps_when_flag_set_and_missing():
    """When `use_pp_registry_fallback=True` AND a prop carries neither
    `playable_on_pp` nor `pp_layer`, the registry decides."""
    # rbis UNDER → PP rejects it (rbis is OVER-only)
    rbis_under = _live_prop(
        stat_family="rbis", stat_type="RBIs",
        side="UNDER", recommendation="UNDER",
        line=0.5, playable_on_pp=None, pp_layer=None,
    )
    # hits UNDER → PP accepts (hits is both sides)
    hits_under = _live_prop(
        stat_family="hits", stat_type="Hits",
        side="UNDER", recommendation="UNDER",
        line=1.5, playable_on_pp=None, pp_layer=None,
    )
    raw = [rbis_under, hits_under]
    result = apply_production_eligibility(
        raw, sport="mlb", use_pp_registry_fallback=True,
    )
    assert result.pp_registry_fallback_applied == 2
    # rbis UNDER must be dropped at filter_pp_playable.
    families_remaining = {p.get("stat_family") for p in result.props}
    assert "rbis" not in families_remaining
    assert "hits" in families_remaining


def test_pp_registry_does_not_override_existing_playable_on_pp():
    """Even when the registry fallback flag is set, an existing
    `playable_on_pp` (set by the live sync) MUST NOT be overridden."""
    rbis_under_marked_playable = _live_prop(
        stat_family="rbis", stat_type="RBIs",
        side="UNDER", recommendation="UNDER",
        playable_on_pp=True,    # explicitly set — trust it
    )
    raw = [rbis_under_marked_playable]
    result = apply_production_eligibility(
        raw, sport="mlb", use_pp_registry_fallback=True,
    )
    # Even though registry says rbis UNDER is NOT playable, the
    # explicit True wins.
    assert result.pp_registry_fallback_applied == 0
    assert len(result.props) == 1
