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

# Note: sync.py routes are NOT registered here because server.py has richer implementations
# with game lock integration. Sync routes will be migrated once server.py is thinned.


def register_all_routes(app, engine, game_lock_engine=None, db=None, 
                        injury_service=None, vision_service=None, 
                        live_scores_engine=None, ai_context_engine_class=None,
                        master_hub_funcs=None, get_odds_mapper_func=None,
                        demon_tracker=None, raw_stat_fetcher=None,
                        social_signal_engine=None, demon_goblin_engine_class=None):
    """Register all route modules and set engine
    
    Note: Some routes (sync, lock) remain in server.py because they have
    additional game_lock_engine integration that needs to be migrated.
    """
    # Set engine for all route modules
    set_picks_engine(engine)
    set_parlays_engine(engine)
    set_board_engine(engine)
    set_intel_engine(engine)
    set_board_intel_engine(engine)
    set_roster_engine(engine)
    
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
    
    # New routes (Phase 14-15 extraction)
    app.include_router(payouts_router, prefix="/api")
    app.include_router(validation_router, prefix="/api")
    app.include_router(social_router, prefix="/api")
    app.include_router(roster_sync_router, prefix="/api")
    app.include_router(board_intel_v2_router, prefix="/api")
