"""
MLB Vegas Killer Historical Backfill Tests
==========================================
Tests for the MLB BDL sync and VK historical backfill features.

Features tested:
1. MLB BDL sync correctly populates game dates from game cache
2. MLB BDL sync correctly maps MLB stat fields (rbi -> rbis, k -> strikeouts, etc.)
3. POST /api/v3/mlb/vk-backfill endpoint works
4. GET /api/v3/mlb/vk-baselines/{player_name} endpoint returns player baselines
5. MLB player data has date and opponent_abbr populated
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://best-bet-finder-1.preview.emergentagent.com')


class TestMLBBDLSync:
    """Tests for MLB BDL sync functionality - verifying date and opponent population."""
    
    def test_mlb_players_endpoint_returns_data(self):
        """Test that MLB players endpoint returns data with BDL IDs."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/players", params={"sport": "mlb", "limit": 10})
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "players" in data, "Response should contain 'players' key"
        assert "sport" in data, "Response should contain 'sport' key"
        assert data["sport"] == "mlb", "Sport should be 'mlb'"
        assert len(data["players"]) > 0, "Should have at least one player"
        
        # Verify player structure
        player = data["players"][0]
        assert "bdl_id" in player, "Player should have bdl_id"
        assert "display_name" in player, "Player should have display_name"
        print(f"✓ MLB players endpoint returned {len(data['players'])} players")
    
    def test_mlb_player_stats_have_dates(self):
        """Test that MLB player stats have date field populated from game cache."""
        # Get a known MLB player - Mike Trout
        response = requests.get(f"{BASE_URL}/api/v3/bdl/stats/Mike%20Trout", params={"sport": "mlb"})
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "game_logs" in data, "Response should contain 'game_logs'"
        assert len(data["game_logs"]) > 0, "Should have at least one game log"
        
        # Verify dates are populated (P0 blocker fix)
        game_log = data["game_logs"][0]
        assert "date" in game_log, "Game log should have 'date' field"
        assert game_log["date"] is not None, "Date should not be None (P0 blocker fix)"
        assert "T" in game_log["date"], "Date should be in ISO format with time"
        
        print(f"✓ MLB player stats have dates populated: {game_log['date']}")
    
    def test_mlb_player_stats_have_opponent_abbr(self):
        """Test that MLB player stats have opponent_abbr field populated."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/stats/Mike%20Trout", params={"sport": "mlb"})
        
        assert response.status_code == 200
        
        data = response.json()
        game_log = data["game_logs"][0]
        
        assert "opponent_abbr" in game_log, "Game log should have 'opponent_abbr' field"
        assert game_log["opponent_abbr"] is not None, "opponent_abbr should not be None"
        assert len(game_log["opponent_abbr"]) >= 2, "opponent_abbr should be a team abbreviation (2-3 chars)"
        
        print(f"✓ MLB player stats have opponent_abbr populated: {game_log['opponent_abbr']}")
    
    def test_mlb_stat_field_mappings_batter(self):
        """Test that MLB batter stat fields are correctly mapped (rbi -> rbis, k -> strikeouts, etc.)."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/stats/Mike%20Trout", params={"sport": "mlb"})
        
        assert response.status_code == 200
        
        data = response.json()
        game_log = data["game_logs"][0]
        
        # Verify batter stat field mappings (BDL uses short names, we map to full names)
        batter_fields = [
            "at_bats",
            "hits",
            "runs",
            "rbis",        # BDL uses 'rbi', we map to 'rbis'
            "home_runs",   # BDL uses 'hr', we map to 'home_runs'
            "stolen_bases",
            "walks",       # BDL uses 'bb', we map to 'walks'
            "strikeouts",  # BDL uses 'k', we map to 'strikeouts'
            "total_bases",
            "doubles",
            "triples",
            "plate_appearances"
        ]
        
        for field in batter_fields:
            assert field in game_log, f"Game log should have '{field}' field"
        
        print(f"✓ MLB batter stat fields correctly mapped: {batter_fields}")
    
    def test_mlb_stat_field_mappings_pitcher(self):
        """Test that MLB pitcher stat fields are correctly mapped."""
        # Get a pitcher's stats
        response = requests.get(f"{BASE_URL}/api/v3/bdl/players", params={"sport": "mlb", "limit": 50})
        
        assert response.status_code == 200
        
        data = response.json()
        
        # Find a pitcher with game logs
        pitcher = None
        for player in data["players"]:
            if player.get("position") and "Pitcher" in player.get("position", ""):
                if player.get("bdl_game_logs") and len(player.get("bdl_game_logs", [])) > 0:
                    pitcher = player
                    break
        
        if pitcher is None:
            pytest.skip("No pitcher with game logs found")
        
        game_log = pitcher["bdl_game_logs"][0]
        
        # Verify pitcher stat field mappings
        pitcher_fields = [
            "innings_pitched",      # BDL uses 'ip'
            "pitcher_strikeouts",   # BDL uses 'p_k'
            "pitcher_walks",        # BDL uses 'p_bb'
            "hits_allowed",         # BDL uses 'p_hits'
            "earned_runs",          # BDL uses 'er'
        ]
        
        for field in pitcher_fields:
            assert field in game_log, f"Pitcher game log should have '{field}' field"
        
        print(f"✓ MLB pitcher stat fields correctly mapped for {pitcher['display_name']}")


class TestMLBVKBackfillEndpoint:
    """Tests for the VK Historical Backfill endpoint."""
    
    def test_vk_backfill_endpoint_exists(self):
        """Test that the VK backfill endpoint exists and accepts POST requests."""
        # Note: We don't actually run the backfill as it takes 5-15 minutes
        # Just verify the endpoint exists and returns proper response
        response = requests.post(
            f"{BASE_URL}/api/v3/mlb/vk-backfill",
            params={"seasons": "2026", "save_to_db": "false"}
        )
        
        # Should return 200 (success) or 500 (if API rate limited)
        # Not 404 (endpoint not found) or 405 (method not allowed)
        assert response.status_code != 404, "VK backfill endpoint should exist"
        assert response.status_code != 405, "VK backfill endpoint should accept POST"
        
        print(f"✓ VK backfill endpoint exists, status: {response.status_code}")
    
    def test_vk_backfill_validates_seasons(self):
        """Test that VK backfill validates season parameters."""
        # Test with invalid season format
        response = requests.post(
            f"{BASE_URL}/api/v3/mlb/vk-backfill",
            params={"seasons": "invalid", "save_to_db": "false"}
        )
        
        assert response.status_code == 400, "Should return 400 for invalid seasons"
        
        data = response.json()
        assert "detail" in data, "Error response should have 'detail'"
        
        print(f"✓ VK backfill validates seasons parameter")


class TestMLBVKBaselinesEndpoint:
    """Tests for the VK baselines endpoint."""
    
    def test_vk_baselines_endpoint_returns_data(self):
        """Test that VK baselines endpoint returns player baselines."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("success") == True, "Response should indicate success"
        assert "player_name" in data, "Response should contain 'player_name'"
        assert "baselines" in data, "Response should contain 'baselines'"
        assert "total_games" in data, "Response should contain 'total_games'"
        
        print(f"✓ VK baselines endpoint returned data for {data['player_name']}")
    
    def test_vk_baselines_contain_weighted_baseline(self):
        """Test that VK baselines contain weighted_baseline field."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200
        
        data = response.json()
        baselines = data.get("baselines", {})
        
        # Should have at least one stat with baselines
        assert len(baselines) > 0, "Should have at least one stat baseline"
        
        # Check structure of a baseline
        stat_name = list(baselines.keys())[0]
        stat_baseline = baselines[stat_name]
        
        assert "weighted_baseline" in stat_baseline, "Baseline should have 'weighted_baseline'"
        assert "l10_average" in stat_baseline, "Baseline should have 'l10_average'"
        assert "sample_size" in stat_baseline, "Baseline should have 'sample_size'"
        assert "seasons_included" in stat_baseline, "Baseline should have 'seasons_included'"
        
        print(f"✓ VK baselines contain weighted_baseline: {stat_baseline['weighted_baseline']}")
    
    def test_vk_baselines_contain_l10_average(self):
        """Test that VK baselines contain l10_average field."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200
        
        data = response.json()
        baselines = data.get("baselines", {})
        
        # Check hits baseline specifically
        if "hits" in baselines:
            hits_baseline = baselines["hits"]
            assert "l10_average" in hits_baseline, "Hits baseline should have 'l10_average'"
            print(f"✓ VK baselines contain l10_average for hits: {hits_baseline['l10_average']}")
        else:
            # Check any available stat
            stat_name = list(baselines.keys())[0]
            stat_baseline = baselines[stat_name]
            assert "l10_average" in stat_baseline, f"{stat_name} baseline should have 'l10_average'"
            print(f"✓ VK baselines contain l10_average for {stat_name}: {stat_baseline['l10_average']}")
    
    def test_vk_baselines_contain_baseline_vs_l10(self):
        """Test that VK baselines contain baseline_vs_l10 deviation field."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200
        
        data = response.json()
        baselines = data.get("baselines", {})
        
        stat_name = list(baselines.keys())[0]
        stat_baseline = baselines[stat_name]
        
        assert "baseline_vs_l10" in stat_baseline, "Baseline should have 'baseline_vs_l10'"
        
        print(f"✓ VK baselines contain baseline_vs_l10 for {stat_name}: {stat_baseline['baseline_vs_l10']}")
    
    def test_vk_baselines_contain_weighted_cv(self):
        """Test that VK baselines contain weighted_cv (coefficient of variation) field."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200
        
        data = response.json()
        baselines = data.get("baselines", {})
        
        stat_name = list(baselines.keys())[0]
        stat_baseline = baselines[stat_name]
        
        assert "weighted_cv" in stat_baseline, "Baseline should have 'weighted_cv'"
        
        print(f"✓ VK baselines contain weighted_cv for {stat_name}: {stat_baseline['weighted_cv']}")
    
    def test_vk_baselines_404_for_unknown_player(self):
        """Test that VK baselines returns 404 for unknown player."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Unknown%20Player%20XYZ")
        
        assert response.status_code == 404, f"Expected 404 for unknown player, got {response.status_code}"
        
        print(f"✓ VK baselines returns 404 for unknown player")
    
    def test_vk_baselines_batter_stats(self):
        """Test that VK baselines include batter stats for position players."""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/vk-baselines/Mike%20Trout")
        
        assert response.status_code == 200
        
        data = response.json()
        baselines = data.get("baselines", {})
        
        # Mike Trout is a batter, should have batter stats
        batter_stats = ["hits", "total_bases", "rbis", "runs", "stolen_bases", "home_runs", "at_bats", "walks", "strikeouts"]
        
        found_batter_stats = [stat for stat in batter_stats if stat in baselines]
        assert len(found_batter_stats) > 0, f"Should have at least one batter stat baseline, found: {list(baselines.keys())}"
        
        print(f"✓ VK baselines include batter stats: {found_batter_stats}")


class TestMLBDataIntegrity:
    """Tests for MLB data integrity - verifying the P0 blocker fix."""
    
    def test_multiple_players_have_dates(self):
        """Test that multiple MLB players have dates populated in their game logs."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/players", params={"sport": "mlb", "limit": 20})
        
        assert response.status_code == 200
        
        data = response.json()
        players_with_dates = 0
        players_checked = 0
        
        for player in data["players"]:
            game_logs = player.get("bdl_game_logs", [])
            if game_logs:
                players_checked += 1
                first_log = game_logs[0]
                if first_log.get("date") is not None:
                    players_with_dates += 1
        
        if players_checked > 0:
            date_coverage = (players_with_dates / players_checked) * 100
            assert date_coverage >= 90, f"At least 90% of players should have dates, got {date_coverage}%"
            print(f"✓ {players_with_dates}/{players_checked} players have dates populated ({date_coverage:.1f}%)")
        else:
            pytest.skip("No players with game logs found")
    
    def test_multiple_players_have_opponent_abbr(self):
        """Test that multiple MLB players have opponent_abbr populated in their game logs."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/players", params={"sport": "mlb", "limit": 20})
        
        assert response.status_code == 200
        
        data = response.json()
        players_with_opponent = 0
        players_checked = 0
        
        for player in data["players"]:
            game_logs = player.get("bdl_game_logs", [])
            if game_logs:
                players_checked += 1
                first_log = game_logs[0]
                if first_log.get("opponent_abbr") is not None:
                    players_with_opponent += 1
        
        if players_checked > 0:
            opponent_coverage = (players_with_opponent / players_checked) * 100
            assert opponent_coverage >= 90, f"At least 90% of players should have opponent_abbr, got {opponent_coverage}%"
            print(f"✓ {players_with_opponent}/{players_checked} players have opponent_abbr populated ({opponent_coverage:.1f}%)")
        else:
            pytest.skip("No players with game logs found")
    
    def test_vk_baselines_populated_for_multiple_players(self):
        """Test that VK baselines are populated for multiple players after backfill."""
        response = requests.get(f"{BASE_URL}/api/v3/bdl/players", params={"sport": "mlb", "limit": 20})
        
        assert response.status_code == 200
        
        data = response.json()
        players_with_baselines = 0
        
        for player in data["players"]:
            if player.get("vk_baselines") and len(player.get("vk_baselines", {})) > 0:
                players_with_baselines += 1
        
        # After backfill, most players should have baselines
        assert players_with_baselines > 0, "At least some players should have VK baselines"
        
        print(f"✓ {players_with_baselines}/{len(data['players'])} players have VK baselines populated")


# Fixtures
@pytest.fixture
def api_client():
    """Shared requests session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
