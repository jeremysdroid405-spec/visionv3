"""Services module initialization — UNIVERSAL PATH ONLY.

Legacy wrapper services (DemonGoblinEngine delegate shims, duplicate
sync/tier/gate services) were deleted 2026-04-22. Only shared
utilities + high-level non-legacy services are re-exported here.
"""
from .dvp_service import calculate_dvp_modifier, get_dvp_label, get_full_dvp_analysis
from .stats_service import (
    calculate_hit_rates, calculate_heat_level, calculate_safety_level,
    calculate_bullet_level, calculate_volatility, calculate_season_average,
    STAT_FIELD_MAP
)
from .insights_service import (
    generate_insight_summary, calculate_confidence_rating,
    calculate_usage_bump, calculate_pace_factor, calculate_rest_metrics,
    calculate_density_factor, build_player_insights, get_team_pace,
    get_high_usage_players, calculate_volatility as insights_calculate_volatility,
    calculate_usage_bump_simple
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
from .utils_service import (
    normalize_team_name as utils_normalize_team,
    sanitize_player_name as utils_sanitize_player,
    create_composite_key, get_current_date, get_player_photo_url,
    format_stat_display, format_odds_display,
    calculate_implied_probability, probability_to_american_odds,
    NBA_TEAM_MAP, NBA_TEAM_ABBREV_TO_FULL, NAME_ALIASES
)

# High-level services using repository pattern
from .picks_service import PicksService
from .board_service import BoardService
from .photo_service import PhotoService
from .odds_api_service import OddsApiService
from .picks_getter_service import PicksGetterService
