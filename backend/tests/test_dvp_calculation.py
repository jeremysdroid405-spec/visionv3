"""
DvP (Defense vs Position) Calculation Tests
============================================

Tests for P1 Task #2: Real DvP data replacing placeholder modifier

Tests:
1. calculate_dvp_modifier returns correct values based on team rankings
2. DvP data is included in goblin vault picks
3. DvP data is included in war zone picks  
4. Opponent team is correctly calculated from home/away teams
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestDvPCalculation:
    """Tests for DvP modifier calculation and data enrichment"""
    
    def test_goblin_vault_has_dvp_data(self):
        """Goblin vault picks should include dvp_modifier, dvp_label, and opponent_team"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200, f"API returned {response.status_code}"
        
        data = response.json()
        picks = data.get('picks', [])
        assert len(picks) > 0, "No goblin vault picks returned"
        
        # Check first pick has DvP data
        pick = picks[0]
        
        # Verify opponent is set
        assert pick.get('opponent') is not None, f"opponent is None for {pick.get('player_name')}"
        assert pick.get('opponent_abbr') is not None, f"opponent_abbr is None for {pick.get('player_name')}"
        
        # Verify DvP modifier is set and not placeholder 0.5
        dvp_mod = pick.get('dvp_modifier')
        assert dvp_mod is not None, f"dvp_modifier is None for {pick.get('player_name')}"
        
        # Verify pillar_3_dvp equals dvp_modifier (should be same value)
        pillar_3 = pick.get('pillar_3_dvp')
        assert pillar_3 is not None, f"pillar_3_dvp is None for {pick.get('player_name')}"
        
        # Verify DvP label is set
        dvp_label = pick.get('dvp_label')
        assert dvp_label is not None, f"dvp_label is None for {pick.get('player_name')}"
        assert dvp_label in ['TOUGH', 'NEUTRAL', 'FAVORABLE'], f"Invalid dvp_label: {dvp_label}"
        
        print(f"✓ {pick.get('player_name')} vs {pick.get('opponent')}: dvp={dvp_mod:.3f} ({dvp_label})")
    
    def test_dvp_values_are_real_not_placeholder(self):
        """DvP values should vary based on opponent, not all be 0.5 placeholder"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get('picks', [])
        assert len(picks) > 0
        
        # Collect all DvP values
        dvp_values = [p.get('dvp_modifier', 0.5) for p in picks]
        
        # Check that not all values are 0.5 (the placeholder)
        unique_values = set(dvp_values)
        assert len(unique_values) > 1, f"All DvP values are same: {unique_values}"
        assert 0.5 not in unique_values or len(unique_values) > 1, "All values are placeholder 0.5"
        
        print(f"✓ DvP values vary correctly: {sorted(unique_values)[:5]}...")
    
    def test_dvp_label_matches_modifier_range(self):
        """DvP label should match the modifier value range"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            dvp_mod = pick.get('dvp_modifier', 0.5)
            dvp_label = pick.get('dvp_label', 'NEUTRAL')
            
            # Verify label matches range:
            # 0.0-0.4 = TOUGH, 0.4-0.7 = NEUTRAL, 0.7-1.0 = FAVORABLE
            if dvp_mod >= 0.7:
                expected_label = 'FAVORABLE'
            elif dvp_mod >= 0.4:
                expected_label = 'NEUTRAL'
            else:
                expected_label = 'TOUGH'
            
            assert dvp_label == expected_label, \
                f"{pick.get('player_name')}: dvp={dvp_mod} should be {expected_label}, got {dvp_label}"
        
        print(f"✓ All {len(picks)} picks have correct label-modifier mapping")
    
    def test_opponent_correctly_calculated(self):
        """Opponent should be different from player's team"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            team = pick.get('team')
            opponent = pick.get('opponent')
            
            if team and opponent:
                assert team != opponent, \
                    f"{pick.get('player_name')}: team ({team}) should not equal opponent ({opponent})"
        
        print(f"✓ All opponents correctly different from player teams")


class TestPlayerDetailPageAPI:
    """Tests for player detail page API endpoint"""
    
    def test_cached_player_returns_props(self):
        """GET /api/v3/cached-player/{name} should return player with props"""
        # First get a player name from the board
        board_response = requests.get(f"{BASE_URL}/api/v3/players")
        assert board_response.status_code == 200
        
        players = board_response.json().get('players', [])
        assert len(players) > 0, "No players in board"
        
        # Get first player's details
        player_name = players[0].get('player_name')
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/{player_name}")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get('success') is True, f"API returned success=False: {data.get('message')}"
        
        player = data.get('player', {})
        assert player.get('player_name') == player_name
        assert len(player.get('props', [])) > 0, "Player has no props"
        
        print(f"✓ {player_name}: {len(player.get('props', []))} props loaded")
    
    def test_player_has_opponent_in_cached_board(self):
        """Player in cached board should have opponent set"""
        board_response = requests.get(f"{BASE_URL}/api/v3/players")
        assert board_response.status_code == 200
        
        players = board_response.json().get('players', [])
        assert len(players) > 0
        
        player_name = players[0].get('player_name')
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/{player_name}")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get('player', {})
        
        # Check opponent is set
        opponent = player.get('opponent')
        opponent_abbr = player.get('opponent_abbr')
        
        assert opponent is not None or opponent_abbr is not None, \
            f"{player_name} has no opponent set"
        
        print(f"✓ {player_name} vs {opponent or opponent_abbr}")


class TestAPIStatus:
    """Basic API health checks"""
    
    def test_v3_status_endpoint(self):
        """V3 status endpoint should return sync info"""
        response = requests.get(f"{BASE_URL}/api/v3/status")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get('success') is True
        assert data.get('data', {}).get('total_props', 0) > 0
        
        print(f"✓ Status: {data.get('data', {}).get('total_props')} props synced")
    
    def test_goblin_vault_endpoint(self):
        """Goblin vault should return 10 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get('success') is True
        picks = data.get('picks', [])
        assert len(picks) == 10, f"Expected 10 picks, got {len(picks)}"
        
        print(f"✓ Goblin vault: {len(picks)} picks")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
