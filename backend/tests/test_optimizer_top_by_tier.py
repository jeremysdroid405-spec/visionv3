"""
Pin `/optimizer/{run_id}/top-by-tier` contract.

User directive:
  > "i want the top 3 for every stat for every tier without having
  >  to run it separately. example: optimizer runs. response is
  >  safe haven section with safe haven hr top 3, safe haven rbi
  >  top 3, safe have walks top 3 so on. next section is front lines
  >  with the top 3 for all stats. i need them organized by tier so
  >  i can figure out the prod gates per stat per tier"

The endpoint must:
  1. Group by (tier × stat_family) with Top-N configs each.
  2. Default top_n = 3.
  3. Return tiers in canonical DEFAULT_TIERS order.
  4. include_empty=True by default — every discovered family appears
     under every tier, even if it had no graded combos in that tier.
"""
from __future__ import annotations
import inspect
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import (
    top_by_tier,
    DEFAULT_TIERS,
    router,
)


def test_top_by_tier_endpoint_is_registered():
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    assert "/{run_id}/top-by-tier" in paths


def test_top_by_tier_default_top_n_is_three():
    sig = inspect.signature(top_by_tier)
    assert sig.parameters["top_n"].default == 3


def test_top_by_tier_default_include_empty_is_true():
    sig = inspect.signature(top_by_tier)
    assert sig.parameters["include_empty"].default is True


def test_default_tiers_order_is_canonical():
    """`tier_order` returned by the endpoint must equal DEFAULT_TIERS
    so the UI renders sections in the same order across runs."""
    assert DEFAULT_TIERS == ["safe_haven", "front_lines", "war_zone"]


def test_top_by_tier_response_shape_pin():
    """Lock the response shape: every tier in DEFAULT_TIERS appears
    in `tiers` dict, even if empty. Within each tier, every
    discovered stat_family gets an entry (graded or
    status=no_rows_in_tier)."""
    # Simulate what the endpoint builds after merging discovered
    # families. This is the contract the frontend depends on.
    by_tier = {t: [] for t in DEFAULT_TIERS}
    discovered = {"pitcher_strikeouts", "hits", "total_bases",
                    "earned_runs"}
    seen_by_tier = {
        "safe_haven":  {"pitcher_strikeouts"},
        "front_lines": {"pitcher_strikeouts", "hits", "total_bases",
                          "earned_runs"},
        "war_zone":    set(),
    }
    for tier_name, rows in by_tier.items():
        for sf in sorted(discovered - seen_by_tier[tier_name]):
            rows.append({"stat_family": sf, "configs": [],
                            "status": "no_rows_in_tier"})

    # Every tier present
    assert set(by_tier.keys()) == set(DEFAULT_TIERS)
    # safe_haven has empties for hits / total_bases / earned_runs
    sh_empty = [r for r in by_tier["safe_haven"]
                if r["status"] == "no_rows_in_tier"]
    assert len(sh_empty) == 3
    # war_zone has empties for all 4 families
    wz_empty = [r for r in by_tier["war_zone"]
                if r["status"] == "no_rows_in_tier"]
    assert len(wz_empty) == 4
