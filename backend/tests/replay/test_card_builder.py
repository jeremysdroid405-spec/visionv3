"""Pure unit tests for `services/picks/card_builder.py` (Phase 3).

These run in pytest with NO DB and NO model — pure-function tests on
synthetic rows. < 1 second runtime.
"""
import pytest

from services.picks.card_builder import (
    build_production_cards,
    select_best_book,
    dedupe_by_keys,
    per_game_top_n,
    final_card_order,
    DEFAULT_DEDUPE_KEYS,
    DEFAULT_ORDER_BY,
)


def _row(**overrides):
    """Synthetic Layer-3+4 output row with sensible defaults."""
    base = {
        "replay_serial": "TEST-1",
        "sport": "mlb",
        "game_date": "2026-05-05",
        "snapshot_iso": "2026-05-05T11:00:00Z",
        "event_id": "evt1",
        "player_name": "Test Player",
        "player_name_normalized": "test player",
        "stat_family": "total_bases",
        "market": "batter_total_bases",
        "is_alternate": False,
        "line": 1.5,
        "side": "OVER",
        "book": "fanduel",
        "odds": -110,
        "projection_mu": 2.1,
        "sigma": 0.7,
        "model_probability": 0.6,
        "fair_probability": 0.6,
        "implied_probability": 0.5238,
        "edge": 0.08,
        "tier": "war_zone",
        "gate_pass": True,
        "failed_gates": [],
        "gate_config_version": "v1",
        "grade_status": "win",
        "actual_value": 2,
        "profit_units": 0.91,
        "stake_units": 1.0,
    }
    base.update(overrides)
    return base


# ── select_best_book ────────────────────────────────────────────────
def test_best_book_keeps_highest_edge():
    rows = [
        _row(book="fanduel",  odds=-110, edge=0.05),
        _row(book="draftkings", odds=-105, edge=0.09),
        _row(book="caesars", odds=-115, edge=0.04),
    ]
    out = select_best_book(rows)
    assert len(out) == 1
    assert out[0]["book"] == "draftkings"
    assert out[0]["odds_was_best_among_n_books"] == 3


def test_best_book_does_not_collapse_distinct_picks():
    rows = [
        _row(book="fd", side="OVER",  line=1.5),
        _row(book="dk", side="UNDER", line=1.5),
        _row(book="dk", line=2.5),
        _row(book="dk", stat_family="hits", line=0.5),
    ]
    out = select_best_book(rows)
    assert len(out) == 4


def test_best_book_does_not_mutate_input():
    rows = [_row(book="fd", edge=0.10), _row(book="dk", edge=0.05)]
    snapshot = [dict(r) for r in rows]
    select_best_book(rows)
    assert rows == snapshot, "input rows must NOT be mutated"


# ── dedupe_by_keys ──────────────────────────────────────────────────
def test_dedupe_by_player_keeps_best_edge():
    rows = [
        _row(player_name_normalized="alice", line=1.5, edge=0.10),
        _row(player_name_normalized="alice", line=2.5, edge=0.18),
        _row(player_name_normalized="bob",   line=1.5, edge=0.07),
    ]
    out = dedupe_by_keys(rows, ("player_name_normalized",), DEFAULT_ORDER_BY)
    names = {r["player_name_normalized"] for r in out}
    assert names == {"alice", "bob"}
    alice = next(r for r in out if r["player_name_normalized"] == "alice")
    assert alice["edge"] == 0.18


def test_dedupe_empty_keys_returns_all():
    rows = [_row(player_name_normalized=f"p{i}") for i in range(5)]
    out = dedupe_by_keys(rows, (), DEFAULT_ORDER_BY)
    assert len(out) == 5


# ── per_game_top_n ─────────────────────────────────────────────────
def test_per_game_top_n_none_returns_all():
    rows = [_row(event_id=f"e{i}", edge=0.1) for i in range(10)]
    out = per_game_top_n(rows, None, DEFAULT_ORDER_BY)
    assert len(out) == 10


def test_per_game_top_n_caps_per_event():
    rows = [
        _row(event_id="A", player_name_normalized=f"a{i}", edge=0.1 + i * 0.01)
        for i in range(5)
    ] + [
        _row(event_id="B", player_name_normalized=f"b{i}", edge=0.05 + i * 0.01)
        for i in range(3)
    ]
    out = per_game_top_n(rows, 2, DEFAULT_ORDER_BY)
    by_event = {}
    for r in out:
        by_event.setdefault(r["event_id"], []).append(r)
    assert len(by_event["A"]) == 2
    assert len(by_event["B"]) == 2
    # The top-2 from event A should be the highest-edge ones
    a_edges = sorted([r["edge"] for r in by_event["A"]], reverse=True)
    assert a_edges == [0.14, 0.13]


# ── final_card_order ───────────────────────────────────────────────
def test_final_card_order_sorts_by_edge_desc():
    rows = [_row(edge=0.05), _row(edge=0.20), _row(edge=0.12)]
    out = final_card_order(rows, DEFAULT_ORDER_BY)
    assert [r["edge"] for r in out] == [0.20, 0.12, 0.05]


# ── build_production_cards (end-to-end pipeline) ───────────────────
def test_build_cards_filters_gate_pass_false():
    rows = [
        _row(player_name_normalized="alice", gate_pass=True,  edge=0.10),
        _row(player_name_normalized="bob",   gate_pass=False, edge=0.20),
    ]
    cards = build_production_cards(
        rows, tier="war_zone", replay_serial="TEST", sport="mlb")
    assert len(cards) == 1
    assert cards[0]["player_name_normalized"] == "alice"


def test_build_cards_respects_slate_top_k():
    rows = [_row(player_name_normalized=f"p{i:02d}", edge=0.10 - i * 0.001)
            for i in range(50)]
    cards = build_production_cards(
        rows, tier="war_zone", replay_serial="TEST", sport="mlb",
        slate_top_k=10)
    assert len(cards) == 10
    assert cards[0]["rank"] == 1
    assert cards[-1]["rank"] == 10


def test_build_cards_one_pick_per_player_by_default():
    """Default dedupe is by player — matches live `get_war_zone()`."""
    rows = [
        _row(player_name_normalized="alice", line=1.5, edge=0.10),
        _row(player_name_normalized="alice", line=2.5, edge=0.18),
        _row(player_name_normalized="bob",   line=1.5, edge=0.07),
    ]
    cards = build_production_cards(
        rows, tier="war_zone", replay_serial="TEST", sport="mlb")
    players = {c["player_name_normalized"] for c in cards}
    assert players == {"alice", "bob"}
    alice = next(c for c in cards if c["player_name_normalized"] == "alice")
    assert alice["line"] == 2.5
    assert alice["edge"] == 0.18


def test_build_cards_is_idempotent():
    rows = [_row(player_name_normalized=f"p{i}", edge=0.10 + i * 0.001)
            for i in range(15)]
    cards1 = build_production_cards(
        rows, tier="war_zone", replay_serial="A", sport="mlb")
    cards2 = build_production_cards(
        rows, tier="war_zone", replay_serial="A", sport="mlb")
    assert cards1 == cards2


def test_build_cards_carries_serial_sport_tier_rank():
    rows = [_row(player_name_normalized=f"p{i}", edge=0.10 + i * 0.001)
            for i in range(5)]
    cards = build_production_cards(
        rows, tier="war_zone", replay_serial="X", sport="mlb")
    for i, c in enumerate(cards):
        assert c["replay_serial"] == "X"
        assert c["sport"] == "mlb"
        assert c["tier"] == "war_zone"
        assert c["rank"] == i + 1


def test_build_cards_per_game_top_n_with_dedupe_disabled():
    rows = [
        _row(event_id="A", player_name_normalized=f"a{i}", edge=0.1 + i * 0.01)
        for i in range(5)
    ] + [
        _row(event_id="B", player_name_normalized=f"b{i}", edge=0.05 + i * 0.01)
        for i in range(3)
    ]
    cards = build_production_cards(
        rows, tier="custom", replay_serial="X", sport="mlb",
        dedupe_keys=(), per_game_top_n_value=2, slate_top_k=20)
    by_event = {}
    for c in cards:
        by_event.setdefault(c["game_id"], []).append(c)
    assert len(by_event["A"]) == 2
    assert len(by_event["B"]) == 2
