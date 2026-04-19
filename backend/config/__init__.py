"""Config module initialization"""
from .settings import (
    get_database, close_database,
    MONGO_URL, DB_NAME, ODDS_API_KEY, BDL_API_KEY, GOOGLE_API_KEY, JWT_SECRET,
    ODDS_API_BASE, BDL_API_BASE, NBA_API_BASE, NBA_HEADSHOT_URL,
    MAX_RETRIES, RETRY_DELAY, API_RATE_LIMIT,
    SCORING_WEIGHTS, TEAM_ABBREV_MAP, STAT_TYPE_MAP, DVP_RANKINGS
)

# Multi-sport database configuration
from .db_config import (
    get_collection_name,
    get_collection,
    get_all_collection_names,
    validate_sport,
    is_collection_for_sport,
    get_sport_config,
    SUPPORTED_SPORTS,
    DEFAULT_SPORT,
    SPORT_PREFIXES,
    BASE_COLLECTIONS,
    SPORT_CONFIG
)
