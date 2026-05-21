"""
Emergent Admin API — scoped backend access for E1 agents.

All endpoints sit under /api/emergent-admin/* and require X-Admin-Token.
Every action is audit-logged to the `emergent_admin_audit_log` collection.

Mount in server.py:
    from routes.emergent_admin import router as emergent_admin_router
    app.include_router(emergent_admin_router, prefix="/api")
"""
from fastapi import APIRouter

from .auth      import router as auth_router
from .policy    import router as policy_router
from .collections import router as collections_router
from .jobs      import router as jobs_router
from .configs   import router as configs_router
from .services_admin import router as services_router
from .audit     import router as audit_router

router = APIRouter(prefix="/emergent-admin",
                    tags=["emergent-admin"])
router.include_router(auth_router,        prefix="/auth")
router.include_router(policy_router,      prefix="/policy")
router.include_router(collections_router, prefix="/collections")
router.include_router(jobs_router,        prefix="/jobs")
router.include_router(configs_router,     prefix="/configs")
router.include_router(services_router,    prefix="/services")
router.include_router(audit_router,       prefix="/audit")
