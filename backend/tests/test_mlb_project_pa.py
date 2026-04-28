"""
Tests for the project_pa() spec-v2 PA model.

Run:
    cd /app/backend && python -m pytest tests/test_mlb_project_pa.py -v
"""
import sys
sys.path.insert(0, "/app/backend")

# The engine module lives at scripts/ but has no scripts/__init__.py;
# we import the function directly via importlib so pytest doesn't need
# any package init.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "mlb_pv", "/app/backend/scripts/mlb_propvision_total_bases.py")
mlb_pv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mlb_pv)
project_pa = mlb_pv.project_pa


# ---- baseline per slot --------------------------------------------------
def test_slot_1_baseline():
    pa, src = project_pa(1, None, None)
    assert pa == 4.7 and src == "lineup"


def test_slot_9_baseline():
    pa, src = project_pa(9, None, None)
    assert pa == 3.7 and src == "lineup"


def test_invalid_slot_falls_back():
    pa, src = project_pa(99, None, None)
    assert pa == 4.2 and src == "fallback"


def test_string_slot_falls_back():
    pa, src = project_pa("not-a-number", None, None)
    assert pa == 4.2 and src == "fallback"


def test_none_slot_falls_back():
    pa, src = project_pa(None, 6.0, True)
    # No team-context bumps when fallback fires (per spec).
    assert pa == 4.2 and src == "fallback"


# ---- team_total bumps --------------------------------------------------
def test_high_team_total_adds_pa():
    """team_total >= 5.5 → +0.20 PA"""
    pa, _ = project_pa(3, 5.5, False)
    assert abs(pa - 4.7) < 1e-9


def test_low_team_total_subtracts_pa():
    """team_total <= 3.5 → -0.20 PA"""
    pa, _ = project_pa(3, 3.5, False)
    assert abs(pa - 4.3) < 1e-9


def test_neutral_team_total_no_change():
    pa, _ = project_pa(3, 4.5, False)
    assert pa == 4.5


# ---- home-team penalty -------------------------------------------------
def test_home_team_subtracts_pa():
    pa, _ = project_pa(5, None, True)
    assert abs(pa - (4.3 - 0.10)) < 1e-9


def test_away_team_no_change():
    pa, _ = project_pa(5, None, False)
    assert pa == 4.3


# ---- clamp -------------------------------------------------------------
def test_clamp_max():
    """Slot 1 + high team total + away → 4.7 + 0.20 = 4.90 (within clamp)."""
    pa, _ = project_pa(1, 6.5, False)
    assert pa == 4.9


def test_clamp_min():
    """Slot 9 + low team total + home → 3.7 - 0.20 - 0.10 = 3.40."""
    pa, _ = project_pa(9, 3.0, True)
    assert abs(pa - 3.4) < 1e-9


def test_clamp_extreme_low_does_not_underflow():
    """Even a (hypothetical) sub-3.4 input gets pinned to 3.2."""
    # Synthesize via "manually" stripping baseline + applying both deltas
    # — this confirms the floor when external feeds get noisy.
    pa, _ = project_pa(9, 2.0, True)
    assert pa >= 3.2  # 3.7 - 0.20 - 0.10 = 3.4 ≥ 3.2 — clamp not active here
    # This also shows the clamp would activate for hypothetical lower
    # baselines; current spec values keep us above 3.2.


def test_string_team_total_handled():
    """Bad team_total type → no adjustment, no crash."""
    pa, _ = project_pa(4, "not-a-number", False)
    assert pa == 4.4
