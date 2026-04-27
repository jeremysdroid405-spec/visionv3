"""
NBA stat-family calibration table — STUB.

Today NBA scoring goes through its own probability path
(`services/scoring/adapters/nba_scoring.py` → ECDF / LOM / vk2). The
distribution-layer entry point exists for parity so that, when ready,
NBA can be migrated by registering its real distributions here without
touching the engine code.

Until then, every NBA family resolves to a conservative Normal CDF
(no μ-floor, sport-agnostic defaults). Audit fields (`distribution`,
`sigma_source`, etc.) will populate consistently across sports.
"""
from __future__ import annotations

from typing import Dict

from ..normal import NormalCDFDistribution, NormalCDFConfig
from ..registry import FamilySpec


def _placeholder_normal() -> NormalCDFDistribution:
    return NormalCDFDistribution(NormalCDFConfig(
        cv_floor=0.45, mu_floor=0.0,
        sigma_min_absolute=0.50,
        mu_floor_capped=False,
    ))


# Canonical NBA tokens (compact form already used elsewhere).
NBA_FAMILIES: Dict[str, FamilySpec] = {
    "PTS":     FamilySpec(default=_placeholder_normal(), notes="placeholder; see ECDF/LOM"),
    "REB":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "AST":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "3PM":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "STL":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "BLK":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "TO":      FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "P+A":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "P+R":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "R+A":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "PRA":     FamilySpec(default=_placeholder_normal(), notes="placeholder"),
    "BLK+STL": FamilySpec(default=_placeholder_normal(), notes="placeholder"),
}


__all__ = ["NBA_FAMILIES"]
