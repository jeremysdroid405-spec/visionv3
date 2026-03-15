"""
API Versioning Configuration
============================
Centralized configuration for API versioning and deprecation management.
"""
from enum import Enum
from datetime import date
from typing import Dict, List, Optional
from pydantic import BaseModel


class APIVersion(str, Enum):
    """Supported API versions."""
    V3 = "v3"
    V4 = "v4"  # Future version


class EndpointStatus(str, Enum):
    """Endpoint lifecycle status."""
    STABLE = "stable"
    BETA = "beta"
    DEPRECATED = "deprecated"
    SUNSET = "sunset"  # Will be removed


class VersionedEndpoint(BaseModel):
    """Configuration for a versioned endpoint."""
    path: str
    version: APIVersion
    status: EndpointStatus
    introduced: str  # Date string YYYY-MM-DD
    deprecated_date: Optional[str] = None
    sunset_date: Optional[str] = None
    replacement: Optional[str] = None
    notes: Optional[str] = None


# Current API Version
CURRENT_VERSION = APIVersion.V3

# Minimum supported version
MIN_SUPPORTED_VERSION = APIVersion.V3

# Version-specific configurations
VERSION_CONFIG: Dict[APIVersion, Dict] = {
    APIVersion.V3: {
        "prefix": "/api/v3",
        "released": "2025-01-01",
        "status": "stable",
        "description": "Current stable version with full feature set"
    },
    APIVersion.V4: {
        "prefix": "/api/v4",
        "released": None,  # Not yet released
        "status": "planned",
        "description": "Future version with breaking changes",
        "planned_features": [
            "Unified response format",
            "GraphQL support",
            "WebSocket real-time updates",
            "Rate limiting headers",
            "Request ID tracking"
        ]
    }
}

# Deprecated endpoints that will be removed in v4
DEPRECATED_ENDPOINTS: List[VersionedEndpoint] = [
    VersionedEndpoint(
        path="/api/full-board",
        version=APIVersion.V3,
        status=EndpointStatus.DEPRECATED,
        introduced="2024-01-01",
        deprecated_date="2025-06-01",
        sunset_date="2026-01-01",
        replacement="/api/v3/board",
        notes="Use /api/v3/board for production"
    ),
    VersionedEndpoint(
        path="/api/calculate-hit-rate",
        version=APIVersion.V3,
        status=EndpointStatus.DEPRECATED,
        introduced="2024-01-01",
        deprecated_date="2025-06-01",
        replacement="/api/v3/player/{name}",
        notes="Hit rates included in player detail response"
    ),
    VersionedEndpoint(
        path="/api/validate-demon",
        version=APIVersion.V3,
        status=EndpointStatus.DEPRECATED,
        introduced="2024-01-01",
        deprecated_date="2025-06-01",
        replacement="/api/v3/demons",
        notes="Demon validation now automatic in /v3/demons"
    ),
]

# V4 Breaking Changes (Planned)
V4_BREAKING_CHANGES = [
    {
        "change": "Unified response envelope",
        "description": "All responses will use {success, data, error, meta} format",
        "migration": "Update response parsing to use .data instead of direct access"
    },
    {
        "change": "Pagination standardization",
        "description": "All list endpoints will use cursor-based pagination",
        "migration": "Replace offset/limit with cursor/limit parameters"
    },
    {
        "change": "Error code standardization",
        "description": "All errors will use RFC 7807 Problem Details format",
        "migration": "Update error handling to parse type, title, detail, status"
    },
    {
        "change": "Rate limit headers",
        "description": "X-RateLimit-* headers on all responses",
        "migration": "Implement rate limit handling in client"
    },
    {
        "change": "Request ID tracking",
        "description": "X-Request-ID header for debugging",
        "migration": "Log request IDs for support tickets"
    },
]


def get_version_info(version: APIVersion) -> Dict:
    """Get configuration for a specific API version."""
    return VERSION_CONFIG.get(version, {})


def is_version_supported(version: APIVersion) -> bool:
    """Check if a version is still supported."""
    return version.value >= MIN_SUPPORTED_VERSION.value


def get_deprecated_endpoints() -> List[VersionedEndpoint]:
    """Get list of deprecated endpoints."""
    return DEPRECATED_ENDPOINTS


def get_sunset_warning(endpoint_path: str) -> Optional[str]:
    """Get sunset warning message for an endpoint if applicable."""
    for ep in DEPRECATED_ENDPOINTS:
        if ep.path == endpoint_path and ep.status in [EndpointStatus.DEPRECATED, EndpointStatus.SUNSET]:
            if ep.sunset_date:
                return f"This endpoint will be removed on {ep.sunset_date}. Use {ep.replacement} instead."
            elif ep.replacement:
                return f"This endpoint is deprecated. Use {ep.replacement} instead."
    return None
