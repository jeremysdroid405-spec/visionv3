"""Services module initialization"""
from .dvp_service import calculate_dvp_modifier, get_dvp_label, get_full_dvp_analysis
from .stats_service import (
    calculate_hit_rates, calculate_heat_level, calculate_safety_level,
    calculate_bullet_level, calculate_volatility, calculate_season_average,
    STAT_FIELD_MAP
)
from .insights_service import (
    generate_insight_summary, calculate_confidence_rating,
    calculate_usage_bump, calculate_pace_factor, calculate_rest_metrics,
    calculate_density_factor, build_player_insights
)
from .parlay_service import (
    build_parlay_tickets, validate_ticket, apply_parlay_matrix,
    calculate_parlay_probability, calculate_payout_multiplier,
    interleave_pick_arrays, build_correlated_parlay,
    calculate_weighted_parlay_probability, calculate_live_payout,
    PARLAY_MATRIX, TICKET_NAMES
)
from .data_scraper import (
    OddsApiScraper, BDLScraper, NBAScraper,
    fetch_with_backoff, normalize_team_name, sanitize_player_name
)
from .social_scout import (
    SocialSignalAnalyzer, get_team_pace, calculate_pace_factor as social_calculate_pace_factor,
    get_high_usage_players, calculate_usage_bump as social_calculate_usage_bump,
    calculate_volatility as social_calculate_volatility,
    generate_insight_summary as social_generate_insight_summary,
    calculate_confidence_rating as social_calculate_confidence_rating
)
