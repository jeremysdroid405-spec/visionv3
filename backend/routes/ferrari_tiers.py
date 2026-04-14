"""
Ferrari Tier Routes
===================
API endpoints for the "Best of the Best" Ferrari-filtered picks.

Uses Bovada separation as the primary sharp benchmark.
Global 15% kill-switch ensures only elite plays are visible.
Whistle Matrix applies referee-based modifiers to power scores.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Dict, Any, List
import logging
import os

from services.ferrari_tier_service import get_ferrari_tier_service
from services.referee_scraper_service import get_referee_service
from services.mlb_matchup_math import get_mlb_matchup_analysis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Ferrari Tiers"])

# Engine reference for DB access
_db = None
_vegas_killer_model = None
_sync_db = None


def enrich_mlb_prop_with_averages(prop: Dict, player_data: Dict = None) -> Dict:
    """
    Enrich an MLB prop with ALL fields needed to match NBA pick card display:
    - L5/L10/L20 averages
    - Hit rates (h5_rate, h10_rate, h20_rate)
    - VK prediction and edge
    - Vision intel summary
    
    Args:
        prop: The prop dictionary to enrich
        player_data: Optional player-level data (for player_name, team, etc.)
    
    Returns:
        The enriched prop dictionary matching NBA structure
    """
    # Add player-level data if provided
    if player_data:
        prop["player_name"] = prop.get("player_name") or player_data.get("player_name")
        prop["team"] = prop.get("team") or player_data.get("team")
        prop["position"] = prop.get("position") or player_data.get("position")
        prop["headshot_url"] = prop.get("headshot_url") or player_data.get("headshot_url")
        prop["photo_url"] = prop.get("photo_url") or player_data.get("headshot_url")
    
    # Calculate L5/L10/L20 averages from last_10_games
    last_games = prop.get("last_10_games", [])
    line = prop.get("line", 0)
    
    values = []
    if last_games and isinstance(last_games, list):
        values = [g.get("value", 0) for g in last_games if isinstance(g, dict) and "value" in g]
    
    # L5 calculations
    l5_vals = values[:5] if len(values) >= 5 else values
    if l5_vals:
        prop["l5_avg"] = prop.get("l5_avg") or round(sum(l5_vals) / len(l5_vals), 2)
        if line > 0:
            l5_hits = sum(1 for v in l5_vals if v >= line)
            prop["l5_hits"] = l5_hits
            prop["h5_rate"] = prop.get("h5_rate") or round((l5_hits / len(l5_vals)) * 100, 1)
            prop["hit_rate_l5"] = prop["h5_rate"]
    
    # L10 calculations
    l10_vals = values[:10] if len(values) >= 10 else values
    if l10_vals:
        prop["l10_avg"] = prop.get("l10_avg") or round(sum(l10_vals) / len(l10_vals), 2)
        if line > 0:
            l10_hits = sum(1 for v in l10_vals if v >= line)
            prop["l10_hits"] = l10_hits
            prop["h10_rate"] = prop.get("h10_rate") or round((l10_hits / len(l10_vals)) * 100, 1)
            prop["hit_rate_l10"] = prop["h10_rate"]
    
    # L20 calculations (use all available, up to 20)
    l20_vals = values[:20] if len(values) >= 20 else values
    if l20_vals:
        prop["l20_avg"] = prop.get("l20_avg") or round(sum(l20_vals) / len(l20_vals), 2)
        if line > 0:
            l20_hits = sum(1 for v in l20_vals if v >= line)
            prop["l20_hits"] = l20_hits
            prop["h20_rate"] = prop.get("h20_rate") or round((l20_hits / len(l20_vals)) * 100, 1)
            prop["hit_rate_l20"] = prop["h20_rate"]
    
    # Fallbacks for averages
    season_avg = prop.get("season_average") or prop.get("season_avg")
    if not prop.get("l10_avg"):
        prop["l10_avg"] = season_avg
    if not prop.get("l5_avg"):
        prop["l5_avg"] = prop.get("l10_avg") or season_avg
    if not prop.get("l20_avg"):
        prop["l20_avg"] = prop.get("l10_avg") or season_avg
    
    # Set season_avg
    prop["season_avg"] = season_avg or prop.get("l10_avg")
    
    # =========================================================================
    # VK PREDICTION - Calculate vision model projection
    # Use weighted average: L5 (40%) + L10 (35%) + Season (25%)
    # =========================================================================
    l5 = prop.get("l5_avg") or 0
    l10 = prop.get("l10_avg") or 0
    season = prop.get("season_avg") or l10
    
    if l5 or l10 or season:
        # Weighted projection
        vk_predicted = (l5 * 0.40) + (l10 * 0.35) + (season * 0.25)
        prop["vk_predicted"] = round(vk_predicted, 2)
        
        # VK Edge = Projection - Line (raw cushion)
        if line > 0:
            vk_edge = vk_predicted - line
            prop["vk_edge"] = round(vk_edge, 2)
            
            # VK Probability (simplified: based on hit rate and edge)
            h10 = prop.get("h10_rate") or 50
            edge_boost = min(20, max(-20, vk_edge * 10))  # Cap at ±20%
            vk_prob = min(95, max(5, h10 + edge_boost))
            prop["vk_prob_over"] = round(vk_prob, 1)
            prop["vk_probability"] = round(vk_prob, 1)
            prop["vk_prob_under"] = round(100 - vk_prob, 1)
            
            # VK Recommendation
            if vk_edge >= 0.5 and h10 >= 60:
                prop["vk_recommendation"] = "STRONG OVER"
            elif vk_edge >= 0.2 and h10 >= 50:
                prop["vk_recommendation"] = "LEAN OVER"
            elif vk_edge <= -0.5 and h10 <= 40:
                prop["vk_recommendation"] = "LEAN UNDER"
            else:
                prop["vk_recommendation"] = "HOLD"
    
    # =========================================================================
    # VISION INTEL - Generate gritty scout-style summary
    # =========================================================================
    player_name = prop.get("player_name", "Player")
    stat_type = prop.get("stat_type", "stat")
    h10 = prop.get("h10_rate") or 0
    vk_pred = prop.get("vk_predicted") or 0
    vk_edge = prop.get("vk_edge") or 0
    tempo_mult = prop.get("tempo_modifier") or prop.get("intel_suite", {}).get("tempo", {}).get("multiplier") or 1.0
    is_goblin = prop.get("is_goblin", False)
    is_demon = prop.get("is_demon", False)
    
    # Gritty scout-style reports based on conditions
    if h10 >= 80 and vk_edge >= 0.5:
        vision_intel = f"{player_name} is absolutely locked in right now - {h10:.0f}% hit rate over L10 is printing money. Our math has him at {vk_pred:.1f} vs a {line} line, that's a +{vk_edge:.1f} cushion the book is sleeping on. Smash spot, don't overthink it."
    elif h10 >= 70 and vk_edge >= 0.3:
        vision_intel = f"Riding the hot hand with {player_name} here. {h10:.0f}% L10 hit rate with a +{vk_edge:.1f} edge over the line - the book set this too low. This is a soft landing, lock it in."
    elif is_goblin and h10 >= 60:
        vision_intel = f"{player_name} {stat_type} is chalky for a reason - this line is disrespectful at {line}. {h10:.0f}% L10, averaging {l10:.1f}. Safe haven territory, let it ride."
    elif is_demon:
        vision_intel = f"DEMON PLAY: {player_name} {stat_type} @ {line} is a ceiling spot. High variance but when this hits, it pays big. Boom or bust - you know what you're signing up for."
    elif tempo_mult >= 1.05:
        vision_intel = f"Volume play on {player_name} today. Lineup spot and pace means extra ABs coming his way. At {l10:.1f} L10 average vs a {line} line, let the plate appearances pile up."
    elif h10 < 50:
        vision_intel = f"{player_name} has been ice cold at {h10:.0f}% L10 - this feels like a trap. Our model still sees {vk_pred:.1f} but proceed with caution or fade entirely."
    elif vk_edge >= 0.2:
        vision_intel = f"Solid value on {player_name} {stat_type} @ {line}. Model projects {vk_pred:.1f} with a comfortable +{vk_edge:.1f} edge. Not a slam dunk but the math works."
    else:
        vision_intel = f"{player_name} {stat_type} @ {line}: Projecting {vk_pred:.1f} with {h10:.0f}% recent hit rate. Edge is thin here - need the situation to break right."
    
    prop["vision_intel"] = vision_intel
    prop["vision_summary"] = vision_intel
    
    # =========================================================================
    # ADDITIONAL NBA-MATCHING FIELDS
    # =========================================================================
    prop["oracle_apex_qualified"] = prop.get("is_goblin", False) and h10 >= 60
    prop["tier"] = prop.get("tier") or ("safe_haven" if prop.get("is_goblin") else "front_lines")
    prop["synced_at"] = prop.get("synced_at") or prop.get("fetched_at")
    
    # Opponent field
    if not prop.get("opponent"):
        player_team = prop.get("team")
        away = prop.get("away_team")
        home = prop.get("home_team")
        if player_team and away and home:
            prop["opponent"] = away if player_team == home else home
    
    # Game time
    prop["game_time"] = prop.get("game_time") or prop.get("commence_time")
    
    return prop


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
    
    # Classification
    is_goblin = prop.get("is_goblin", False)
    is_demon = prop.get("is_demon", False)
    dk_odds = prop.get("dk_odds")
    
    # Check if pitcher
    is_pitcher = "pitcher" in position or stat_type.lower() in ["strikeouts", "pitcher strikeouts", "pitching outs", "earned runs", "hits allowed"]
    
    # Initialize intel_suite - preserve existing tempo/pace_delta
    existing_intel = prop.get("intel_suite", {})
    intel_suite = {
        "tempo": existing_intel.get("tempo", {}),
        "pace_delta": existing_intel.get("pace_delta", {}),
        "sport": "mlb"
    }
    
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
    
    # VOLATILITY_EXTREME: High CV indicates boom/bust
    if cv and cv > 70:
        badge_keys.append("volatility_extreme")
    
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
    
    if cv and cv <= 40:
        reasons.append(f"Low variance (CV {cv:.0f}%) indicates consistency")
    elif cv and cv > 70:
        reasons.append(f"High variance (CV {cv:.0f}%) - boom/bust potential")
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
        if cv <= 30:
            stability_score = 90
            consistency = "Elite"
        elif cv <= 50:
            stability_score = 70
            consistency = "Stable"
        elif cv <= 70:
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


@router.get("/v3/ferrari/safe-haven")
async def get_ferrari_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)"),
    legacy: bool = Query(False, description="Use legacy Safe Haven logic instead of stored data")
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
    
    # DEFAULT: VAULT ISOLATION - NBA uses elite collections, MLB uses legacy
    if sport == "nba":
        collection_name = "elite_safe_haven"
    else:
        collection_name = "mlb_safe_haven"
    
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # JIT Injury Check for NBA picks
    if sport == "nba" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="nba")
        except Exception as e:
            logger.warning(f"[SAFE_HAVEN NBA] JIT injury check failed: {e}")
    
    # JIT Injury Check for MLB picks
    if sport == "mlb" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="mlb")
        except Exception as e:
            logger.warning(f"[SAFE_HAVEN MLB] JIT injury check failed: {e}")
    
    # Return picks from vault-isolated collection
    return {
        "tier": "safe_haven",
        "tier_label": f"Safe Haven ({sport.upper()})",
        "logic": "elite_vault" if sport == "nba" else "legacy_ferrari",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks),
        "note": "Elite Top 10 Sequential Claim picks" if sport == "nba" else "Oracle Apex qualified picks"
    }


@router.get("/v3/ferrari/front-lines")
async def get_ferrari_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
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
    
    # VAULT ISOLATION: NBA uses elite collections, MLB uses legacy
    if sport == "nba":
        collection_name = "elite_front_lines"
    else:
        collection_name = "mlb_front_lines"
    
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # JIT Injury Check for NBA picks
    if sport == "nba" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="nba")
        except Exception as e:
            logger.warning(f"[FRONT_LINES NBA] JIT injury check failed: {e}")
    
    # JIT Injury Check for MLB picks
    if sport == "mlb" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="mlb")
        except Exception as e:
            logger.warning(f"[FRONT_LINES MLB] JIT injury check failed: {e}")
    
    # Return picks from vault-isolated collection
    return {
        "tier": "front_lines",
        "tier_label": f"Front Lines ({sport.upper()})",
        "logic": "elite_vault" if sport == "nba" else "legacy_ferrari",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks),
        "note": "Elite Top 10 Sequential Claim picks" if sport == "nba" else "Oracle Apex qualified picks"
    }


@router.get("/v3/ferrari/war-zone")
async def get_ferrari_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("nba", description="Sport to query (nba or mlb)")
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
    
    # VAULT ISOLATION: NBA uses elite collections, MLB uses legacy
    if sport == "nba":
        collection_name = "elite_war_zone"
    else:
        collection_name = "mlb_war_zone"
    
    collection = _db[collection_name]
    
    cursor = collection.find({}, {"_id": 0}).limit(limit)
    picks = await cursor.to_list(length=limit)
    
    # JIT Injury Check for NBA picks
    if sport == "nba" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="nba")
        except Exception as e:
            logger.warning(f"[WAR_ZONE NBA] JIT injury check failed: {e}")
    
    # JIT Injury Check for MLB picks
    if sport == "mlb" and picks:
        try:
            from services.live_injury_micro_sync import get_live_injury_service
            live_injury_svc = get_live_injury_service()
            if live_injury_svc:
                picks = await live_injury_svc.jit_filter_picks(picks, sport="mlb")
        except Exception as e:
            logger.warning(f"[WAR_ZONE MLB] JIT injury check failed: {e}")
    
    # Return picks from vault-isolated collection
    return {
        "tier": "war_zone",
        "tier_label": f"War Zone ({sport.upper()})",
        "logic": "elite_vault" if sport == "nba" else "legacy_ferrari",
        "sport": sport,
        "collection": collection_name,
        "picks": picks,
        "count": len(picks),
        "note": "Elite Top 10 Sequential Claim picks" if sport == "nba" else "Oracle Apex qualified picks"
    }


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
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
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
    master_hub = _db["mlb_master_hub_2026"]
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
    
    # Evaluate MLB badges for each prop
    try:
        from services.mlb_badge_system import get_mlb_badge_service
        badge_service = get_mlb_badge_service(_db)
        
        if player.get("props"):
            for prop in player["props"]:
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
    
    Safe Haven criteria:
    - VK Edge > 20%
    - R-Squared > 0.75
    - L10 Hit Rate > 70%
    - Vision Intel: CONFIRMED (not TRAP)
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("safe_haven", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
    # Filter out TRAP picks (vision_intel can be a string now or dict for legacy)
    confirmed = []
    for p in picks:
        vi = p.get("vision_intel")
        # If vision_intel is a string (new format), include it
        # If it's a dict (legacy format), check verdict
        if isinstance(vi, str):
            confirmed.append(p)
        elif isinstance(vi, dict) and vi.get("verdict") != "TRAP":
            confirmed.append(p)
        elif vi is None:
            confirmed.append(p)
    
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
    
    Front Lines criteria:
    - VK Edge > 15%
    - R-Squared > 0.60
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("front_lines", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
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
    
    War Zone criteria:
    - VK Edge > 25%
    - R-Squared < 0.40 (High variance = high risk/reward)
    """
    from config.db_config import get_collection_name
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    collection = _db[get_collection_name("war_zone", "mlb")]
    picks = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
    
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
    cached_board = _db[f"{sport}_cached_board"]
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
    cached_board = _db["mlb_cached_board"]
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
    Execute the full MLB PropVision Ferrari Pipeline.
    
    Phases:
    1. Quantitative Sorting Gates
    2. Vision Intel Scout Badges  
    3. Gemini Oracle Summarizer
    4. Save to Ferrari Collections
    
    Returns:
        Complete pipeline results with all tiers
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        from services.mlb_ferrari_pipeline import run_mlb_ferrari_pipeline
        
        result = await run_mlb_ferrari_pipeline(_db, save_to_db)
        
        return result
        
    except Exception as e:
        logger.error(f"[FERRARI_PIPELINE] Error: {e}")
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

@router.post("/mlb/sync/master")
async def mlb_master_sync():
    """
    MLB Master Sync - Enforces strict pipeline sequence.
    
    Sequence:
    1. Sync Vegas Odds → mlb_live_props
    2. Build mlb_cached_board (INTERSECTION: PrizePicks AND Odds only)
    3. BDL Splits Prefetch (ONLY for players in cached_board)
    4. Oracle Apex Tier Rebuilds
    
    Returns:
        Detailed metrics for each step including BDL API calls made.
    """
    from services.mlb_master_sync import get_mlb_master_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        master_sync = get_mlb_master_sync(_db)
        result = await master_sync.run_master_sync()
        return result
        
    except Exception as e:
        logger.error(f"[MLB_MASTER_SYNC] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nba/sync/master")
async def nba_master_sync_endpoint(
    refresh_intel: bool = Query(False, description="Force refresh all Vision Intel")
):
    """
    NBA Master Sync - Full Pipeline with Elite Top 10.
    
    This is the RECOMMENDED endpoint for NBA sync. It runs:
    
    **Phase 1: Ferrari Rebuild**
    - Syncs fresh odds data
    - Applies Blowout Risk filters
    - Applies Injury/Usage adjustments
    - Applies DvP Matchup analysis
    - Applies V7 Quality gates
    - Populates ferrari_scored with "smart-filtered" props
    
    **Phase 2: Elite Top 10 Sequential Claim**
    - Reads from ferrari_scored (Ferrari-vetted props)
    - WAR ZONE claims first (Demons + High-Odds Standards, true_edge >= 8%)
    - SAFE HAVEN claims second (Goblins only, HR >= 60%, CV <= 0.35)
    - FRONT LINES claims last (remaining pool, HR >= 50%, CV <= 0.50)
    
    **Phase 3: Store Results**
    - Stores exclusive tier assignments to ferrari_safe_haven, ferrari_front_lines, ferrari_war_zone
    - No prop appears in multiple tiers (deduplication guaranteed)
    
    Returns:
        Combined metrics from both phases with tier counts.
    """
    from services.nba_master_sync import get_nba_master_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        master_sync = get_nba_master_sync(_db)
        result = await master_sync.run_full_pipeline(refresh_intel=refresh_intel)
        return result
        
    except Exception as e:
        logger.error(f"[NBA_MASTER_SYNC] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nba/sync/elite-top-10")
async def nba_elite_top_10_sync():
    """
    NBA Elite Top 10 Sync - Sequential Claim Engine.
    
    Applies the Elite Top 10 Sequential Claim Logic to NBA props:
    1. Build QUALIFIED POOL (preserves Blowout/Injury/DvP data)
    2. WAR ZONE claims first (Demons + Standards DK > +100, true_edge >= 8%)
    3. SAFE HAVEN claims second (Goblins only, HR >= 60%, CV <= 0.35)
    4. FRONT LINES claims last (remaining pool, HR >= 50%, CV <= 0.50)
    
    PREREQUISITE: Run /api/v3/ferrari/rebuild first to populate ferrari_scored.
    
    Features:
    - Uses unified 50/50 Master Probability (market_prob + true_hit_rate)
    - Exclusive tier assignment (no prop in multiple tiers)
    - Preserves NBA intel: Blowout Warnings, Injury/Usage, DvP Matchups
    
    Returns:
        Detailed metrics including tier counts and no-duplicate verification.
    """
    from services.nba_master_sync import get_nba_master_sync
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        master_sync = get_nba_master_sync(_db)
        result = await master_sync.run_elite_sync()
        return result
        
    except Exception as e:
        logger.error(f"[NBA_ELITE_SYNC] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

