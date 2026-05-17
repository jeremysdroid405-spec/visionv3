"""Single-thread inference guard tests.

Locks in the `MLB_HF_ALLOW_MULTITHREAD` opt-out semantics added in
2026-05-17 to prevent OpenMP/multiprocessing-fork OOM kills.
"""
import json
import os
import pytest


# A small but representative subset to keep the test fast.
SAMPLE_STATS = ("total_bases", "hits", "pitcher_strikeouts",
                "home_runs", "earned_runs")


def _booster_nthread(model, stat):
    """Introspect the XGBoost booster's saved config and pull nthread."""
    mdl = model.models.get(stat)
    if mdl is None:
        return None
    booster = mdl.get_booster()
    cfg = json.loads(booster.save_config())
    gp = cfg.get("learner", {}).get("generic_param", {})
    return gp.get("nthread") or gp.get("n_jobs")


def test_default_loaded_boosters_use_nthread_1(model):
    """After load_models() default behaviour every booster must report
    nthread=1. The guard runs unless MLB_HF_ALLOW_MULTITHREAD=1."""
    if os.environ.get("MLB_HF_ALLOW_MULTITHREAD") == "1":
        pytest.skip("guard intentionally disabled via env override")
    checked = 0
    for stat in SAMPLE_STATS:
        if stat not in model.models:
            continue
        n = _booster_nthread(model, stat)
        assert str(n) == "1", (
            f"{stat}: booster nthread={n!r} — single-thread guard failed"
        )
        checked += 1
    assert checked >= 3, f"only {checked} models available for check"


def test_sklearn_wrapper_n_jobs_is_1(model):
    """The sklearn-style `set_params(n_jobs=1)` also took effect."""
    if os.environ.get("MLB_HF_ALLOW_MULTITHREAD") == "1":
        pytest.skip("guard intentionally disabled via env override")
    for stat in SAMPLE_STATS:
        mdl = model.models.get(stat)
        if mdl is None:
            continue
        try:
            params = mdl.get_params()
        except Exception:
            continue
        n = params.get("n_jobs")
        # n_jobs may be None on older xgboost; nthread on booster is the
        # authoritative check (above). When set, must be 1.
        if n is not None:
            assert n == 1, f"{stat}: sklearn n_jobs={n}"


def test_no_orphan_workers_after_predict(model):
    """A predict() call must not leak child processes."""
    import psutil
    me = psutil.Process()
    children_before = len(me.children(recursive=True))
    r = model.predict(
        player_name="Matt Olson", stat_type="total_bases", line=1.5,
        opponent_team="Seattle Mariners", park_team="Atlanta Braves",
        batter_hand="L", opp_pitcher_throws="R",
        as_of_date="2026-05-06",
    )
    assert r.get("predicted") is not None
    children_after = len(me.children(recursive=True))
    leaked = children_after - children_before
    assert leaked == 0, (
        f"{leaked} child workers leaked from predict(): "
        f"before={children_before} after={children_after}"
    )
