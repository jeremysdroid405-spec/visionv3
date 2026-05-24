"""
Pin the MLB market→family map extension so HRR / singles / batter_walks
/ pitcher_hits_allowed / pitcher_outs / HR / SB / doubles never silently
disappear from the replay pipeline again.

The 2026-05-24 audit on prod found 7 markets present in raw odds but
silently dropped before the replay cache. Root cause was
`_STAT_FAMILY_MAP` in `services/replay/mlb_feature_cache.py` missing
batter_singles / batter_walks / HR / SB / doubles. These tests lock
the canonical SSOT family token for each market so a future refactor
can't regress the coverage.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from services.replay.mlb_feature_cache import (
    _STAT_FAMILY_MAP,
    _CANONICAL_FAMILY_TO_MODEL_KEY,
    market_to_stat_family,
    family_to_model_key,
)
from services.mlb_high_friction_model import MLBHighFrictionModel


# Markets the user expects to surface in optimizer sweeps.
REQUIRED_MARKETS = {
    "batter_hits":             "hits",
    "batter_total_bases":      "total_bases",
    "batter_rbis":             "rbis",
    "batter_runs_scored":      "runs",
    "batter_hits_runs_rbis":   "hits_runs_rbis",
    "batter_strikeouts":       "batter_strikeouts",
    "batter_singles":          "singles",
    "batter_walks":            "batter_walks",
    "batter_home_runs":        "home_runs",
    "batter_stolen_bases":     "stolen_bases",
    "batter_doubles":          "doubles",
    "pitcher_strikeouts":      "pitcher_strikeouts",
    "pitcher_hits_allowed":    "hits_allowed",
    "pitcher_walks":           "walks_allowed",
    "pitcher_earned_runs":     "earned_runs",
    "pitcher_outs":            "pitching_outs",
}


def test_every_required_market_maps_to_a_family():
    """Every market that SGO returns for MLB must produce a canonical
    family token via `market_to_stat_family`. If not, the runner
    silently drops the row → optimizer never sees those bets."""
    misses = []
    for market, expected_family in REQUIRED_MARKETS.items():
        got = market_to_stat_family(market)
        if got != expected_family:
            misses.append(f"{market!r}: expected {expected_family!r}, "
                              f"got {got!r}")
    assert not misses, "Market→family map regressions:\n" + "\n".join(misses)


def test_alternate_markets_also_resolve():
    """`batter_singles_alternate` etc. must resolve identically to the
    base market — the runner strips `_alternate` before lookup."""
    for market, expected_family in REQUIRED_MARKETS.items():
        alt = f"{market}_alternate"
        got = market_to_stat_family(alt)
        assert got == expected_family, (
            f"{alt!r}: expected {expected_family!r}, got {got!r}")


def test_every_required_family_has_a_model_key_the_model_supports():
    """Every market we accept must reach a model the live MLB-HF
    model can actually score. If the model key is missing from
    `MLB_STAT_TYPES`, the engine returns None and the row is dropped
    silently."""
    supported_models = set(MLBHighFrictionModel.MLB_STAT_TYPES)
    misses = []
    for market, family in REQUIRED_MARKETS.items():
        model_key = family_to_model_key(family)
        if model_key not in supported_models:
            misses.append(f"{market!r} → family={family!r} → "
                              f"model_key={model_key!r} not in model "
                              f"({sorted(supported_models)})")
    assert not misses, "Model coverage gaps:\n" + "\n".join(misses)


def test_batter_walks_specifically_uses_walks_model():
    """Regression pin — batter_walks must NOT be conflated with
    pitcher_walks. Pitcher walks → `pitcher_walks` model; batter
    walks → `walks` model. The 2026-05-24 fix added this distinction."""
    assert family_to_model_key("batter_walks") == "walks"
    assert family_to_model_key("walks_allowed") == "pitcher_walks"


def test_pitcher_outs_uses_pitcher_outs_model_key():
    """The canonical family is `pitching_outs` (matches SSOT) but the
    model pkl is `mlb_hf_pitcher_outs.pkl`. The translator must bridge."""
    assert family_to_model_key("pitching_outs") == "pitcher_outs"


def test_hits_runs_rbis_uses_legacy_plus_key():
    """The trained pkl uses `hits+runs+rbis` (with plus signs); the
    canonical family is `hits_runs_rbis` (underscores). The translator
    bridges so the model lookup hits."""
    assert family_to_model_key("hits_runs_rbis") == "hits+runs+rbis"
