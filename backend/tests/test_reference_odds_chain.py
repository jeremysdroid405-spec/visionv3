"""Regression tests for the 2026-05-09 NBA reference-odds chain port.

Locks down `_pick_reference_odds` behaviour for both NBA and MLB:

  • NBA preference order: DK → FD → MGM → BOL → none
  • NBA does NOT use DK+FD consensus (gates were single-book-calibrated)
  • MLB preference order: DK+FD consensus → DK → FD → MGM → BOL → none
  • PrizePicks is NEVER a reference book
  • Sharp/Pinnacle is NEVER a reference book

These tests are the safety net that proves the fix exists and prevents
silent regressions: a future refactor that drops FD/BOL from the NBA
chain will fail multiple of these tests and surface immediately.
"""
from __future__ import annotations

import pytest

from services.scoring.scoring_stack import _pick_reference_odds


def L(odds):
    """Build a minimal book layer dict the function understands."""
    return None if odds is None else {"odds": odds}


# ---------------------------------------------------------------------------
# 1. NBA chain — primary behaviour
# ---------------------------------------------------------------------------
def test_nba_dk_preferred_over_fd_mgm_bol():
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), mgm_layer=L(-105),
        fd_layer=L(-115), bol_layer=L(-120), sport="nba",
    )
    assert (odds, book) == (-110, "dk")


def test_nba_fd_used_when_dk_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(-105),
        fd_layer=L(-115), bol_layer=L(-120), sport="nba",
    )
    assert (odds, book) == (-115, "fd")


def test_nba_mgm_used_when_dk_and_fd_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(-105),
        fd_layer=L(None), bol_layer=L(-120), sport="nba",
    )
    assert (odds, book) == (-105, "mgm")


def test_nba_bol_used_when_dk_fd_mgm_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(None), bol_layer=L(-120), sport="nba",
    )
    assert (odds, book) == (-120, "bol")


def test_nba_returns_none_when_all_four_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(None), bol_layer=L(None), sport="nba",
    )
    assert (odds, book) == (None, "none")


def test_nba_does_not_use_dk_fd_consensus():
    """NBA gates were calibrated against single-book reference odds.
    A consensus path would silently change routing on already-tiered
    props. NBA must NOT compute DK+FD consensus (only MLB does)."""
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), mgm_layer=L(None),
        fd_layer=L(-130), bol_layer=L(None), sport="nba",
    )
    assert book == "dk"
    assert odds == -110


def test_nba_handles_none_layers():
    """Defensive: passing `None` (rather than `{"odds": None}`) for
    a layer must not crash."""
    odds, book = _pick_reference_odds(
        dk_layer=None, mgm_layer=None,
        fd_layer={"odds": -125}, bol_layer=None, sport="nba",
    )
    assert (odds, book) == (-125, "fd")


# ---------------------------------------------------------------------------
# 2. NBA — historical-behaviour preservation (already-tiered props)
# ---------------------------------------------------------------------------
def test_nba_dk_only_tiered_props_unchanged_post_port():
    """A prop that previously qualified with `tier_reference_book=dk`
    MUST still resolve to (dk_odds, "dk") after the port. This is the
    primary regression guard for the 56 already-tiered NBA props."""
    odds, book = _pick_reference_odds(
        dk_layer=L(-150), mgm_layer=L(None),
        fd_layer=L(None), bol_layer=L(None), sport="nba",
    )
    assert book == "dk"
    assert odds == -150


def test_nba_mgm_only_tiered_props_unchanged_post_port():
    """Likewise for the 92 NBA props previously routed via MGM."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(-160),
        fd_layer=L(None), bol_layer=L(None), sport="nba",
    )
    assert book == "mgm"
    assert odds == -160


# ---------------------------------------------------------------------------
# 3. MLB — chain unchanged by this patch
# ---------------------------------------------------------------------------
def test_mlb_dk_fd_consensus_when_both_present():
    """MLB must still produce DK+FD consensus when both books quote."""
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), mgm_layer=L(None),
        fd_layer=L(-110), bol_layer=L(None), sport="mlb",
    )
    assert book == "consensus"
    # Symmetric -110/-110 → consensus implied prob 0.5238 → ~-110
    assert -120 <= odds <= -100


def test_mlb_dk_only_when_fd_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), mgm_layer=L(-115),
        fd_layer=L(None), bol_layer=L(-120), sport="mlb",
    )
    assert (odds, book) == (-110, "dk")


def test_mlb_fd_used_when_dk_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(-115),
        fd_layer=L(-130), bol_layer=L(-120), sport="mlb",
    )
    assert (odds, book) == (-130, "fd")


def test_mlb_mgm_used_when_dk_fd_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(-115),
        fd_layer=L(None), bol_layer=L(-120), sport="mlb",
    )
    assert (odds, book) == (-115, "mgm")


def test_mlb_bol_used_when_dk_fd_mgm_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(None), bol_layer=L(-120), sport="mlb",
    )
    assert (odds, book) == (-120, "bol")


def test_mlb_returns_none_when_all_four_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(None), bol_layer=L(None), sport="mlb",
    )
    assert (odds, book) == (None, "none")


# ---------------------------------------------------------------------------
# 4. Cross-sport mutation guard — chains must stay aligned post-fix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "scenario,expected_nba,expected_mlb",
    [
        # All four present
        ((L(-110), L(-115), L(-120), L(-125)), ("dk", -110), ("consensus", None)),
        # No DK
        ((L(None), L(-115), L(-120), L(-125)), ("fd", -120), ("fd", -120)),
        # No DK or FD
        ((L(None), L(-115), L(None), L(-125)), ("mgm", -115), ("mgm", -115)),
        # Only BOL
        ((L(None), L(None),  L(None), L(-125)), ("bol", -125), ("bol", -125)),
        # Nothing
        ((L(None), L(None),  L(None), L(None)), ("none", None), ("none", None)),
    ],
)
def test_nba_and_mlb_chains_aligned(scenario, expected_nba, expected_mlb):
    """Once DK is missing the two chains must produce identical book
    labels (FD → MGM → BOL → none). This makes regressions in EITHER
    chain visible without touching the other."""
    dk, mgm, fd, bol = scenario
    odds_nba, book_nba = _pick_reference_odds(
        dk_layer=dk, mgm_layer=mgm, fd_layer=fd, bol_layer=bol, sport="nba",
    )
    odds_mlb, book_mlb = _pick_reference_odds(
        dk_layer=dk, mgm_layer=mgm, fd_layer=fd, bol_layer=bol, sport="mlb",
    )
    assert book_nba == expected_nba[0]
    if expected_nba[1] is not None:
        assert odds_nba == expected_nba[1]
    assert book_mlb == expected_mlb[0]
    if expected_mlb[1] is not None:
        assert odds_mlb == expected_mlb[1]


def test_pp_never_a_reference_book_invariant():
    """The function signature does not even accept a PP layer — this
    test pins that contract so a future refactor can't add one
    without an explicit review."""
    import inspect
    sig = inspect.signature(_pick_reference_odds)
    forbidden_params = {"pp_layer", "prizepicks_layer", "sharp_layer", "pinnacle_layer"}
    assert set(sig.parameters) & forbidden_params == set()


def test_default_sport_uses_nba_chain():
    """Unknown / missing sport → NBA chain (default branch)."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(-130), bol_layer=L(None), sport=None,
    )
    assert (odds, book) == (-130, "fd")
    odds, book = _pick_reference_odds(
        dk_layer=L(None), mgm_layer=L(None),
        fd_layer=L(-130), bol_layer=L(None), sport="nfl",
    )
    assert (odds, book) == (-130, "fd")
