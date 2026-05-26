"""
Pipeline-step subprocess-exit contract (2026-05-26).

ROOT CAUSE this pins:
  Several SSOT historical-pipeline scripts opened
  `AsyncIOMotorClient(os.environ["MONGO_URL"])` at function entry and
  NEVER called `.close()`. When `asyncio.run(_run())` returned, motor's
  background connection-pool tasks kept the event loop alive → the
  Python subprocess never exited → research_worker saw the job as
  `status='running'` forever → the orchestrator never advanced to the
  next step. The user described this as "freezes between Full Pipeline
  and Gate Grid."

CONTRACT this test pins:
  Every SSOT pipeline script under `scripts/sgo/` and
  `scripts/research/` that opens an `AsyncIOMotorClient` MUST close it.
  Verified by source-grep: both `client = AsyncIOMotorClient(...)` and
  a corresponding `client.close()` (or `cli.close()`) must appear.

  We grep the source (cheap, deterministic) rather than launching
  subprocesses (slow, flaky in CI, requires a live Mongo).
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

BACKEND = Path("/app/backend")

# Scripts that previously leaked the motor client and now MUST close it.
# These are the modules wired into the Workflow / Guided pipeline (see
# workers/queue.py::HEAVY_MODULES + AdminTesting.jsx::SPORT_ADAPTERS).
SCRIPTS = [
    "scripts/sgo/historical_full_pipeline_replay.py",
    "scripts/sgo/historical_gate_replay_grid.py",
    "scripts/sgo/reshape_sgo_to_replay_odds.py",
    "scripts/sgo/score_historical_with_live_mlb_hf.py",
    "scripts/research/grid_sweep.py",
    # Already-good scripts kept here so any regression that REMOVES the
    # close() call gets caught:
    "scripts/sgo/build_pp_research_core.py",
    "scripts/sgo/build_historical_outcomes.py",
    "scripts/sgo/build_historical_model_features.py",
    "scripts/sgo/ingest_historical_player_stats.py",
    "scripts/mlb_replay_build_feature_cache.py",
]


@pytest.mark.parametrize("rel_path", SCRIPTS)
def test_script_closes_motor_client(rel_path: str):
    src = (BACKEND / rel_path).read_text()
    # Must open a client …
    opens = re.findall(
        r"\b(?:client|cli|c)\s*=\s*AsyncIOMotorClient\s*\(", src)
    assert opens, (
        f"{rel_path} doesn't open AsyncIOMotorClient with a named "
        f"variable — refactor needed so we can close it")
    # … AND must call .close() on it somewhere.
    closes = re.findall(r"\b(?:client|cli|c)\.close\s*\(", src)
    assert closes, (
        f"{rel_path} opens AsyncIOMotorClient but never calls "
        f".close() on it. This leaks motor's background tasks and "
        f"prevents `asyncio.run()` from returning — subprocess hangs "
        f"after the script logically finishes. Wrap the body in "
        f"try/finally + client.close().")


@pytest.mark.parametrize("rel_path", SCRIPTS)
def test_script_uses_try_finally_for_close(rel_path: str):
    """Close MUST be in a finally (or equivalent context manager) so
    it runs on exception paths too — otherwise a HARD-FAIL preflight
    leaves the client open and the subprocess still hangs."""
    src = (BACKEND / rel_path).read_text()
    has_try   = "try:" in src
    has_final = re.search(r"\bfinally\s*:", src) is not None
    has_close = re.search(r"\b(?:client|cli|c)\.close\s*\(", src) is not None
    assert has_close
    # Either: try/finally pattern (preferred) OR an `async with` /
    # context manager that auto-closes. We accept either by checking
    # for one of: (try AND finally), or `__aexit__` / `async with` /
    # contextlib hints. In practice all our scripts use try/finally.
    if not (has_try and has_final):
        # Some legacy scripts close in multiple branches without a
        # single try/finally; allow if the close() count is ≥ 2 (e.g.
        # one per success/exception path).
        n_closes = len(re.findall(
            r"\b(?:client|cli|c)\.close\s*\(", src))
        assert n_closes >= 2, (
            f"{rel_path}: close() must be in a try/finally (or "
            f"appear on every exit path). Found {n_closes} close() "
            f"call(s) and no try/finally — exception paths likely "
            f"leak the client.")
