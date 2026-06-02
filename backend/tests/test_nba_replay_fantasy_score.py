"""Regression: `fantasy_score` resolves through the canonical stat
registry to the PRA family without any `STAT_REGISTRY_MISS` warning.

Replay contract: every SGO stat_id the reshape emits MUST resolve
deterministically to a registered NBA family. No `_default`
fallthroughs from the production scorer.
"""
from __future__ import annotations
import io
import logging
import sys

sys.path.insert(0, "/app/backend")


def _capture_log_lines(target_logger_name: str = "services.scoring.canonical_stats"):
    """Yield a (logger, handler, buf) tuple. Caller can read buf.getvalue()."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    lg = logging.getLogger(target_logger_name)
    lg.addHandler(handler)
    return lg, handler, buf


def test_canonical_stat_type_resolves_every_fantasy_variant():
    from services.scoring.canonical_stats import canonical_stat_type
    for raw in (
        "fantasy_score", "fantasyScore", "player_fantasy_score",
        "FANTASY", "fantasy", "Player_Fantasy_Score",
    ):
        cst = canonical_stat_type("nba", raw)
        assert cst == "FANTASY", (
            f"canonical_stat_type('nba', {raw!r}) → {cst!r}; "
            f"expected 'FANTASY'")


def test_stat_family_resolves_fantasy_to_pra():
    """Fantasy_score should route to the `pra` family — the closest
    available combo predictor — not fall through to `_default`."""
    from services.scoring.canonical_stats import stat_family
    for raw in ("fantasy_score", "FANTASY", "fantasy", "fantasyScore"):
        fam = stat_family("nba", raw)
        assert fam == "pra", (
            f"stat_family('nba', {raw!r}) → {fam!r}; expected 'pra'")


def test_stat_family_emits_no_registry_miss_for_fantasy():
    """Resolving fantasy variants must NOT log
    `[STAT_REGISTRY_MISS]` — that's the diagnostic for unregistered
    stats falling back to `_default`. Replay calls this path hot in
    inner loops; a single miss multiplies into millions of log
    lines."""
    lg, handler, buf = _capture_log_lines("services.scoring.canonical_stats")
    try:
        from services.scoring.canonical_stats import stat_family
        for raw in (
            "fantasy_score", "FANTASY", "fantasy", "fantasyScore",
            "player_fantasy_score",
        ):
            stat_family("nba", raw)
    finally:
        lg.removeHandler(handler)
    msgs = buf.getvalue()
    assert "STAT_REGISTRY_MISS" not in msgs, (
        f"Resolving fantasy variants emitted STAT_REGISTRY_MISS — "
        f"family is not registered correctly. Captured: {msgs!r}")


def test_reshape_emits_player_fantasy_score_market_key():
    """The SGO → replay-odds reshape must emit the Odds-API-canonical
    market key (`player_fantasy_score`) for fantasy SGO stat_ids so
    downstream `canonical_stat_type` lookups hit the registry."""
    from scripts.sgo.reshape_sgo_to_replay_odds import (
        _STAT_ID_TO_MARKET_NBA,
    )
    for sgo_id in ("fantasyScore", "fantasy_score"):
        market = _STAT_ID_TO_MARKET_NBA.get(sgo_id)
        assert market == "player_fantasy_score", (
            f"_STAT_ID_TO_MARKET_NBA[{sgo_id!r}] = {market!r}; "
            f"expected 'player_fantasy_score'")


def test_nba_replay_adapter_normalize_fantasy_score():
    """The replay-side adapter must also resolve fantasy_score to
    the `pra` family (same routing as live scoring)."""
    from services.replay.providers.nba_adapter import NBAReplayAdapter

    class _StubDB:
        pass

    a = NBAReplayAdapter(_StubDB())
    assert a.normalize_stat_family("player_fantasy_score") == "pra"
    assert a.normalize_stat_family("fantasy_score") == "pra"
    assert a.normalize_stat_family("fantasyScore") == "pra"


if __name__ == "__main__":
    import traceback
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures += 1
            print(f"  ✗ {name}: {e}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {name} (uncaught exception)")
            traceback.print_exc(limit=2)
    print()
    if failures:
        print(f"  {failures} test(s) FAILED")
        sys.exit(1)
    print(f"  All fantasy_score canonicalization tests PASSED")
