"""
P2 Tech Debt Cleanup Tests
==========================
Tests for consolidated player_lookup utility and PickVisionUtils components.

Features tested:
- Backend /api/command/search endpoint using consolidated player_lookup utility
- Backend /api/command/profile/{player_name} endpoint for player stats
- Goblin Vault (Safe Haven) endpoint
- War Zone (Demon Radar) endpoint
- API data integrity checks
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestCommandSearchEndpoint:
    """Tests for /api/command/search endpoint using player_lookup utility"""
    
    def test_search_lebron(self):
        """Search for LeBron James"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "lebron", "limit": 5})
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert data["source"] == "master_hub"
        assert len(data["players"]) >= 1
        
        # Verify player data structure
        player = data["players"][0]
        assert "player_name" in player
        assert "LeBron" in player["player_name"]
        assert "team" in player
        assert "position" in player
        assert "headshot_url" in player
        
    def test_search_kevin(self):
        """Search for Kevin (multiple matches expected)"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "kevin", "limit": 10})
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert len(data["players"]) >= 2  # Kevin Durant, Kevin Love, etc.
        
        # All results should contain "kevin" in name (case-insensitive)
        for player in data["players"]:
            assert "kevin" in player["player_name"].lower()
            
    def test_search_short_query_rejected(self):
        """Search with too short query should fail validation"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "a"})
        assert response.status_code == 422  # Validation error
        
    def test_search_returns_has_stats_field(self):
        """Verify search results include has_stats field"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "durant", "limit": 1})
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        
        if len(data["players"]) > 0:
            player = data["players"][0]
            assert "has_stats" in player
            assert isinstance(player["has_stats"], bool)


class TestCommandProfileEndpoint:
    """Tests for /api/command/profile/{player_name} endpoint"""
    
    def test_profile_lebron_james(self):
        """Get tactical profile for LeBron James"""
        response = requests.get(f"{BASE_URL}/api/command/profile/LeBron%20James")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert data["player_name"] == "LeBron James"
        assert data["team"] == "LAL"
        assert "photo_url" in data
        assert "baseline_stats" in data
        assert "lines" in data
        
        # Verify baseline_stats structure
        baseline_stats = data["baseline_stats"]
        assert "PTS" in baseline_stats
        assert "REB" in baseline_stats
        assert "AST" in baseline_stats
        
        # Verify PTS stats have required fields
        pts_stats = baseline_stats["PTS"]
        assert "l5_avg" in pts_stats
        assert "l10_avg" in pts_stats
        assert "season_avg" in pts_stats
        
    def test_profile_kevin_durant(self):
        """Get tactical profile for Kevin Durant"""
        response = requests.get(f"{BASE_URL}/api/command/profile/Kevin%20Durant")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Kevin Durant" in data["player_name"]
        
    def test_profile_nonexistent_player(self):
        """Profile for nonexistent player should return success=false or empty"""
        response = requests.get(f"{BASE_URL}/api/command/profile/NonExistentPlayer12345")
        assert response.status_code == 200
        
        data = response.json()
        # API returns success false or empty lines for unknown players
        assert "player_name" in data


class TestGoblinVaultEndpoint:
    """Tests for Safe Haven (Goblin Vault) endpoint"""
    
    def test_goblin_vault_returns_picks(self):
        """Verify Goblin Vault returns picks with player data"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "picks" in data
        assert len(data["picks"]) > 0
        
    def test_goblin_vault_pick_structure(self):
        """Verify Goblin Vault pick data structure"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        pick = data["picks"][0]
        
        # Required fields for PlayerCard component
        assert "player_name" in pick
        assert "team" in pick
        assert "stat_type" in pick
        assert "goblin_line" in pick or "line" in pick
        
        # Stats from master hub
        assert "h10_rate" in pick or "l10_avg" in pick
        assert "h5_rate" in pick or "l5_avg" in pick
        
        # Photo URL from master hub
        assert "photo_url" in pick or "headshot_url" in pick
        
    def test_goblin_vault_has_dvp_data(self):
        """Verify Goblin Vault picks include DvP data"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        pick = data["picks"][0]
        
        # DvP fields
        assert "dvp_rank" in pick or "dvp_modifier" in pick
        

class TestWarZoneEndpoint:
    """Tests for War Zone (Demon Radar) endpoint"""
    
    def test_war_zone_returns_picks(self):
        """Verify War Zone returns picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "picks" in data
        assert len(data["picks"]) > 0
        
    def test_war_zone_pick_structure(self):
        """Verify War Zone pick data structure"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        pick = data["picks"][0]
        
        # Required fields for PlayerCard component
        assert "player_name" in pick
        assert "team" in pick
        assert "stat_type" in pick
        assert "demon_line" in pick or "line" in pick
        
        # Demon-specific fields
        assert "is_demon" in pick or "is_radar_pick" in pick


class TestAPIHealth:
    """General API health and status tests"""
    
    def test_api_root_status(self):
        """Check API root returns status"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        
        data = response.json()
        assert "status" in data
        assert data["status"] == "operational"
        
    def test_command_grades_endpoint(self):
        """Verify grades endpoint works"""
        response = requests.get(f"{BASE_URL}/api/command/grades")
        assert response.status_code == 200
        
        data = response.json()
        assert "grades" in data
        assert "S" in data["grades"]
        assert "A" in data["grades"]


class TestDataIntegrity:
    """Data integrity checks for SSOT architecture"""
    
    def test_master_hub_photo_urls_used(self):
        """Verify photo URLs come from master hub (no external API calls)"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        for pick in data["picks"][:5]:  # Check first 5
            photo_url = pick.get("photo_url") or pick.get("headshot_url")
            if photo_url:
                # Should be from ESPN or NBA CDN (cached in master hub)
                assert "espncdn.com" in photo_url or "nba.com" in photo_url or "cdn.nba.com" in photo_url
                
    def test_baseline_stats_present(self):
        """Verify baseline_stats from master hub are included"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        for pick in data["picks"][:3]:  # Check first 3
            assert "baseline_stats" in pick
            baseline = pick["baseline_stats"]
            assert isinstance(baseline, dict)
            # Should have at least some stat categories
            stat_keys = ["PTS", "REB", "AST", "3PM"]
            has_stats = any(key in baseline for key in stat_keys)
            assert has_stats, f"Pick {pick.get('player_name')} missing baseline_stats"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
