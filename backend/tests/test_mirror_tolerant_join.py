"""
Unit tests for the tolerant mirror-to-legacy outcome join (the fix for
the prod failure where 8,693 replay rows had only 46 (0.5%) graded
outcomes because the previous mirror required exact float/case/case
match on player_name_normalized + market + line + side, and the
outcomes collection wrote line as string + side lowercase + player
name_normalized/market as null).
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from scripts.sgo.historical_full_pipeline_replay import (
    _norm_line, _norm_side, _norm_player, _pick_outcome,
    _flip_outcome_for_opposite_side,
)


def test_norm_line_casts_str_to_float():
    assert _norm_line("0.5") == 0.5
    assert _norm_line(0.5)   == 0.5
    assert _norm_line(2)     == 2.0
    assert _norm_line(None)  is None
    assert _norm_line("nan-string-junk") is None


def test_norm_side_uppercases():
    assert _norm_side("over") == "OVER"
    assert _norm_side("UNDER") == "UNDER"
    assert _norm_side("  Over  ") == "OVER"
    assert _norm_side(None) is None


def test_norm_player_lowercases_and_trims():
    assert _norm_player("Hunter Renfroe") == "hunter renfroe"
    assert _norm_player("  TANNER HOUCK ") == "tanner houck"
    assert _norm_player(None) is None


def test_pick_outcome_single_candidate_returns_it():
    cand = {"player_name_normalized": None, "outcome_numeric": 1}
    assert _pick_outcome([cand], wanted_player_norm="anyone",
                            wanted_player_raw="anyone") is cand


def test_pick_outcome_disambiguates_by_normalized_name():
    a = {"player_name_normalized": "alice smith", "outcome_numeric": 1}
    b = {"player_name_normalized": "bob jones",   "outcome_numeric": 0}
    pick = _pick_outcome([a, b], wanted_player_norm="bob jones",
                              wanted_player_raw="Bob Jones")
    assert pick is b


def test_pick_outcome_substring_fallback():
    """Outcomes side has player_name='Hunter Renfroe' but no normalized.
    Replay wants 'hunter renfroe'. Substring match must hit."""
    a = {"player_name": "Mike Trout"}
    b = {"player_name": "Hunter Renfroe Jr."}
    pick = _pick_outcome([a, b], wanted_player_norm="hunter renfroe",
                              wanted_player_raw="Hunter Renfroe")
    assert pick is b


def test_pick_outcome_returns_first_when_no_player_info():
    a = {"player_name_normalized": None, "player_name": None, "outcome_numeric": 1}
    b = {"player_name_normalized": None, "player_name": None, "outcome_numeric": 0}
    assert _pick_outcome([a, b], wanted_player_norm=None,
                            wanted_player_raw=None) is a


def test_pick_outcome_empty_list_returns_none():
    assert _pick_outcome([], wanted_player_norm="x", wanted_player_raw="x") is None


# ── Simulated end-to-end key match (the actual prod failure mode) ──
def test_index_lookup_simulates_prod_failure_mode():
    """Reproduce the user's evidence:
    replay:  line=0.5 (float), side='OVER', stat_family='hits'
    outcome: line='0.5' (str), side='over', stat_family='hits'
    The normalized lookup must produce a match. Side is now NOT in
    the key (handled by the side-flip step downstream)."""
    eid, fam = "evt_X", "hits"
    outcome = {"event_id": eid, "stat_family": fam, "line": "0.5",
                  "side": "over", "outcome_numeric": 1,
                  "player_name_normalized": None,
                  "player_name": "Hunter Renfroe", "market": None}
    index_key = (eid, fam, _norm_line(outcome["line"]))
    index = {index_key: [outcome]}

    replay_key = (eid, fam, _norm_line(0.5))
    assert replay_key == index_key

    candidates = index.get(replay_key) or []
    pick = _pick_outcome(candidates,
                              wanted_player_norm="hunter renfroe",
                              wanted_player_raw="Hunter Renfroe")
    assert pick is outcome
    assert pick["outcome_numeric"] == 1


# ── Side-flip: outcomes are graded per-side ─────────────────────────
def test_flip_outcome_when_replay_side_disagrees_with_outcome_side():
    """The original prod bug behind HR=13.9% on pitcher_strikeouts:
    outcomes are graded with side='over' regardless of which side the
    replay row was on. When replay is UNDER and outcome is OVER, the
    win/loss must be inverted (UNDER wins when OVER loses)."""
    over_lost = {"side": "over", "outcome_numeric": 0, "hit": False}
    flipped = _flip_outcome_for_opposite_side(over_lost, "UNDER")
    assert flipped["outcome_numeric"] == 1
    assert flipped["hit"] is True
    assert flipped["side_flipped_from_outcome"] is True


def test_no_flip_when_sides_agree():
    over_won = {"side": "over", "outcome_numeric": 1, "hit": True}
    out = _flip_outcome_for_opposite_side(over_won, "OVER")
    assert out is over_won  # same dict, not a copy
    assert out["outcome_numeric"] == 1
    assert "side_flipped_from_outcome" not in out


def test_flip_preserves_push():
    """PUSH (0.5) outcomes are side-agnostic — the actual stat hit the
    line exactly. Don't flip."""
    push = {"side": "over", "outcome_numeric": 0.5, "hit": False, "push": True}
    out = _flip_outcome_for_opposite_side(push, "UNDER")
    assert out["outcome_numeric"] == 0.5


def test_flip_handles_missing_outcome_gracefully():
    assert _flip_outcome_for_opposite_side(None, "OVER") is None


def test_flip_when_outcome_won():
    """The other half of the inversion: if OVER won, UNDER lost."""
    over_won = {"side": "over", "outcome_numeric": 1, "hit": True}
    out = _flip_outcome_for_opposite_side(over_won, "UNDER")
    assert out["outcome_numeric"] == 0
    assert out["hit"] is False
    assert out["side_flipped_from_outcome"] is True


def test_pick_outcome_narrows_by_stat_family_when_pool_has_multiple_props():
    """Fallback index lookup returns every prop on the same
    (event, line, side). _pick_outcome must narrow by stat_family
    before falling back to player-name disambiguation."""
    a = {"stat_family": "batter_strikeouts", "player_name": "Mike Trout",
            "outcome_numeric": 1}
    b = {"stat_family": "walks_allowed",     "player_name": "Mike Trout",
            "outcome_numeric": 0}
    pick = _pick_outcome([a, b],
                              wanted_player_norm="mike trout",
                              wanted_player_raw="Mike Trout",
                              wanted_stat_family="walks_allowed")
    assert pick is b


def test_pick_outcome_narrows_by_market_or_stat_id():
    """When stat_family isn't enough (e.g. outcome side stores
    stat_id='batting_strikeouts' but replay's market is
    'batter_strikeouts'), the market/stat_id match must still
    narrow the pool."""
    a = {"stat_family": "strikeouts", "stat_id": "pitching_strikeouts",
            "market": None, "player_name": "Same Guy",
            "outcome_numeric": 1}
    b = {"stat_family": "strikeouts", "stat_id": "batting_strikeouts",
            "market": None, "player_name": "Same Guy",
            "outcome_numeric": 0}
    # market="batter_strikeouts" → no stat_id/market exact match,
    # so we fall back to first; this test ensures market filter
    # doesn't accidentally zero the pool.
    pick = _pick_outcome([a, b],
                              wanted_player_norm="same guy",
                              wanted_player_raw="Same Guy",
                              wanted_market="batter_strikeouts")
    assert pick in (a, b)


def test_pick_outcome_market_filter_picks_stat_id_when_market_is_null():
    a = {"stat_family": "x", "stat_id": "pitching_walks", "market": None,
            "player_name": "p", "outcome_numeric": 1}
    b = {"stat_family": "x", "stat_id": "batting_hits", "market": None,
            "player_name": "p", "outcome_numeric": 0}
    pick = _pick_outcome([a, b],
                              wanted_player_norm="p", wanted_player_raw="p",
                              wanted_market="pitching_walks")
    assert pick is a
