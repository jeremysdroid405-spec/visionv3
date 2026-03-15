"""
Request Tracer Middleware
=========================
Generates unique request IDs for tracing and debugging.

Features:
- UUIDv4 generation for each request
- Propagation through request state
- Response header injection
- Logger integration for automatic tracing
"""
import uuid
import time
import logging
from contextvars import ContextVar
from typing import Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Context variable for request ID (thread-safe)
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
request_start_time_var: ContextVar[Optional[float]] = ContextVar("request_start_time", default=None)

logger = logging.getLogger(__name__)


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return request_id_var.get()


def get_request_duration() -> Optional[float]:
    """Get the current request duration in milliseconds."""
    start_time = request_start_time_var.get()
    if start_time:
        return (time.time() - start_time) * 1000
    return None


class RequestIdFilter(logging.Filter):
    """
    Logging filter that adds request_id to log records.
    
    Usage:
        handler.addFilter(RequestIdFilter())
        formatter = logging.Formatter('%(request_id)s - %(message)s')
    """
    
    def filter(self, record):
        record.request_id = get_request_id() or "no-request-id"
        record.request_duration = get_request_duration()
        return True


class TracingFormatter(logging.Formatter):
    """
    Custom formatter that prepends request ID to log messages.
    
    Format: [request_id] timestamp - level - message
    """
    
    def format(self, record):
        # Add request ID prefix if available
        request_id = getattr(record, 'request_id', None) or get_request_id()
        
        if request_id and request_id != "no-request-id":
            # Truncate UUID for readability (first 8 chars)
            short_id = request_id[:8]
            record.msg = f"[{short_id}] {record.msg}"
        
        return super().format(record)


class RequestTracerMiddleware(BaseHTTPMiddleware):
    """
    Middleware for request tracing and ID generation.
    
    Adds the following headers to all responses:
    - X-Request-ID: Unique identifier for the request
    - X-Response-Time: Processing time in milliseconds
    
    Also attaches request_id to request.state for use in handlers.
    """
    
    def __init__(self, app, header_name: str = "X-Request-ID"):
        super().__init__(app)
        self.header_name = header_name
    
    def _generate_request_id(self) -> str:
        """Generate a new UUIDv4 request ID."""
        return str(uuid.uuid4())
    
    async def dispatch(self, request: Request, call_next):
        # Check if client provided a request ID (for distributed tracing)
        request_id = request.headers.get(self.header_name)
        
        # Generate new ID if not provided
        if not request_id:
            request_id = self._generate_request_id()
        
        # Store in context variables
        request_id_var.set(request_id)
        request_start_time_var.set(time.time())
        
        # Attach to request state for handler access
        request.state.request_id = request_id
        request.state.start_time = time.time()
        
        # Log request start
        logger.debug(
            f"Request started: {request.method} {request.url.path}",
            extra={"request_id": request_id}
        )
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate response time
            duration_ms = (time.time() - request.state.start_time) * 1000
            
            # Add tracing headers
            response.headers[self.header_name] = request_id
            response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
            
            # Log request completion
            logger.debug(
                f"Request completed: {request.method} {request.url.path} "
                f"- Status: {response.status_code} - Duration: {duration_ms:.2f}ms",
                extra={"request_id": request_id}
            )
            
            return response
            
        except Exception as e:
            # Log error with request ID for debugging
            duration_ms = (time.time() - request.state.start_time) * 1000
            logger.error(
                f"Request failed: {request.method} {request.url.path} "
                f"- Error: {str(e)} - Duration: {duration_ms:.2f}ms",
                extra={"request_id": request_id},
                exc_info=True
            )
            raise
        
        finally:
            # Clear context variables
            request_id_var.set(None)
            request_start_time_var.set(None)


def setup_tracing_logger(logger_name: str = None) -> logging.Logger:
    """
    Configure a logger with request ID tracing.
    
    Args:
        logger_name: Name of the logger to configure (None for root)
    
    Returns:
        Configured logger instance
    """
    target_logger = logging.getLogger(logger_name)
    
    # Add request ID filter to all handlers
    request_filter = RequestIdFilter()
    for handler in target_logger.handlers:
        handler.addFilter(request_filter)
    
    return target_logger


def get_trace_context() -> dict:
    """
    Get the current trace context for external service calls.
    
    Returns:
        Dict with trace headers to propagate
    """
    request_id = get_request_id()
    return {
        "X-Request-ID": request_id,
        "X-Correlation-ID": request_id,  # Alternative header name
    } if request_id else {}


def create_tracer_middleware(app, header_name: str = "X-Request-ID") -> RequestTracerMiddleware:
    """Factory function to create tracer middleware."""
    return RequestTracerMiddleware(app, header_name=header_name)
