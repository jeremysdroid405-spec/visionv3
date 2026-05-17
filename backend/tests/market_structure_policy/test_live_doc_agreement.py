"""Live-doc agreement regression test.

Reads real `nba_prop_scores` and `mlb_prop_scores` documents (today's
production output) and asserts that `decide_one_sided(...)` returns
a verdict CONSISTENT with what the live engine actually recorded on
each doc.

Consistency contract:
  • For one_sided props that are STILL on the active board with
    `gate_pass=True`: the policy MUST agree (passes both gates).
  • For one_sided props with `failed_gates` containing
    "market_structure_gate" (NBA) or "tp_source_gate" (MLB): the
    policy MUST predict a rejection on the same axis.
  • For one_sided props that passed via the elite-binary override:
    the policy MUST flag `via_elite_override=True`.

Skipping behavior:
  Test skips (not fails) when no matching live docs exist — the
  CI can't fail because of empty production data, but when data is
  present the assertions are strict.

Marked `pytest.mark.integration` so it can be selected separately:
    pytest -m integration tests/market_structure_policy/
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, "/app/backend")

import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.scoring.market_structure_policy import (
    decide_one_sided, policy_for,
)


# Lazy import — only when test actually runs.
@pytest.fixture(scope="module")
def db():
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=4000)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB not reachable")
    return client[os.environ["DB_NAME"]]


def _metrics_from_score_doc(doc):
    """Map a live score-doc into the dict shape `decide_one_sided`
    expects. Names mirror NormalizedMetrics."""
    return {
        "tp_source":     doc.get("tp_source"),
        "is_alt":        bool(
            doc.get("is_alternate_market") or doc.get("is_alt") or False
        ),
        "stat_family":   doc.get("stat_family"),
        "hit_rate_l20":  doc.get("hit_rate_l20") or doc.get("hit_rate"),
        "hit_rate_l5":   doc.get("hit_rate_l5"),
        "edge_pct":      doc.get("edge_pct") or doc.get("edge"),
        "cv":            doc.get("cv"),
    }


def test_nba_sh_live_doc_agreement(db):
    """Every live one_sided NBA SH doc must agree with policy."""
    sport, tier = "nba", "safe_haven"
    docs = list(db.nba_prop_scores.find(
        {"tier": tier, "tp_source": "one_sided"},
        {"_id": 0, "tp_source": 1, "is_alternate_market": 1,
         "is_alt": 1, "failed_gates": 1, "gate_pass": 1,
         "stat_family": 1, "hit_rate_l20": 1, "hit_rate_l5": 1,
         "hit_rate": 1, "edge_pct": 1, "edge": 1, "cv": 1,
         "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "rejection_reason": 1},
    ))
    if not docs:
        pytest.skip("No one_sided NBA SH docs in live cohort")

    pol = policy_for(sport, tier)
    disagreements = []
    for d in docs:
        metrics = _metrics_from_score_doc(d)
        verdict = decide_one_sided(metrics, pol)
        failed = set(d.get("failed_gates") or [])
        actually_rejected_by_ms = (
            "market_structure_gate" in failed
            or d.get("rejection_reason") == "market_structure_gate"
        )
        is_alt = metrics["is_alt"]

        # NBA SH contract: ONLY alts get rejected.
        if is_alt:
            # Policy must predict alt rejection.
            if verdict.passes_market_structure is not False:
                disagreements.append(
                    (d.get("player_name"), d.get("stat_type"), is_alt,
                     "policy says alt passes but engine should reject")
                )
        else:
            # Standard-line one-sided NBA SH: policy must predict pass.
            if verdict.passes_market_structure is not True:
                disagreements.append(
                    (d.get("player_name"), d.get("stat_type"), is_alt,
                     "policy rejected standard-line; engine wouldn't")
                )
            if actually_rejected_by_ms:
                disagreements.append(
                    (d.get("player_name"), d.get("stat_type"), is_alt,
                     "engine rejected standard-line via "
                     "market_structure_gate — unexpected")
                )
    assert not disagreements, (
        f"NBA SH live-doc disagreements: {disagreements[:5]} "
        f"(of {len(disagreements)})"
    )


def test_mlb_sh_live_doc_agreement(db):
    """Every live one_sided MLB SH doc must agree with policy."""
    sport, tier = "mlb", "safe_haven"
    docs = list(db.mlb_prop_scores.find(
        {"tier": tier, "tp_source": "one_sided"},
        {"_id": 0, "tp_source": 1, "is_alternate_market": 1,
         "is_alt": 1, "failed_gates": 1, "gate_pass": 1,
         "stat_family": 1, "hit_rate_l20": 1, "hit_rate_l5": 1,
         "hit_rate": 1, "edge_pct": 1, "edge": 1, "cv": 1,
         "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "rejection_reason": 1, "gate_details": 1},
    ))
    if not docs:
        pytest.skip("No one_sided MLB SH docs in live cohort")

    pol = policy_for(sport, tier)
    disagreements = []
    for d in docs:
        metrics = _metrics_from_score_doc(d)
        verdict = decide_one_sided(metrics, pol)
        failed = set(d.get("failed_gates") or [])
        actually_rejected_by_tp_source = (
            "tp_source_gate" in failed
            or d.get("rejection_reason") == "tp_source_gate"
        )
        actually_rejected_by_ms = (
            "market_structure_gate" in failed
            or d.get("rejection_reason") == "market_structure_gate"
        )

        # MLB SH contract: all one_sided rejected (alt via the
        # tp_source_gate too, since required_source=devig), unless
        # elite override fires for standard-line on allowed families.
        if not verdict.passes_market_structure or not verdict.passes_tp_source:
            # Policy says reject.
            #   • alt → ought to be caught either way (the engine has
            #     ONLY tp_source_gate; MLB SH does not have a separate
            #     market_structure_gate). So we expect tp_source_gate
            #     failure regardless of is_alt.
            #   • standard non-override → tp_source_gate failure.
            if not actually_rejected_by_tp_source and d.get("gate_pass"):
                disagreements.append(
                    (d.get("player_name"), d.get("stat_type"),
                     metrics["is_alt"],
                     "policy rejects but engine passed")
                )
        else:
            # Policy says pass → must be elite override OR live gate
            # let it through. Check the override conditions hold.
            if verdict.via_elite_override:
                # Sanity: engine doc shouldn't have tp_source_gate in
                # failed_gates for these.
                if "tp_source_gate" in failed:
                    disagreements.append(
                        (d.get("player_name"), d.get("stat_type"),
                         metrics["is_alt"],
                         "policy: override passes; engine recorded "
                         "tp_source_gate failure")
                    )

    assert not disagreements, (
        f"MLB SH live-doc disagreements: {disagreements[:5]} "
        f"(of {len(disagreements)})"
    )


def test_nba_fl_wz_live_doc_no_one_sided_rejection_by_market_structure(db):
    """NBA FL / WZ today don't have `market_structure_gate`. Any
    one_sided pick in those tiers must NOT be rejected by it on
    the live doc, and the policy must agree."""
    for tier in ("front_lines", "war_zone"):
        docs = list(db.nba_prop_scores.find(
            {"tier": tier, "tp_source": "one_sided"},
            {"_id": 0, "failed_gates": 1, "rejection_reason": 1,
             "player_name": 1, "stat_type": 1, "tp_source": 1,
             "is_alternate_market": 1, "is_alt": 1,
             "stat_family": 1, "hit_rate_l20": 1, "hit_rate_l5": 1,
             "edge_pct": 1, "cv": 1},
        ))
        if not docs:
            continue
        pol = policy_for("nba", tier)
        for d in docs:
            failed = set(d.get("failed_gates") or [])
            assert "market_structure_gate" not in failed, (
                f"NBA {tier} doc rejected by market_structure_gate; "
                f"shouldn't be present in cfg"
            )
            verdict = decide_one_sided(_metrics_from_score_doc(d), pol)
            assert verdict.passes_market_structure is True
            assert verdict.passes_tp_source is True


def test_mlb_fl_wz_live_doc_no_one_sided_rejection_by_tp_source(db):
    """MLB FL / WZ today don't have `tp_source_gate`. Same shape."""
    for tier in ("front_lines", "war_zone"):
        docs = list(db.mlb_prop_scores.find(
            {"tier": tier, "tp_source": "one_sided"},
            {"_id": 0, "failed_gates": 1, "rejection_reason": 1,
             "player_name": 1, "stat_type": 1, "tp_source": 1,
             "is_alternate_market": 1, "is_alt": 1,
             "stat_family": 1, "hit_rate_l20": 1, "hit_rate_l5": 1,
             "edge_pct": 1, "cv": 1},
        ))
        if not docs:
            continue
        pol = policy_for("mlb", tier)
        for d in docs:
            failed = set(d.get("failed_gates") or [])
            assert "tp_source_gate" not in failed, (
                f"MLB {tier} doc rejected by tp_source_gate; "
                f"shouldn't be present in cfg"
            )
            verdict = decide_one_sided(_metrics_from_score_doc(d), pol)
            assert verdict.passes_market_structure is True
            assert verdict.passes_tp_source is True
