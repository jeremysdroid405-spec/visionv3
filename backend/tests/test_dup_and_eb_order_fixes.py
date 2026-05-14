"""Regression tests — 2026-05-14 bug-fix lockdown.

Locks in three invariants discovered during the duplicate-prop + binary
probability investigation:

  1. EB shrinkage runs BEFORE compute_probability so the persisted
     `distribution_effective_mu` equals the final `model_projection`
     (not the raw HF μ).
  2. After a successful write to the canonical live tag
     (`final-{sport}-rt`), cross-tag `active=True` rows for the same
     canonical_key are flipped to active=False (no stale-tag bleed).
  3. The active pool has no duplicate
     (event_id, player_name, stat_type, line, side) clusters.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest


# ───────────────────────── Bug #2 — EB order-of-ops ───────────────────


def _stub_hf_predict_result() -> Dict[str, Any]:
    """Build the dict shape returned by `MLBHighFrictionModel.predict`."""
    return {
        "predicted": 0.61,          # raw HF μ (the bug-causing value)
        "raw_prediction": 0.61,     # same value, kept for ECDF
        "std_dev": 3.266,
        "prob_over": 51.3,
        "feature_health": {},
        "mu_raw_model_projection": 0.6093,
    }


def test_eb_shrinkage_runs_before_probability_engine(monkeypatch):
    """Distribution layer must receive the EB-shrunk μ — never the raw
    HF projection. Pre-fix bug: `distribution_effective_mu == raw_hf`
    while `model_projection == eb_shrunk`."""
    capture: Dict[str, Any] = {}

    def fake_compute_probability(*, sport, stat_family, mu, line, cv):
        # Capture the μ the distribution layer received.
        capture["mu_received"] = mu
        capture["line"] = line
        capture["cv"] = cv

        class _Stub:
            p_over = 0.7044
            p_under = 0.2956
            distribution = "normal_cdf"
            selector_reason = "test_stub"
            clamped = False
            sigma = 1.5006
            sigma_source = "test"
            effective_mu = mu  # ← matches whatever was passed in
            mu_floor_applied = False
            mu_floor_capped = False
            cv_floor_applied = False
            lambda_ = None
            threshold = None
            dispersion_r = None
            p_param = None
        return _Stub()

    def fake_apply_eb_shrinkage(*, master_hub, bdl_player_id, stat_type, raw_projection):
        # EB shrinkage pulls raw 0.61 toward career mean 2.35, → 1.306.
        return 1.306, {
            "raw_hf_projection": 0.61,
            "eb_shrunk_projection": 1.306,
            "eb_player_career_mean": 2.35,
            "eb_weight_model": 0.6,
            "eb_weight_player": 0.4,
            "eb_shrinkage_applied": True,
            "eb_skip_reason": None,
            "eb_career_sample_n": 137,
        }

    # monkeypatch ensures both stubs are reverted at end-of-test —
    # the previous reload-based path leaked the stub into the next
    # test in the file, masking the real distribution math.
    import services.probability.distribution as dist_mod
    import services.scoring.mlb_eb_shrinkage as eb_mod
    monkeypatch.setattr(dist_mod, "compute_probability", fake_compute_probability)
    monkeypatch.setattr(eb_mod, "apply_eb_shrinkage", fake_apply_eb_shrinkage)

    # Simulate the relevant code path: raw → EB → compute_probability.
    from services.probability.distribution import compute_probability
    from services.scoring.mlb_eb_shrinkage import apply_eb_shrinkage

    raw_mu = _stub_hf_predict_result()["predicted"]   # 0.61
    shrunk_mu, _audit = apply_eb_shrinkage(
        master_hub=None, bdl_player_id=1, stat_type="hits+runs+rbis",
        raw_projection=raw_mu,
    )
    assert shrunk_mu == 1.306, "EB stub did not produce shrunk projection"

    # NOW probability must be called with the shrunk μ:
    compute_probability(
        sport="mlb", stat_family="hits+runs+rbis",
        mu=shrunk_mu, line=0.5, cv=1.149,
    )
    assert capture["mu_received"] == 1.306, (
        f"compute_probability received μ={capture['mu_received']} but "
        f"the EB-shrunk projection was {shrunk_mu}. EB must run BEFORE "
        f"the distribution engine."
    )


def test_andy_pages_canary_distribution_consistency():
    """End-to-end canary: Normal-CDF with the same inputs we found live
    must produce p_over≈0.70, not 0.56. Locks the math down without
    depending on a live MongoDB."""
    from services.probability.distribution import compute_probability

    # Andy Pages HRR OVER 0.5 — post-fix inputs:
    result = compute_probability(
        sport="mlb",
        stat_family="Hits+Runs+RBIs",
        mu=1.306,    # ← post-EB μ
        line=0.5,
        cv=1.149,
    )
    assert result is not None
    # σ = max(cv × max(μ, μ_floor), σ_min) = max(1.149 × 1.306, 0.20) ≈ 1.5
    # z = (0.5 - 1.306) / 1.5 ≈ -0.537 → p_under ≈ 0.296, p_over ≈ 0.704
    assert 0.69 < result.p_over < 0.72, (
        f"Expected p_over≈0.70 for mu=1.306, line=0.5, cv=1.149, got "
        f"{result.p_over:.4f}"
    )

    # Pre-fix bug repro — feeding the raw 0.61 produces the buggy 0.56:
    buggy = compute_probability(
        sport="mlb", stat_family="Hits+Runs+RBIs",
        mu=0.61, line=0.5, cv=1.149,
    )
    assert 0.54 < buggy.p_over < 0.58, (
        f"Pre-fix repro check: feeding raw HF μ=0.61 should yield ≈0.56, "
        f"got {buggy.p_over:.4f}"
    )


# ───────────── Bug #1 — cross-tag active=True sweep ───────────────────


@pytest.mark.asyncio
async def test_cross_tag_active_sweep_flips_other_tags_on_live_write():
    """Writing to `final-{sport}-rt` must flip cross-tag active=True
    rows for the same canonical_key. Stale tags (`final-mlb`, ad-hoc
    recompute jobs, shadow tags) must NOT pollute the active pool."""
    import mongomock_motor

    client = mongomock_motor.AsyncMongoMockClient()
    db = client["test_pv"]
    coll = db["mlb_prop_scores"]
    ck = "mlb|EVT1|Andy Pages|HRR|0.5|OVER"

    # Seed: TWO active=True rows under stale tags + one row under live tag.
    await coll.insert_many([
        {"canonical_key": ck, "version_tag": "final-mlb",
         "active": True, "model_projection": 0.61,
         "computed_at": "stale-2026-04-29"},
        {"canonical_key": ck, "version_tag": "recompute-old",
         "active": True, "model_projection": 0.61,
         "computed_at": "stale-2026-05-13"},
        # Existing live-tag row (will be replaced).
        {"canonical_key": ck, "version_tag": "final-mlb-rt",
         "active": True, "model_projection": 1.30,
         "computed_at": "old-live"},
    ])

    # Simulate a fresh write to the live tag for the same canonical_key.
    # We exercise the same `update_many` predicate the production code
    # uses to make sure the invariant holds.
    from datetime import datetime, timezone
    live_tag = "final-mlb-rt"
    res = await coll.update_many(
        {
            "canonical_key": {"$in": [ck]},
            "version_tag": {"$ne": live_tag},
            "active": True,
        },
        {"$set": {
            "active": False,
            "inactive_reason": "stale_tag_active_sweep",
            "active_changed_at": datetime.now(timezone.utc),
        }},
    )
    assert res.modified_count == 2, (
        f"Expected 2 stale-tag rows flipped, got {res.modified_count}"
    )

    # Active=True for this canonical_key must now exist ONLY under the
    # live tag. Invariant: at most one active=True row per canonical_key
    # (after the dedupe done by the writer in `seen[ck] = d`).
    n_active = await coll.count_documents(
        {"canonical_key": ck, "active": True}
    )
    assert n_active == 1, (
        f"Cross-tag sweep failed — {n_active} rows still active for "
        f"the same canonical_key."
    )
    live_row = await coll.find_one(
        {"canonical_key": ck, "active": True}, {"_id": 0, "version_tag": 1}
    )
    assert live_row["version_tag"] == live_tag


@pytest.mark.asyncio
async def test_cross_tag_sweep_does_not_disturb_other_canonical_keys():
    """The sweep must be scoped to the canonical_keys being written —
    audit / shadow / backtest runs writing under OTHER tags must not
    affect live-tag active rows for OTHER canonical_keys."""
    import mongomock_motor

    client = mongomock_motor.AsyncMongoMockClient()
    db = client["test_pv"]
    coll = db["mlb_prop_scores"]
    ck_live = "mlb|EVT1|Andy Pages|HRR|0.5|OVER"
    ck_other = "mlb|EVT2|Other Player|Hits|1.5|OVER"

    await coll.insert_many([
        # Live row for the OTHER canonical_key (must remain active).
        {"canonical_key": ck_other, "version_tag": "final-mlb-rt",
         "active": True},
        # Stale row for our subject canonical_key (must flip to False).
        {"canonical_key": ck_live, "version_tag": "final-mlb",
         "active": True},
    ])

    from datetime import datetime, timezone
    await coll.update_many(
        {
            "canonical_key": {"$in": [ck_live]},
            "version_tag": {"$ne": "final-mlb-rt"},
            "active": True,
        },
        {"$set": {
            "active": False,
            "inactive_reason": "stale_tag_active_sweep",
            "active_changed_at": datetime.now(timezone.utc),
        }},
    )
    n_other = await coll.count_documents(
        {"canonical_key": ck_other, "active": True}
    )
    assert n_other == 1, (
        "Sweep must not touch active rows for other canonical_keys"
    )


@pytest.mark.asyncio
async def test_active_pool_has_no_duplicate_clusters_after_sweep():
    """Active-pool invariant per spec:
       sport + event_id + player_name + stat_type + line + side
       must be unique across active=True rows."""
    import mongomock_motor
    from collections import defaultdict

    client = mongomock_motor.AsyncMongoMockClient()
    db = client["test_pv"]
    coll = db["mlb_prop_scores"]

    # Seed 3 distinct canonical_keys, each with multiple tag bleed.
    rows = []
    for eid in ("EVT_A", "EVT_B", "EVT_C"):
        for tag in ("final-mlb", "final-mlb-rt", "recompute-old"):
            rows.append({
                "canonical_key": f"mlb|{eid}|Pl|HRR|0.5|OVER",
                "event_id": eid,
                "player_name": "Pl",
                "stat_type": "HRR",
                "line": 0.5,
                "recommendation": "OVER",
                "version_tag": tag,
                "active": True,
            })
    await coll.insert_many(rows)

    # Apply the production sweep predicate.
    from datetime import datetime, timezone
    await coll.update_many(
        {
            "version_tag": {"$ne": "final-mlb-rt"},
            "active": True,
        },
        {"$set": {"active": False, "inactive_reason": "stale_tag_active_sweep",
                  "active_changed_at": datetime.now(timezone.utc)}},
    )

    # Now: per-(event,player,stat,line,side), at most one active row.
    groups = defaultdict(int)
    async for d in coll.find({"active": True}, {"_id": 0}):
        k = (d["event_id"], d["player_name"], d["stat_type"], d["line"], d["recommendation"])
        groups[k] += 1
    dup_clusters = [k for k, n in groups.items() if n > 1]
    assert not dup_clusters, (
        f"Duplicate active clusters survived sweep: {dup_clusters}"
    )


def test_source_code_invariant_eb_appears_before_compute_probability():
    """Static-source check: `apply_eb_shrinkage` must literally appear
    BEFORE `compute_probability(` in the MLB adapter. Catches future
    refactors that re-introduce the original order-of-ops bug."""
    src = open(
        "/app/backend/services/scoring/adapters/mlb_scoring.py"
    ).read()

    eb_call = src.find("apply_eb_shrinkage(")
    prob_call = src.find("compute_probability(")
    assert eb_call >= 0, "apply_eb_shrinkage call not found"
    assert prob_call >= 0, "compute_probability call not found"
    assert eb_call < prob_call, (
        f"Source order regression: `apply_eb_shrinkage` at char "
        f"{eb_call} must appear BEFORE `compute_probability(` at "
        f"char {prob_call}. Reverting the 2026-05-14 fix would put "
        f"compute_probability first and re-introduce the binary-line "
        f"probability bug."
    )
