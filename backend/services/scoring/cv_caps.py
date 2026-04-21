"""
Stat-aware CV caps for Safe Haven eligibility  (multi-sport, sport-agnostic)

The single `max_cv = 0.50` constant was structurally penalizing small-mean
stats (AST 1.5, REB 2.5, STL/BLK) whose CV = σ/μ is mechanically higher even
when the pick profile is strong (high hit rate, positive edge).  Audit
(2026-04-21) showed 13 picks rejected ONLY by the CV gate with 75-95% hit
rates, most sitting in the 0.51-0.58 CV band — a narrow bump per stat admits
them without opening the door for extreme-volatility noise.

Caps are keyed by the NBA stat codes used in ``{sport}_prop_scores.stat_type``.
Unknown stats (MLB's ``Hits`` / ``Pitcher Strikeouts``, future NFL codes) fall
back to ``DEFAULT_CV_CAP`` so this module is safe to import from any sport's
scoring adapter without sport-specific branching.
"""
from __future__ import annotations

from typing import Dict, Optional

DEFAULT_CV_CAP: float = 0.50

CV_CAP_BY_STAT: Dict[str, float] = {
    # --- NBA ---
    "PTS": 0.50,
    "PRA": 0.50,
    "PTS+REB": 0.50,
    "PTS+AST": 0.50,
    "REB+AST": 0.55,
    "AST": 0.60,
    "REB": 0.60,
    "3PM": 0.55,
    "STL": 0.65,
    "BLK": 0.65,
    # MLB / NFL entries can be added here without touching callers.
}


def resolve_cv_cap(stat_type: Optional[str]) -> float:
    """Return the stat-aware Safe Haven CV cap, falling back to default."""
    if not stat_type:
        return DEFAULT_CV_CAP
    return CV_CAP_BY_STAT.get(stat_type, DEFAULT_CV_CAP)


__all__ = ["CV_CAP_BY_STAT", "DEFAULT_CV_CAP", "resolve_cv_cap"]
