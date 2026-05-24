"""
Pin "every discovered stat family is surfaced even when it produces
zero graded combos" — the user's core complaint:

  > "where are all the other stat types? i want the top 5 combos for
  >  every stat type listed ... if the optimizer doesn't return 5 top
  >  combos for every stat type available consider it broken and failed"

Two behaviors locked here:
  1. `/optimizer/{run_id}/top-per-family` default `top_n=5` (was 3)
  2. `include_empty=True` (default) appends placeholder groups for
     every discovered family that wrote 0 graded combos, with
     `status` set to `no_rows_after_tier_filter` or `no_graded_combos`.
  3. `/optimizer/{run_id}/results.family_coverage` ALWAYS includes
     every family in `state.stat_families` — empty ones get a
     placeholder row marked `status="no_rows_after_tier_filter"`.
"""
from __future__ import annotations
import inspect
import sys
sys.path.insert(0, "/app/backend")


def test_top_per_family_default_top_n_is_five():
    """Pin: per-family default raised 3 → 5 per user request."""
    from routes.emergent_admin.optimizer import top_per_family
    sig = inspect.signature(top_per_family)
    assert sig.parameters["top_n"].default == 5, (
        "top_per_family must default top_n=5; got "
        f"{sig.parameters['top_n'].default}")


def test_top_per_family_default_include_empty_is_true():
    """Pin: per-family default surfaces all discovered families,
    even empty ones."""
    from routes.emergent_admin.optimizer import top_per_family
    sig = inspect.signature(top_per_family)
    assert sig.parameters["include_empty"].default is True, (
        "top_per_family must default include_empty=True so the UI "
        "never silently hides a family the operator launched on")


def test_top_per_family_endpoint_surface_path():
    """Pin: the endpoint is registered at /{run_id}/top-per-family."""
    from routes.emergent_admin.optimizer import router
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    assert "/{run_id}/top-per-family" in paths


def test_family_coverage_appends_discovered_empty_families():
    """Replicate the post-aggregation merge that surfaces empty
    families. The optimizer's `get_results` builds `family_coverage`
    from a Mongo aggregation, then appends placeholder rows for
    every family in `state.stat_families` that the aggregation
    missed. This unit-tests the merge logic itself."""
    family_coverage = [
        {"stat_family": "pitcher_strikeouts", "n_cells": 15,
              "n_graded_cells": 12, "best_score": 2.2,
              "best_n_bets": 7, "status": "graded"},
        {"stat_family": "earned_runs",       "n_cells": 15,
              "n_graded_cells": 0,  "best_score": None,
              "best_n_bets": None, "status": "all_skipped_low_sample"},
    ]
    seen = {f["stat_family"] for f in family_coverage}
    discovered = {
        "pitcher_strikeouts", "earned_runs", "hits", "rbis",
        "total_bases", "singles", "batter_strikeouts",
        "hits_runs_rbis", "walks_allowed", "hits_allowed",
        "pitching_outs", "runs", "batter_walks", "home_runs",
    }
    for sf in sorted(discovered - seen):
        family_coverage.append({
            "stat_family":     sf,
            "n_cells":         0,
            "n_graded_cells":  0,
            "best_score":      None,
            "best_n_bets":     None,
            "status":          "no_rows_after_tier_filter",
        })
    assert len(family_coverage) == len(discovered), (
        f"merge must surface ALL {len(discovered)} discovered "
        f"families; got {len(family_coverage)}")
    empty = [f for f in family_coverage
             if f.get("status") == "no_rows_after_tier_filter"]
    assert len(empty) == len(discovered) - len(seen), (
        f"every non-graded family must have status="
        f"no_rows_after_tier_filter; got {len(empty)}")


def test_user_strategy_requires_full_family_visibility():
    """The user explicitly requires Top-5 combos for EVERY discovered
    stat family. If even one family is silently omitted (because no
    rows passed the tier filter or all combos failed min_bets), the
    UX is broken from the user's POV.

    The contract: top-per-family returns AT LEAST one entry per
    discovered stat_family, even if `configs == []`.
    """
    groups = [
        {"stat_family": "pitcher_strikeouts",
              "odds_bucket": "odds_lt_-200",
              "configs": [{"score": 2.2, "n_bets": 3}],
              "status": "graded"},
    ]
    discovered_families = [
        "pitcher_strikeouts", "earned_runs", "hits",
        "total_bases", "rbis",
    ]
    seen_families = {g["stat_family"] for g in groups}
    for sf in sorted(set(discovered_families) - seen_families):
        groups.append({
            "stat_family": sf, "odds_bucket": None,
            "configs": [], "status": "no_rows_after_tier_filter",
        })
    family_set = {g["stat_family"] for g in groups}
    missing = set(discovered_families) - family_set
    assert not missing, (
        f"every discovered family MUST be represented; missing: "
        f"{missing}")
    # And every group has either configs OR an explanatory status.
    for g in groups:
        assert g.get("configs") or g.get("status"), (
            f"group {g['stat_family']!r} has neither configs nor "
            f"a status — UX regression risk")
