"""
DvP Service Tests
==================
Test the DvP (Defense vs Position) service functionality.
"""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


# ==================== UNIT TESTS ====================

class TestDvPServiceFunctions:
    """Test DvP service core functions."""
    
    def test_get_current_season(self):
        """Test season calculation."""
        from services.dvp_service import get_current_season
        
        season = get_current_season()
        assert isinstance(season, int)
        assert 2020 <= season <= 2030
    
    def test_get_current_season_str(self):
        """Test season string formatting."""
        from services.dvp_service import get_current_season_str
        
        season_str = get_current_season_str()
        assert isinstance(season_str, str)
        assert "-" in season_str
        # Format should be like "2024-25"
        parts = season_str.split("-")
        assert len(parts) == 2
        assert len(parts[1]) == 2
    
    def test_calculate_dvp_modifier_pts(self):
        """Test DvP modifier calculation for points."""
        from services.dvp_service import calculate_dvp_modifier
        
        # Washington has rank 30 (worst defense) for PTS
        modifier = calculate_dvp_modifier("WAS", "PTS")
        assert modifier == 1.0  # Best matchup
        
        # Cleveland has rank 1 (best defense) for PTS
        modifier = calculate_dvp_modifier("CLE", "PTS")
        assert modifier == 0.0  # Worst matchup
    
    def test_calculate_dvp_modifier_reb(self):
        """Test DvP modifier calculation for rebounds."""
        from services.dvp_service import calculate_dvp_modifier
        
        # Washington has rank 30 (worst defense) for REB
        modifier = calculate_dvp_modifier("WAS", "REB")
        assert modifier == 1.0
        
        # Boston has rank 1 (best defense) for REB
        modifier = calculate_dvp_modifier("BOS", "REB")
        assert modifier == 0.0
    
    def test_calculate_dvp_modifier_stat_type_mapping(self):
        """Test DvP modifier with different stat type formats."""
        from services.dvp_service import calculate_dvp_modifier
        
        # Test with player_points format
        modifier1 = calculate_dvp_modifier("WAS", "player_points")
        modifier2 = calculate_dvp_modifier("WAS", "PTS")
        assert modifier1 == modifier2
    
    def test_calculate_dvp_modifier_invalid_team(self):
        """Test DvP modifier with invalid team."""
        from services.dvp_service import calculate_dvp_modifier
        
        modifier = calculate_dvp_modifier("XXX", "PTS")
        assert modifier == 0.5  # Neutral default
    
    def test_calculate_dvp_modifier_invalid_stat(self):
        """Test DvP modifier with invalid stat type."""
        from services.dvp_service import calculate_dvp_modifier
        
        modifier = calculate_dvp_modifier("WAS", "INVALID_STAT")
        assert modifier == 0.5  # Neutral default
    
    def test_calculate_dvp_modifier_combo_stats(self):
        """Test DvP modifier for combo stats (PRA, P+R, etc.)."""
        from services.dvp_service import calculate_dvp_modifier
        
        # PRA (Points + Rebounds + Assists)
        modifier = calculate_dvp_modifier("WAS", "PRA")
        assert 0.9 <= modifier <= 1.0  # Should be high (worst defense)
    
    def test_calculate_matchup_multiplier_center_reb(self):
        """Test matchup multiplier for Center on rebounds."""
        from services.dvp_service import calculate_matchup_multiplier
        
        # Against bottom 5 defense (rank > 25)
        multiplier = calculate_matchup_multiplier("C", "WAS", "REB", "over")
        assert multiplier == 1.12  # 12% boost
        
        # Against top 5 defense (rank < 6)
        multiplier = calculate_matchup_multiplier("C", "CLE", "REB", "under")
        assert multiplier == 1.12  # 12% boost for under
    
    def test_calculate_matchup_multiplier_irrelevant_stat(self):
        """Test matchup multiplier for non-position-relevant stat."""
        from services.dvp_service import calculate_matchup_multiplier
        
        # Center vs AST (not a center stat)
        multiplier = calculate_matchup_multiplier("C", "WAS", "AST", "over")
        assert multiplier == 1.0  # No boost
    
    def test_get_dvp_rank(self):
        """Test getting raw DvP rank."""
        from services.dvp_service import get_dvp_rank
        
        # Washington has rank 30 for PTS (worst defense)
        rank = get_dvp_rank("WAS", "PTS")
        assert rank == 30
        
        # Unknown team returns default 15
        rank = get_dvp_rank("XXX", "PTS")
        assert rank == 15
    
    def test_get_dvp_rank_color(self):
        """Test DvP rank color assignment."""
        from services.dvp_service import get_dvp_rank_color
        
        assert get_dvp_rank_color(30) == "green"  # Bottom 5 (worst defense)
        assert get_dvp_rank_color(25) == "green"  # Bottom 5
        assert get_dvp_rank_color(24) == "yellow"  # Middle
        assert get_dvp_rank_color(10) == "yellow"  # Middle
        assert get_dvp_rank_color(9) == "red"  # Top 10 (best defense)
        assert get_dvp_rank_color(1) == "red"  # Top 10
    
    def test_calculate_dvp_certainty_multiplier(self):
        """Test DvP certainty multiplier calculation."""
        from services.dvp_service import calculate_dvp_certainty_multiplier
        
        # Bottom 5 defense (rank >= 25) = +10% boost
        assert calculate_dvp_certainty_multiplier(30) == 1.10
        assert calculate_dvp_certainty_multiplier(25) == 1.10
        
        # Top 5 defense (rank <= 5) = -15% penalty
        assert calculate_dvp_certainty_multiplier(1) == 0.85
        assert calculate_dvp_certainty_multiplier(5) == 0.85
        
        # Middle = no change
        assert calculate_dvp_certainty_multiplier(15) == 1.0
        assert calculate_dvp_certainty_multiplier(10) == 1.0
    
    def test_get_dvp_label(self):
        """Test DvP label generation."""
        from services.dvp_service import get_dvp_label
        
        assert get_dvp_label(0.8) == "FAVORABLE"
        assert get_dvp_label(0.5) == "NEUTRAL"
        assert get_dvp_label(0.2) == "TOUGH"
    
    def test_get_full_dvp_analysis(self):
        """Test full DvP analysis output."""
        from services.dvp_service import get_full_dvp_analysis
        
        analysis = get_full_dvp_analysis("WAS", "PTS", "C")
        
        assert "dvp_modifier" in analysis
        assert "dvp_label" in analysis
        assert "defensive_rank" in analysis
        assert "over_multiplier" in analysis
        assert "under_multiplier" in analysis
        assert analysis["opponent_team"] == "WAS"
        assert analysis["stat_type"] == "PTS"
        assert analysis["player_position"] == "C"
    
    def test_apply_dvp_to_prop(self):
        """Test applying DvP to prop data."""
        from services.dvp_service import apply_dvp_to_prop
        
        prop = {
            "opponent": "WAS",
            "stat_type": "REB",
            "player_position": "C",
            "hit_probability": 60,
            "direction": "over"
        }
        
        enhanced = apply_dvp_to_prop(prop)
        
        assert "dvp_modifier" in enhanced
        assert "dvp_label" in enhanced
        assert "defensive_rank" in enhanced
        assert "matchup_multiplier" in enhanced
        assert "adjusted_hit_probability" in enhanced
        
        # Should have boosted probability
        assert enhanced["adjusted_hit_probability"] >= 60


class TestDvPDataStructures:
    """Test DvP data structures."""
    
    def test_dvp_cache_entry_expiry(self):
        """Test DvP cache entry expiry check."""
        from services.dvp_service import DvPCacheEntry
        
        # Create entry that expires in 1 hour
        entry = DvPCacheEntry(
            rankings={"PTS": {"WAS": 30}},
            source="dynamic_live",
            fetched_at=datetime.now(timezone.utc),
            season="2024-25",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        
        assert not entry.is_expired
        assert entry.age_hours < 1
    
    def test_dvp_cache_entry_expired(self):
        """Test DvP cache entry expired check."""
        from services.dvp_service import DvPCacheEntry
        
        # Create entry that expired 1 hour ago
        entry = DvPCacheEntry(
            rankings={"PTS": {"WAS": 30}},
            source="dynamic_live",
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=25),
            season="2024-25",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
        
        assert entry.is_expired
        assert entry.age_hours > 24


class TestDvPServiceStatus:
    """Test DvP service status functions."""
    
    def test_get_dvp_status(self):
        """Test DvP service status output."""
        from services.dvp_service import get_dvp_status
        
        status = get_dvp_status()
        
        assert "has_live_data" in status
        assert "data_source" in status
        assert "dvp_type" in status
        assert "season" in status
        assert "fetch_failures" in status
        assert "api_key_configured" in status
        assert "mongodb_configured" in status
    
    def test_get_data_source_header(self):
        """Test data source header generation."""
        from services.dvp_service import get_data_source_header
        
        headers = get_data_source_header()
        
        assert "X-Data-Source" in headers
        assert "X-DVP-Last-Update" in headers
        assert "dvp_type" in headers


class TestDvPStatsConversion:
    """Test stats conversion functions."""
    
    def test_convert_stats_to_rankings(self):
        """Test converting raw stats to rankings."""
        from services.dvp_service import _convert_stats_to_rankings
        
        raw_stats = {
            "BOS": {"PTS": 100.0, "REB": 40.0},
            "WAS": {"PTS": 120.0, "REB": 50.0},
            "CLE": {"PTS": 95.0, "REB": 38.0}
        }
        
        rankings = _convert_stats_to_rankings(raw_stats)
        
        # Lower stats = better defense = lower rank
        assert rankings["PTS"]["CLE"] == 1  # 95 pts allowed (best)
        assert rankings["PTS"]["BOS"] == 2  # 100 pts allowed
        assert rankings["PTS"]["WAS"] == 3  # 120 pts allowed (worst)
    
    def test_convert_stats_to_rankings_empty(self):
        """Test converting empty stats."""
        from services.dvp_service import _convert_stats_to_rankings
        
        rankings = _convert_stats_to_rankings({})
        assert rankings == {}


# ==================== INTEGRATION TESTS ====================

@pytest.mark.asyncio
async def test_fetch_live_dvp_fallback():
    """Test that fetch_live_dvp returns valid rankings (live or fallback)."""
    from services.dvp_service import fetch_live_dvp, DvPDataSource
    
    rankings, source, metadata = await fetch_live_dvp()
    
    # Should return valid rankings (either live or fallback)
    assert rankings is not None
    assert len(rankings) >= 3  # At least PTS, AST, REB
    # Source can be dynamic_live (if API key works), cached, mongodb, or static-fallback
    assert source in [
        DvPDataSource.STATIC_FALLBACK, 
        DvPDataSource.CACHED, 
        DvPDataSource.MONGODB,
        DvPDataSource.DYNAMIC_LIVE
    ]


@pytest.mark.asyncio
async def test_get_dvp_rankings():
    """Test getting DvP rankings."""
    from services.dvp_service import get_dvp_rankings
    
    rankings = await get_dvp_rankings()
    
    assert rankings is not None
    assert "PTS" in rankings
    assert "REB" in rankings
    assert "AST" in rankings
    
    # Check rankings have all 30 teams
    assert len(rankings["PTS"]) == 30


@pytest.mark.asyncio
async def test_get_dvp_rankings_with_source():
    """Test getting DvP rankings with source headers."""
    from services.dvp_service import get_dvp_rankings_with_source
    
    rankings, headers = await get_dvp_rankings_with_source()
    
    assert rankings is not None
    assert headers is not None
    assert "X-Data-Source" in headers
    assert "dvp_type" in headers


# ==================== API ENDPOINT TESTS ====================
# These tests require a test_client fixture and full app setup
# For quick validation, use curl or the testing agent instead

@pytest.mark.skip(reason="Requires test_client fixture - test via curl instead")
@pytest.mark.asyncio
async def test_dvp_status_endpoint(test_client):
    """Test /api/dvp-status endpoint."""
    response = test_client.get("/api/dvp-status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "dvp" in data


@pytest.mark.skip(reason="Requires test_client fixture - test via curl instead")
@pytest.mark.asyncio
async def test_dvp_rankings_endpoint(test_client):
    """Test /api/dvp-rankings endpoint."""
    response = test_client.get("/api/dvp-rankings")
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "rankings" in data
    assert "stat_types" in data


@pytest.mark.skip(reason="Requires test_client fixture - test via curl instead")
@pytest.mark.asyncio
async def test_dvp_analysis_endpoint(test_client):
    """Test /api/dvp-analysis endpoint."""
    response = test_client.get("/api/dvp-analysis/WAS/PTS?player_position=C")
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "analysis" in data
    assert data["analysis"]["opponent_team"] == "WAS"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
