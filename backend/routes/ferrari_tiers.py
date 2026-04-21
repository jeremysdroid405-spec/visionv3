"""
Ferrari Tier Routes
===================
API endpoints for the "Best of the Best" Ferrari-filtered picks.

Uses Bovada separation as the primary sharp benchmark.
Global 15% kill-switch ensures only elite plays are visible.
Whistle Matrix applies referee-based modifiers to power scores.
"""
from fastapi import APIRouter, HTTPException, Query, Request, Response
import asyncio
from typing import Dict, Any, List, Optional
import logging
import os

from services.ferrari_tier_service import get_ferrari_tier_service
from services.referee_scraper_service import get_referee_service
from services.mlb_matchup_math import get_mlb_matchup_analysis

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Ferrari Tiers"])

# Engine reference for DB access
_db = None
_vegas_killer_model = None
_sync_db = None


_enrichment_cache = {}
_enrichment_cache_mtime = {}

# ---------------------------------------------------------------------------
# Canonical stat-window invariant guard
# ---------------------------------------------------------------------------
# The windowed hit-rate fields (h5_rate / h10_rate / h20_rate) are the single
# source of truth for the UI's "L5 Hit / L10 Hit / L20 Hit" tiles and must
# always match the literal chart math derived from game_logs[:N].
#
# This guard is called immediately before any Ferrari board endpoint serializes
# its response. On a divergence it logs (never raises) and auto-corrects the
# stored hit_rate back to the canonical count-based value. The guard is the
# last line of defense in case a new overlay/merge layer forgets the contract.
# ---------------------------------------------------------------------------
def _assert_canonical_hit_rate_invariant(prop: dict) -> None:
    for hits_key, rate_key, window in (
        ("l5_hits",  "h5_rate",  5),
        ("l10_hits", "h10_rate", 10),
        ("l20_hits", "h20_rate", 20),
    ):
        hits = prop.get(hits_key)
        rate = prop.get(rate_key)
        if hits is None or rate is None:
            continue
        try:
            canonical = round((float(hits) / float(window)) * 100.0, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        # Accept ±0.1 float rounding slack.
        if abs(float(rate) - canonical) > 0.5:
            logger.warning(
                "[CANONICAL_GUARD] %s %s %s stored %s=%s clobbered (true=%s from %s=%s). Auto-correcting.",
                prop.get("player_name"),
                prop.get("stat_type"),
                prop.get("line"),
                rate_key, rate, canonical, hits_key, hits,
            )
            prop[rate_key] = canonical


def _guard_board_picks(picks):
    """Apply the canonical stat-window invariant to a list of picks in place."""
    if not picks:
        return picks
    for p in picks:
        if isinstance(p, dict):
            try:
                _assert_canonical_hit_rate_invariant(p)
            except Exception as e:  # pragma: no cover - guard must never crash
                logger.error("[CANONICAL_GUARD] skipped pick due to %s", e)
    return picks


def _dedupe_picks_by_player(picks, keep: str = "best", sort: Optional[str] = None):
    """Tier-integrity invariant: one player = max one pick per tier.

    Legacy-Ferrari MLB collections and a few NBA paths can surface multiple
    qualifying props for the same player (e.g. PTS 14.5 OVER + PTS 15.5 OVER,
    or RBIs O0.5 + Batter Walks O0.5). The product contract is that each
    tier lists DISTINCT players; alternate lines / alternate stat families
    for the same player must collapse into a single pick.

    Comparison is on a normalized `player_name` — `player_id` is frequently
    None across the MLB collections and some NBA shadow rows.

    Ranking (best-pick-wins):
        1. vision_score           DESC  (primary tier ranker)
        2. pp_utility             DESC
        3. abs(edge_pct or vk_edge) DESC  (fallback for legacy MLB docs)

    `picks` input is expected to be pre-sorted by the reader, but we sort
    again defensively so the invariant holds even when a caller bypasses the
    reader or a downstream overlay mutates ordering.
    """
    if not picks:
        return picks

    def _rank_score(p):
        # Projection-gap sort (opt-in via ?sort=gap). When active, rank by
        # ranking_score_v2 DESC; fall back to vision_score for picks missing
        # the field so we never drop a pick.
        if (sort or "").lower() == "gap":
            rv = p.get("ranking_score_v2")
            rv_r = float(rv) if isinstance(rv, (int, float)) else float("-inf")
            return (rv_r,)
        vs = p.get("vision_score")
        pu = p.get("pp_utility")
        eg = p.get("edge_pct") if p.get("edge_pct") is not None else p.get("vk_edge")
        # Treat None as lowest rank; negative edges are still ranked by magnitude.
        vs_r = float(vs) if isinstance(vs, (int, float)) else float("-inf")
        pu_r = float(pu) if isinstance(pu, (int, float)) else float("-inf")
        eg_r = abs(float(eg)) if isinstance(eg, (int, float)) else float("-inf")
        return (vs_r, pu_r, eg_r)

    ordered = sorted(
        [p for p in picks if isinstance(p, dict)],
        key=_rank_score,
        reverse=True,
    )

    seen = set()
    collapsed_count = 0
    out = []
    for p in ordered:
        pname = (p.get("player_name") or "").strip().lower()
        if not pname:
            # Unnamed rows are never valid tier picks.
            continue
        if pname in seen:
            collapsed_count += 1
            continue
        seen.add(pname)
        out.append(p)

    if collapsed_count:
        logger.info(
            "[TIER_DEDUPE] collapsed %d duplicate-player pick(s); kept %d of %d",
            collapsed_count, len(out), len(picks),
        )
    return out




def _resolve_prop_direction(pick: dict) -> str:
    """Universal, sport-agnostic side extractor for Vision Intel wording.
    Reads `direction` first (PP ships per-side prop rows), falls back to
    `recommendation`. Returns 'OVER' or 'UNDER'."""
    raw = (pick.get("direction") or pick.get("recommendation") or "").upper()
    if "UNDER" in raw:
        return "UNDER"
    return "OVER"


def _generate_vision_fallback(pick: dict) -> str:
    """Generate a punchy fallback vision_intel summary — DIRECTION-AWARE.

    Same reasoning shape for OVER and UNDER; only the interpretive verbs
    and hit-rate field used flip. No sport-specific branches.
    """
    player = pick.get("player_name", "Player")
    stat = pick.get("stat_type", "stat")
    line = pick.get("line") or 0
    vk_pred = pick.get("vk_predicted")
    vk_edge_raw = pick.get("vk_edge") or pick.get("true_edge") or 0
    prob_over = pick.get("vk_prob_over") or pick.get("propvision_true_prob") or 0
    prob_under = pick.get("vk_prob_under")
    if prob_under is None and prob_over:
        prob_under = max(0.0, 100.0 - float(prob_over))

    direction = _resolve_prop_direction(pick)
    is_under = direction == "UNDER"

    # Side-aware hit-rate. Prefer explicit side-keyed field (backend now
    # emits `hit_rate_under` / `hit_rate_over`); fall back to raw OVER rate
    # flipped for UNDER when only legacy h10/l10 is present.
    h_over = pick.get("hit_rate_over") or pick.get("h10_rate") or pick.get("l10_rate") or pick.get("true_hit_rate") or 0
    h_under = pick.get("hit_rate_under")
    if h_under is None and h_over is not None:
        h_under = max(0.0, 100.0 - float(h_over))
    h_side = h_under if is_under else h_over

    # Side-aware edge (vk_edge is OVER-edge; flip for UNDER).
    try:
        vk_edge = -float(vk_edge_raw) if is_under else float(vk_edge_raw)
    except (TypeError, ValueError):
        vk_edge = 0

    # Side-aware probability for the low-confidence fallback.
    prob_side = prob_under if is_under else prob_over
    prob_side = float(prob_side or 0)

    # Verbs / floor-vs-ceiling chosen per side.
    if is_under:
        hammer_verb = "suppressing"           # high under-rate
        relation = "below"                    # projection sits BELOW line
        pick_verb = "ride the under"
        caution_noun = "ceiling"              # UNDER loses if the ceiling breaks
        math_backs = "the math backs the under"
    else:
        hammer_verb = "hammering"
        relation = "above"
        pick_verb = "ride"
        caution_noun = "floor"
        math_backs = "the math backs the over"

    if vk_pred and vk_edge_raw:
        if h_side >= 80:
            return (f"{player} is {hammer_verb} {stat} at an {h_side:.0f}% L10 {direction.lower()} clip — "
                    f"projection of {vk_pred:.1f} sits {relation} the {line} line for a {vk_edge:+.1f} edge to {pick_verb}.")
        elif h_side >= 65:
            return (f"{player} {stat} projects to {vk_pred:.1f} against a {line} line ({direction} {vk_edge:+.1f}). "
                    f"L10 {direction.lower()} rate at {h_side:.0f}% — consistent enough to back.")
        else:
            return (f"{player} {stat} at {line} — model sees {vk_pred:.1f} ({direction} {vk_edge:+.1f}). "
                    f"Stay cautious, the {caution_noun} isn't locked here.")
    elif prob_side >= 70:
        return (f"{player} {stat} at {line} — model confidence at {prob_side:.0f}%. "
                f"L10 {direction.lower()} rate {h_side:.0f}%. {math_backs}.")
    else:
        return (f"{player} {stat} at {line} — riding the {direction.lower()} with {prob_side:.0f}% model probability "
                f"and {h_side:.0f}% recent form.")


def overlay_enrichment_cache(picks: list, sport: str) -> list:
    """Merge vision_intel, scout_badges, and Lasso data from the enrichment cache onto picks."""
    import json as _json
    cache_path = f"/app/backend/data/{'nba' if sport == 'nba' else 'mlb'}_master_active_cache.json"

    # Memory-cache with file mtime check
    try:
        import os
        mtime = os.path.getmtime(cache_path)
        if cache_path not in _enrichment_cache or _enrichment_cache_mtime.get(cache_path) != mtime:
            with open(cache_path) as f:
                _enrichment_cache[cache_path] = _json.load(f).get("props", {})
            _enrichment_cache_mtime[cache_path] = mtime
        cache_props = _enrichment_cache.get(cache_path, {})
    except Exception:
        return picks

    if not cache_props:
        return picks

    cache_lookup = {}
    for pid, cprop in cache_props.items():
        key = f"{cprop.get('player_name', '')}|{cprop.get('stat_type', '')}".lower()
        cache_lookup[key] = cprop

    for pick in picks:
        key = f"{pick.get('player_name', '')}|{pick.get('stat_type', '')}".lower()
        cached = cache_lookup.get(key)
        if cached:
            # Only overlay vision_intel if cache has LONGER text than DB
            # (Phase 7 Gemini writes directly to DB — don't overwrite with stale cache)
            cached_vi = cached.get("vision_intel", "")
            existing_vi = pick.get("vision_intel", "")
            if cached_vi and len(cached_vi) > len(existing_vi):
                pick["vision_intel"] = cached_vi
                pick["vision_summary"] = cached_vi
            if cached.get("scout_badges"):
                pick["scout_badges"] = cached["scout_badges"]
            enriched_suite = cached.get("intel_suite")
            if enriched_suite and enriched_suite.get("lasso"):
                existing = pick.get("intel_suite", {})
                existing.update(enriched_suite)
                pick["intel_suite"] = existing

    # Apply shared volatility profile to ALL picks (both sports)
    from services.volatility_profile import get_volatility_profile
    for pick in picks:
        cv = pick.get("cv")
        stat_type = pick.get("stat_type", "")
        line_val = pick.get("line")
        vol = get_volatility_profile(cv, stat_type, line_val)
        pick["volatility_score"] = vol.score
        pick["volatility_label"] = vol.label
        pick["volatility_family"] = vol.family

        # Reconcile scout_badges with volatility profile
        scout = pick.get("scout_badges") or []
        if isinstance(scout, list):
            if vol.is_extreme:
                # Ensure badge is present
                has_it = any(
                    (b.get("badge_key") if isinstance(b, dict) else b) == "volatility_extreme"
                    for b in scout
                )
                if not has_it:
                    scout.append({"badge_key": "volatility_extreme", "id": "volatility_extreme"})
            else:
                # Remove stale badge that contradicts the profile
                scout = [
                    b for b in scout
                    if (b.get("badge_key") if isinstance(b, dict) else b) != "volatility_extreme"
                ]
            pick["scout_badges"] = scout

    return picks



def enrich_mlb_prop_with_averages(prop: Dict, player_data: Dict = None) -> Dict:
    """DEPRECATED — removed from live path in Stage 5 (2026-04-21, MLB↔NBA
    carbon-copy). Kept as an intentional stub for import-compat with any
    external caller; raises RuntimeError if invoked so accidental
    re-introduction into the live path is caught immediately. The fields
    this function used to produce (L5/L10/L20 rolling averages, hit rates,
    vk_predicted, vk_edge, vk_probability, vision_intel fallback, etc.)
    are EITHER:
      * already persisted on `mlb_prop_scores` by the canonical scoring
        pass (`model_projection`, `p_true_model`, `p_true_active`,
        `ranking_score_v2`, `intel_suite`, `tempo_modifier`), OR
      * built by the shared `_generate_vision_fallback()` helper and the
        `enrich_mlb_intel_suite()` route-time enricher (idempotent guard,
        Stage 4), which run once per pick and consume persisted fields.
    No live path calls this function. Eliminates D5.
    """
    raise RuntimeError(
        "enrich_mlb_prop_with_averages was removed in Stage 5 of the "
        "MLB↔NBA carbon-copy migration. Live MLB picks obtain projection/"
        "probability/hit-rate fields from the canonical scoring pass "
        "(mlb_prop_scores) and Stage-4 scoring-write enrichers. See "
        "PRD.md § 'Stage 5 Complete' for details."
    )


def dedupe_mlb_props(props: List[Dict], sort_key: str = "hit_rate_l10") -> List[Dict]:
    """
    Deduplicate MLB props by player_name + stat_type.
    Keeps the prop with the best sort_key value (highest by default).
    
    Args:
        props: List of prop dictionaries
        sort_key: Field to use for determining which duplicate to keep
    
    Returns:
        Deduplicated list of props
    """
    seen = {}
    for prop in props:
        key = f"{prop.get('player_name')}|{prop.get('stat_type')}"
        
        if key not in seen:
            seen[key] = prop
        else:
            # Keep the one with better sort_key value
            current_val = seen[key].get(sort_key) or 0
            new_val = prop.get(sort_key) or 0
            if new_val > current_val:
                seen[key] = prop
    
    return list(seen.values())


def enrich_mlb_prop_with_tempo(prop: Dict) -> Dict:
    """
    Add tempo intel_suite data to an MLB prop.
    
    Args:
        prop: The prop dictionary to enrich
    
    Returns:
        The enriched prop dictionary with intel_suite.tempo
    """
    from services.mlb_tempo_math import (
        calculate_hitter_tempo, calculate_pitcher_tempo,
        get_hitter_tempo_breakdown, get_pitcher_tempo_breakdown
    )

    # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): if tempo was persisted at
    # scoring-write time (MLBScoringAdapter.enrich_score_doc), this route-time
    # pass is a NO-OP. The persisted fields are authoritative. Eliminates D11
    # as a live route-time dependency.
    if prop and (prop.get("tempo_modifier") is not None
                 or ((prop.get("intel_suite") or {}).get("tempo") is not None)):
        return prop

    stat_key = (prop.get("stat_type") or "").upper()
    is_pitcher = stat_key in ["K", "OUTS", "ER", "STRIKEOUTS", "PITCHER STRIKEOUTS", 
                              "PITCHER_STRIKEOUTS", "PITCHING OUTS", "HITS ALLOWED", "EARNED RUNS"]
    
    # Determine if player is on away team
    player_team = prop.get("team")
    away_team = prop.get("away_team")
    home_team = prop.get("home_team")
    is_away = player_team == away_team if player_team and away_team else prop.get("is_away_team")
    
    # If is_away is still None, try to infer from team names
    if is_away is None and player_team:
        # Check if team abbreviation matches away_team name
        away_abbrs = ["PIT", "NYY", "BOS", "LAD", "ATL", "CHC", "SF", "PHI", "HOU", "TEX"]  # Common away teams
        if home_team and player_team:
            # Player is away if their team is not the home team
            home_abbr = home_team.split()[-1][:3].upper() if home_team else ""
            is_away = player_team.upper() != home_abbr
    
    if is_pitcher:
        ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
        rest = prop.get("bullpen_rest_days")
        mult = calculate_pitcher_tempo(ppa, rest)
        breakdown = get_pitcher_tempo_breakdown(ppa, rest)
        pct = (mult - 1) * 100
        if pct >= 8:
            label = "Pitcher Deep - High K Upside"
        elif pct <= -8:
            label = "Early Hook Risk"
        else:
            label = "Standard Workload"
    else:
        # Get or infer batting order from position
        order = prop.get("batting_order") or prop.get("lineup_position")
        
        # Infer batting order from position if not available
        if order is None:
            position = (prop.get("position") or "").lower()
            # Position-based batting order inference (typical lineup construction)
            position_order_map = {
                "center fielder": 1,  # Leadoff hitters often play CF
                "second baseman": 2,  # Speed guys bat 2nd
                "shortstop": 2,       # Often bat 2nd
                "right fielder": 3,   # Power/avg hitters
                "first baseman": 4,   # Cleanup hitters
                "designated hitter": 4,
                "left fielder": 5,
                "third baseman": 5,
                "catcher": 8,
                "pitcher": 9,
            }
            for pos_key, inferred_order in position_order_map.items():
                if pos_key in position:
                    order = inferred_order
                    break
        
        # Get or estimate team OBP rank
        obp = prop.get("team_obp_rank")
        if obp is None:
            # Estimate team OBP rank based on team abbreviation (2026 rough estimates)
            team_obp_estimates = {
                # Top 10 OBP teams (ranks 1-10)
                "LAD": 2, "NYY": 3, "ATL": 4, "PHI": 5, "SD": 6, 
                "TEX": 7, "HOU": 8, "BOS": 9, "SF": 10, "BAL": 11,
                # Middle tier (ranks 11-20)  
                "TOR": 12, "SEA": 13, "CLE": 14, "MIN": 15, "CHC": 16,
                "MIL": 17, "ARI": 18, "NYM": 19, "TB": 20, "STL": 21,
                # Bottom tier (ranks 21-30)
                "DET": 22, "CIN": 23, "KC": 24, "LAA": 25, "PIT": 26,
                "WSH": 27, "COL": 28, "OAK": 29, "MIA": 30, "CWS": 30,
            }
            team = prop.get("team", "").upper()
            obp = team_obp_estimates.get(team, 15)  # Default to middle
        
        mult = calculate_hitter_tempo(order, is_away, obp)
        breakdown = get_hitter_tempo_breakdown(order, is_away, obp)
        pct = (mult - 1) * 100
        if pct >= 10:
            label = "Max PA Opportunity"
        elif pct >= 5:
            label = "High PA Upside"
        elif pct <= -10:
            label = "Limited PA Risk"
        elif pct <= -5:
            label = "Reduced Opportunity"
        else:
            label = "Standard PA Volume"
    
    prop["tempo_modifier"] = mult
    prop["intel_suite"] = prop.get("intel_suite", {})
    prop["intel_suite"]["tempo"] = {
        "multiplier": mult,
        "display": f"{'+' if pct >= 0 else ''}{pct:.0f}%",
        "tempo_label": label,
        "factors": breakdown.get("factors", []),
        "total_pct": breakdown.get("total_pct", 0),
    }
    prop["intel_suite"]["pace_delta"] = prop["intel_suite"]["tempo"]
    
    return prop


def enrich_mlb_intel_suite(prop: Dict) -> Dict:
    """
    Build complete intel_suite with badges, vision insight, and target lock rationale
    for MLB props based on available data.
    
    MLB Badge Keys (matching BADGE_REGISTRY):
    - pure_contact: Elite contact hitter with exceptional plate discipline
    - high_heat_trap: Facing pitcher with velocity spike (caution)
    - workhorse: Reliable starting pitcher who goes deep
    - barrel_master: Elite power hitter with high barrel rate
    - wind_boost: Wind conditions favor hitting
    - cold_zone: Cold weather reduces power stats
    - bvp_dominator: Strong career vs current pitcher
    - split_advantage: Platoon advantage (L vs R or vice versa)
    - whiff_wizard: Pitcher with elite strikeout ability
    - hitters_haven: Ballpark favors hitters
    - volatility_extreme: High variance/boom-bust player
    
    Args:
        prop: The prop dictionary to enrich
    
    Returns:
        The enriched prop dictionary with full intel_suite
    """
    # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): if intel_suite was persisted
    # at scoring-write time (MLBScoringAdapter.enrich_score_doc), this
    # route-time pass is a NO-OP. Eliminates D11 as a live route-time
    # dependency for core board fields. Route-time only runs when the score
    # doc has no persisted intel_suite (e.g. legacy cached entries).
    existing_is = prop.get("intel_suite") or {}
    if existing_is.get("badges") is not None or existing_is.get("vision_insight") is not None:
        return prop

    player_name = prop.get("player_name", "Unknown")
    stat_type = prop.get("stat_type", "")
    line = prop.get("line", 0)
    team = prop.get("team", "")
    position = (prop.get("position") or "").lower()
    
    # Get opponent from various fields
    opponent = (prop.get("opposing_team") or prop.get("opponent") or 
                prop.get("away_team") if prop.get("team") == prop.get("home_team") 
                else prop.get("home_team") or "OPP")
    
    # Get hit rates and averages
    h5_rate = prop.get("h5_rate") or prop.get("hit_rate_l5") or 0
    h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10") or 0
    l5_avg = prop.get("l5_avg") or 0
    l10_avg = prop.get("l10_avg") or 0
    season_avg = prop.get("season_avg") or prop.get("season_average") or l10_avg
    cv = prop.get("cv") or 0
    # Normalize CV through shared volatility profile
    from services.volatility_profile import get_volatility_profile
    vol_profile = get_volatility_profile(cv, stat_type, line)
    cv = vol_profile.cv_raw
    
    # Classification
    is_goblin = prop.get("is_goblin", False)
    is_demon = prop.get("is_demon", False)
    dk_odds = prop.get("dk_odds")
    
    # Check if pitcher
    is_pitcher = "pitcher" in position or stat_type.lower() in ["strikeouts", "pitcher strikeouts", "pitching outs", "earned runs", "hits allowed"]
    
    # Initialize intel_suite - preserve ALL existing data (lasso, tempo, etc.)
    existing_intel = prop.get("intel_suite", {})
    intel_suite = {**existing_intel, "sport": "mlb"}
    
    # =========================================================================
    # BUILD CONTEXT BADGES (Using BADGE_REGISTRY keys)
    # =========================================================================
    badge_keys = []
    
    # PURE_CONTACT: Elite contact hitter (high hit rate, low strikeout)
    if not is_pitcher and h10_rate >= 70:
        badge_keys.append("pure_contact")
    
    # BARREL_MASTER: Power hitter with high average (suggests extra base hits)
    if not is_pitcher and l10_avg and l10_avg >= 2.0 and "total bases" in stat_type.lower():
        badge_keys.append("barrel_master")
    
    # WORKHORSE: Pitcher who goes deep (for pitcher props)
    if is_pitcher and h10_rate >= 60:
        badge_keys.append("workhorse")
    
    # WHIFF_WIZARD: Pitcher with high strikeout rate
    if is_pitcher and "strikeout" in stat_type.lower() and l10_avg and l10_avg >= 6.0:
        badge_keys.append("whiff_wizard")
    
    # HITTERS_HAVEN: Playing in hitter-friendly park
    hitter_parks = ["COL", "CIN", "TEX", "PHI", "MIL", "BOS"]
    home_team_abbr = (prop.get("home_team", "")[:3]).upper()
    if home_team_abbr in hitter_parks and not is_pitcher:
        badge_keys.append("hitters_haven")
    
    # COLD_ZONE: Cold weather games (early season, northern teams)
    cold_teams = ["MIN", "CHC", "CWS", "DET", "CLE", "BOS", "NYY", "NYM", "PIT", "MIL"]
    if team in cold_teams or home_team_abbr in cold_teams:
        # Only apply in early season (April-May hypothetically)
        badge_keys.append("cold_zone")
    
    # SPLIT_ADVANTAGE: Platoon advantage (simplified - assign based on position tendencies)
    # Lefty hitters vs RHP, Righty hitters vs LHP
    if not is_pitcher and h10_rate >= 65:
        badge_keys.append("split_advantage")
    
    # BVP_DOMINATOR: Strong career numbers vs opponent (use hit rate as proxy)
    if not is_pitcher and h10_rate >= 80:
        badge_keys.append("bvp_dominator")
    
    # Limit to 5 badges
    intel_suite["context_badges"] = badge_keys[:5]
    
    # =========================================================================
    # BUILD VISION INSIGHT (Target Lock Rationale)
    # =========================================================================
    reasons = []
    confidence = "STANDARD"
    
    # Build reasons based on actual data
    if h10_rate >= 70:
        reasons.append(f"Hitting at {h10_rate:.0f}% over last 10 games")
        confidence = "HIGH" if h10_rate >= 80 else "ELEVATED"
    
    if l10_avg and line and l10_avg > line:
        reasons.append(f"L10 average of {l10_avg:.1f} exceeds {line} line")
    
    if cv and cv <= 0.40:
        reasons.append(f"Low variance (CV {cv:.0%}) indicates consistency")
    elif vol_profile.label in ("high", "extreme"):
        reasons.append(f"High variance (CV {cv:.0%}, {vol_profile.family}) - boom/bust potential")
        confidence = "SPECULATIVE"
    
    if is_goblin and dk_odds and dk_odds <= -250:
        reasons.append(f"Sharp money favors this line ({dk_odds})")
        confidence = "HIGH" if confidence != "SPECULATIVE" else confidence
    
    if is_demon:
        reasons.append("Demon line - high risk, high reward play")
        confidence = "SPECULATIVE"
    
    # Matchup insight
    if opponent and opponent != "OPP":
        reasons.append(f"Matchup vs {opponent}")
    
    # Default if no reasons
    if not reasons:
        reasons.append(f"Analyzing {player_name} {stat_type} @ {line}")
    
    # Primary insight text
    if h10_rate >= 80 and l10_avg and l10_avg > line:
        primary = f"{player_name} is locked in - {h10_rate:.0f}% hit rate with {l10_avg:.1f} avg vs {line} line"
    elif is_goblin and h10_rate >= 60:
        primary = f"Goblin play: {player_name} showing {h10_rate:.0f}% consistency on {stat_type}"
    elif is_demon:
        primary = f"Demon alert: {player_name} {stat_type} @ {line} - ceiling play"
    else:
        primary = f"{player_name} {stat_type} @ {line} - {h10_rate:.0f}% L10 hit rate"
    
    intel_suite["vision_insight"] = {
        "primary": primary,
        "reasons": reasons[:4],  # Limit to 4 reasons
        "confidence": confidence
    }
    
    # =========================================================================
    # BUILD STABILITY INDEX from actual data
    # =========================================================================
    if cv:
        if cv <= 0.30:
            stability_score = 90
            consistency = "Elite"
        elif cv <= 0.50:
            stability_score = 70
            consistency = "Stable"
        elif cv <= 0.70:
            stability_score = 50
            consistency = "Variable"
        else:
            stability_score = 30
            consistency = "Volatile"
    else:
        stability_score = 50
        consistency = "Unknown"
    
    intel_suite["stability_index"] = {
        "display": f"{stability_score}%",
        "score": stability_score,
        "consistency": consistency,
        "std_dev": cv
    }
    
    # =========================================================================
    # BUILD MATCHUP DVP
    # =========================================================================
    intel_suite["matchup_dvp"] = {
        "display": f"vs {opponent}",
        "opponent": opponent,
        "opponent_abbr": opponent[:3] if opponent else "OPP",
        "friction_level": "Medium",
        "friction_label": "Standard Matchup",
        "color": "yellow",
        "dvp_rank": 15,
        "stat_type": stat_type
    }
    
    # Set sport
    intel_suite["sport"] = "mlb"
    
    prop["intel_suite"] = intel_suite
    
    # =========================================================================
    # BUILD SCOUT BADGES (Model-driven, cross-sport)
    # Only set if not already populated from cache
    # =========================================================================
    if not prop.get("scout_badges"):
        scout = []
        # hot_streak: L5 avg significantly above season baseline
        if l5_avg and season_avg and l5_avg > season_avg * 1.15:
            scout.append({"badge_key": "hot_streak", "id": "hot_streak"})
        # floor_lock: 90%+ hit rate over L10
        if h10_rate >= 90:
            scout.append({"badge_key": "floor_lock", "id": "floor_lock"})
        # lasso_high_edge: VK edge > 15%
        vk_edge = prop.get("vk_edge") or prop.get("edge_pct") or 0
        if abs(vk_edge) >= 15:
            scout.append({"badge_key": "lasso_high_edge", "id": "lasso_high_edge"})
        # high_fidelity_model: R² > 0.30
        r_squared = prop.get("r_squared") or prop.get("model_r2") or 0
        if r_squared > 0.30:
            scout.append({"badge_key": "high_fidelity_model", "id": "high_fidelity_model"})
        # soft_matchup: Already handled by context badges, but check for general soft indicator
        if h10_rate >= 70 and l10_avg and line and l10_avg > line * 1.1:
            scout.append({"badge_key": "soft_matchup", "id": "soft_matchup"})
        # usage_spike: Vacuum modifier present
        if prop.get("has_vacuum_modifier") or prop.get("vacuum_modifier"):
            scout.append({"badge_key": "usage_spike", "id": "usage_spike"})
        # volatility_extreme: From shared volatility profile (prop-family-aware)
        if vol_profile.is_extreme:
            scout.append({"badge_key": "volatility_extreme", "id": "volatility_extreme"})
        
        # SAFETY OVERRIDE: Block soft_matchup if SP is a buzzsaw (Top 15 rank)
        matchup = prop.get("matchup_analysis") or {}
        sp_rank = (matchup.get("sp_matchup") or {}).get("rank")
        if sp_rank and sp_rank <= 15:
            scout = [b for b in scout if b.get("badge_key") != "soft_matchup"]
        
        prop["scout_badges"] = scout

    # Attach volatility profile to prop for consistent downstream use
    prop["volatility_score"] = vol_profile.score
    prop["volatility_label"] = vol_profile.label
    prop["volatility_family"] = vol_profile.family

    return prop


def set_ferrari_db(db):
    """Set the database reference for Ferrari service."""
    global _db
    _db = db


def get_service():
    """Get the Ferrari tier service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Ferrari service not initialized")
    return get_ferrari_tier_service(_db)


def get_vegas_killer():
    """Get or initialize Vegas Killer model instance using sync PyMongo."""
    global _vegas_killer_model, _sync_db
    if _vegas_killer_model is None:
        try:
            from services.vegas_killer_model import VegasKillerModel
            from pymongo import MongoClient
            
            # Create sync MongoDB connection for VK model
            if _sync_db is None:
                mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
                db_name = os.environ.get("DB_NAME", "pick_vision")
                client = MongoClient(mongo_url)
                _sync_db = client[db_name]
            
            _vegas_killer_model = VegasKillerModel(_sync_db)
            _vegas_killer_model.load_models()
            logger.info("[VK-Ferrari] Vegas Killer model loaded for Ferrari tier enrichment")
        except Exception as e:
            logger.warning(f"[VK-Ferrari] Failed to load Vegas Killer model: {e}")
    return _vegas_killer_model


# =============================================================================
# NBA FERRARI TIER MIGRATION — reads directly from `nba_prop_scores`
# (version_tag="final-nba") instead of the deprecated `elite_*` collections.
#
# Wiring:
#   1. Pull top N scored rows for the target tier, sorted by vision_score DESC.
#   2. Overlay full UI enrichment (intel_suite, hit rates, headshots, team,
#      opponent, price, etc.) from `dg_cached_board` via the natural key
#      (event_id, player_name, stat_type, line, direction).
#   3. Preserve the legacy UI payload contract so UniversalPlayerCard.jsx
#      renders unchanged.
# =============================================================================

_NBA_BOARD_CACHE: Dict[str, Any] = {"lookup": None, "built_at": 0.0}


# Badges that only make sense as OVER signals (trigger language asserts
# "player is producing MORE than usual" in some form). For UNDER picks these
# describe the wrong side — they must be stripped.
_OVER_ONLY_BADGES = {
    "hot_streak", "floor_lock", "lasso_high_edge", "soft_matchup",
    "locked_in", "pay_day", "usage_spike", "barrel_master",
    "pure_contact", "bvp_dominator", "split_advantage",
    "hitters_haven", "wind_boost", "workhorse", "milestone",
    "high_heat_trap", "revenge", "home_cookin",
}


def _badge_key(b) -> str | None:
    """Normalize a badge list entry to its key string."""
    if isinstance(b, dict):
        return b.get("badge_key") or b.get("id")
    return b


def _apply_under_badge_rewire(prop: Dict[str, Any], score: Dict[str, Any]) -> None:
    """Mutate `prop` in place so its badge lists are UNDER-correct.

    Strips `_OVER_ONLY_BADGES` from `context_badges`, `scout_badges`,
    `active_badges`, and `intel_suite.context_badges`. Then re-derives the
    side-agnostic scouts (`floor_lock` from `hit_rate_under`,
    `lasso_high_edge` from `edge_vs_fair`) using score-doc authoritative
    fields.
    """
    # Strip OVER-only keys from every badge list on the pick
    prop["context_badges"] = [
        b for b in (prop.get("context_badges") or [])
        if _badge_key(b) not in _OVER_ONLY_BADGES
    ]
    prop["scout_badges"] = [
        b for b in (prop.get("scout_badges") or [])
        if _badge_key(b) not in _OVER_ONLY_BADGES
    ]
    prop["active_badges"] = [
        b for b in (prop.get("active_badges") or [])
        if _badge_key(b) not in _OVER_ONLY_BADGES
    ]
    if isinstance(prop.get("intel_suite"), dict):
        ctx = prop["intel_suite"].get("context_badges")
        if isinstance(ctx, list):
            prop["intel_suite"]["context_badges"] = [
                b for b in ctx if _badge_key(b) not in _OVER_ONLY_BADGES
            ]

    # Re-derive side-correct scouts from authoritative score fields
    scout: List[Dict[str, Any]] = list(prop.get("scout_badges") or [])
    present = {_badge_key(b) for b in scout}

    hu = score.get("hit_rate_under")
    if hu is not None and float(hu) >= 90 and "floor_lock" not in present:
        scout.append({"badge_key": "floor_lock", "id": "floor_lock"})
        present.add("floor_lock")

    ev = score.get("edge_vs_fair")
    if ev is not None and float(ev) >= 0.15 and "lasso_high_edge" not in present:
        scout.append({"badge_key": "lasso_high_edge", "id": "lasso_high_edge"})
        present.add("lasso_high_edge")

    prop["scout_badges"] = scout


def _generate_under_vision_gritty(score: Dict[str, Any], prop: Dict[str, Any]) -> str:
    """Deprecated — replaced by Gemini UNDER enrichment. Kept as an import-safe
    no-op so any stale callers don't break; returns empty string so the
    fallback chain continues. Remove once all callers are migrated.
    """
    return ""


async def _build_nba_board_lookup() -> Dict[tuple, Dict[str, Any]]:
    """Flatten `dg_cached_board` (player-grain) into a prop-grain lookup keyed
    by (event_id, player_name_lower, STAT_UPPER, line_float, DIR_UPPER).

    Each entry carries both the prop dict (full enrichment) and the parent
    player doc (headshot_url, team, opponent, nba_id, position, etc.).
    Re-built on every call — dg_cached_board is ~126 player docs, negligible.

    **Reads from `props[]`, not `standard/goblins/demons` buckets.** The persist
    step in `optimized_sync_engine._persist_enriched_picks` writes modern
    `intel_suite` (momentum_data, vacuum_data, whistle_data, board, sport,
    ferrari_power_score) by positional index into `props.{idx}.intel_suite`.
    The bucket arrays are rebuilt by `cached_board_builder_service` and carry
    whatever `intel_suite` shape was live when they were categorized — which
    drifts out of sync with the persist output. Reading from `props[]`
    eliminates the drift (single source of truth, guaranteed fresh).
    """
    lookup: Dict[tuple, Dict[str, Any]] = {}
    if _db is None:
        return lookup
    async for player_doc in _db[COLL("board_cache", "nba")].find({}):
        for p in (player_doc.get("props") or []):
            if not isinstance(p, dict):
                continue
            line = p.get("line")
            try:
                line_f = float(line) if line is not None else None
            except (TypeError, ValueError):
                line_f = None
            key = (
                p.get("event_id"),
                (p.get("player_name") or "").strip().lower(),
                (p.get("stat_type") or "").strip().upper(),
                line_f,
                (p.get("direction") or "").strip().upper(),
            )
            if key[0] and key[1] and key[2] and key[3] is not None and key[4]:
                # Derive bucket label from pp_multiplier_label for downstream
                # display components that still expect it (goblin/demon/std).
                lbl = (p.get("pp_multiplier_label") or "").lower()
                bucket_lbl = (
                    "demons" if lbl == "demon"
                    else "goblins" if lbl == "goblin"
                    else "standard"
                )
                lookup[key] = {"player": player_doc, "prop": p, "bucket": bucket_lbl}
    return lookup


def _merge_score_with_board(score: Dict[str, Any], board_entry: Dict[str, Any] | None) -> Dict[str, Any]:
    """Produce a UI-ready pick dict from a `nba_prop_scores` row plus an
    optional matching board entry (from `dg_cached_board`).

    The output shape matches the legacy `elite_*` contract consumed by
    UniversalPlayerCard.jsx / Dashboard.jsx. Score-layer fields (tier,
    vision_score, pp_*, tier_gate_results, edge_vs_fair, recommendation)
    are authoritative. Board-layer fields (intel_suite, hit rates,
    headshot, opponent, price) provide display enrichment.
    """
    direction_upper = (score.get("recommendation") or "OVER").strip().upper()
    direction_title = "Under" if direction_upper == "UNDER" else "Over"
    is_under = direction_upper == "UNDER"

    # Start from board prop if matched (full enrichment), else empty dict.
    if board_entry:
        from bson import ObjectId
        def _strip_objectids(obj):
            if isinstance(obj, ObjectId):
                return None
            if isinstance(obj, dict):
                return {k: _strip_objectids(v) for k, v in obj.items() if k != "_id"}
            if isinstance(obj, list):
                return [_strip_objectids(x) for x in obj]
            return obj

        prop = _strip_objectids(board_entry["prop"])  # deep-clean enrichment
        player_doc = _strip_objectids(board_entry["player"])
        # Player-level fields from parent player doc
        for fld in (
            "player_id", "nba_id", "bdl_id", "nba_com_id", "espn_id",
            "headshot_url", "photo_url", "team", "team_name", "team_logo_url",
            "position", "jersey_number", "opponent", "opponent_abbr",
            "injury_status", "injured_teammates", "context_badges",
        ):
            val = player_doc.get(fld)
            if val is not None and prop.get(fld) in (None, "", []):
                prop[fld] = val
    else:
        prop = {
            "player_name": score.get("player_name"),
            "stat_type": score.get("stat_type"),
            "line": score.get("line"),
        }

    # --- Authoritative fields from nba_prop_scores ---
    prop["player_name"] = score.get("player_name") or prop.get("player_name")
    prop["stat_type"] = score.get("stat_type") or prop.get("stat_type")
    prop["line"] = score.get("line")
    prop["direction"] = direction_title
    prop["recommendation"] = direction_title
    prop["sport"] = "nba"

    # Tier / scoring layer
    prop["tier"] = score.get("tier")
    tier_label_map = {"safe_haven": "SAFE_HAVEN", "front_lines": "FRONT_LINE", "war_zone": "WAR_ZONE"}
    prop["tier_label"] = tier_label_map.get(score.get("tier"), (score.get("tier") or "").upper())
    prop["vision_score"] = score.get("vision_score")
    prop["vision_score_raw"] = score.get("vision_score_raw")
    prop["tier_gate_results"] = score.get("tier_gate_results")
    prop["tier_reason"] = score.get("tier_reason")
    prop["tier_reference_book"] = score.get("tier_reference_book")
    prop["tier_reference_odds"] = score.get("tier_reference_odds")
    prop["edge_vs_fair"] = score.get("edge_vs_fair")
    prop["fair_prob"] = score.get("fair_prob")
    prop["quality_source"] = score.get("quality_source")
    prop["stability"] = score.get("stability")
    prop["confidence_score"] = score.get("confidence")
    prop["version_tag"] = score.get("version_tag")
    prop["computed_at"] = score.get("computed_at")

    # PrizePicks layer
    prop["pp_utility"] = score.get("pp_utility")
    prop["pp_utility_category"] = score.get("pp_utility_category")
    prop["pp_utility_components"] = score.get("pp_utility_components")
    prop["pp_multiplier"] = score.get("pp_multiplier")
    prop["pp_multiplier_label"] = score.get("pp_multiplier_label")
    prop["pp_multiplier_source"] = score.get("pp_multiplier_source")
    prop["pp_reference_source"] = score.get("pp_reference_source")
    prop["pp_playable"] = score.get("pp_playable")
    prop["pp_playability_reason"] = score.get("pp_playability_reason")

    # Goblin/demon flags — derived from pp_multiplier_label for consistency
    lbl = (score.get("pp_multiplier_label") or "").lower()
    prop["is_goblin"] = lbl == "goblin"
    prop["is_demon"] = lbl == "demon"
    prop["is_standard"] = lbl in ("", "standard")
    if prop["is_goblin"]:
        prop["prop_type"] = "GOBLIN"
    elif prop["is_demon"]:
        prop["prop_type"] = "DEMON"
    else:
        prop["prop_type"] = "STANDARD"

    # VK2 projection layer (authoritative over any board-cached vk_predicted)
    vk2 = score.get("vk2_projection")
    if vk2 is not None:
        prop["vk_predicted"] = round(float(vk2), 2)
        try:
            prop["vk_edge"] = round(float(vk2) - float(score.get("line") or 0), 2)
        except (TypeError, ValueError):
            pass
    prop["vk2_projection"] = vk2
    prop["vk2_sigma"] = score.get("vk2_sigma")
    prop["model_projection"] = score.get("model_projection")
    prop["model_sigma"] = score.get("model_sigma")
    prop["p_true_active"] = score.get("p_true_active")
    prop["p_true_method"] = score.get("p_true_method")
    prop["p_true_vk2"] = score.get("p_true_vk2")
    prop["p_true_hit_rate"] = score.get("p_true_hit_rate")
    prop["ranking_score_v2"] = score.get("ranking_score_v2")

    # Side-aware VK probabilities from p_true_active (percent)
    p_true = score.get("p_true_active")
    if p_true is not None:
        try:
            p_pct = round(float(p_true) * 100, 1)
            # p_true is the probability of the recommended side hitting
            if is_under:
                prop["vk_prob_under"] = p_pct
                prop["vk_prob_over"] = round(100 - p_pct, 1)
            else:
                prop["vk_prob_over"] = p_pct
                prop["vk_prob_under"] = round(100 - p_pct, 1)
        except (TypeError, ValueError):
            pass

    # ============================================================
    # CANONICAL STAT FIELD CONTRACT
    # ------------------------------------------------------------
    # prop["h5_rate"], prop["h10_rate"], prop["h20_rate"],
    # prop["l5_avg"], prop["l10_avg"], prop["l20_avg"], prop["season_avg"]
    # are IMMUTABLE canonical windowed values sourced from game_logs
    # by _normalize_hit_rates_from_game_logs and its peers.
    # No downstream board/overlay/merge layer is allowed to
    # overwrite them. Model/scorer-derived side-aware rates live
    # in their own namespace: model_hit_rate_{over,under,active}.
    # Regression: /app/backend/tests/test_hit_rate_canonical.py
    # ============================================================
    ho = score.get("hit_rate_over")
    hu = score.get("hit_rate_under")
    # Diagnostic fields: model/scorer-derived L20 side-aware hit rates.
    # These are NOT the chart's L10 hit rate. They are fed into VK /
    # p_true / tier_gate calculations and rendered only where a
    # frontend explicitly opts in to model_hit_rate_*.
    if ho is not None:
        prop["hit_rate_over"] = ho
        prop["model_hit_rate_over"] = round(float(ho), 1)
    if hu is not None:
        prop["hit_rate_under"] = hu
        prop["model_hit_rate_under"] = round(float(hu), 1)
    # Active-side model hit rate convenience (mirrors score.hit_rate)
    if is_under and hu is not None:
        prop["model_hit_rate_active"] = round(float(hu), 1)
    elif ho is not None:
        prop["model_hit_rate_active"] = round(float(ho), 1)
    # NOTE: prop["h10_rate"] is intentionally NOT written here. It is the
    # canonical L10 hit rate computed by _normalize_hit_rates_from_game_logs
    # (line ~239) from the first 10 game_logs. Any downstream write here
    # would silently clobber the chart/tile contract and produce the
    # "chart 9/10 but tile shows 95%" bug (root-caused 2026-04-18).

    # VK recommendation label for downstream display
    if is_under:
        prop["vk_recommendation"] = "STRONG_UNDER" if (prop.get("vk_prob_under") or 0) >= 70 else "LEAN_UNDER"
    else:
        prop["vk_recommendation"] = "STRONG_OVER" if (prop.get("vk_prob_over") or 0) >= 70 else "LEAN_OVER"

    # Canonical key for client-side de-dupe / highlight tracking
    prop["canonical_key"] = score.get("canonical_key")

    # =========================================================================
    # SIDE-AWARE BADGE REWIRE (UNDER picks only)
    # Board props are OVER-semantic — badges baked in there (hot_streak,
    # floor_lock, soft_matchup, etc.) describe why the OVER is attractive.
    # For UNDER picks we strip those OVER-signaling badges and recompute the
    # badges that are TRUE for the UNDER side from the score doc itself.
    # OVER picks pass through unchanged. This also runs in a post-overlay
    # pass (see _apply_under_badge_rewire) to catch cache-injected badges.
    # =========================================================================
    if is_under:
        _apply_under_badge_rewire(prop, score)

    # Validation flag — data completeness
    prop["validation"] = {
        "has_market_data": bool(board_entry and prop.get("draftkings_price")),
        "has_hit_rates": prop.get("h10_rate") is not None,
        "has_context": bool(prop.get("intel_suite")),
        "has_mlr": vk2 is not None,
        "has_gemini": bool(prop.get("vision_intel")),
        "is_fully_validated": bool(
            board_entry and prop.get("intel_suite") and vk2 is not None
        ),
    }

    return prop


async def _get_nba_tier_picks_from_scores(
    tier: str,
    limit: int,
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read the top-N final-nba scores for a given tier via the UNIVERSAL
    BOARD READER (services/board/reader.py), enrich with board data, and
    return a list of UI-ready picks sorted by the adapter's tier sort key.
    Also stashes the score doc on each pick as `_nba_score_doc` so that
    downstream post-overlay passes (e.g. UNDER badge rewire) can re-read
    authoritative side-aware fields after cache overlays inject OVER-side
    enrichment. The helper is cleared before the pick is returned to the
    client.

    `sort`:
        None (default) — adapter's default sort key (vision_score DESC).
        "gap"          — projection-gap sort (ranking_score_v2 DESC).
    """
    if _db is None:
        return []

    from services.board.reader import get_board

    sort_override = "ranking_score_v2" if (sort or "").lower() == "gap" else None
    scores = await get_board(
        _db, sport="nba", tier=tier, limit=limit,
        sort_key_override=sort_override,
    )

    if not scores:
        return []

    lookup = await _build_nba_board_lookup()

    picks: List[Dict[str, Any]] = []
    for sc in scores:
        try:
            line_f = float(sc.get("line"))
        except (TypeError, ValueError):
            line_f = None
        direction_upper = (sc.get("recommendation") or "OVER").strip().upper()
        key_exact = (
            sc.get("event_id"),
            (sc.get("player_name") or "").strip().lower(),
            (sc.get("stat_type") or "").strip().upper(),
            line_f,
            direction_upper,
        )
        entry = lookup.get(key_exact)
        # Fallback: if the exact direction isn't in the board, fall back to
        # the opposite direction row so we at least pull player/intel context.
        if entry is None:
            opp = "UNDER" if direction_upper == "OVER" else "OVER"
            key_opp = (key_exact[0], key_exact[1], key_exact[2], key_exact[3], opp)
            entry = lookup.get(key_opp)
        merged = _merge_score_with_board(sc, entry)
        # Stash the score doc so downstream post-overlay passes can rewire
        # side-aware fields (UNDER badges, UNDER vision_intel) after the
        # enrichment cache overlay injects OVER-side data.
        merged["_nba_score_doc"] = sc
        picks.append(merged)

    return picks


async def _get_mlb_tier_picks_from_scores(
    tier: str,
    limit: int,
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Structural mirror of `_get_nba_tier_picks_from_scores`, for MLB.

    Reads the top-N `final-mlb` scored rows for a given tier via the universal
    board reader (services/board/reader.py) and returns UI-ready picks.

    Differences from the NBA helper (by necessity, not design):
      * NBA has `dg_cached_board` (player-grain) as a display-enrichment
        source. MLB has no equivalent -- MLB's display enrichment is applied
        downstream as pure-function transforms (enrich_mlb_prop_with_tempo,
        enrich_mlb_intel_suite, overlay_enrichment_cache). We therefore skip
        the lookup-merge step and hand the score doc through as-is.
      * The score doc already carries `model_projection`, `p_true_model`,
        `model_sigma`, `ranking_score_v2`, `p_true_method`. We surface those
        to the UI and also mirror them into the legacy `vk_*` contract that
        downstream intel_suite / card components read, so the UI works
        without component-side changes.

    `sort`:
        None (default) -- adapter's default sort key (vision_score DESC).
        "gap"          -- projection-gap sort (ranking_score_v2 DESC).
    """
    if _db is None:
        return []

    from services.board.reader import get_board

    sort_override = "ranking_score_v2" if (sort or "").lower() == "gap" else None
    scores = await get_board(
        _db, sport="mlb", tier=tier, limit=limit,
        sort_key_override=sort_override,
    )
    if not scores:
        return []

    picks: List[Dict[str, Any]] = []
    for sc in scores:
        # Base shape: pass the score doc through. Downstream enrichers
        # (overlay_enrichment_cache, enrich_mlb_prop_with_tempo,
        # enrich_mlb_intel_suite) mutate this dict in place.
        pick: Dict[str, Any] = dict(sc)

        # Mirror model fields -> legacy vk_* contract so existing UI
        # components and intel_suite assembly continue to work unchanged.
        p_model = sc.get("p_true_model") or sc.get("p_true_active")
        model_proj = sc.get("model_projection")
        if (sc.get("p_true_method") or "").lower() == "model":
            if model_proj is not None:
                try:
                    pick["vk_predicted"] = round(float(model_proj), 2)
                except (TypeError, ValueError):
                    pass
            if p_model is not None:
                try:
                    prob_over = float(p_model) * 100.0
                    side = (sc.get("recommendation") or "OVER").upper()
                    pick["vk_prob_over"]  = round(prob_over if side == "OVER" else (100.0 - prob_over), 1)
                    pick["vk_prob_under"] = round(100.0 - pick["vk_prob_over"], 1)
                    pick["vk_probability"] = pick["vk_prob_over"]
                except (TypeError, ValueError):
                    pass
            pick["vk_source"] = "model"
            # Surface canonical model fields on the pick as well.
            pick["model_projection"]  = model_proj
            pick["p_true_method"]     = sc.get("p_true_method")
            pick["p_true_model"]      = p_model
            pick["ranking_score_v2"]  = sc.get("ranking_score_v2")
            pick["model_sigma"]       = sc.get("model_sigma")

        # Stash the score doc so any later post-overlay pass can re-read
        # authoritative fields, parallel to the NBA `_nba_score_doc` stash.
        pick["_mlb_score_doc"] = sc
        picks.append(pick)

    return picks


async def _enrich_under_picks_with_gemini(
    picks: List[Dict[str, Any]],
    tier_name: str,
) -> None:
    """JIT Gemini enrichment for UNDER picks only.

    OVER picks already carry `vision_intel` from the legacy pipeline that
    wrote into `dg_cached_board`. UNDER picks never went through Gemini, so
    this method picks them up after the tier-scoring pass and runs the
    direction-aware batch prompt using the same `GOOGLE_API_KEY` +
    `gemini-3-flash-preview` path used for OVERs.

    Results are cached directly on the `nba_prop_scores` doc (fields
    `vision_intel` + `vision_intel_generated_at`) keyed by `canonical_key`,
    so subsequent requests skip the API call. Cache is invalidated whenever
    the score's `computed_at` is newer than the cached `vision_intel_generated_at`.
    """
    if _db is None or not picks:
        return

    from datetime import datetime, timezone
    from services.vision_intel_service import get_vision_intel_service

    under_picks = [p for p in picks if (p.get("direction") or "").strip().upper() == "UNDER"]
    if not under_picks:
        return

    # --- Cache lookup: fetch any previously-generated UNDER vision_intel
    # that's still fresh (generated AFTER the current computed_at).
    ckeys = [p.get("canonical_key") for p in under_picks if p.get("canonical_key")]
    cache_docs = await _db[COLL("prop_scores", "nba")].find(
        {"canonical_key": {"$in": ckeys}, "version_tag": "final-nba-rt",
         "vision_intel": {"$ne": None}},
        {"_id": 0, "canonical_key": 1, "vision_intel": 1,
         "vision_intel_generated_at": 1, "computed_at": 1},
    ).to_list(length=len(ckeys))
    cache_by_ck: Dict[str, Dict[str, Any]] = {d["canonical_key"]: d for d in cache_docs}

    def _is_cache_fresh(pick: Dict[str, Any], cached: Dict[str, Any]) -> bool:
        """Cache is fresh if generated at-or-after the score's computed_at."""
        gen = cached.get("vision_intel_generated_at")
        comp = cached.get("computed_at") or pick.get("computed_at")
        if not gen or not comp:
            return False
        try:
            gen_dt = gen if isinstance(gen, datetime) else datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
            comp_dt = comp if isinstance(comp, datetime) else datetime.fromisoformat(str(comp).replace("Z", "+00:00"))
            # Normalize tz — drop tz from both sides for a consistent compare.
            # (Mongo stores datetimes as BSON which can return naive in some drivers.)
            if gen_dt.tzinfo is not None:
                gen_dt = gen_dt.replace(tzinfo=None)
            if comp_dt.tzinfo is not None:
                comp_dt = comp_dt.replace(tzinfo=None)
            return gen_dt >= comp_dt
        except Exception:
            return False

    # Attach cached intel where available; collect the uncached for batch call.
    to_call: List[Dict[str, Any]] = []
    for p in under_picks:
        ck = p.get("canonical_key")
        cached = cache_by_ck.get(ck) if ck else None
        if cached and cached.get("vision_intel") and _is_cache_fresh(p, cached):
            p["vision_intel"] = cached["vision_intel"]
            p["vision_summary"] = cached["vision_intel"]
        else:
            to_call.append(p)

    if not to_call:
        return

    vis = get_vision_intel_service()
    if not getattr(vis, "enabled", False):
        logger.warning(
            "[UNDER_GEMINI] VisionIntelService disabled — leaving %d UNDER picks without Gemini intel",
            len(to_call),
        )
        return

    # Call Gemini ONE PROP AT A TIME via the strict method. This guarantees
    # we only cache text Gemini actually authored — if the prop_id echo fails
    # or the response is empty, we return None and the pick falls back to
    # _generate_vision_fallback downstream (without corrupting the cache).
    results = await asyncio.gather(
        *[vis.analyze_prop_strict(p, tier_name) for p in to_call]
    )

    # Map results back by canonical_key and persist to nba_prop_scores.
    now = datetime.now(timezone.utc)
    persist_ops = []
    for src, out in zip(to_call, results):
        if not out:
            continue
        vi = (out.get("vision_intel") or "").strip()
        if not vi:
            continue
        src["vision_intel"] = vi
        src["vision_summary"] = vi
        ck = src.get("canonical_key")
        if ck:
            persist_ops.append(
                _db[COLL("prop_scores", "nba")].update_one(
                    {"canonical_key": ck, "version_tag": "final-nba-rt"},
                    {"$set": {
                        "vision_intel": vi,
                        "vision_intel_generated_at": now,
                    }},
                )
            )
    if persist_ops:
        await asyncio.gather(*persist_ops, return_exceptions=True)


def _finalize_nba_picks_side_aware(picks: List[Dict[str, Any]]) -> None:
    """Post-overlay side-aware finalization for NBA picks.

    Runs AFTER `overlay_enrichment_cache` (which re-injects OVER-side
    `scout_badges` and `vision_intel` from the master enrichment cache).
    For UNDER picks:
      - re-applies the UNDER badge rewire using the stashed score doc
      - clears inherited OVER-biased `vision_intel` so the UNDER-Gemini
        enrichment can supply correct side-specific text downstream
    Mutates picks in place; strips internal helper fields before return.
    """
    for pick in picks:
        score = pick.pop("_nba_score_doc", None)
        if score is None:
            continue
        direction_upper = (pick.get("direction") or pick.get("recommendation") or "OVER").strip().upper()
        if direction_upper == "UNDER":
            _apply_under_badge_rewire(pick, score)
            # Clear any OVER-biased vision_intel inherited from the enrichment
            # cache or dg_cached_board. UNDER Gemini enrichment fills this
            # back in via `_enrich_under_picks_with_gemini` below.
            pick["vision_intel"] = None
            pick["vision_summary"] = None





def normalize_mlb_pick_for_ui(pick: dict) -> dict:
    """
    Normalize MLB pick fields to match the UI expected format.
    
    MLB data uses:
    - hit_rate_l10 -> maps to h10_rate
    - l10_avg -> maps to season_avg
    
    The UI (UniversalPlayerCard) expects:
    - h5_rate, h10_rate, season_avg
    """
    if not pick:
        return pick
    
    normalized = dict(pick)
    
    # Map MLB hit rate fields to UI expected format
    # h10_rate = hit_rate_l10 (already a percentage like 0.7 = 70%)
    if 'hit_rate_l10' in normalized and normalized.get('h10_rate') is None:
        hit_rate = normalized['hit_rate_l10']
        # Convert decimal (0.7) to percentage (70) if needed
        if hit_rate is not None and hit_rate <= 1:
            normalized['h10_rate'] = round(hit_rate * 100)
        else:
            normalized['h10_rate'] = hit_rate
    
    # Map l10_avg to season_avg for display purposes
    if 'l10_avg' in normalized and normalized.get('season_avg') is None:
        normalized['season_avg'] = normalized['l10_avg']
    
    # Also map projected_value as season_avg fallback
    if normalized.get('season_avg') is None and 'projected_value' in normalized:
        normalized['season_avg'] = normalized['projected_value']
    
    # Map edge_pct if available (as additional context)
    if 'edge_pct' in normalized and normalized.get('vk_edge') is None:
        normalized['vk_edge'] = normalized['edge_pct']
    
    # Ensure sport is set
    normalized['sport'] = 'mlb'
    
    return normalized


def normalize_mlb_picks_batch(picks: list) -> list:
    """Normalize a batch of MLB picks for UI display."""
    return [normalize_mlb_pick_for_ui(p) for p in picks]


def enrich_picks_with_vk(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich picks with Vegas Killer ML predictions."""
    vk_model = get_vegas_killer()
    if not vk_model:
        return picks
    
    for pick in picks:
        try:
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "")
            line = pick.get("line")
            opponent = pick.get("opponent") or pick.get("opponent_abbr")
            
            if not player_name or not stat_type or line is None:
                continue
            
            result = vk_model.predict(
                player_name=player_name,
                stat_type=stat_type,
                line=float(line),
                opponent_team=opponent
            )
            
            if result and not result.get("error") and result.get("predicted") is not None:
                predicted = result.get("predicted")
                edge = result.get("edge")
                prob_over = result.get("prob_over", 50)
                prob_under = result.get("prob_under", 50)
                
                # Recommendation logic
                if prob_over >= 70:
                    recommendation = "STRONG_OVER"
                elif prob_over >= 55:
                    recommendation = "LEAN_OVER"
                elif prob_under >= 70:
                    recommendation = "STRONG_UNDER"
                elif prob_under >= 55:
                    recommendation = "LEAN_UNDER"
                else:
                    recommendation = "NEUTRAL"
                
                pick["vk_predicted"] = float(predicted) if predicted else None
                pick["vk_edge"] = float(edge) if edge else None
                pick["vk_prob_over"] = float(prob_over)
                pick["vk_prob_under"] = float(prob_under)
                pick["vk_recommendation"] = recommendation
                pick["vk_data_source"] = result.get("data_source", "PROXY")
                
                # Include FULL feature breakdown for deep intel
                if result.get("full_features"):
                    pick["vk_full_features"] = result["full_features"]
                if result.get("v2_advanced_stats"):
                    pick["vk_v2_stats"] = result["v2_advanced_stats"]
        except Exception as e:
            logger.warning(f"[VK-Ferrari] Failed to enrich {pick.get('player_name')}: {e}")
    
    return picks


@router.get("/v3/ferrari/oracle-apex")
async def get_oracle_apex_picks(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    ORACLE APEX - ML-powered Safe Haven picks.
    
    NEW TIER LOGIC using Vegas Killer predictions with stat-specific gates:
    
    | Stat | Max CV | Hit Rate | Min Edge |
    |------|--------|----------|----------|
    | PTS  | 0.22   | 18/20    | 2.0      |
    | REB  | 0.35   | 16/20*   | 1.5      |
    | AST  | 0.35   | 15/20    | 2.0      |
    | PRA  | 0.20   | 18/20    | 2.0      |
    
    *REB: 14/20 OK if L20 Mean >= Line + 2.5
    
    Additional filters:
    - Minutes >= 22
    - Dedupe: Lowest line per player+stat (best goblin)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        from services.oracle_apex_service import get_oracle_apex_service
        
        vk_model = get_vegas_killer()
        if not vk_model:
            raise HTTPException(status_code=500, detail="Vegas Killer model not available")
        
        oracle_apex = get_oracle_apex_service(_db, vk_model)
        result = await oracle_apex.scan_all_props()
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
        
        picks = result.get('apex_picks', [])[:limit]
        picks = _guard_board_picks(picks)
        picks = _dedupe_picks_by_player(picks)

        return {
            "tier": "oracle_apex",
            "tier_label": "Oracle Apex (Safe Haven)",
            "description": "ML-powered mathematically-proven safe plays",
            "picks": picks,
            "count": len(picks),
            "total_scanned": result.get('total_scanned', 0),
            "gate_stats": result.get('gate_stats', {}),
            "config": {
                "PTS": {"max_cv": 0.22, "hit_rate": "18/20", "min_edge": 2.0},
                "REB": {"max_cv": 0.35, "hit_rate": "16/20 (14/20 w/ buffer)", "min_edge": 1.5},
                "AST": {"max_cv": 0.35, "hit_rate": "15/20", "min_edge": 2.0},
                "PRA": {"max_cv": 0.20, "hit_rate": "18/20", "min_edge": 2.0},
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORACLE_APEX] Endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ---------------------------------------------------------------------------
# Stage 6 (2026-04-21, MLB↔NBA carbon-copy): SPORT_TIER_HELPERS dispatch
# ---------------------------------------------------------------------------
# Single point of truth for per-sport wiring of the Ferrari tier endpoints.
# Adding a new sport (e.g. NFL) requires ONE new entry below — no route
# edits, no per-endpoint IF-chain. Eliminates D4.
#
# Each registered helper provides:
#   * source_tag_template: the `pipeline.source` string surfaced in the
#     endpoint response; used for monitoring/observability parity.
#   * fetch_picks(tier, limit, sort) -> list[dict]: pulls scored picks
#     from the canonical sport store via the universal board reader.
#   * post_process(picks, tier_name) -> None: applies sport-specific
#     display finalization (NBA: side-aware strip + Gemini UNDER;
#     MLB: tempo + intel_suite enricher no-op guards).
# ---------------------------------------------------------------------------
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class SportTierHelpers:
    source_tag_template: str
    fetch_picks:  Callable[[str, int, Optional[str]], Awaitable[List[Dict[str, Any]]]]
    post_process: Callable[[List[Dict[str, Any]], str], Awaitable[None]]


async def _apply_jit_injury_filter(
    picks: List[Dict[str, Any]], sport: str, tier_name: str
) -> List[Dict[str, Any]]:
    """Sport-uniform JIT injury filter. Replaces the duplicated
    sport-branched blocks that used to live in each Ferrari endpoint."""
    if not picks:
        return picks
    try:
        from services.live_injury_micro_sync import get_live_injury_service
        svc = get_live_injury_service()
        if svc:
            picks = await svc.jit_filter_picks(picks, sport=sport)
    except Exception as e:
        logger.warning(f"[{tier_name.upper()} {sport.upper()}] JIT injury check failed: {e}")
    return picks


async def _post_process_nba_picks(
    picks: List[Dict[str, Any]], tier_name: str
) -> None:
    """NBA post-process: side-aware finalization + Gemini UNDER enrichment.
    Mutates picks in place. No-op when picks is empty."""
    if not picks:
        return
    _finalize_nba_picks_side_aware(picks)
    await _enrich_under_picks_with_gemini(picks, tier_name)


async def _post_process_mlb_picks(
    picks: List[Dict[str, Any]], tier_name: str
) -> None:
    """MLB post-process: defensive tempo + intel_suite. Both enrichers
    are idempotent no-ops when fields were persisted at scoring-write
    time (Stage 4). Mutates picks in place. No-op when picks is empty."""
    if not picks:
        return
    for pick in picks:
        try:
            enrich_mlb_prop_with_tempo(pick)
        except Exception:
            pass
        enrich_mlb_intel_suite(pick)


SPORT_TIER_HELPERS: Dict[str, SportTierHelpers] = {
    "nba": SportTierHelpers(
        source_tag_template="nba_prop_scores[tier={tier},version=final-nba-rt]",
        fetch_picks=_get_nba_tier_picks_from_scores,
        post_process=_post_process_nba_picks,
    ),
    "mlb": SportTierHelpers(
        source_tag_template="mlb_prop_scores[tier={tier},version=final-mlb]",
        fetch_picks=_get_mlb_tier_picks_from_scores,
        post_process=_post_process_mlb_picks,
    ),
}


async def _serve_ferrari_tier(
    sport: str, tier_name: str, tier_label_prefix: str,
    limit: int, sort: Optional[str],
) -> Dict[str, Any]:
    """Canonical Ferrari tier resolver. Replaces the per-sport IF-chain
    that used to live in every tier endpoint. Adding a new sport is a
    single-line SPORT_TIER_HELPERS entry — no route edits required.
    Eliminates D4."""
    helpers = SPORT_TIER_HELPERS.get(sport)
    if helpers is None:
        raise HTTPException(
            status_code=400,
            detail=f"sport={sport!r} not registered in SPORT_TIER_HELPERS",
        )

    collection_name = helpers.source_tag_template.format(tier=tier_name)
    picks = await helpers.fetch_picks(tier_name, limit, sort=sort)

    # Uniform JIT injury filter (sport-agnostic wrapper).
    picks = await _apply_jit_injury_filter(picks, sport, tier_name)

    # Overlay async Gemini enrichment cache (sport-agnostic).
    picks = overlay_enrichment_cache(picks, sport)

    # Sport-specific finalization via dispatch table.
    await helpers.post_process(picks, tier_name)

    # Common fallback + canonical guards (sport-agnostic).
    for pick in picks:
        if not pick.get("vision_intel"):
            pick["vision_intel"] = _generate_vision_fallback(pick)
    picks = _guard_board_picks(picks)
    picks = _dedupe_picks_by_player(picks, sort=sort)

    fully_validated = sum(1 for p in picks if (p.get("validation") or {}).get("is_fully_validated", False))
    has_any_mlr    = sum(1 for p in picks if (p.get("validation") or {}).get("has_mlr", False))
    has_any_gemini = sum(1 for p in picks if (p.get("validation") or {}).get("has_gemini", False))
    status = "full" if fully_validated == len(picks) and picks else ("partial" if picks else "no_data")

    return {
        "tier": tier_name,
        "tier_label": f"{tier_label_prefix} ({sport.upper()})",
        "sport": sport,
        "picks": picks,
        "count": len(picks),
        "status": status,
        "pipeline": {
            "source": collection_name,
            "fully_validated": fully_validated,
            "with_mlr": has_any_mlr,
            "with_gemini": has_any_gemini,
        },
    }


@router.get("/v3/ferrari/safe-haven")
async def get_ferrari_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    legacy: bool = Query(False, description="Use legacy Safe Haven logic instead of stored data"),
    sort: Optional[str] = Query(None, description="Sort override. Pass 'gap' to sort by projection-gap ranking (ranking_score_v2 DESC) instead of the default vision_score DESC. NBA only."),
):
    """
    FERRARI SAFE HAVEN - Returns stored picks with Vision Intel data.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Picks are populated by the rebuild endpoint which runs:
    1. Oracle Apex 3-Gate qualification
    2. Vision Intel (Gemini) analysis and gating
    3. Composite scoring and final selection
    
    Use ?legacy=true to bypass stored data and run live Oracle Apex scan.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if legacy:
        # Legacy behavior - run live Oracle Apex scan (no Vision Intel)
        try:
            from services.oracle_apex_service import get_oracle_apex_service
            vk_model = get_vegas_killer()
            if not vk_model:
                raise HTTPException(status_code=500, detail="Vegas Killer model not available")
            
            oracle_apex = get_oracle_apex_service(_db, vk_model)
            result = await oracle_apex.scan_all_props()
            
            if not result.get('success'):
                raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
            
            picks = result.get('apex_picks', [])[:limit]
            picks = _guard_board_picks(picks)
            picks = _dedupe_picks_by_player(picks)
            return {
                "tier": "safe_haven",
                "tier_label": "Safe Haven (Live Scan)",
                "logic": "oracle_apex_live",
                "sport": sport,
                "picks": picks,
                "count": len(picks),
                "note": "Live scan - Vision Intel not applied. Use rebuild for full analysis."
            }
        except Exception as e:
            logger.error(f"[SAFE_HAVEN] Legacy scan error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # Stage 6 (2026-04-21, MLB↔NBA carbon-copy): single dispatch path.
    # Eliminates D4 — no per-sport IF-chain. Preserves response shape
    # (tier/tier_label/sport/picks/count/status/pipeline), default sort,
    # `?sort=gap` behavior, JIT injury filter, and enrichment order.
    return await _serve_ferrari_tier(
        sport=sport, tier_name="safe_haven",
        tier_label_prefix="Safe Haven",
        limit=limit, sort=sort,
    )


@router.get("/v3/ferrari/front-lines")
async def get_ferrari_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    sort: Optional[str] = Query(None, description="Sort override. Pass 'gap' to sort by projection-gap ranking (ranking_score_v2 DESC) instead of the default vision_score DESC. NBA only."),
):
    """
    FERRARI FRONT LINES - Returns stored picks with Vision Intel data.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    **VAULT ISOLATION**: NBA reads from elite_front_lines (Elite Top 10 engine).
    MLB reads from mlb_front_lines (legacy Ferrari).
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    from config.db_config import validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Stage 6 (2026-04-21, MLB↔NBA carbon-copy): single dispatch path.
    return await _serve_ferrari_tier(
        sport=sport, tier_name="front_lines",
        tier_label_prefix="Front Lines",
        limit=limit, sort=sort,
    )


@router.get("/v3/ferrari/war-zone")
async def get_ferrari_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    sort: Optional[str] = Query(None, description="Sort override. Pass 'gap' to sort by projection-gap ranking (ranking_score_v2 DESC) instead of the default vision_score DESC. NBA only."),
):
    """
    FERRARI WAR ZONE - Returns stored high-risk/high-reward picks with Vision Intel.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    **VAULT ISOLATION**: NBA reads from elite_war_zone (Elite Top 10 engine).
    MLB reads from mlb_war_zone (legacy Ferrari).
    
    Picks include:
    - Vision Intel analysis (intel_score, intel_verdict, vision_intel summary)
    - Composite scoring based on VK + Gemini confidence
    - All props that passed the Gemini gate (TRAP verdicts removed)
    """
    from config.db_config import validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Stage 6 (2026-04-21, MLB↔NBA carbon-copy): single dispatch path.
    return await _serve_ferrari_tier(
        sport=sport, tier_name="war_zone",
        tier_label_prefix="War Zone",
        limit=limit, sort=sort,
    )


@router.get("/v3/ferrari/discarded")
async def get_ferrari_discarded(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    FERRARI DISCARDED - Props killed by the 15% separation filter.
    
    Sport-aware: Pass ?sport=mlb for MLB data, ?sport=nba for NBA data.
    
    Shows what was filtered out for being "mid" plays.
    Useful for debugging and transparency.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Read from sport-specific collection
    collection_name = get_collection_name("discarded", sport)
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    return {
        "tier": "discarded",
        "tier_label": f"Discarded ({sport.upper()})",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/ferrari/market-moves")
async def get_market_moves(
    response: Response,
    sport: str = Query(None, description="Filter by sport (nba or mlb). Omit for combined feed."),
    limit: int = Query(10, ge=1, le=20),
):
    """
    MARKET MOVES — Board-diff activity feed.

    Returns picks that were recently on a visible board tier
    (Safe Haven / Front Lines / War Zone) and then left or changed state.

    NOT a recommendation tier. Purely a trust/visibility layer.

    Statuses:
    - "Line moved"         — pick's line changed, no longer qualifies
    - "Moved off board"    — pick dropped out of tier rankings
    - "Locked"             — game started / prop locked
    - "No longer qualified"— failed gate checks on re-evaluation
    """
    from services.market_moves_engine import get_recent_events

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    events = await get_recent_events(_db, sport=sport, limit=limit)

    return {
        "events": events,
        "count": len(events),
        "sport_filter": sport,
        "ttl_minutes": 20,
    }


# ==========================================================================
# SYNC ARCHITECTURE V2 — Observability Endpoints
# ==========================================================================

@router.get("/v2/coordinator/status")
async def get_coordinator_status(response: Response):
    """
    Rebuild Coordinator observability.

    Returns: mode, lock states, event counters, scope classification counts,
    last rebuild timestamps, dedup stats.
    """
    from services.rebuild_coordinator import get_coordinator
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    coordinator = get_coordinator()
    return coordinator.get_stats()


@router.get("/v2/odds/budget")
async def get_odds_budget(response: Response):
    """
    Odds API budget tracker.

    Returns: monthly/daily budget, per-sport allocation, per-pool usage,
    peak window status, recommended polling intervals.
    """
    from services.odds_budget_manager import get_budget_manager
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return get_budget_manager().get_status()


@router.get("/v2/event-bus/stats")
async def get_event_bus_stats(response: Response):
    """
    Event bus throughput stats.

    Returns: total published, total delivered, breakdown by type and sport.
    """
    from services.event_bus import get_event_bus
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return get_event_bus().get_stats()


@router.post("/v2/coordinator/trigger")
async def manual_coordinator_trigger(
    sport: str = Query("nba", description="Sport to rebuild (nba or mlb)"),
    reason: str = Query("manual", description="Trigger reason for logging"),
):
    """
    Manual rebuild trigger via Coordinator.

    Phase 1 (shadow mode): logs what WOULD happen, returns classification.
    Phase 2+: dispatches actual pipeline rebuild.
    """
    from services.event_bus import BoardEvent, get_event_bus
    from services.rebuild_coordinator import get_coordinator

    event = BoardEvent(
        sport=sport.lower(),
        event_type="manual",
        severity="high",
        source="manual_api",
        metadata={"reason": reason},
    )

    bus = get_event_bus()
    await bus.publish(event)

    coordinator = get_coordinator()
    return {
        "triggered": True,
        "sport": sport,
        "reason": reason,
        "coordinator_mode": "shadow" if coordinator.shadow_mode else "live",
        "message": f"Event published to coordinator ({'shadow — logged only' if coordinator.shadow_mode else 'live — rebuild dispatched'})",
    }



@router.get("/v2/watchers/status")
async def get_watchers_status(request: Request):
    """
    Status of all event-driven watchers and sensors.
    """
    from services.rebuild_coordinator import get_coordinator

    watcher_data = {}

    # Injury Sensor (replaces old InjuryWatcher)
    sensor = getattr(request.app.state, "injury_sensor", None)
    if sensor:
        watcher_data["injury_sensor"] = sensor.get_stats()
    else:
        watcher_data["injury_sensor"] = {"status": "not initialized"}

    # Other watchers
    for name in ["game_clock_watcher", "odds_delta_watcher"]:
        watcher = getattr(request.app.state, name, None)
        if watcher:
            watcher_data[name] = watcher.get_stats()
        else:
            watcher_data[name] = {"status": "not initialized"}

    coordinator = get_coordinator()
    return {
        "watchers": watcher_data,
        "trigger_classes": coordinator._trigger_enabled,
        "coordinator_summary": {
            "events_received": coordinator._metrics["events_received"],
            "events_deduped": coordinator._metrics["events_deduped"],
            "events_trigger_disabled": coordinator._metrics["events_trigger_disabled"],
            "events_rate_limited": coordinator._metrics["events_rate_limited"],
            "rebuilds_dispatched": coordinator._metrics["rebuilds_dispatched"],
            "rebuilds_completed": coordinator._metrics["rebuilds_completed"],
            "rebuilds_failed": coordinator._metrics["rebuilds_failed"],
        },
    }


@router.post("/v2/watchers/toggle")
async def toggle_watcher(
    request: Request,
    watcher: str = Query(..., description="injury_watcher, game_clock_watcher, or odds_delta_watcher"),
    enabled: bool = Query(..., description="true to enable, false to disable"),
):
    """
    Enable or disable a specific watcher at runtime.
    Also toggles the corresponding trigger class in the coordinator.
    """
    from services.rebuild_coordinator import get_coordinator

    watcher_obj = getattr(request.app.state, watcher, None)
    if not watcher_obj:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher}' not found")

    # Map watcher name → trigger event type
    trigger_map = {
        "injury_watcher": "injury_change",
        "game_clock_watcher": "game_lock",
        "odds_delta_watcher": "odds_delta",
    }

    if enabled:
        await watcher_obj.start()
    else:
        await watcher_obj.stop()

    # Also toggle the trigger class in coordinator
    trigger_type = trigger_map.get(watcher)
    if trigger_type:
        get_coordinator().set_trigger_enabled(trigger_type, enabled)

    return {
        "watcher": watcher,
        "enabled": enabled,
        "stats": watcher_obj.get_stats(),
    }




@router.post("/v3/ferrari/rebuild")
async def rebuild_ferrari_tiers(
    use_optimized: bool = True,
    sport: str = Query("nba", description="Target sport to sync (nba or mlb)"),
    refresh_intel: bool = Query(False, description="Force refresh all Vision Intel (ignores cache)")
):
    """
    Manually trigger a rebuild of all Ferrari tiers.
    
    **SPORT-EXCLUSIVE**: Syncs only the specified sport's data.
    - sport=nba: Syncs NBA collections (dg_cached_board, ferrari_* tiers)
    - sport=mlb: Syncs MLB collections (mlb_cached_board, mlb_ferrari_* tiers)
    
    With use_optimized=True (default):
    1. Fetches ALL global data in parallel (standings, refs, momentum, vacuums)
    2. Runs Ferrari pipeline with power score calculation
    3. Enriches all picks with cached data
    4. Generates AI summaries in batches (rate-limited)
    5. Persists enriched data to sport-specific cached_board
    
    Target: Complete sync in under 5 seconds (excluding AI summaries)
    
    With refresh_intel=True:
    - Forces Gemini to regenerate all Vision Intel (ignores cached intel)
    - Use when Vision Intel prompt has been updated
    
    With use_optimized=False:
    - Falls back to legacy sequential pipeline (NBA only)
    """
    from datetime import datetime, timezone
    
    # Normalize sport parameter
    target_sport = (sport or "nba").lower()
    if target_sport not in ["nba", "mlb"]:
        raise HTTPException(status_code=400, detail=f"Invalid sport '{sport}'. Must be 'nba' or 'mlb'.")
    
    if use_optimized:
        # Use the new optimized sync engine with sport isolation
        from services.optimized_sync_engine import run_optimized_sync
        result = await run_optimized_sync(_db, target_sport=target_sport, refresh_intel=refresh_intel)
        return result
    else:
        # Legacy path (NBA only for backwards compatibility)
        if target_sport != "nba":
            raise HTTPException(status_code=400, detail="Legacy sync only supports NBA. Use use_optimized=true for MLB.")
        service = get_service()
        result = await service.build_ferrari_tiers(datetime.now(timezone.utc), target_sport=target_sport, refresh_intel=refresh_intel)
        return result


@router.post("/v3/ferrari/sync-refs")
async def sync_referee_data():
    """
    Manually sync referee assignments and stats.
    
    Fetches:
    - Daily assignments from official.nba.com
    - Referee O/U and PPG stats from Covers.com
    
    Returns whistle classifications for today's crews.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    result = await ref_service.sync_all()
    return result


@router.get("/v3/ferrari/refs")
async def get_todays_refs(response: Response):
    """
    Get today's referee assignments with whistle classifications.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    
    # Return cached assignments - convert dict_values to list explicitly
    assignments = list(ref_service.daily_assignments_cache.values()) if ref_service.daily_assignments_cache else []
    
    # Dedupe (same game appears for both teams)
    seen_games = set()
    unique_assignments = []
    for a in assignments:
        # Ensure a is a dict
        if not isinstance(a, dict):
            continue
        game = a.get("game", "")
        if game not in seen_games:
            seen_games.add(game)
            # Enrich with stats
            crew_chief = a.get("crew_chief", "")
            normalized = ref_service._normalize_ref_name(crew_chief)
            stats = ref_service.referee_stats_cache.get(normalized, {})
            # Build a clean dict without any non-serializable objects
            unique_assignments.append({
                "game": a.get("game"),
                "away_team": a.get("away_team"),
                "home_team": a.get("home_team"),
                "crew_chief": a.get("crew_chief"),
                "referee": a.get("referee"),
                "umpire": a.get("umpire"),
                "date": a.get("date"),
                "ppg": stats.get("ppg"),
                "ou_pct": stats.get("ou_pct"),
                "whistle_class": stats.get("whistle_class", "neutral")
            })
    
    # Get date safely
    date_str = None
    if ref_service.last_assignments_fetch:
        try:
            date_str = ref_service.last_assignments_fetch.strftime("%Y-%m-%d")
        except Exception:
            date_str = None
    
    return {
        "date": date_str,
        "assignments": unique_assignments,
        "total_refs_in_cache": len(ref_service.referee_stats_cache) if ref_service.referee_stats_cache else 0,
        "total_games": len(unique_assignments)
    }


@router.get("/v3/ferrari/all")
async def get_all_ferrari_tiers(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get all Ferrari tiers in a single response.
    
    Returns:
    - safe_haven: Top 10 elite goblins
    - front_lines: Top 10 battleground picks
    - war_zone: Top 10 elite demons
    - verification: Market Intel stats
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    safe_haven = await service.get_safe_haven(limit)
    front_lines = await service.get_front_lines(limit)
    war_zone = await service.get_war_zone(limit)
    
    # Get verification stats from any tier (they all share the same stats)
    verification = safe_haven.get("verification", {})
    active_props = verification.get("active_props_verified", 0)
    output_total = safe_haven.get("count", 0) + front_lines.get("count", 0) + war_zone.get("count", 0)
    
    return {
        "safe_haven": safe_haven,
        "front_lines": front_lines,
        "war_zone": war_zone,
        "verification": {
            "active_props_verified": active_props,
            "elite_opportunities": output_total,
            "safe_haven_pool": verification.get("safe_haven_pool", 0),
            "front_lines_pool": verification.get("front_lines_pool", 0),
            "war_zone_pool": verification.get("war_zone_pool", 0),
            "message": f"Verified {active_props} active props to identify these {output_total} Elite opportunities."
        }
    }


@router.get("/v3/ferrari/parlays")
async def get_ferrari_parlays(
    response: Response,
    tier: str = Query(None, description="Filter by tier: safe_haven, front_lines, war_zone")
):
    """
    Get PropVision v7 Diversified Parlays.
    
    Returns optimized, EV-positive parlays with diversification constraints:
    - Max 2 appearances per player per tier
    - Max 2 picks from same team per parlay  
    - Max 3 picks from same stat type per parlay
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    query = {}
    if tier:
        if tier not in ["safe_haven", "front_lines", "war_zone"]:
            raise HTTPException(status_code=400, detail="Invalid tier. Use: safe_haven, front_lines, war_zone")
        query["tier"] = tier
    
    cursor = _db.ferrari_parlays.find(query, {"_id": 0})
    parlays = await cursor.to_list(length=None)
    
    # Group by tier
    by_tier = {
        "safe_haven": [],
        "front_lines": [],
        "war_zone": []
    }
    
    for p in parlays:
        t = p.get("tier", "unknown")
        if t in by_tier:
            by_tier[t].append(p)
    
    return {
        "total_parlays": len(parlays),
        "parlays_by_tier": {
            "safe_haven": len(by_tier["safe_haven"]),
            "front_lines": len(by_tier["front_lines"]),
            "war_zone": len(by_tier["war_zone"])
        },
        "safe_haven_parlays": by_tier["safe_haven"],
        "front_lines_parlays": by_tier["front_lines"],
        "war_zone_parlays": by_tier["war_zone"],
        "diversification_rules": {
            "max_player_appearances_per_tier": 2,
            "max_team_per_parlay": 2,
            "max_stat_type_per_parlay": 3
        }
    }



@router.post("/v3/odds/sync")
async def sync_odds_universal(
    sport: str = Query("nba", description="Sport to sync (nba or mlb)"),
    bookmakers: str = Query(
        None,
        description="Comma-separated bookmakers to fetch. MLB defaults to PrizePicks only. NBA defaults to prizepicks,draftkings,fanduel,pinnacle"
    ),
    include_sharp: bool = Query(True, description="Include sharp books (Pinnacle, Circa, BetCRIS) - ignored for MLB")
):
    """
    Universal Multi-Bookmaker Odds Sync.
    
    Fetches props from multiple bookmakers for cross-market comparison.
    
    **Bookmakers Supported:**
    - DFS: prizepicks, underdog
    - US Books: draftkings, fanduel, betmgm
    - Sharp Books: pinnacle, circa, betcris
    
    **NBA** (basketball_nba):
    - Markets: player_points, player_rebounds, player_assists, PRA
    - Bookmakers: All (prizepicks, draftkings, fanduel, pinnacle)
    - Saves to: dg_live_props
    
    **MLB** (baseball_mlb):
    - Markets: ALL available PrizePicks markets (home_runs, hits, total_bases, rbis, runs, strikeouts, walks, stolen_bases, pitcher_strikeouts, etc.)
    - Bookmakers: PrizePicks ONLY
    - Saves to: mlb_live_props
    
    **Output includes:**
    - all_lines: Lines from each bookmaker
    - sharp_line: Line from sharp book (Pinnacle) - NBA only
    - sharp_edge: Percentage difference between DFS line and sharp line - NBA only
    
    Returns sync summary with event count, prop count, bookmaker breakdown.
    """
    from config.db_config import validate_sport
    from services.universal_odds_sync import get_universal_odds_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Parse bookmakers - if None, let the service use sport-specific defaults
    bookmaker_list = None
    if bookmakers is not None:
        bookmaker_list = [b.strip().lower() for b in bookmakers.split(",") if b.strip()]
    
    # Run the sync
    service = get_universal_odds_service(_db)
    result = await service.sync_sport_props(sport, bookmakers=bookmaker_list, include_sharp=include_sharp)
    
    return result


@router.get("/v3/odds/props")
async def get_live_props(
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    limit: int = Query(100, ge=1, le=500),
    stat_type: str = Query(None, description="Filter by stat type (e.g., PTS, Strikeouts)")
):
    """
    Get live props from the sport-specific collection.
    
    **NBA**: Returns props from dg_live_props
    **MLB**: Returns props from mlb_live_props
    
    Optional filtering by stat_type.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("live_props", sport)
    collection = _db[collection_name]
    
    # Build query
    query = {}
    if stat_type:
        query["stat_type"] = stat_type
    
    # Fetch props
    cursor = collection.find(query, {"_id": 0}).limit(limit)
    props = await cursor.to_list(length=limit)
    
    # Get unique stat types for reference
    stat_types = await collection.distinct("stat_type")
    
    return {
        "sport": sport,
        "collection": collection_name,
        "props": props,
        "count": len(props),
        "available_stat_types": stat_types
    }



@router.post("/v3/bdl/sync")
async def sync_bdl_universal(
    sport: str = Query("nba", description="Sport to sync (nba or mlb)"),
    include_players: bool = Query(True, description="Sync player roster"),
    include_stats: bool = Query(True, description="Sync game logs/stats")
):
    """
    BDL Universal Sync - Fetch stats from BallDontLie v1 API.
    
    **Endpoints:**
    - NBA: https://api.balldontlie.io/nba/v1/stats
    - MLB: https://api.balldontlie.io/mlb/v1/stats
    
    **STRICT cursor-based pagination** using next_cursor from meta object.
    
    Saves to sport-specific master_hub collection:
    - NBA: nba_master_hub_2026
    - MLB: mlb_master_hub_2026
    
    Returns sync summary with player count, game logs count, and errors.
    """
    from config.db_config import validate_sport
    from services.bdl_universal_sync import run_bdl_universal_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Run the sync
    result = await run_bdl_universal_sync(
        _db,
        sport=sport,
        include_players=include_players,
        include_stats=include_stats
    )
    
    return result


@router.get("/v3/bdl/players")
async def get_bdl_players(
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    limit: int = Query(50, ge=1, le=500),
    team: str = Query(None, description="Filter by team abbreviation")
):
    """
    Get players from sport-specific master_hub collection.
    
    Returns player profiles synced from BDL.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("master_hub", sport)
    collection = _db[collection_name]
    
    # Build query
    query = {"bdl_id": {"$exists": True}}
    if team:
        query["team_abbr"] = team.upper()
    
    # Fetch players
    cursor = collection.find(query, {"_id": 0}).limit(limit)
    players = await cursor.to_list(length=limit)
    
    # Get unique teams for reference
    teams = await collection.distinct("team_abbr")
    
    return {
        "sport": sport,
        "collection": collection_name,
        "players": players,
        "count": len(players),
        "available_teams": sorted([t for t in teams if t])
    }


@router.get("/v3/bdl/stats/{player_name}")
async def get_bdl_player_stats(
    player_name: str,
    response: Response,
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
):
    """
    Get game logs for a specific player from master_hub.
    
    Returns BDL game logs with full box score data.
    """
    from config.db_config import get_collection_name, validate_sport
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Validate sport parameter
    try:
        sport = validate_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get sport-specific collection
    collection_name = get_collection_name("master_hub", sport)
    collection = _db[collection_name]
    
    # Search for player (case-insensitive)
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player:
        # Try partial match
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return {
        "sport": sport,
        "player": player.get("display_name"),
        "team": player.get("team_abbr"),
        "bdl_id": player.get("bdl_id"),
        "game_logs_count": player.get("bdl_game_logs_count", 0),
        "game_logs": player.get("bdl_game_logs", [])[:20],  # Limit to recent 20
        "last_sync": player.get("bdl_last_sync")
    }



@router.post("/v3/mlb/build-board")
async def build_mlb_cached_board():
    """
    Build the MLB Cached Board (Enrichment Pipeline).
    
    Process:
    1. Fetches all props from mlb_live_props
    2. Matches each prop to mlb_master_hub_2026 by player_name
    3. Enriches with:
       - Last 10 game logs
       - Season average
       - CV (Coefficient of Variation)
       - Hit rates (L10, L5)
    4. Saves to mlb_cached_board
    
    **CIRCUIT BREAKER**: If 0 props found, preserves existing board.
    
    Returns build summary with counts.
    """
    from services.mlb_cached_board_builder import run_mlb_board_build
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    result = await run_mlb_board_build(_db)
    return result


@router.get("/v3/mlb/cached-board")
async def get_mlb_cached_board(
    response: Response,
    limit: int = Query(100, ge=1, le=500)
):
    """
    Get the MLB Cached Board with enriched props.
    
    Returns players with their enriched props including:
    - Season averages
    - CV scores
    - Hit rates
    - Last 10 game logs
    """
    from services.mlb_cached_board_builder import get_mlb_board_builder
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    builder = get_mlb_board_builder(_db)
    result = await builder.get_cached_board(limit)
    return result


@router.get("/v3/mlb/player/{player_name}")
async def get_mlb_player_props(
    player_name: str,
    response: Response
):
    """
    Get a specific MLB player's enriched props from the cached board.
    
    Returns:
    - Player info with game_logs
    - All props with enrichment data (CV, hit rates, averages)
    - L5/L10 stats calculated per stat type
    - Vision Intel from Rolling Cache (vision_intel, scout_badges, vk_data)
    """
    from config.db_config import get_collection_name
    from services.rolling_cache_manager import get_cached_props
    from services.normalize_to_intel_mapping import generate_prop_id
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Load Rolling Cache for Vision Intel merge
    mlb_cache_data = get_cached_props("MLB")
    cached_props_map = mlb_cache_data.get("props", {}) if mlb_cache_data.get("success") else {}
    logger.info(f"[CACHE_MERGE] Loaded {len(cached_props_map)} props from MLB rolling cache")
    
    collection_name = get_collection_name("cached_board", "mlb")
    collection = _db[collection_name]
    
    # Search for player (case-insensitive)
    player = await collection.find_one(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player:
        # Try partial match
        player = await collection.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found in MLB board")
    
    # Deduplicate props - keep only unique stat_type + line combinations
    # Priority: GOBLIN > DEMON > STANDARD (keep the better classification)
    if player.get("props"):
        prop_map = {}
        for prop in player["props"]:
            key = f"{prop.get('stat_type')}|{prop.get('line')}"
            
            if key not in prop_map:
                prop_map[key] = prop
            else:
                # If current prop is goblin and existing is not, replace
                if prop.get('is_goblin') and not prop_map[key].get('is_goblin'):
                    prop_map[key] = prop
                # If current prop is demon and existing is standard (neither goblin nor demon), replace
                elif prop.get('is_demon') and not prop_map[key].get('is_goblin') and not prop_map[key].get('is_demon'):
                    prop_map[key] = prop
        
        player["props"] = list(prop_map.values())
    
    # SSOT: Fetch game logs AND vk_baselines from mlb_master_hub_2026
    # This ensures consistency between pick cards and player detail views
    master_hub = _db[COLL("master_hub", "mlb")]
    player_hub = await master_hub.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "bdl_game_logs": 1, "vk_baselines": 1, "vk_baseline_games": 1, "is_pitcher": 1, "is_batter": 1}
    )
    
    # Fallback: Try partial match if exact match fails
    if not player_hub:
        player_hub = await master_hub.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "bdl_game_logs": 1, "vk_baselines": 1, "vk_baseline_games": 1, "is_pitcher": 1, "is_batter": 1}
        )
    
    game_logs = []
    if player_hub and player_hub.get("bdl_game_logs"):
        # Sort by date descending (most recent first)
        raw_logs = player_hub["bdl_game_logs"]
        sorted_logs = sorted(
            raw_logs, 
            key=lambda x: (x.get("date") or "", x.get("game_id") or 0), 
            reverse=True
        )
        
        # Format game logs for frontend (take most recent 10)
        for game in sorted_logs[:10]:
            game_log = {
                "date": game.get("date"),
                "game_id": game.get("game_id"),  # Include for debugging
                "opponent": game.get("opponent_abbr") or game.get("opponent") or game.get("team_name", "")[:3].upper(),
                "pts": game.get("hits", 0),  # For chart compatibility
                "hits": game.get("hits", 0),
                "rbi": game.get("rbis", 0),  # Frontend uses 'rbi'
                "rbis": game.get("rbis", 0),  # Also include 'rbis' for consistency
                "runs": game.get("runs", 0),
                "total_bases": game.get("total_bases", 0),
                "stolen_bases": game.get("stolen_bases", 0),
                "home_runs": game.get("home_runs", 0),
                "walks": game.get("walks", 0),
                "strikeouts": game.get("strikeouts", 0),
                # Additional batter stats
                "doubles": game.get("doubles", 0),
                "singles": game.get("singles", 0),
                "triples": game.get("triples", 0),
                # Pitcher stats
                "innings_pitched": game.get("innings_pitched"),
                "pitcher_strikeouts": game.get("pitcher_strikeouts"),
                "pitcher_walks": game.get("pitcher_walks"),
                "hits_allowed": game.get("hits_allowed"),
                "earned_runs": game.get("earned_runs"),
            }
            game_logs.append(game_log)
    
    player["game_logs"] = game_logs
    
    # Add vk_baselines from master hub (5-year historical data)
    if player_hub:
        player["vk_baselines"] = player_hub.get("vk_baselines", {})
        player["vk_baseline_games"] = player_hub.get("vk_baseline_games", 0)
        player["is_pitcher"] = player_hub.get("is_pitcher", False)
        player["is_batter"] = player_hub.get("is_batter", False)
    
    # MLB stat type to game log field mapping
    # SSOT: mlb_master_hub_2026.bdl_game_logs uses 'rbis' (plural from BDL API)
    STAT_FIELD_MAP = {
        "Hits": "hits",
        "Total Bases": "total_bases",
        "RBIs": "rbis",
        "Runs": "runs",
        "Stolen Bases": "stolen_bases",
        "Home Runs": "home_runs",
        "Walks": "walks",
        "Strikeouts": "strikeouts",
        "Batter Strikeouts": "strikeouts",  # PrizePicks variant
        "Doubles": "doubles",
        "Singles": "singles",
        "Triples": "triples",
        "Hits+Runs+RBIs": None,  # Combo stat
        # Pitcher stats
        "Pitcher Strikeouts": "pitcher_strikeouts",
        "Pitching Outs": "innings_pitched",  # Will multiply by 3
        "Earned Runs Allowed": "earned_runs",
        "Earned Runs": "earned_runs",  # Both variants
        "Hits Allowed": "hits_allowed",
        "Walks Allowed": "pitcher_walks",
    }
    
    def calculate_hit_rate(games, stat_field, line, is_combo=False):
        """Calculate hit rate for L5 and L10 - how often player goes OVER the line
        
        SSOT: Skips games with None/missing values (consistent with cached board builder)
        """
        if not games:
            return 0
        
        hits = 0
        valid_games = 0
        for game in games:
            if is_combo:
                # Hits + Runs + RBIs combo - all components must exist
                h = game.get("hits")
                r = game.get("runs")
                rbi = game.get("rbis")
                if h is None or r is None or rbi is None:
                    continue  # Skip games with missing combo components
                value = (h or 0) + (r or 0) + (rbi or 0)
            elif stat_field == "innings_pitched":
                # Convert IP to outs (IP * 3)
                ip = game.get(stat_field)
                if ip is None:
                    continue  # Skip games with missing data
                value = ip * 3 if ip else 0
            else:
                value = game.get(stat_field)
                if value is None:
                    continue  # Skip games with missing data
            
            valid_games += 1
            # For "Over" props, player needs to meet or exceed the line
            if value >= line:
                hits += 1
        
        return round((hits / valid_games) * 100, 1) if valid_games else 0
    
    def calculate_avg(games, stat_field, is_combo=False):
        """Calculate average for L5 and L10
        
        SSOT: Skips games with None/missing values (consistent with cached board builder)
        """
        if not games:
            return None
        
        total = 0
        valid_games = 0
        for game in games:
            if is_combo:
                h = game.get("hits")
                r = game.get("runs")
                rbi = game.get("rbis")
                if h is None or r is None or rbi is None:
                    continue
                value = (h or 0) + (r or 0) + (rbi or 0)
            elif stat_field == "innings_pitched":
                ip = game.get(stat_field)
                if ip is None:
                    continue
                value = ip * 3 if ip else 0
            else:
                value = game.get(stat_field)
                if value is None:
                    continue
            
            valid_games += 1
            total += value
        
        return round(total / valid_games, 1) if valid_games else None
    
    if player.get("props"):
        for prop in player["props"]:
            stat_type = prop.get("stat_type", "")
            line = prop.get("line", 0)
            
            # Add stat_type_extracted for frontend
            prop["stat_type_extracted"] = stat_type
            
            # Add direction field
            if not prop.get("direction"):
                prop["direction"] = prop.get("recommendation", "Over")
            
            # Add market field
            if not prop.get("market"):
                prop["market"] = prop.get("market_key") or stat_type
            
            # is_goblin and is_demon should already be set from PrizePicks data
            # Ensure they're boolean, not None
            prop["is_goblin"] = bool(prop.get("is_goblin", False))
            prop["is_demon"] = bool(prop.get("is_demon", False))
            
            # Calculate L5/L10 hit rates and averages from game logs
            stat_field = STAT_FIELD_MAP.get(stat_type)
            is_combo = stat_type in ["Hits+Runs+RBIs", "batter_hits_runs_rbis"]
            
            if game_logs and (stat_field or is_combo):
                l5_games = game_logs[:5]
                l10_games = game_logs[:10]
                
                prop["h5_rate"] = calculate_hit_rate(l5_games, stat_field, line, is_combo)
                prop["h10_rate"] = calculate_hit_rate(l10_games, stat_field, line, is_combo)
                prop["l5_avg"] = calculate_avg(l5_games, stat_field, is_combo)
                prop["l10_avg"] = calculate_avg(l10_games, stat_field, is_combo)
                # Season average = L10 average (or use full game_logs if more available)
                prop["season_avg"] = prop["l10_avg"]
                
                # Add game_logs to prop for bar chart
                prop["game_logs"] = game_logs
    
    # =========================================================================
    # ROLLING CACHE MERGE - Vision Intel Suite
    # Merge vision_intel, vision_summary, scout_badges, vk_data from cache
    # This ensures the Player Detail Page displays pre-enriched Vision Intel
    # =========================================================================
    if player.get("props") and cached_props_map:
        merged_count = 0
        for prop in player["props"]:
            # Generate prop ID to lookup in cache
            prop_id = generate_prop_id(prop)
            cached_prop = cached_props_map.get(prop_id)
            
            logger.debug(f"[CACHE_MERGE] Looking for prop_id={prop_id}, found={cached_prop is not None}")
            
            if cached_prop and cached_prop.get("_enriched"):
                # Merge Vision Intel fields from cache
                if cached_prop.get("vision_intel"):
                    prop["vision_intel"] = cached_prop["vision_intel"]
                if cached_prop.get("vision_summary"):
                    prop["vision_summary"] = cached_prop["vision_summary"]
                if cached_prop.get("scout_badges"):
                    prop["scout_badges"] = cached_prop["scout_badges"]
                if cached_prop.get("vk_data"):
                    vk_data = cached_prop["vk_data"]
                    prop["vk_data"] = vk_data
                    # Also flatten VK fields for frontend compatibility
                    prop["vk_predicted"] = vk_data.get("predicted")
                    prop["vk_prob_over"] = vk_data.get("prob_over")
                    prop["vk_prob_under"] = vk_data.get("prob_under")
                    prop["vk_edge"] = vk_data.get("edge")
                    prop["vk_recommendation"] = vk_data.get("verdict")
                    prop["projected_value"] = vk_data.get("predicted")
                if cached_prop.get("matchup_analysis"):
                    prop["matchup_analysis"] = cached_prop["matchup_analysis"]
                if cached_prop.get("l20_variance"):
                    prop["l20_variance"] = cached_prop["l20_variance"]
                # Mark as enriched from cache
                prop["_enriched_from_cache"] = True
                merged_count += 1
                logger.debug(f"[CACHE_MERGE] Merged vision intel for {prop_id}")
        
        logger.info(f"[CACHE_MERGE] Merged {merged_count}/{len(player['props'])} props for {player_name}")
    
    # Evaluate MLB badges for each prop (FALLBACK - only if not already from cache)
    try:
        from services.mlb_badge_system import get_mlb_badge_service
        badge_service = get_mlb_badge_service(_db)
        
        if player.get("props"):
            for prop in player["props"]:
                # Skip badge evaluation if already enriched from cache
                if prop.get("_enriched_from_cache") and prop.get("scout_badges"):
                    continue
                    
                try:
                    badges = await badge_service.evaluate_all_badges(
                        player_name=player_name,
                        stat_type=prop.get("stat_type", "Total Bases"),
                        prop=prop,
                        opponent_pitcher=None  # Could be enhanced with game data
                    )
                    prop["scout_badges"] = badges
                except Exception as badge_err:
                    logger.warning(f"Badge evaluation failed for {player_name}: {badge_err}")
                    if not prop.get("scout_badges"):
                        prop["scout_badges"] = []
    except Exception as e:
        logger.warning(f"MLB badge service initialization failed: {e}")
    
    # Add MLB Matchup Analysis to each prop
    # Team abbreviation map for opponent derivation
    TEAM_ABBREV_MAP = {
        "Pittsburgh Pirates": "PIT", "Chicago Cubs": "CHC", "Los Angeles Dodgers": "LAD",
        "New York Yankees": "NYY", "Boston Red Sox": "BOS", "Atlanta Braves": "ATL",
        "Philadelphia Phillies": "PHI", "Houston Astros": "HOU", "San Diego Padres": "SD",
        "Cleveland Guardians": "CLE", "Tampa Bay Rays": "TB", "Baltimore Orioles": "BAL",
        "Milwaukee Brewers": "MIL", "Seattle Mariners": "SEA", "Minnesota Twins": "MIN",
        "Texas Rangers": "TEX", "Arizona Diamondbacks": "ARI", "Miami Marlins": "MIA",
        "Detroit Tigers": "DET", "San Francisco Giants": "SF", "Cincinnati Reds": "CIN",
        "Kansas City Royals": "KC", "St. Louis Cardinals": "STL", "Toronto Blue Jays": "TOR",
        "New York Mets": "NYM", "Los Angeles Angels": "LAA", "Colorado Rockies": "COL",
        "Oakland Athletics": "OAK", "Chicago White Sox": "CWS", "Washington Nationals": "WAS"
    }
    
    if player.get("props"):
        player_team = player.get("team", "")
        for prop in player["props"]:
            # Derive opponent from prop data
            prop_away = prop.get("away_team", "")
            prop_home = prop.get("home_team", "")
            opponent = None
            
            if prop_away and prop_home and player_team:
                away_abbr = TEAM_ABBREV_MAP.get(prop_away, prop_away[:3].upper() if prop_away else "")
                home_abbr = TEAM_ABBREV_MAP.get(prop_home, prop_home[:3].upper() if prop_home else "")
                if player_team == away_abbr:
                    opponent = home_abbr
                elif player_team == home_abbr:
                    opponent = away_abbr
            
            # Fallback to last_10_games
            if not opponent:
                last_games = prop.get("last_10_games", [])
                if last_games:
                    opponent = last_games[0].get("opponent")
            
            prop["opponent"] = opponent
            prop["opponent_abbr"] = opponent
            
            # Add matchup_analysis
            if opponent:
                try:
                    prop["matchup_analysis"] = get_mlb_matchup_analysis(
                        stat_type=prop.get("stat_type", ""),
                        opponent_team=opponent,
                        starting_pitcher_name=prop.get("opposing_pitcher")
                    )
                except Exception as ma_err:
                    logger.warning(f"Matchup analysis failed: {ma_err}")
                    prop["matchup_analysis"] = None
            else:
                prop["matchup_analysis"] = None
    
    # Enrich each prop with tempo and full intel_suite (context_badges, stability, matchup_dvp)
    if player.get("props"):
        for prop in player["props"]:
            try:
                enrich_mlb_prop_with_tempo(prop)
            except Exception:
                pass
            enrich_mlb_intel_suite(prop)
    
    return {
        "success": True,
        "player": player
    }


# =============================================================================
# MLB VEGAS KILLER HISTORICAL BACKFILL
# =============================================================================

@router.post("/v3/mlb/vk-backfill")
async def run_mlb_vk_historical_backfill(
    seasons: str = Query("2021,2022,2023,2024,2025,2026", description="Comma-separated seasons to fetch"),
    save_to_db: bool = Query(True, description="Save results to database")
):
    """
    MLB Vegas Killer 5-Season Historical Backfill.
    
    Fetches historical stats (2021-2026) and calculates weighted baselines
    for the ML regression model.
    
    **Process:**
    1. Data Retrieval: Fetch BDL /mlb/v1/stats for each season
    2. Game Cache: Build game date caches for accurate timestamps
    3. Weighted Regression: Apply time-decaying weights
       - 2026: w=1.0 (most recent)
       - 2021: w=0.5 (oldest)
    4. Output: 5-Year Weighted Baseline vs L10 Average
    
    **Collections Updated:**
    - mlb_historical_logs: Raw game logs by player
    - mlb_master_hub_2026: Player baselines (vk_baselines field)
    
    **Warning:** This is a long-running operation (5-15 minutes).
    """
    from services.mlb_vk_historical_backfill import run_mlb_historical_backfill
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse seasons - support both year integers and 'current'
    season_list = []
    for s in seasons.split(","):
        s = s.strip()
        if s.lower() == 'current':
            season_list.append('current')
        else:
            try:
                year = int(s)
                if 2020 <= year <= 2026:
                    season_list.append(year)
            except ValueError:
                continue
    
    if not season_list:
        raise HTTPException(status_code=400, detail="No valid seasons provided (2020-2026 or 'current')")
    
    # Add 'current' to get live 2026 data if 2026 is requested
    if 2026 in season_list and 'current' not in season_list:
        season_list.append('current')
    
    result = await run_mlb_historical_backfill(_db, seasons=season_list)
    return result


@router.post("/v3/mlb/advanced-stats-sync")
async def run_mlb_advanced_stats_sync_endpoint(
    seasons: str = Query("2024,2025,2026", description="Comma-separated seasons to fetch"),
    include_splits: bool = Query(True, description="Fetch vL/vR, home/away splits"),
    include_season_stats: bool = Query(True, description="Fetch WAR, OPS, WHIP, etc."),
    player_limit: int = Query(None, description="Limit players for testing (None = all)")
):
    """
    MLB Advanced Stats Sync.
    
    Fetches advanced stats from BDL for the VK Regression Model:
    
    **Splits Data (vL/vR, Park, Opponent):**
    - vs_left: Stats vs left-handed pitchers
    - vs_right: Stats vs right-handed pitchers
    - home/away: Home and away splits
    - day/night: Day and night game splits
    - by_park: Park-specific performance
    - by_opponent: Opponent-specific performance
    
    **Season Stats (Advanced Metrics):**
    - WAR: Wins Above Replacement
    - OPS: On-Base Plus Slugging
    - WHIP: Walks + Hits per Inning Pitched
    - K/9: Strikeouts per 9 innings
    - ERA: Earned Run Average
    - FIP: Fielding Independent Pitching
    
    **Derived Metrics:**
    - days_rest: Calculated from game log dates
    
    **Warning:** This is a long-running operation (5-30 minutes depending on player count).
    """
    from services.mlb_advanced_stats_sync import run_mlb_advanced_stats_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse seasons
    try:
        season_list = [int(s.strip()) for s in seasons.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid seasons format")
    
    valid_seasons = [s for s in season_list if 2020 <= s <= 2026]
    if not valid_seasons:
        raise HTTPException(status_code=400, detail="No valid seasons provided (2020-2026)")
    
    result = await run_mlb_advanced_stats_sync(
        _db,
        seasons=valid_seasons,
        include_splits=include_splits,
        include_season_stats=include_season_stats,
        player_limit=player_limit
    )
    return result


@router.get("/v3/mlb/advanced-stats/{player_name}")
async def get_mlb_player_advanced_stats(
    player_name: str,
    response: Response
):
    """
    Get a player's advanced stats.
    
    Returns:
    - vL/vR splits (batting stats vs left/right-handed pitchers)
    - Home/Away splits
    - Season stats (WAR, OPS, WHIP, K/9, ERA)
    - Days of rest data from game logs
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("master_hub", "mlb")]
    
    # Find player
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "display_name": 1, "vs_left": 1, "vs_right": 1, "home_splits": 1, 
         "away_splits": 1, "war": 1, "ops": 1, "whip": 1, "k_per_9": 1, "era": 1,
         "advanced_stats": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "display_name": 1, "vs_left": 1, "vs_right": 1, "home_splits": 1,
             "away_splits": 1, "war": 1, "ops": 1, "whip": 1, "k_per_9": 1, "era": 1,
             "advanced_stats": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return {
        "success": True,
        "player_name": player.get("display_name"),
        "quick_stats": {
            "war": player.get("war"),
            "ops": player.get("ops"),
            "whip": player.get("whip"),
            "k_per_9": player.get("k_per_9"),
            "era": player.get("era")
        },
        "vs_left": player.get("vs_left"),
        "vs_right": player.get("vs_right"),
        "home_splits": player.get("home_splits"),
        "away_splits": player.get("away_splits"),
        "advanced_stats": player.get("advanced_stats")
    }


@router.get("/v3/mlb/vk-baselines/{player_name}")
async def get_mlb_vk_baselines(
    player_name: str,
    response: Response
):
    """
    Get a player's VK weighted baselines.
    
    Returns the 5-year weighted baselines calculated during historical backfill:
    - weighted_baseline: Time-weighted average
    - l10_average: Recent 10-game average
    - baseline_vs_l10: Deviation percentage
    - weighted_cv: Consistency score
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("master_hub", "mlb")]
    
    # Find player
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "display_name": 1, "vk_baselines": 1, "vk_baseline_games": 1, "vk_baseline_updated": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "display_name": 1, "vk_baselines": 1, "vk_baseline_games": 1, "vk_baseline_updated": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    if not player.get("vk_baselines"):
        raise HTTPException(status_code=404, detail=f"No VK baselines found for '{player_name}'. Run historical backfill first.")
    
    return {
        "success": True,
        "player_name": player.get("display_name"),
        "baselines": player.get("vk_baselines"),
        "total_games": player.get("vk_baseline_games"),
        "updated_at": player.get("vk_baseline_updated")
    }


# =============================================================================
# MLB VK REGRESSION MODEL ENDPOINTS
# =============================================================================

@router.post("/v3/mlb/vk-regression")
async def run_mlb_vk_regression_analysis(
    save_to_db: bool = Query(True, description="Save results to Ferrari collections"),
    vision_intel: bool = Query(True, description="Run Vision Intel on Safe Haven picks")
):
    """
    MLB Vegas Killer Regression Analysis.
    
    Runs weighted linear regression on today's MLB slate:
    
    **Process:**
    1. Fetch all live props from mlb_live_props
    2. Calculate projections using weighted linear regression
    3. Calculate VK Edge: (Projected - Line) / Line
    4. Classify into Ferrari tiers:
       - Safe Haven: Edge > 20% + R² > 0.75 + L10 Hit Rate > 70%
       - Front Lines: Edge > 15% + R² > 0.60
       - War Zone: Edge > 25% + R² < 0.40 (High risk/reward)
    5. Run Vision Intel on Safe Haven picks (optional)
    6. Save to mlb_ferrari_* collections
    
    **Returns:** Tiered picks with projections and edges
    """
    from services.mlb_vk_regression import run_mlb_vk_slate_analysis
    from services.mlb_vision_intel_service import run_mlb_vision_intel_analysis
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Run regression analysis
    results = await run_mlb_vk_slate_analysis(_db, save_to_db=save_to_db)
    
    # Run Vision Intel on Safe Haven picks if requested
    if vision_intel and results.get("tiers", {}).get("safe_haven"):
        vision_results = await run_mlb_vision_intel_analysis(
            _db,
            results["tiers"]["safe_haven"],
            save_to_db=save_to_db
        )
        results["vision_intel"] = vision_results
    
    return results


@router.get("/v3/mlb/vk-projection/{player_name}")
async def get_mlb_vk_projection(
    player_name: str,
    stat_type: str = Query(..., description="Stat type (e.g., 'Total Bases', 'Strikeouts')"),
    line: float = Query(..., description="Sportsbook line to calculate edge against"),
    opponent: str = Query(None, description="Opponent team abbreviation"),
    venue: str = Query(None, description="Home team abbreviation for park factor"),
    response: Response = None
):
    """
    Get VK projection for a specific player and stat.
    
    Uses weighted linear regression on historical game logs.
    
    **Returns:**
    - projected_value: Model's prediction
    - r_squared: Confidence score (0-1)
    - edge: (Projected - Line) / Line
    - tier: Suggested tier classification
    """
    from services.mlb_vk_regression import get_mlb_vk_regression
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Find player
    collection = _db[get_collection_name("master_hub", "mlb")]
    player = await collection.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "bdl_id": 1}
    )
    
    if not player:
        player = await collection.find_one(
            {"display_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0, "bdl_id": 1}
        )
    
    if not player or not player.get("bdl_id"):
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    # Get model and calculate projection
    model = get_mlb_vk_regression(_db)
    
    projection = await model.calculate_player_projection(
        player_id=player["bdl_id"],
        stat_type=stat_type,
        opponent_abbr=opponent,
        venue_team=venue
    )
    
    if not projection.get("valid"):
        raise HTTPException(
            status_code=400, 
            detail=f"Could not calculate projection: {projection.get('error', 'Unknown error')}"
        )
    
    # Calculate edge
    edge_data = model.calculate_edge(projection["projected_value"], line)
    
    # Calculate hit rate
    hit_rate = model.calculate_hit_rate(
        projection.get("l10_values", []),
        line,
        edge_data["direction"]
    )
    
    # Classify tier
    tier = model.classify_tier(
        edge_data["edge"],
        projection["r_squared"],
        hit_rate
    )
    
    return {
        "success": True,
        "player_name": projection["player_name"],
        "stat_type": stat_type,
        "line": line,
        "projection": {
            "projected_value": projection["projected_value"],
            "raw_projection": projection["raw_projection"],
            "r_squared": projection["r_squared"],
            "std_error": projection["std_error"],
            "slope": projection["slope"],
            "intercept": projection["intercept"],
            "sample_size": projection["sample_size"]
        },
        "edge": edge_data,
        "hit_rate_l10": hit_rate,
        "l10_avg": projection["l10_avg"],
        "tier": tier,
        "adjustments": projection["adjustments"]
    }


@router.get("/v3/mlb/ferrari/safe-haven")
async def get_mlb_safe_haven_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Safe Haven picks.
    Board = live query against mlb_prop_scores via the universal board reader
    (services/board/reader.py). Top N active props in tier 'safe_haven'.
    No stored mlb_safe_haven collection is consulted.
    """
    from services.board.reader import get_board

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    picks = await get_board(_db, sport="mlb", tier="safe_haven", limit=limit)

    # Filter out TRAP verdicts for legacy-format vision_intel dicts.
    confirmed = []
    for p in picks:
        vi = p.get("vision_intel")
        if isinstance(vi, str) or vi is None:
            confirmed.append(p)
        elif isinstance(vi, dict) and vi.get("verdict") != "TRAP":
            confirmed.append(p)

    # Overlay enrichment cache (Gemini + Lasso)
    confirmed = overlay_enrichment_cache(confirmed, "mlb")

    # Enrich each pick with full intel_suite (tempo, context_badges, stability, matchup)
    for pick in confirmed:
        try:
            enrich_mlb_prop_with_tempo(pick)
        except Exception:
            pass
        enrich_mlb_intel_suite(pick)

    return {
        "success": True,
        "tier": "SAFE_HAVEN",
        "sport": "mlb",
        "picks": confirmed,
        "count": len(confirmed),
        "total_before_filter": len(picks)
    }


@router.get("/v3/mlb/ferrari/front-lines")
async def get_mlb_front_lines_picks(
    response: Response,
    limit: int = Query(30, ge=1, le=100)
):
    """
    Get MLB Front Lines picks.
    Board = live query against mlb_prop_scores via the universal board reader.
    """
    from services.board.reader import get_board

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    picks = await get_board(_db, sport="mlb", tier="front_lines", limit=limit)

    # Overlay enrichment cache (Gemini + Lasso)
    picks = overlay_enrichment_cache(picks, "mlb")

    # Enrich each pick with full intel_suite (tempo, context_badges, stability, matchup)
    for pick in picks:
        try:
            enrich_mlb_prop_with_tempo(pick)
        except Exception:
            pass
        enrich_mlb_intel_suite(pick)

    return {
        "success": True,
        "tier": "FRONT_LINES",
        "sport": "mlb",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/ferrari/war-zone")
async def get_mlb_war_zone_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB War Zone picks.
    Board = live query against mlb_prop_scores via the universal board reader.
    """
    from services.board.reader import get_board

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    picks = await get_board(_db, sport="mlb", tier="war_zone", limit=limit)

    # Overlay enrichment cache (Gemini + Lasso)
    picks = overlay_enrichment_cache(picks, "mlb")

    # Enrich each pick with full intel_suite (tempo, context_badges, stability, matchup)
    for pick in picks:
        try:
            enrich_mlb_prop_with_tempo(pick)
        except Exception:
            pass
        enrich_mlb_intel_suite(pick)

    return {
        "success": True,
        "tier": "WAR_ZONE",
        "sport": "mlb",
        "picks": picks,
        "count": len(picks)
    }


@router.get("/v3/mlb/ferrari/hrr-picks")
async def get_mlb_hrr_picks(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    min_edge: float = Query(50.0, description="Minimum edge percentage"),
    min_hit_rate: float = Query(0.5, description="Minimum L10 hit rate")
):
    """
    Get MLB Hits+Runs+RBIs (HRR) combo picks.
    
    HRR props have inherently lower R² due to variance in combo stats.
    Uses adjusted criteria: High edge + High hit rate.
    
    **Adjusted Criteria for Combo Stats:**
    - Edge > 50% (combo lines are often set conservatively)
    - L10 Hit Rate > 50%
    - Sorted by balanced score (edge * hit_rate)
    
    **Returns:** HRR picks sorted by value score
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Query HRR props from war_zone (they end up there due to low R²)
    collection = _db[get_collection_name("war_zone", "mlb")]
    
    # Find HRR props with edge and hit rate filters
    query = {
        "stat_type": "Hits+Runs+RBIs",
        "edge_pct": {"$gte": min_edge},
        "hit_rate_l10": {"$gte": min_hit_rate}
    }
    
    picks = await collection.find(query, {"_id": 0}).to_list(length=None)
    
    # Calculate value score and sort
    for pick in picks:
        edge = abs(pick.get("edge_pct", 0))
        hr = pick.get("hit_rate_l10", 0) or 0
        # Score: edge weighted by hit rate
        pick["value_score"] = round(edge * hr, 1)
    
    # Sort by value_score descending
    picks.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    
    # Deduplicate (same player can appear twice for OVER/UNDER)
    seen = set()
    unique_picks = []
    for p in picks:
        key = f"{p.get('player_name')}|{p.get('line')}|{p.get('direction')}"
        if key not in seen:
            seen.add(key)
            unique_picks.append(p)
    
    return {
        "success": True,
        "stat_type": "Hits+Runs+RBIs",
        "sport": "mlb",
        "picks": normalize_mlb_picks_batch(unique_picks[:limit]),
        "count": len(unique_picks[:limit]),
        "total_available": len(unique_picks),
        "filters": {
            "min_edge": min_edge,
            "min_hit_rate": min_hit_rate
        }
    }


# =============================================================================
# MLB SHARP SORTING & TIER DISTRIBUTION
# =============================================================================

@router.post("/v3/mlb/sharp-sort")
async def run_mlb_sharp_sorting_endpoint(
    stat_types: str = Query(
        None, 
        description="Comma-separated stat types to filter (e.g., 'Hits+Runs+RBIs,Total Bases')"
    ),
    save_to_db: bool = Query(True, description="Save results to collections")
):
    """
    MLB Sharp Sorting & Tier Distribution.
    
    Classifies props using sharp book analysis:
    
    **1. Pinnacle De-Vig Layer:**
    - Calculates fair value probability from Pinnacle odds
    - Removes ~4.5% vig to get true probability
    - Sharp Goblin: Fair value > 70% (odds ≤ -240)
    
    **2. DraftKings Market Depth:**
    - Compares DK alt-lines to PrizePicks
    - Identifies mispricing where DK is plus money but PP favors
    - Demon: DK +180 vs PP -110 equivalent = 12% edge
    
    **3. Ferrari Final Sort:**
    - mlb_goblins: Sharp odds ≤ -240 AND VK Projection > Line
    - mlb_demons: VK Slope trending + DK alt-line mispricing
    - mlb_standard: Sharp and public agree (-110 to -130)
    
    **Collections Created:**
    - mlb_goblins, mlb_demons, mlb_standard
    """
    from services.mlb_sharp_sorting_service import run_mlb_sharp_sorting
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Parse stat types
    stat_type_list = None
    if stat_types:
        stat_type_list = [s.strip() for s in stat_types.split(",") if s.strip()]
    
    results = await run_mlb_sharp_sorting(_db, stat_types=stat_type_list, save_to_db=save_to_db)
    
    # Return summary (don't return full lists to avoid serialization issues)
    return {
        "success": results.get("success"),
        "props_processed": results.get("props_processed"),
        "goblins_count": len(results.get("goblins", [])),
        "demons_count": len(results.get("demons", [])),
        "standard_count": len(results.get("standard", [])),
        "unclassified": results.get("unclassified"),
        "stats": results.get("stats"),
        "duration_seconds": results.get("duration_seconds"),
        "top_5_goblins": [
            {
                "player_name": g.get("player_name"),
                "stat_type": g.get("stat_type"),
                "line": g.get("line"),
                "projected_value": g.get("projected_value"),
                "direction": g.get("recommendation"),
                "sharp_odds": g.get("all_odds", {}).get("pinnacle"),
                "sharp_fair_value": g.get("sharp_fair_value"),
                "edge_pct": g.get("edge_pct"),
                "hit_rate_l10": g.get("hit_rate_l10")
            }
            for g in results.get("goblins", [])[:5]
        ]
    }


@router.get("/v3/mlb/sharp/goblins")
async def get_mlb_goblins(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Sharp Goblins.
    
    Criteria: Sharp odds ≤ -240 AND VK Projection > Line
    
    These are the highest-confidence plays backed by sharp money.
    Sorted by pp_odds ascending (most negative/favorable first), then by line ascending.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_goblins"]
    # Sort by pp_odds ascending (most negative first), then by line ascending
    picks = await collection.find({}, {"_id": 0}).sort([("pp_odds", 1), ("line", 1)]).limit(limit).to_list(length=limit)
    
    # Normalize MLB pick fields for UI compatibility
    normalized_picks = normalize_mlb_picks_batch(picks)
    
    return {
        "success": True,
        "tier": "GOBLINS",
        "description": "Sharp odds ≤ -240 AND VK confirms",
        "picks": normalized_picks,
        "count": len(normalized_picks)
    }


@router.get("/v3/mlb/sharp/demons")
async def get_mlb_demons(
    response: Response,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get MLB Demons.
    
    Criteria: DK mispricing detected + VK Slope trending
    
    These are mispriced props where DK alt-lines suggest PP is wrong.
    Sorted by pp_odds ascending, then by line ascending (highest line/demon at bottom).
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_demons"]
    # Sort by pp_odds ascending, then by line ascending (lowest line first, highest demon at bottom)
    picks = await collection.find({}, {"_id": 0}).sort([("pp_odds", 1), ("line", 1)]).limit(limit).to_list(length=limit)
    
    # Normalize MLB pick fields for UI compatibility
    normalized_picks = normalize_mlb_picks_batch(picks)
    
    return {
        "success": True,
        "tier": "DEMONS",
        "description": "DK mispricing + VK slope confirms",
        "picks": normalized_picks,
        "count": len(normalized_picks)
    }


@router.get("/v3/mlb/sharp/standard")
async def get_mlb_standard(
    response: Response,
    limit: int = Query(30, ge=1, le=100)
):
    """
    Get MLB Standard Props.
    
    Criteria: Sharp and public books agree (-110 to -130 range)
    
    These are consensus plays where all books are aligned.
    Sorted by pp_odds ascending, then by line ascending.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db["mlb_standard"]
    # Sort by pp_odds ascending, then by line ascending
    picks = await collection.find({}, {"_id": 0}).sort([("pp_odds", 1), ("line", 1)]).limit(limit).to_list(length=limit)
    
    # Normalize MLB pick fields for UI compatibility
    normalized_picks = normalize_mlb_picks_batch(picks)
    
    return {
        "success": True,
        "tier": "STANDARD",
        "description": "Books agree (-110 to -130)",
        "picks": normalized_picks,
        "count": len(normalized_picks)
    }


# =============================================================================
# MLB HEADSHOT SYNC ENDPOINTS
# =============================================================================

@router.post("/v3/mlb/headshots/sync")
async def sync_mlb_headshots(
    limit: int = Query(None, description="Optional limit on players to process"),
    phase: str = Query("full", description="Phase to run: 'ids', 'headshots', or 'full'")
):
    """
    MLB Headshot Sync - Multi-step process.
    
    **Phase 1: ID Discovery**
    - Searches MLB API (https://statsapi.mlb.com/api/v1/people/search)
    - Extracts official 6-digit MLB ID
    - Saves to official_mlb_id field
    
    **Phase 2: Headshot Fetch**
    - Downloads from MLB CDN using official_mlb_id
    - Falls back to ESPN CDN if MLB CDN fails
    - Saves to /app/frontend/public/images/mlb_headshots/{id}.png
    
    **Options:**
    - phase='ids' - Only run ID discovery
    - phase='headshots' - Only fetch headshots (requires IDs)
    - phase='full' - Run both phases (default)
    """
    from services.mlb_headshot_sync import get_mlb_headshot_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_mlb_headshot_service(_db)
    
    if phase == "ids":
        result = await service.discover_mlb_ids(limit)
    elif phase == "headshots":
        result = await service.fetch_headshots(limit)
    else:  # full
        result = await service.run_full_sync(limit)
    
    return result


@router.get("/v3/mlb/headshots/status")
async def get_mlb_headshot_status(response: Response):
    """
    Get MLB headshot sync status.
    
    Returns counts of:
    - Total players
    - Players with official_mlb_id
    - Players with headshot path
    - Local headshot files
    - Coverage percentage
    """
    from services.mlb_headshot_sync import get_mlb_headshot_service
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_mlb_headshot_service(_db)
    status = await service.get_sync_status()
    
    return status


@router.get("/v3/mlb/headshots/errors")
async def get_mlb_mapping_errors(response: Response):
    """
    Get list of players that couldn't be mapped to MLB IDs.
    
    These players don't have official headshots available.
    """
    from pathlib import Path
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    error_log = Path("/app/backend/logs/mlb_mapping_errors.log")
    
    if not error_log.exists():
        return {"errors": [], "message": "No mapping errors logged yet"}
    
    with open(error_log, "r") as f:
        content = f.read()
    
    # Parse player names (skip comment lines)
    players = [
        line.strip()
        for line in content.split("\n")
        if line.strip() and not line.startswith("#")
    ]
    
    return {
        "unmapped_players": players,
        "count": len(players),
        "log_path": str(error_log)
    }



# =============================================================================
# PROPVISION ORACLE SERVICE ENDPOINTS
# =============================================================================

@router.post("/v3/oracle/analyze-tiers")
async def run_oracle_tier_analysis(
    sport: str = Query("mlb", description="Sport to analyze (mlb or nba)")
):
    """
    Run PropVision Oracle Analysis on ALL tier picks (single batch call).
    
    **Process:**
    1. Fetches picks from Safe Haven, Front Lines, War Zone (max 30 total)
    2. Synthesizes VK Projection, Pinnacle De-Vig, DK Ladder for each
    3. Sends ALL picks to Gemini in ONE batch call
    4. Returns Bull/Bear arguments + Oracle scores for each pick
    
    **Single Gemini Call:**
    - Input: All tier picks (up to 30)
    - Output: JSON array with verdict for each pick
    
    **Oracle uses verdicts to:**
    - Gate/filter picks (score < 7 = demoted)
    - Sort picks within tiers by confidence
    """
    from services.propvision_oracle_service import get_oracle_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_oracle_service(_db)
    service.sport = sport
    
    results = await service.batch_oracle_analysis()
    
    return results


@router.get("/v3/oracle/verdict/{player_name}/{stat_type}")
async def get_oracle_verdict(
    player_name: str,
    stat_type: str,
    sport: str = Query("mlb", description="Sport (mlb or nba)")
):
    """
    Get Oracle verdict for a specific player/stat combo (no Gemini call).
    
    Uses quantitative factors only:
    - VK Projection edge
    - Pinnacle De-Vigged Probability
    - DK Line comparison
    - Historical hit rates
    """
    from services.propvision_oracle_service import get_oracle_service
    from urllib.parse import unquote
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    player_name = unquote(player_name)
    stat_type = unquote(stat_type)
    
    service = get_oracle_service(_db)
    service.sport = sport
    
    # Get prop from cached board
    cached_board = _db[COLL("board_cache", sport)]
    player_doc = await cached_board.find_one(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player_doc:
        raise HTTPException(status_code=404, detail=f"Player {player_name} not found")
    
    # Find the specific prop
    prop = None
    for p in player_doc.get("props", []):
        if p.get("stat_type", "").lower() == stat_type.lower():
            prop = p
            break
    
    if not prop:
        raise HTTPException(status_code=404, detail=f"Stat type {stat_type} not found for {player_name}")
    
    # Synthesize data
    synth = await service.oracle_data_synthesis(prop)
    
    # Get verdict (no Gemini)
    verdict = await service.oracle_final_verdict(
        vk_projection=synth.get("vk_projection"),
        pinnacle_devig_prob=synth.get("pinnacle_devig_prob"),
        dk_ladder=synth.get("dk_ladder"),
        prop=prop
    )
    
    return {
        "success": True,
        "player_name": player_name,
        "stat_type": stat_type,
        "line": prop.get("line"),
        "recommendation": prop.get("recommendation"),
        "data_synthesis": {
            "vk_projection": synth.get("vk_projection"),
            "pinnacle_devig_prob": synth.get("pinnacle_devig_prob"),
            "sharp_line": synth.get("sharp_line"),
            "dk_ladder": synth.get("dk_ladder")
        },
        "oracle": verdict
    }


# =============================================================================
# MLB FOUR-GATE SYSTEM ENDPOINTS
# =============================================================================

@router.post("/v3/mlb/four-gate/analyze")
async def analyze_four_gate_system(
    tier: str = Query("safe_haven", description="Tier to analyze (safe_haven, front_lines, war_zone)"),
    limit: int = Query(10, description="Max props to analyze")
):
    """
    Run MLB props through the 4-Gate System.
    
    **THE 4 GATES:**
    
    | Gate | Name | Source |
    |------|------|--------|
    | 1 | The Math | VK Linear Regression (5-Year History) |
    | 2 | The Market | Sharp Book (Pinnacle) + DK Alt Lines |
    | 3 | The Scout | Vision Intel (Weather, Park Factor, Statcast) |
    | 4 | The Brain | Oracle Adversarial Verdict (Bull vs Bear) |
    
    **TRAP DETECTOR:**
    - Weather: Wind > 15mph + HR prop = TRAP
    - Park: HR Factor < 0.85 = TRAP
    - Statcast: Cold batter (L5 AVG < .150) = TRAP
    - Pitcher: Velocity drop > 2mph = TRAP
    
    **VERDICTS:**
    - ELITE_PLAY: All 4 gates passed
    - SOLID_PLAY: 3 gates passed
    - LEAN: 2 gates passed
    - TRAP: Trap detected
    - AVOID: Failed gates
    """
    from services.mlb_four_gate_system import get_four_gate_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_four_gate_service(_db)
    results = await service.analyze_tier_props(tier=tier, limit=limit)
    
    return results


@router.get("/v3/mlb/four-gate/prop/{player_name}/{stat_type}")
async def analyze_single_prop_four_gate(
    player_name: str,
    stat_type: str
):
    """
    Analyze a single prop through all 4 gates.
    
    Returns detailed gate-by-gate analysis including:
    - Gate 1 (Math): VK projection, edge, R-squared
    - Gate 2 (Market): PP/DK/Sharp lines and edges
    - Gate 3 (Scout): Weather, park factor, Statcast data, TRAPS
    - Gate 4 (Brain): Oracle score and reasoning
    """
    from services.mlb_four_gate_system import get_four_gate_service
    from urllib.parse import unquote
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    player_name = unquote(player_name)
    stat_type = unquote(stat_type)
    
    # Find prop in cached board
    cached_board = _db[COLL("board_cache", "mlb")]
    player_doc = await cached_board.find_one(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player_doc:
        raise HTTPException(status_code=404, detail=f"Player {player_name} not found")
    
    # Find specific prop
    prop = None
    for p in player_doc.get("props", []):
        if p.get("stat_type", "").lower() == stat_type.lower():
            prop = p
            break
    
    if not prop:
        raise HTTPException(status_code=404, detail=f"Stat type {stat_type} not found for {player_name}")
    
    # Add team info
    prop["team"] = player_doc.get("team")
    prop["home_team"] = prop.get("home_team") or player_doc.get("team")
    
    service = get_four_gate_service(_db)
    result = await service.analyze_prop(prop)
    
    return result


@router.get("/v3/mlb/park-factors")
async def get_park_factors():
    """
    Get all MLB park factors.
    
    Park Factor > 1.0 = Hitter friendly
    Park Factor < 1.0 = Pitcher friendly
    
    Includes HR factor, altitude, and venue type.
    """
    from services.mlb_four_gate_system import PARK_FACTORS
    
    parks = []
    for team, data in sorted(PARK_FACTORS.items(), key=lambda x: -x[1]["factor"]):
        parks.append({
            "team": team,
            **data
        })
    
    return {
        "success": True,
        "count": len(parks),
        "parks": parks
    }


@router.get("/v3/mlb/weather/{team}")
async def get_venue_weather(team: str):
    """
    Get current weather at an MLB venue.
    
    Uses Open-Meteo API (free, no key required).
    Returns temperature, wind speed/direction.
    """
    from services.mlb_four_gate_system import get_four_gate_service
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    service = get_four_gate_service(_db)
    team = team.upper()
    
    park = service.get_park_factor(team)
    weather = await service.get_weather(team)
    
    return {
        "success": True,
        "team": team,
        "venue": park.get("name"),
        "venue_type": park.get("type"),
        "park_factor": park.get("factor"),
        "hr_factor": park.get("hr_factor"),
        "weather": weather
    }



@router.get("/v3/mlb/badges")
async def get_mlb_badges():
    """
    Get all MLB badge definitions.
    
    **MLB Scout Insight Badges:**
    - 🟢 Pure Contact: Whiff Rate < 15% + xBA > .290
    - 🔴 High-Heat Trap: Facing pitcher with velo +1.5mph
    - 🔵 Workhorse: Pitcher Outs 17.5+ with 80% L10 6th inning
    - 🔥 Barrel Master: Barrel % > 15% over last 25 PA
    
    **Situational Badges:**
    - 💨 Wind Boost: Wind blowing out (+10% to Over TB/HR)
    - ❄️ Cold Zone: Pitcher-friendly umpire
    - ⚔️ BvP Dominator: Strong vs today's pitcher
    - 📊 Split Advantage: Favorable handedness matchup
    """
    from services.mlb_badge_system import MLBBadge, FRONTEND_BADGE_ICONS
    
    badges = MLBBadge.get_all_badges()
    
    # Add frontend icon config to each badge
    for badge in badges:
        badge["frontend"] = FRONTEND_BADGE_ICONS.get(badge["id"], {})
    
    return {
        "success": True,
        "count": len(badges),
        "badges": badges
    }


@router.get("/v3/mlb/badges/player/{player_name}")
async def get_player_badges(
    player_name: str,
    stat_type: str = Query(None, description="Optional stat type filter"),
    opponent_pitcher: str = Query(None, description="Optional opponent pitcher for BvP")
):
    """
    Get badges earned by a specific player.
    
    Evaluates player against all badge criteria and returns earned badges.
    """
    from services.mlb_badge_system import get_mlb_badge_service
    from urllib.parse import unquote
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    player_name = unquote(player_name)
    
    badge_service = get_mlb_badge_service(_db)
    
    # Build a minimal prop for evaluation
    prop = {"player_name": player_name, "line": 1.5}
    
    badges = await badge_service.evaluate_all_badges(
        player_name=player_name,
        stat_type=stat_type or "Total Bases",
        prop=prop,
        opponent_pitcher=unquote(opponent_pitcher) if opponent_pitcher else None
    )
    
    return {
        "success": True,
        "player_name": player_name,
        "badges_earned": len(badges),
        "badges": badges
    }


@router.get("/v3/mlb/oracle-weights")
async def get_oracle_weights():
    """
    Get MLB Oracle decision weights.
    
    **Priority Order:**
    1. BvP (Batter vs Pitcher) - if sample > 15 PA
    2. Split Dominance (handedness) - if no BvP
    3. VK Projection + Market Signal
    
    **Weight Distribution:**
    - BvP: 35% (when available)
    - Split: 20% (55% when no BvP)
    - VK: 20%
    - Market: 15%
    - Badges: 10%
    """
    from services.mlb_badge_system import MLBOracleWeighting
    
    return {
        "success": True,
        "weights": MLBOracleWeighting.WEIGHTS,
        "priority_rules": [
            {"priority": 1, "source": "BvP", "condition": "Sample > 15 PA", "weight": "35%"},
            {"priority": 2, "source": "Split Dominance", "condition": "No BvP available", "weight": "55%"},
            {"priority": 3, "source": "VK Projection", "condition": "Always", "weight": "20%"},
            {"priority": 4, "source": "Market Signal", "condition": "Always", "weight": "15%"},
            {"priority": 5, "source": "Badge Boost", "condition": "Multiplier", "weight": "varies"}
        ]
    }



# =============================================================================
# MLB PROPVISION FERRARI PIPELINE
# =============================================================================

@router.post("/v3/mlb/ferrari-pipeline")
async def run_mlb_ferrari_pipeline_endpoint(
    save_to_db: bool = Query(True, description="Save results to collections"),
):
    """
    MLB Ferrari Pipeline — routes through Rebuild Coordinator → UnifiedPipeline(MLBAdapter).

    Phase 3: Same authoritative publish path as mlb/sync/master.
    """
    from services.event_bus import BoardEvent, get_event_bus
    from services.rebuild_coordinator import get_coordinator

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        event = BoardEvent(
            sport="mlb",
            event_type="manual",
            severity="high",
            source="manual_api_mlb_pipeline",
        )
        await get_event_bus().publish(event)
        await asyncio.sleep(1)

        stats = get_coordinator().get_stats()
        last = stats.get("last_publish", {}).get("mlb", {})

        return {
            "success": True,
            "coordinator_mode": stats["sport_modes"]["mlb"],
            "dispatch": "coordinator → UnifiedPipeline(MLBAdapter)",
            "last_publish": last,
        }

    except Exception as e:
        logger.error(f"[MLB_FERRARI_PIPELINE] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/mlb/ferrari-pipeline/top-hrr")
async def get_top_hrr_safe_haven_endpoint(
    limit: int = Query(3, description="Number of props to return", ge=1, le=10),
):
    """
    Get top Safe Haven HRR (Hits+Runs+RBIs) props.
    
    Returns:
        Top HRR props from Safe Haven tier with Oracle summaries
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        from services.mlb_ferrari_pipeline import get_top_safe_haven_hrr
        
        props = await get_top_safe_haven_hrr(_db, limit)
        
        return {
            "success": True,
            "count": len(props),
            "tier": "safe_haven",
            "stat_filter": "HRR",
            "props": props
        }
        
    except Exception as e:
        logger.error(f"[FERRARI_PIPELINE] Error getting HRR props: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================================
# MLB MASTER SYNC ENDPOINT
# ============================================================================

@router.post("/mlb/sync/master", status_code=202)
async def mlb_master_sync():
    """
    MLB Master Sync — unified entrypoint (Stage 1 carbon-copy enforcement).

    Byte-identical to /api/nba/sync/master. Delegates to the coordinator's
    single dispatch method. No sport-specific orchestration exceptions,
    no route-level state tracker, no MLB-only fire-and-forget machinery.
    """
    from services.rebuild_coordinator import get_coordinator

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    coord = get_coordinator()
    coord.set_db(_db)
    return await coord.dispatch_master_sync("mlb")


@router.post("/nba/sync/master", status_code=202)
async def nba_master_sync_endpoint(
    refresh_intel: bool = Query(False, description="Force refresh all Vision Intel")
):
    """
    NBA Master Sync — unified entrypoint (Stage 1 carbon-copy enforcement).

    Byte-identical to /api/mlb/sync/master. Delegates to the coordinator's
    single dispatch method. No sport-specific orchestration exceptions,
    no per-route state tracker, no sleep-and-read-stale-stats pattern.
    """
    from services.rebuild_coordinator import get_coordinator

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    coord = get_coordinator()
    coord.set_db(_db)
    return await coord.dispatch_master_sync("nba")


@router.post("/nba/sync/elite-top-10")
async def nba_elite_top_10_sync():
    """
    NBA Elite Sync — routes through Rebuild Coordinator → UnifiedPipeline(NBAAdapter).
    
    Phase 2: Same authoritative publish path as nba_master_sync.
    """
    from services.event_bus import BoardEvent, get_event_bus
    from services.rebuild_coordinator import get_coordinator
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        event = BoardEvent(
            sport="nba",
            event_type="manual",
            severity="high",
            source="manual_api_nba_elite",
        )
        await get_event_bus().publish(event)
        await asyncio.sleep(1)
        
        stats = get_coordinator().get_stats()
        last = stats.get("last_publish", {}).get("nba", {})
        
        return {
            "success": True,
            "coordinator_mode": stats["sport_modes"]["nba"],
            "dispatch": "coordinator → UnifiedPipeline(NBAAdapter)",
            "last_publish": last,
        }
        
    except Exception as e:
        logger.error(f"[NBA_ELITE_SYNC] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# =============================================================================
# LASSO-WEIGHTED PREDICTION ENGINE
# =============================================================================

@router.get("/v3/lasso/predict/{sport}/{player_name}/{target_stat}")
async def lasso_predict(
    sport: str,
    player_name: str,
    target_stat: str,
    line: float = Query(None, description="PrizePicks line to compute Vision Score"),
    playoff: bool = Query(False, description="Enable Playoff Intensity override for rest-game outliers"),
):
    """
    Lasso-Weighted Prediction with Vision Score.
    
    Projection = Intercept + SUM( beta_i * (Feature_i - Mean_i) / Std_i )
    Vision Score = Projection - Line (flagged as High Edge if >15% of line)
    """
    from models.predictor import get_lasso_engine

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    engine = get_lasso_engine()
    sport_lower = e(
        {name_field: {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, name_field: 1, "player_name": 1, "history": 1, "team": 1, "position": 1, "bdl_game_logs": 1}
    )
    if not doc:
        doc = await hub.find_one(
            {name_field: {"$regex": player_name, "$options": "i"}},
            {"_id": 0, name_field: 1, "player_name": 1, "history": 1, "team": 1, "position": 1, "bdl_game_logs": 1}
        )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")

    name = doc.get("player_name") or doc.get("display_name")

    # =========================================================
    # CURRENT-SEASON ANCHORING
    # NBA: history.2025_season IS the 2025-26 season (Oct 2025 - Jun 2026)
    # MLB: bdl_game_logs has live 2026 season; history.2025_season is LAST year
    # Historical years only inform the Lasso coefficients, not live features
    # =========================================================
    history = doc.get("history", {})

    if sport_lower == "nba":
        # NBA: use 2025_season directly (already current season, DNPs already filtered)
        all_logs = list(history.get("2025_season", []))
        all_logs.sort(key=lambda x: x.get("game_id") or x.get("date") or 0)
        data_source = "history.2025_season"
    else:
        # MLB: use bdl_game_logs (2026 live sync) as primary
        # Reverse-map from mapped field names back to BDL raw keys
        MLB_REVERSE_MAP = {
            "rbis": "rbi", "home_runs": "hr", "walks": "bb",
            "strikeouts": "k", "innings_pitched": "ip",
            "pitcher_strikeouts": "p_k", "pitcher_walks": "p_bb",
            "hits_allowed": "p_hits", "earned_runs": "er",
            "batting_avg": "avg",
        }
        raw_bdl = doc.get("bdl_game_logs", [])
        if raw_bdl and len(raw_bdl) >= 11:
            # Map live sync logs back to BDL raw key names
            all_logs = []
            for log in raw_bdl:
                mapped = dict(log)
                for mapped_key, raw_key in MLB_REVERSE_MAP.items():
                    if mapped_key in mapped:
                        mapped[raw_key] = mapped.pop(mapped_key)
                all_logs.append(mapped)
            all_logs.sort(key=lambda x: x.get("game_id") or 0)
            data_source = "bdl_game_logs (2026 live)"
        else:
            # Fallback: use history.2025_season (last MLB season)
            all_logs = list(history.get("2025_season", []))
            all_logs.sort(key=lambda x: x.get("game_id") or 0)
            data_source = "history.2025_season (fallback)"

    if len(all_logs) < 11:
        raise HTTPException(status_code=400, detail=f"Insufficient current-season data: {len(all_logs)} games (need 11+)")

    # Auto-fetch line from the live board if not provided
    if line is None:
        from models.predictor import STAT_ALIASES
        target_resolved = STAT_ALIASES.get(target_stat, target_stat.lower().replace(" ", "_"))
        board_doc = await board.find_one(
            {"player_name": {"$regex": f"^{name}$", "$options": "i"}},
            {"_id": 0, "props": 1}
        )
        if board_doc:
            for prop in board_doc.get("props", []):
                prop_stat = prop.get("stat_type", "").lower().replace(" ", "_")
                if prop_stat == target_resolved or prop.get("stat_field") == target_resolved:
                    line = prop.get("line")
                    break

    result = engine.predict_player(
        sport=sport_lower,
        target_stat=target_stat,
        game_logs=all_logs,
        player_name=name,
        line=line,
        playoff_intensity=playoff,
    )

    if result and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    result["team"] = doc.get("team")
    result["position"] = doc.get("position")
    result["total_history_games"] = len(all_logs)
    result["data_source"] = data_source

    return {"success": True, "prediction": result}


@router.get("/v3/lasso/models")
async def lasso_models():
    """List all available Lasso prediction models."""
    from models.predictor import get_lasso_engine
    engine = get_lasso_engine()
    models = []
    for key, model in engine.models.items():
        models.append({
            "model_key": key,
            "sport": model.sport,
            "target_stat": model.target_stat,
            "survivors": len(model.survivor_names),
            "r_squared": model.r_squared,
            "confidence_tier": model.confidence_tier,
            "lasso_alpha": model.alpha,
        })
    return {"success": True, "models": models}



# =============================================================================
# TEST: Non-manual scheduled MLB sync (same event as daily cron)
# =============================================================================

@router.post("/v3/mlb/test-scheduled-sync")
async def test_mlb_scheduled_sync():
    """
    Test endpoint: Fires the EXACT same BoardEvent as the daily cron job.
    event_type='scheduled_safety' (NOT 'manual') so the coordinator
    treats it identically to an automated trigger.
    """
    from services.event_bus import BoardEvent, get_event_bus
    from services.rebuild_coordinator import get_coordinator

    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    event = BoardEvent(
        sport="mlb",
        event_type="scheduled_safety",
        severity="high",
        source="scheduler_daily_mlb",
    )
    await get_event_bus().publish(event)

    # Wait briefly for dispatch
    await asyncio.sleep(1)

    stats = get_coordinator().get_stats()
    last = stats.get("last_publish", {}).get("mlb", {})

    return {
        "success": True,
        "event_type": "scheduled_safety",
        "source": "scheduler_daily_mlb",
        "note": "Non-manual: identical to daily cron trigger",
        "coordinator_mode": stats["sport_modes"]["mlb"],
        "last_publish": last,
    }
