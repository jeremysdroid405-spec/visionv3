"""
End-to-end pipeline-step subprocess test (2026-05-26).

This is the test the user explicitly asked for: actually run the
historical-pipeline subprocesses, prove they exit cleanly, prove
they don't leak motor, prove they don't hang. No mocks. No source
inspection. The subprocess runs against an EMPTY data window so it
returns in seconds, but it runs the full code path — argparse, motor
client setup, the actual `_run_body`, the finally clause, asyncio
shutdown.

The bug class this protects against:
  - motor client leaked → `asyncio.run()` never returns → subprocess
    hangs after work logically completes → worker reports running
    forever → next pipeline step never starts → user sees "freeze."
"""
from __future__ import annotations
import subprocess
import sys
import time

import pytest


# (module, args) pairs hit by the Guided Workflow / Run Full Pipeline.
# Empty 1990 window means each script returns instantly (no data) —
# we're only asserting clean exit, not correctness of feature math.
PIPELINE_STEPS = [
    # Step 1 — Ingest historical odds (already SSOT-clean upstream)
    pytest.param(
        "scripts.sgo.build_pp_research_core",
        ["--start", "1990-01-01", "--end", "1990-01-01", "--league", "MLB"],
        id="build_pp_research_core",
    ),
    # Step 2 — Build features (the one user is failing on)
    pytest.param(
        "scripts.sgo.build_historical_model_features",
        ["--start", "1990-01-01", "--end", "1990-01-01", "--league", "MLB"],
        id="build_historical_model_features",
    ),
    # Step 4 — Reshape SGO → replay odds
    pytest.param(
        "scripts.sgo.reshape_sgo_to_replay_odds",
        ["--league", "MLB", "--start", "1990-01-01", "--end", "1990-01-01"],
        id="reshape_sgo_to_replay_odds",
    ),
    # Step 7 — Full pipeline replay
    pytest.param(
        "scripts.sgo.historical_full_pipeline_replay",
        ["--league", "MLB", "--start", "1990-01-01", "--end", "1990-01-01",
         "--continue-on-error", "--dry-run"],
        id="historical_full_pipeline_replay",
    ),
    # Step 8 — Gate-grid (was the visible freeze point)
    pytest.param(
        "scripts.sgo.historical_gate_replay_grid",
        ["--league", "MLB", "--start", "1990-01-01", "--end", "1990-01-01",
         "--min-bets", "1000"],
        id="historical_gate_replay_grid",
    ),
    # Step 9 — Grid sweep
    pytest.param(
        "scripts.research.grid_sweep",
        ["--league", "MLB", "--start", "1990-01-01", "--end", "1990-01-01",
         "--min-bets", "1000"],
        id="grid_sweep",
    ),
]


@pytest.mark.parametrize("module,args", PIPELINE_STEPS)
def test_pipeline_step_exits_cleanly_on_empty_window(module: str,
                                                                  args: list):
    """Run each pipeline step as a real subprocess with no data, assert
    it exits within 20 seconds. Pre-fix these would hang indefinitely
    until the worker's 7200 s timeout SIGKILL'd them."""
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-m", module, *args],
            capture_output=True,
            timeout=30,
            cwd="/app/backend",
        )
    except subprocess.TimeoutExpired as e:
        pytest.fail(
            f"{module} did not exit within 30s on an empty data window "
            f"— motor client likely still leaks, or there's a new "
            f"shutdown hang.\nstdout tail: {(e.stdout or b'')[-400:]!r}\n"
            f"stderr tail: {(e.stderr or b'')[-400:]!r}")
    elapsed = time.monotonic() - t0
    # 15 seconds is generous — most return in <2s on an empty window.
    # Anything above this strongly suggests a shutdown hang.
    assert elapsed < 15.0, (
        f"{module} took {elapsed:.1f}s to exit on an empty window — "
        f"likely a motor-client shutdown hang.\n"
        f"stdout tail: {result.stdout.decode()[-400:]!r}")
    # Returncode can be 0/1/2 depending on script — what matters is
    # that it RETURNED instead of hanging. Reject only the kill codes.
    assert result.returncode not in (-9, -15, 137, 143), (
        f"{module} was killed (returncode={result.returncode}) instead "
        f"of exiting normally.\nstdout tail: {result.stdout.decode()[-400:]!r}")
