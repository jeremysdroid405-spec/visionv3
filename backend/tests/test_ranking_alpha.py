"""
Stat-aware α for ranking_score_v2 (2026-04-21)

Locks the contract for services/scoring/recompute.py::_compute_ranking_score_v2:
  * Low-line AST/REB props rank higher than with the legacy single α=0.40
  * High-line PTS/PRA props stay competitive (not collapsed)
  * Unknown stats (MLB/NFL/future) fall back to α = 0.50
  * Backwards compatible signature: stat_type is kwarg-only with default None
"""
from __future__ import annotations

import math
import pytest

from services.scoring.recompute import (
    ALPHA_BY_STAT,
    _DEFAULT_ALPHA,
    _compute_ranking_score_v2,
    _resolve_alpha,
)


# ---------- Alpha lookup -----------------------------------------------------
def test_alpha_map_defaults_and_known_values():
    assert _resolve_alpha("PTS") == 0.40
    assert _resolve_alpha("PRA") == 0.40
    assert _resolve_alpha("AST") == 0.60
    assert _resolve_alpha("REB") == 0.60
    assert _resolve_alpha("3PM") == 0.60
    assert _resolve_alpha("STL") == 0.70
    assert _resolve_alpha("BLK") == 0.70
    assert _resolve_alpha("PTS+REB") == 0.50
    assert _resolve_alpha("PTS+AST") == 0.50
    assert _resolve_alpha("REB+AST") == 0.50


def test_alpha_fallback_is_default_for_unknown_stats():
    assert _resolve_alpha("Hits") == _DEFAULT_ALPHA == 0.50          # MLB
    assert _resolve_alpha("Pitcher Strikeouts") == _DEFAULT_ALPHA    # MLB
    assert _resolve_alpha("passing_yards") == _DEFAULT_ALPHA         # future NFL
    assert _resolve_alpha("totally_fake_stat") == _DEFAULT_ALPHA
    assert _resolve_alpha(None) == _DEFAULT_ALPHA
    assert _resolve_alpha("") == _DEFAULT_ALPHA


# ---------- Formula correctness ---------------------------------------------
def _legacy_ranking(proj, line, rec, p):
    """α = 0.40 everywhere (pre-2026-04-21 behavior)."""
    raw = (proj - line) if rec == "OVER" else (line - proj)
    return round((raw / max(line, 1.0) ** 0.40) * p, 6)


def _new_ranking(proj, line, rec, p, stat):
    return _compute_ranking_score_v2(proj, line, rec, p, stat_type=stat)


def _mixed_slate():
    """Representative mixed-stat slate drawn from the 2026-04-21 NBA board."""
    return [
        # (name, stat, line, proj, p, direction)
        ("Wemby PRA 43.5 U",   "PRA", 43.5, 33.1, 0.90, "UNDER"),
        ("Jokic REB 13.5 U",   "REB", 13.5, 10.1, 0.81, "UNDER"),
        ("Brunson PTS 26.5 U", "PTS", 26.5, 22.9, 0.83, "UNDER"),
        ("Booker PTS 17.5 O",  "PTS", 17.5, 20.1, 0.80, "OVER"),
        ("LeBron PTS 24.5 U",  "PTS", 24.5, 21.0, 0.60, "UNDER"),
        ("Vuc REB 3.5 O",      "REB", 3.5,  5.5,  0.75, "OVER"),
        ("Mitchell AST 1.5",   "AST", 1.5,  2.9,  0.74, "OVER"),
        ("Pritchard AST 2.5",  "AST", 2.5,  3.9,  0.83, "OVER"),
        ("Jokic AST 8.5 O",    "AST", 8.5,  11.2, 0.70, "OVER"),
        ("Hayes REB 2.5",      "REB", 2.5,  3.9,  0.73, "OVER"),
        ("Kawhi 3PM 2.5 O",    "3PM", 2.5,  3.8,  0.70, "OVER"),
        ("Shai STL 1.5",       "STL", 1.5,  2.6,  0.68, "OVER"),
        ("Ant BLK 1.5",        "BLK", 1.5,  2.4,  0.65, "OVER"),
    ]


def _rank_of(name_prefix, fixtures, *, alpha):
    """Return 1-based rank position in descending-score sort.
    alpha=None  -> legacy α=0.40 everywhere
    alpha='stat-aware' -> stat-aware mapping (production)
    """
    scored = []
    for (name, stat, line, proj, p, d) in fixtures:
        if alpha is None:
            s = _legacy_ranking(proj, line, d, p)
        else:
            s = _new_ranking(proj, line, d, p, stat)
        scored.append((name, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    for i, (n, _) in enumerate(scored, 1):
        if n.startswith(name_prefix):
            return i
    raise AssertionError(f"{name_prefix} not found in fixtures")


def test_low_line_AST_improves_rank_vs_legacy():
    """On a realistic mixed slate, AST 1.5 must occupy a STRICTLY BETTER
    rank-position under stat-aware α than under the legacy α=0.40
    everywhere (what matters for Top-10 selection is position, not absolute
    score — the formula is monotonically decreasing in α for line > 1)."""
    fixtures = _mixed_slate()
    legacy_rank = _rank_of("Mitchell AST 1.5", fixtures, alpha=None)        # legacy
    new_rank = _rank_of("Mitchell AST 1.5", fixtures, alpha="stat-aware")
    assert new_rank < legacy_rank, (
        f"AST 1.5 must rank higher position-wise under stat-aware α "
        f"(legacy_rank={legacy_rank}, new_rank={new_rank})"
    )


def test_low_line_REB_improves_rank_vs_legacy():
    fixtures = _mixed_slate()
    legacy_rank = _rank_of("Hayes REB 2.5", fixtures, alpha=None)
    new_rank = _rank_of("Hayes REB 2.5", fixtures, alpha="stat-aware")
    assert new_rank < legacy_rank, (
        f"REB 2.5 must rank higher (legacy_rank={legacy_rank}, new_rank={new_rank})"
    )


def test_tiny_line_STL_BLK_also_improve_rank():
    fixtures = _mixed_slate()
    for label in ("Shai STL 1.5", "Ant BLK 1.5"):
        legacy_rank = _rank_of(label, fixtures, alpha=None)
        new_rank = _rank_of(label, fixtures, alpha="stat-aware")
        assert new_rank <= legacy_rank, (
            f"{label} must not drop under stat-aware α "
            f"(legacy_rank={legacy_rank}, new_rank={new_rank})"
        )


def test_high_line_PTS_not_collapsed():
    # PTS picks keep α=0.40 so their ranking must be IDENTICAL to legacy.
    legacy = _legacy_ranking(24.5, 21.0, "UNDER", 0.60)
    new = _new_ranking(24.5, 21.0, "UNDER", 0.60, "PTS")
    assert new == legacy, f"PTS ranking must not change when α stays at 0.40"


def test_high_line_PRA_not_collapsed():
    legacy = _legacy_ranking(33.1, 43.5, "UNDER", 0.90)
    new = _new_ranking(33.1, 43.5, "UNDER", 0.90, "PRA")
    assert new == legacy, f"PRA ranking must not change when α stays at 0.40"


def test_unknown_stat_uses_default_0_5():
    # α_default=0.5 → denom = line^0.5 (sqrt of line)
    proj, line, p = 3.0, 1.5, 0.8
    expected = round((1.5 / math.sqrt(1.5)) * 0.8, 6)
    got = _new_ranking(proj, line, "OVER", p, "nonexistent_stat")
    assert got == pytest.approx(expected, abs=1e-6)


# ---------- Invariants of the base formula (no regression) ------------------
def test_none_inputs_still_return_none():
    assert _compute_ranking_score_v2(None, 1.5, "OVER", 0.8, stat_type="AST") is None
    assert _compute_ranking_score_v2(2.9, None, "OVER", 0.8, stat_type="AST") is None
    assert _compute_ranking_score_v2(2.9, 1.5, "OVER", None, stat_type="AST") is None


def test_invalid_recommendation_returns_none():
    assert _compute_ranking_score_v2(2.9, 1.5, "PUSH", 0.8, stat_type="AST") is None
    assert _compute_ranking_score_v2(2.9, 1.5, None, 0.8, stat_type="AST") is None


def test_under_direction_flips_gap_sign():
    over = _new_ranking(2.9, 1.5, "OVER", 0.8, "AST")
    under = _new_ranking(2.9, 1.5, "UNDER", 0.8, "AST")
    assert over > 0 and under < 0
    assert over == -under


def test_backwards_compatible_call_signature_without_stat_type():
    # Legacy call (no stat_type) MUST still work and land on default α = 0.50.
    got = _compute_ranking_score_v2(2.9, 1.5, "OVER", 0.8)
    expected = round((1.4 / math.sqrt(1.5)) * 0.8, 6)
    assert got == pytest.approx(expected, abs=1e-6)


# ---------- Top-10 diversity smoke test -------------------------------------
def test_top10_not_monopolized_by_one_stat():
    """Build a realistic mixed slate and assert that under stat-aware α no
    single stat occupies the entire Top-10 ranked list."""
    fixtures = [
        # (name, stat, line, proj, p)
        ("VW  PRA 43.5 U", "PRA", 43.5, 33.1, 0.90),
        ("Jokic REB 13.5 U", "REB", 13.5, 10.1, 0.81),
        ("Brunson PTS 26.5 U", "PTS", 26.5, 22.9, 0.83),
        ("Booker PTS 17.5 O", "PTS", 17.5, 20.1, 0.80),
        ("LeBron PTS 24.5 U", "PTS", 24.5, 21.0, 0.60),
        ("Wemby PTS 28.5 U", "PTS", 28.5, 25.1, 0.78),
        ("Vuc REB 3.5 O", "REB", 3.5, 5.5, 0.75),
        ("Mitchell AST 1.5 O", "AST", 1.5, 2.9, 0.74),
        ("Pritchard AST 2.5 O", "AST", 2.5, 3.9, 0.83),
        ("Jokic AST 8.5 O", "AST", 8.5, 11.2, 0.70),
        ("Hayes REB 2.5 O", "REB", 2.5, 3.9, 0.73),
        ("KAT REB 11.5 U", "REB", 11.5, 8.5, 0.74),
        ("Kawhi 3PM 2.5 O", "3PM", 2.5, 3.8, 0.70),
        ("Shai STL 1.5 O", "STL", 1.5, 2.6, 0.68),
        ("Ant BLK 1.5 O", "BLK", 1.5, 2.4, 0.65),
    ]
    ranked = sorted(
        [(name, stat, _new_ranking(proj, line, "OVER" if proj > line else "UNDER", p, stat))
         for (name, stat, line, proj, p) in fixtures],
        key=lambda x: x[2], reverse=True,
    )
    top10_stats = [r[1] for r in ranked[:10]]
    # At least 3 different stat families in the Top-10 (was often just PTS/PRA).
    assert len(set(top10_stats)) >= 3, f"Top-10 monopolized by {set(top10_stats)}: {ranked[:10]}"


# ---------- Multi-sport future-proofing -------------------------------------
def test_adding_nfl_stats_is_config_change_only():
    """Registering future NFL weights should require NO code changes to the
    ranking function — only a dict update.  Confirm by mutating the map
    in-place and asserting resolver picks it up."""
    try:
        ALPHA_BY_STAT["passing_yards"] = 0.40
        ALPHA_BY_STAT["receptions"] = 0.65
        assert _resolve_alpha("passing_yards") == 0.40
        assert _resolve_alpha("receptions") == 0.65
        got = _new_ranking(250, 225.5, "OVER", 0.72, "passing_yards")
        # Sanity: non-None and positive
        assert got is not None and got > 0
    finally:
        ALPHA_BY_STAT.pop("passing_yards", None)
        ALPHA_BY_STAT.pop("receptions", None)
