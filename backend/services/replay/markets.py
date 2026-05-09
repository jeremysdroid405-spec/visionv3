"""
Market + book whitelist for the historical replay test suite.

Phase 1 (NBA only). Phase 2 (MLB) and Phase 3 (NFL) extend this module.
"""
from __future__ import annotations

from typing import List


# Markets we score on for NBA replay. Mirrors the live universe plus all
# `_alternate` ladders confirmed by the 2026-05-09 historical alt-prop audit.
REPLAY_NBA_MARKETS: List[str] = [
    # base
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    # base combos
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    # alt ladders
    "player_points_alternate",
    "player_rebounds_alternate",
    "player_assists_alternate",
    "player_threes_alternate",
    # alt combos
    "player_points_rebounds_assists_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
]


# Phase 1 books — Pinnacle deferred to Phase 2 (EU region cost).
# DK + FD + BetOnline + Caesars (williamhill_us) + MGM. Caesars is the
# canonical replacement for the legacy `williamhill_us` Odds API key.
REPLAY_BOOK_WHITELIST_PHASE1: List[str] = [
    "draftkings",
    "fanduel",
    "betonlineag",
    "williamhill_us",   # Caesars
    "betmgm",
]


# Phase 1 regions. Pinnacle would require adding "eu".
REPLAY_REGIONS_PHASE1: List[str] = ["us"]


__all__ = [
    "REPLAY_NBA_MARKETS",
    "REPLAY_BOOK_WHITELIST_PHASE1",
    "REPLAY_REGIONS_PHASE1",
]
