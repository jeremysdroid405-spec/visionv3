"""
SSOT enforcement: cached_board.hit_rates must NOT cross line boundaries
=======================================================================

Three contracts (Daniss-Jenkins-style cross-line corruption is the
canonical example): a prop at line=14.5 must not absorb a cached_board
entry's `hit_rates` from line=9.5; the displayed `h10_rate` must equal
the score doc's `hit_rate_l10`; and an API response must NOT prefer
`hit_rates.l10_rate` over `score.hit_rate_l10`.

Surface under test: `routes/ferrari_tiers._get_nba_tier_picks_from_scores`
+ `_merge_score_with_board`. We invoke them with stub MongoDB and the
universal board reader patched to return one canonical score doc, then
inject a cross-line cached_board entry into the (player, stat) lookup
to verify the SSOT firewall holds.
"""
from __future__ import annotations

import asyncio
import sys
import importlib
import pytest


# ── Async DB stub ────────────────────────────────────────────────────
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self, docs=None):
        self._docs = list(docs or [])

    def find(self, *_, **__):
        return _Cursor(self._docs)

    async def find_one(self, *_, **__):
        return self._docs[0] if self._docs else None

    async def update_many(self, *_, **__):
        return None

    async def update_one(self, *_, **__):
        return None

    async def bulk_write(self, *_, **__):
        return None


class _DB:
    def __init__(self, mapping=None):
        self._mapping = mapping or {}

    def __getitem__(self, name):
        return self._mapping.setdefault(name, _Coll([]))


# ── Fixtures ─────────────────────────────────────────────────────────
JENKINS_CK = "nba|0d7933fbc3dd3e384f9b709cff4e882c|Daniss Jenkins|player_points_assists_alternate|14.5|OVER"


def _score_doc():
    """Canonical SSOT score doc — line=14.5, P+A, OVER side. Side-aware
    L10 OVER for Jenkins is 20% (manually verified vs raw game logs)."""
    return {
        "player_name":  "Daniss Jenkins",
        "stat_type":    "player_points_assists_alternate",
        "line":         14.5,
        "recommendation": "OVER",
        "tier":         "war_zone",
        "version_tag":  "final-nba-rt",
        "event_id":     "0d7933fbc3dd3e384f9b709cff4e882c",
        "canonical_key": JENKINS_CK,
        "active":       True,
        "hit_rate_l5":  20.0,   # SSOT (1/5 OVER)
        "hit_rate_l10": 20.0,   # SSOT (2/10 OVER)
        "hit_rate_l20": 60.0,   # SSOT (12/20 OVER)
        "hit_rate_over":  60.0,
        "hit_rate_under": 40.0,
        "vision_score": 50.0,
    }


def _cached_board_index_with_cross_line_entry():
    """Build a fake `nba_cached_board` index that ONLY carries a
    line=9.5 P+A entry for Jenkins (the canonical leak vector). The
    score doc is at line=14.5 — a SSOT-clean merge must NOT pull
    `hit_rates` from this 9.5 entry."""
    cached_prop = {
        "player_name":  "Daniss Jenkins",
        "stat_type":    "P+A",
        "line":         9.5,                 # ≠ 14.5 score-doc line
        "h5_rate":      20,
        "h10_rate":     60,                  # ← LEAK candidate
        "hit_rates": {
            "l5_rate":       20,
            "l10_rate":      60,             # ← LEAK candidate
            "l5_hit_count":  1,
            "l10_hit_count": 6,              # ← LEAK candidate
            "l5_avg":  8.2,
            "l10_avg": 17.7,
        },
    }
    cached_player = {
        "player_name":  "Daniss Jenkins",
        "team":         "DET",
        "context_badges": [],
    }
    stat_entry = {"prop": cached_prop, "player": cached_player}
    # Index shape mirrors `_build_nba_board_lookup` output:
    # __by_5tuple__, __by_4tuple__ (line-keyed) MUST NOT match line=14.5
    # because the cached entry is line=9.5. Only the (player, stat)
    # stat-level index resolves.
    return {
        "__by_5tuple__":      {},
        "__by_4tuple__":      {},
        "__by_player_stat__": {("daniss jenkins", "P+A"): stat_entry},
    }


@pytest.fixture
def patched_route(monkeypatch):
    sys.path.insert(0, "/app/backend")

    # Force a fresh import so monkeypatching `_db` and helpers sticks.
    import routes.ferrari_tiers as ft
    importlib.reload(ft)

    db = _DB()
    monkeypatch.setattr(ft, "_db", db)

    # Replace the universal board reader with a stub that returns our
    # canonical score doc. `get_board` is imported INSIDE the helper.
    async def _fake_get_board(_db, sport, tier, limit=None, sort_key_override=None):
        return [_score_doc()] if tier == "war_zone" else []

    import services.board.reader as _reader
    monkeypatch.setattr(_reader, "get_board", _fake_get_board)

    # Replace the cached_board index builder with our cross-line stub.
    async def _fake_build_lookup():
        return _cached_board_index_with_cross_line_entry()

    monkeypatch.setattr(ft, "_build_nba_board_lookup", _fake_build_lookup)

    # Stub out unrelated downstream enrichers so the test stays focused
    # on the SSOT contract. Each is async and a no-op.
    async def _noop_async(*_, **__):
        return None

    for name in (
        "annotate_market_gap_async",
    ):
        if hasattr(ft, name):
            monkeypatch.setattr(ft, name, _noop_async, raising=False)

    return ft


# ── Contract 1 ───────────────────────────────────────────────────────
def test_prop_line_14_5_does_not_inherit_cached_board_hit_rates_from_line_9_5(patched_route):
    """SSOT firewall: a 14.5-line prop must not receive `hit_rates`
    that were computed against a 9.5-line cached entry."""
    ft = patched_route
    picks = asyncio.run(ft._get_nba_tier_picks_from_scores("war_zone", limit=10))
    assert picks, "expected at least one Jenkins pick from stub get_board"

    jenkins = next(
        (p for p in picks if p.get("player_name") == "Daniss Jenkins"),
        None,
    )
    assert jenkins is not None, f"Jenkins pick not surfaced; got: {[p.get('player_name') for p in picks]}"

    # Score doc is line=14.5 — the cached_board has only a 9.5 entry.
    # `hit_rates` must NOT be present (or must be empty/None) because
    # the SSOT firewall forbids line-mismatched stat-level overlay.
    leaked = jenkins.get("hit_rates")
    assert leaked in (None, {}, []), (
        f"SSOT BREACH: cached_board.hit_rates from line=9.5 leaked "
        f"onto a line=14.5 prop. value={leaked!r}"
    )


# ── Contract 2 ───────────────────────────────────────────────────────
def test_displayed_h10_rate_equals_score_doc_hit_rate_l10(patched_route):
    """The `h10_rate` field surfaced to the UI must equal the score doc's
    canonical `hit_rate_l10` (NOT `hit_rate_l20`, NOT
    `hit_rates.l10_rate` from a cross-line cached entry)."""
    ft = patched_route
    picks = asyncio.run(ft._get_nba_tier_picks_from_scores("war_zone", limit=10))
    jenkins = next(
        p for p in picks if p.get("player_name") == "Daniss Jenkins"
    )

    # Score doc: hit_rate_l10 = 20.0, hit_rate_l20 = 60.0.
    # Pre-fix: `h10_rate` = 60 (L20 leaked into L10 field) — broken.
    # Post-fix: `h10_rate` = 20 (canonical L10).
    h10 = jenkins.get("h10_rate")
    canonical_l10 = _score_doc()["hit_rate_l10"]
    assert h10 is not None, "h10_rate not stamped on the merged prop"
    assert int(round(float(h10))) == int(round(float(canonical_l10))), (
        f"SSOT BREACH: displayed h10_rate={h10} != "
        f"score.hit_rate_l10={canonical_l10}. The merge layer is "
        f"reading the wrong window or the wrong source."
    )

    # Side-aware canonical fields must round-trip verbatim.
    assert jenkins.get("hit_rate_l5")  == _score_doc()["hit_rate_l5"]
    assert jenkins.get("hit_rate_l10") == _score_doc()["hit_rate_l10"]
    assert jenkins.get("hit_rate_l20") == _score_doc()["hit_rate_l20"]


# ── Contract 3 ───────────────────────────────────────────────────────
def test_api_response_does_not_prefer_hit_rates_l10_rate_over_score_hit_rate_l10(patched_route):
    """Frontend/API contract: when both `score.hit_rate_l10` and a
    cached `hit_rates.l10_rate` exist, the response's user-visible
    L10 windows must reflect the SSOT (score doc), not the cached bag.

    Implementation contract: after the SSOT firewall, the merged prop
    has either NO `hit_rates` key (preferred) or its `hit_rates.l10_rate`
    must equal the canonical `hit_rate_l10`. NEVER 60 (the cross-line
    Jenkins leak value).
    """
    ft = patched_route
    picks = asyncio.run(ft._get_nba_tier_picks_from_scores("war_zone", limit=10))
    jenkins = next(
        p for p in picks if p.get("player_name") == "Daniss Jenkins"
    )

    canonical_l10 = _score_doc()["hit_rate_l10"]   # 20.0
    leaked_value = 60                              # the line=9.5 value

    nested = jenkins.get("hit_rates")
    if nested:
        # Nested bag may legitimately ride along when 5/4-tuple is
        # line-exact — but in this test scenario it's NOT line-exact,
        # so any value here is a leak. Hard-fail on the leaked rate.
        assert nested.get("l10_rate") != leaked_value, (
            f"SSOT BREACH: API exposes hit_rates.l10_rate={leaked_value} "
            f"from a cached_board entry at a different line. Score doc "
            f"canonical hit_rate_l10={canonical_l10}."
        )

    # Final user-facing field MUST be the canonical SSOT value.
    assert jenkins.get("hit_rate_l10") == canonical_l10
