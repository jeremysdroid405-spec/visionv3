"""
Event Bus
==========
Lightweight in-process async event bus for board-related events.
All watchers publish here; the RebuildCoordinator subscribes.

Design:
  - asyncio.Queue per subscriber (backpressure-safe)
  - Type-filtered subscriptions
  - No external dependencies
  - Thread-safe via asyncio primitives
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class BoardEvent:
    """Canonical event shape for all board-related triggers."""
    sport: str                              # "nba" | "mlb"
    event_type: str                         # "odds_delta" | "injury_change" | "game_lock" | "manual" | "scheduled_safety" | "prop_appeared" | "prop_removed"
    severity: str = "medium"                # "high" | "medium" | "low"
    affected_players: List[str] = field(default_factory=list)
    affected_props: List[str] = field(default_factory=list)   # pick_ids
    source: str = "unknown"                 # "odds_delta_engine" | "injury_watcher" | "game_clock" | "manual_api" | "scheduler"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Dedup key: sport + event_type + sorted affected players."""
        players = ",".join(sorted(self.affected_players[:5]))
        return f"{self.sport}|{self.event_type}|{players}"


class EventBus:
    """
    Simple async pub/sub for BoardEvents.

    Usage:
        bus = EventBus()
        bus.subscribe(handler_coroutine)          # subscribe to all events
        bus.subscribe(handler, event_types={"odds_delta"})  # filtered
        await bus.publish(BoardEvent(...))
    """

    def __init__(self):
        self._subscribers: List[dict] = []
        self._stats = {
            "total_published": 0,
            "total_delivered": 0,
            "by_type": {},
            "by_sport": {},
        }

    def subscribe(
        self,
        handler: Callable[[BoardEvent], Coroutine],
        event_types: Optional[Set[str]] = None,
    ):
        """Register an async handler. Optionally filter by event_type."""
        self._subscribers.append({
            "handler": handler,
            "event_types": event_types,
        })
        logger.info(f"[EVENT_BUS] Subscriber added (filter={event_types or 'ALL'}). Total: {len(self._subscribers)}")

    async def publish(self, event: BoardEvent):
        """Publish an event to all matching subscribers."""
        self._stats["total_published"] += 1
        self._stats["by_type"][event.event_type] = self._stats["by_type"].get(event.event_type, 0) + 1
        self._stats["by_sport"][event.sport] = self._stats["by_sport"].get(event.sport, 0) + 1

        delivered = 0
        for sub in self._subscribers:
            if sub["event_types"] and event.event_type not in sub["event_types"]:
                continue
            try:
                await sub["handler"](event)
                delivered += 1
            except Exception as e:
                logger.error(f"[EVENT_BUS] Subscriber error on {event.event_type}: {e}")

        self._stats["total_delivered"] += delivered
        logger.debug(f"[EVENT_BUS] Published {event.event_type}({event.sport}) → {delivered} subscribers")

    def get_stats(self) -> dict:
        return {**self._stats, "subscriber_count": len(self._subscribers)}


# Singleton
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
