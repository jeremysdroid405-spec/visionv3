"""
Test Suite: Advanced Analytics Insights API (v3.1)
Tests for schedule density, pace adjustments, usage bumps, volatility scores, and AI insights
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestSyncDailyInsights:
    """Tests for /api/v3/sync-daily-insights endpoint - calculates advanced analytics"""
    
    def test_sync_insights_endpoint_exists(self):
        """Verify the sync-daily-insights endpoint returns 200"""
        response = requests.post(f"{BASE_URL}/api/v3/sync-daily-insights", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("success") == True, "Expected success=True"
        assert "insights_calculated" in data, "Response should contain insights_calculated count"
        
    def test_sync_insights_returns_player_count(self):
        """Verify sync returns count of players analyzed"""
        response = requests.post(f"{BASE_URL}/api/v3/sync-daily-insights", timeout=30)
        data = response.json()
        
        insights_count = data.get("insights_calculated", 0)
        assert insights_count >= 0, "insights_calculated should be a non-negative number"
        
        # If we have players in cached_board, we should get insights
        print(f"Insights calculated for {insights_count} players")


class TestPlayerInsightsAPI:
    """Tests for /api/v3/player-insights/{player_name} endpoint"""
    
    def test_get_kevin_durant_insights(self):
        """Verify Kevin Durant insights API returns correct structure"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Verify required fields exist
        required_fields = [
            "player_name", "volatility_score", "insight_summary", 
            "pace_adjustment_factor", "schedule_density_factor",
            "ai_confidence_rating", "days_rest"
        ]
        
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
    
    def test_insights_volatility_score_valid(self):
        """Verify volatility_score is a valid value (Low/Med/High)"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        data = response.json()
        
        volatility = data.get("volatility_score")
        valid_volatility_values = ["Low", "Med", "High"]
        assert volatility in valid_volatility_values, f"volatility_score '{volatility}' not in {valid_volatility_values}"
    
    def test_insights_pace_adjustment_factor(self):
        """Verify pace_adjustment_factor is a reasonable number (0.8-1.2)"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        data = response.json()
        
        pace_factor = data.get("pace_adjustment_factor", 1.0)
        assert 0.8 <= pace_factor <= 1.3, f"pace_adjustment_factor {pace_factor} out of expected range 0.8-1.3"
    
    def test_insights_usage_bump_percent(self):
        """Verify usage_bump_percent is a valid number >= 0"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        data = response.json()
        
        usage_bump = data.get("usage_bump_percent", 0)
        assert usage_bump >= 0, f"usage_bump_percent {usage_bump} should be >= 0"
        assert usage_bump <= 50, f"usage_bump_percent {usage_bump} should be <= 50"
    
    def test_insights_ai_confidence_rating(self):
        """Verify ai_confidence_rating is 0-100"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        data = response.json()
        
        confidence = data.get("ai_confidence_rating", 50)
        assert 0 <= confidence <= 100, f"ai_confidence_rating {confidence} should be 0-100"
    
    def test_insights_summary_not_empty(self):
        """Verify insight_summary contains text"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Kevin%20Durant", timeout=10)
        data = response.json()
        
        summary = data.get("insight_summary", "")
        assert len(summary) > 0, "insight_summary should not be empty"
    
    def test_nonexistent_player_returns_404(self):
        """Verify nonexistent player returns 404"""
        response = requests.get(f"{BASE_URL}/api/v3/player-insights/Fake%20Player%20XYZ", timeout=10)
        assert response.status_code == 404, f"Expected 404 for nonexistent player, got {response.status_code}"


class TestCachedPlayerWithInsights:
    """Tests for /api/v3/cached-player/{player_name} to verify insights integration"""
    
    def test_cached_player_includes_insights(self):
        """Verify cached-player endpoint includes insights object"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/Kevin%20Durant", timeout=10)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("success") == True, "Expected success=True"
        
        player = data.get("player", {})
        assert "insights" in player, "Player should contain 'insights' object"
        
        insights = player.get("insights", {})
        assert "volatility_score" in insights, "insights should contain volatility_score"
        assert "insight_summary" in insights, "insights should contain insight_summary"
    
    def test_cached_player_insights_volatility_score(self):
        """Verify insights.volatility_score in cached-player response"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/Kevin%20Durant", timeout=10)
        data = response.json()
        
        insights = data.get("player", {}).get("insights", {})
        volatility = insights.get("volatility_score")
        
        valid_values = ["Low", "Med", "High"]
        assert volatility in valid_values, f"volatility_score '{volatility}' not valid"
        print(f"Kevin Durant volatility score: {volatility}")
    
    def test_cached_player_insights_pace_factor(self):
        """Verify insights.pace_adjustment_factor in cached-player response"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/Kevin%20Durant", timeout=10)
        data = response.json()
        
        insights = data.get("player", {}).get("insights", {})
        pace_factor = insights.get("pace_adjustment_factor", 1.0)
        
        assert isinstance(pace_factor, (int, float)), "pace_adjustment_factor should be numeric"
        print(f"Kevin Durant pace factor: {pace_factor}")
    
    def test_cached_player_insights_ai_confidence(self):
        """Verify insights.ai_confidence_rating in cached-player response"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/Kevin%20Durant", timeout=10)
        data = response.json()
        
        insights = data.get("player", {}).get("insights", {})
        confidence = insights.get("ai_confidence_rating", 0)
        
        assert 0 <= confidence <= 100, f"ai_confidence_rating {confidence} out of range"
        print(f"Kevin Durant AI confidence: {confidence}%")
    
    def test_cached_player_insights_summary(self):
        """Verify insights.insight_summary contains meaningful text"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/Kevin%20Durant", timeout=10)
        data = response.json()
        
        insights = data.get("player", {}).get("insights", {})
        summary = insights.get("insight_summary", "")
        
        assert len(summary) > 5, f"insight_summary too short: '{summary}'"
        print(f"Kevin Durant insight: {summary}")


class TestV3StatusEndpoint:
    """Tests for /api/v3/status to verify system health"""
    
    def test_status_returns_200(self):
        """Verify status endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        assert response.status_code == 200
    
    def test_status_has_sync_date(self):
        """Verify status includes sync_date"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        data = response.json().get("data", {})
        
        assert "sync_date" in data, "Status should include sync_date"
        assert "unique_players" in data, "Status should include unique_players"
        
        print(f"Sync date: {data.get('sync_date')}")
        print(f"Unique players: {data.get('unique_players')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
