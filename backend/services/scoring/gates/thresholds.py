"""Universal Gate Engine — threshold configuration.

Shape:

    THRESHOLDS[sport][tier][stat_family] = {
        "coverage_gate":  {"min_books": int},
        "hit_rate_gate":  {"min": float, "window": "l20|l10|l5|default"},
        "tp_gate":        {"min": float, "under_floor": Optional[float]},
        "cv_gate":        {"max": float},
        "edge_gate":      {"min": float},
        "ceiling_gate":   {"min": float},
        "context_gate":   {"vetoes": [str, ...]}   # list of veto keys
    }

Any gate whose config is absent is not evaluated.
Adding a new sport is a pure data change.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 2026-05-13 — Stat identity consolidation. The duplicated
# `STAT_FAMILY_ALIASES` table that previously lived here is now derived
# from `services.scoring.canonical_stats` (the single source of truth
# for `external market key → canonical stat_type → stat family → model
# key → display label`). The dict + `resolve_stat_family()` function
# below are kept as thin shims so legacy import sites keep working
# without modification.
from services.scoring.canonical_stats import (
    stat_family as _registry_stat_family,
    iter_sports as _registry_iter_sports,
    market_to_stat_map as _registry_market_to_stat_map,
)


TIERS_ORDERED = ("safe_haven", "front_lines", "war_zone")


# --------------------------------------------------------------------------
# Stat family aliases (DEPRECATED — kept as a derived view of the registry)
# --------------------------------------------------------------------------
# The 2026-05-13 consolidation moved the canonical data to
# `services.scoring.canonical_stats`. This dict is now generated from
# the registry so any import that previously did
# `STAT_FAMILY_ALIASES["nba"]["pr"]` still reads "pts_reb" without code
# changes. Use the new `canonical_stats.stat_family(...)` API for any
# new code.
def _build_alias_view() -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for s in _registry_iter_sports():
        out[s] = {}
        # Direct family entries from the registry
        from services.scoring.canonical_stats import _REGISTRY  # type: ignore
        reg = _REGISTRY.get(s)
        if reg is not None:
            out[s].update(reg.stat_to_family)
    return out


STAT_FAMILY_ALIASES: Dict[str, Dict[str, str]] = _build_alias_view()

# Ensure the NFL scaffold (kept here for backward compatibility — the
# engine routes NFL through the same `resolve_stat_family` plumbing
# even though the adapter isn't fully landed yet). New sports should be
# added by calling `canonical_stats.register_sport(...)` instead.
STAT_FAMILY_ALIASES.setdefault("nfl", {}).update({
    "receptions":      "receptions",
    "rushing_yards":   "rushing_yards",
    "receiving_yards": "receiving_yards",
    "passing_yards":   "passing_yards",
})


def resolve_stat_family(sport: str, raw_stat: Optional[str]) -> str:
    """Return the canonical stat family for a sport/raw-stat pair.

    Thin shim over `canonical_stats.stat_family(...)`. Preserves the
    legacy contract:
      • Empty / None → "_default"
      • Unknown stat_type → logs `[STAT_REGISTRY_MISS]` ERROR + returns
        "_default" (so callers' gate-engine logic keeps routing through
        the sport's `_default` config — but unmapped tokens are no
        longer silently invisible).
    """
    if not raw_stat:
        return "_default"
    return _registry_stat_family(sport, raw_stat, strict=False)


# --------------------------------------------------------------------------
# Gate thresholds — config-driven, sport × tier × stat_family
# --------------------------------------------------------------------------
# Safe Haven rebuild (2026-04-24) — four-gate spec:
#   1. vision_score_gate (VS >= 85)
#   2. hit_rate_gate (HR >= 85)
#   3. stat-aware cv_gate (PTS/PRA 0.40, REB/AST 0.45, 3PM 0.55, combos 0.45)
#   4. market_structure_gate (reject alt AND tp_source=one_sided)
# No tp_gate / edge_gate / coverage_gate / context_gate in Safe Haven.
_NBA_SAFE_HAVEN_BASE = {
    # ── Universal OVER-side direction rule (2026-04-29, refactored
    # strict-only 2026-05-15). Strict engine: passes iff
    # projection > line (sign only). Cushion keys ignored — removed
    # 2026-05-17 cleanup. UNDER picks bypass via `applies_to_sides`.
    "direction_gate": {
        "applies_to_sides": ["OVER"],
    },
    # 2026-05-02 — edge floor lowered to 0.0 per user spec. Any
    # non-negative edge qualifies for SH; downstream gates
    # (vision / HR / CV / direction / market-structure) remain the
    # binding quality bar. Replaces the previous 0.01 floor.
    "edge_gate":      {"min": 0.0},
    # 2026-05-01 — L20 floor lowered to 80 per user spec; L5 sub-gate
    # (universal, in `engine.py:_eval_hit_rate`) now BACKS this floor
    # by requiring recent-form L5 ≥ 80 too. Together: "elite L20 only
    # if recent form is still elite". Replaces the previous 85 floor.
    "hit_rate_gate":  {"min": 80.0, "window": "default"},
    # 2026-05-02 — Vision floor lowered from 85 → 80 per user spec
    # post NBA Phase 1 Debias. Debiased projections compressed vision
    # scores; 85 was calibrated pre-debias and was rejecting legit
    # SH candidates (e.g., Tatum 3PM 1.5 OVER @ VS 83.2).
    "vision_score_gate": {"min": 80.0},
    "cv_gate": {
        "caps": {
            "pts":         0.40,
            "pra":         0.40,
            "reb":         0.45,
            "ast":         0.45,
            "threes":      0.55,   # preserved — not lowering
            "pts_reb":     0.45,
            "pts_ast":     0.45,
            "reb_ast":     0.45,
            "stl":         0.55,
            "blk":         0.55,
            "turnovers":   0.55,
        },
    },
    "market_structure_gate": {
        "reject_when": {"is_alt": True, "tp_source": "one_sided"},
    },
    # ── FINAL Safe Haven Override Spec (2026-04-29, NBA) ─────────────
    # Universal override layer activated by this sentinel block. Spec
    # in services/scoring/gates/overrides.py. Engine NEVER touches
    # market_structure_gate / tp_gate / edge_gate via overrides.
    "__safe_haven_overrides__": {
        # Rule 1 — Elite Vision: VS >= 90 AND CV <= 0.35 →
        #          relax hit_rate floor to 75
        "elite_vision": {
            "enabled":             True,
            "min_vision_score":    90.0,
            "max_cv":              0.35,
            "relax_hit_rate_to":   75.0,
        },
        # Rules 2-3 — stat-family CV cap relaxation (HR >= 85 required)
        "stat_family_cv_relax": {
            "reb":    0.60,    # spec rule 2
            "threes": 0.60,    # spec rule 2
            "ast":    0.50,    # spec rule 3
        },
        # Rule 4 — PTS dominance CV BYPASS (cv failure ignored ONLY)
        "pts_dominance": {
            "enabled":                       True,
            "stat_family":                   "pts",
            "min_hit_rate":                  90.0,
            "min_l20_avg_to_line_ratio":     1.75,
        },
    },
}


# ────────────────────────────────────────────────────────────────────
# NBA UNIFIED UNDER RULESET (2026-04-29)
# ────────────────────────────────────────────────────────────────────
# Spec (user, 2026-04-29):
#   Apply ONLY to NBA UNDER side. SH/FL/WZ all share the same UNDER
#   block (same direction / HR / CV / projection-gap behaviour). OVER
#   side is untouched (lives in `_default`).
#
#   Rules:
#     1. Direction: projection < line REQUIRED.
#     2. Base hit rate: HR >= 65.
#     3. CV: stat-family caps (canonical SH map), with HR-conditional
#        relaxation:
#           HR >= 75  →  cap += 0.10
#           HR >= 80  →  CV is no longer a hard fail
#     4. Critical filter: (line - projection) / line >= 0.15
#        (skew vs volatility separator)
#
#   Per-tier preserved config (NOT touched by the UNDER block):
#     • SH UNDER: market_structure_gate, vision_score_gate(min=85)
#     • FL UNDER: coverage_gate, edge_gate(min=5), vision_score_gate
#                 (FL has no vision floor today; we leave that alone),
#                 tp_gate(under_floor=65) — UNDER-side TP floor stays.
#     • WZ UNDER: coverage_gate, vision_score_gate(min=60)
#                 No edge_gate in WZ today (none in spec) — leave off.
# ────────────────────────────────────────────────────────────────────

# Canonical NBA UNDER stat-family CV caps. The user said "use existing
# CV caps by stat family" — these mirror Safe Haven's caps (the only
# tier today that ships a stat-family `caps` map) so UNDER inherits a
# proven baseline before the HR-conditional relax kicks in.
_NBA_UNDER_CV_CAPS: Dict[str, float] = {
    "pts":         0.40,
    "pra":         0.40,
    "reb":         0.45,
    "ast":         0.45,
    "threes":      0.55,
    "pts_reb":     0.45,
    "pts_ast":     0.45,
    "reb_ast":     0.45,
    "stl":         0.55,
    "blk":         0.55,
    "turnovers":   0.55,
}

# HR-conditional CV relax + disable rules (declared in order; engine
# evaluates each in sequence and applies the matched action).
_NBA_UNDER_CV_HR_RELAX = [
    {"min_hr": 75.0, "absolute_add": 0.10},
    {"min_hr": 80.0, "disable_gate":  True},
]

# Direction gate config — UNDER-side only.
# 2026-05-17 cleanup: legacy `max_projection_minus_line` / 
# `min_line_minus_projection_ratio` keys removed. The universal
# direction-gate refactor (2026-05-15, services/scoring/gates/engine.py
# ::_eval_direction) is strict-only — it consults `applies_to_sides`
# and the SIGN of `projection - line` only. Cushion/margin keys were
# never enforced by the strict engine; carrying them in the config
# made stored-threshold audits misleading. Other quality concerns
# (margin, CV, edge, hit-rate) live in their OWN gates.
_NBA_UNDER_DIRECTION_GATE = {
    "applies_to_sides": ["UNDER"],
}

_NBA_SAFE_HAVEN_UNDER = {
    # No coverage_gate in SH (parity with `_default`).
    "direction_gate":  _NBA_UNDER_DIRECTION_GATE,
    "hit_rate_gate":   {"min": 65.0, "window": "default"},
    "cv_gate":         {"caps": _NBA_UNDER_CV_CAPS,
                        "hr_relax": _NBA_UNDER_CV_HR_RELAX},
    # 2026-05-02 — SH UNDER vision floor lowered from 85 → 80 per user
    # spec (mirrors OVER-side change; same debias rationale).
    "vision_score_gate": {"min": 80.0},
    # Preserve SH market-structure rule (alt + one_sided UNDERs are
    # still rejected — per "do not override market structure gates").
    "market_structure_gate": {
        "reject_when": {"is_alt": True, "tp_source": "one_sided"},
    },
}

_NBA_FRONT_LINES_UNDER = {
    "coverage_gate":   {"min_books": 1},
    "direction_gate":  _NBA_UNDER_DIRECTION_GATE,
    "hit_rate_gate":   {"min": 65.0, "window": "default"},
    "cv_gate":         {"caps": _NBA_UNDER_CV_CAPS,
                        "hr_relax": _NBA_UNDER_CV_HR_RELAX},
    # Preserve FL UNDER-side TP floor (was 65 in OVER `_default` via
    # `under_floor`). Spec says "do NOT change TP calculation"; we keep
    # the existing TP gate exactly as in OVER `_default`.
    "tp_gate":         {"min": 50.0, "under_floor": 65.0},
    "edge_gate":       {"min": 5.0},
    # FL has no vision_score_gate in `_default` — leave UNDER unchanged.
}

_NBA_WAR_ZONE_UNDER = {
    "coverage_gate":   {"min_books": 1},
    "direction_gate":  _NBA_UNDER_DIRECTION_GATE,
    "hit_rate_gate":   {"min": 65.0, "window": "default"},
    "cv_gate":         {"caps": _NBA_UNDER_CV_CAPS,
                        "hr_relax": _NBA_UNDER_CV_HR_RELAX},
    "vision_score_gate": {"min": 60.0},
    # No edge_gate in WZ today — preserve.
}
_NBA_FRONT_LINES_BASE = {
    "coverage_gate": {"min_books": 1},
    # Scenario B promoted to live config (2026-04-23). See
    # /app/memory/PRD.md "Front Lines Threshold Tradeoff" entry for
    # the simulation run. Tightens HR 60→70 and loosens OVER-side
    # TP 55→50. UNDER-side TP floor (65) and CV / edge / coverage
    # gates are unchanged — per the user's scenario spec.
    # Net effect on the live board: 53 passing → 90 passing (+37),
    # avg HR of the passing set rose from ~70% → 75.5%.
    "hit_rate_gate": {"min": 70.0, "window": "default"},
    "tp_gate":       {"min": 50.0, "under_floor": 65.0},
    "cv_gate":       {"max": 0.75},
    "edge_gate":     {"min": 5.0},
    # ── Direction-consistency (NBA FL OVER only, 2026-04-29; strict
    # cleanup 2026-05-17). Strict engine: passes iff projection > line.
    # Cushion keys removed. UNDER picks bypass via `applies_to_sides`.
    "direction_gate": {
        "applies_to_sides": ["OVER"],
    },
    # ── NBA Front Lines OVER conditional override layer ───────────
    # Spec: see services/scoring/gates/overrides.py docstring.
    # Rescues SPECIFIC tp_gate / cv_gate failures only — NEVER
    # touches market_structure / direction / hit_rate / vision /
    # coverage / edge gates. UNDER-side picks skip this entire block
    # (engine guards on `metrics.side == "OVER"` before invoking).
    "__front_lines_over_overrides__": {
        # Rule 2 — 3PM TP override: HR > 75 AND projection >= line
        # → tp floor relaxed to 45.
        "threes_tp": {
            "enabled":        True,
            "min_hit_rate":   75.0,
            "relax_tp_to":    45.0,
        },
        # Rule 3 — AST CV override: HR > 85 AND projection >= line
        # → cv cap relaxed to 0.95.
        "ast_cv": {
            "enabled":        True,
            "min_hit_rate":   85.0,
            "relax_cv_to":    0.95,
        },
        # Rule 4 — PTS dominance: HR ≥ 75 AND L20/line ≥ 1.5 AND
        # projection >= line → bypass TP / CV failures.
        "pts_dominance": {
            "enabled":                       True,
            "min_hit_rate":                  75.0,
            "min_l20_avg_to_line_ratio":     1.5,
        },
    },
}
_NBA_WAR_ZONE_BASE = {
    # War Zone gate config refactor (2026-04-29, per user spec).
    # Uses ONLY the universal gate types — no WZ-only logic.
    # REMOVED: market_trap_gate, tp_source vision branching, stat-family
    # cv caps map.
    #
    # OVER rules (UNDER side gates auto-skip via direction_gate config):
    #   • direction_gate  : projection >= line (OVER only, 2026-05-01
    #                       relaxed 1.05 → 1.00 as part of WZ volume
    #                       tuning; model-edge alone is gate enough)
    #   • hit_rate_gate   : HR >= 55  (WZ opts OUT of the universal
    #                       L5 sub-gate — the tier thesis IS variance
    #                       of recent form; see `enforce_l5_subgate`)
    #   • cv_gate         : CV <= 0.75 (flat)
    #   • vision_score_gate : v2 >= 60 (uses extras['vision_score_v2'])
    #
    # Conditional expansion (HR > 70 → CV cap 1.00) lives in the
    # `__war_zone_overrides__` block — implemented by the universal
    # override layer so the gate config stays declarative.
    "coverage_gate": {"min_books": 1},
    "direction_gate": {
        "applies_to_sides": ["OVER"],
    },
    # ── Universal OVER-side edge floor (2026-04-29): edge > 0 strict.
    # Direction is proj >= line above (relaxed to 1.00 on 2026-05-01).
    "edge_gate":         {"min": 0.01},
    # 2026-05-01 — War Zone explicitly DISABLES the universal L5
    # sub-gate. L5 drawdowns ARE the high-variance shots WZ exists to
    # take; enforcing L5 >= L20 floor kills the tier's supply (1 pick
    # across 920 rejects). Safe Haven + Front Lines still enforce it.
    "hit_rate_gate":     {"min": 50.0, "window": "default",
                          "enforce_l5_subgate": False},
    "cv_gate":           {"max": 0.75},
    "vision_score_gate": {"min": 60.0},
    "__war_zone_overrides__": {
        # 2026-05-09 — Controlled supply increase per user spec.
        # CV-cap expansion ladder for WZ OVER (highest tier wins).
        # Each tier requires (a) a hit-rate floor AND (b) an edge floor;
        # if both are met, cv_gate failures are rescued provided the
        # actual CV is at or below the tier's relaxed cap.
        # Direction / coverage / edge / vision_score / market_structure
        # gates are NEVER overridden — only `cv_gate` failures.
        # Tiers are evaluated highest → lowest; first match wins.
        "hr_expansion_ladder": [
            # Tier 3 — strongest signal: HR ≥ 80 + edge ≥ 5pp → CV ≤ 1.50
            {"min_hit_rate": 80.0, "min_edge_pct": 5.0,  "relax_cv_to": 1.50},
            # Tier 2 — strong signal: HR ≥ 70 + positive edge → CV ≤ 1.15
            {"min_hit_rate": 70.0, "min_edge_pct": 0.01, "relax_cv_to": 1.15},
        ],
    },
}


# MLB safe-haven per-stat gates (updated 2026-04-29 — user-calibrated for hits/HRRBI/Ks)
_MLB_SAFE_HAVEN: Dict[str, Dict[str, Any]] = {
    "hits":              {"cv_max": 0.90, "hr_min": 70.0, "edge_min": 5.0, "tp_min": 74.0, "min_margin": 0.50},
    "total_bases":       {"cv_max": 0.75, "hr_min": 70.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 1.00},
    "hits_runs_rbis":    {"cv_max": 0.90, "hr_min": 80.0, "edge_min": 4.0, "tp_min": 80.0, "min_margin": 1.00},
    "rbis":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "runs":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "pitching_outs":     {"cv_max": 0.30, "hr_min": 85.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "pitcher_strikeouts":{"cv_max": 0.45, "hr_min": 70.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "batter_strikeouts": {"cv_max": 0.80, "hr_min": 80.0, "edge_min": 4.0, "tp_min": 78.0, "min_margin": 0.50},
    "earned_runs":       {"cv_max": 0.40, "hr_min": 70.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "_default":          {"cv_max": 0.60, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
}
_MLB_FRONT_LINES: Dict[str, Dict[str, Any]] = {
    # 2026-05-13 — NBA-parity rebuild (un-frozen from audit mode).
    # HR/edge/TP values now MATCH `_NBA_FRONT_LINES_BASE`:
    #   • HR floor:   70 (was per-family 60-70)
    #   • edge floor: 5.0 (was per-family 6-15 with universal 0.01 floor)
    #   • TP floor:   50 (was per-family 58-70)
    # CV is PER-FAMILY (more granular than NBA's flat 0.75) so we can
    # fine-tune by stat without disturbing the NBA-shaped gate structure.
    # Initial caps target NBA FL's median CV (~0.40) for the comparable
    # families; Singles/HRR/TB caps tightened from the pre-rebuild
    # values (0.85-1.92 actual CVs were tiering as Front Lines).
    "hits":               {"cv_max": 0.55, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "total_bases":        {"cv_max": 0.70, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "hits_runs_rbis":     {"cv_max": 0.75, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "rbis":               {"cv_max": 0.55, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "runs":               {"cv_max": 0.55, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "pitcher_outs":       {"cv_max": 0.40, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "pitcher_strikeouts": {"cv_max": 0.50, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "batter_strikeouts":  {"cv_max": 0.65, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "earned_runs":        {"cv_max": 0.50, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "singles":            {"cv_max": 0.50, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "batter_walks":       {"cv_max": 0.60, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "walks_allowed":      {"cv_max": 0.60, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
    "_default":           {"cv_max": 0.65, "hr_min": 70.0, "edge_min": 4.0, "tp_min": 50.0},
}
_MLB_WAR_ZONE: Dict[str, Dict[str, Any]] = {
    # 2026-05-16 — FULL REPLACEMENT of MLB War Zone gates per user
    # spec. All prior WZ thresholds (`ceiling_min`, edge_min=30, etc.)
    # are DELETED. _mlb_thresholds() short-circuits the war_zone=True
    # branch when `__mlb_war_zone_rewrite_2026_05_16__` is present and
    # emits the 5-gate spec directly.
    "_default": {"__mlb_war_zone_rewrite_2026_05_16__": True},
}


# ────────────────────────────────────────────────────────────────────
# MLB WAR ZONE — 2026-05-16 final gate spec (user-mandated rewrite)
# ────────────────────────────────────────────────────────────────────
# Source of truth: full user spec dated 2026-05-16.
# Gates (ALL must pass):
#   1. hr_l20 >= 70                        → hit_rate_gate(window=l20,min=70,min_l5=60)
#   2. hr_l5  >= 60                        → same gate's L5 sub-gate
#   3. cv     <= 1.1                       → cv_gate(max=1.1,suppress_binary_swap)
#   4. projection > line  (strict for OVER) → direction_gate (engine strict default)
#                            (mirrored < for UNDER)
#   5. edge   >= 5                          → edge_gate(min=5.0)
# NOT enforced (deliberately removed): tp_gate, ceiling_gate,
# tp_source_gate, market_structure_gate, vision_score_gate,
# margin_gate, regression hard-fail, all override blocks, coverage
# floor > 1, ceiling_min, and any soft direction margin.
# `coverage_gate` is RETAINED at `min_books: 1` strictly so the
# engine's safety guard (`gate_config_missing` when cfg is empty)
# isn't triggered for one-sided/PrizePicks-only props that have
# already been filtered upstream. Removing `coverage_gate` would
# fail-close every prop with `gate_config_missing`.
_MLB_WAR_ZONE_OVER_2026_05_16: Dict[str, Any] = {
    "coverage_gate":   {"min_books": 1},
    "direction_gate":  {"applies_to_sides": ["OVER"]},
    "hit_rate_gate":   {
        "min": 70.0, "window": "l20",
        "enforce_l5_subgate": True, "min_l5": 60.0,
    },
    "cv_gate":         {"max": 1.1, "suppress_binary_swap": True},
    "edge_gate":       {"min": 5.0},
}

_MLB_WAR_ZONE_UNDER_2026_05_16: Dict[str, Any] = {
    "coverage_gate":   {"min_books": 1},
    "direction_gate":  {"applies_to_sides": ["UNDER"]},
    "hit_rate_gate":   {
        "min": 70.0, "window": "l20",
        "enforce_l5_subgate": True, "min_l5": 60.0,
    },
    "cv_gate":         {"max": 1.1, "suppress_binary_swap": True},
    "edge_gate":       {"min": 5.0},
}

def _mlb_thresholds(
    per_stat: Dict[str, Dict[str, Any]],
    *,
    war_zone: bool = False,
    front_lines: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Build MLB threshold map.

    tp_gate semantics are now UNIVERSAL across all sports — model
    probability is always evaluated (see `engine._eval_tp`). Only
    threshold *values* differ by sport/stat/tier.

    2026-05-13 NBA-parity rebuild adds two FL-only knobs:
      • `enforce_l5_subgate=True` on hit_rate_gate (NBA parity — recent
        L5 form must back the L20 hit rate floor).
      • `under_floor=65.0` on tp_gate (NBA FL behaviour — UNDER picks
        face a stricter TP floor than OVER).
    Both are added only when `front_lines=True`."""
    out: Dict[str, Dict[str, Any]] = {}
    for family, vals in per_stat.items():
        # Universal OVER-side direction rule (2026-04-29, refactored
        # strict-only 2026-05-15). The engine consults
        # `applies_to_sides` and the SIGN of `projection - line` only;
        # any cushion/margin key is ignored. Legacy
        # `min_projection_minus_line` removed 2026-05-17 (cleanup).
        _UNIVERSAL_OVER_DIRECTION = {
            "applies_to_sides": ["OVER"],
        }
        # Universal OVER-side rule (2026-04-29): edge > 0 (strictly
        # positive). Per-stat `edge_min` is honoured when stricter
        # than 0.01; otherwise the universal floor takes over.
        _UNIVERSAL_OVER_EDGE_FLOOR = 0.01
        family_edge_min = max(
            float(vals.get("edge_min", 0.0)), _UNIVERSAL_OVER_EDGE_FLOOR
        )
        if war_zone:
            # 2026-05-16 — FULL REPLACEMENT. Ignore family-level
            # values entirely; emit the user-mandated 5-gate spec for
            # every stat family, with side-specific `_default_over` /
            # `_default_under` variants resolved by `resolve_thresholds`.
            # No `ceiling_gate`, no `tp_gate`, no `tp_source_gate`,
            # no margin/cushion soft logic, no overrides.
            out[family] = dict(_MLB_WAR_ZONE_OVER_2026_05_16)
        else:
            hit_rate_block: Dict[str, Any] = {
                "min": vals["hr_min"], "window": "default",
            }
            tp_block: Dict[str, Any] = {"min": vals["tp_min"]}
            if front_lines:
                # NBA-parity additions (2026-05-13).
                hit_rate_block["enforce_l5_subgate"] = True
                tp_block["under_floor"] = 65.0
            gate_block: Dict[str, Any] = {
                "coverage_gate":  {"min_books": 1},
                "direction_gate": _UNIVERSAL_OVER_DIRECTION,
                # cv_gate carries `min_margin` so the engine's MLB+0.5
                # swap can read the per-stat-family margin floor
                # without consulting another table.
                "cv_gate":        {
                    "max": vals["cv_max"],
                    "min_margin": vals.get("min_margin", 0.75),
                },
                "hit_rate_gate":  hit_rate_block,
                "edge_gate":      {"min": family_edge_min},
                "tp_gate":        tp_block,
            }
            if not front_lines:
                # 2026-05-13 — Safe Haven only: reject `tp_source=one_sided`
                # props. User audit of HRR 0.5 OVER rejects showed the
                # 5 closest-edge props were all DK/FD/MGM one-sided
                # chalk (-300 to -500) with no UNDER companion price,
                # which structurally inflates edge_vs_fair by 4-8 pp.
                # FL keeps one_sided picks (lower supply, larger
                # edge/HR/CV floors absorb the inflation there).
                #
                # 2026-05-13 (revision) — narrow override rescue path
                # for elite binary props. Blanket rejection killed
                # legitimately strong picks (e.g. Josh Jung HRR
                # HR_L20=90 / L5=80 / edge 15.6pp). Override allowed
                # ONLY for hitter binary-line stat families (where
                # the one-sided structure reflects real heavy chalk,
                # not an inflation artefact). Pitcher counting stats
                # and continuous markets remain hard-rejected.
                gate_block["tp_source_gate"] = {
                    "required_source": "devig",
                    "one_sided_override": {
                        "allowed_stat_families": [
                            "hits", "hits_runs_rbis", "runs", "rbis",
                            "batter_strikeouts", "stolen_bases", "batter_walks",
                        ],
                        "hr_l20_min": 90.0,
                        "hr_l5_min":  80.0,
                        "min_edge_pp": 5.0,  # fair_prob - implied_prob ≥ 0.05
                        "cv_max":      0.70,
                    },
                }
            out[family] = gate_block
    return out


# ── 2026-05-13 — MLB Front Lines UNDER (NBA-parity rebuild) ────────────
# Mirrors `_NBA_FRONT_LINES_UNDER` exactly in STRUCTURE (HR-relax CV
# ladder, edge floor 5.0, TP gate with under_floor) but uses MLB-tuned
# CV caps per stat family so we can fine-tune by stat type without
# disturbing NBA.
# 2026-05-17 cleanup: legacy `max_projection_minus_line` /
# `min_line_minus_projection_ratio` keys removed (see _NBA_UNDER_DIRECTION_GATE).
_MLB_UNDER_DIRECTION_GATE = {
    "applies_to_sides": ["UNDER"],
}

_MLB_UNDER_CV_CAPS = {
    "hits":               0.55,
    "total_bases":        0.70,
    "hits_runs_rbis":     0.65,
    "rbis":               0.55,
    "runs":               0.55,
    "pitcher_outs":       0.40,
    "pitcher_strikeouts": 0.50,
    "batter_strikeouts":  0.65,
    "earned_runs":        0.50,
    "singles":            0.50,
    "batter_walks":       0.60,
    "walks_allowed":      0.60,
}

# Same HR-relax ladder as NBA FL UNDER — tested numerically.
# HR≥75 → CV cap +0.10. HR≥80 → CV gate disabled entirely.
_MLB_UNDER_CV_HR_RELAX = [
    {"min_hr": 75.0, "absolute_add": 0.10},
    {"min_hr": 80.0, "disable_gate": True},
]

_MLB_FRONT_LINES_UNDER = {
    "coverage_gate":   {"min_books": 1},
    "direction_gate":  _MLB_UNDER_DIRECTION_GATE,
    "hit_rate_gate":   {
        "min": 65.0, "window": "default",
        "enforce_l5_subgate": True,
    },
    "cv_gate":         {
        "caps":     _MLB_UNDER_CV_CAPS,
        "hr_relax": _MLB_UNDER_CV_HR_RELAX,
    },
    "tp_gate":         {"min": 50.0, "under_floor": 65.0},
    "edge_gate":       {"min": 5.0},
}




THRESHOLDS: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {
    "nba": {
        "safe_haven":  {"_default": _NBA_SAFE_HAVEN_BASE,
                         "_default_under": _NBA_SAFE_HAVEN_UNDER},
        "front_lines": {"_default": _NBA_FRONT_LINES_BASE,
                         "_default_under": _NBA_FRONT_LINES_UNDER},
        "war_zone":    {"_default": _NBA_WAR_ZONE_BASE,
                         "_default_under": _NBA_WAR_ZONE_UNDER},
    },
    "mlb": {
        # tp_gate semantics are universal (model-probability) across
        # all sports — see `_eval_tp` in engine.py. Only thresholds
        # differ per sport/tier/stat.
        "safe_haven":  _mlb_thresholds(_MLB_SAFE_HAVEN),
        # 2026-05-13 NBA-parity rebuild: FL gates produce
        # `enforce_l5_subgate=True` + `tp_gate.under_floor=65.0`
        # via the `front_lines=True` flag. `_default_under` mirrors
        # `_NBA_FRONT_LINES_UNDER` (gap ratio 0.15 + HR-relax CV ladder
        # + edge_gate min 5.0). Override layer injected onto `_default`
        # after _mlb_thresholds builds it (NBA-parity rescue rules
        # adapted to MLB stats: pitcher_strikeouts_tp ↔ NBA threes_tp,
        # batter_strikeouts_cv ↔ NBA ast_cv, hits_dominance ↔ NBA
        # pts_dominance).
        "front_lines": {
            **{
                **_mlb_thresholds(_MLB_FRONT_LINES, front_lines=True),
                "_default": {
                    **_mlb_thresholds(_MLB_FRONT_LINES, front_lines=True)["_default"],
                    "__front_lines_over_overrides__": {
                        "pitcher_strikeouts_tp": {
                            "enabled":        True,
                            "min_hit_rate":   75.0,
                            "relax_tp_to":    45.0,
                        },
                        "batter_strikeouts_cv": {
                            "enabled":        True,
                            "min_hit_rate":   85.0,
                            "relax_cv_to":    0.95,
                        },
                        "hits_dominance": {
                            "enabled":                       True,
                            "min_hit_rate":                  75.0,
                            "min_l20_avg_to_line_ratio":     1.5,
                        },
                    },
                },
            },
            "_default_under": _MLB_FRONT_LINES_UNDER,
        },
        "war_zone":    {
            # 2026-05-16 — FULL REPLACEMENT of MLB War Zone gates.
            # Per-family entries all carry the OVER spec (produced by
            # `_mlb_thresholds(..., war_zone=True)`); the
            # `_default_under` block routes UNDER picks through the
            # mirrored gate set. `resolve_thresholds` resolves
            # `_default_under` before falling back to the family entry
            # for UNDER side.
            **_mlb_thresholds(_MLB_WAR_ZONE, war_zone=True),
            "_default_under": _MLB_WAR_ZONE_UNDER_2026_05_16,
        },
    },
    # Drop NFL adapter in place and start tuning here — engine works
    # as-soon-as the table is populated.
    "nfl": {
        "safe_haven":  {},
        "front_lines": {},
        "war_zone":    {},
    },
}


# ============================================================================
# AUDIT MODE — MLB GATES TEMPORARILY DISABLED (2026-04-25)
# ============================================================================
# Reason: empty MLB SH/FL/WZ boards on a 100% Singles slate forced a config
# audit (see /app/memory/PRD.md "MLB gate-outcome audit, 2026-04-25"). The
# Singles stat family has no bespoke threshold row in `_MLB_*` and falls
# through to `_default` (Hits-calibrated), which rejects ~94% of Singles
# props. Rather than tune one stat in isolation we are reviewing the raw
# odds-routed candidate pool to rebuild the MLB gate suite from scratch.
#
# To re-enable the production MLB gates, set `MLB_GATES_DISABLED_FOR_AUDIT
# = False` and re-run `recompute_sport`. The full pre-audit MLB config is
# preserved unchanged in `_MLB_SAFE_HAVEN`, `_MLB_FRONT_LINES`,
# `_MLB_WAR_ZONE` above. NBA / NFL paths are not touched.
#
# In audit mode every MLB-routed prop passes its gates by default
# (empty config → engine evaluates 0 gates → `failed_gates=[]` → PASS),
# letting tier assignment depend strictly on odds-bucket routing.
MLB_GATES_DISABLED_FOR_AUDIT: bool = False

# ============================================================================
# AUDIT MODE — MLB FRONT LINES gates pulled (2026-04-27)
# ============================================================================
# Reason: rebuilding the MLB Front Lines gate suite from scratch. With the
# legacy thresholds in place, only 1 of 445 routed-FL props qualifies — a
# 99.8% kill rate dominated by `edge_gate` floors that cannot be cleared on
# tightly devigged chalk markets (HRR/Hits/RBIs at -200..-240 ref odds).
# Rather than tune family-by-family in isolation we are disabling all FL
# gates so the unfiltered routed-FL pool is observable end-to-end and a
# new gate suite can be designed against the actual edge / HR / CV
# distribution. SH and WZ MLB gates are NOT affected.
#
# To re-enable the production MLB Front Lines gates, set
# `MLB_FRONT_LINES_GATES_DISABLED = False` and re-run `recompute_sport`.
# The full pre-audit MLB FL config is preserved unchanged in
# `_MLB_FRONT_LINES` and `_MLB_FRONT_LINES_FROZEN_AUDIT_2026_04_25`.
#
# Behaviour: routed_tier == "front_lines" → final tier == "front_lines"
# for every prop that reached the gate stage (i.e. survived 0-book
# exclusion + has a reference_odds in -239..+149).
MLB_FRONT_LINES_GATES_DISABLED: bool = False

# Frozen pre-audit config (kept verbatim so the rebuild has a reference
# to diff against — DO NOT EDIT until the audit lands.)
_MLB_SAFE_HAVEN_FROZEN_AUDIT_2026_04_25 = _mlb_thresholds(_MLB_SAFE_HAVEN)
_MLB_FRONT_LINES_FROZEN_AUDIT_2026_04_25 = _mlb_thresholds(_MLB_FRONT_LINES)
_MLB_WAR_ZONE_FROZEN_AUDIT_2026_04_25 = _mlb_thresholds(_MLB_WAR_ZONE, war_zone=True)

if MLB_GATES_DISABLED_FOR_AUDIT:
    # `coverage_gate` with `min_books: 0` is the engine's only "pass-all"
    # idiom: it requires `book_count >= 0`, true for every prop that
    # survived the universal 0-book-exclusion filter upstream. The
    # engine would fail-close on a literally empty `{}` config (returns
    # `gate_config_missing`), so we register one always-pass gate here
    # to suppress every other gate while still satisfying the engine's
    # safety guard. This keeps the audit cleanly readable: routed tier
    # is preserved iff a prop reaches the gate stage at all.
    _AUDIT_PASS_ALL = {"coverage_gate": {"min_books": 0}}
    THRESHOLDS["mlb"] = {
        "safe_haven":  {"_default": _AUDIT_PASS_ALL},
        "front_lines": {"_default": _AUDIT_PASS_ALL},
        "war_zone":    {"_default": _AUDIT_PASS_ALL},
    }
elif MLB_FRONT_LINES_GATES_DISABLED:
    # Surgical variant: production `_MLB_FRONT_LINES` block remains
    # disabled (not loaded). Instead a TEMPORARY user-calibrated FL
    # config is layered on. Both blocks (frozen production +
    # temporary calibration) coexist; flipping
    # `MLB_FRONT_LINES_GATES_DISABLED = False` swaps in the production
    # block.
    #
    # 2026-04-29 — USER-CALIBRATED FL GATES (per-family OVER, global UNDER).
    # Universal across all sports: every OVER pick must have
    # `edge > 0` AND `projection >= line`. UNDER picks must have
    # `projection <= line`. Per-stat thresholds below for OVER side;
    # one global gate block for UNDER side (HR>=75, CV<=0.85).
    _AUDIT_PASS_ALL = {"coverage_gate": {"min_books": 0}}

    def _mlb_fl_over(*, hr: float, cv: float, tp: Optional[float] = None,
                     ) -> Dict[str, Dict[str, Any]]:
        cfg: Dict[str, Dict[str, Any]] = {
            "coverage_gate": {"min_books": 1},
            # Strict-only direction (2026-05-15 refactor; 2026-05-17
            # cleanup removed legacy cushion key).
            "direction_gate": {"applies_to_sides": ["OVER"]},
            "edge_gate":     {"min": 0.01},          # edge > 0 (strictly positive)
            "hit_rate_gate": {"min": hr, "window": "default"},
            "cv_gate":       {"max": cv},
        }
        if tp is not None:
            cfg["tp_gate"] = {"min": float(tp)}
        return cfg

    _MLB_FL_UNDER_GLOBAL = {
        "coverage_gate": {"min_books": 1},
        # Strict-only direction (2026-05-15 refactor; 2026-05-17
        # cleanup removed legacy cushion keys).
        "direction_gate": {"applies_to_sides": ["UNDER"]},
        "hit_rate_gate": {"min": 75.0, "window": "default"},
        "cv_gate":       {"max": 0.85},
    }

    THRESHOLDS["mlb"]["front_lines"] = {
        # _default fallback (when neither side suffix matches and
        # the family has no entry) — keeps any unknown stat from
        # accidentally crashing the engine.
        "_default": _AUDIT_PASS_ALL,
        # Global UNDER gate (resolves before family lookup when
        # side==UNDER per resolve_thresholds order).
        "_default_under":            _MLB_FL_UNDER_GLOBAL,
        # Per-family OVER configs (resolve_thresholds picks
        # `{family}_over` first when side==OVER).
        "hits_runs_rbis_over":       _mlb_fl_over(hr=80.0, cv=0.85),
        "hits_over":                 _mlb_fl_over(hr=75.0, cv=0.75),
        "total_bases_over":          _mlb_fl_over(hr=80.0, cv=0.90, tp=50.0),
        "batter_strikeouts_over":    _mlb_fl_over(hr=75.0, cv=0.75, tp=60.0),
        "runs_over":                 _mlb_fl_over(hr=75.0, cv=0.75),
        "pitcher_strikeouts_over":   _mlb_fl_over(hr=70.0, cv=0.50),
        "singles_over":              _mlb_fl_over(hr=75.0, cv=0.75),
        "batter_walks_over":         _mlb_fl_over(hr=80.0, cv=0.85),
        "walks_allowed_over":        _mlb_fl_over(hr=80.0, cv=0.85),
        # OVER fallback for any stat family not listed above (e.g.,
        # rbis, pitching_outs, earned_runs, hits_allowed, doubles,
        # stolen_bases, home_runs). Mirrors the looser "Walks" tier
        # so unlisted families still surface.
        "_default_over":             _mlb_fl_over(hr=75.0, cv=0.85),
    }


def resolve_thresholds(
    sport: str, tier: str, stat_family: str,
    side: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return the active threshold block for (sport, tier, stat_family).

    Resolution order:
      1. explicit ``{stat_family}_under`` / ``{stat_family}_over`` (when
         a side is provided)
      2. ``_under_default`` / ``_over_default`` (when a side is provided
         AND that key exists for the tier)
      3. explicit ``stat_family``
      4. ``_default``
      5. ``{}``

    Side-aware UNDER configs let NBA tiers ship a unified UNDER ruleset
    while leaving the OVER-side config (the historical `_default`)
    completely untouched.
    """
    by_sport = THRESHOLDS.get((sport or "").lower(), {})
    by_tier = by_sport.get(tier, {})
    side_norm = (side or "").upper()
    side_suffix = None
    if side_norm == "UNDER":
        side_suffix = "_under"
    elif side_norm == "OVER":
        side_suffix = "_over"

    if side_suffix and stat_family:
        explicit_keyed = f"{stat_family}{side_suffix}"
        if explicit_keyed in by_tier:
            return by_tier[explicit_keyed]
    if side_suffix:
        side_default = f"_default{side_suffix}"
        if side_default in by_tier:
            return by_tier[side_default]

    if stat_family in by_tier:
        return by_tier[stat_family]
    return by_tier.get("_default", {})


# Universal odds-bucket routing (2026-04-25, post-MLB-routing audit).
#
# Tier routing is the FIRST-CLASS pipeline step that decides which gate
# block a prop will be evaluated against. It is sport-agnostic by design
# — markets across NBA / MLB / NFL all price favorites/longshots on the
# same American-odds scale, so a single threshold pair governs all
# sports.
#
# Bucket definition:
#     ref_odds <= -240          → safe_haven  (heavy favorite)
#     -239 <= ref_odds <= +149   → front_lines (mid range)
#     ref_odds >= +150          → war_zone    (longshot)
#     ref_odds is None          → unqualified (no reference market)
#
# ── Universal Tier Routing Boundaries (2026-04-29) ─────────────────
# New odds buckets per FINAL tier-routing spec:
#   Safe Haven  : ref_odds <= -300   (heavy chalk)
#   Front Lines : -299 <= ref_odds <= +149
#   War Zone    : ref_odds >= +150
#
# Hard contract change (2026-04-29): a prop is no longer locked to its
# routed tier. If it fails the SH gate block, it is RE-EVALUATED under
# the FL gate block. If it fails FL, it is re-evaluated under WZ. Only
# props that fail ALL applicable gate blocks are rejected. Implemented
# by `compute_tier` cascade — see `services/scoring/scoring_stack.py`.
UNIVERSAL_SAFE_HAVEN_MAX: int = -300
UNIVERSAL_WAR_ZONE_MIN: int = 150

# Per-sport ODDS_BUCKETS retained as a thin alias on the universal
# constants — read sites continue to work, but every sport now uses
# the same threshold pair. Pre-2026-04-25 NBA was -250 / +150;
# the universal cutover normalises it to -240 / +150.
ODDS_BUCKETS: Dict[str, Dict[str, Any]] = {
    "nba": {
        "safe_haven_max":  UNIVERSAL_SAFE_HAVEN_MAX,
        "war_zone_min":    UNIVERSAL_WAR_ZONE_MIN,
    },
    "mlb": {
        "safe_haven_max":  UNIVERSAL_SAFE_HAVEN_MAX,
        "war_zone_min":    UNIVERSAL_WAR_ZONE_MIN,
    },
    "nfl": {
        "safe_haven_max":  UNIVERSAL_SAFE_HAVEN_MAX,
        "war_zone_min":    UNIVERSAL_WAR_ZONE_MIN,
    },
}


def resolve_target_tier(sport: str, reference_odds: Optional[int]) -> Optional[str]:
    """Map reference_odds → routed tier, or None if no odds.

    `sport` is accepted for backwards-compat but ignored — routing is
    universal across all sports per the 2026-04-25 cutover. Cross-sport
    consistency is the entire point of the routing layer.
    """
    if reference_odds is None:
        return None
    if reference_odds <= UNIVERSAL_SAFE_HAVEN_MAX:
        return "safe_haven"
    if reference_odds >= UNIVERSAL_WAR_ZONE_MIN:
        return "war_zone"
    return "front_lines"
