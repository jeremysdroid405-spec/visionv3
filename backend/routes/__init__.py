"""Routes module initialization"""
from .picks import router as picks_router, set_engine as set_picks_engine
from .parlays import router as parlays_router, set_engine as set_parlays_engine
from .board import router as board_router, set_engine as set_board_engine
from .sync import router as sync_router, set_engine as set_sync_engine
from .intel import router as intel_router, set_engine as set_intel_engine
from .board_intel import router as board_intel_router, set_engine as set_board_intel_engine


def register_all_routes(app, engine):
    """Register all route modules and set engine"""
    # Set engine for all route modules
    set_picks_engine(engine)
    set_parlays_engine(engine)
    set_board_engine(engine)
    set_sync_engine(engine)
    set_intel_engine(engine)
    set_board_intel_engine(engine)
    
    # Include all routers
    app.include_router(picks_router)
    app.include_router(parlays_router)
    app.include_router(board_router)
    app.include_router(sync_router)
    app.include_router(intel_router)
    app.include_router(board_intel_router)
