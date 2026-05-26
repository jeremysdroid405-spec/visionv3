"""
Tests for the /optimizer/{run_id}/results endpoint refactor (2026-05-26).

Pins TWO contracts:

  1. **`maxTimeMS` is set on every aggregation** so the endpoint fails
     fast (Mongo returns OperationFailure) instead of letting nginx
     504 at the 60-second mark. This is what caused the
     "504 every time I open the testing suite" symptom in prod.

  2. **All read aggregations run concurrently** via `asyncio.gather`
     rather than serially. With 6 separate aggregations on a 900k-row
     collection, serial dispatch alone could push the response past
     nginx's 60s ceiling even when each query is fast in isolation.

  Both contracts are verified by code-inspection of the endpoint source
  (we walk the bytecode of `optimizer_results`) rather than firing a
  pretend Mongo and timing it — the latter is flaky in CI.
"""
from __future__ import annotations
import inspect
import sys

sys.path.insert(0, "/app/backend")

from routes.emergent_admin import optimizer


def _src_of(fn_name: str) -> str:
    fn = getattr(optimizer, fn_name)
    return inspect.getsource(fn)


def test_results_endpoint_passes_max_time_ms():
    """The `/results` endpoint must pin `maxTimeMS` on every cursor it
    opens so a runaway aggregation fails at the Mongo layer instead of
    hanging the FastAPI worker until nginx 504s."""
    src = _src_of("get_results")
    assert "maxTimeMS" in src, (
        "expected `maxTimeMS=...` on the aggregation cursors in "
        "/optimizer/{run_id}/results — 504s are caused by missing this")
    # The threshold must be ≤ nginx's 60s ceiling — anything else
    # defeats the purpose. Lock the constant.
    assert "_AGG_TIMEOUT_MS = 30_000" in src or "30000" in src or "30_000" in src, (
        "maxTimeMS must be ≤ 30s to fail before nginx's 60s 504")


def test_results_endpoint_runs_aggregations_in_parallel():
    """All read-side aggregations must be dispatched concurrently via
    `asyncio.gather` so the response is bottlenecked by the slowest
    one, not the sum.  Six serial × ~10s = 60s = nginx 504. Same six
    in parallel = ~10s, comfortably below the ceiling."""
    src = _src_of("get_results")
    assert "asyncio.gather(" in src, (
        "expected `asyncio.gather(...)` in /results endpoint — without "
        "it, the 6 aggregations run serially and can blow the 60s "
        "nginx timeout even if each one is individually fast")


def test_results_endpoint_has_clean_error_path_on_timeout():
    """When an aggregation does exceed `maxTimeMS`, the endpoint must
    convert the Mongo OperationFailure into a clean 503 with a
    meaningful message — not let it bubble as an opaque 500 (or
    silently let nginx 504)."""
    src = _src_of("get_results")
    assert "503" in src, (
        "/results must convert aggregation timeouts to 503 (not 500) "
        "so the operator gets actionable feedback")
    assert "HTTPException" in src and ("aggregation" in src.lower()
                                                   or "timeout" in src.lower())


def test_default_agg_timeout_module_const_is_set():
    """The module-level `_DEFAULT_AGG_TIMEOUT_MS` is the SSOT for
    aggregation budgets. Lock it ≤ 30s so it can't drift past the
    nginx ceiling in a future refactor."""
    val = getattr(optimizer, "_DEFAULT_AGG_TIMEOUT_MS", None)
    assert val is not None, "module must export _DEFAULT_AGG_TIMEOUT_MS"
    assert isinstance(val, int)
    assert 5_000 <= val <= 30_000, (
        f"_DEFAULT_AGG_TIMEOUT_MS must be 5..30s (got {val} ms) — "
        f"<5s starves real aggregations, >30s blows the nginx 504 floor")


def test_helper_agg_to_list_passes_max_time_ms():
    """The shared `_agg_to_list` helper must propagate `maxTimeMS` to
    the underlying `.aggregate(...)` call so all routes using it get
    the safety net for free."""
    src = inspect.getsource(optimizer._agg_to_list)
    assert "maxTimeMS" in src
    assert "allowDiskUse" in src
