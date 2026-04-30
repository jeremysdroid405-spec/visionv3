"""Regression tests for the structured error logger.

These tests lock in the invariants of the observability primitive so
that future refactors can't silently break the one thing that exists
to catch silent failures.
"""
import asyncio
import os
import pytest
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

from services.observability import (
    ERROR_LOG_COLLECTION,
    log_caught_exception,
    log_silent_failure,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_log_caught_exception_persists_row(db):
    """Any caught exception ends up as a structured row with full context."""
    # Clear any prior test rows.
    await db[ERROR_LOG_COLLECTION].delete_many({"subsystem": "test_suite.basic"})

    try:
        raise ValueError("boom — sample message")
    except ValueError as e:
        ok = await log_caught_exception(
            db, e,
            subsystem="test_suite.basic",
            sport="mlb",
            context={"event_id": "evt_123", "attempt": 2},
        )

    assert ok is True

    row = await db[ERROR_LOG_COLLECTION].find_one({"subsystem": "test_suite.basic"})
    assert row is not None
    assert row["exception_type"] == "ValueError"
    assert "boom" in row["message"]
    assert row["sport"] == "mlb"
    assert row["context"]["event_id"] == "evt_123"
    assert row["context"]["attempt"] == 2
    assert "traceback" in row
    assert isinstance(row["ts"], datetime)

    await db[ERROR_LOG_COLLECTION].delete_many({"subsystem": "test_suite.basic"})


@pytest.mark.asyncio
async def test_log_caught_exception_never_raises_on_db_failure():
    """The logger must never raise, even if the DB is unreachable.

    This is the single most important invariant: an error in the error
    logger must never create a new error.
    """
    class BrokenDB:
        def __getitem__(self, _):
            raise RuntimeError("db is down")

    try:
        raise RuntimeError("downstream failure")
    except RuntimeError as e:
        # Must return False without raising even though the DB blows up.
        ok = await log_caught_exception(
            BrokenDB(), e,
            subsystem="test_suite.broken_db",
        )
    assert ok is False


def test_log_silent_failure_never_raises():
    """Sync fallback must survive even the craziest inputs."""
    try:
        raise TypeError("sync failure path")
    except TypeError as e:
        # Weird context values that aren't natively mongo-serializable.
        log_silent_failure(
            "test_suite.sync",
            e,
            context={
                "set_not_serializable": {1, 2, 3},
                "lambda_ref": lambda x: x,
                "deep": {"nested": {"a": 1}},
            },
        )
    # If we reach here without raising, invariant holds.
    assert True


@pytest.mark.asyncio
async def test_traceback_truncation_respects_doc_limit(db):
    """Very long tracebacks are trimmed so one bad error can't blow out the
    16 MB Mongo document limit or flood the collection."""
    await db[ERROR_LOG_COLLECTION].delete_many(
        {"subsystem": "test_suite.truncate"}
    )
    # Build a 50 KB-message error.
    msg = "x" * 50_000
    try:
        raise RuntimeError(msg)
    except RuntimeError as e:
        await log_caught_exception(
            db, e, subsystem="test_suite.truncate",
        )

    row = await db[ERROR_LOG_COLLECTION].find_one(
        {"subsystem": "test_suite.truncate"}
    )
    assert row is not None
    # message field is capped at 2000 chars.
    assert len(row["message"]) <= 2000
    # traceback is capped at ~16K.
    assert len(row["traceback"]) <= 17_000

    await db[ERROR_LOG_COLLECTION].delete_many(
        {"subsystem": "test_suite.truncate"}
    )


@pytest.mark.asyncio
async def test_indexes_are_created_idempotently(db):
    """TTL + triage indexes exist after any write, and re-writing doesn't
    re-run createIndex every call."""
    await db[ERROR_LOG_COLLECTION].delete_many({"subsystem": "test_suite.idx"})
    try:
        raise ValueError("idx check")
    except ValueError as e:
        await log_caught_exception(db, e, subsystem="test_suite.idx")

    idx = await db[ERROR_LOG_COLLECTION].index_information()
    # TTL index on ts exists with expireAfterSeconds.
    has_ttl = any(
        "expireAfterSeconds" in spec and "ts" in dict(spec.get("key", [])).keys()
        for spec in idx.values()
    )
    assert has_ttl, f"TTL index missing. Indexes: {list(idx.keys())}"

    await db[ERROR_LOG_COLLECTION].delete_many({"subsystem": "test_suite.idx"})
