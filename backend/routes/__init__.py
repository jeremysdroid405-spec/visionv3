"""Routes module initialization"""
from .picks import router as picks_router, set_engine as set_picks_engine
from .parlays import router as parlays_router, set_engine as set_parlays_engine
from .board import router as board_router, set_engine as set_board_engine
from .intel import router as intel_router, set_engine as set_intel_engine
from .board_intel import router as board_intel_router, set_engine as set_board_intel_engine

# Note: sync.py routes are NOT registered here because server.py has richer implementations
# with game lock integration. Sync routes will be migrated once server.py is thinned.


def register_all_routes(app, engine, game_lock_engine=None, db=None):
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
    
    # Include routers with /api prefix to match frontend expectations
    # Note: sync_router is NOT included - server.py handles sync routes
    app.include_router(picks_router, prefix="/api")
    app.include_router(parlays_router, prefix="/api")
    app.include_router(board_router, prefix="/api")
    app.include_router(intel_router, prefix="/api")
    app.include_router(board_intel_router, prefix="/api")
