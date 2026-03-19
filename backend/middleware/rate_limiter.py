"""
Rate Limiter Middleware
=======================
High-performance rate limiting using Token Bucket algorithm with in-memory storage.

Features:
- Token Bucket algorithm for smooth rate limiting
- In-memory cache (no database hits)
- Standard 2026 rate limit headers
- Per-IP and per-endpoint rate limiting
- Configurable limits by endpoint tier
"""
import time
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """Token Bucket implementation for rate limiting."""
    capacity: int  # Maximum tokens
    refill_rate: float  # Tokens per second
    tokens: float = field(default=0)
    last_update: float = field(default_factory=time.time)
    
    def __post_init__(self):
        self.tokens = float(self.capacity)
    
    def consume(self, tokens: int = 1) -> Tuple[bool, float, float]:
        """
        Try to consume tokens from the bucket.
        
        Returns:
            Tuple of (success, remaining_tokens, reset_time)
        """
        now = time.time()
        
        # Refill tokens based on time elapsed
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_update = now
        
        # Calculate reset time (when bucket will be full)
        tokens_needed = self.capacity - self.tokens
        reset_time = now + (tokens_needed / self.refill_rate) if self.refill_rate > 0 else now + 60
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, self.tokens, reset_time
        
        return False, self.tokens, reset_time


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting tiers."""
    requests_per_minute: int
    burst_size: int  # Maximum burst capacity
    
    @property
    def refill_rate(self) -> float:
        """Tokens per second."""
        return self.requests_per_minute / 60.0


# Rate limit tiers by endpoint pattern
RATE_LIMIT_TIERS: Dict[str, RateLimitConfig] = {
    # High-frequency endpoints (read-heavy) - increased for dashboard loading
    "default": RateLimitConfig(requests_per_minute=200, burst_size=50),
    
    # AI/Sync endpoints (expensive operations)
    "sync": RateLimitConfig(requests_per_minute=10, burst_size=3),
    "intel": RateLimitConfig(requests_per_minute=30, burst_size=5),
    "vision": RateLimitConfig(requests_per_minute=20, burst_size=3),
    "briefing": RateLimitConfig(requests_per_minute=20, burst_size=5),
    
    # Admin endpoints
    "admin": RateLimitConfig(requests_per_minute=30, burst_size=10),
    
    # Authentication endpoints (prevent brute force)
    "auth": RateLimitConfig(requests_per_minute=20, burst_size=5),
    
    # Data-heavy endpoints - increased for player detail pages
    "board": RateLimitConfig(requests_per_minute=120, burst_size=30),
    "players": RateLimitConfig(requests_per_minute=120, burst_size=30),
}

# Endpoint pattern to tier mapping
ENDPOINT_TIER_MAP: Dict[str, str] = {
    # Sync operations
    "/api/v3/sync": "sync",
    "/api/v3/sync-to-mongo": "sync",
    "/api/v3/primary-sync": "sync",
    "/api/v3/delta-refresh": "sync",
    "/api/sync-rosters": "sync",
    "/api/trigger-daily-sync": "sync",
    
    # AI/Intel operations
    "/api/v3/generate-intel-briefings": "intel",
    "/api/v3/intel-briefing": "briefing",
    "/api/intel": "intel",
    "/api/v3/vision": "vision",
    
    # Admin operations
    "/api/cache-status": "admin",
    "/api/clear-all-cache": "admin",
    "/api/clear-expired-cache": "admin",
    
    # Auth operations
    "/api/auth/login": "auth",
    "/api/auth/signup": "auth",
    "/api/auth/register": "auth",
    
    # Board/Player data
    "/api/v3/board": "board",
    "/api/v3/players": "players",
    "/api/v3/cached-props": "board",
    "/api/v3/war-zone": "board",
    "/api/v3/goblin-vault": "board",
    "/api/v3/front-lines": "board",
    "/api/v3/player-with-badges": "players",
    "/api/v3/safe-haven": "board",
    "/api/v3/most-popular-bets": "board",
    "/api/command": "players",
    "/api/live": "board",
}


class RateLimiterStorage:
    """In-memory storage for rate limit buckets."""
    
    def __init__(self, cleanup_interval: int = 300):
        """
        Initialize storage.
        
        Args:
            cleanup_interval: Seconds between cleanup of stale buckets
        """
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
    
    def _get_bucket_key(self, client_ip: str, endpoint_tier: str) -> str:
        """Generate a unique key for the bucket."""
        return f"{client_ip}:{endpoint_tier}"
    
    async def get_or_create_bucket(
        self, 
        client_ip: str, 
        endpoint_tier: str,
        config: RateLimitConfig
    ) -> TokenBucket:
        """Get existing bucket or create a new one."""
        key = self._get_bucket_key(client_ip, endpoint_tier)
        
        async with self._lock:
            # Periodic cleanup of stale buckets
            if time.time() - self._last_cleanup > self._cleanup_interval:
                await self._cleanup_stale_buckets()
            
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(
                    capacity=config.burst_size,
                    refill_rate=config.refill_rate
                )
            
            return self._buckets[key]
    
    async def _cleanup_stale_buckets(self):
        """Remove buckets that haven't been used recently."""
        now = time.time()
        stale_threshold = 600  # 10 minutes
        
        stale_keys = [
            key for key, bucket in self._buckets.items()
            if now - bucket.last_update > stale_threshold
        ]
        
        for key in stale_keys:
            del self._buckets[key]
        
        if stale_keys:
            logger.debug(f"[RATE_LIMIT] Cleaned up {len(stale_keys)} stale buckets")
        
        self._last_cleanup = now
    
    def get_stats(self) -> Dict:
        """Get storage statistics."""
        return {
            "active_buckets": len(self._buckets),
            "last_cleanup": self._last_cleanup
        }


# Global storage instance
_rate_limit_storage: Optional[RateLimiterStorage] = None


def get_rate_limit_storage() -> RateLimiterStorage:
    """Get the global rate limit storage instance."""
    global _rate_limit_storage
    if _rate_limit_storage is None:
        _rate_limit_storage = RateLimiterStorage()
    return _rate_limit_storage


def get_endpoint_tier(path: str) -> str:
    """Determine the rate limit tier for an endpoint."""
    # Check exact matches first
    if path in ENDPOINT_TIER_MAP:
        return ENDPOINT_TIER_MAP[path]
    
    # Check prefix matches
    for pattern, tier in ENDPOINT_TIER_MAP.items():
        if path.startswith(pattern):
            return tier
    
    return "default"


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, handling proxies."""
    # Check for forwarded headers (common in production)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP in the chain (original client)
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fallback to direct connection
    if request.client:
        return request.client.host
    
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware using Token Bucket algorithm.
    
    Adds standard rate limit headers to all responses:
    - X-RateLimit-Limit: Maximum requests per window
    - X-RateLimit-Remaining: Remaining requests
    - X-RateLimit-Reset: Unix timestamp when limit resets
    - Retry-After: (on 429) Seconds until retry is allowed
    """
    
    def __init__(self, app, enabled: bool = True, exempt_paths: list = None):
        super().__init__(app)
        self.enabled = enabled
        self.exempt_paths = exempt_paths or [
            "/api/docs",
            "/api/redoc",
            "/api/openapi.json",
            "/api/health",
            "/api/",  # Root endpoint
            "/api/proxy/nba-headshot",  # Images are cached, no need to rate limit
        ]
        self.storage = get_rate_limit_storage()
    
    async def dispatch(self, request: Request, call_next):
        # Skip if disabled or exempt path
        if not self.enabled:
            return await call_next(request)
        
        path = request.url.path
        
        # Check exempt paths
        for exempt in self.exempt_paths:
            if path == exempt or path.startswith(exempt + "/"):
                return await call_next(request)
        
        # Get rate limit configuration
        tier = get_endpoint_tier(path)
        config = RATE_LIMIT_TIERS.get(tier, RATE_LIMIT_TIERS["default"])
        client_ip = get_client_ip(request)
        
        # Get or create bucket for this client/tier
        bucket = await self.storage.get_or_create_bucket(client_ip, tier, config)
        
        # Try to consume a token
        allowed, remaining, reset_time = bucket.consume(1)
        
        # Prepare rate limit headers
        headers = {
            "X-RateLimit-Limit": str(config.requests_per_minute),
            "X-RateLimit-Remaining": str(max(0, int(remaining))),
            "X-RateLimit-Reset": str(int(reset_time)),
            "X-RateLimit-Tier": tier,
        }
        
        if not allowed:
            # Calculate retry time
            retry_after = int(reset_time - time.time()) + 1
            headers["Retry-After"] = str(max(1, retry_after))
            
            logger.warning(
                f"[RATE_LIMIT] Rate limit exceeded: {client_ip} on {path} (tier: {tier})"
            )
            
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    "tier": tier,
                    "limit": config.requests_per_minute,
                    "retry_after": retry_after
                },
                headers=headers
            )
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers to response
        for key, value in headers.items():
            response.headers[key] = value
        
        return response


def create_rate_limit_middleware(app, enabled: bool = True) -> RateLimitMiddleware:
    """Factory function to create rate limit middleware."""
    return RateLimitMiddleware(app, enabled=enabled)
