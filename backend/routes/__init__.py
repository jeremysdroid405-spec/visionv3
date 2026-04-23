"""Routes module initialization - UNIVERSAL PATH ONLY (post HARD CONSOLIDATION 2026-04-22).

All legacy engine-driven routes (picks, parlays, board, tiers, legacy,
core_v3, intel_sync, cached_data, board_intel_v2, roster_sync, scheduler)
have been deleted along with DemonGoblinEngine. The only routes that
remain are those backed by the universal path (ferrari_tiers read-side,
auth, vision, master_hub, etc.) or pure utility/admin endpoints.
"""
from .auth import router as auth_router, profile_router
from .injuries import router as injuries_router, set_injury_service, set_live_injury_service
from .vision import router as vision_router, player_router as player_vision_router, context_router as context_router, set_vision_service
from .live_scores import router as live_scores_router, command_center_router, set_live_scores_engine
from .ai_context import router as ai_context_router, set_ai_context_deps
from .master_hub import router as master_hub_router, set_master_hub_deps
from .odds_mapper import router as odds_mapper_router, set_odds_mapper_deps
from .payouts import router as payouts_router
from .social import router as social_router, set_social_signal_engine
from .game_lock import router as game_lock_router
from .adaptive_sync import router as adaptive_sync_router
from .admin import router as admin_router, set_admin_deps
from .command import router as command_router, set_db as set_command_db
from .live import router as live_router, set_db as set_live_db
from .qa_testing import router as qa_router, set_qa_db
from .image_proxy import router as image_proxy_router
from .ferrari_tiers import router as ferrari_router, set_ferrari_db
from .vacuum import router as vacuum_router, set_vacuum_db
from .mlb_vacuum import router as mlb_vacuum_router, set_mlb_vacuum_db
from .mlb_weather import router as mlb_weather_router
from .mlb_tiers import router as mlb_tiers_router, set_mlb_tiers_db
from .mlb_ripple import router as mlb_ripple_router, set_mlb_ripple_db
from .forward_testing import router as forward_testing_router, set_forward_test_db
from .intel_cache import router as intel_cache_router, set_db as set_intel_cache_db
from .scores import router as scores_router
from .delta_admin import router as delta_admin_router, set_delta_admin_db
from .gemini_admin import router as gemini_admin_router


def register_all_routes(
    app,
    db=None,
    injury_service=None,
    vision_service=None,
    live_scores_engine=None,
    ai_context_engine_class=None,
    master_hub_funcs=None,
    get_odds_mapper_func=None,
    social_signal_engine=None,
    stats_manager=None,
):
    """Register all route modules.

    Universal path only. DemonGoblinEngine and all its dependent routes
    were deleted as part of the 2026-04-22 HARD CONSOLIDATION.
    """
    # ---- Dependency injection for remaining routes ----
    if injury_service is not None:
        set_injury_service(injury_service)

    if db is not None:
        from services.live_injury_micro_sync import init_live_injury_service
        live_injury_svc = init_live_injury_service(db)
        set_live_injury_service(live_injury_svc)

    if db is not None:
        set_vision_service(vision_service, db)
    if live_scores_engine is not None:
        set_live_scores_engine(live_scores_engine)
    if db is not None and ai_context_engine_class is not None:
        set_ai_context_deps(db, ai_context_engine_class)
    if master_hub_funcs is not None:
        set_master_hub_deps(db, master_hub_funcs)
    if get_odds_mapper_func is not None and db is not None:
        set_odds_mapper_deps(db, get_odds_mapper_func)
    if social_signal_engine is not None:
        set_social_signal_engine(social_signal_engine)
    if stats_manager is not None and db is not None:
        set_admin_deps(stats_manager, db)

    # ---- Router registration ----
    # Auth
    app.include_router(auth_router, prefix="/api")
    app.include_router(profile_router, prefix="/api")

    # AI/Vision
    app.include_router(injuries_router, prefix="/api")
    app.include_router(vision_router, prefix="/api")
    app.include_router(player_vision_router, prefix="/api")
    app.include_router(context_router, prefix="/api")
    app.include_router(ai_context_router, prefix="/api")

    # Data routes
    app.include_router(live_scores_router, prefix="/api")
    app.include_router(command_center_router, prefix="/api")
    app.include_router(master_hub_router, prefix="/api")
    app.include_router(odds_mapper_router, prefix="/api")

    # Social
    app.include_router(payouts_router, prefix="/api")
    app.include_router(social_router, prefix="/api")

    # Game lock + adaptive sync + admin
    app.include_router(game_lock_router, prefix="/api")
    app.include_router(adaptive_sync_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")

    # Command Post
    if db is not None:
        set_command_db(db)
    app.include_router(command_router, prefix="/api")

    # Live Data
    if db is not None:
        set_live_db(db)
    app.include_router(live_router, prefix="/api")

    # QA Testing
    if db is not None:
        set_qa_db(db)
    app.include_router(qa_router, prefix="/api")

    # Image proxy
    app.include_router(image_proxy_router, prefix="/api")

    # Ferrari Tiers (canonical read path — reads {sport}_prop_scores)
    if db is not None:
        set_ferrari_db(db)
    app.include_router(ferrari_router, prefix="/api")

    # Usage Vacuum
    if db is not None:
        set_vacuum_db(db)
    app.include_router(vacuum_router, prefix="/api")

    # MLB Usage Vacuum
    if db is not None:
        set_mlb_vacuum_db(db)
    app.include_router(mlb_vacuum_router, prefix="/api")

    # MLB Weather
    app.include_router(mlb_weather_router, prefix="/api")

    # MLB Tiers (reads mlb_prop_scores)
    if db is not None:
        set_mlb_tiers_db(db)
    app.include_router(mlb_tiers_router, prefix="/api")

    # MLB Lineup Ripple Engine
    if db is not None:
        set_mlb_ripple_db(db)
    app.include_router(mlb_ripple_router, prefix="/api")

    # Forward Testing
    if db is not None:
        set_forward_test_db(db)
    app.include_router(forward_testing_router, prefix="/api")

    # Intel Cache
    if db is not None:
        set_intel_cache_db(db)
    app.include_router(intel_cache_router)

    # Scoring Recompute Framework
    app.include_router(scores_router)

    # Delta Engine admin
    if db is not None:
        set_delta_admin_db(db)
    app.include_router(delta_admin_router, prefix="/api")

    # Gemini admin
    app.include_router(gemini_admin_router, prefix="/api")
