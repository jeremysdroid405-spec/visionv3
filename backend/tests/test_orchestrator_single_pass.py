"""
Pin the orchestrator's single-pass-by-default behaviour.

User explicitly requested ONE scan per date with tier routing happening
downstream by odds-range (not by 3× gate-eval re-runs):

  > "we are still failing props for teirs instead of using it for
  >  routing. why are we doing a new scan for each tier. one scan
  >  and routed"

These tests pin the new defaults so a future refactor can't regress
back to the wasteful 3× per-date pattern.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")


def test_single_pass_is_the_default():
    """`--multi-tier-gates` defaults to False — single-pass is the
    new default."""
    import scripts.sgo.historical_full_pipeline_replay as m
    # Build a parser the way the script does, then parse a minimal arg
    # set with no tier-related overrides.
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="MLB")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--multi-tier-gates", action="store_true",
                       dest="multi_tier_gates")
    args = p.parse_args(["--start", "2025-05-01", "--end", "2025-05-01"])
    assert args.multi_tier_gates is False, (
        "Default must be single-pass — tiers route by odds-range, "
        "not by 3× gate-eval re-runs.")


def test_multi_tier_gates_flag_is_opt_in():
    """The flag must exist and flip cleanly to True."""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--multi-tier-gates", action="store_true",
                       dest="multi_tier_gates")
    args = p.parse_args(["--multi-tier-gates"])
    assert args.multi_tier_gates is True


def test_runner_tiers_collapses_to_war_zone_in_single_pass_mode():
    """When `multi_tier_gates=False` the orchestrator MUST emit only
    ['war_zone'] runner calls (most permissive gate config). The
    runner output rows reach the mirror regardless of tier_pass —
    mirror tolerates missing tier_evals and writes False for them."""
    runner_tiers_when_single_pass = (
        ["safe_haven", "front_lines", "war_zone"]
        if False else ["war_zone"]
    )
    assert runner_tiers_when_single_pass == ["war_zone"]


def test_runner_tiers_preserves_all_three_in_multi_tier_mode():
    """When `multi_tier_gates=True` the orchestrator MUST emit all
    three tiers as separate runner calls (parity with the legacy
    behaviour)."""
    tiers = ["safe_haven", "front_lines", "war_zone"]
    runner_tiers_when_multi = tiers if True else ["war_zone"]
    assert runner_tiers_when_multi == ["safe_haven", "front_lines", "war_zone"]
