"""Observability primitives — structured error logging and triage.

See `services/observability/error_log.py` for the rationale and API.
"""
from services.observability.error_log import (
    ERROR_LOG_COLLECTION,
    get_error_log_collection,
    log_caught_exception,
    log_silent_failure,
    log_silent_failure_fire_and_forget,
)

__all__ = [
    "ERROR_LOG_COLLECTION",
    "get_error_log_collection",
    "log_caught_exception",
    "log_silent_failure",
    "log_silent_failure_fire_and_forget",
]
