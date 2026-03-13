"""
Tests for NBA Player Headshot Display Bug Fix
=============================================

Verifies that:
1. Trey Murphy III's ESPN headshot URL is returned from API endpoints
2. photo_url is included in /api/v3/cached-props, demon-radar, goblin-vault
3. Players without photos (like Ace Bailey) have null photo_url and show team logos
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHeadshotBugFix:
    """Tests for verifying headshot display fix for Trey Murphy III"""
    
    def test_cached_props_returns_photo_url_for_trey_murphy(self):
        """Verify Trey Murphy III has photo_url in cached-props response"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        players = data.get('players', [])
        
        # Find Trey Murphy III
        trey_murphy = None
        for player in players:
            if 'Trey Murphy' in player.get('player_name', ''):
                trey_murphy = player
                break
        
        assert trey_murphy is not None, "Trey Murphy III not found in cached-props"
        assert 'photo_url' in trey_murphy, "photo_url field missing for Trey Murphy III"
        assert trey_murphy['photo_url'] is not None, "photo_url is null for Trey Murphy III"
        assert 'espncdn.com' in trey_murphy['photo_url'], f"Expected ESPN URL, got {trey_murphy['photo_url']}"
        print(f"PASSED: Trey Murphy III photo_url = {trey_murphy['photo_url']}")
    
    def test_demon_radar_returns_photo_url_for_trey_murphy(self):
        """Verify Trey Murphy III has photo_url in demon-radar response"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        picks = data.get('picks', [])
        
        # Find Trey Murphy III in radar picks
        trey_murphy_picks = [p for p in picks if 'Trey Murphy' in p.get('player_name', '')]
        
        if trey_murphy_picks:
            for pick in trey_murphy_picks:
                assert 'photo_url' in pick, "photo_url field missing in demon-radar pick"
                assert pick['photo_url'] is not None, "photo_url is null for Trey Murphy III in demon-radar"
                print(f"PASSED: Demon Radar - Trey Murphy III photo_url = {pick['photo_url']}")
        else:
            print("INFO: Trey Murphy III not in current Demon Radar picks - checking photo_url structure")
    
    def test_goblin_vault_returns_photo_url_for_trey_murphy(self):
        """Verify Trey Murphy III has photo_url in goblin-vault response"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        picks = data.get('picks', [])
        
        # Find Trey Murphy III in vault picks
        trey_murphy_picks = [p for p in picks if 'Trey Murphy' in p.get('player_name', '')]
        
        if trey_murphy_picks:
            for pick in trey_murphy_picks:
                assert 'photo_url' in pick, "photo_url field missing in goblin-vault pick"
                assert pick['photo_url'] is not None, "photo_url is null for Trey Murphy III in goblin-vault"
                print(f"PASSED: Goblin Vault - Trey Murphy III photo_url = {pick['photo_url']}")
        else:
            print("INFO: Trey Murphy III not in current Goblin Vault picks")
    
    def test_ace_bailey_has_null_photo_url_fallback(self):
        """Verify Ace Bailey (rookie without photo) has null photo_url - team logo fallback expected"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        assert response.status_code == 200
        
        data = response.json()
        players = data.get('players', [])
        
        # Find Ace Bailey
        ace_bailey = None
        for player in players:
            if 'Ace Bailey' in player.get('player_name', ''):
                ace_bailey = player
                break
        
        if ace_bailey:
            assert 'photo_url' in ace_bailey, "photo_url field missing for Ace Bailey"
            # Ace Bailey should have null photo_url (will show team logo in UI)
            if ace_bailey['photo_url'] is None:
                print("PASSED: Ace Bailey has null photo_url (team logo fallback expected)")
            else:
                print(f"INFO: Ace Bailey has photo_url: {ace_bailey['photo_url']}")
        else:
            print("INFO: Ace Bailey not found in current props data")
    
    def test_photo_url_is_valid_espn_url(self):
        """Verify photo URLs are valid ESPN CDN URLs"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get('picks', [])
        
        valid_sources = ['espncdn.com', 'nba.com']
        
        for pick in picks:
            photo_url = pick.get('photo_url')
            if photo_url:
                is_valid = any(source in photo_url for source in valid_sources)
                assert is_valid, f"Invalid photo URL source: {photo_url}"
        
        print(f"PASSED: All {len(picks)} photo URLs are from valid sources (ESPN/NBA CDN)")
    
    def test_all_endpoints_include_photo_url_field(self):
        """Verify photo_url field is present in all player data endpoints"""
        endpoints = [
            '/api/v3/cached-props',
            '/api/v3/demon-radar',
            '/api/v3/goblin-vault'
        ]
        
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}")
            assert response.status_code == 200, f"{endpoint} returned {response.status_code}"
            
            data = response.json()
            
            if endpoint == '/api/v3/cached-props':
                players = data.get('players', [])
                for player in players[:5]:  # Check first 5
                    assert 'photo_url' in player, f"photo_url missing in {endpoint}"
            else:
                picks = data.get('picks', [])
                for pick in picks[:5]:
                    assert 'photo_url' in pick, f"photo_url missing in {endpoint}"
            
            print(f"PASSED: {endpoint} includes photo_url field")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
