"""
Distribution registry & selector.
=================================

Resolves `(sport, stat_family, line, …)` → `Distribution` instance
based on per-sport calibration tables.

A `FamilySpec` is the per-(sport, family) record carrying:
- `default`: the `Distribution` to use when no rule matches.
- `selector`: optional callable `(line, mu, cv, extras) → Distribution`
  that overrides the default when a context-specific distribution is
  more appropriate (e.g. Bernoulli for line=0.5, Normal CDF for line ≥ 1.5).
- `notes`: free-form documentation.

Sport calibration modules (`calibration/mlb.py`, `nba.py`, `nfl.py`)
expose a `MLB_FAMILIES` / `NBA_FAMILIES` / `NFL_FAMILIES` dict mapping
canonical family token → `FamilySpec`.

Family-token canonicalisation
-----------------------------
The registry normalises the input via
`canonical_stat_family(stat, sport).lower().replace(" ", "_")` so all
display variants ("Pitcher Outs", "PITCHER_OUTS", "pitcher outs")
collapse to the same key. Sports / families not present in the
registry resolve to a sport-agnostic default (Normal CDF with
conservative defaults).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from services.scoring.stat_family import canonical_stat_family

from .base import Distribution
from .normal import NormalCDFDistribution, NormalCDFConfig


SelectorFn = Callable[
    [float, Optional[float], Optional[float], Optional[Dict[str, Any]]],
    Distribution,
]


@dataclass(frozen=True)
class FamilySpec:
    default: Distribution
    selector: Optional[SelectorFn] = None
    notes: str = ""


# Sport-agnostic ultra-conservative default — used when no per-sport
# spec exists for a given family. Behaves like the legacy Normal-CDF
# layer with no μ-floor and a wide-CV floor.
DEFAULT_DISTRIBUTION: Distribution = NormalCDFDistribution(NormalCDFConfig(
    cv_floor=0.50,
    mu_floor=0.0,
    sigma_min_absolute=0.20,
    mu_floor_capped=False,
))


def _normalize_family(stat_family: str, sport: str) -> str:
    """Canonical, registry-key form of a stat-family token."""
    canon = canonical_stat_family(stat_family or "", sport=sport)
    return canon.lower().replace(" ", "_")


class DistributionRegistry:
    """Heterogeneous mapping of (sport → family → FamilySpec)."""

    def __init__(self):
        self._sport_tables: Dict[str, Dict[str, FamilySpec]] = {}

    def register_sport(self, sport: str, table: Dict[str, FamilySpec]) -> None:
        self._sport_tables[sport.lower()] = {
            _normalize_family(k, sport): v for k, v in table.items()
        }

    def resolve(
        self,
        sport: str,
        stat_family: str,
        line: float,
        mu: Optional[float] = None,
        cv: Optional[float] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Distribution:
        sp = (sport or "").lower()
        family_key = _normalize_family(stat_family, sp)
        table = self._sport_tables.get(sp, {})
        spec = table.get(family_key)
        if spec is None:
            return DEFAULT_DISTRIBUTION
        if spec.selector is not None:
            try:
                return spec.selector(line, mu, cv, extras)
            except Exception:
                return spec.default
        return spec.default

    def list_sports(self):
        return list(self._sport_tables.keys())

    def list_families(self, sport: str):
        return list((self._sport_tables.get(sport.lower()) or {}).keys())


# ---------------------------------------------------------------------------
# Module-level singleton registry. Populated by the calibration modules
# at import time (see `calibration/__init__.py`).
# ---------------------------------------------------------------------------
_REGISTRY = DistributionRegistry()


def get_registry() -> DistributionRegistry:
    return _REGISTRY


__all__ = [
    "DistributionRegistry", "FamilySpec", "SelectorFn",
    "DEFAULT_DISTRIBUTION", "get_registry",
]
