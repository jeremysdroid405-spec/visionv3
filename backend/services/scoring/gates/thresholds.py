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


TIERS_ORDERED = ("safe_haven", "front_lines", "war_zone")


# --------------------------------------------------------------------------
# Stat family aliases (normalize adapter-specific stat names)
# --------------------------------------------------------------------------
STAT_FAMILY_ALIASES: Dict[str, Dict[str, str]] = {
    "nba": {
        # Short canonical names (scoring adapter emits these directly).
        # 2026-04-28: keys are lowercase because `resolve_stat_family`
        # lowercases the raw input before lookup. Pre-2026-04-28 these
        # were uppercase, which silently failed for "3PM" and "TO" (the
        # only stats whose canonical family name differs from their
        # lowercased token — the others trivially round-trip via the
        # `replace(" ", "_")` fallback).
        "pts": "pts", "reb": "reb", "ast": "ast", "pra": "pra",
        "stl": "stl", "blk": "blk", "3pm": "threes", "to": "turnovers",
        # Raw odds-market names (both standard + alternate variants map
        # to the SAME canonical family — a PTS alt-line has the same
        # underlying stat distribution as the standard PTS market).
        "player_points":                     "pts",
        "player_points_alternate":           "pts",
        "player_rebounds":                   "reb",
        "player_rebounds_alternate":         "reb",
        "player_assists":                    "ast",
        "player_assists_alternate":          "ast",
        "player_points_rebounds_assists":            "pra",
        "player_points_rebounds_assists_alternate":  "pra",
        "player_threes":                     "threes",
        "player_threes_alternate":           "threes",
        "player_steals":                     "stl",
        "player_steals_alternate":           "stl",
        "player_blocks":                     "blk",
        "player_blocks_alternate":           "blk",
        "player_points_rebounds":            "pts_reb",
        "player_points_rebounds_alternate":  "pts_reb",
        "player_points_assists":             "pts_ast",
        "player_points_assists_alternate":   "pts_ast",
        "player_rebounds_assists":           "reb_ast",
        "player_rebounds_assists_alternate": "reb_ast",
        "player_turnovers":                  "turnovers",
        "player_turnovers_alternate":        "turnovers",
    },
    "mlb": {
        "hits": "hits", "total_bases": "total_bases",
        "hits+runs+rbis": "hits_runs_rbis",
        "rbis": "rbis", "runs": "runs",
        "pitching_outs": "pitching_outs",
        "pitcher_strikeouts": "pitcher_strikeouts",
        "earned_runs": "earned_runs",
    },
    "nfl": {
        # Ready-to-fill scaffold. The engine already supports NFL once
        # the adapter + thresholds land.
        "receptions": "receptions",
        "rushing_yards": "rushing_yards",
        "receiving_yards": "receiving_yards",
        "passing_yards": "passing_yards",
    },
}


def resolve_stat_family(sport: str, raw_stat: Optional[str]) -> str:
    """Return the canonical stat family for a sport/raw-stat pair.

    Falls back to the lower-cased raw stat so a new stat type still
    routes somewhere (it will land on the sport's ``_default`` config
    if no direct entry exists).
    """
    if not raw_stat:
        return "_default"
    alias_map = STAT_FAMILY_ALIASES.get((sport or "").lower(), {})
    # Case-insensitive alias lookup (2026-05). Raw stat names from the
    # universal odds sync are mixed-case (e.g., "Hits+Runs+RBIs"); the
    # alias map keys are lowercase. Without this normalize step the
    # lookup misses and the prop falls through to `_default`.
    raw_lower = raw_stat.strip().lower()
    if raw_lower in alias_map:
        return alias_map[raw_lower]
    return raw_lower.replace(" ", "_")


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
    "hit_rate_gate":  {"min": 85.0, "window": "default"},
    "vision_score_gate": {"min": 85.0},
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
_NBA_UNDER_DIRECTION_GATE = {
    "applies_to_sides":                 ["UNDER"],
    "max_projection_minus_line":         0.0,    # proj <= line
    "min_line_minus_projection_ratio":   0.15,   # gap rule
}

_NBA_SAFE_HAVEN_UNDER = {
    # No coverage_gate in SH (parity with `_default`).
    "direction_gate":  _NBA_UNDER_DIRECTION_GATE,
    "hit_rate_gate":   {"min": 65.0, "window": "default"},
    "cv_gate":         {"caps": _NBA_UNDER_CV_CAPS,
                        "hr_relax": _NBA_UNDER_CV_HR_RELAX},
    "vision_score_gate": {"min": 85.0},
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
    # ── Direction-consistency (NBA FL OVER only, 2026-04-29) ──────
    # OVER picks must have projection >= line. UNDER picks bypass
    # the gate (`applies_to_sides` is OVER-only).
    "direction_gate": {
        "applies_to_sides":            ["OVER"],
        "min_projection_minus_line":   0.0,
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
    #   • direction_gate  : projection >= line × 1.05 (OVER only)
    #   • hit_rate_gate   : HR >= 55  (universal)
    #   • cv_gate         : CV <= 0.75 (flat)
    #   • vision_score_gate : v2 >= 60 (uses extras['vision_score_v2'])
    #
    # Conditional expansion (HR > 70 → CV cap 1.00) lives in the
    # `__war_zone_overrides__` block — implemented by the universal
    # override layer so the gate config stays declarative.
    "coverage_gate": {"min_books": 1},
    "direction_gate": {
        "applies_to_sides":              ["OVER"],
        "min_projection_to_line_ratio":  1.05,
    },
    "hit_rate_gate":     {"min": 55.0, "window": "default"},
    "cv_gate":           {"max": 0.75},
    "vision_score_gate": {"min": 60.0},
    "__war_zone_overrides__": {
        # HR-expansion rule: HR > 70 → cv cap relaxed to 1.00.
        # Only `cv_gate` failures may be rescued by this layer.
        "hr_expansion": {
            "enabled":         True,
            "min_hit_rate":    70.0,
            "relax_cv_to":     1.00,
        },
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
    "hits":              {"cv_max": 0.85, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 58.0},
    "total_bases":       {"cv_max": 0.95, "hr_min": 60.0, "edge_min": 15.0, "tp_min": 58.0},
    "hits_runs_rbis":    {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "rbis":              {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "runs":              {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "pitching_outs":     {"cv_max": 0.50, "hr_min": 70.0, "edge_min": 6.0,  "tp_min": 70.0},
    "pitcher_strikeouts":{"cv_max": 0.60, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 65.0},
    "earned_runs":       {"cv_max": 0.55, "hr_min": 65.0, "edge_min": 8.0,  "tp_min": 65.0},
    "_default":          {"cv_max": 0.85, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 58.0},
}
_MLB_WAR_ZONE: Dict[str, Dict[str, Any]] = {
    "hits":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "total_bases":       {"ceiling_min": 35.0, "edge_min": 30.0},
    "hits_runs_rbis":    {"ceiling_min": 35.0, "edge_min": 30.0},
    "rbis":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "runs":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "pitcher_strikeouts":{"ceiling_min": 30.0, "edge_min": 25.0},
    "_default":          {"ceiling_min": 35.0, "edge_min": 30.0},
}

def _mlb_thresholds(per_stat: Dict[str, Dict[str, Any]], *, war_zone: bool = False, tp_source: str = "market") -> Dict[str, Dict[str, Any]]:
    """Build MLB threshold map. `tp_source` controls whether the
    OVER tp_gate evaluates market-implied tp ("market", default) or
    model-derived `p_model_pct` ("model"). Safe Haven uses "model"
    per the 2026-04-29 user spec — the market is already qualifying
    the prop via the −300 odds floor; tp_gate measures whether OUR
    model independently agrees."""
    out: Dict[str, Dict[str, Any]] = {}
    for family, vals in per_stat.items():
        if war_zone:
            out[family] = {
                "coverage_gate": {"min_books": 1},
                "ceiling_gate":  {"min": vals["ceiling_min"]},
                "edge_gate":     {"min": vals["edge_min"]},
            }
        else:
            tp_cfg: Dict[str, Any] = {"min": vals["tp_min"]}
            if tp_source != "market":
                tp_cfg["source"] = tp_source
            out[family] = {
                "coverage_gate": {"min_books": 1},
                # cv_gate carries `min_margin` so the engine's MLB+0.5
                # swap can read the per-stat-family margin floor
                # without consulting another table.
                "cv_gate":       {
                    "max": vals["cv_max"],
                    "min_margin": vals.get("min_margin", 0.75),
                },
                "hit_rate_gate": {"min": vals["hr_min"], "window": "default"},
                "edge_gate":     {"min": vals["edge_min"]},
                "tp_gate":       tp_cfg,
            }
    return out


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
        # Safe Haven: model-prob-driven tp_gate (per 2026-04-29 user spec).
        # Front Lines & War Zone keep market-implied tp_gate semantics.
        "safe_haven":  _mlb_thresholds(_MLB_SAFE_HAVEN, tp_source="model"),
        "front_lines": _mlb_thresholds(_MLB_FRONT_LINES),
        "war_zone":    _mlb_thresholds(_MLB_WAR_ZONE, war_zone=True),
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
MLB_FRONT_LINES_GATES_DISABLED: bool = True

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
    # Surgical variant: pull ONLY the Front Lines gate suite. SH + WZ
    # MLB gates remain on. Same `coverage_gate` min_books=0 idiom so
    # every routed-FL prop passes (the engine returns `failed_gates=[]`
    # when its only gate is satisfied).
    _AUDIT_PASS_ALL = {"coverage_gate": {"min_books": 0}}
    THRESHOLDS["mlb"]["front_lines"] = {"_default": _AUDIT_PASS_ALL}


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
