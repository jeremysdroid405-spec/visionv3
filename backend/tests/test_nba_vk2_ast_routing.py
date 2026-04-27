"""Unit tests for the AST → VK2-primary routing change (2026-04-28)."""
from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def test_ast_is_in_vk2_primary_stats():
    assert "AST" in NBAScoringAdapter._VK2_PRIMARY_STATS


def test_pts_pra_not_in_vk2_primary_stats():
    """PTS and PRA stay on legacy paths:
    - PTS: shadow-first via mu_pts_vk2 audit column (2026-04-28).
    - PRA: synth-preferred per 2026-04-26 audit (synth ≫ direct VK2).
    REB and 3PM were promoted on 2026-04-28 alongside the shadow PTS."""
    assert "PTS" not in NBAScoringAdapter._VK2_PRIMARY_STATS
    assert "PRA" not in NBAScoringAdapter._VK2_PRIMARY_STATS
    assert "REB" in NBAScoringAdapter._VK2_PRIMARY_STATS
    assert "3PM" in NBAScoringAdapter._VK2_PRIMARY_STATS


def test_ast_artifact_exists():
    """`vk2_ast.pkl` must exist so the promotion has something to call."""
    import os
    assert os.path.exists(
        os.path.join(NBAScoringAdapter._VK2_DIR, "vk2_ast.pkl")
    ), "vk2_ast.pkl must exist for the AST-VK2-primary routing to work"


def test_vk2_ast_loads_with_expected_metadata():
    a = NBAScoringAdapter()
    a._load_vk2_models()
    m = a._vk2_models.get("AST")
    assert m is not None
    assert m["sigma"] > 0, "AST sigma must be positive for p_over math"
    assert len(m["features"]) > 0, "AST must declare its feature columns"
    assert "ast_L3_mean" in m["features"] or any(
        "ast" in c.lower() for c in m["features"]
    ), "AST model must use AST-specific features"
