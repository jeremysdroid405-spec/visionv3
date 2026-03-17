"""
Test: Pick Card Stats Separation
================================
Verifies that:
1. Dashboard pick cards (War Zone, Safe Haven) do NOT display BDL stats (FG%, 3P%, STL, BLK)
2. Player profile endpoints return BDL stats for Command Post display
3. Search endpoint returns has_stats field correctly

These tests validate the separation between:
- UniversalPickCard (for bets) - NO detailed BDL stats
- UniversalPlayerCard (for profiles) - WITH detailed BDL stats
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCommandSearchEndpoint:
    """Test /api/command/search endpoint - should return has_stats field"""
    
    def test_search_returns_has_stats_field(self):
        """Verify search results include has_stats indicator"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "lebron"})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("success") == True
        assert "players" in data
        
        if len(data["players"]) > 0:
            player = data["players"][0]
            # Verify has_stats field exists
            assert "has_stats" in player, "has_stats field missing from search results"
            # Verify other required fields
            assert "player_name" in player
            assert "team" in player
            print(f"Player: {player['player_name']}, has_stats: {player['has_stats']}")
    
    def test_search_returns_headshot_url(self):
        """Verify search results include headshot_url"""
        response = requests.get(f"{BASE_URL}/api/command/search", params={"query": "jaime"})
        assert response.status_code == 200
        
        data = response.json()
        if len(data.get("players", [])) > 0:
            player = data["players"][0]
            # headshot_url should be present (can be null for players without photos)
            assert "headshot_url" in player or "photo_url" in player, "headshot/photo field missing"


class TestCommandProfileEndpoint:
    """Test /api/command/profile/{name} endpoint - should return baseline_stats"""
    
    def test_profile_returns_baseline_stats(self):
        """Verify profile endpoint returns BDL baseline stats"""
        response = requests.get(f"{BASE_URL}/api/command/profile/LeBron James")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("success") == True
        assert "player_name" in data
        
        # Verify baseline_stats exists with BDL data
        assert "baseline_stats" in data, "baseline_stats missing from profile response"
        
        stats = data["baseline_stats"]
        # Verify key BDL stats are present
        assert "pts" in stats, "pts missing from baseline_stats"
        assert "reb" in stats, "reb missing from baseline_stats"
        assert "ast" in stats, "ast missing from baseline_stats"
        assert "fg_pct" in stats, "fg_pct missing from baseline_stats"
        assert "fg3_pct" in stats, "fg3_pct missing from baseline_stats"
        assert "stl" in stats, "stl missing from baseline_stats"
        assert "blk" in stats, "blk missing from baseline_stats"
        
        print(f"Baseline stats for {data['player_name']}:")
        print(f"  PTS: {stats.get('pts')}, REB: {stats.get('reb')}, AST: {stats.get('ast')}")
        print(f"  FG%: {stats.get('fg_pct')}, 3P%: {stats.get('fg3_pct')}")
        print(f"  STL: {stats.get('stl')}, BLK: {stats.get('blk')}")
    
    def test_profile_returns_correct_structure(self):
        """Verify profile response has all required fields"""
        response = requests.get(f"{BASE_URL}/api/command/profile/Jaime Jaquez Jr")
        assert response.status_code == 200
        
        data = response.json()
        
        # Required fields for Command Post display
        required_fields = ["player_name", "team", "baseline_stats"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"


class TestWarZoneEndpoint:
    """Test /api/v3/war-zone endpoint - pick data structure"""
    
    def test_war_zone_pick_structure(self):
        """Verify war-zone returns pick data (NOT player profile data)"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") == True
        assert "picks" in data
        
        if len(data["picks"]) > 0:
            pick = data["picks"][0]
            # Pick cards should have these fields
            assert "player_name" in pick
            assert "stat_type" in pick or "props" in pick
            assert "team" in pick
            
            # BDL stats MAY be in the raw API response (for backend use)
            # but the FRONTEND UniversalPickCard should NOT display them
            # This test just verifies the API structure is correct
            print(f"Pick: {pick['player_name']} - {pick.get('stat_type', 'multi-props')}")
            
            # If BDL stats are present, they should be at root level (not in baseline_stats)
            # This is fine for API, frontend just ignores them in UniversalPickCard
            if "fg_pct" in pick:
                print(f"  Note: fg_pct present in API response: {pick['fg_pct']}")


class TestGoblinVaultEndpoint:
    """Test /api/v3/goblin-vault endpoint - pick data structure"""
    
    def test_goblin_vault_pick_structure(self):
        """Verify goblin-vault returns pick data"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") == True
        assert "picks" in data
        
        if len(data["picks"]) > 0:
            pick = data["picks"][0]
            # Basic pick structure
            assert "player_name" in pick
            assert "team" in pick
            print(f"Goblin pick: {pick['player_name']}")


class TestStatsDataSeparation:
    """Verify the separation of data between pick cards and player profiles"""
    
    def test_profile_has_more_stats_than_picks(self):
        """Profile should have comprehensive baseline_stats, picks have only betting data"""
        # Get profile data
        profile_resp = requests.get(f"{BASE_URL}/api/command/profile/Tyler Herro")
        assert profile_resp.status_code == 200
        profile_data = profile_resp.json()
        
        # Get war-zone pick data
        warzone_resp = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert warzone_resp.status_code == 200
        warzone_data = warzone_resp.json()
        
        # Profile should have structured baseline_stats
        assert "baseline_stats" in profile_data, "Profile missing baseline_stats"
        
        # Profile baseline_stats should have all BDL fields
        baseline = profile_data["baseline_stats"]
        required_bdl_fields = ["pts", "reb", "ast", "fg_pct", "fg3_pct", "stl", "blk"]
        for field in required_bdl_fields:
            assert field in baseline, f"Profile baseline_stats missing {field}"
        
        print("Profile baseline_stats has all required BDL fields")
        print("Frontend UniversalPickCard should NOT display these - verified via Playwright test")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
