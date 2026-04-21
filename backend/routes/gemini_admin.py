"""
Gemini admin routes (P3.3, 2026-04-21)
=======================================
Observability surface for the Gemini-call cache + counters.

Endpoint:
  GET /api/v3/admin/gemini/cache-stats

Response shape:
  {
    "hits": int,
    "misses": int,
    "total": int,
    "hit_rate": float,
    "calls_last_24h": int,
    "real_api_calls_last_24h": int,
    "calls_by_sport": {"nba": int, "mlb": int, "unknown": int},
    "calls_by_kind": {"vision_intel_batch": int, "scout_engine_single": int, ...},
    "window_calls_by_sport": {"nba": int, "mlb": int}
  }
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from services.gemini_metrics import cache_stats

router = APIRouter(tags=["Gemini Admin"])


@router.get("/v3/admin/gemini/cache-stats")
async def gemini_cache_stats(
    window_hours: int = Query(24, ge=1, le=168),
):
    """Return Gemini cache counters + per-sport call history."""
    return cache_stats(window_hours=window_hours)
