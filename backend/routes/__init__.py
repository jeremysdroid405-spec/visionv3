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
    
    # Include all routers with /api prefix to match existing frontend expectations
    app.include_router(picks_router, prefix="/api")
    app.include_router(parlays_router, prefix="/api")
    app.include_router(board_router, prefix="/api")
    app.include_router(sync_router, prefix="/api")
    app.include_router(intel_router, prefix="/api")
    app.include_router(board_intel_router, prefix="/api")
