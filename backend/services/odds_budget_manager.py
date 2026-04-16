"""
Odds Budget Manager
====================
Tracks Odds API call budget across NBA and MLB.

Budget: 5,000,000 calls/month (no rollover)
  → ~166,666/day → ~6,944/hour

Allocation is configurable per sport and per polling pool (hot/warm/cold).
Peak windows get higher allocation.

This module is OBSERVE-ONLY in Phase 1:
  - Tracks calls
  - Reports usage
  - Recommends intervals
  - Does NOT gate actual calls yet
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

MONTHLY_BUDGET = 5_000_000
DAYS_PER_MONTH = 30

# Sport allocation percentages (must sum to <= 100)
DEFAULT_SPORT_ALLOCATION = {
    "nba": 0.50,
    "mlb": 0.50,
}

# Pool allocation within each sport's budget
POOL_ALLOCATION = {
    "hot": 0.60,     # board props + near-board
    "warm": 0.30,    # active slate
    "cold": 0.10,    # inactive / stale
}

# Peak window: 5 PM - 11 PM ET (21:00 - 03:00 UTC)
PEAK_START_UTC = 21
PEAK_END_UTC = 3
PEAK_MULTIPLIER = 2.0
OFF_PEAK_MULTIPLIER = 0.5


class OddsBudgetManager:
    """
    Tracks and recommends Odds API usage.

    Phase 1: observe + recommend only.
    Phase 4: will gate actual polling decisions.
    """

    def __init__(self, monthly_budget: int = MONTHLY_BUDGET):
        self.monthly_budget = monthly_budget
        self.daily_budget = monthly_budget // DAYS_PER_MONTH
        self._sport_alloc = dict(DEFAULT_SPORT_ALLOCATION)

        # Rolling counters
        self._calls_today: Dict[str, Dict[str, int]] = {}  # {sport: {pool: count}}
        self._calls_hour: Dict[str, Dict[str, int]] = {}
        self._day_start: Optional[datetime] = None
        self._hour_start: Optional[datetime] = None
        self._total_calls = 0

        self._reset_day()
        self._reset_hour()

    def _reset_day(self):
        self._calls_today = {s: {p: 0 for p in POOL_ALLOCATION} for s in self._sport_alloc}
        self._day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def _reset_hour(self):
        self._calls_hour = {s: {p: 0 for p in POOL_ALLOCATION} for s in self._sport_alloc}
        self._hour_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def _maybe_roll(self):
        now = datetime.now(timezone.utc)
        if now.date() != self._day_start.date():
            logger.info(f"[BUDGET] Day rolled. Yesterday total: {self._day_total()} calls")
            self._reset_day()
        if now.hour != self._hour_start.hour:
            self._reset_hour()

    def _day_total(self) -> int:
        return sum(sum(pools.values()) for pools in self._calls_today.values())

    def _hour_total(self) -> int:
        return sum(sum(pools.values()) for pools in self._calls_hour.values())

    def _is_peak(self) -> bool:
        h = datetime.now(timezone.utc).hour
        if PEAK_START_UTC > PEAK_END_UTC:  # wraps midnight
            return h >= PEAK_START_UTC or h < PEAK_END_UTC
        return PEAK_START_UTC <= h < PEAK_END_UTC

    # ---------------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------------

    def record_calls(self, sport: str, pool: str, count: int):
        """Record API calls made."""
        self._maybe_roll()
        sport = sport.lower()
        pool = pool.lower()

        if sport not in self._calls_today:
            self._calls_today[sport] = {p: 0 for p in POOL_ALLOCATION}
            self._calls_hour[sport] = {p: 0 for p in POOL_ALLOCATION}

        self._calls_today[sport][pool] = self._calls_today[sport].get(pool, 0) + count
        self._calls_hour[sport][pool] = self._calls_hour[sport].get(pool, 0) + count
        self._total_calls += count

    def get_recommended_interval(self, sport: str, pool: str) -> int:
        """
        Recommended polling interval in seconds for a given sport + pool.

        Based on budget allocation and peak/off-peak status.
        """
        sport_pct = self._sport_alloc.get(sport.lower(), 0.5)
        pool_pct = POOL_ALLOCATION.get(pool.lower(), 0.1)
        hourly_budget = (self.daily_budget * sport_pct * pool_pct) / 24

        multiplier = PEAK_MULTIPLIER if self._is_peak() else OFF_PEAK_MULTIPLIER
        effective_hourly = hourly_budget * multiplier

        if effective_hourly <= 0:
            return 3600  # 1 hour fallback

        interval = max(10, int(3600 / effective_hourly))
        return interval

    def can_poll(self, sport: str, pool: str) -> bool:
        """
        Phase 1: always returns True (observe-only).
        Phase 4: will enforce budget gates.
        """
        # Observe-only — always allow
        return True

    def get_status(self) -> dict:
        """Full budget status for observability endpoint."""
        self._maybe_roll()
        is_peak = self._is_peak()

        sport_status = {}
        for sport in self._sport_alloc:
            daily_alloc = int(self.daily_budget * self._sport_alloc[sport])
            sport_used_today = sum(self._calls_today.get(sport, {}).values())
            sport_used_hour = sum(self._calls_hour.get(sport, {}).values())

            pool_detail = {}
            for pool in POOL_ALLOCATION:
                pool_used_today = self._calls_today.get(sport, {}).get(pool, 0)
                pool_used_hour = self._calls_hour.get(sport, {}).get(pool, 0)
                rec_interval = self.get_recommended_interval(sport, pool)
                pool_detail[pool] = {
                    "calls_today": pool_used_today,
                    "calls_this_hour": pool_used_hour,
                    "recommended_interval_sec": rec_interval,
                }

            sport_status[sport] = {
                "daily_allocation": daily_alloc,
                "used_today": sport_used_today,
                "used_this_hour": sport_used_hour,
                "remaining_today": daily_alloc - sport_used_today,
                "pools": pool_detail,
            }

        return {
            "monthly_budget": self.monthly_budget,
            "daily_budget": self.daily_budget,
            "is_peak_window": is_peak,
            "total_calls_lifetime": self._total_calls,
            "used_today_all": self._day_total(),
            "used_this_hour_all": self._hour_total(),
            "sports": sport_status,
        }


# Singleton
_manager: Optional[OddsBudgetManager] = None


def get_budget_manager() -> OddsBudgetManager:
    global _manager
    if _manager is None:
        _manager = OddsBudgetManager()
    return _manager
