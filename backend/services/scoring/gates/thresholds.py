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
}
_NBA_WAR_ZONE_BASE = {
    # Final War Zone gating spec (2026-04-24, native gate config).
    # All logic lives here — the UniversalGateEngine evaluates these
    # exactly like any other tier. Re-evaluated in `recompute.py`
    # AFTER slate-level `vision_score` normalization.
    "coverage_gate": {"min_books": 1},
    # Stat-aware CV caps (HARD reject). Unknown stat_family fails
    # closed (no `default` / `max` on purpose).
    "cv_gate": {
        "caps": {
            "pts":        0.45,
            "pra":        0.45,
            "reb":        0.55,
            "ast":        0.55,
            "threes":     0.75,
            "pts_ast":    0.45,
            "pts_reb":    0.45,
            "reb_ast":    0.55,
            "stl":        0.75,
            "blk":        0.75,
            "turnovers":  0.75,
        },
    },
    # Vision-score floor, branched on tp_source (single gate, OR
    # semantics for `one_sided`):
    #   devig      → vs >= 85
    #   one_sided  → vs >= 90 OR hr >= 60
    # Missing tp_source fails closed.
    "vision_score_gate": {
        "by_tp_source": {
            "devig":     {"min_vs": 85.0},
            "one_sided": {"min_vs": 90.0, "or_min_hr": 60.0},
        },
    },
    # Pricing-trap: reject mid-odds/mid-signal props.
    "market_trap_gate": {
        "odds_low": 150, "odds_high": 220,
        "hr_max": 60.0, "vs_max": 90.0,
    },
}


# MLB safe-haven per-stat gates preserved from services/mlb_tier_sorter.py
_MLB_SAFE_HAVEN: Dict[str, Dict[str, Any]] = {
    "hits":              {"cv_max": 0.60, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "total_bases":       {"cv_max": 0.75, "hr_min": 70.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 1.00},
    "hits_runs_rbis":    {"cv_max": 0.80, "hr_min": 85.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 1.00},
    "rbis":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "runs":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "pitching_outs":     {"cv_max": 0.30, "hr_min": 85.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "pitcher_strikeouts":{"cv_max": 0.45, "hr_min": 70.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
    "batter_strikeouts": {"cv_max": 0.75, "hr_min": 85.0, "edge_min": 0.0, "tp_min": 50.0, "min_margin": 0.75},
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

def _mlb_thresholds(per_stat: Dict[str, Dict[str, Any]], *, war_zone: bool = False) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for family, vals in per_stat.items():
        if war_zone:
            out[family] = {
                "coverage_gate": {"min_books": 1},
                "ceiling_gate":  {"min": vals["ceiling_min"]},
                "edge_gate":     {"min": vals["edge_min"]},
            }
        else:
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
                "tp_gate":       {"min": vals["tp_min"]},
            }
    return out


THRESHOLDS: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {
    "nba": {
        "safe_haven":  {"_default": _NBA_SAFE_HAVEN_BASE},
        "front_lines": {"_default": _NBA_FRONT_LINES_BASE},
        "war_zone":    {"_default": _NBA_WAR_ZONE_BASE},
    },
    "mlb": {
        "safe_haven":  _mlb_thresholds(_MLB_SAFE_HAVEN),
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
) -> Dict[str, Dict[str, Any]]:
    """Return the active threshold block for (sport, tier, stat_family).

    Falls back from explicit stat_family → sport-tier ``_default`` → {}.
    """
    by_sport = THRESHOLDS.get((sport or "").lower(), {})
    by_tier = by_sport.get(tier, {})
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
# Hard contract: a prop's FINAL tier is constrained to be one of
# {routed_tier, "unqualified"}. Promotions across buckets are forbidden.
# The constraint is enforced at `scoring_stack.compute_tier` and
# audit-checkable via the persisted `routed_tier` score-doc field.
UNIVERSAL_SAFE_HAVEN_MAX: int = -240
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
