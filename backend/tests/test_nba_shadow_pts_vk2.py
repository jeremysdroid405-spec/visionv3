"""Unit tests for the shadow VK2 PTS projection layer.

Covers:
  - PTS only — REB/AST/PRA/etc. are skipped silently.
  - Shadow ALWAYS returns mu_current unchanged.
  - Audit fields stamped when VK2 PTS predicts cleanly.
  - mu_pts_vk2_applied = False (must never be True).
"""
import pytest
from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def _logs(rows):
    return [
        {"date": d, "min": m, "pts": p, "reb": r, "ast": a,
         "fg3m": tm, "fga": fga, "fg3a": fg3a}
        for (d, m, p, r, a, tm, fga, fg3a) in rows
    ]


def _adapter_with_logs(pid, logs):
    a = NBAScoringAdapter()
    # Real VK2 model load (uses the .pkl files on disk).
    a._load_vk2_models()
    a._logs_by_id = {pid: logs}
    a._logs_loaded = True
    a._vk2_adv_map = {}
    a._vk2_adv_loaded = True
    return a


def test_shadow_pts_vk2_only_fires_for_pts():
    logs = _logs([(f"2026-04-{15+i:02d}", 30, 25, 5, 5, 2, 18, 6) for i in range(10)])
    a = _adapter_with_logs(1, logs)
    for stat in ["REB", "AST", "PRA", "3PM"]:
        prop = {}
        out = a._maybe_apply_shadow_pts_vk2(stat, 1, prop, 5.0)
        assert out == 5.0
        assert prop["mu_pts_vk2_applied"] is False
        assert "mu_pts_vk2" not in prop, f"{stat} must not stamp mu_pts_vk2"


def test_shadow_pts_vk2_returns_mu_current_unchanged():
    """Critical invariant — production μ must NEVER be replaced."""
    logs = _logs([(f"2026-04-{15+i:02d}", 30, 25, 5, 5, 2, 18, 6) for i in range(10)])
    a = _adapter_with_logs(2, logs)
    prop = {}
    out = a._maybe_apply_shadow_pts_vk2("PTS", 2, prop, 22.5)
    assert out == 22.5
    assert prop["mu_pts_vk2_applied"] is False


def test_shadow_pts_vk2_stamps_audit_fields_when_predicts():
    logs = _logs([(f"2026-04-{15+i:02d}", 30, 25, 5, 5, 2, 18, 6) for i in range(10)])
    a = _adapter_with_logs(3, logs)
    prop = {}
    a._maybe_apply_shadow_pts_vk2("PTS", 3, prop, 22.5)
    if "mu_pts_vk2" in prop:
        # The VK2 model gave us a value — verify shape.
        assert isinstance(prop["mu_pts_vk2"], float)
        assert "delta_mu_pts_vk2_vs_vk1" in prop
        assert prop["delta_mu_pts_vk2_vs_vk1"] == pytest.approx(
            prop["mu_pts_vk2"] - 22.5, rel=1e-3,
        )
        assert prop["mu_pts_vk2_applied"] is False


def test_shadow_pts_vk2_skipped_when_logs_missing():
    a = _adapter_with_logs(4, [])
    prop = {}
    out = a._maybe_apply_shadow_pts_vk2("PTS", 4, prop, 25.0)
    assert out == 25.0
    assert "mu_pts_vk2" not in prop


def test_shadow_pts_vk2_skipped_when_mu_current_none():
    logs = _logs([(f"2026-04-{15+i:02d}", 30, 25, 5, 5, 2, 18, 6) for i in range(10)])
    a = _adapter_with_logs(5, logs)
    prop = {}
    out = a._maybe_apply_shadow_pts_vk2("PTS", 5, prop, None)
    assert out is None
    assert "mu_pts_vk2" not in prop


def test_vk2_primary_stats_includes_reb_and_3pm():
    """Promotion verification — REB and 3PM should now be VK2-primary."""
    assert "AST" in NBAScoringAdapter._VK2_PRIMARY_STATS
    assert "REB" in NBAScoringAdapter._VK2_PRIMARY_STATS
    assert "3PM" in NBAScoringAdapter._VK2_PRIMARY_STATS
    # PTS NOT promoted yet — shadow only.
    assert "PTS" not in NBAScoringAdapter._VK2_PRIMARY_STATS
    # PRA stays out — synth-preferred per 2026-04-26.
    assert "PRA" not in NBAScoringAdapter._VK2_PRIMARY_STATS
