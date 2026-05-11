"""
Regression tests — Caesars (williamhill_us) extension of the
universal reference-odds chain  (2026-05-11).

Pins the NEW preference order:

  NBA: DK → FD → MGM → CSR → BOL → none
  MLB: DK+FD consensus → DK → FD → MGM → CSR → BOL → none

`csr_layer` is a keyword-only parameter on `_pick_reference_odds`
with default `None`, so the 4-book existing test suite still passes
unmodified — this file adds the new-position-specific cases.

Each test pins ONE call-site behaviour. If a future refactor changes
the chain ordering, multiple of these will fail with a clear message
identifying which book got demoted / promoted.
"""
from __future__ import annotations

import pytest

from services.scoring.scoring_stack import _pick_reference_odds


def L(odds):
    """Build a minimal book layer dict the function understands."""
    return None if odds is None else {"odds": odds}


# ---------------------------------------------------------------------------
# Caesars priority slot  — slot 4 of 5 (between MGM and BOL).
# ---------------------------------------------------------------------------
def test_nba_csr_used_when_dk_fd_mgm_missing():
    """Caesars promoted to reference book when DK, FD, MGM all absent
    but BOL is also present — chain must pick CSR ahead of BOL."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=L(+155), bol_layer=L(-118), sport="nba",
    )
    assert (odds, book) == (+155, "csr")


def test_nba_csr_NOT_used_when_mgm_present():
    """MGM still beats Caesars — Caesars sits at slot 4."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(-108),
        csr_layer=L(+155), bol_layer=L(-118), sport="nba",
    )
    assert (odds, book) == (-108, "mgm")


def test_nba_csr_falls_through_to_bol_when_csr_missing():
    """BOL must remain the final reference book when CSR layer absent."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=None, bol_layer=L(-118), sport="nba",
    )
    assert (odds, book) == (-118, "bol")


def test_nba_csr_alone_returns_csr():
    """Caesars is the only book with a price → 'csr'."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=L(+185), bol_layer=L(None), sport="nba",
    )
    assert (odds, book) == (+185, "csr")


def test_nba_csr_default_none_preserves_legacy_chain():
    """Calls that pre-date the 2026-05-11 patch (no `csr_layer=` kw)
    must continue to work identically — DK→FD→MGM→BOL fallback."""
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None),
        mgm_layer=L(None), bol_layer=L(-130),
        sport="nba",
        # NO csr_layer kwarg at all.
    )
    assert (odds, book) == (-130, "bol")


# ---------------------------------------------------------------------------
# MLB chain — Caesars sits between MGM and BOL too. Consensus path is
# unaffected (it only fires when BOTH DK and FD are present, which
# trumps Caesars regardless).
# ---------------------------------------------------------------------------
def test_mlb_csr_used_when_dk_fd_mgm_missing():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=L(-140), bol_layer=L(-160), sport="mlb",
    )
    assert (odds, book) == (-140, "csr")


def test_mlb_consensus_still_wins_over_csr():
    """Caesars must NOT short-circuit the DK+FD consensus path."""
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), fd_layer=L(-115),  # consensus path fires
        mgm_layer=L(-105), csr_layer=L(-140), bol_layer=L(-160),
        sport="mlb",
    )
    assert book == "consensus"
    # Consensus mid-point should land between -110 and -115.
    assert -116 <= odds <= -110


def test_mlb_csr_not_used_when_dk_only():
    """DK-only fallback must still pick 'dk' — Caesars never trumps DK."""
    odds, book = _pick_reference_odds(
        dk_layer=L(-110), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=L(-140), bol_layer=L(None), sport="mlb",
    )
    assert (odds, book) == (-110, "dk")


# ---------------------------------------------------------------------------
# `none` boundary — Caesars missing alongside everything else must
# return ("none", None) untouched.
# ---------------------------------------------------------------------------
def test_nba_all_missing_including_csr_returns_none():
    odds, book = _pick_reference_odds(
        dk_layer=L(None), fd_layer=L(None), mgm_layer=L(None),
        csr_layer=L(None), bol_layer=L(None), sport="nba",
    )
    assert (odds, book) == (None, "none")
