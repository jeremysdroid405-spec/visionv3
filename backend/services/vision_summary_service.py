"""
Vision AI Summary Service — Consolidated (P2.1, 2026-04-21)
============================================================
Generates short pick summaries. Historically owned its own Gemini call
path with a duplicate narrative prompt. **P2.1 Gemini cost audit**
consolidated this into a thin wrapper around the single unified Gemini
entry point `VisionIntelService.analyze_prop_strict`.

What stayed the same:
  * Class-level caches (`_summary_cache`, `_cache_timestamps`,
    `_cache_keys`) — preserved because 13+ existing call sites in
    `mlb_sync_engine.py` / `optimized_sync_engine.py` /
    `routes/cached_data.py` index into them directly.
  * Public API `generate_pick_summary(...)` signature.
  * Content-hash cache key via `_generate_pick_hash(...)`.

What changed:
  * The duplicate prompt + Gemini-call + retry block (~170 LOC) now
    delegates to `VisionIntelService.analyze_prop_strict`, which owns
    the canonical prompt, retry strategy, and Gemini client.
  * Every outcome (hit / miss / real-API call) is tagged through
    `services.gemini_metrics.record_gemini_call` for admin visibility.
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _generate_pick_hash(player_name: str, stat_type: str, line: float,
                        h10_rate: float, badges: List[str], opponent: str = None) -> str:
    """Generate a content-based hash for cache invalidation."""
    content = (
        f"{player_name}|{stat_type}|{line}|{h10_rate}|"
        f"{','.join(sorted(badges or []))}|{opponent or ''}"
    )
    return hashlib.md5(content.encode()).hexdigest()[:12]


class VisionSummaryService:
    """Thin delegation wrapper around VisionIntelService (post-P2.1)."""

    # Class-level cache for AI summaries
    _summary_cache: Dict[str, str] = {}
    _cache_timestamps: Dict[str, datetime] = {}
    _cache_keys: Dict[str, str] = {}
    _CACHE_TTL_SECONDS = 21600  # 6 hours

    # Circuit breaker
    _circuit_breaker_open = False
    _circuit_breaker_until: Optional[datetime] = None
    _CIRCUIT_BREAKER_DURATION = 30

    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            logger.warning("[VISION] No GOOGLE_API_KEY — summaries disabled")

    async def generate_pick_summary(
        self,
        player_name: str,
        stat_type: str,
        line: float,
        season_avg: float,
        h10_rate: float,
        badges: list,
        opponent: str = None,
        is_demon: bool = False,
        is_goblin: bool = False,
        dvp_rank: int = None,
        dvp_friction: str = None,
        player_team: str = None,
    ) -> Optional[str]:
        """Generate a 2-3 sentence summary explaining the pick.

        Post-P2.1: routes through `VisionIntelService.analyze_prop_strict`
        so there is EXACTLY ONE Gemini entry point for narrative text.
        Returns None on any failure (preserves original contract).
        """
        from services.gemini_metrics import record_gemini_call

        if not self.api_key:
            return None

        now = datetime.now(timezone.utc)

        # Circuit breaker
        if VisionSummaryService._circuit_breaker_open:
            if (VisionSummaryService._circuit_breaker_until
                    and now < VisionSummaryService._circuit_breaker_until):
                logger.debug("[VISION] Circuit breaker OPEN — skipping")
                return None
            VisionSummaryService._circuit_breaker_open = False
            VisionSummaryService._circuit_breaker_until = None

        # Content hash
        badge_keys: List[str] = []
        if badges:
            for b in badges:
                if isinstance(b, dict):
                    badge_keys.append(b.get("badge_key") or b.get("key", ""))
                else:
                    badge_keys.append(str(b))

        content_hash = _generate_pick_hash(
            player_name, stat_type, line, h10_rate or 0, badge_keys, opponent
        )
        simple_key = f"{player_name}|{stat_type}|{line}"

        # Cache hit path
        if content_hash in VisionSummaryService._summary_cache:
            cached_time = VisionSummaryService._cache_timestamps.get(content_hash)
            if (cached_time
                    and (now - cached_time).total_seconds()
                    < VisionSummaryService._CACHE_TTL_SECONDS):
                record_gemini_call("vision_summary", sport=None, hit=True)
                return VisionSummaryService._summary_cache[content_hash]

        old_hash = VisionSummaryService._cache_keys.get(simple_key)
        if (old_hash and old_hash == content_hash
                and old_hash in VisionSummaryService._summary_cache):
            cached_time = VisionSummaryService._cache_timestamps.get(old_hash)
            if (cached_time
                    and (now - cached_time).total_seconds()
                    < VisionSummaryService._CACHE_TTL_SECONDS):
                record_gemini_call("vision_summary", sport=None, hit=True)
                return VisionSummaryService._summary_cache[old_hash]

        # Cache miss → unified service
        try:
            from services.vision_intel_service import get_vision_intel_service
            svc = get_vision_intel_service()
            if not svc or not getattr(svc, "enabled", False):
                logger.debug("[VISION] Unified service disabled — skip")
                return None

            if is_goblin or is_demon:
                direction = "OVER"
            elif season_avg and line < season_avg - 1:
                direction = "OVER"
            elif season_avg and line > season_avg + 1:
                direction = "UNDER"
            else:
                direction = "OVER" if (h10_rate and h10_rate >= 60) else "UNDER"

            prop = {
                "player_name": player_name,
                "stat_type": stat_type,
                "line": line,
                "direction": direction,
                "recommendation": direction,
                "opponent": opponent,
                "team": player_team,
                "season_avg": season_avg,
                "h10_rate": h10_rate,
                "dvp_rank": dvp_rank,
                "dvp_friction": dvp_friction,
                "is_demon": is_demon,
                "is_goblin": is_goblin,
                "badges": badge_keys,
            }

            intel = await svc.analyze_prop_strict(prop, tier_name="safe_haven")
            record_gemini_call("vision_summary", sport=None, hit=False)

            if not intel:
                return None
            summary = (intel.get("vision_intel") or "").strip()
            if not summary:
                return None

            VisionSummaryService._summary_cache[content_hash] = summary
            VisionSummaryService._cache_timestamps[content_hash] = now
            VisionSummaryService._cache_keys[simple_key] = content_hash
            logger.info(
                f"[VISION] Cached (via unified service) {simple_key} "
                f"hash={content_hash}"
            )
            return summary

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[VISION] unified-service call failed for {player_name}: {exc}")
            VisionSummaryService._circuit_breaker_open = True
            VisionSummaryService._circuit_breaker_until = (
                now + timedelta(seconds=VisionSummaryService._CIRCUIT_BREAKER_DURATION)
            )
            return None
