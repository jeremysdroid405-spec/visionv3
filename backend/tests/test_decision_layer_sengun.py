"""
Decision-layer integrity regressions — Sengun AST 6.5 OVER
==========================================================
Covers the five decision-layer fixes shipped 2026-04-19 after the Sengun
card audit:

  1. floor_lock uses newest-L10 AND requires hit_rate >= 90 (tooltip honesty)
  2. vision_insight quotes the canonical VK projection, not Lasso
  3. vision_insight flags model disagreement when VK and Lasso pick
     opposite sides of the line with >10% gap
  4. PP-anchor direction veto vetoes a side when vk_edge<0, hit_rate_over<50,
     and l10_avg<line all converge against an OVER (symmetric UNDER)
  5. high_fidelity_model badge copy reworked (frontend-only; not tested here)
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, "/app/backend")


def _calc():
    """Build an IntelSuiteCalculator with a mocked db (no collections touched)."""
    from services.intel_suite_calculator import IntelSuiteCalculator
    db = MagicMock()
    return IntelSuiteCalculator(db=db)


# ---------------------------------------------------------------------------
# Fix #1: floor_lock window + tooltip honesty
# ---------------------------------------------------------------------------
def test_scout_badges_use_newest_l10_and_require_hit_rate_90():
    # Sengun's actual newest-L10 AST values (50% hit rate, std 2.13).
    newest_l10 = [8, 4, 6, 7, 5, 4, 10, 7, 7, 3]
    logs = [{"ast": v, "min": "30", "pts": 20} for v in newest_l10]
    badges = _calc()._generate_scout_badges(
        active_logs=logs, stat_type="AST", line=6.5,
        board_pick={}, lasso_result=None,
    )
    assert "floor_lock" not in badges, (
        f"floor_lock must NOT fire at 50% hit rate (got badges: {badges})"
    )


def test_scout_badges_floor_lock_fires_only_at_90_pct_hit_rate():
    # 9/10 over line 6.5 = 90%.
    hot_l10 = [7, 8, 7, 9, 10, 8, 7, 8, 9, 4]
    logs = [{"ast": v, "min": "30", "pts": 20} for v in hot_l10]
    badges = _calc()._generate_scout_badges(
        active_logs=logs, stat_type="AST", line=6.5,
        board_pick={}, lasso_result=None,
    )
    assert "floor_lock" in badges


# ---------------------------------------------------------------------------
# Fix #2 + #3: canonical projection + narrative disagreement
# ---------------------------------------------------------------------------
def test_vision_insight_quotes_canonical_vk_and_flags_disagreement():
    calc = _calc()
    board_pick = {"vk_predicted": 6.3, "team": "HOU", "opponent": "LAL"}
    lasso_result = {"projection": 7.383, "confidence_tier": "HIGH_FIDELITY"}

    async def _run():
        return await calc._generate_vision_insight(
            "Alperen Sengun", "AST", 6.5, "Over",
            usage_ripple={}, matchup_dvp={}, pace_delta={},
            stability_index={}, board_pick=board_pick, lasso_result=lasso_result,
            use_llm=False,
        )

    vi = asyncio.run(_run())
    assert vi["canonical_projection"] == 6.3
    assert vi["lasso_projection"] == 7.383
    assert vi["models_disagree"] is True
    assert "6.3" in vi["summary"]
    assert "7.4" in vi["summary"]
    assert "disagreement" in vi["summary"].lower()


def test_vision_insight_agreement_no_disagreement_flag():
    calc = _calc()
    board_pick = {"vk_predicted": 22.0, "team": "BOS"}
    lasso_result = {"projection": 21.5, "confidence_tier": "HIGH_FIDELITY"}

    async def _run():
        return await calc._generate_vision_insight(
            "X Player", "PTS", 20.5, "Over",
            usage_ripple={}, matchup_dvp={}, pace_delta={},
            stability_index={}, board_pick=board_pick, lasso_result=lasso_result,
            use_llm=False,
        )

    vi = asyncio.run(_run())
    assert vi["models_disagree"] is False
    assert "disagreement" not in vi["summary"].lower()


# ---------------------------------------------------------------------------
# Fix #4: direction veto
# ---------------------------------------------------------------------------
def test_pp_anchor_veto_fires_on_sengun_profile():
    from services.scoring.scoring_stack import _model_contradicts_anchor

    sengun = {
        "line": 6.5, "recommendation": "OVER",
        "vk_edge": -0.2, "model_projection": 6.32,
        "hit_rate_over": 45.0, "hit_rate_under": 55.0, "l10_avg": 6.1,
    }
    reason = _model_contradicts_anchor(sengun, "OVER")
    assert reason is not None
    assert "vk_edge" in reason and "hit_rate_over" in reason and "l10_avg" in reason


def test_pp_anchor_veto_leaves_clean_over_alone():
    from services.scoring.scoring_stack import _model_contradicts_anchor

    clean = {
        "line": 20.5, "recommendation": "OVER",
        "vk_edge": +1.5, "hit_rate_over": 70, "l10_avg": 22.0,
    }
    assert _model_contradicts_anchor(clean, "OVER") is None


def test_pp_anchor_veto_symmetric_under():
    from services.scoring.scoring_stack import _model_contradicts_anchor

    # UNDER pick where evidence favors OVER should veto.
    contradicted_under = {
        "line": 6.5, "recommendation": "UNDER",
        "vk_edge": +0.4, "hit_rate_under": 40.0, "l10_avg": 7.2,
    }
    reason = _model_contradicts_anchor(contradicted_under, "UNDER")
    assert reason is not None

    # UNDER where evidence truly favors UNDER — not vetoed.
    clean_under = {
        "line": 6.5, "recommendation": "UNDER",
        "vk_edge": -0.4, "hit_rate_under": 65.0, "l10_avg": 5.9,
    }
    assert _model_contradicts_anchor(clean_under, "UNDER") is None
