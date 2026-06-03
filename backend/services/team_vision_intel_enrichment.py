"""services.team_vision_intel_enrichment

Generates Gemini Vision Intel narratives for active-board TEAM picks
using the SAME `VisionIntelService.analyze_tier_batch` path the player
orchestrators (`_enrich_nba_board_vision_intel`,
`_enrich_mlb_board_vision_intel`) call. No separate Gemini client, no
separate prompt module — teams flow through the exact same model
(`gemini-flash-lite-latest`), same `GOOGLE_API_KEY`, same content-hash
cache pattern, same per-tier batching/chunking.

Behaviour mirrors the player flow:

  1. Read the visible team board for each tier via the ferrari team
     tier service (the SAME service `/api/v3/ferrari/team/{tier}`
     serves to the dashboard).
  2. Filter to picks whose `vision_intel_content_hash` is stale or
     missing on `team_prop_scores`.
  3. Cap per tier (PICKS_PER_TIER_CAP) and globally
     (MAX_BOARD_VISION_INTEL_PICKS).
  4. For each tier, project each team pick into the prop_data shape
     `VisionIntelService._build_batch_prompt` expects (player_name,
     stat_type, line, direction, hit rates, averages, scout_badges,
     intel_suite). Team-specific signals (opponent abbr, DVP rank
     from `_resolve_opp_def_rank`, market_category as stat_type,
     etc.) feed the SAME prompt slots.
  5. Call `analyze_tier_batch` — one Gemini call per tier-chunk.
  6. Write `vision_intel` + `vision_intel_content_hash` +
     `vision_intel_generated_at` to `team_prop_scores`.

Fallback: when Gemini returns empty/None for a pick, the existing
deterministic sentence (already on the pick from the tier-service
enrichment) stays as-is. No card ever goes blank.

Scheduling: invoked from `services.master_sync.run_master_sync`
right after the player intel enrichment finishes (same 60-min
cadence), and from `services.jit_vision_intel_reaper.run_jit_reaper`
on the 5-min JIT cadence — same hooks the player path uses. See
the wire-up in `master_sync.py` for the registration point.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateMany

logger = logging.getLogger(__name__)

# Caps mirror the player orchestrator. Cheap defaults — never want a
# runaway Gemini call here.
TEAM_VISION_INTEL_TIERS: Tuple[str, ...] = ("safe_haven", "front_lines", "war_zone")
TEAM_PICKS_PER_TIER_CAP: Dict[str, int] = {
    "safe_haven":  10,
    "front_lines": 10,
    "war_zone":    10,
}
TEAM_MAX_BOARD_VISION_INTEL_PICKS: int = 30
TEAM_VISION_INTEL_FETCH_LIMIT_PER_TIER: int = 25


# ── Content hash (mirrors `_vision_intel_content_hash` in
# routes/ferrari_tiers.py — same field set, same hash algo). Team
# picks lack `player_name` so we substitute `team_id` + `market_key`.
def _team_vision_intel_content_hash(pick: Dict[str, Any]) -> str:
    keys = (
        pick.get("team_id"), pick.get("market_key"), pick.get("line"),
        pick.get("side"), pick.get("odds"), pick.get("tier"),
        pick.get("vk_predicted"), pick.get("edge_pct"),
        pick.get("hit_rate_l10"), pick.get("hit_rate_l20"),
    )
    payload = json.dumps(keys, default=str, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _is_team_cache_fresh(pick: Dict[str, Any]) -> bool:
    """Cache-fresh iff `vision_intel` is non-empty AND
    `vision_intel_content_hash` matches the freshly computed hash.
    Matches the player-side semantics.
    """
    if not (pick.get("vision_intel") or "").strip():
        return False
    stored = pick.get("vision_intel_content_hash")
    if not stored:
        return False
    return stored == _team_vision_intel_content_hash(pick)


# ── Project a team pick into the player-prompt prop_data shape.
def _team_pick_to_prompt_prop(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Map a team pick onto the canonical prop dict
    `VisionIntelService._build_batch_prompt` consumes. The same prompt
    template accepts these fields verbatim; team-specific naming just
    rides through (`stat_type=Team Total`, `player_name=Boston Celtics`,
    etc.).
    """
    out = dict(pick)
    # `_build_batch_prompt` keys on `player_name` — use team display
    # name so the Gemini-authored sentence references the team.
    out["player_name"] = pick.get("team_name") or pick.get("team") or \
                          (pick.get("team_id") or "Team").upper()
    # stat_type already set by tier-service enrichment to "Team Total"
    # / "Spread" / "Moneyline" / "Game Total" — same human-friendly
    # label the prompt expects.
    out.setdefault("stat_type", pick.get("market_category") or "Team Total")
    # Direction.
    out.setdefault("direction", pick.get("side") or pick.get("recommendation") or "OVER")
    # Hit rate aliases the player prompt reads.
    out.setdefault("h20_rate", pick.get("hit_rate_l20"))
    out.setdefault("h10_rate", pick.get("hit_rate_l10"))
    out.setdefault("hit_rate_under_l20", pick.get("hit_rate_under"))
    out.setdefault("hit_rate_under",     pick.get("hit_rate_under"))
    # Averages — already present.
    out.setdefault("l5_avg",     pick.get("l5_avg"))
    out.setdefault("season_avg", pick.get("season_avg"))
    # Probability + projection aliases.
    out.setdefault("vk_predicted", pick.get("vk_predicted"))
    tp_pct = pick.get("tp") or pick.get("vision_score")
    if tp_pct is not None:
        out.setdefault("vk_prob_over",  tp_pct)
        out.setdefault("vk_prob_under", max(0, 100 - float(tp_pct)))
    # Edge — already populated.
    out.setdefault("vk_edge", pick.get("edge"))
    # Variance.
    out.setdefault("cv", pick.get("cv"))
    # Matchup.
    out.setdefault("opponent",  pick.get("opponent") or pick.get("opponent_abbr"))
    out.setdefault("dvp_rank",  pick.get("dvp_rank") or pick.get("opponent_defensive_rank"))
    # Odds.
    out.setdefault("dk_odds", pick.get("odds"))
    # Tier flags (same names the player prompt checks).
    tier_label = (pick.get("tier_label") or "").upper()
    out.setdefault("is_goblin", tier_label == "SAFE_HAVEN")
    out.setdefault("is_demon",  tier_label == "WAR_ZONE")
    # Badge buckets are already on the pick (scout_badges / context_badges).
    return out


# ── Orchestrator. ───────────────────────────────────────────────────
async def enrich_team_board_vision_intel(
    db, sport: str,
) -> Dict[str, Any]:
    """Player-style orchestrator for team picks.

    Returns a metrics dict matching the shape
    `_enrich_nba_board_vision_intel` returns, so a future audit
    endpoint can show identical telemetry for the team side.
    """
    from services.vision_intel_service import get_vision_intel_service
    from services.team_prop_tier_service import get_team_prop_picks

    metrics: Dict[str, Any] = {
        "sport":                  sport,
        "safe_haven_count":       0,
        "front_lines_count":      0,
        "war_zone_count":         0,
        "total_visible_picks":    0,
        "cache_hits":             0,
        "to_call":                0,
        "gemini_calls":           0,
        "gemini_returned":        0,
        "gemini_empty_or_failed": 0,
        "score_docs_written":     0,
        "tiers_called":           {},
        "skip_reasons":           {},
    }

    vis = get_vision_intel_service()
    if not getattr(vis, "enabled", False):
        metrics["skip_reasons"]["service_disabled"] = (
            "VisionIntelService.enabled=False "
            "(missing GOOGLE_API_KEY or SDK)"
        )
        return metrics

    # ── Step A: pull visible board per tier via the SAME service the
    # dashboard ferrari team endpoint uses. Ensures we never enrich
    # picks the UI can't see.
    board_picks: List[Dict[str, Any]] = []
    per_tier_visible: Dict[str, int] = {}
    for tier_name in TEAM_VISION_INTEL_TIERS:
        tier_out = await get_team_prop_picks(
            db, sport=sport, tier_name=tier_name,
            limit=TEAM_VISION_INTEL_FETCH_LIMIT_PER_TIER,
        )
        tier_picks = tier_out.get("picks", []) or []
        per_tier_visible[tier_name] = len(tier_picks)
        board_picks.extend(tier_picks)

    metrics["safe_haven_count"]    = per_tier_visible.get("safe_haven", 0)
    metrics["front_lines_count"]   = per_tier_visible.get("front_lines", 0)
    metrics["war_zone_count"]      = per_tier_visible.get("war_zone", 0)
    metrics["total_visible_picks"] = len(board_picks)
    if not board_picks:
        return metrics

    # ── Step B: cache filter.
    to_call: List[Dict[str, Any]] = []
    for p in board_picks:
        if _is_team_cache_fresh(p):
            metrics["cache_hits"] += 1
            continue
        to_call.append(p)
    metrics["to_call"] = len(to_call)
    if not to_call:
        return metrics

    # ── Step C: per-tier cap + global cap (mirror player path).
    by_tier_cap: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in to_call:
        by_tier_cap[p.get("tier")].append(p)
    capped: List[Dict[str, Any]] = []
    for tier_name in ("war_zone", "front_lines", "safe_haven"):
        tier_picks = by_tier_cap.get(tier_name, [])
        tier_picks.sort(
            key=lambda d: float(d.get("vision_score") or 0), reverse=True,
        )
        capped.extend(
            tier_picks[:TEAM_PICKS_PER_TIER_CAP.get(tier_name, 10)]
        )
    if len(capped) > TEAM_MAX_BOARD_VISION_INTEL_PICKS:
        capped = capped[:TEAM_MAX_BOARD_VISION_INTEL_PICKS]
    to_call = capped

    # ── Step D: call Gemini per-tier via the SAME analyze_tier_batch
    # path the player orchestrator uses.
    by_tier: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in to_call:
        by_tier[p.get("tier")].append(p)

    now = datetime.now(timezone.utc)
    score_bulk: List[UpdateMany] = []

    for tier_name, tier_picks in by_tier.items():
        # Map team picks → player-prompt prop_data shape.
        prompt_props = [_team_pick_to_prompt_prop(p) for p in tier_picks]
        # `canonical_key` is the prompt's identity key — synthesise
        # one from team_id + market + line + side if missing.
        for src, mapped in zip(tier_picks, prompt_props):
            mapped.setdefault("canonical_key", (
                f"team_{src.get('team_id')}_"
                f"{src.get('market_key')}_"
                f"{src.get('line')}_"
                f"{src.get('side')}"
            ))

        # Chunked Gemini call — same CHUNK size the player path uses.
        CHUNK = 20
        results: List[Optional[Dict[str, Any]]] = []
        for i in range(0, len(prompt_props), CHUNK):
            chunk = prompt_props[i:i + CHUNK]
            metrics["gemini_calls"] += 1
            try:
                # `strict=True` → returns one slot per input, in input
                # order. Slots Gemini failed to fill come back None.
                # We DO NOT overwrite the deterministic fallback text
                # already on the pick — preserves the "no blank shells"
                # invariant the user enforced.
                chunk_results = await vis.analyze_tier_batch(
                    chunk, tier_name, strict=True,
                )
            except Exception as exc:
                logger.warning(
                    f"[TEAM_VISION_INTEL] {sport}/{tier_name} chunk{i} "
                    f"raised {type(exc).__name__}: {exc}"
                )
                chunk_results = [None] * len(chunk)
            results.extend(chunk_results)

        # Pair Gemini results to source picks by canonical_key (same
        # invariant as the player path — re-sorting in `analyze_tier_batch`
        # breaks positional zip alignment).
        out_by_ck: Dict[str, Dict[str, Any]] = {}
        for o in results:
            if o and o.get("canonical_key"):
                out_by_ck[o["canonical_key"]] = o

        tier_returned = 0
        tier_empty = 0
        for src, mapped in zip(tier_picks, prompt_props):
            ck = mapped["canonical_key"]
            out = out_by_ck.get(ck)
            vi = ((out or {}).get("vision_intel") or "").strip() if out else ""
            if not vi:
                tier_empty += 1
                continue
            tier_returned += 1
            content_hash = _team_vision_intel_content_hash(src)
            score_bulk.append(UpdateMany(
                {
                    "event_id":   src.get("event_id"),
                    "team_id":    src.get("team_id"),
                    # `_hydrate_card` sets pick.market to the human
                    # label ("Moneyline"); the DB column matching the
                    # natural key is `market_key`. Filter by
                    # market_key so all per-book rows for the natural
                    # key receive the Gemini write.
                    "market_key": src.get("market_key") or src.get("market"),
                    "line":       src.get("line"),
                    "side":       src.get("side"),
                },
                {"$set": {
                    "vision_intel":              vi,
                    "vision_intel_content_hash": content_hash,
                    "vision_intel_generated_at": now,
                }},
            ))

        metrics["tiers_called"][tier_name] = {
            "selected":        len(tier_picks),
            "gemini_returned": tier_returned,
            "gemini_empty":    tier_empty,
        }
        metrics["gemini_returned"] += tier_returned
        metrics["gemini_empty_or_failed"] += tier_empty

    if score_bulk:
        try:
            await db["team_prop_scores"].bulk_write(
                score_bulk, ordered=False)
            metrics["score_docs_written"] = len(score_bulk)
        except Exception as exc:
            logger.warning(
                f"[TEAM_VISION_INTEL] bulk_write failed for "
                f"{sport}: {exc}"
            )
            metrics["skip_reasons"]["bulk_write_failed"] = str(exc)

    logger.info(
        f"[TEAM_VISION_INTEL:{sport}] "
        f"safe_haven={metrics['safe_haven_count']} "
        f"front_lines={metrics['front_lines_count']} "
        f"war_zone={metrics['war_zone_count']} "
        f"visible={metrics['total_visible_picks']} "
        f"cache_hits={metrics['cache_hits']} "
        f"to_call={metrics['to_call']} "
        f"gemini_calls={metrics['gemini_calls']} "
        f"returned={metrics['gemini_returned']} "
        f"empty={metrics['gemini_empty_or_failed']} "
        f"writes={metrics['score_docs_written']}"
    )
    return metrics


__all__ = [
    "enrich_team_board_vision_intel",
    "TEAM_VISION_INTEL_TIERS",
]
