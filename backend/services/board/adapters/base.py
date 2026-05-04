"""
SportBoardAdapter — the plug-in contract.

Everything the universal engine needs to know about a sport lives here.
The engine never branches on `if sport == "nba"`; it calls
`get_adapter(sport).<method>()`.

Collection names are resolved through `config.collections` so the
universal engine uses the canonical `{sport}_<concept>` name everywhere
it's available, and seamlessly falls back to legacy names (e.g. NBA's
`dg_live_props` / `dg_cached_board`) until Phase B/C/D migrations
complete. Each adapter only sets `self.sport` + `self.version_tag`; all
collection properties are derived.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config.collections import resolve


class SportBoardAdapter(ABC):
    """Abstract base. Each sport implements this in one file."""

    # ---- Identity (adapters set these two only) ----
    sport: str                   # 'nba', 'mlb', 'nfl', …
    version_tag: str             # 'final-nba', 'final-mlb', …

    # ---- Collection properties (resolved through config.collections) ----
    # These are properties (not class attributes) so that the resolver is
    # the single source of truth and migrations happen via one config
    # file, not by touching every adapter.
    @property
    def live_props_collection(self) -> str:
        """Raw odds-API inventory collection for this sport."""
        return resolve(self.sport, "live_props")

    @property
    def scores_collection(self) -> str:
        """Master pool collection for this sport."""
        return resolve(self.sport, "prop_scores")

    @property
    def cached_board_collection(self) -> str:
        """Enrichment overlay collection for this sport."""
        return resolve(self.sport, "cached_board")

    tier_names: Tuple[str, ...]  # e.g. ('safe_haven','front_lines','war_zone')

    @abstractmethod
    def sort_key_for_tier(self, tier: str) -> str:
        """Primary sort field the board uses for this tier.
        NBA safe_haven → 'vk_prob_over' etc."""

    def capacity_for_tier(self, tier: str) -> int:
        """Number of props the visible board holds for this tier.
        Default 10; override if a sport needs a different cap."""
        return 10

    # ---- Optional hooks (no-op by default) ----
    def extract_game_start(self, prop: Dict) -> Optional[datetime]:
        """Extract commence_time / game_time from a RAW prop document.
        Default reads common fields. Override if a sport uses something
        exotic (epoch, nested object, …)."""
        raw = prop.get("commence_time") or prop.get("event_start_utc") or prop.get("game_time")
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                return None
        return None

    def canonical_key(self, prop: Dict) -> Optional[str]:
        """Build the canonical identity key used everywhere in the pool.
        Default: '{sport}|{event_id}|{player}|{stat_type}|{line}|{side}'.
        Matches the existing NBA/MLB scoring-adapter convention so no
        re-keying is required."""
        try:
            event_id = prop.get("event_id") or prop.get("_event_id") or ""
            player = prop.get("player_name") or ""
            stat_type = prop.get("stat_type") or ""
            line = prop.get("line")
            side = (prop.get("recommendation") or prop.get("side") or prop.get("direction") or "").upper()
            if not (event_id and player and stat_type and line is not None and side):
                return None
            return f"{self.sport}|{event_id}|{player}|{stat_type}|{line}|{side}"
        except Exception:
            return None

    # ---- Scoring (heavy lifting stays per-sport) ----
    @abstractmethod
    async def score_batch(self, db, canonical_keys: List[str]) -> List[Dict]:
        """Score ONLY these canonical keys. Returns pool-ready docs with
        universal fields + sport-specific score fields.

        The existing NBA/MLB adapters delegate to
        `services.scoring.recompute.recompute()` scoped via the Phase 3
        monkey-patch pattern; any future sport can do the same."""

    # ---- Tier classification (sport-specific gate logic) ----
    def classify_tier(self, scored: Dict) -> str:
        """Return the tier name for a scored doc. Default = trust the
        score doc's `tier` field (produced by the existing scoring stack).
        Override if an adapter wants to apply extra sport gates."""
        return scored.get("tier") or "unqualified"

    # ---- Post-score enrichment hook (optional) ----
    async def enrich_post_score(self, db, scored: List[Dict]) -> None:
        """Non-blocking side effects after a batch is upserted into the
        pool (Gemini, cached-board patch, etc.). Default: no-op."""
        return None
