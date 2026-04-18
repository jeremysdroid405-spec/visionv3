"""
Canonical stat-window hit-rate contract
=======================================
Regression test that enforces the architecture rule:

    prop["h5_rate"]  == round(l5_hits  / 5  * 100, 1)
    prop["h10_rate"] == round(l10_hits / 10 * 100, 1)
    prop["h20_rate"] == round(l20_hits / 20 * 100, 1)

No Ferrari board endpoint may return a prop whose windowed hit-rate field
disagrees with its paired hits count. Model/scorer-derived values live in
`model_hit_rate_over`, `model_hit_rate_under`, `model_hit_rate_active` and
may legitimately differ (different window / side / smoothing).

Root cause the contract was written for:
    ferrari_tiers.py:1059-1072 clobbered h10_rate with score.hit_rate_over
    (an L20 side-aware rate), producing the "chart 9/10 but tile shows 95%"
    bug on the Grayson Allen OVER 7.5 card observed 2026-04-18.
"""
from __future__ import annotations

import os
import json
import pytest
import requests


def _api_base() -> str:
    # Tests run inside the pod; localhost is canonical for the backend.
    return os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"


def _fetch_board(name: str, sport: str = "nba"):
    url = f"{_api_base()}/api/v3/ferrari/{name}?sport={sport}"
    r = requests.get(url, timeout=30)
    assert r.status_code == 200, f"{url} returned {r.status_code}: {r.text[:200]}"
    payload = r.json()
    return payload.get("picks", []) or []


def _assert_prop_canonical(prop: dict) -> None:
    """Assert every present (hits, rate) pair matches literal chart math."""
    for hits_key, rate_key, window in (
        ("l5_hits",  "h5_rate",  5),
        ("l10_hits", "h10_rate", 10),
        ("l20_hits", "h20_rate", 20),
    ):
        hits = prop.get(hits_key)
        rate = prop.get(rate_key)
        if hits is None or rate is None:
            continue
        canonical = round((float(hits) / float(window)) * 100.0, 1)
        assert abs(float(rate) - canonical) <= 0.5, (
            f"Canonical contract violated: {prop.get('player_name')} "
            f"{prop.get('stat_type')} {prop.get('line')} {prop.get('direction')}: "
            f"{rate_key}={rate} but {hits_key}={hits} → canonical={canonical}"
        )


@pytest.mark.parametrize("board", ["front-lines", "safe-haven", "war-zone"])
def test_board_hit_rate_canonical(board: str) -> None:
    picks = _fetch_board(board, sport="nba")
    # War Zone may legitimately be empty; the other two are not required
    # to be non-empty either, but the contract must hold for any prop present.
    for prop in picks:
        _assert_prop_canonical(prop)


def test_model_hit_rate_fields_are_separate() -> None:
    """model_hit_rate_* must never write back into h*_rate."""
    picks = _fetch_board("front-lines", sport="nba")
    for prop in picks:
        m_active = prop.get("model_hit_rate_active")
        h10 = prop.get("h10_rate")
        l10_hits = prop.get("l10_hits")
        if m_active is None or l10_hits is None or h10 is None:
            continue
        canonical = round((float(l10_hits) / 10.0) * 100.0, 1)
        # h10_rate MUST equal canonical L10 math, even when model diverges.
        assert abs(float(h10) - canonical) <= 0.5


def test_grayson_allen_pts_7_5_regression() -> None:
    """Pinned regression for the exact card that surfaced the bug.

    Historical expectation (when this pick is on the front-lines board):
      - l10_hits == 9   (from bdl_game_logs.pts vs 7.5 over last 10)
      - h10_rate == 90.0
      - model_hit_rate_over == 95.0  (L20 / p_true-derived — diagnostic only)
    """
    picks = _fetch_board("front-lines", sport="nba")
    target = next(
        (p for p in picks
         if p.get("player_name") == "Grayson Allen"
         and p.get("stat_type") == "PTS"
         and p.get("line") == 7.5
         and (p.get("direction") or "").lower() == "over"),
        None,
    )
    if target is None:
        pytest.skip("Grayson Allen PTS 7.5 OVER not on Front Lines board right now")
    _assert_prop_canonical(target)
    # If model-derived fields are populated, they must live under their own
    # namespace, never under h10_rate.
    if target.get("model_hit_rate_over") is not None and target.get("l10_hits") is not None:
        canonical = round(float(target["l10_hits"]) / 10.0 * 100.0, 1)
        assert abs(float(target["h10_rate"]) - canonical) <= 0.5
