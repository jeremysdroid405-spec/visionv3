"""
Book policy enforcement tests — 2026-06-03 audit follow-up.

User directive:
  "Remove all those books that you listed in step 3 except Caesars
   and prize picks. Confirm prize picks is used for reference only
   and that nothing from that prize picks book is entering the model
   or used in any kind of mathematical equation."

These tests are the runtime contract guarding the directive. They
will fail if a future refactor accidentally re-admits a blocked book
to the math layer, or routes PrizePicks pricing into any aggregation.
"""
import pytest

from services.team_policy import BLOCKED_BOOKS, REFERENCE_ONLY_BOOKS
from services.scoring.tp_engine import _BOOKS, _OPP_FIELDS


# ── 1. PrizePicks is reference-only, NEVER in math ───────────────────
def test_prizepicks_is_reference_only_never_in_blocked():
    assert "prizepicks" in REFERENCE_ONLY_BOOKS, (
        "prizepicks must remain REFERENCE_ONLY — kept for line-pool "
        "anchor labeling only"
    )
    assert "prizepicks" not in BLOCKED_BOOKS, (
        "prizepicks is reference-only (kept for line anchoring), not "
        "fully blocked"
    )


def test_prizepicks_not_in_scoring_tp_books_tuple():
    """The single biggest contamination risk: PrizePicks pricing
    entering the `_BOOKS` tuple → devig / fair-prob / best_book all
    pull PP odds. This must never happen."""
    book_codes = {short.upper() for (_, _, short) in _BOOKS}
    forbidden_pp_codes = {"PP", "PRIZEPICKS", "UD", "UNDERDOG"}
    leak = book_codes & forbidden_pp_codes
    assert not leak, (
        f"REFERENCE_ONLY books leaked into _BOOKS math tuple: {leak}"
    )
    # Also check legacy / universal field names
    for legacy, universal, _ in _BOOKS:
        for kw in ("prizepicks", "underdog", "pp_", "ud_"):
            assert kw not in legacy.lower(), (
                f"_BOOKS legacy field `{legacy}` references "
                f"reference-only book `{kw}`"
            )
            assert kw not in universal.lower(), (
                f"_BOOKS universal field `{universal}` references "
                f"reference-only book `{kw}`"
            )


def test_underdog_not_in_scoring_tp_books_tuple():
    """Underdog is also DFS pick'em — same fixed-payout problem."""
    book_codes = {short.upper() for (_, _, short) in _BOOKS}
    assert "UD" not in book_codes
    assert "UNDERDOG" not in book_codes


# ── 2. Blocked books removed from math entirely ──────────────────────
def test_blocked_books_not_in_scoring_tp_books_tuple():
    """User-blocked books (betparx, betonline, betrivers, ballybet,
    fliff + 40+ international) must never reach the devig / fair-prob
    / best_book pipeline."""
    book_short_codes = {short.upper() for (_, _, short) in _BOOKS}
    book_legacy_fields = {legacy.lower() for (legacy, _, _) in _BOOKS}

    # Specific tier-3 / wild-pricing US books that must be GONE from math:
    expected_removed = {
        "BOL", "BRV", "PRX", "BLY", "FLF",  # short codes
    }
    leak = book_short_codes & expected_removed
    assert not leak, (
        f"Blocked books still present in scoring _BOOKS tuple: {leak}"
    )

    # Verify legacy field names too
    forbidden_legacy = {
        "betparx_price", "betonline_price", "betrivers_price",
        "ballybet_price", "fliff_price",
    }
    leak_legacy = book_legacy_fields & forbidden_legacy
    assert not leak_legacy, (
        f"Blocked books still present as legacy fields: {leak_legacy}"
    )


def test_blocked_books_not_in_scoring_opp_fields():
    """_OPP_FIELDS feeds the per-book OVER/UNDER de-vig pairing.
    Blocked books must not appear here either."""
    forbidden_codes = {"BOL", "BRV", "PRX", "BLY", "FLF"}
    leak = forbidden_codes & set(_OPP_FIELDS.keys())
    assert not leak, (
        f"Blocked books still in _OPP_FIELDS: {leak}"
    )


def test_only_approved_us_books_in_scoring_math():
    """Positive check — exactly the 6 approved US books are in math."""
    approved = {"DK", "FD", "MGM", "CSR", "EB", "HRB"}
    book_codes = {short.upper() for (_, _, short) in _BOOKS}
    assert book_codes == approved, (
        f"_BOOKS must be exactly {approved}, got {book_codes}"
    )
    assert set(_OPP_FIELDS.keys()) == approved, (
        f"_OPP_FIELDS must be exactly {approved}, "
        f"got {set(_OPP_FIELDS.keys())}"
    )


# ── 3. BLOCKED_BOOKS set covers all step-3 audit findings ────────────
def test_blocked_books_includes_tier3_us_books():
    """The 2026-06-03 audit flagged these US books for removal."""
    for b in ("betparx", "betonline", "betonlineag", "betrivers",
              "ballybet", "fliff"):
        assert b in BLOCKED_BOOKS, (
            f"`{b}` must be in BLOCKED_BOOKS per Odds API audit"
        )


def test_blocked_books_includes_international():
    """50+ international books in `team_live_props` were a major
    contamination source. Spot-check the most common offenders."""
    for b in ("1xbet", "888sport", "sportsbet", "playup",
              "livescorebet", "leovegas", "casumo", "virginbet",
              "tabtouch", "grosvenor", "thescorebet", "paddypower",
              "ladbrokes", "boylesports", "marathonbet", "nordicbet",
              "matchbook", "bet365", "betfairexchange", "tipico"):
        assert b in BLOCKED_BOOKS, (
            f"international book `{b}` must be in BLOCKED_BOOKS"
        )


def test_approved_books_not_accidentally_blocked():
    """Don't break Caesars / DK / FD / MGM / ESPN BET / Hard Rock /
    PrizePicks."""
    for b in ("draftkings", "fanduel", "betmgm", "caesars",
              "williamhill_us", "espnbet", "hardrockbet", "prizepicks"):
        assert b not in BLOCKED_BOOKS, (
            f"approved book `{b}` was incorrectly added to BLOCKED_BOOKS"
        )


# ── 4. Live ingest gates apply the policy ────────────────────────────
def test_team_live_sync_imports_policy():
    """`team_live_sync_service` must import the policy so its
    bookmaker loop can filter."""
    import services.team_live_sync_service as svc
    src = open(svc.__file__).read()
    assert "BLOCKED_BOOKS" in src, (
        "team_live_sync_service must import BLOCKED_BOOKS"
    )
    assert "REFERENCE_ONLY_BOOKS" in src, (
        "team_live_sync_service must import REFERENCE_ONLY_BOOKS so "
        "DFS pricing never reaches team_live_props"
    )


def test_universal_odds_sync_imports_policy():
    import services.universal_odds_sync as svc
    src = open(svc.__file__).read()
    assert "BLOCKED_BOOKS" in src, (
        "universal_odds_sync must import BLOCKED_BOOKS to gate the "
        "raw-ingest layer"
    )


def test_universal_odds_sync_request_lists_match_policy():
    """The lists of bookmakers we REQUEST from the Odds API should not
    include blocked books. This is the cheapest layer of defence —
    don't even fetch what we can't use."""
    from services.universal_odds_sync import (
        DEFAULT_BOOKMAKERS, MLB_BOOKMAKERS, USER_SHARP_BOOKMAKERS,
    )
    for lst_name, lst in (("DEFAULT_BOOKMAKERS", DEFAULT_BOOKMAKERS),
                          ("MLB_BOOKMAKERS", MLB_BOOKMAKERS),
                          ("USER_SHARP_BOOKMAKERS", USER_SHARP_BOOKMAKERS)):
        leak = [b for b in lst if b.lower() in BLOCKED_BOOKS]
        assert not leak, (
            f"Blocked books in {lst_name}: {leak}"
        )
