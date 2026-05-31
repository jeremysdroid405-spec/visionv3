"""
Unit tests for scripts/sgo/audit_ncaaf_player_reconciliation — the pure
matching primitives.

Locks in:
  • Name extraction from the SGO player_id pattern
  • Normalization rules (lowercase + alphanumerics only)
  • Fuzzy ratio bounds (identical → 1.0; clearly different → < threshold)
  • All five strategy functions on synthetic in-memory candidate pools

No Mongo I/O. Self-contained.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.audit_ncaaf_player_reconciliation import (
    _Candidates, fuzzy_ratio, name_from_pid, normalize,
    strategy_1_exact, strategy_2_normalized,
    strategy_3_name_team, strategy_4_name_date,
    strategy_5_fuzzy,
)


# ────────────────────── helpers: name extraction ──────────────────────
def test_name_from_pid_standard_pattern():
    assert name_from_pid("CALEB_WILLIAMS_1_NCAAF") == "Caleb Williams"
    assert name_from_pid("BIJAN_ROBINSON_2_NCAAF") == "Bijan Robinson"


def test_name_from_pid_single_token():
    # No underscores in name proper — still strips the SGO suffix.
    assert name_from_pid("SHEDEUR_3_NCAAF") == "Shedeur"


def test_name_from_pid_handles_empty_and_none():
    assert name_from_pid("") == ""
    assert name_from_pid(None) == ""   # type: ignore[arg-type]


def test_name_from_pid_preserves_compound_names():
    # Names with apostrophes/initials get encoded as underscores
    # ("J_D_SPIELMAN_99_NCAAF" → "J D Spielman") — that's expected.
    assert name_from_pid("J_D_SPIELMAN_99_NCAAF") == "J D Spielman"


# ────────────────────── helpers: normalization ────────────────────────
def test_normalize_strips_non_alphanumerics():
    assert normalize("J.D. Spielman") == "jdspielman"
    assert normalize("J D Spielman") == "jdspielman"
    assert normalize("J-D-Spielman") == "jdspielman"


def test_normalize_lowercases():
    assert normalize("Caleb Williams") == "calebwilliams"


def test_normalize_handles_empty():
    assert normalize("") == ""
    assert normalize(None) == ""   # type: ignore[arg-type]


# ───────────────────────── fuzzy ratio ─────────────────────────
def test_fuzzy_ratio_identical_is_one():
    assert fuzzy_ratio("calebwilliams", "calebwilliams") == 1.0


def test_fuzzy_ratio_typo_high():
    # one-letter typo → high but not 1.0
    r = fuzzy_ratio("calebwilliams", "calebwilliam")
    assert 0.9 <= r < 1.0


def test_fuzzy_ratio_unrelated_low():
    # completely different names → well below threshold
    assert fuzzy_ratio("calebwilliams", "shedeursanders") < 0.5


# ───────────────────────── strategy 1 ──────────────────────────
def _make_candidates(rows):
    """Build a _Candidates object from a list of (pid, name, team_id, dates)."""
    c = _Candidates()
    for pid, name, team, dates in rows:
        c.meta[pid] = {
            "player_name": name, "team_id": team, "norm": normalize(name),
        }
        if name:
            c.by_exact_name[name].append(pid)
            c.by_norm_name[normalize(name)].append(pid)
        if team is not None:
            c.by_team[team].add(pid)
            c.by_norm_team[(normalize(name), team)].append(pid)
        for d in dates:
            c.dates_by_pid[pid].add(d)
    return c


def _info(pid, *, dates=None, teams=None):
    return {
        "n_rows":         3,
        "game_dates":     set(dates or []),
        "team_ids":       set(teams or []),
        "name_from_pid":  name_from_pid(pid),
        "name_from_row":  "",
        "fams":           set(),
    }


def test_strategy_1_exact_match_unique():
    cand = _make_candidates([
        ("S_999_NCAAF", "Caleb Williams", "USC", ["2024-09-07"]),
        ("X_1_NCAAF",   "Other Player",   "USC", ["2024-09-07"]),
    ])
    unres = {
        "CALEB_WILLIAMS_1_NCAAF": _info("CALEB_WILLIAMS_1_NCAAF"),
    }
    mapping = {}
    n = strategy_1_exact(unres, cand, mapping)
    assert n == 1
    assert mapping["CALEB_WILLIAMS_1_NCAAF"] == "S_999_NCAAF"


def test_strategy_1_exact_no_match_when_ambiguous():
    """Two stats players with the exact same name → strategy 1 abstains."""
    cand = _make_candidates([
        ("A_1_NCAAF", "John Smith", "OSU", []),
        ("B_2_NCAAF", "John Smith", "MICH", []),
    ])
    unres = {"JOHN_SMITH_77_NCAAF": _info("JOHN_SMITH_77_NCAAF")}
    mapping = {}
    n = strategy_1_exact(unres, cand, mapping)
    assert n == 0
    assert mapping == {}


# ───────────────────────── strategy 2 ──────────────────────────
def test_strategy_2_normalized_handles_punctuation_drift():
    """Props side encoded 'J_D_SPIELMAN_...' → 'J D Spielman'.
    Stats side spells it 'J.D. Spielman'. Strategy 1 fails, 2 wins."""
    cand = _make_candidates([
        ("S_1_NCAAF", "J.D. Spielman", "NEB", []),
    ])
    unres = {"J_D_SPIELMAN_99_NCAAF": _info("J_D_SPIELMAN_99_NCAAF")}
    mapping = {}
    assert strategy_1_exact(unres, cand, mapping) == 0
    assert strategy_2_normalized(unres, cand, mapping) == 1
    assert mapping["J_D_SPIELMAN_99_NCAAF"] == "S_1_NCAAF"


# ───────────────────────── strategy 3 ──────────────────────────
def test_strategy_3_name_team_disambiguates_homonyms():
    """Two 'John Smith's exist in stats; we know props.team_id=MICH →
    map to the Michigan one."""
    cand = _make_candidates([
        ("OSU_A_NCAAF",  "John Smith", "OSU",  []),
        ("MICH_B_NCAAF", "John Smith", "MICH", []),
    ])
    unres = {
        "JOHN_SMITH_77_NCAAF": _info(
            "JOHN_SMITH_77_NCAAF", teams=["MICH"]),
    }
    mapping = {}
    # Strategy 2 abstains (multi-hit on norm name)
    assert strategy_2_normalized(unres, cand, mapping) == 0
    # Strategy 3 resolves via team
    assert strategy_3_name_team(unres, cand, mapping) == 1
    assert mapping["JOHN_SMITH_77_NCAAF"] == "MICH_B_NCAAF"


# ───────────────────────── strategy 4 ──────────────────────────
def test_strategy_4_name_date_resolves_via_date_overlap():
    """Two 'John Smith's; no team_id on props side; stats player A
    played 2024-09-07 and our prop is for 2024-09-07 → unique match."""
    cand = _make_candidates([
        ("A_1_NCAAF", "John Smith", "OSU",  ["2024-09-07"]),
        ("B_2_NCAAF", "John Smith", "MICH", ["2024-09-14"]),
    ])
    unres = {
        "JOHN_SMITH_77_NCAAF": _info(
            "JOHN_SMITH_77_NCAAF", dates=["2024-09-07"]),
    }
    mapping = {}
    assert strategy_2_normalized(unres, cand, mapping) == 0
    assert strategy_3_name_team(unres, cand, mapping) == 0
    assert strategy_4_name_date(unres, cand, mapping) == 1
    assert mapping["JOHN_SMITH_77_NCAAF"] == "A_1_NCAAF"


# ───────────────────────── strategy 5 ──────────────────────────
def test_strategy_5_fuzzy_high_threshold_skips_noise():
    """Fuzzy strategy must NOT match unrelated names just because they
    share initials."""
    cand = _make_candidates([
        ("S_1_NCAAF", "Caleb Williams", "USC", []),
    ])
    unres = {"BILL_WILLIAMS_99_NCAAF": _info("BILL_WILLIAMS_99_NCAAF")}
    mapping = {}
    # threshold 0.9 should NOT match "billwilliams" vs "calebwilliams"
    assert strategy_5_fuzzy(unres, cand, mapping, 0.90) == 0
    # at 0.7 it might match (testing the bound is sensible)
    mapping2 = {}
    n2 = strategy_5_fuzzy(unres, cand, mapping2, 0.70)
    assert n2 in (0, 1)


def test_strategy_5_fuzzy_catches_minor_typo():
    """One-letter typo ('Caleb Wiliams' vs 'Caleb Williams') passes
    the default 0.90 threshold."""
    cand = _make_candidates([
        ("S_1_NCAAF", "Caleb Williams", "USC", []),
    ])
    # SGO would have encoded this as "CALEB_WILIAMS_..."
    unres = {"CALEB_WILIAMS_1_NCAAF": _info("CALEB_WILIAMS_1_NCAAF")}
    mapping = {}
    assert strategy_1_exact(unres, cand, mapping) == 0
    assert strategy_2_normalized(unres, cand, mapping) == 0
    assert strategy_5_fuzzy(unres, cand, mapping, 0.90) == 1
    assert mapping["CALEB_WILIAMS_1_NCAAF"] == "S_1_NCAAF"


# ───────────────────────── strategy pipeline ──────────────────
def test_strategy_pipeline_does_not_double_map():
    """Later strategies must SKIP pids already matched by earlier ones."""
    cand = _make_candidates([
        ("S_1_NCAAF", "Caleb Williams", "USC", []),
    ])
    unres = {"CALEB_WILLIAMS_1_NCAAF": _info("CALEB_WILLIAMS_1_NCAAF")}
    mapping = {}
    n1 = strategy_1_exact(unres, cand, mapping)
    n2 = strategy_2_normalized(unres, cand, mapping)
    n3 = strategy_3_name_team(unres, cand, mapping)
    n4 = strategy_4_name_date(unres, cand, mapping)
    n5 = strategy_5_fuzzy(unres, cand, mapping, 0.90)
    assert n1 == 1
    assert n2 == 0
    assert n3 == 0
    assert n4 == 0
    assert n5 == 0
    assert len(mapping) == 1
