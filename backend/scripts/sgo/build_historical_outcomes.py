"""
build_historical_outcomes.py — derive sgo_pp_research_outcomes.

Reads:   sgo_pp_research_core_enriched   (immutable; never mutated)
         sgo_player_stats                (immutable reference; never mutated)
Writes:  sgo_pp_research_outcomes        (idempotent upserts)

Joins every enriched PP-anchored prop with the actual player stat for the
same game, resolves composite stats (PRA, fantasyScore, hits+runs+rbi, etc.)
via a pluggable stat-resolver registry, and stamps:
    actual_value, outcome (WIN|LOSS|PUSH|UNRESOLVED), outcome_numeric
    (1|0|0.5|None), hit, push, margin_vs_line, outcome_resolved, resolved_at,
    grading_version, stat_family

OOM-safe:
    Chunked by game_date. Each date loads sgo_player_stats for that date
    into a {(event_id, player_id): stats_dict} map once, then streams enriched
    docs for the same date, grades, bulk_write upserts in batches of 1000.

Idempotent / resumable:
    Unique key (event_id, player_id, stat_id, side, line, period_id).
    --resume skips docs already at the current GRADING_VERSION.

Usage:
    python -m scripts.sgo.build_historical_outcomes \\
        --league MLB --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.build_historical_outcomes --dry-run
    python -m scripts.sgo.build_historical_outcomes --drop-existing --yes
    python -m scripts.sgo.build_historical_outcomes --resume
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")  # preview fallback
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

SRC_COLL     = "sgo_pp_research_core_enriched"
STATS_COLL   = "sgo_player_stats"
OUT_COLL     = "sgo_pp_research_outcomes"
GRADING_VERSION = "v1"


# ───────────────────────────── stat resolver registry ─────────────────────
def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _g(stats: Dict[str, Any], *keys: str) -> Any:
    """First-match across multiple key variants (case-insensitive fallback)."""
    if not stats:
        return None
    for k in keys:
        if k in stats and stats[k] is not None:
            return stats[k]
    lower_map = {k.lower(): v for k, v in stats.items()
                  if isinstance(k, str)}
    for k in keys:
        v = lower_map.get(k.lower())
        if v is not None:
            return v
    return None


def _sum_or_none(*vals: Optional[float]) -> Optional[float]:
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


# Each resolver: stats_dict -> Optional[float]
STAT_RESOLVERS: Dict[str, Callable[[Dict[str, Any]], Optional[float]]] = {
    # ─── MLB batting (atomic) ───
    "batting_hits":          lambda s: _num(_g(s, "hits", "batting_hits", "H")),
    "batting_runs":          lambda s: _num(_g(s, "runs", "batting_runs", "R")),
    "batting_rbi":           lambda s: _num(_g(s, "rbi", "RBI", "batting_rbi")),
    "batting_homeRuns":      lambda s: _num(_g(s, "homeRuns", "home_runs",
                                                "HR", "hr", "batting_homeRuns")),
    "batting_totalBases":    lambda s: _num(_g(s, "totalBases", "total_bases",
                                                "TB", "batting_totalBases")),
    "batting_strikeouts":    lambda s: _num(_g(s, "batting_strikeouts",
                                                "strikeouts", "K", "SO")),
    "batting_walks":         lambda s: _num(_g(s, "walks", "batting_walks", "BB")),
    "batting_stolenBases":   lambda s: _num(_g(s, "stolenBases", "stolen_bases",
                                                "SB")),
    "batting_singles":       lambda s: _num(_g(s, "singles", "1B")),
    "batting_doubles":       lambda s: _num(_g(s, "doubles", "2B")),
    "batting_triples":       lambda s: _num(_g(s, "triples", "3B")),
    # ─── MLB pitching ───
    "pitcher_strikeouts":    lambda s: _num(_g(s, "pitching_strikeouts",
                                                "pitcher_strikeouts",
                                                "strikeoutsPitched", "SO")),
    "pitcher_hits_allowed":  lambda s: _num(_g(s, "pitching_hits",
                                                "pitcher_hits_allowed",
                                                "hitsAllowed", "hits_allowed")),
    "pitching_outs":         lambda s: _num(_g(s, "pitching_outs", "outs",
                                                "outsPitched")),
    "pitcher_earned_runs":   lambda s: _num(_g(s, "pitching_earnedRuns",
                                                "earnedRuns", "earned_runs",
                                                "ER", "pitcher_earned_runs")),
    "pitcher_walks":         lambda s: _num(_g(s, "pitching_basesOnBalls",
                                                "pitcher_walks",
                                                "walksAllowed", "walks_allowed")),
    # ─── MLB composites ───
    "hits_runs_rbis":        lambda s: _sum_or_none(
        _num(_g(s, "hits", "H")),
        _num(_g(s, "runs", "R")),
        _num(_g(s, "rbi", "RBI"))),
    "fantasyScore":          lambda s: _num(_g(s, "fantasyScore",
                                                "fantasy_score",
                                                "fantasyPoints")),
    # ─── NBA atomic ───
    "points":                lambda s: _num(_g(s, "points", "PTS")),
    "rebounds":              lambda s: _num(_g(s, "rebounds", "REB",
                                                "totalRebounds")),
    "assists":               lambda s: _num(_g(s, "assists", "AST")),
    "steals":                lambda s: _num(_g(s, "steals", "STL")),
    "blocks":                lambda s: _num(_g(s, "blocks", "BLK")),
    "turnovers":             lambda s: _num(_g(s, "turnovers", "TO")),
    "threePointersMade":     lambda s: _num(_g(s, "threePointersMade",
                                                "three_pointers_made", "3PM")),
    # ─── NBA composites ───
    "pts_reb_ast":           lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "rebounds", "REB", "totalRebounds")),
        _num(_g(s, "assists", "AST"))),
    "pts_reb":               lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "rebounds", "REB", "totalRebounds"))),
    "pts_ast":               lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "assists", "AST"))),
    "reb_ast":               lambda s: _sum_or_none(
        _num(_g(s, "rebounds", "REB", "totalRebounds")),
        _num(_g(s, "assists", "AST"))),
}

# ─── SGO canonical statID aliases ───
# SGO statIDs are the source-of-truth keys we receive in event.results.stats
# (e.g. "pitching_strikeouts", "batting_basesOnBalls", "points+rebounds+assists").
# Map them onto existing resolvers so the outcomes pipeline grades them
# without per-aliasing each one.
_SGO_ALIASES = {
    # MLB
    # batting_basesOnBalls falls through direct-lookup; we override below
    # with an explicit resolver so it doesn't go through batting_walks.
    "batting_hits+runs+rbi": "hits_runs_rbis",
    "pitching_strikeouts":   "pitcher_strikeouts",
    "pitching_hits":         "pitcher_hits_allowed",
    "pitching_earnedRuns":   "pitcher_earned_runs",
    "pitching_pitchesThrown": None,   # explicit resolver below
    # NBA
    "points+rebounds+assists": "pts_reb_ast",
    "points+rebounds":         "pts_reb",
    "points+assists":          "pts_ast",
    "rebounds+assists":        "reb_ast",
    "blocks+steals":           None,  # explicit resolver below
    "minutesPlayed":           None,  # explicit resolver below
}
for _alias, _target in _SGO_ALIASES.items():
    if _target and _target in STAT_RESOLVERS and _alias not in STAT_RESOLVERS:
        STAT_RESOLVERS[_alias] = STAT_RESOLVERS[_target]

# Explicit SGO-statID resolvers that look up the exact SGO key first.
STAT_RESOLVERS["batting_basesOnBalls"] = lambda s: _num(
    _g(s, "batting_basesOnBalls", "batting_walks", "walks",
        "baseOnBalls", "BB"))
STAT_RESOLVERS["batting_RBI"] = lambda s: _num(
    _g(s, "batting_RBI", "batting_rbi", "rbi", "RBI"))

# Add resolver for blocks+steals composite (not in original NBA registry)
if "blocks+steals" not in STAT_RESOLVERS:
    STAT_RESOLVERS["blocks+steals"] = lambda s: _sum_or_none(
        _num(_g(s, "blocks", "BLK")),
        _num(_g(s, "steals", "STL")))

# Add resolver for raw pitches thrown
if "pitching_pitchesThrown" not in STAT_RESOLVERS:
    STAT_RESOLVERS["pitching_pitchesThrown"] = lambda s: _num(
        _g(s, "pitching_pitchesThrown", "pitches_thrown",
            "numberOfPitches", "pitchCount"))

# Add resolver for minutes
if "minutesPlayed" not in STAT_RESOLVERS:
    def _mins_resolver(s):
        v = _g(s, "minutesPlayed", "minutes", "min", "MIN")
        if isinstance(v, str) and ":" in v:
            try:
                m, sec = v.split(":"); return float(m) + float(sec)/60
            except (TypeError, ValueError): return None
        return _num(v)
    STAT_RESOLVERS["minutesPlayed"] = _mins_resolver

# stat_family bucket (for telemetry / coverage reports)
STAT_FAMILY: Dict[str, str] = {
    "batting_hits": "hits", "batting_runs": "runs", "batting_rbi": "rbi",
    "batting_homeRuns": "home_runs", "batting_totalBases": "total_bases",
    "batting_strikeouts": "batting_strikeouts", "batting_walks": "batting_walks",
    "batting_stolenBases": "stolen_bases", "batting_singles": "singles",
    "batting_doubles": "doubles", "batting_triples": "triples",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_hits_allowed": "pitcher_hits_allowed",
    "pitching_outs": "pitching_outs",
    "pitcher_earned_runs": "pitcher_earned_runs",
    "pitcher_walks": "pitcher_walks",
    "hits_runs_rbis": "hits_runs_rbis",
    "fantasyScore": "fantasy_score",
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "steals": "steals", "blocks": "blocks", "turnovers": "turnovers",
    "threePointersMade": "threes_made",
    "pts_reb_ast": "pra", "pts_reb": "pts_reb", "pts_ast": "pts_ast",
    "reb_ast": "reb_ast",
    # SGO canonical statIDs (alongside our internal ones)
    "batting_basesOnBalls":  "batting_walks",
    "batting_hits+runs+rbi": "hits_runs_rbis",
    "pitching_strikeouts":   "pitcher_strikeouts",
    "pitching_hits":         "pitcher_hits_allowed",
    "pitching_earnedRuns":   "pitcher_earned_runs",
    "pitching_pitchesThrown": "pitches_thrown",
    "points+rebounds+assists": "pra",
    "points+rebounds":         "pts_reb",
    "points+assists":          "pts_ast",
    "rebounds+assists":        "reb_ast",
    "blocks+steals":           "blocks_steals",
    "minutesPlayed":           "minutes",
    "batting_RBI":             "rbi",
}


# ─── safe deterministic synthesis ─────────────────────────────────────────
# ONLY for composite stats with universally-agreed component math. Never
# invent formulas (e.g. fantasyScore stays unresolved without an SGO value).

# {stat_id → [list of component stat_ids needed]}
_SAFE_COMPOSITE_COMPONENTS: Dict[str, List[str]] = {
    "batting_totalBases":     ["batting_singles", "batting_doubles",
                                 "batting_triples", "batting_homeRuns"],
    "batting_hits+runs+rbi":  ["batting_hits", "batting_runs", "batting_RBI"],
    "batting_runs+rbi":       ["batting_runs", "batting_RBI"],
}

# Coefficient maps for components (default 1.0)
_SAFE_COMPOSITE_COEFFS: Dict[str, Dict[str, float]] = {
    "batting_totalBases": {
        "batting_singles": 1.0, "batting_doubles": 2.0,
        "batting_triples": 3.0, "batting_homeRuns": 4.0,
    },
}


def _try_safe_synthesis(stat_id: str,
                          canonical: Dict[str, Any]
                          ) -> Tuple[Optional[float], Optional[List[str]]]:
    """Synthesize composite from atomic components in `canonical`.

    Returns (value, components_used) on success, (None, missing_components)
    on failure. Caller can use the missing list for reporting.
    """
    components = _SAFE_COMPOSITE_COMPONENTS.get(stat_id)
    if not components:
        return None, None
    coeffs = _SAFE_COMPOSITE_COEFFS.get(stat_id, {})
    missing = []
    total = 0.0
    for c in components:
        v = _num(canonical.get(c)) if isinstance(canonical, dict) else None
        if v is None:
            missing.append(c)
        else:
            total += v * coeffs.get(c, 1.0)
    if missing:
        return None, missing
    return total, components


# ─── unresolved-row classifier ────────────────────────────────────────────
# Heuristics: which canonical key prefixes indicate which player "role"?
_BATTING_PREFIX = "batting_"
_PITCHING_PREFIX = "pitching_"


def _classify_unresolved(stat_id: str,
                            canonical: Dict[str, Any],
                            norm: Dict[str, Any]
                            ) -> str:
    """Return a precise reason bucket for an unresolved row.

    Buckets:
      player_not_in_results              ← caller handles (no dict at all)
      missing_canonical_field_but_components_available
      missing_composite_but_components_available
      role_mismatch
      field_omitted_possible_zero
      missing_field_no_components
      canonical_value_null               ← key present but value=None
      missing_field_no_canonical_dict    ← canonical empty, norm has nothing
    """
    if not isinstance(canonical, dict) or not canonical:
        if not isinstance(norm, dict) or not norm:
            return "player_not_in_results"
        return "missing_field_no_canonical_dict"

    # Detect role from what canonical actually carries
    has_batting  = any(isinstance(k, str) and k.startswith(_BATTING_PREFIX)
                         for k in canonical)
    has_pitching = any(isinstance(k, str) and k.startswith(_PITCHING_PREFIX)
                         for k in canonical)

    if stat_id in canonical and canonical[stat_id] is None:
        return "canonical_value_null"

    # Role mismatch: requested stat's role isn't present at all in this row
    if isinstance(stat_id, str):
        if stat_id.startswith(_BATTING_PREFIX) and not has_batting:
            return "role_mismatch"
        if stat_id.startswith(_PITCHING_PREFIX) and not has_pitching:
            return "role_mismatch"

    # Composite stat? Check whether some/all components exist
    components = _SAFE_COMPOSITE_COMPONENTS.get(stat_id)
    if components:
        present = sum(1 for c in components if c in canonical
                       and canonical[c] is not None)
        if present == len(components):
            # All present but we still couldn't resolve → caller bug; treat as
            # derivable (resolver should have synthesized)
            return "missing_composite_but_components_available"
        elif present > 0:
            return "missing_composite_but_components_available"
        else:
            return "missing_field_no_components"

    # Non-composite missing canonical field. Was the role correct AND the row
    # actually carried sibling counting stats? Then the field is most likely
    # omitted-when-zero (SGO sometimes drops zero fields).
    if isinstance(stat_id, str):
        if stat_id.startswith(_BATTING_PREFIX) and has_batting:
            return "field_omitted_possible_zero"
        if stat_id.startswith(_PITCHING_PREFIX) and has_pitching:
            return "field_omitted_possible_zero"

    return "missing_field_no_components"


def resolve_stat_value(
    stat_id: str,
    raw_stats: Optional[Dict[str, Any]],
    canonical_stats: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[float], str, Optional[str], Optional[str]]:
    """Return (value | None, stat_family, reason | None, source | None).

    `source` is set when value is resolved:
        canonical_exact
        canonical_composite_derived
        canonical_family_resolver
        normalized_family_resolver
        direct_key_variant
    `reason` is set when value is None:
        canonical_value_null
        role_mismatch
        missing_composite_but_components_available
        field_omitted_possible_zero
        missing_field_no_components
        missing_field_no_canonical_dict
        player_not_in_results
        no_stats_dict_passed
    """
    fam = STAT_FAMILY.get(stat_id, stat_id or "unknown")
    if raw_stats is None and canonical_stats is None:
        return None, fam, "no_stats_dict_passed", None

    canonical = canonical_stats or {}
    norm = raw_stats or {}

    # 1. EXACT SGO statID match in canonical → fastest, highest-fidelity path
    if stat_id and isinstance(canonical, dict) and stat_id in canonical:
        v = canonical[stat_id]
        n = _num(v)
        if n is not None:
            return n, fam, None, "canonical_exact"
        # Key present but value None → fall through (don't claim unresolved yet)

    # 2. Safe deterministic synthesis from canonical components
    syn_val, syn_components = _try_safe_synthesis(stat_id, canonical)
    if syn_val is not None:
        return syn_val, fam, None, "canonical_composite_derived"

    # 3. SGO-aware family resolver against canonical (composites, aliases)
    fn = STAT_RESOLVERS.get(stat_id)
    if fn is not None and canonical:
        try:
            v = fn(canonical)
        except Exception:
            v = None
        if v is not None:
            return v, fam, None, "canonical_family_resolver"

    # 4. Family resolver against the normalized stats dict
    if fn is not None and norm:
        try:
            v = fn(norm)
        except Exception:
            v = None
        if v is not None:
            return v, fam, None, "normalized_family_resolver"

    # 5. Direct camel/snake key variants on BOTH dicts
    if stat_id:
        variants = [stat_id, stat_id.lower(),
                     stat_id.replace("_", ""), stat_id.replace("-", "_")]
        for d in (canonical, norm):
            if not isinstance(d, dict) or not d:
                continue
            v = _num(_g(d, *variants))
            if v is not None:
                return v, fam, None, "direct_key_variant"

    # Unresolved — classify
    return None, fam, _classify_unresolved(stat_id, canonical, norm), None


# ───────────────────────────── grading ────────────────────────────────────
def grade_outcome(
    side: Optional[str], actual: Optional[float], line: Optional[float]
) -> Dict[str, Any]:
    """Return the outcome dict for a single anchor."""
    if actual is None or line is None or side is None:
        return {
            "actual_value":     actual,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None,
            "push":             None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    side_u = side.upper()
    try:
        line_f = float(line)
        actual_f = float(actual)
    except (TypeError, ValueError):
        return {
            "actual_value":     actual,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None, "push": None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    if actual_f == line_f:
        return {
            "actual_value":     actual_f,
            "outcome":          "PUSH",
            "outcome_numeric":  0.5,
            "hit":              False, "push": True,
            "margin_vs_line":   0.0,
            "outcome_resolved": True,
        }
    if side_u in ("OVER", "YES"):
        won = actual_f > line_f
        margin = actual_f - line_f
    elif side_u in ("UNDER", "NO"):
        won = actual_f < line_f
        margin = line_f - actual_f
    else:
        return {
            "actual_value":     actual_f,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None, "push": None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    return {
        "actual_value":     actual_f,
        "outcome":          "WIN" if won else "LOSS",
        "outcome_numeric":  1 if won else 0,
        "hit":              bool(won),
        "push":             False,
        "margin_vs_line":   margin,
        "outcome_resolved": True,
    }


# ───────────────────────────── indexes ────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db[OUT_COLL]
    await c.create_index(
        [("event_id", ASCENDING), ("player_id", ASCENDING),
         ("stat_id", ASCENDING), ("side", ASCENDING),
         ("line", ASCENDING), ("period_id", ASCENDING)],
        unique=True, name="outcome_anchor_pk")
    await c.create_index("league_id")
    await c.create_index("game_date")
    await c.create_index("player_id")
    await c.create_index("stat_id")
    await c.create_index("stat_family")
    await c.create_index("outcome")
    await c.create_index("outcome_resolved")
    await c.create_index("hit")
    await c.create_index("edge_vs_consensus")
    await c.create_index("has_valid_devig")
    await c.create_index("grading_version")


# ───────────────────────────── per-date processing ────────────────────────
async def _distinct_game_dates(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    start: Optional[str], end: Optional[str],
) -> List[str]:
    match: Dict[str, Any] = {}
    if league: match["league_id"] = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    pipeline: List[Dict[str, Any]] = []
    if match: pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": "$game_date"}})
    pipeline.append({"$sort": {"_id": 1}})
    dates: List[str] = []
    async for r in db[SRC_COLL].aggregate(pipeline, allowDiskUse=True):
        if r.get("_id"):
            dates.append(r["_id"])
    return dates


async def process_date(
    db: AsyncIOMotorDatabase, *, league: Optional[str], game_date: str,
    dry_run: bool, resume: bool,
    debug_unresolved: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # Load stats for this date into multiple lookup maps for fallback joins:
    #   stats_map           : (event_id, player_id) → {stats, canonical}
    #   stats_map_by_entity : (event_id, stat_entity_id) → {stats, canonical}
    #   stats_map_by_name   : (event_id, lower(player_name)) → {stats, canonical}
    stat_match: Dict[str, Any] = {"game_date": game_date}
    if league:
        stat_match["league_id"] = league
    stats_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    stats_map_by_entity: Dict[Tuple[str, str], Dict[str, Any]] = {}
    stats_map_by_name: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for s in db[STATS_COLL].find(stat_match, {"_id": 0}):
        eid = s.get("event_id")
        stats = s.get("stats") or {}
        canonical = s.get("stats_sgo_canonical") or {}
        bundle = {"stats": stats, "canonical": canonical}
        pid = s.get("player_id")
        if eid and pid and (stats or canonical):
            stats_map[(eid, pid)] = bundle
        ent = s.get("stat_entity_id")
        if eid and ent and (stats or canonical):
            stats_map_by_entity[(eid, ent)] = bundle
        nm = (s.get("player_name") or "").strip().lower()
        if eid and nm and (stats or canonical):
            stats_map_by_name[(eid, nm)] = bundle

    # Optional --resume set
    already_done: set = set()
    if resume and not dry_run:
        async for r in db[OUT_COLL].find(
            {"game_date": game_date,
             "grading_version": GRADING_VERSION},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                         "stat_id": 1, "side": 1, "line": 1,
                         "period_id": 1, "outcome_resolved": 1}
        ):
            if r.get("outcome_resolved"):
                already_done.add((r.get("event_id"), r.get("player_id"),
                                    r.get("stat_id"),
                                    (r.get("side") or "").upper(),
                                    r.get("line"), r.get("period_id")))

    upserts: List[UpdateOne] = []
    processed = 0
    resolved = 0
    unresolved = 0
    wins = 0; losses = 0; pushes = 0
    skipped = 0
    fam_counts: Dict[str, int] = {}
    sample_docs: List[Dict[str, Any]] = []
    missing_stats = 0  # joined but stats dict didn't carry the stat
    no_player_stats = 0  # no row at all for (event, player)
    derived_source_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}

    src_match: Dict[str, Any] = {"game_date": game_date}
    if league:
        src_match["league_id"] = league

    async for doc in db[SRC_COLL].find(src_match, {"_id": 0}):
        processed += 1
        uid = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               (doc.get("side") or "").upper(), doc.get("line"),
               doc.get("period_id"))
        if uid in already_done:
            skipped += 1
            continue

        eid = doc.get("event_id")
        pid = doc.get("player_id")
        # Multi-tier join: player_id → stat_entity_id → player_name (lowercase)
        bundle = stats_map.get((eid, pid))
        if bundle is None and pid is not None:
            bundle = stats_map_by_entity.get((eid, pid))
        if bundle is None:
            nm = (doc.get("player_name") or "").strip().lower()
            if eid and nm:
                bundle = stats_map_by_name.get((eid, nm))
        if bundle is None:
            no_player_stats += 1
            actual = None
            fam = STAT_FAMILY.get(doc.get("stat_id"), doc.get("stat_id") or "unknown")
            reason = "player_not_in_results"
            derived_source = None
        else:
            actual, fam, reason, derived_source = resolve_stat_value(
                doc.get("stat_id"),
                bundle.get("stats"),
                bundle.get("canonical"))
            if actual is None:
                missing_stats += 1
                # Group by reason for debug breakdown
                if debug_unresolved is not None:
                    key = (doc.get("stat_id"), fam, reason or "unknown")
                    entry = debug_unresolved.setdefault(
                        key, {"count": 0, "samples": []})
                    entry["count"] += 1
                    if len(entry["samples"]) < 3:
                        canonical_keys = sorted(
                            list((bundle.get("canonical") or {}).keys()))[:25]
                        norm_keys = sorted(
                            list((bundle.get("stats") or {}).keys()))[:25]
                        entry["samples"].append({
                            "event_id": eid, "player_id": pid,
                            "player_name": doc.get("player_name"),
                            "side": doc.get("side"), "line": doc.get("line"),
                            "canonical_keys": canonical_keys,
                            "normalized_keys": norm_keys,
                        })

        outcome = grade_outcome(doc.get("side"), actual, doc.get("line"))

        if outcome["outcome_resolved"]:
            resolved += 1
            if outcome["outcome"] == "WIN":   wins += 1
            elif outcome["outcome"] == "LOSS": losses += 1
            elif outcome["outcome"] == "PUSH": pushes += 1
            if derived_source:
                derived_source_counts[derived_source] = \
                    derived_source_counts.get(derived_source, 0) + 1
        else:
            unresolved += 1
            r = reason or "unknown"
            reason_counts[r] = reason_counts.get(r, 0) + 1
        fam_counts[fam] = fam_counts.get(fam, 0) + 1

        merged = {
            **doc,
            **outcome,
            "stat_family":             fam,
            "grading_version":         GRADING_VERSION,
            "resolved_at":             datetime.now(timezone.utc),
            "derived_value_source":    derived_source,
            "unresolved_reason_detail": reason if not outcome["outcome_resolved"]
                                          else None,
        }
        merged.pop("_id", None)
        if (outcome["outcome_resolved"] and len(sample_docs) < 2):
            sample_docs.append(merged)

        filt = {
            "event_id":  merged["event_id"],
            "player_id": merged["player_id"],
            "stat_id":   merged["stat_id"],
            "side":      merged["side"],
            "line":      merged["line"],
            "period_id": merged["period_id"],
        }
        upserts.append(UpdateOne(filt, {"$set": merged}, upsert=True))
        if len(upserts) >= 1000 and not dry_run:
            await db[OUT_COLL].bulk_write(upserts, ordered=False)
            upserts = []

    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "processed":            processed,
        "resolved":             resolved,
        "unresolved":           unresolved,
        "wins":                 wins,
        "losses":               losses,
        "pushes":               pushes,
        "skipped_resume":       skipped,
        "no_player_stats":      no_player_stats,
        "missing_stats":        missing_stats,
        "fam_counts":           fam_counts,
        "derived_source_counts": derived_source_counts,
        "reason_counts":        reason_counts,
        "sample_docs":          sample_docs,
    }


# ───────────────────────────── main ───────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_historical_outcomes (grading={GRADING_VERSION})")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  drop={args.drop_existing}  resume={args.resume}")

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes (or --dry-run). "
                  f"Refusing to drop {OUT_COLL}.")
            client.close()
            return 2
        if not args.dry_run:
            existing = await db[OUT_COLL].count_documents({})
            print(f"  [drop] {OUT_COLL} has {existing} docs — dropping")
            await db[OUT_COLL].drop()
        else:
            print(f"  [drop] dry-run: would have dropped {OUT_COLL}")

    await ensure_out_indexes(db)

    dates = await _distinct_game_dates(
        db, league=args.league, start=args.start, end=args.end)
    if not dates:
        print(f"  [err] no anchor docs found in {SRC_COLL} for the given window")
        client.close()
        return 1
    print(f"  [plan] {len(dates)} game_dates to process  "
          f"(from {dates[0]} to {dates[-1]})")

    tot: Dict[str, Any] = {
        "dates": 0, "processed": 0, "resolved": 0, "unresolved": 0,
        "wins": 0, "losses": 0, "pushes": 0,
        "skipped_resume": 0, "no_player_stats": 0, "missing_stats": 0,
        "fam_counts": {}, "sample_docs": [],
        "derived_source_counts": {}, "reason_counts": {},
    }
    log_every = 10_000
    next_log = log_every

    debug_unresolved: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = (
        {} if args.debug_unresolved else None)

    for gd in dates:
        try:
            r = await process_date(
                db, league=args.league, game_date=gd,
                dry_run=args.dry_run, resume=args.resume,
                debug_unresolved=debug_unresolved)
        except Exception as e:
            print(f"    [{gd}] FAILED: {e!r}")
            continue
        tot["dates"] += 1
        for k in ("processed", "resolved", "unresolved", "wins", "losses",
                   "pushes", "skipped_resume", "no_player_stats", "missing_stats"):
            tot[k] += r[k]
        for fam, n in r["fam_counts"].items():
            tot["fam_counts"][fam] = tot["fam_counts"].get(fam, 0) + n
        for src, n in r.get("derived_source_counts", {}).items():
            tot["derived_source_counts"][src] = \
                tot["derived_source_counts"].get(src, 0) + n
        for rkey, n in r.get("reason_counts", {}).items():
            tot["reason_counts"][rkey] = tot["reason_counts"].get(rkey, 0) + n
        if r.get("sample_docs") and len(tot["sample_docs"]) < 2:
            tot["sample_docs"].extend(
                r["sample_docs"][:2 - len(tot["sample_docs"])])
        if tot["processed"] >= next_log:
            el = time.time() - t0
            rate = tot["processed"] / el if el > 0 else 0
            print(f"  [{gd}] cumulative processed={tot['processed']:,}  "
                  f"resolved={tot['resolved']:,}  unresolved={tot['unresolved']:,}  "
                  f"W/L/P={tot['wins']:,}/{tot['losses']:,}/{tot['pushes']:,}  "
                  f"rate={rate:,.0f}/s  elapsed={el:,.0f}s")
            next_log += log_every

    runtime = time.time() - t0
    pct = lambda a, b: (100.0 * a / b) if b else 0.0
    print()
    print("=" * 72)
    print(f"  build_historical_outcomes SUMMARY  ({GRADING_VERSION})")
    print("=" * 72)
    print(f"  game_dates processed:    {tot['dates']:,}")
    print(f"  docs processed (input):  {tot['processed']:,}")
    print(f"  resolved:                {tot['resolved']:,}  "
          f"({pct(tot['resolved'], tot['processed']):.2f}%)")
    print(f"  unresolved:              {tot['unresolved']:,}  "
          f"({pct(tot['unresolved'], tot['processed']):.2f}%)")
    print(f"    of which missing player stats row: {tot['no_player_stats']:,}")
    print(f"    of which stat not in player_stats: {tot['missing_stats']:,}")
    print(f"  wins / losses / pushes:  "
          f"{tot['wins']:,} / {tot['losses']:,} / {tot['pushes']:,}")
    if tot["resolved"]:
        print(f"  hit-rate (W / (W+L)):    "
              f"{pct(tot['wins'], tot['wins']+tot['losses']):.2f}%")
    print(f"  skipped (resume):        {tot['skipped_resume']:,}")
    print(f"  runtime:                 {runtime:,.1f}s")

    # Resolved-value source breakdown
    if tot.get("derived_source_counts"):
        print(f"\n  RESOLVED — value source breakdown")
        print(f"  ─────────────────────────────────")
        for src, n in sorted(tot["derived_source_counts"].items(),
                              key=lambda kv: -kv[1]):
            print(f"    {src:<32s}  {n:,}  "
                  f"({pct(n, tot['resolved']):.2f}%)")

    # Unresolved buckets — the new precise classification
    if tot.get("reason_counts"):
        print(f"\n  UNRESOLVED — bucket breakdown")
        print(f"  ─────────────────────────────")
        derivable = (tot["reason_counts"].get(
            "missing_composite_but_components_available", 0))
        truly_missing = (tot["reason_counts"].get("missing_field_no_components", 0)
                          + tot["reason_counts"].get("canonical_value_null", 0)
                          + tot["reason_counts"].get("missing_field_no_canonical_dict", 0))
        non_applicable = (tot["reason_counts"].get("role_mismatch", 0)
                           + tot["reason_counts"].get("player_not_in_results", 0))
        omitted_zero = tot["reason_counts"].get("field_omitted_possible_zero", 0)
        for r, n in sorted(tot["reason_counts"].items(),
                            key=lambda kv: -kv[1]):
            print(f"    {r:<46s}  {n:,}  "
                  f"({pct(n, tot['unresolved']):.2f}%)")
        print(f"\n  → derivable on next patch (components avail):   {derivable:,}")
        print(f"  → likely non-applicable (role/no-show):         {non_applicable:,}")
        print(f"  → field omitted (probably zero, NOT graded):    {omitted_zero:,}")
        print(f"  → truly missing in source:                      {truly_missing:,}")

    # stat_family coverage breakdown (sorted descending)
    if tot["fam_counts"]:
        print(f"\n  stat_family coverage (input docs):")
        for fam, n in sorted(tot["fam_counts"].items(),
                              key=lambda kv: -kv[1])[:30]:
            print(f"    {fam:<30s}  {n:,}")

    if tot["sample_docs"]:
        import json
        print(f"\n  Sample graded docs (first {len(tot['sample_docs'])}):")
        for d in tot["sample_docs"]:
            print("    " + "─" * 60)
            d2 = {**d, "books": (d.get("books") or [])[:2]}
            print("    " + json.dumps(d2, indent=2, default=str)
                              .replace("\n", "\n    "))

    if debug_unresolved:
        print(f"\n  UNRESOLVED BREAKDOWN (grouped by stat_id × stat_family × reason)")
        print(f"  ───────────────────────────────────────────────────────────────")
        sorted_groups = sorted(debug_unresolved.items(),
                                 key=lambda kv: -kv[1]["count"])
        for (stat_id, fam, reason), info in sorted_groups[:40]:
            print(f"  • {info['count']:>6,}  stat_id={stat_id!r:35s}  "
                  f"family={fam!r:20s}  reason={reason}")
            if info["samples"]:
                s = info["samples"][0]
                print(f"        sample: event={s['event_id']}  "
                      f"player={s['player_id']}  name='{s['player_name']}'")
                if s["canonical_keys"]:
                    print(f"        canonical_keys: {s['canonical_keys']}")
                if s["normalized_keys"]:
                    print(f"        normalized_keys: {s['normalized_keys']}")
        if len(sorted_groups) > 40:
            print(f"  ... ({len(sorted_groups)-40} more groups)")
    print("=" * 72)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--start",  default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end",    default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--drop-existing", action="store_true",
                    help=f"Drop {OUT_COLL} before rebuild (requires --yes)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--resume", action="store_true",
                    help=f"Skip docs already resolved at grading_version "
                         f"{GRADING_VERSION}")
    p.add_argument("--debug-unresolved", action="store_true",
                    help="Print a grouped breakdown of every unresolved row "
                         "(stat_id, stat_family, reason, sample player + "
                         "available stats keys). Use to diagnose resolver gaps.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
