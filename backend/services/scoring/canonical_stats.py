"""
Universal sport-agnostic stat canonicalizer.

Single source of truth for the full stat identity chain:

    external market key  →  canonical stat_type  →  stat family  →  model key  →  display label

Built as a registry. New sports plug in by calling `register_sport(...)` once
at import time — no edits required in `universal_odds_sync`, scoring adapters,
or gate threshold modules. The three previously-duplicated mapping layers
(`SPORT_API_CONFIG[*]['stat_type_map']`, `nba_scoring._MARKET_TO_STAT`, and
`gates/thresholds.STAT_FAMILY_ALIASES`) now read from this module.

Design constraints (2026-05-13 user spec):
  • Registry-driven by sport — zero hardcoded sport branches in the resolver
  • Backward compatible — every key previously resolved still resolves
  • Fail-loud diagnostics — unmapped stat_types emit a `[STAT_REGISTRY_MISS]`
    ERROR log (sport + stat_type) so silent `_default` fallthroughs surface
    instead of disappearing from tiers
  • Idempotent — `canonical_stat_type(sport, "PTS")` returns `"PTS"`
    (canonical tokens round-trip through every API)

Public surface:
    register_sport(sport, *, market_to_stat, stat_to_family,
                   stat_to_model=None, stat_to_display=None)
    canonical_stat_type(sport, raw)              -> str
    stat_family(sport, stat_type, *, strict)     -> str
    model_key(sport, stat_type)                  -> Optional[str]
    display_label(sport, stat_type)              -> str
    markets_for_sport(sport)                     -> List[str]
    market_to_stat_map(sport)                    -> Dict[str, str]
    iter_sports()                                -> List[str]
    validate_sport(sport)                        -> Dict[str, Any]
    miss_counters()                              -> Dict[str, int]
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StatFamilyMissError(KeyError):
    """Raised by `stat_family(..., strict=True)` when a stat_type has no
    registered family for the given sport. Use this in tests / startup
    health checks; callers in production gate code should pass
    `strict=False` (default) which logs + returns `_default` instead."""


@dataclass
class SportStatRegistry:
    """Per-sport stat identity tables. All keys lower-cased on insert."""
    sport:           str
    market_to_stat:  Dict[str, str] = field(default_factory=dict)
    stat_to_family:  Dict[str, str] = field(default_factory=dict)
    stat_to_model:   Dict[str, str] = field(default_factory=dict)
    stat_to_display: Dict[str, str] = field(default_factory=dict)


_REGISTRY: Dict[str, SportStatRegistry] = {}
_REGISTRY_LOCK = RLock()
_MISS_COUNTERS: Dict[str, int] = defaultdict(int)

# Read-side family-alias normalization. When historical/legacy rows emit
# `stat_family="strikeouts"` or `"pitcher_walks"`, downstream callers
# (gate engine, grid-search audits, output writers) should still resolve
# these to the same canonical family that fresh writes emit. The
# registry is the source of truth; this mapping is rebuilt on every
# `register_sport(...)` from `stat_to_family` so any alias added to
# the registry automatically propagates.
_FAMILY_ALIAS: Dict[str, Dict[str, str]] = {}  # sport -> alias -> canonical


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


# ----------------------------------------------------------------------
# Public registration API
# ----------------------------------------------------------------------
def register_sport(
    sport:           str,
    *,
    market_to_stat:  Dict[str, str],
    stat_to_family:  Dict[str, str],
    stat_to_model:   Optional[Dict[str, str]] = None,
    stat_to_display: Optional[Dict[str, str]] = None,
) -> SportStatRegistry:
    """Register (or overwrite) the canonical stat identity chain for one sport.

    All keys are case-folded on insert so the resolver is case-insensitive.
    Values are preserved exactly as supplied (callers control the canonical
    casing of stat_types, families, model keys, and display labels).
    """
    sport_key = _norm(sport)
    if not sport_key:
        raise ValueError("register_sport: sport must be a non-empty string")

    reg = SportStatRegistry(
        sport=sport_key,
        market_to_stat={_norm(k): v for k, v in (market_to_stat or {}).items()},
        stat_to_family={_norm(k): v for k, v in (stat_to_family or {}).items()},
        stat_to_model={_norm(k): v for k, v in (stat_to_model or {}).items()}
        if stat_to_model else {},
        stat_to_display={_norm(k): v for k, v in (stat_to_display or {}).items()}
        if stat_to_display else {},
    )
    # Auto-extend: every value in `market_to_stat` (the canonical stat_type)
    # must round-trip — i.e., `canonical_stat_type(sport, "PTS")` returns
    # `"PTS"`. We seed the lookup with the canonical token itself so callers
    # can pass either the raw market key OR the canonical stat_type and get
    # the same answer.
    for _market, canonical in dict(reg.market_to_stat).items():
        reg.market_to_stat.setdefault(_norm(canonical), canonical)

    with _REGISTRY_LOCK:
        _REGISTRY[sport_key] = reg
        # Rebuild family alias table: every unique canonical family in
        # `stat_to_family.values()` maps to itself, and every alias key
        # in `stat_to_family.keys()` whose value differs from the key
        # is recorded as `alias -> canonical`. This is the single
        # source of read-side normalization (`canonical_family()`).
        _FAMILY_ALIAS[sport_key] = _build_family_alias(reg)
    logger.info(
        f"[STAT_REGISTRY] registered sport={sport_key!r} "
        f"markets={len(reg.market_to_stat)} families={len(reg.stat_to_family)} "
        f"models={len(reg.stat_to_model)} displays={len(reg.stat_to_display)}"
    )
    return reg


def _build_family_alias(reg: SportStatRegistry) -> Dict[str, str]:
    """Build alias→canonical map from the registered `stat_to_family`.

    Each canonical family value (e.g. `"batter_strikeouts"`) maps to
    itself. Each `stat_to_family` key whose value is the canonical
    becomes an alias for it (e.g. `"strikeouts"` →
    `"batter_strikeouts"`). The mechanically-normalised form (lowercase
    + spaces→underscores) of every key also points to the same
    canonical, so legacy lowercase rows resolve correctly.
    """
    out: Dict[str, str] = {}
    for fam in set(reg.stat_to_family.values()):
        out[_norm(fam)] = fam
    for key, fam in reg.stat_to_family.items():
        norm_key = _norm(key)
        mech = norm_key.replace(" ", "_")
        out.setdefault(norm_key, fam)
        out.setdefault(mech, fam)
    return out


# ----------------------------------------------------------------------
# Public lookup API
# ----------------------------------------------------------------------
def canonical_stat_type(sport: str, raw: Optional[str]) -> str:
    """Resolve any external market key OR already-canonical stat_type to
    the sport's canonical stat_type token.

    Idempotent: feeding a canonical token back through returns it unchanged.
    Falls through to the raw input (preserving caller casing) when the
    sport / key is not registered. Never raises.
    """
    if not raw:
        return raw or ""
    reg = _REGISTRY.get(_norm(sport))
    if reg is None:
        return raw
    return reg.market_to_stat.get(_norm(raw), raw)


def stat_family(
    sport: str, stat_type: Optional[str], *, strict: bool = False
) -> str:
    """Return the canonical stat family for a `(sport, stat_type)` pair.

    Resolution order:
      1. Direct alias lookup (case-insensitive)
      2. Convert canonical stat_type to family via the registered map
      3. Lowercase + space→underscore mechanical fallback (matches
         legacy `resolve_stat_family` behavior so historical callers keep
         working until the registry is fully populated)

    On miss:
      • Increments `_MISS_COUNTERS[sport]` for observability
      • Emits `[STAT_REGISTRY_MISS]` at ERROR level so silent
        `_default` fallthroughs become visible in logs
      • If `strict=True`, raises `StatFamilyMissError` so tests / startup
        health checks can fail loudly
      • Otherwise returns `_default` (preserves today's gate engine
        semantics — caller decides what to do with `_default`)
    """
    if not stat_type:
        return "_default"
    reg = _REGISTRY.get(_norm(sport))
    if reg is None:
        return "_default"

    key = _norm(stat_type)
    if key in reg.stat_to_family:
        return reg.stat_to_family[key]

    # Mechanical fallback (matches legacy `resolve_stat_family` shape).
    mechanical = key.replace(" ", "_")
    if mechanical in reg.stat_to_family:
        return reg.stat_to_family[mechanical]
    # Treat the lowercased+normalized key itself as a possible family if
    # the registered family value equals the stat_type (e.g. "pts" → "pts").
    if mechanical in set(reg.stat_to_family.values()):
        return mechanical

    # Miss — diagnostic
    _MISS_COUNTERS[_norm(sport)] += 1
    msg = (
        f"[STAT_REGISTRY_MISS] sport={sport!r} stat_type={stat_type!r} "
        f"(normalized={mechanical!r}) — no family registered; "
        f"falling back to _default"
    )
    if strict:
        logger.error(msg)
        raise StatFamilyMissError(f"{sport}:{stat_type}")
    logger.error(msg)
    return "_default"


def market_to_family(
    sport: str, market_key: Optional[str], *, strict: bool = False
) -> str:
    """One-shot resolver — market_key → canonical stat family.

    Composes `canonical_stat_type` + `stat_family` so callers never
    have to do it themselves. THIS is the function every writer
    (normalizer, feature cache, replay engine, output projector)
    should call to stamp `stat_family` on a row.

    Idempotent: handing in an already-canonical family also returns
    the same family (via the alias table).
    """
    if not market_key:
        return "_default"
    sport_key = _norm(sport)
    # 1) market_key → canonical stat_type (handles `_alternate` suffix
    #    via the registry's market list which carries both variants).
    stat_t = canonical_stat_type(sport, market_key)
    # 2) canonical stat_type → family.
    fam = stat_family(sport, stat_t, strict=False)
    if fam != "_default":
        return fam
    # 3) Legacy fallback: the input might already BE a family name
    #    (or an alias of one). Read-side normalization rescues it.
    alias = _FAMILY_ALIAS.get(sport_key, {}).get(_norm(market_key))
    if alias is not None:
        return alias
    # 4) Strip `_alternate` suffix and retry mechanically — matches
    #    the legacy `scripts/odds_api_backfill/sport_markets.py`
    #    contract. Idempotent for anything already canonical.
    raw = _norm(market_key)
    if raw.endswith("_alternate"):
        raw = raw[: -len("_alternate")]
    rescued = _FAMILY_ALIAS.get(sport_key, {}).get(raw)
    if rescued is not None:
        return rescued
    _MISS_COUNTERS[sport_key] += 1
    msg = (
        f"[STAT_REGISTRY_MISS] market_to_family sport={sport!r} "
        f"market_key={market_key!r} — no family resolved; "
        f"returning _default"
    )
    if strict:
        logger.error(msg)
        raise StatFamilyMissError(f"{sport}:{market_key}")
    logger.error(msg)
    return "_default"


def canonical_family(
    sport: str, family_or_alias: Optional[str], *, strict: bool = False
) -> str:
    """Resolve a (possibly legacy / aliased) stat-family value to its
    canonical form for THIS sport.

    Use this at READ time anywhere downstream consumes a stored
    `stat_family` value that might predate the canonicalisation fix.
    Examples handled:
      • `"strikeouts"`     → `"batter_strikeouts"` (MLB)
      • `"pitcher_walks"`  → `"walks_allowed"`     (MLB)
      • `"Hits"` / `"hits"` / `"HITS"` → `"hits"`  (case-insensitive)
      • Already-canonical input → returned unchanged.

    Falls back to the input (preserving caller casing) when the sport
    isn't registered. Never raises unless `strict=True` and the
    family is unrecognised.
    """
    if not family_or_alias:
        return family_or_alias or ""
    aliases = _FAMILY_ALIAS.get(_norm(sport))
    if aliases is None:
        return family_or_alias
    norm = _norm(family_or_alias)
    if norm in aliases:
        return aliases[norm]
    mech = norm.replace(" ", "_")
    if mech in aliases:
        return aliases[mech]
    if strict:
        raise StatFamilyMissError(f"{sport}:{family_or_alias}")
    return family_or_alias



def model_key(sport: str, stat_type: Optional[str]) -> Optional[str]:
    """Return the per-sport model artifact key (e.g. 'pts' → loads vk2_pts.pkl).

    Returns None when the sport does not register model keys (e.g. MLB uses
    HF v3 family-routing instead of per-stat model artifacts). Idempotent.
    """
    if not stat_type:
        return None
    reg = _REGISTRY.get(_norm(sport))
    if reg is None or not reg.stat_to_model:
        return None
    return reg.stat_to_model.get(_norm(stat_type))


def display_label(sport: str, stat_type: Optional[str]) -> str:
    """Human-facing label (UI section headers). Falls back to the
    canonical stat_type unchanged when no display label is registered."""
    if not stat_type:
        return stat_type or ""
    reg = _REGISTRY.get(_norm(sport))
    if reg is None or not reg.stat_to_display:
        return stat_type
    return reg.stat_to_display.get(_norm(stat_type), stat_type)


def markets_for_sport(sport: str) -> List[str]:
    """All external market keys registered for a sport (excluding the
    canonical-token self-aliases auto-seeded by `register_sport`). Used by
    universal_odds_sync to build the Odds API request market list."""
    reg = _REGISTRY.get(_norm(sport))
    if reg is None:
        return []
    canonicals = {_norm(v) for v in reg.market_to_stat.values()}
    return sorted(
        k for k in reg.market_to_stat.keys() if k not in canonicals
    )


def market_to_stat_map(sport: str) -> Dict[str, str]:
    """Return a copy of the sport's external-market → canonical-stat dict
    (excludes the canonical self-aliases). Drop-in replacement for the
    legacy `SPORT_API_CONFIG[sport]['stat_type_map']` dict."""
    reg = _REGISTRY.get(_norm(sport))
    if reg is None:
        return {}
    canonicals = {_norm(v) for v in reg.market_to_stat.values()}
    return {
        k: v for k, v in reg.market_to_stat.items() if k not in canonicals
    }


def iter_sports() -> List[str]:
    """All currently-registered sport keys."""
    with _REGISTRY_LOCK:
        return sorted(_REGISTRY.keys())


def miss_counters() -> Dict[str, int]:
    """Snapshot of the `[STAT_REGISTRY_MISS]` counter, keyed by sport.
    Operators expose this through admin endpoints to surface drift."""
    return dict(_MISS_COUNTERS)


def validate_sport(sport: str) -> Dict[str, Any]:
    """Audit a registered sport. Returns a structured report:

        {
          "sport":              "nba",
          "ok":                 True/False,
          "n_markets":          26,
          "n_families":         11,
          "n_models":           5,
          "canonical_tokens":   {"PTS", "REB", ...},
          "families_missing_canonical": [],   # canonical tokens with no family
          "markets_missing_canonical": [],    # markets whose canonical has no family
        }

    Used by `/api/admin/canonical-stats/audit` and by the pytest suite
    to confirm every emitted stat_type round-trips."""
    reg = _REGISTRY.get(_norm(sport))
    if reg is None:
        return {"sport": sport, "ok": False, "error": "not_registered"}

    canonicals = sorted({v for v in reg.market_to_stat.values()})
    families_missing_canonical = [
        c for c in canonicals
        if _norm(c) not in reg.stat_to_family
    ]
    # Also surface families whose canonical never appears in market_to_stat
    # (rare but a useful drift signal):
    orphan_families = sorted(
        k for k in reg.stat_to_family.keys()
        if k not in reg.market_to_stat
    )
    return {
        "sport":                       reg.sport,
        "ok":                          not families_missing_canonical,
        "n_markets":                   len(market_to_stat_map(reg.sport)),
        "n_families":                  len(reg.stat_to_family),
        "n_models":                    len(reg.stat_to_model),
        "n_displays":                  len(reg.stat_to_display),
        "canonical_tokens":            canonicals,
        "families_missing_canonical":  families_missing_canonical,
        "orphan_families":             orphan_families,
        "miss_counter":                _MISS_COUNTERS.get(reg.sport, 0),
    }


# ----------------------------------------------------------------------
# Built-in registrations — NBA + MLB
# ----------------------------------------------------------------------
# IMPORTANT: keep these aligned with the historical data in
#   • services/universal_odds_sync.SPORT_API_CONFIG
#   • services/scoring/adapters/nba_scoring._MARKET_TO_STAT
#   • services/scoring/gates/thresholds.STAT_FAMILY_ALIASES
# Until those modules fully migrate to the registry, those dicts may be
# wider than this — but every key they contain MUST resolve here.

# ---- NBA ----
_NBA_MARKET_TO_STAT: Dict[str, str] = {
    # Standard markets — short canonical SSOT tokens
    "player_points":                                "PTS",
    "player_points_alternate":                      "PTS",
    "player_rebounds":                              "REB",
    "player_rebounds_alternate":                    "REB",
    "player_assists":                               "AST",
    "player_assists_alternate":                     "AST",
    "player_points_rebounds_assists":               "PRA",
    "player_points_rebounds_assists_alternate":     "PRA",
    "player_threes":                                "3PM",
    "player_threes_alternate":                      "3PM",
    "player_steals":                                "STL",
    "player_steals_alternate":                      "STL",
    "player_blocks":                                "BLK",
    "player_blocks_alternate":                      "BLK",
    "player_turnovers":                             "TO",
    "player_turnovers_alternate":                   "TO",
    # 2026-05-13 — SSOT-collapsed 2-way combos
    "player_points_rebounds":                       "PR",
    "player_points_rebounds_alternate":             "PR",
    "player_points_assists":                        "PA",
    "player_points_assists_alternate":              "PA",
    "player_rebounds_assists":                      "RA",
    "player_rebounds_assists_alternate":            "RA",
    # Optional combo (BLK+STL); registered for future activation
    "player_blocks_steals":                         "BLST",
    "player_blocks_steals_alternate":               "BLST",
}

_NBA_STAT_TO_FAMILY: Dict[str, str] = {
    # Direct canonical → family
    "pts":   "pts",
    "reb":   "reb",
    "ast":   "ast",
    "pra":   "pra",
    "3pm":   "threes",
    "stl":   "stl",
    "blk":   "blk",
    "to":    "turnovers",
    "pr":    "pts_reb",
    "pa":    "pts_ast",
    "ra":    "reb_ast",
    "blst":  "blocks_steals",
    # Legacy long-form aliases — kept for any code path that still emits
    # the Odds API market key as `stat_type`. Removing these requires
    # confirming zero call sites still pass the long form.
    "player_points":                                "pts",
    "player_points_alternate":                      "pts",
    "player_rebounds":                              "reb",
    "player_rebounds_alternate":                    "reb",
    "player_assists":                               "ast",
    "player_assists_alternate":                     "ast",
    "player_points_rebounds_assists":               "pra",
    "player_points_rebounds_assists_alternate":     "pra",
    "player_threes":                                "threes",
    "player_threes_alternate":                      "threes",
    "player_steals":                                "stl",
    "player_steals_alternate":                      "stl",
    "player_blocks":                                "blk",
    "player_blocks_alternate":                      "blk",
    "player_points_rebounds":                       "pts_reb",
    "player_points_rebounds_alternate":             "pts_reb",
    "player_points_assists":                        "pts_ast",
    "player_points_assists_alternate":              "pts_ast",
    "player_rebounds_assists":                      "reb_ast",
    "player_rebounds_assists_alternate":            "reb_ast",
    "player_blocks_steals":                         "blocks_steals",
    "player_blocks_steals_alternate":               "blocks_steals",
    "player_turnovers":                             "turnovers",
    "player_turnovers_alternate":                   "turnovers",
}

_NBA_STAT_TO_MODEL: Dict[str, str] = {
    "pts": "pts", "reb": "reb", "ast": "ast", "3pm": "3pm", "pra": "pra",
}

_NBA_STAT_TO_DISPLAY: Dict[str, str] = {
    "pts": "PTS", "reb": "REB", "ast": "AST", "pra": "PRA", "3pm": "3PM",
    "stl": "STL", "blk": "BLK", "to": "TO",
    "pr":  "P+R", "pa":  "P+A", "ra":  "R+A", "blst": "BLK+STL",
}

# ---- MLB ----
_MLB_MARKET_TO_STAT: Dict[str, str] = {
    # Batter (standard + alternate variants collapse to same canonical)
    "batter_home_runs":              "Home Runs",
    "batter_home_runs_alternate":    "Home Runs",
    "batter_hits":                   "Hits",
    "batter_hits_alternate":         "Hits",
    "batter_total_bases":            "Total Bases",
    "batter_total_bases_alternate":  "Total Bases",
    "batter_rbis":                   "RBIs",
    "batter_rbis_alternate":         "RBIs",
    "batter_runs_scored":            "Runs",
    "batter_runs_scored_alternate":  "Runs",
    "batter_stolen_bases":           "Stolen Bases",
    "batter_stolen_bases_alternate": "Stolen Bases",
    "batter_walks":                  "Batter Walks",
    "batter_walks_alternate":        "Batter Walks",
    "batter_strikeouts":             "Batter Strikeouts",
    "batter_strikeouts_alternate":   "Batter Strikeouts",
    "batter_singles":                "Singles",
    "batter_singles_alternate":      "Singles",
    "batter_doubles":                "Doubles",
    "batter_doubles_alternate":      "Doubles",
    "batter_triples":                "Triples",
    "batter_triples_alternate":      "Triples",
    "batter_hits_runs_rbis":             "Hits+Runs+RBIs",
    "batter_hits_runs_rbis_alternate":   "Hits+Runs+RBIs",
    "batter_total_bases_runs_rbis":           "Total Bases+Runs+RBIs",
    "batter_total_bases_runs_rbis_alternate": "Total Bases+Runs+RBIs",
    "batter_hits_runs":           "Hits+Runs",
    "batter_hits_runs_alternate": "Hits+Runs",
    # Pitcher
    "pitcher_strikeouts":           "Pitcher Strikeouts",
    "pitcher_strikeouts_alternate": "Pitcher Strikeouts",
    "pitcher_walks":                "Walks Allowed",
    "pitcher_walks_alternate":      "Walks Allowed",
    "pitcher_hits_allowed":            "Hits Allowed",
    "pitcher_hits_allowed_alternate":  "Hits Allowed",
    "pitcher_earned_runs":             "Earned Runs",
    "pitcher_earned_runs_alternate":   "Earned Runs",
    "pitcher_outs":                 "Pitcher Outs",
    "pitcher_outs_alternate":       "Pitcher Outs",
    "pitcher_record_a_win":         "Pitcher Win",
}

_MLB_STAT_TO_FAMILY: Dict[str, str] = {
    # Canonical stat_type (case-folded) → family
    "hits":               "hits",
    "total bases":        "total_bases",
    "hits+runs+rbis":     "hits_runs_rbis",
    "total bases+runs+rbis": "total_bases_runs_rbis",
    "hits+runs":          "hits_runs",
    "rbis":               "rbis",
    "runs":               "runs",
    "home runs":          "home_runs",
    "stolen bases":       "stolen_bases",
    "singles":            "singles",
    "doubles":            "doubles",
    "triples":            "triples",
    "batter walks":       "batter_walks",
    "batter strikeouts":  "batter_strikeouts",
    # 2026-05-17 — Bare-name alias for the MLB batter-K family.
    # Historical / canonical replay rows can emit `stat_family="strikeouts"`
    # (legacy market_to_stat_family output), but the universal gate
    # engine + one-sided override allow-list keys off
    # `batter_strikeouts`. Registering the alias HERE in the canonical
    # SSOT makes every downstream resolver — gate override, vision_v2,
    # pp registry — see the canonical family without per-call-site
    # patches. `pitcher_strikeouts` is intentionally NOT aliased; it
    # is a structurally distinct family.
    "strikeouts":         "batter_strikeouts",
    # 2026-05-18 — Bare-name alias for the pitcher walks-allowed family.
    # Historical replay rows emit `stat_family="pitcher_walks"` (the
    # raw Odds API market key), but the canonical family token is
    # `walks_allowed`. Registering the alias here lets `canonical_family`
    # normalize old-data reads consistently.
    "pitcher_walks":      "walks_allowed",
    "pitcher strikeouts": "pitcher_strikeouts",
    "walks allowed":      "walks_allowed",
    "hits allowed":       "hits_allowed",
    "earned runs":        "earned_runs",
    "pitcher outs":       "pitching_outs",
    "pitcher win":        "pitcher_win",
}

_MLB_STAT_TO_DISPLAY: Dict[str, str] = {
    "hits": "Hits", "total bases": "Total Bases",
    "hits+runs+rbis": "Hits+Runs+RBIs",
    "rbis": "RBIs", "runs": "Runs",
    "home runs": "Home Runs", "stolen bases": "Stolen Bases",
    "singles": "Singles", "doubles": "Doubles", "triples": "Triples",
    "batter walks": "Batter Walks", "batter strikeouts": "Batter Strikeouts",
    "pitcher strikeouts": "Pitcher Strikeouts",
    "walks allowed": "Walks Allowed", "hits allowed": "Hits Allowed",
    "earned runs": "Earned Runs", "pitcher outs": "Pitcher Outs",
}

# Eager registration on import — guarantees every sport is available
# before any downstream module starts looking up stat identities.
register_sport(
    "nba",
    market_to_stat=_NBA_MARKET_TO_STAT,
    stat_to_family=_NBA_STAT_TO_FAMILY,
    stat_to_model=_NBA_STAT_TO_MODEL,
    stat_to_display=_NBA_STAT_TO_DISPLAY,
)

register_sport(
    "mlb",
    market_to_stat=_MLB_MARKET_TO_STAT,
    stat_to_family=_MLB_STAT_TO_FAMILY,
    stat_to_display=_MLB_STAT_TO_DISPLAY,
)


__all__ = [
    "StatFamilyMissError",
    "SportStatRegistry",
    "register_sport",
    "canonical_stat_type",
    "stat_family",
    "market_to_family",
    "canonical_family",
    "model_key",
    "display_label",
    "markets_for_sport",
    "market_to_stat_map",
    "iter_sports",
    "miss_counters",
    "validate_sport",
]
