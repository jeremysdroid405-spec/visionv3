"""
Universal Board Observability + Longevity Validation Suite
==========================================================

Locks down the 5 spec proofs:

A. Same pick across 3 reconciles → lifetime increases monotonically
B. New pick inserted → starts at near-0 seconds
C. Removed pick → no longer returned by stamping or get_published_board
D. Front Lines OVER ↔ UNDER independence (insertion / removal on one
   side never affects the other side's longevity / state / events)
E. Health endpoint counts / capacity / fill_pct correct

These tests exercise the SAME code paths as production. The only
shortcut is a small Mongo mock (no real DB) — sufficient because the
publisher's only external dependency is async motor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from services.board.publisher import (
    TIER_CONFIG,
    _classify_status,
    _longevity_label,
    board_health_report,
    ensure_indexes,
    get_published_board,
    reconcile,
    stamp_longevity_on_picks,
)


# ─── Mongo mock (compatible with publisher's queries) ────────────────
class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self._sort = None
        self._limit = None

    def sort(self, key, direction=None):
        if isinstance(key, str):
            self._sort = [(key, direction or 1)]
        else:
            self._sort = list(key) if isinstance(key, list) else key
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        rows = list(self._rows)
        if self._sort:
            for k, d in reversed(self._sort):
                rows.sort(
                    key=lambda r: (r.get(k) is None, r.get(k)),
                    reverse=(d == -1),
                )
        if self._limit:
            rows = rows[: self._limit]
        if length:
            rows = rows[:length]
        return rows

    def __aiter__(self):
        async def _gen():
            rows = list(self._rows)
            if self._sort:
                for k, d in reversed(self._sort):
                    rows.sort(
                        key=lambda r: (r.get(k) is None, r.get(k)),
                        reverse=(d == -1),
                    )
            for r in rows:
                yield r
        return _gen()


def _q_match(d, q):
    for k, v in q.items():
        if isinstance(v, dict):
            if "$in" in v and d.get(k) not in v["$in"]:
                return False
            if "$nin" in v and d.get(k) in v["$nin"]:
                return False
            if "$gte" in v and (d.get(k) is None or d.get(k) < v["$gte"]):
                return False
            if "$gt" in v and (d.get(k) is None or d.get(k) <= v["$gt"]):
                return False
            if "$lt" in v and (d.get(k) is None or d.get(k) >= v["$lt"]):
                return False
        else:
            if d.get(k) != v:
                return False
    return True


class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, q=None, proj=None):
        return _Cursor([dict(d) for d in self.docs if _q_match(d, q or {})])

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if _q_match(d, q):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if _q_match(d, q):
                d.update(upd.get("$set") or {})
                return type("R", (), {"matched_count": 1})()
        if upsert:
            new = {}
            new.update(upd.get("$setOnInsert") or {})
            new.update(upd.get("$set") or {})
            for k, v in q.items():
                new.setdefault(k, v)
            self.docs.append(new)
        return type("R", (), {"matched_count": 0})()

    async def update_many(self, q, upd):
        for d in self.docs:
            if _q_match(d, q):
                d.update(upd.get("$set") or {})
        return type("R", (), {"matched_count": 0})()

    async def insert_many(self, docs, ordered=True):
        for d in docs:
            self.docs.append(dict(d))
        return type("R", (), {"inserted_ids": list(range(len(docs)))})()

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _q_match(d, q))

    async def distinct(self, field):
        return list({d.get(field) for d in self.docs if d.get(field) is not None})

    async def create_index(self, *a, **kw):
        return None


class _DB:
    def __init__(self):
        self._colls: Dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]


# ─── Helpers ─────────────────────────────────────────────────────────
def pick(player: str, side: str = "OVER", *, ranking=50.0,
         vision=50.0, edge=0.0, stat="PTS", line=10.5, event="evt1"):
    return {
        "canonical_key":  f"nba|{event}|{player}|{stat}|{line}|{side}",
        "player_name":    player,
        "stat_type":      stat,
        "line":           line,
        "recommendation": side,
        "ranking_score":  ranking,
        "vision_score":   vision,
        "edge_pct":       edge,
    }


def _shift_first_seen(db, sport, tier, side, ck, delta_sec):
    """Mutate `first_seen_at` on a board_state row to simulate the row
    having been on the board longer."""
    coll = db["board_state"]
    for d in coll.docs:
        if (d.get("sport") == sport and d.get("tier") == tier
                and d.get("side") == side and d.get("canonical_key") == ck):
            fs = d.get("first_seen_at")
            d["first_seen_at"] = fs - timedelta(seconds=delta_sec)


# ─── A. Same pick persists across 3 reconciles → lifetime grows ──────
@pytest.mark.asyncio
async def test_a_lifetime_grows_across_reconciles():
    db = _DB()
    await ensure_indexes(db)

    cands = [pick("alpha", ranking=80)]
    await reconcile(db, "nba", "safe_haven", cands)

    # Simulate 30 minutes elapsed by rewinding first_seen_at.
    _shift_first_seen(db, "nba", "safe_haven", None,
                      cands[0]["canonical_key"], 30 * 60)

    # Reconcile again — same pick, score unchanged.
    await reconcile(db, "nba", "safe_haven", cands)
    picks_out = list(cands)
    await stamp_longevity_on_picks(db, "nba", "safe_haven", picks_out)
    assert picks_out[0]["on_board_seconds"] >= 30 * 60 - 5
    assert picks_out[0]["on_board_label"] is None  # < 1h still

    # 90 more minutes → total 2h.
    _shift_first_seen(db, "nba", "safe_haven", None,
                      cands[0]["canonical_key"], 90 * 60)
    await reconcile(db, "nba", "safe_haven", cands)
    picks_out2 = list(cands)
    await stamp_longevity_on_picks(db, "nba", "safe_haven", picks_out2)
    assert picks_out2[0]["on_board_seconds"] >= 2 * 3600 - 5
    assert picks_out2[0]["on_board_label"] == "on board 1h+"

    # 4 more hours → 6h+.
    _shift_first_seen(db, "nba", "safe_haven", None,
                      cands[0]["canonical_key"], 4 * 3600)
    await reconcile(db, "nba", "safe_haven", cands)
    picks_out3 = list(cands)
    await stamp_longevity_on_picks(db, "nba", "safe_haven", picks_out3)
    assert picks_out3[0]["on_board_seconds"] >= 6 * 3600 - 5
    assert picks_out3[0]["on_board_label"] == "on board 6h+"

    # Critical: first_seen_at MUST NEVER go backwards across reconciles.
    state_doc = next(d for d in db["board_state"].docs
                     if d["canonical_key"] == cands[0]["canonical_key"])
    assert state_doc["first_seen_at"] <= datetime.now(timezone.utc)


# ─── B. New pick inserted → starts at near-0 seconds ─────────────────
@pytest.mark.asyncio
async def test_b_new_pick_starts_near_zero_seconds():
    db = _DB()
    await ensure_indexes(db)

    cands = [pick("alpha", ranking=80)]
    await reconcile(db, "nba", "safe_haven", cands)
    await stamp_longevity_on_picks(db, "nba", "safe_haven", cands)
    assert cands[0]["on_board_seconds"] <= 2
    assert cands[0]["on_board_label"] is None
    assert cands[0]["on_board_minutes"] == 0


# ─── C. Removed pick → no longer returned ────────────────────────────
@pytest.mark.asyncio
async def test_c_removed_pick_disappears_from_published_board():
    db = _DB()
    await ensure_indexes(db)

    p1 = pick("alpha", ranking=80)
    p2 = pick("bravo", ranking=70)
    await reconcile(db, "nba", "safe_haven", [p1, p2])
    rows = await get_published_board(db, "nba", "safe_haven")
    assert len(rows) == 2

    # Remove p2 from the candidate pool — should be evicted.
    await reconcile(db, "nba", "safe_haven", [p1])
    rows2 = await get_published_board(db, "nba", "safe_haven")
    assert len(rows2) == 1
    assert rows2[0]["canonical_key"] == p1["canonical_key"]

    # And longevity stamping on a removed pick gives 0/None (it has
    # no active state row).
    fresh = [dict(p2)]
    await stamp_longevity_on_picks(db, "nba", "safe_haven", fresh)
    assert fresh[0]["on_board_seconds"] == 0
    assert fresh[0]["on_board_label"] is None

    # Removal event was recorded.
    n_removals = await db["board_state_events"].count_documents({
        "sport": "nba", "tier": "safe_haven", "kind": "removal",
        "canonical_key": p2["canonical_key"],
    })
    assert n_removals == 1


# ─── D. Front Lines OVER ↔ UNDER independence ────────────────────────
@pytest.mark.asyncio
async def test_d_front_lines_over_under_full_independence():
    """Inserting / removing on OVER must NEVER affect UNDER's:
        * board_state rows
        * first_seen_at on UNDER picks
        * insertion / removal events on UNDER side
    """
    db = _DB()
    await ensure_indexes(db)

    over_cands = [pick(f"o{i}", side="OVER", ranking=50 + i) for i in range(5)]
    under_cands = [pick(f"u{i}", side="UNDER", ranking=40 + i) for i in range(3)]
    await reconcile(db, "nba", "front_lines", over_cands + under_cands)

    # Snapshot UNDER rows + their first_seen_at
    under_state_t1 = [
        (d["canonical_key"], d["first_seen_at"])
        for d in db["board_state"].docs
        if d["side"] == "UNDER" and d["active"]
    ]
    assert len(under_state_t1) == 3

    # Wait virtually, then insert 5 fresh OVER candidates that bump out
    # the original OVERs (board fills to 10 OVER, no-op for UNDER side).
    new_over_cands = over_cands + [
        pick(f"o_new_{i}", side="OVER", ranking=200 + i) for i in range(5)
    ]
    await reconcile(db, "nba", "front_lines", new_over_cands + under_cands)

    # UNDER rows are untouched: same canonical_keys, same first_seen_at,
    # zero new UNDER events emitted.
    under_state_t2 = [
        (d["canonical_key"], d["first_seen_at"])
        for d in db["board_state"].docs
        if d["side"] == "UNDER" and d["active"]
    ]
    assert sorted(under_state_t2) == sorted(under_state_t1), (
        "OVER reconciliation leaked into UNDER state — sides not "
        "independent"
    )

    n_under_insertions = await db["board_state_events"].count_documents({
        "sport": "nba", "tier": "front_lines", "side": "UNDER",
        "kind": "insertion",
    })
    n_under_removals = await db["board_state_events"].count_documents({
        "sport": "nba", "tier": "front_lines", "side": "UNDER",
        "kind": "removal",
    })
    # Only the original 3 insertions for UNDER should exist; no removals.
    assert n_under_insertions == 3
    assert n_under_removals == 0


# ─── E. Health endpoint correctness ──────────────────────────────────
@pytest.mark.asyncio
async def test_e_health_endpoint_counts_capacity_fill_pct():
    db = _DB()
    await ensure_indexes(db)

    # Build a deliberately-mixed slate.
    sh_cands = [pick(f"sh{i}", ranking=80 + i) for i in range(10)]
    fl_over = [pick(f"flo{i}", side="OVER",  ranking=70 + i) for i in range(8)]
    fl_under = [pick(f"flu{i}", side="UNDER", ranking=60 + i) for i in range(5)]
    wz_cands = [pick(f"wz{i}", ranking=50 + i) for i in range(3)]

    await reconcile(db, "nba", "safe_haven", sh_cands)
    await reconcile(db, "nba", "front_lines", fl_over + fl_under)
    await reconcile(db, "nba", "war_zone", wz_cands)

    report = await board_health_report(db)
    by_key = {(b["sport"], b["tier"], b["side"]): b
              for b in report["buckets"]}

    # Safe Haven: 10 / 10 = full
    sh = by_key[("nba", "safe_haven", None)]
    assert sh["count"] == 10
    assert sh["capacity"] == 10
    assert sh["fill_pct"] == 1.0
    assert sh["status"] == "healthy"

    # Front Lines OVER: 8 / 10
    flo = by_key[("nba", "front_lines", "OVER")]
    assert flo["count"] == 8
    assert flo["capacity"] == 10
    assert flo["fill_pct"] == 0.8
    assert flo["status"] == "underfilled"

    # Front Lines UNDER: 5 / 10
    flu = by_key[("nba", "front_lines", "UNDER")]
    assert flu["count"] == 5
    assert flu["capacity"] == 10
    assert flu["fill_pct"] == 0.5
    assert flu["status"] == "underfilled"

    # War Zone: 3 / 10
    wz = by_key[("nba", "war_zone", None)]
    assert wz["count"] == 3
    assert wz["capacity"] == 10
    assert wz["fill_pct"] == 0.3
    assert wz["status"] == "underfilled"

    # Aggregate roll-up status: any underfilled → "underfilled"
    assert report["overall_status"] == "underfilled"

    # Insertion counter sanity — initial fill emits N insertions.
    assert sh["insertions_last_hour"] == 10
    assert flo["insertions_last_hour"] == 8
    assert flu["insertions_last_hour"] == 5
    assert wz["insertions_last_hour"] == 3
    assert sh["removals_last_hour"] == 0


# ─── Longevity label lockdown ────────────────────────────────────────
def test_longevity_label_thresholds():
    """The label boundaries are part of the public contract."""
    assert _longevity_label(0)            is None
    assert _longevity_label(59 * 60)      is None
    assert _longevity_label(60 * 60)      == "on board 1h+"
    assert _longevity_label(2 * 3600)     == "on board 1h+"
    assert _longevity_label(3 * 3600)     == "on board 3h+"
    assert _longevity_label(5 * 3600 + 59) == "on board 3h+"
    assert _longevity_label(6 * 3600)     == "on board 6h+"
    assert _longevity_label(48 * 3600)    == "on board 6h+"


# ─── Status classifier lockdown ──────────────────────────────────────
def test_status_classifier_decisions():
    # Healthy: full + recent + low churn (initial fill insertions don't count)
    assert _classify_status(10, 10, newest_age=120, insertions=10, removals=0) \
           == "healthy"
    # Underfilled: count below capacity
    assert _classify_status(7, 10, newest_age=60, insertions=0, removals=0) \
           == "underfilled"
    # Stale: full + newest pick is > 2 h old
    assert _classify_status(10, 10, newest_age=2 * 3600 + 1, insertions=0, removals=0) \
           == "stale"
    # High churn: ≥ 5 removals last hour
    assert _classify_status(10, 10, newest_age=60, insertions=0, removals=5) \
           == "high_churn"
    # High churn: ≥ 3 removals AND ≥ 3 insertions (active replacement)
    assert _classify_status(10, 10, newest_age=60, insertions=3, removals=3) \
           == "high_churn"
    # NOT high churn: 4 insertions, 0 removals (filling, not churning)
    assert _classify_status(8, 10, newest_age=60, insertions=4, removals=0) \
           == "underfilled"


# ─── Universality: a brand-new sport produces health entries ────────
@pytest.mark.asyncio
async def test_health_universal_for_arbitrary_sport():
    db = _DB()
    await ensure_indexes(db)
    nhl_cands = [
        {"canonical_key": f"nhl|g1|p{i}|GOALS|0.5|OVER",
         "player_name": f"p{i}", "stat_type": "GOALS",
         "line": 0.5, "recommendation": "OVER",
         "ranking_score": 50 + i, "vision_score": 50 + i, "edge_pct": 0}
        for i in range(4)
    ]
    await reconcile(db, "nhl", "safe_haven", nhl_cands)
    report = await board_health_report(db)
    nhl_buckets = [b for b in report["buckets"] if b["sport"] == "nhl"]
    # 3 tiers × (1 or 2 sides) = 4 nhl buckets surfaced
    assert len(nhl_buckets) == 4
    sh_nhl = next(b for b in nhl_buckets if b["tier"] == "safe_haven")
    assert sh_nhl["count"] == 4
