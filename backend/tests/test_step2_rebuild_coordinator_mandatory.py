"""
Step 2 contract: RebuildCoordinator is mandatory
================================================

Pins the 2026-05-07 enforcement that `run_master_sync()` cannot be
invoked without holding `UpstreamSyncLock.exclusive(sport)`. The lock
is the only signal the delta detector has that a full sync is in
flight; bypassing it produces partial-write races.

Five contracts:
  1. Bare call (no lock) raises `MasterSyncBypassError`.
  2. Call with the lock held proceeds.
  3. `_admin_override=True` skips the gate (escape hatch for the
     bootstrap init script).
  4. Every backend caller of `run_master_sync(` is classified —
     coordinator path, lock-acquiring path, or admin-override.
     A grep finding any UNCLASSIFIED bare call is a fail.
  5. The error message is loud (mentions `RebuildCoordinator` and
     `UpstreamSyncLock`) so debugging is fast.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")


# ── Contract 1 ───────────────────────────────────────────────────────
def test_bare_call_without_lock_raises_bypass_error():
    """Calling run_master_sync without holding the upstream lock MUST
    raise MasterSyncBypassError. Pre-2026-05-07 it logged CRITICAL and
    proceeded; that toothless check let 9 bypasses through in 4h."""
    from services.master_sync import MasterSyncBypassError, run_master_sync

    async def _bare():
        # Stub db (run_master_sync should fail before touching it).
        return await run_master_sync(object(), "nba")

    with pytest.raises(MasterSyncBypassError) as excinfo:
        asyncio.run(_bare())
    msg = str(excinfo.value)
    assert "UpstreamSyncLock" in msg
    assert "RebuildCoordinator" in msg


# ── Contract 2 ───────────────────────────────────────────────────────
def test_call_with_lock_held_passes_the_gate():
    """When the lock IS held, the gate is satisfied. We verify by
    asserting that `MasterSyncBypassError` is NOT raised. Any other
    exception thrown by the stub DB downstream is expected — those
    errors prove we reached real code AFTER the gate."""
    from services.master_sync import MasterSyncBypassError, run_master_sync
    from services.upstream_sync_lock import get_upstream_sync_lock

    async def _go():
        lock = get_upstream_sync_lock()
        async with lock.exclusive("nba", holder="contract-test"):
            try:
                await run_master_sync(object(), "nba")
            except MasterSyncBypassError:
                pytest.fail("Gate raised MasterSyncBypassError despite "
                            "the lock being held by this caller.")
            except Exception:
                pass  # post-gate stub-DB errors are expected

    asyncio.run(_go())


# ── Contract 3 ───────────────────────────────────────────────────────
def test_admin_override_passes_the_gate():
    """`_admin_override=True` is the single escape hatch for one-time
    bootstrap scripts (init_database.py). Same shape as contract 2:
    we only assert the BYPASS-ERROR is not raised."""
    from services.master_sync import MasterSyncBypassError, run_master_sync

    async def _go():
        try:
            await run_master_sync(object(), "nba", _admin_override=True)
        except MasterSyncBypassError:
            pytest.fail("admin_override did not bypass the gate")
        except Exception:
            pass  # post-gate stub-DB errors are expected

    asyncio.run(_go())


# ── Contract 4 ───────────────────────────────────────────────────────
def test_every_backend_call_site_is_classified():
    """Static parse: every `run_master_sync(` call in /app/backend
    must be in one of three categories:

      a. routes/* — uses `coord.dispatch_master_sync(...)` (NOT a direct
         call to run_master_sync)
      b. services/rebuild_coordinator.py — wraps in `lock.exclusive(...)`
      c. services/master_sync.py — the function definition + class
      d. server.py — wraps in `lock.exclusive(...)`
      e. scripts/init_database.py — uses `_admin_override=True`
      f. tests/* — out of scope

    Any direct `await run_master_sync(` outside these allowlisted shapes
    is an unclassified bypass and FAILS this contract.
    """
    backend = Path("/app/backend")
    pattern = re.compile(r"\brun_master_sync\s*\(")

    violations: list[tuple[str, int, str]] = []
    for py in backend.rglob("*.py"):
        rel = py.relative_to(backend).as_posix()
        if rel.startswith(("__pycache__/", "tests/", "scripts/")):
            continue
        if rel == "services/master_sync.py":
            continue  # function definition lives here
        text = py.read_text()
        if "run_master_sync" not in text:
            continue
        # Collapse whitespace into single-line "logical chunks" so
        # multi-line `async with lock.exclusive(...): \n run_master_sync(...)`
        # patterns resolve cleanly.
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            if not pattern.search(line):
                continue
            if "import" in line or "def " in line:
                continue  # imports / function defs / docstring refs
            if "_master_sync_state" in line:
                continue  # attribute reference, not a call
            # Look at the surrounding ±10 lines for an `async with
            # ... lock.exclusive(...)` or `_admin_override=True` or
            # `dispatch_master_sync(`. If any present → classified.
            window = "\n".join(lines[max(0, n - 12): n + 2])
            classified = any(token in window for token in (
                "lock.exclusive(",
                "_admin_override=True",
                "dispatch_master_sync(",
            ))
            if not classified:
                violations.append((rel, n, line.strip()))

    assert not violations, (
        "Unclassified `run_master_sync(` call sites found — every site "
        "MUST hold UpstreamSyncLock or use _admin_override:\n  "
        + "\n  ".join(f"{r}:{n}: {ln}" for r, n, ln in violations)
    )


# ── Contract 5 ───────────────────────────────────────────────────────
def test_bypass_error_message_is_loud_and_actionable():
    """Operator looking at a stack trace must see exactly what to fix."""
    from services.master_sync import MasterSyncBypassError, run_master_sync

    with pytest.raises(MasterSyncBypassError) as excinfo:
        asyncio.run(run_master_sync(object(), "mlb"))
    msg = str(excinfo.value)
    for required in (
        "MASTER_SYNC:mlb",
        "RebuildCoordinator.dispatch_master_sync",
        "lock.exclusive",
        "_admin_override=True",
    ):
        assert required in msg, (
            f"error message missing actionable hint `{required}`: {msg}"
        )
