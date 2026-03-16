"""Routes module initialization"""
from .picks import router as picks_router, set_engine as set_picks_engine
from .parlays import router as parlays_router, set_engine as set_parlays_engine
from .board import router as board_router, set_engine as set_board_engine
from .intel import router as intel_router, set_engine as set_intel_engine
from .board_intel import router as board_intel_router, set_engine as set_board_intel_engine
from .board_intel_v2 import router as board_intel_v2_router, set_board_intel_deps
from .auth import router as auth_router, profile_router
from .injuries import router as injuries_router, set_injury_service
from .vision import router as vision_router, set_vision_service
from .live_scores import router as live_scores_router, command_center_router, set_live_scores_engine
from .ai_context import router as ai_context_router, set_ai_context_deps
from .master_hub import router as master_hub_router, set_master_hub_deps
from .odds_mapper import router as odds_mapper_router, set_odds_mapper_deps
from .demon_tracker import router as demon_tracker_router, set_demon_tracker
from .payouts import router as payouts_router
from .validation import router as validation_router, set_raw_stat_fetcher
from .social import router as social_router, set_social_signal_engine
from .roster_sync import router as roster_sync_router, set_demon_goblin_engine as set_roster_engine
from .game_lock import router as game_lock_router
from .adaptive_sync import router as adaptive_sync_router
from .admin import router as admin_router, set_admin_deps
from .cached_data import router as cached_data_router, set_cached_data_engine
from .scheduler import router as scheduler_router, set_scheduler_deps
from .core_v3 import router as core_v3_router, set_core_v3_engine
from .tiers import router as tiers_router, set_tier_engine
from .intel_sync import router as intel_sync_router, set_intel_sync_engine
from .legacy import router as legacy_router, set_legacy_deps
from .command import router as command_router, set_db as set_command_db
from .live import router as live_router, set_db as set_live_db
from .qa_testing import router as qa_router, set_qa_db


def register_all_routes(app, engine, game_lock_engine=None, db=None, 
                        injury_service=None, vision_service=None, 
                        live_scores_engine=None, ai_context_engine_class=None,
                        master_hub_funcs=None, get_odds_mapper_func=None,
                        demon_tracker=None, raw_stat_fetcher=None,
                        social_signal_engine=None, demon_goblin_engine_class=None,
                        stats_manager=None, scheduler=None):
    """Register all route modules and set engine"""
    # Set engine for all route modules
    set_picks_engine(engine)
    set_parlays_engine(engine)
    set_board_engine(engine)
    set_intel_engine(engine)
    set_board_intel_engine(engine)
    set_roster_engine(engine)
    set_cached_data_engine(engine)
    set_core_v3_engine(engine)
    set_tier_engine(engine)
    set_intel_sync_engine(engine)
    set_legacy_deps(engine, stats_manager)
    
    # Set services for new routes
    if injury_service is not None:
        set_injury_service(injury_service)
    if vision_service is not None and db is not None:
        set_vision_service(vision_service, db)
    if live_scores_engine is not None:
        set_live_scores_engine(live_scores_engine)
    if db is not None and ai_context_engine_class is not None:
        set_ai_context_deps(db, ai_context_engine_class)
    if master_hub_funcs is not None:
        set_master_hub_deps(db, master_hub_funcs)
    if get_odds_mapper_func is not None and db is not None:
        set_odds_mapper_deps(db, get_odds_mapper_func)
    if demon_tracker is not None:
        set_demon_tracker(demon_tracker)
    if raw_stat_fetcher is not None:
        set_raw_stat_fetcher(raw_stat_fetcher)
    if social_signal_engine is not None:
        set_social_signal_engine(social_signal_engine)
    if db is not None and demon_goblin_engine_class is not None:
        set_board_intel_deps(db, demon_goblin_engine_class)
    if stats_manager is not None and db is not None:
        set_admin_deps(stats_manager, db)
    if engine is not None and live_scores_engine is not None:
        set_scheduler_deps(engine, live_scores_engine, scheduler)
    
    # Include routers with /api prefix to match frontend expectations
    app.include_router(picks_router, prefix="/api")
    app.include_router(parlays_router, prefix="/api")
    app.include_router(board_router, prefix="/api")
    app.include_router(intel_router, prefix="/api")
    app.include_router(board_intel_router, prefix="/api")
    
    # Auth routes
    app.include_router(auth_router, prefix="/api")
    app.include_router(profile_router, prefix="/api")
    
    # AI/Vision routes
    app.include_router(injuries_router, prefix="/api")
    app.include_router(vision_router, prefix="/api")
    app.include_router(ai_context_router, prefix="/api")
    
    # Data routes
    app.include_router(live_scores_router, prefix="/api")
    app.include_router(command_center_router, prefix="/api")
    app.include_router(master_hub_router, prefix="/api")
    app.include_router(odds_mapper_router, prefix="/api")
    app.include_router(demon_tracker_router, prefix="/api")
    
    # Phase 14-17 extraction
    app.include_router(payouts_router, prefix="/api")
    app.include_router(validation_router, prefix="/api")
    app.include_router(social_router, prefix="/api")
    app.include_router(roster_sync_router, prefix="/api")
    app.include_router(board_intel_v2_router, prefix="/api")
    app.include_router(game_lock_router, prefix="/api")
    app.include_router(adaptive_sync_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(cached_data_router, prefix="/api")
    app.include_router(scheduler_router, prefix="/api")
    
    # Phase 18 extraction - Core V3 routes
    app.include_router(core_v3_router, prefix="/api")
    app.include_router(tiers_router, prefix="/api")
    app.include_router(intel_sync_router, prefix="/api")
    app.include_router(legacy_router, prefix="/api")
    
    # Command Post - Risk Assessment Hub
    if db is not None:
        set_command_db(db)
    app.include_router(command_router, prefix="/api")
    
    # Live Data - Scores and News tickers
    if db is not None:
        set_live_db(db)
    app.include_router(live_router, prefix="/api")
    
    # QA Testing routes
    if db is not None:
        set_qa_db(db)
    app.include_router(qa_router, prefix="/api")
