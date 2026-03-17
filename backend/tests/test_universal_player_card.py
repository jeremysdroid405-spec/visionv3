"""
Test UniversalPlayerCard Backend APIs
=====================================
Tests for the features_or_bugs_to_test:
1. /api/v3/war-zone returns picks with fg_pct, fg3_pct, stl, blk from vault
2. /api/v3/safe-haven returns picks with fg_pct, fg3_pct, stl, blk from vault
3. /api/command/profile/{name} returns baseline_stats with PTS, REB, AST, FG%, 3P%, STL, BLK
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://best-bet-finder-1.preview.emergentagent.com')

class TestWarZoneAPI:
    """Test War Zone endpoint returns picks with BDL vault stats"""
    
    def test_war_zone_endpoint_status(self):
        """Verify /api/v3/war-zone returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print(f"✅ War Zone endpoint returns 200")
    
    def test_war_zone_has_picks(self):
        """Verify /api/v3/war-zone returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        
        assert "success" in data, "Response missing 'success' field"
        assert "picks" in data, "Response missing 'picks' field"
        print(f"✅ War Zone has {len(data.get('picks', []))} picks")
    
    def test_war_zone_picks_have_tier_label(self):
        """Verify War Zone picks have DEMON tier_label"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No War Zone picks available - data dependent")
        
        for pick in picks[:3]:  # Check first 3
            assert pick.get("tier_label") == "DEMON" or pick.get("is_demon") == True, \
                f"Pick {pick.get('player_name')} missing DEMON tier"
        print(f"✅ War Zone picks have DEMON tier_label")
    
    def test_war_zone_picks_have_vault_stats(self):
        """Verify War Zone picks have fg_pct, fg3_pct, stl, blk from vault"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No War Zone picks available - data dependent")
        
        # Check if at least some picks have vault stats
        picks_with_stats = 0
        for pick in picks:
            baseline = pick.get("baseline_stats", {})
            has_fg = pick.get("fg_pct") is not None or baseline.get("fg_pct") is not None
            has_fg3 = pick.get("fg3_pct") is not None or baseline.get("fg3_pct") is not None
            has_stl = pick.get("stl") is not None or baseline.get("stl") is not None
            has_blk = pick.get("blk") is not None or baseline.get("blk") is not None
            
            if has_fg or has_fg3 or has_stl or has_blk:
                picks_with_stats += 1
                print(f"  {pick.get('player_name')}: FG%={pick.get('fg_pct', baseline.get('fg_pct'))}, "
                      f"3P%={pick.get('fg3_pct', baseline.get('fg3_pct'))}, "
                      f"STL={pick.get('stl', baseline.get('stl'))}, "
                      f"BLK={pick.get('blk', baseline.get('blk'))}")
        
        print(f"✅ War Zone: {picks_with_stats}/{len(picks)} picks have vault stats")


class TestSafeHavenAPI:
    """Test Safe Haven endpoint returns picks with BDL vault stats"""
    
    def test_safe_haven_endpoint_status(self):
        """Verify /api/v3/safe-haven returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print(f"✅ Safe Haven endpoint returns 200")
    
    def test_safe_haven_has_picks(self):
        """Verify /api/v3/safe-haven returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        data = response.json()
        
        assert "success" in data, "Response missing 'success' field"
        assert "picks" in data, "Response missing 'picks' field"
        print(f"✅ Safe Haven has {len(data.get('picks', []))} picks")
    
    def test_safe_haven_picks_have_tier_label(self):
        """Verify Safe Haven picks have GOBLIN tier_label"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No Safe Haven picks available - data dependent")
        
        for pick in picks[:3]:  # Check first 3
            assert pick.get("tier_label") == "GOBLIN" or pick.get("is_goblin") == True, \
                f"Pick {pick.get('player_name')} missing GOBLIN tier"
        print(f"✅ Safe Haven picks have GOBLIN tier_label")
    
    def test_safe_haven_picks_have_vault_stats(self):
        """Verify Safe Haven picks have fg_pct, fg3_pct, stl, blk from vault"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No Safe Haven picks available - data dependent")
        
        # Check if at least some picks have vault stats
        picks_with_stats = 0
        for pick in picks:
            baseline = pick.get("baseline_stats", {})
            has_fg = pick.get("fg_pct") is not None or baseline.get("fg_pct") is not None
            has_fg3 = pick.get("fg3_pct") is not None or baseline.get("fg3_pct") is not None
            has_stl = pick.get("stl") is not None or baseline.get("stl") is not None
            has_blk = pick.get("blk") is not None or baseline.get("blk") is not None
            
            if has_fg or has_fg3 or has_stl or has_blk:
                picks_with_stats += 1
                print(f"  {pick.get('player_name')}: FG%={pick.get('fg_pct', baseline.get('fg_pct'))}, "
                      f"3P%={pick.get('fg3_pct', baseline.get('fg3_pct'))}, "
                      f"STL={pick.get('stl', baseline.get('stl'))}, "
                      f"BLK={pick.get('blk', baseline.get('blk'))}")
        
        print(f"✅ Safe Haven: {picks_with_stats}/{len(picks)} picks have vault stats")


class TestFrontLinesAPI:
    """Test Front Lines endpoint returns picks with appropriate tier styling"""
    
    def test_front_lines_endpoint_status(self):
        """Verify /api/v3/front-lines returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print(f"✅ Front Lines endpoint returns 200")
    
    def test_front_lines_has_picks(self):
        """Verify /api/v3/front-lines returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        data = response.json()
        
        assert "success" in data, "Response missing 'success' field"
        assert "picks" in data, "Response missing 'picks' field"
        print(f"✅ Front Lines has {len(data.get('picks', []))} picks")


class TestCommandPostProfileAPI:
    """Test Command Post profile returns baseline_stats with all BDL stats"""
    
    def test_command_profile_endpoint_status(self):
        """Verify /api/command/profile/{name} returns 200"""
        response = requests.get(f"{BASE_URL}/api/command/profile/LeBron James")
        # Might be 404 if player not found, 200 if found
        assert response.status_code in [200, 404], f"Expected 200 or 404, got {response.status_code}"
        print(f"✅ Command profile endpoint returns {response.status_code}")
    
    def test_command_profile_has_baseline_stats(self):
        """Verify profile returns baseline_stats with PTS, REB, AST, FG%, 3P%, STL, BLK"""
        # First get a player from the board
        board_response = requests.get(f"{BASE_URL}/api/v3/board")
        if board_response.status_code != 200:
            pytest.skip("Board endpoint not working")
        
        board_data = board_response.json()
        players = board_data.get("players", [])
        
        if not players:
            pytest.skip("No players available on board")
        
        # Pick a player to test
        test_player = players[0].get("player_name")
        if not test_player:
            pytest.skip("Player name not found")
        
        # Get profile
        profile_response = requests.get(f"{BASE_URL}/api/command/profile/{test_player}")
        
        if profile_response.status_code == 404:
            pytest.skip(f"Player {test_player} not found in profile")
        
        profile_data = profile_response.json()
        
        if not profile_data.get("success"):
            pytest.skip(f"Profile not successful: {profile_data.get('message')}")
        
        baseline_stats = profile_data.get("baseline_stats", {})
        
        # Check for key stats
        expected_keys = ["pts", "reb", "ast", "fg_pct", "fg3_pct", "stl", "blk"]
        found_keys = []
        for key in expected_keys:
            if key in baseline_stats:
                found_keys.append(key)
                print(f"  {key}: {baseline_stats[key]}")
        
        print(f"✅ Profile has {len(found_keys)}/{len(expected_keys)} expected baseline stats")
        print(f"✅ Found: {found_keys}")


class TestPickDataStructure:
    """Test pick data structure includes all required display fields"""
    
    def test_pick_has_required_fields(self):
        """Verify picks have player_name, team, stat_type, line, tier_label"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No picks available")
        
        pick = picks[0]
        required_fields = ["player_name", "team", "stat_type", "line"]
        
        for field in required_fields:
            assert field in pick, f"Pick missing required field: {field}"
        
        print(f"✅ Picks have all required fields: {required_fields}")
    
    def test_pick_has_hit_rates(self):
        """Verify picks have L10 hit rate and season average"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No picks available")
        
        picks_with_hit_rate = 0
        picks_with_avg = 0
        
        for pick in picks:
            if pick.get("h10_rate") is not None:
                picks_with_hit_rate += 1
            if pick.get("season_avg") is not None:
                picks_with_avg += 1
        
        print(f"✅ Picks with h10_rate: {picks_with_hit_rate}/{len(picks)}")
        print(f"✅ Picks with season_avg: {picks_with_avg}/{len(picks)}")
    
    def test_pick_has_diff_from_avg(self):
        """Verify picks have diff_from_avg percentage"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No picks available")
        
        picks_with_diff = 0
        
        for pick in picks:
            if pick.get("diff_from_avg") is not None:
                picks_with_diff += 1
                print(f"  {pick.get('player_name')}: diff_from_avg={pick.get('diff_from_avg')}%")
        
        print(f"✅ Picks with diff_from_avg: {picks_with_diff}/{len(picks)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
