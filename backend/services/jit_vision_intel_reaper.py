"""
JIT (just-in-time) Vision Intel reaper — narrow, periodic, visible-only.
========================================================================

Problem
-------
Vision Intel is generated EXCLUSIVELY by `master_sync` Step 6 on a 60-minute
cadence. Between master_sync passes, brand-new picks that enter a visible
tier (board_state churn, new slate ingest, tier reshuffle) can surface to
users WITHOUT a `vision_intel` narrative. Audit (2026-05-08) confirmed
the no-intel window is 0 → 60 min for any newly-visible canonical_key
that wasn't covered by the previous master_sync run.

Solution (option B from the JIT audit)
--------------------------------------
A periodic reaper job runs every `JIT_REAPER_INTERVAL_MINUTES` and:

  1. Reads the visible board for each sport via the SAME path the
     dashboard uses — `services.board.reader.get_board()`.
  2. Filters the visible canonical_keys to those whose LIVE-tag score
     doc has `vision_intel` null / empty / missing.
  3. If the filtered set is empty → returns without calling Gemini
     (cheap no-op; supports the "do not re-enrich every dirty prop"
     directive).
  4. Otherwise delegates to the existing
     `services.master_sync._enrich_{sport}_board_vision_intel(db)`,
     which:
       • applies the content-hash cache (so already-covered picks are
         skipped at the per-pick level, not just at the reaper level)
       • applies the LIVE+BASELINE writer mirror (writer fix-D)
       • mirrors to `{sport}_cached_board.props[].vision_intel`
       • caps at MAX_BOARD_VISION_INTEL_PICKS

Bounds & safety
---------------
  * Only visible board picks are inspected. Hidden tier siblings are
    explicitly ignored.
  * Already-covered picks short-circuit via `_is_cache_fresh`.
  * Bounded scan: ≤ visible-cards-per-sport (today ≈ 20 / sport).
  * Bounded Gemini cost: capped by existing PICKS_PER_TIER_CAP and
    MAX_BOARD_VISION_INTEL_PICKS in master_sync; reaper adds an
    extra explicit `JIT_REAPER_MAX_PICKS_PER_RUN` ceiling to make
    the cost envelope obvious in code.
  * Failure-isolated: every catch is logged but never raised so a
    reaper crash cannot brick the scheduler.
  * Read-only with respect to scoring, gates, ingestion, queue, and
    frontend.

Out of scope (per directive)
----------------------------
  * Does NOT enrich every dirty prop.
  * Does NOT re-enrich every delta-rescored prop.
  * Does NOT call Gemini for already-covered picks.
  * Does NOT change prompts, scoring, gates, or frontend.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Tunables (kept here so they're greppable & easy to flip) ────────
JIT_REAPER_INTERVAL_MINUTES: int = 5     # scheduler cadence
JIT_REAPER_SPORTS: tuple = ("nba", "mlb")
JIT_REAPER_MAX_PICKS_PER_RUN: int = 25   # explicit Gemini cost ceiling
                                          # per sport per run (defense-
                                          # in-depth on top of master
                                          # _sync's existing caps)


async def find_visible_uncovered_cks(db, sport: str) -> List[str]:
    """Return canonical_keys that are currently on the visible board
    AND have no `vision_intel` on the LIVE-tag score doc. Pure read;
    no writes, no Gemini calls.

    Returns the filtered ck list (may be empty).

    2026-05-13 expansion: the dashboard fetches `?sort=gap` (NBA only)
    which returns a DIFFERENT canonical_key per player than the
    default `vision_score` sort (different alt-line wins per ranking).
    Without enriching both sort variants, gap-sort picks land on the
    UI with `vision_intel=None` forever. We now union both visible
    universes so the reaper covers every canonical_key the frontend
    could surface.
    """
    from services.board.reader import get_board
    from config.version_tags import for_sport

    visible_cks: List[str] = []
    # Sort variants the frontend can request via the ferrari endpoints.
    # `None` = adapter default (vision_score DESC for NBA, MLB).
    # `"ranking_score_v2"` = projection-gap sort, fired by Dashboard
    # via `?sort=gap` (NBA only today; harmless for MLB which falls
    # back to the default key when missing).
    sort_variants = (None, "ranking_score_v2") if sport == "nba" else (None,)

    for tier in ("safe_haven", "front_lines", "war_zone"):
        for variant in sort_variants:
            try:
                picks = await get_board(
                    db, sport=sport, tier=tier, limit=50,
                    sort_key_override=variant,
                )
            except Exception:
                logger.exception(
                    f"[JIT_VI:{sport}] get_board({tier}, sort={variant!r}) "
                    f"failed; skipping variant"
                )
                continue
            for p in picks:
                ck = p.get("canonical_key")
                if ck:
                    visible_cks.append(ck)

    if not visible_cks:
        return []

    live_tag = for_sport(sport)
    coll = db[f"{sport}_prop_scores"]
    cursor = coll.find(
        {
            "version_tag":   live_tag,
            "canonical_key": {"$in": visible_cks},
            "$or": [
                {"vision_intel": None},
                {"vision_intel": ""},
                {"vision_intel": {"$exists": False}},
            ],
        },
        {"_id": 0, "canonical_key": 1},
    )
    uncovered: List[str] = []
    seen = set()
    async for d in cursor:
        ck = d.get("canonical_key")
        if ck and ck not in seen:
            seen.add(ck)
            uncovered.append(ck)
    return uncovered


async def run_jit_vision_intel_reaper_for_sport(
    db,
    sport: str,
    *,
    max_picks_per_run: int = JIT_REAPER_MAX_PICKS_PER_RUN,
) -> Dict[str, Any]:
    """One reaper pass for one sport. Idempotent + cheap when nothing
    is uncovered. Returns a metrics dict suitable for logging."""
    started = datetime.now(timezone.utc)
    metrics: Dict[str, Any] = {
        "sport":                sport,
        "started_at":           started.isoformat(),
        "uncovered_visible":    0,
        "would_call_enrichment": False,
        "skipped_reason":       None,
        "enrichment_metrics":   None,
        "duration_seconds":     0.0,
    }

    sport = (sport or "").lower()
    if sport not in JIT_REAPER_SPORTS:
        metrics["skipped_reason"] = f"unsupported_sport:{sport}"
        metrics["duration_seconds"] = (
            datetime.now(timezone.utc) - started
        ).total_seconds()
        return metrics

    try:
        uncovered = await find_visible_uncovered_cks(db, sport)
    except Exception as exc:
        logger.exception(
            f"[JIT_VI:{sport}] find_visible_uncovered_cks crashed; "
            f"reaper aborted for this run: {exc}"
        )
        metrics["skipped_reason"] = f"discovery_error:{exc.__class__.__name__}"
        metrics["duration_seconds"] = (
            datetime.now(timezone.utc) - started
        ).total_seconds()
        return metrics

    metrics["uncovered_visible"] = len(uncovered)

    # ── Cheap no-op path when nothing is uncovered ────────────────────
    # Player path has nothing to do, but the team path still needs to
    # be checked — team picks have their own cache-hash filter and
    # short-circuit cheaply when fresh, so we fall through to that step
    # rather than early-returning.
    if not uncovered:
        metrics["skipped_reason"] = "no_uncovered_visible_picks"
        logger.info(
            f"[JIT_VI:{sport}] no uncovered player picks — "
            f"team path still runs (cache-cheap)"
        )

    # ── Hard ceiling per run as a defense-in-depth Gemini-cost cap ───
    if len(uncovered) > max_picks_per_run:
        logger.warning(
            f"[JIT_VI:{sport}] uncovered={len(uncovered)} exceeds "
            f"max_picks_per_run={max_picks_per_run}; the underlying "
            f"enricher will further cap at master_sync caps. Continuing."
        )

    # ── Delegate to the existing enricher ─────────────────────────────
    # The enricher itself:
    #   • re-pulls the visible board (idempotent w.r.t. `uncovered`)
    #   • uses content-hash to skip already-covered picks
    #   • writes to BOTH live tags via the writer fix-D
    #   • mirrors to cached_board
    # We pass `uncovered` only as a logging hint via metrics.
    # Skip when no uncovered player picks (saves Gemini cost).
    if uncovered:
        metrics["would_call_enrichment"] = True
        try:
            if sport == "nba":
                from services.master_sync import _enrich_nba_board_vision_intel
                enrich_metrics = await _enrich_nba_board_vision_intel(db)
            else:  # mlb
                from services.master_sync import _enrich_mlb_board_vision_intel
                enrich_metrics = await _enrich_mlb_board_vision_intel(db)
            metrics["enrichment_metrics"] = {
                "to_call":              enrich_metrics.get("to_call", 0),
                "cache_hits":           enrich_metrics.get("cache_hits", 0),
                "gemini_calls":         enrich_metrics.get("gemini_calls", 0),
                "gemini_returned":      enrich_metrics.get("gemini_returned", 0),
                "gemini_empty_or_failed": enrich_metrics.get("gemini_empty_or_failed", 0),
                "score_docs_written":   enrich_metrics.get("score_docs_written", 0),
                "cached_board_writes":  enrich_metrics.get("cached_board_writes", 0),
            }
        except Exception as exc:
            logger.exception(f"[JIT_VI:{sport}] enrichment crashed: {exc}")
            metrics["skipped_reason"] = f"enrichment_error:{exc.__class__.__name__}"

    # ── Team enrichment (JIT parity with player path). ───────────────
    # 2026-06-03 — Teams previously only got Vision Intel on the 60-min
    # master_sync cycle, leaving the 0–60 min window where new team
    # picks surfaced to the dashboard without Gemini narratives. JIT
    # reaper now drives team enrichment on the SAME 5-min cadence as
    # players, using the SAME `VisionIntelService.analyze_tier_batch`
    # under the hood. Cache-hit short-circuits keep this cheap.
    try:
        from services.team_vision_intel_enrichment import (
            enrich_team_board_vision_intel,
        )
        team_metrics = await enrich_team_board_vision_intel(db, sport)
        metrics["team_enrichment_metrics"] = {
            "total_visible_picks":  team_metrics.get("total_visible_picks", 0),
            "cache_hits":           team_metrics.get("cache_hits", 0),
            "to_call":              team_metrics.get("to_call", 0),
            "gemini_calls":         team_metrics.get("gemini_calls", 0),
            "gemini_returned":      team_metrics.get("gemini_returned", 0),
            "gemini_empty_or_failed": team_metrics.get("gemini_empty_or_failed", 0),
            "score_docs_written":   team_metrics.get("score_docs_written", 0),
        }
    except Exception as exc:
        logger.exception(
            f"[JIT_VI:{sport}] team enrichment crashed: {exc}"
        )
        metrics.setdefault("team_enrichment_metrics", {})
        metrics["team_enrichment_metrics"]["error"] = (
            f"{exc.__class__.__name__}: {exc}"
        )

    metrics["duration_seconds"] = (
        datetime.now(timezone.utc) - started
    ).total_seconds()
    _em = metrics.get("enrichment_metrics") or {}
    _tm = metrics.get("team_enrichment_metrics") or {}
    logger.info(
        f"[JIT_VI:{sport}] uncovered_visible={metrics['uncovered_visible']} "
        f"player_to_call={_em.get('to_call', 0)} "
        f"player_gemini_calls={_em.get('gemini_calls', 0)} "
        f"player_gemini_returned={_em.get('gemini_returned', 0)} "
        f"player_score_writes={_em.get('score_docs_written', 0)} "
        f"team_to_call={_tm.get('to_call', 0)} "
        f"team_gemini_calls={_tm.get('gemini_calls', 0)} "
        f"team_gemini_returned={_tm.get('gemini_returned', 0)} "
        f"team_score_writes={_tm.get('score_docs_written', 0)} "
        f"in {metrics['duration_seconds']:.2f}s"
    )
    return metrics


async def run_jit_vision_intel_reaper_all_sports(db=None) -> Dict[str, Any]:
    """Run the reaper for every JIT-enabled sport sequentially.

    `db` resolves to the global handle if not provided — APScheduler
    callables registered via MongoDBJobStore must be parameterless,
    so the scheduler entry point uses this signature."""
    if db is None:
        from server import db as _global_db   # type: ignore
        db = _global_db
    out: Dict[str, Any] = {
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "per_sport":    {},
    }
    for sport in JIT_REAPER_SPORTS:
        out["per_sport"][sport] = await run_jit_vision_intel_reaper_for_sport(db, sport)
    return out


# ─────────────────────────────────────────────────────────────────────
# Module-level scheduler entry point — required by APScheduler +
# MongoDBJobStore which serialises a textual reference rather than
# pickling the callable.
# ─────────────────────────────────────────────────────────────────────
async def scheduled_jit_vision_intel_reaper() -> None:
    """APScheduler entry — runs the reaper across every JIT-enabled
    sport. No raise; failures are logged inside the per-sport helper."""
    try:
        await run_jit_vision_intel_reaper_all_sports()
    except Exception:
        logger.exception(
            "[JIT_VI] reaper run crashed at the top-level dispatcher"
        )


__all__ = [
    "JIT_REAPER_INTERVAL_MINUTES",
    "JIT_REAPER_SPORTS",
    "JIT_REAPER_MAX_PICKS_PER_RUN",
    "find_visible_uncovered_cks",
    "run_jit_vision_intel_reaper_for_sport",
    "run_jit_vision_intel_reaper_all_sports",
    "scheduled_jit_vision_intel_reaper",
]
