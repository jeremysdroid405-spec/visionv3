"""
NBA Best Bets API Backend Tests
Testing: /api/todays-games, /api/calculate-hit-rate, /api/cache-status, /api/roster-status, /api/trigger-daily-sync
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "https://best-bet-finder-1.preview.emergentagent.com"

class TestHealthAndRoot:
    """Test root and basic health endpoints"""
    
    def test_root_endpoint(self):
        """Test API root returns expected message"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "NBA Best Bets API" in data["message"]
    

class TestTodaysGames:
    """Test /api/todays-games endpoint"""
    
    def test_todays_games_returns_data(self):
        """Test todays-games endpoint returns games"""
        response = requests.get(f"{BASE_URL}/api/todays-games")
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert data["success"] == True
        assert "date" in data
        assert "games_count" in data
        assert "games" in data
        assert isinstance(data["games"], list)
    
    def test_todays_games_structure(self):
        """Test todays-games response has correct game structure"""
        response = requests.get(f"{BASE_URL}/api/todays-games")
        data = response.json()
        
        if data["games_count"] > 0:
            game = data["games"][0]
            assert "game_id" in game
            assert "date" in game
            assert "home_team" in game
            assert "visitor_team" in game
            
            # Verify team structure
            home_team = game["home_team"]
            assert "id" in home_team
            assert "name" in home_team
            assert "abbreviation" in home_team


class TestHitRateCalculation:
    """Test /api/calculate-hit-rate endpoint"""
    
    def test_hit_rate_lebron_points(self):
        """Test hit rate calculation for LeBron James points"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "LeBron James",
            "prop_type": "points",
            "line": 25.5
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "data" in data
        hit_data = data["data"]
        
        # Verify triple-view stats
        assert "l5" in hit_data
        assert "l10" in hit_data
        assert "season" in hit_data
        
        # Verify L5 structure
        l5 = hit_data["l5"]
        assert "games_over" in l5
        assert "total_games" in l5
        assert "hit_rate" in l5
        assert "avg" in l5
        
        # Verify we have actual data (games played)
        assert l5["total_games"] > 0
    
    def test_hit_rate_curry_points(self):
        """Test hit rate calculation for Stephen Curry points"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "Stephen Curry",
            "prop_type": "points",
            "line": 26.5
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        hit_data = data["data"]
        
        # Verify player info
        assert hit_data["player_name"] == "Stephen Curry"
        assert hit_data["prop_type"] == "points"
        assert hit_data["line_value"] == 26.5
    
    def test_hit_rate_rebounds(self):
        """Test hit rate for rebounds prop type"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "LeBron James",
            "prop_type": "rebounds",
            "line": 7.5
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        hit_data = data["data"]
        assert hit_data["prop_type"] == "rebounds"
    
    def test_hit_rate_assists(self):
        """Test hit rate for assists prop type"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "LeBron James",
            "prop_type": "assists",
            "line": 7.5
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
    
    def test_hit_rate_trends(self):
        """Test hit rate returns trends data"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "Stephen Curry",
            "prop_type": "points",
            "line": 26.5
        })
        data = response.json()
        hit_data = data["data"]
        
        # Verify trends array exists
        assert "trends" in hit_data
        assert isinstance(hit_data["trends"], list)
    
    def test_hit_rate_last_5_games(self):
        """Test hit rate returns last 5 games data"""
        response = requests.get(f"{BASE_URL}/api/calculate-hit-rate", params={
            "player_name": "LeBron James",
            "prop_type": "points",
            "line": 25.5
        })
        data = response.json()
        hit_data = data["data"]
        
        # Verify last_5_games structure
        assert "last_5_games" in hit_data
        assert isinstance(hit_data["last_5_games"], list)
        
        if len(hit_data["last_5_games"]) > 0:
            game = hit_data["last_5_games"][0]
            assert "date" in game
            assert "points" in game
            assert "rebounds" in game
            assert "assists" in game


class TestCacheStatus:
    """Test /api/cache-status endpoint"""
    
    def test_cache_status_returns_data(self):
        """Test cache status returns expected fields"""
        response = requests.get(f"{BASE_URL}/api/cache-status")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "data" in data
        cache_data = data["data"]
        
        # Verify cache status structure
        assert "total_cached_stats" in cache_data
        assert "total_roster_entries" in cache_data
        assert "total_games_cached" in cache_data
        assert "cache_ttl_hours" in cache_data
        assert "season" in cache_data
    
    def test_cache_has_roster_entries(self):
        """Verify roster data has been synced"""
        response = requests.get(f"{BASE_URL}/api/cache-status")
        data = response.json()
        
        # Per the main agent, 555 players should be synced
        assert data["data"]["total_roster_entries"] > 0


class TestRosterStatus:
    """Test /api/roster-status endpoint"""
    
    def test_roster_status_returns_data(self):
        """Test roster status returns expected fields"""
        response = requests.get(f"{BASE_URL}/api/roster-status")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "total_players" in data
        assert "total_teams" in data
        assert "teams" in data
        assert "season" in data
    
    def test_roster_has_players(self):
        """Verify roster has synced players"""
        response = requests.get(f"{BASE_URL}/api/roster-status")
        data = response.json()
        
        assert data["total_players"] > 0
        assert data["total_teams"] > 0


class TestDailySync:
    """Test /api/trigger-daily-sync endpoint"""
    
    def test_trigger_daily_sync(self):
        """Test daily sync can be triggered"""
        response = requests.post(f"{BASE_URL}/api/trigger-daily-sync")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "sync_result" in data
        
        sync_result = data["sync_result"]
        assert "success" in sync_result
        assert sync_result["success"] == True


class TestFullBoard:
    """Test /api/full-board endpoint (mock data)"""
    
    def test_full_board_returns_data(self):
        """Test full board returns player props"""
        response = requests.get(f"{BASE_URL}/api/full-board")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "data" in data
        assert "total" in data
        assert isinstance(data["data"], list)
    
    def test_full_board_market_filter(self):
        """Test full board with market parameter"""
        response = requests.get(f"{BASE_URL}/api/full-board", params={"market": "1q"})
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True


class TestDemonValidation:
    """Test /api/validate-demon endpoint"""
    
    def test_validate_demon_line(self):
        """Test demon line validation"""
        response = requests.get(f"{BASE_URL}/api/validate-demon", params={
            "player_name": "LeBron James",
            "prop_type": "points",
            "demon_line": 30.5
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "is_valid_demon" in data
        assert "hit_rate_data" in data


class TestRateLimitStatus:
    """Test /api/rate-limit-status endpoint"""
    
    def test_rate_limit_status(self):
        """Test rate limit status returns info"""
        response = requests.get(f"{BASE_URL}/api/rate-limit-status")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "rate_limit" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
