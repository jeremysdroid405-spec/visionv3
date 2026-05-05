"""
Regression: `_attach_badges_in_memory` (Finding A)
==================================================

Confirms `scout_badges` + `context_badges` are stamped onto in-memory
pick dicts BEFORE `analyze_tier_batch`, so Gemini receives real badges
instead of `"badges": "None", "context": "None"`.

Validation contract:
  1. After the helper runs, picks expose `scout_badges` from the
     universal generator (when triggers fire).
  2. `context_badges` from `{sport}_master_hub_2026` are joined onto
     the pick by display_name match.
  3. The downstream Vision Intel prompt body renders both buckets
     (NOT the "None" sentinel) for at least one sample.
  4. NO writes are issued to `*_prop_scores` — verified via DB stub.
"""

import asyncio
import json
import sys
import types
import pytest


# ── Async DB stub (master_hub returns 1 enriched doc) ────────────────
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
    def __init__(self, docs):
        self._docs = docs
        self.update_calls = 0
        self.insert_calls = 0

    def find(self, query, projection=None):
        # naive in-memory $in match
        ins = (query.get("display_name") or {}).get("$in", [])
        return _Cursor(d for d in self._docs if d["display_name"] in ins)

    async def update_many(self, *a, **k):
        self.update_calls += 1
        return None

    async def insert_one(self, *a, **k):
        self.insert_calls += 1
        return None

    async def bulk_write(self, *a, **k):
        self.update_calls += 1
        return None


class _DB:
    def __init__(self, hub_docs):
        self._hub = _Coll(hub_docs)
        self._scores = _Coll([])

    def __getitem__(self, name):
        if "master_hub" in name:
            return self._hub
        return self._scores


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture
def picks():
    """Two picks: one OVER with strong floor + edge, one UNDER cold."""
    return [
        {
            "player_name": "LeBron James",
            "canonical_key": "nba::lebron-james::pts::25.5::over",
            "stat_type": "Points",
            "line": 25.5,
            "recommendation": "OVER",
            "direction": "OVER",
            "tier": "safe_haven",
            "hit_rate_l5": 100.0,
            "hit_rate_l10": 95.0,
            "hit_rate_l20": 92.0,
            "edge_vs_fair": 0.22,    # decimal — triggers lasso_high_edge
            "p_true_active": 0.78,
            "vision_score": 88.0,
            "cv": 0.18,
        },
        {
            "player_name": "Random Bench Guy",
            "canonical_key": "nba::random-bench::reb::3.5::under",
            "stat_type": "Rebounds",
            "line": 3.5,
            "recommendation": "UNDER",
            "direction": "UNDER",
            "tier": "front_lines",
            "hit_rate_under": 88.0,
            "hit_rate_l10": 80.0,
            "edge_vs_fair": 0.05,
            "p_true_active": 0.62,
        },
    ]


@pytest.fixture
def hub_docs():
    return [
        {
            "display_name": "LeBron James",
            "context_badges": [
                {"badge_key": "home_cookin"},
                {"badge_key": "locked_in"},
            ],
        },
        # "Random Bench Guy" intentionally absent — verifies graceful
        # fallthrough (pick should NOT crash; context just stays empty).
    ]


# ── Tests ─────────────────────────────────────────────────────────────
def test_attach_badges_in_memory_populates_both_buckets(picks, hub_docs, monkeypatch):
    """scout_badges fires from generator; context_badges from master_hub."""
    sys.path.insert(0, "/app/backend")
    from services.master_sync import _attach_badges_in_memory

    db = _DB(hub_docs)
    metrics = asyncio.run(_attach_badges_in_memory(picks, "nba", db))

    # 1. scout_badges populated for at least the high-edge pick
    lebron = picks[0]
    assert "scout_badges" in lebron, "scout_badges missing on LeBron pick"
    assert isinstance(lebron["scout_badges"], list)
    assert len(lebron["scout_badges"]) > 0
    # `lasso_high_edge` MUST fire (edge=0.22 > 0.15 trigger)
    keys = [b.get("badge_key") for b in lebron["scout_badges"] if isinstance(b, dict)]
    assert "lasso_high_edge" in keys, f"lasso_high_edge missing, got {keys}"

    # 2. context_badges joined from master_hub
    assert lebron.get("context_badges"), "context_badges missing on LeBron"
    ctx_keys = [b.get("badge_key") for b in lebron["context_badges"]]
    assert "home_cookin" in ctx_keys

    # 3. Pick without master_hub doc gets no context_badges (no crash)
    bench = picks[1]
    assert "context_badges" not in bench

    # 4. Metrics surface attachment counts
    assert metrics["scout_attached"] >= 1
    assert metrics["context_attached"] == 1
    assert metrics["master_hub_lookups"] == 2  # 2 distinct names looked up

    # 5. NO DB writes were issued
    assert db._scores.update_calls == 0
    assert db._scores.insert_calls == 0
    assert db._hub.update_calls == 0


def test_vision_intel_prompt_renders_real_badges_not_none(picks, hub_docs):
    """End-to-end: badged picks → vision_intel prompt → badges/context != 'None'."""
    sys.path.insert(0, "/app/backend")
    from services.master_sync import _attach_badges_in_memory

    db = _DB(hub_docs)
    asyncio.run(_attach_badges_in_memory(picks, "nba", db))

    # Mirror the prompt-build path in vision_intel_service.analyze_tier_batch
    # (lines 289-296). We don't need Gemini; we only need to confirm the
    # `badges`/`context` fields hydrate from the in-memory picks.
    perf_list = picks[0].get("scout_badges") or []
    ctx_list  = picks[0].get("context_badges") or []
    badge_text = ", ".join(
        b.get("badge_key", b) if isinstance(b, dict) else str(b)
        for b in perf_list[:3]
    ) if perf_list else "None"
    context_text = ", ".join(
        b.get("badge_key", b) if isinstance(b, dict) else str(b)
        for b in ctx_list[:3]
    ) if ctx_list else "None"

    assert badge_text != "None", "Gemini would receive 'None' for badges"
    assert context_text != "None", "Gemini would receive 'None' for context"
    assert "lasso_high_edge" in badge_text
    assert "home_cookin" in context_text

    # Show one sample prompt body for the audit log.
    sample_prop = {
        "prop_id": picks[0]["canonical_key"],
        "player": picks[0]["player_name"],
        "badges": badge_text,
        "context": context_text,
    }
    print("\n[FINDING A SAMPLE PROMPT BODY]")
    print(json.dumps(sample_prop, indent=2))


def test_attach_badges_does_not_mutate_canonical_key(picks, hub_docs):
    """canonical_key must round-trip — it's how analyze_tier_batch pairs."""
    sys.path.insert(0, "/app/backend")
    from services.master_sync import _attach_badges_in_memory

    db = _DB(hub_docs)
    keys_before = [p["canonical_key"] for p in picks]
    asyncio.run(_attach_badges_in_memory(picks, "nba", db))
    keys_after = [p["canonical_key"] for p in picks]
    assert keys_before == keys_after
