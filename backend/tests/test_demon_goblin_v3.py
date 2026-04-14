"""
Test Suite for Demon & Goblin Analytics Engine v3.0
Tests the classification logic for NBA player props:
- STANDARD: Main markets (e.g., player_points) - no icon
- DEMONS: Alternate markets with +100 odds - red icon
- GOBLINS: Alternate markets with odds ≠ +100 - green icon
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://local-first-hub-2.preview.emergentagent.com').rstrip('/')


class TestDemonGoblinStatus:
    """Test /api/v3/status endpoint - verify correct counts"""

    def test_status_returns_success(self):
        """Test that status endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/status")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'data' in data

    def test_status_has_correct_fields(self):
        """Test status response has all required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/status")
        data = response.json()['data']
        
        required_fields = ['last_sync', 'sync_date', 'unique_players', 'total_props', 
                          'standard_count', 'demons_count', 'goblins_count', 'season']
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_status_counts_are_positive(self):
        """Test that counts are positive (data is synced)"""
        response = requests.get(f"{BASE_URL}/api/v3/status")
        data = response.json()['data']
        
        assert data['unique_players'] > 0, "No players synced"
        assert data['total_props'] > 0, "No props synced"
        assert data['standard_count'] >= 0, "Invalid standard count"
        assert data['demons_count'] >= 0, "Invalid demons count"
        assert data['goblins_count'] >= 0, "Invalid goblins count"

    def test_status_counts_sum_to_total(self):
        """Test that standard + demons + goblins = total_props"""
        response = requests.get(f"{BASE_URL}/api/v3/status")
        data = response.json()['data']
        
        calculated_total = data['standard_count'] + data['demons_count'] + data['goblins_count']
        assert calculated_total == data['total_props'], \
            f"Counts don't sum to total: {data['standard_count']} + {data['demons_count']} + {data['goblins_count']} != {data['total_props']}"


class TestPlayerDetail:
    """Test /api/v3/player/{name} endpoint - verify classification by player"""

    def test_player_sga_exists(self):
        """Test that Shai Gilgeous-Alexander player data exists"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'player' in data
        assert data['player']['player_name'] == 'Shai Gilgeous-Alexander'

    def test_player_has_required_fields(self):
        """Test player detail has all required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        player = response.json()['player']
        
        required_fields = ['player_name', 'team', 'position', 'injury_info', 
                          'props', 'standard', 'demons', 'goblins']
        for field in required_fields:
            assert field in player, f"Missing required field: {field}"

    def test_player_has_separate_arrays(self):
        """Test player has demons, goblins, and standard arrays"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        player = response.json()['player']
        
        assert isinstance(player['standard'], list), "standard should be a list"
        assert isinstance(player['demons'], list), "demons should be a list"
        assert isinstance(player['goblins'], list), "goblins should be a list"
        
        # SGA should have props in all three categories
        assert len(player['standard']) > 0, "SGA should have standard props"
        assert len(player['demons']) > 0, "SGA should have demon props"
        assert len(player['goblins']) > 0, "SGA should have goblin props"

    def test_player_standard_props_are_main_market(self):
        """Test that standard props come from main markets (no _alternate)"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        player = response.json()['player']
        
        for prop in player['standard']:
            market = prop.get('market', '')
            assert '_alternate' not in market, f"Standard prop should not have _alternate: {market}"
            assert prop.get('is_alternate_market') is False, "Standard prop is_alternate_market should be False"
            assert prop.get('is_demon') is False, "Standard prop is_demon should be False"
            assert prop.get('is_goblin') is False, "Standard prop is_goblin should be False"
            assert prop.get('prop_type') == 'standard', f"Standard prop type should be 'standard', got {prop.get('prop_type')}"

    def test_player_demon_props_are_alternate_with_100_odds(self):
        """Test that demon props are from alternate markets with +100 odds"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        player = response.json()['player']
        
        for prop in player['demons']:
            market = prop.get('market', '')
            assert '_alternate' in market, f"Demon prop should have _alternate: {market}"
            assert prop.get('is_alternate_market') is True, "Demon prop is_alternate_market should be True"
            assert prop.get('is_demon') is True, "Demon prop is_demon should be True"
            assert prop.get('is_goblin') is False, "Demon prop is_goblin should be False"
            assert prop.get('price') == 100, f"Demon prop price should be 100, got {prop.get('price')}"
            assert prop.get('prop_type') == 'demon', f"Demon prop type should be 'demon', got {prop.get('prop_type')}"

    def test_player_goblin_props_are_alternate_without_100_odds(self):
        """Test that goblin props are from alternate markets with odds ≠ +100"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Shai Gilgeous-Alexander")
        player = response.json()['player']
        
        for prop in player['goblins']:
            market = prop.get('market', '')
            assert '_alternate' in market, f"Goblin prop should have _alternate: {market}"
            assert prop.get('is_alternate_market') is True, "Goblin prop is_alternate_market should be True"
            assert prop.get('is_goblin') is True, "Goblin prop is_goblin should be True"
            assert prop.get('is_demon') is False, "Goblin prop is_demon should be False"
            assert prop.get('price') != 100, f"Goblin prop price should NOT be 100, got {prop.get('price')}"
            assert prop.get('prop_type') == 'goblin', f"Goblin prop type should be 'goblin', got {prop.get('prop_type')}"

    def test_player_not_found_returns_404(self):
        """Test that invalid player returns 404"""
        response = requests.get(f"{BASE_URL}/api/v3/player/Invalid Player Name XYZ")
        assert response.status_code == 404


class TestDemonsEndpoint:
    """Test /api/v3/demons endpoint"""

    def test_demons_endpoint_returns_success(self):
        """Test demons endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/demons")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'demons' in data
        assert 'count' in data

    def test_demons_all_have_100_odds(self):
        """Test all demon props have price == 100"""
        response = requests.get(f"{BASE_URL}/api/v3/demons")
        demons = response.json()['demons']
        
        for demon in demons[:50]:  # Check first 50
            assert demon.get('is_demon') is True, "Demon should have is_demon=True"
            assert demon.get('price') == 100, f"Demon price should be 100, got {demon.get('price')}"

    def test_demons_all_from_alternate_markets(self):
        """Test all demons are from alternate markets"""
        response = requests.get(f"{BASE_URL}/api/v3/demons")
        demons = response.json()['demons']
        
        for demon in demons[:50]:
            market = demon.get('market', '')
            assert '_alternate' in market, f"Demon should be from alternate market: {market}"


class TestGoblinsEndpoint:
    """Test /api/v3/goblins endpoint"""

    def test_goblins_endpoint_returns_success(self):
        """Test goblins endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/goblins")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'goblins' in data
        assert 'count' in data

    def test_goblins_all_have_non_100_odds(self):
        """Test all goblin props have price != 100"""
        response = requests.get(f"{BASE_URL}/api/v3/goblins")
        goblins = response.json()['goblins']
        
        for goblin in goblins[:50]:  # Check first 50
            assert goblin.get('is_goblin') is True, "Goblin should have is_goblin=True"
            assert goblin.get('price') != 100, f"Goblin price should NOT be 100, got {goblin.get('price')}"

    def test_goblins_all_from_alternate_markets(self):
        """Test all goblins are from alternate markets"""
        response = requests.get(f"{BASE_URL}/api/v3/goblins")
        goblins = response.json()['goblins']
        
        for goblin in goblins[:50]:
            market = goblin.get('market', '')
            assert '_alternate' in market, f"Goblin should be from alternate market: {market}"


class TestPlayersListEndpoint:
    """Test /api/v3/players endpoint"""

    def test_players_endpoint_returns_success(self):
        """Test players endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/players")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'players' in data
        assert 'count' in data

    def test_players_have_classification_counts(self):
        """Test players have demon and goblin counts"""
        response = requests.get(f"{BASE_URL}/api/v3/players")
        players = response.json()['players']
        
        for player in players[:10]:
            assert 'demons_count' in player, "Player should have demons_count"
            assert 'goblins_count' in player, "Player should have goblins_count"
            assert isinstance(player['demons_count'], int), "demons_count should be int"
            assert isinstance(player['goblins_count'], int), "goblins_count should be int"


class TestTrendingEndpoint:
    """Test /api/v3/trending endpoint"""

    def test_trending_endpoint_returns_success(self):
        """Test trending endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/trending")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'trending' in data

    def test_trending_returns_up_to_10_players(self):
        """Test trending returns max 10 players"""
        response = requests.get(f"{BASE_URL}/api/v3/trending")
        trending = response.json()['trending']
        assert len(trending) <= 10, "Trending should return max 10 players"

    def test_trending_players_have_special_props(self):
        """Test trending players have at least 1 demon or goblin"""
        response = requests.get(f"{BASE_URL}/api/v3/trending")
        trending = response.json()['trending']
        
        for player in trending:
            demon_count = player.get('demons_count', 0) or 0
            goblin_count = player.get('goblins_count', 0) or 0
            total_special = demon_count + goblin_count
            assert total_special > 0, f"Trending player {player.get('player_name')} should have demons or goblins"


class TestSearchEndpoint:
    """Test /api/v3/search endpoint"""

    def test_search_finds_sga(self):
        """Test search finds Shai Gilgeous-Alexander"""
        response = requests.get(f"{BASE_URL}/api/v3/search", params={"q": "Shai"})
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert len(data['players']) > 0
        player_names = [p['player_name'] for p in data['players']]
        assert any('Shai' in name for name in player_names), "Should find Shai in search results"

    def test_search_empty_query(self):
        """Test search with empty query returns error"""
        response = requests.get(f"{BASE_URL}/api/v3/search")
        # Should return 422 (validation error) for missing required param
        assert response.status_code == 422


class TestBoardEndpoint:
    """Test /api/v3/board endpoint"""

    def test_board_returns_success(self):
        """Test board endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/board")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'players' in data
        assert 'demons_count' in data
        assert 'goblins_count' in data

    def test_board_counts_match_status(self):
        """Test board counts match status counts"""
        status_response = requests.get(f"{BASE_URL}/api/v3/status")
        status_data = status_response.json()['data']
        
        board_response = requests.get(f"{BASE_URL}/api/v3/board")
        board_data = board_response.json()
        
        assert board_data['demons_count'] == status_data['demons_count'], "Demons count mismatch"
        assert board_data['goblins_count'] == status_data['goblins_count'], "Goblins count mismatch"


class TestHybridCachingEndpoints:
    """Test hybrid caching endpoints"""

    def test_static_shell_returns_success(self):
        """Test static-shell endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/static-shell")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True

    def test_live_lines_returns_success(self):
        """Test live-lines endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/live-lines")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'total_demons' in data
        assert 'total_goblins' in data

    def test_hydrated_board_returns_success(self):
        """Test hydrated-board endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/hydrated-board")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
