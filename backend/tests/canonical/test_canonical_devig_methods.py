"""Phase 6 Phase 4 — Canonical TP / devig audit tests.

Validates the new devig-method preference contract and the audit
fields (`devig_method`, `same_book_pair_count`, `cross_book_pair_count`,
`books_used`, `over_books`, `under_books`) on the CanonicalProp.

Contract under test:
  • SAME-BOOK pair → `devig_method="same_book"`; devig probs come from
    averaging per-book devig of paired quotes.
  • NO same-book pair but BOTH sides have ≥1 quote → `devig_method=
    "cross_book"`; devig probs come from cross-book consensus mean.
  • Only one side has quotes → `devig_method="one_sided"`; devig
    probs are None.
  • Same-book preference must not change the cross-book metric values
    (they are computed independently for audit traceability).
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")

import pytest

from services.canonical.canonical_prop import build_canonical_props


def _row(**kw):
    base = dict(event_id="evt1", player_name="Test Player",
                player_name_normalized="test player",
                market="batter_hits", line=0.5, side="OVER",
                book="draftkings", odds=-200)
    base.update(kw)
    return base


# ── devig_method: same-book preferred when paired quote available ──
def test_devig_method_prefers_same_book_when_paired():
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="draftkings", side="UNDER", odds=+150),  # SAME-book pair
        _row(book="fanduel",    side="OVER",  odds=-175),  # OVER-only
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.devig_method == "same_book"
    assert cp.same_book_pair_count == 1
    assert cp.cross_book_pair_count == 0   # no disjoint cross-book pair
    assert cp.books_used == ["draftkings"]
    # Same-book devig populated, cross-book ALSO populated (audit
    # traceability — Phase 4 keeps both visible).
    assert cp.same_book_devig_over_probability is not None
    assert cp.cross_book_devig_over_probability is not None
    # Selected probs equal same-book.
    assert cp.devig_over_probability == pytest.approx(
        cp.same_book_devig_over_probability
    )


def test_devig_method_cross_book_when_no_same_book_pair():
    """OVER@DK, UNDER@FD only — no book quotes both sides.
    Engine MUST fall back to cross-book consensus devig."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="fanduel",    side="UNDER", odds=+170),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.devig_method == "cross_book"
    assert cp.same_book_pair_count == 0
    assert cp.cross_book_pair_count == 1   # min(over_only, under_only) = 1
    assert set(cp.books_used) == {"draftkings", "fanduel"}
    # Same-book devig is None (no same-book pair).
    assert cp.same_book_devig_over_probability is None
    assert cp.same_book_devig_under_probability is None
    # Cross-book devig populated.
    assert cp.cross_book_devig_over_probability is not None
    assert cp.cross_book_devig_under_probability is not None
    # Selected probs equal cross-book.
    assert cp.devig_over_probability == pytest.approx(
        cp.cross_book_devig_over_probability
    )


def test_devig_method_one_sided_when_only_one_side():
    rows = [_row(book="draftkings", side="OVER", odds=-180)]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.devig_method == "one_sided"
    assert cp.same_book_pair_count == 0
    assert cp.cross_book_pair_count == 0
    assert cp.books_used == ["draftkings"]
    assert cp.devig_over_probability is None
    assert cp.devig_under_probability is None
    assert cp.same_book_devig_over_probability is None
    assert cp.cross_book_devig_over_probability is None


def test_over_books_under_books_are_sorted():
    rows = [
        _row(book="fanduel",    side="OVER", odds=-170),
        _row(book="betmgm",     side="OVER", odds=-185),
        _row(book="draftkings", side="OVER", odds=-180),
        _row(book="draftkings", side="UNDER", odds=+155),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.over_books == ["betmgm", "draftkings", "fanduel"]
    assert cp.under_books == ["draftkings"]


# ── Same-book devig math correctness ───────────────────────────────
def test_same_book_devig_math_exact():
    """DK OVER -110 / DK UNDER -110 → devig 50/50 exactly."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-110),
        _row(book="draftkings", side="UNDER", odds=-110),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.devig_method == "same_book"
    assert cp.same_book_devig_over_probability == pytest.approx(0.5, abs=1e-6)
    assert cp.same_book_devig_under_probability == pytest.approx(0.5, abs=1e-6)
    # Selected matches same-book.
    assert cp.devig_over_probability == pytest.approx(0.5, abs=1e-6)


def test_same_book_devig_averages_multiple_pairs():
    """Two same-book pairs, distinct vig curves → average of fair
    probs from each pair."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-110),  # devig 50/50
        _row(book="draftkings", side="UNDER", odds=-110),
        _row(book="fanduel",    side="OVER",  odds=-200),  # devig ~71/29
        _row(book="fanduel",    side="UNDER", odds=+160),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.devig_method == "same_book"
    assert cp.same_book_pair_count == 2
    # Selected devig over is in [0.5, 0.71]
    assert 0.5 < cp.devig_over_probability < 0.72


# ── Cross-book pair count semantics ────────────────────────────────
def test_cross_book_pair_count_is_min_of_disjoint_sides():
    """2 OVER-only books + 1 UNDER-only book → min(2,1)=1 cross-book
    pair available."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="fanduel",    side="OVER",  odds=-175),
        _row(book="betmgm",     side="UNDER", odds=+150),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.same_book_pair_count == 0
    assert cp.cross_book_pair_count == 1
    assert cp.devig_method == "cross_book"


def test_cross_book_pair_count_excludes_same_book_overlap():
    """Same-book pair on DK + cross-book pair (FD-over / MGM-under).
    same_book_pair_count=1; cross_book_pair_count uses disjoint sets
    only → over_only={FD}, under_only={MGM} → 1."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="draftkings", side="UNDER", odds=+150),
        _row(book="fanduel",    side="OVER",  odds=-175),
        _row(book="betmgm",     side="UNDER", odds=+160),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    assert cp.same_book_pair_count == 1
    assert cp.cross_book_pair_count == 1
    assert cp.devig_method == "same_book"


# ── Output stamping wiring contract ────────────────────────────────
def test_canonical_prop_carries_phase4_audit_fields():
    """Smoke-check that the CanonicalProp dataclass surfaces every
    Phase 4 audit field the runner stamps on output docs."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="fanduel",    side="UNDER", odds=+170),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    for attr in (
        "devig_method", "same_book_pair_count", "cross_book_pair_count",
        "books_used", "over_books", "under_books",
        "same_book_devig_over_probability", "same_book_devig_under_probability",
        "cross_book_devig_over_probability", "cross_book_devig_under_probability",
    ):
        assert hasattr(cp, attr), f"CanonicalProp missing Phase 4 attr {attr}"


def test_legacy_devig_field_aliases_selected_value():
    """Backwards compatibility: `devig_over_probability` and
    `devig_under_probability` must always equal the SELECTED method's
    devig probs (was previously hardcoded to cross-book)."""
    rows = [
        _row(book="draftkings", side="OVER",  odds=-110),
        _row(book="draftkings", side="UNDER", odds=-110),
        _row(book="fanduel",    side="OVER",  odds=-200),  # different vig
        _row(book="fanduel",    side="UNDER", odds=+160),
    ]
    cp = build_canonical_props(rows, sport="mlb")[0]
    # Same-book selected. Cross-book devig still computed and DIFFERENT
    # from the same-book selection (proves we're not falsely aliasing).
    assert cp.devig_method == "same_book"
    assert cp.cross_book_devig_over_probability is not None
    # Cross-book ≠ same-book selection in this case (different vig
    # mixing math).
    assert cp.devig_over_probability != pytest.approx(
        cp.cross_book_devig_over_probability, abs=1e-9
    )
