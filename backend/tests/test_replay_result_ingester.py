"""Unit tests for the result-resolver cross-validation logic.

No DB. Verifies stat extraction + agree/mismatch/single-source paths.
"""
from __future__ import annotations

import pytest

from services.replay.result_ingester import (
    _bdl_min_to_int, _norm_name, _stats_from_bdl, _stats_from_hub,
    cross_validate,
)


def test_norm_name_strips_punctuation_and_case():
    assert _norm_name("LeBron James") == "lebronjames"
    assert _norm_name("D'Angelo Russell") == "dangelorussell"
    assert _norm_name("  José  Alvarado  ") == "josalvarado"


def test_norm_name_empty_input():
    assert _norm_name("") == ""
    assert _norm_name(None) == ""


def test_bdl_min_int_pass_through():
    assert _bdl_min_to_int(28) == 28
    assert _bdl_min_to_int("31") == 31


def test_bdl_min_mmss_format():
    assert _bdl_min_to_int("31:42") == 32      # rounds up at 30+s
    assert _bdl_min_to_int("31:00") == 31
    assert _bdl_min_to_int("31:29") == 31


def test_bdl_min_garbage_is_none():
    assert _bdl_min_to_int("xx") is None
    assert _bdl_min_to_int("") is None
    assert _bdl_min_to_int(None) is None


def test_stats_from_bdl_full_row():
    row = {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3,
           "stl": 1, "blk": 0, "min": 32}
    s = _stats_from_bdl(row)
    assert s == {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3,
                 "stl": 1, "blk": 0, "min": 32,
                 "pra": 37, "pr": 32, "pa": 29, "ra": 13}


def test_stats_from_hub_handles_three_pointers_made_alias():
    log = {"pts": 24, "reb": 8, "ast": 5,
           "three_pointers_made": 3, "min": 32}
    s = _stats_from_hub(log)
    assert s["fg3m"] == 3
    assert s["pra"] == 37


def test_stats_with_missing_pieces_yield_none_combos():
    s = _stats_from_bdl({"pts": 24, "reb": None, "ast": 5})
    assert s["pra"] is None
    assert s["pr"] is None
    assert s["pa"] == 29


def test_cross_validate_agree():
    a = {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3, "min": 32}
    b = {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3, "min": 32}
    status, meta = cross_validate(a, b)
    assert status == "agree"
    assert meta == {}


def test_cross_validate_mismatch_records_diffs():
    a = {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3, "min": 32}
    b = {"pts": 25, "reb": 8, "ast": 5, "fg3m": 3, "min": 30}
    status, meta = cross_validate(a, b)
    assert status == "mismatch"
    assert meta["diffs"]["pts"] == {"a": 24, "b": 25, "delta": -1}
    assert meta["diffs"]["min"] == {"a": 32, "b": 30, "delta": 2}


def test_cross_validate_source_a_only():
    a = {"pts": 24, "reb": 8, "ast": 5}
    status, _ = cross_validate(a, None)
    assert status == "source_a_only"


def test_cross_validate_source_b_only():
    b = {"pts": 24, "reb": 8, "ast": 5}
    status, _ = cross_validate(None, b)
    assert status == "source_b_only"


def test_cross_validate_both_missing():
    status, _ = cross_validate(None, None)
    assert status == "missing_both"


def test_cross_validate_ignores_none_in_one_side():
    a = {"pts": 24, "reb": 8, "ast": 5, "fg3m": None}
    b = {"pts": 24, "reb": 8, "ast": 5, "fg3m": 3}
    status, _ = cross_validate(a, b)
    assert status == "agree"   # None on either side is ignored, not mismatch
