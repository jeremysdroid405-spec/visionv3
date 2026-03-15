"""
Middleware Package
==================
High-performance middleware for rate limiting, request tracing, and more.
"""
from .rate_limiter import (
    RateLimitMiddleware,
    create_rate_limit_middleware,
    get_rate_limit_storage,
    get_endpoint_tier,
    get_client_ip,
    RATE_LIMIT_TIERS,
)

from .tracer import (
    RequestTracerMiddleware,
    create_tracer_middleware,
    get_request_id,
    get_request_duration,
    get_trace_context,
    setup_tracing_logger,
    RequestIdFilter,
    TracingFormatter,
    request_id_var,
)

__all__ = [
    # Rate Limiter
    "RateLimitMiddleware",
    "create_rate_limit_middleware",
    "get_rate_limit_storage",
    "get_endpoint_tier",
    "get_client_ip",
    "RATE_LIMIT_TIERS",
    # Tracer
    "RequestTracerMiddleware",
    "create_tracer_middleware",
    "get_request_id",
    "get_request_duration",
    "get_trace_context",
    "setup_tracing_logger",
    "RequestIdFilter",
    "TracingFormatter",
    "request_id_var",
]
