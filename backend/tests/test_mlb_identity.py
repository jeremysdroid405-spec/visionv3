"""
Tests for services.mlb.identity — name-normalization correctness.

Run:
    cd /app/backend && python -m pytest tests/test_mlb_identity.py -v
"""
import sys
sys.path.insert(0, "/app/backend")

from services.mlb.identity import (
    normalize_player_name, apply_alias, string_similarity,
)


# ---------------------------------------------------------------------------
# normalize_player_name
# ---------------------------------------------------------------------------
def test_accent_strip_acuna():
    assert normalize_player_name("Ronald Acuña Jr.") == "ronald acuna"


def test_accent_strip_garcia_with_suffix():
    assert normalize_player_name("Luis García Jr.") == "luis garcia"


def test_suffix_jr():
    assert normalize_player_name("Vladimir Guerrero Jr.") == "vladimir guerrero"


def test_suffix_sr():
    assert normalize_player_name("Ken Griffey Sr.") == "ken griffey"


def test_suffix_iii():
    assert normalize_player_name("Cedric Mullins III") == "cedric mullins"


def test_suffix_ii():
    assert normalize_player_name("Bobby Bonilla II") == "bobby bonilla"


def test_suffix_iv():
    assert normalize_player_name("Test Player IV") == "test player"


def test_initials_periods():
    """Periods inside initials must be removed — 'J.D.' → 'jd'."""
    assert normalize_player_name("J.D. Martinez") == "jd martinez"


def test_initials_no_period():
    assert normalize_player_name("JD Martinez") == "jd martinez"


def test_apostrophe_oneill():
    assert normalize_player_name("Tyler O'Neill") == "tyler oneill"


def test_apostrophe_dorourke():
    assert normalize_player_name("Ryan O'Rourke") == "ryan orourke"


def test_apostrophe_dapostrophe_lower():
    """Names like d'Arnaud must drop the apostrophe AND keep the d."""
    assert normalize_player_name("Travis d'Arnaud") == "travis darnaud"


def test_hyphen_lopez_marin():
    """Hyphens are punctuation → become space → collapsed normally."""
    n = normalize_player_name("Otto Lopez-Marin")
    assert n == "otto lopez marin"


def test_combined_accent_apostrophe_suffix():
    """Edge case stacking: accent + apostrophe + Jr."""
    n = normalize_player_name("Luis O'Brién Jr.")
    assert n == "luis obrien"


def test_extra_whitespace():
    assert normalize_player_name("  Mike   Trout  ") == "mike trout"


def test_empty_returns_none():
    assert normalize_player_name("") is None
    assert normalize_player_name("   ") is None
    assert normalize_player_name(None) is None


def test_only_suffix_returns_none():
    """Pathological: only "Jr." with nothing else → None (don't crash)."""
    assert normalize_player_name("Jr.") is None


def test_chadwick_first_name_with_space():
    """Chadwick stores 'J.P.' as first='j p' (literal space). The
    normalizer must fold adjacent single-letter tokens so 'J.P. Crawford'
    on the live side and 'j p crawford' from Chadwick produce the
    SAME canonical key."""
    a = normalize_player_name("J.P. Crawford")
    b = normalize_player_name("j p crawford")
    assert a == b == "jp crawford"


def test_three_initials_fold():
    """A.J. Pollock-style triple initials all glom into one token."""
    assert normalize_player_name("A.J. Pollock") == "aj pollock"


def test_idempotent():
    """Normalizing twice equals normalizing once."""
    once = normalize_player_name("Ronald Acuña Jr.")
    twice = normalize_player_name(once)
    assert once == twice


def test_unicode_japanese():
    """Unidecode should romanize Japanese name (Shohei Ohtani is already
    ASCII, so this targets Murakami / Suzuki forms)."""
    assert normalize_player_name("Shōhei Ohtani") == "shohei ohtani"


# ---------------------------------------------------------------------------
# apply_alias
# ---------------------------------------------------------------------------
def test_alias_passthrough_when_no_entry():
    assert apply_alias("ronald acuna") == "ronald acuna"


def test_alias_handles_none():
    assert apply_alias(None) is None
    assert apply_alias("") == ""


# ---------------------------------------------------------------------------
# string_similarity
# ---------------------------------------------------------------------------
def test_similarity_identical():
    assert string_similarity("ronald acuna", "ronald acuna") == 1.0


def test_similarity_different():
    assert string_similarity("ronald acuna", "mike trout") < 0.5


def test_similarity_close():
    """Two near-identical strings should score >= 0.9."""
    assert string_similarity("luis garcia", "luis garcía") > 0.9


def test_similarity_none_safe():
    assert string_similarity(None, "x") == 0.0
    assert string_similarity("x", None) == 0.0
