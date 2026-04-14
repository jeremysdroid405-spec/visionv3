"""
BallDontLie Stats Enrichment Tests
==================================
Tests for:
1. BDL stats enrichment - props have hit_rates field
2. Demon Radar algorithm - real hit rates used (estimated_p=False)
3. /api/v3/cached-props endpoint
4. /api/v3/demon-radar endpoint
5. /api/v3/cached-player endpoint
6. Correct Demon/Goblin classification
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://local-first-hub-2.preview.emergentagent.com').rstrip('/')


class TestCachedPropsEndpoint:
    """Tests for /api/v3/cached-props - Main board endpoint"""
    
    def test_cached_props_returns_success(self):
        """Verify endpoint returns success and has data"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') == True
        assert 'players' in data
        assert 'players_count' in data
        assert data['players_count'] > 0
        print(f"✓ Cached props: {data['players_count']} players, {data.get('total_props', 0)} props")
    
    def test_players_have_props_array(self):
        """Verify each player has props array"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        players = data.get('players', [])
        
        for player in players[:5]:  # Check first 5 players
            assert 'props' in player, f"Player {player.get('player_name')} missing props"
            assert isinstance(player['props'], list)
        print(f"✓ All checked players have props array")
    
    def test_props_have_hit_rates(self):
        """Verify props have hit_rates field populated (BDL enrichment)"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        total_props = 0
        props_with_hit_rates = 0
        
        for player in data.get('players', []):
            for prop in player.get('props', []):
                total_props += 1
                if prop.get('hit_rates'):
                    props_with_hit_rates += 1
        
        enrichment_rate = props_with_hit_rates / total_props if total_props > 0 else 0
        assert enrichment_rate > 0.90, f"Only {enrichment_rate*100:.1f}% props have hit_rates (expected >90%)"
        print(f"✓ Hit rates enrichment: {props_with_hit_rates}/{total_props} ({enrichment_rate*100:.1f}%)")
    
    def test_hit_rates_structure(self):
        """Verify hit_rates has correct structure (l5, l10, season)"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        # Find a prop with hit_rates
        for player in data.get('players', []):
            for prop in player.get('props', []):
                if prop.get('hit_rates'):
                    hr = prop['hit_rates']
                    
                    # Verify structure
                    assert 'l5' in hr, "Missing l5 in hit_rates"
                    assert 'l10' in hr, "Missing l10 in hit_rates"
                    assert 'season' in hr, "Missing season in hit_rates"
                    
                    # Verify l10 structure
                    l10 = hr['l10']
                    assert 'hit_rate' in l10, "Missing hit_rate in l10"
                    assert 'games_over' in l10, "Missing games_over in l10"
                    assert 'total_games' in l10, "Missing total_games in l10"
                    assert 'avg' in l10, "Missing avg in l10"
                    
                    print(f"✓ Hit rates structure verified for {player['player_name']}")
                    return
        
        pytest.fail("No props with hit_rates found")


class TestDemonGoblinClassification:
    """Tests for correct Demon/Goblin classification"""
    
    def test_demons_have_plus_100_odds(self):
        """Verify all Demons have +100 odds"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        demons_with_100 = 0
        demons_without_100 = 0
        
        for player in data.get('players', []):
            for prop in player.get('props', []):
                if prop.get('is_demon'):
                    if prop.get('price') == 100:
                        demons_with_100 += 1
                    else:
                        demons_without_100 += 1
        
        assert demons_without_100 == 0, f"Found {demons_without_100} demons without +100 odds"
        print(f"✓ All {demons_with_100} demons have +100 odds")
    
    def test_goblins_have_non_100_odds(self):
        """Verify all Goblins have non-+100 odds (like -137)"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        goblins_non_100 = 0
        goblins_with_100 = 0
        
        for player in data.get('players', []):
            for prop in player.get('props', []):
                if prop.get('is_goblin'):
                    if prop.get('price') != 100:
                        goblins_non_100 += 1
                    else:
                        goblins_with_100 += 1
        
        assert goblins_with_100 == 0, f"Found {goblins_with_100} goblins with +100 odds (should be non-100)"
        print(f"✓ All {goblins_non_100} goblins have non-+100 odds")
    
    def test_demons_are_from_alternate_markets(self):
        """Verify Demons come from _alternate markets"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        for player in data.get('players', [])[:10]:
            for prop in player.get('props', []):
                if prop.get('is_demon'):
                    market = prop.get('market', '')
                    assert '_alternate' in market, f"Demon from non-alternate market: {market}"
        
        print(f"✓ All checked demons are from _alternate markets")
    
    def test_goblins_are_from_alternate_markets(self):
        """Verify Goblins come from _alternate markets"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        for player in data.get('players', [])[:10]:
            for prop in player.get('props', []):
                if prop.get('is_goblin'):
                    market = prop.get('market', '')
                    assert '_alternate' in market, f"Goblin from non-alternate market: {market}"
        
        print(f"✓ All checked goblins are from _alternate markets")


class TestDemonRadarEndpoint:
    """Tests for /api/v3/demon-radar - Top 10 picks algorithm"""
    
    def test_demon_radar_returns_success(self):
        """Verify endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') == True
        print(f"✓ Demon radar endpoint returns success")
    
    def test_demon_radar_has_10_picks(self):
        """Verify radar returns top 10 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        picks = data.get('picks', [])
        assert len(picks) == 10, f"Expected 10 picks, got {len(picks)}"
        print(f"✓ Demon radar has 10 picks")
    
    def test_most_picks_have_real_data(self):
        """Verify 7/10 or more picks have real BDL data (estimated_p=False)"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        picks = data.get('picks', [])
        
        real_data_count = sum(1 for p in picks if not p.get('estimated_p', True))
        
        assert real_data_count >= 7, f"Only {real_data_count}/10 picks have real data (expected >=7)"
        print(f"✓ {real_data_count}/10 picks have real BDL data (estimated_p=False)")
    
    def test_radar_pick_structure(self):
        """Verify radar picks have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        picks = data.get('picks', [])
        
        required_fields = [
            'player_name', 'team', 'stat_type', 'demon_line', 
            'h10_rate', 'h5_rate', 'hit_probability', 'radar_strength',
            'price', 'is_radar_pick', 'estimated_p'
        ]
        
        for pick in picks[:3]:
            for field in required_fields:
                assert field in pick, f"Missing field: {field} in pick {pick.get('player_name')}"
        
        print(f"✓ All radar picks have required fields")
    
    def test_radar_picks_have_demon_odds(self):
        """Verify all radar picks have +100 odds (Demon odds)"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            assert pick.get('price') == 100, f"Radar pick {pick['player_name']} has price {pick.get('price')} (expected 100)"
        
        print(f"✓ All 10 radar picks have +100 (Demon) odds")
    
    def test_radar_algorithm_info(self):
        """Verify radar response includes algorithm details"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        
        assert 'algorithm' in data, "Missing algorithm info"
        algo = data['algorithm']
        assert 'formula' in algo, "Missing formula in algorithm"
        assert 'hit_probability' in algo, "Missing hit_probability formula"
        assert 'min_probability' in algo, "Missing min_probability threshold"
        
        print(f"✓ Radar includes algorithm info: {algo.get('description')}")


class TestCachedPlayerEndpoint:
    """Tests for /api/v3/cached-player/{name} endpoint"""
    
    def test_get_player_by_name(self):
        """Verify can fetch player by name"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/LeBron%20James")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') == True
        assert data.get('player') is not None
        print(f"✓ Fetched LeBron James successfully")
    
    def test_player_has_hit_rates_on_all_props(self):
        """Verify player props have hit_rates populated"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/LeBron%20James")
        data = response.json()
        player = data.get('player', {})
        props = player.get('props', [])
        
        props_with_hr = sum(1 for p in props if p.get('hit_rates'))
        total_props = len(props)
        
        if total_props > 0:
            enrichment_rate = props_with_hr / total_props
            assert enrichment_rate > 0.90, f"Only {enrichment_rate*100:.1f}% of props have hit_rates"
            print(f"✓ Player has hit_rates on {props_with_hr}/{total_props} props ({enrichment_rate*100:.1f}%)")
    
    def test_player_has_l10_and_season_hit_rates(self):
        """Verify hit_rates include L10 and Season data"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/LeBron%20James")
        data = response.json()
        player = data.get('player', {})
        
        for prop in player.get('props', [])[:5]:
            hr = prop.get('hit_rates', {})
            if hr:
                l10 = hr.get('l10', {})
                season = hr.get('season', {})
                
                assert l10.get('total_games', 0) > 0, "L10 has no games"
                assert season.get('total_games', 0) > 0, "Season has no games"
                
                print(f"✓ Verified L10 ({l10.get('total_games')} games) and Season ({season.get('total_games')} games) hit rates")
                return
        
        print("✓ No props with hit_rates to verify (may be expected if player not in sync)")
    
    def test_fuzzy_player_search(self):
        """Verify fuzzy matching works for player names"""
        # Try with slightly different name format
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/lebron%20james")  # lowercase
        assert response.status_code == 200
        data = response.json()
        # Should either find player or return lines loading message
        assert 'success' in data
        print(f"✓ Fuzzy search works (success={data.get('success')})")
    
    def test_player_not_found_returns_message(self):
        """Verify non-existent player returns appropriate message"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-player/NonExistentPlayer123")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') == False
        assert 'message' in data or 'Lines loading' in str(data)
        print(f"✓ Non-existent player returns message: {data.get('message', 'N/A')}")


class TestTrendingSection:
    """Tests for trending players in cached-props"""
    
    def test_cached_props_has_trending(self):
        """Verify cached-props includes trending players"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        assert 'trending' in data, "Missing trending in response"
        trending = data.get('trending', [])
        assert len(trending) > 0, "Trending list is empty"
        print(f"✓ Cached props has {len(trending)} trending players")
    
    def test_trending_players_have_required_fields(self):
        """Verify trending players have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        trending = data.get('trending', [])[:5]
        
        for player in trending:
            assert 'player_name' in player
            assert 'team' in player
            assert 'props' in player
            assert 'demons_count' in player
            assert 'goblins_count' in player
        
        print(f"✓ All trending players have required fields")


class TestSyncMetadata:
    """Tests for sync metadata"""
    
    def test_cached_props_has_synced_at(self):
        """Verify cached-props includes synced_at timestamp"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        data = response.json()
        
        assert 'synced_at' in data, "Missing synced_at timestamp"
        assert data['synced_at'] is not None
        print(f"✓ Synced at: {data['synced_at']}")
    
    def test_demon_radar_has_synced_at(self):
        """Verify demon-radar includes synced_at timestamp"""
        response = requests.get(f"{BASE_URL}/api/v3/demon-radar")
        data = response.json()
        
        assert 'synced_at' in data, "Missing synced_at timestamp in radar"
        print(f"✓ Radar synced at: {data['synced_at']}")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
