"""
Gemini Call Metrics + LRU Cache (P3.1 + P3.3 admin)
===================================================
Shared, dependency-free tracking for every Gemini API call site in the
app. Provides:

  * `record_gemini_call(kind, sport, hit)` — per-call counter sink
  * `GeminiLRUCache` — simple hash-keyed LRU for request/response
    short-window dedupe (P3.1, used by `AIContextEngine._call_gemini`).
  * `cache_stats()` — snapshot for the admin endpoint
    (`GET /api/v3/admin/gemini/cache-stats`).

No external dependencies. Pure in-memory state; resets on pod restart
(correct behaviour for a per-pod instrumentation surface).
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Deque, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
# `_counters` shape:
#   {"total_calls": int, "total_hits": int,
#    "calls_by_sport": {"nba": int, "mlb": int, ...},
#    "call_timestamps": deque[(epoch_seconds, sport)]}
_counters: Dict[str, Any] = {
    "total_calls": 0,
    "total_hits": 0,
    "total_misses": 0,
    "calls_by_sport": defaultdict(int),
    "calls_by_kind": defaultdict(int),
    "call_timestamps": deque(maxlen=50000),  # ~50k calls / day headroom
}


def record_gemini_call(
    kind: str,
    sport: Optional[str] = None,
    hit: bool = False,
) -> None:
    """Record one Gemini API attempt.

    `kind`  — identifier for the call site (e.g. "vision_intel_batch",
              "vision_intel_strict", "scout_engine_batch",
              "ai_context", "intel_briefing", "mlb_vision_intel").
    `sport` — "nba" | "mlb" | None (unknown).
    `hit`   — True if served from cache (no real API call was made).
    """
    _counters["total_calls"] += 1
    if hit:
        _counters["total_hits"] += 1
    else:
        _counters["total_misses"] += 1
    sport_key = (sport or "unknown").lower()
    _counters["calls_by_sport"][sport_key] += 1
    _counters["calls_by_kind"][kind] += 1
    _counters["call_timestamps"].append((time.time(), sport_key, kind, hit))


def cache_stats(window_hours: int = 24) -> Dict[str, Any]:
    """Return a snapshot of Gemini counters for the admin endpoint."""
    now = time.time()
    window_start = now - (window_hours * 3600)
    calls_window = 0
    calls_window_by_sport: Dict[str, int] = defaultdict(int)
    misses_window = 0
    for ts, sport, _kind, hit in _counters["call_timestamps"]:
        if ts < window_start:
            continue
        calls_window += 1
        calls_window_by_sport[sport] += 1
        if not hit:
            misses_window += 1
    total = _counters["total_calls"]
    hits = _counters["total_hits"]
    hit_rate = (hits / total) if total else 0.0
    return {
        "hits": hits,
        "misses": _counters["total_misses"],
        "total": total,
        "hit_rate": round(hit_rate, 4),
        f"calls_last_{window_hours}h": calls_window,
        f"real_api_calls_last_{window_hours}h": misses_window,
        "calls_by_sport": dict(_counters["calls_by_sport"]),
        "calls_by_kind": dict(_counters["calls_by_kind"]),
        "window_calls_by_sport": dict(calls_window_by_sport),
    }


# ---------------------------------------------------------------------------
# LRU Cache (P3.1)
# ---------------------------------------------------------------------------

class GeminiLRUCache:
    """Thread-unsafe in-memory LRU for Gemini prompt → response dedupe.

    Keys are sha1 of the prompt payload. Values are the raw response
    text. Size is fixed at construction; oldest entry is evicted when
    the cache is full. Not async-safe (we don't need it: FastAPI's
    worker loop is single-threaded per event loop).
    """

    def __init__(self, max_size: int = 500) -> None:
        self._max = max_size
        self._store: "OrderedDict[str, str]" = OrderedDict()

    @staticmethod
    def key_for(prompt: str) -> str:
        return hashlib.sha1(prompt.encode("utf-8")).hexdigest()

    def get(self, prompt: str) -> Optional[str]:
        k = self.key_for(prompt)
        if k not in self._store:
            return None
        self._store.move_to_end(k)
        return self._store[k]

    def set(self, prompt: str, value: str) -> None:
        k = self.key_for(prompt)
        if k in self._store:
            self._store.move_to_end(k)
            self._store[k] = value
            return
        if len(self._store) >= self._max:
            self._store.popitem(last=False)
        self._store[k] = value

    def __len__(self) -> int:
        return len(self._store)


__all__ = [
    "record_gemini_call",
    "cache_stats",
    "GeminiLRUCache",
]
