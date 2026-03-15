"""
Test Suite for AI Confidence Rating and PickCard Data Points
============================================================

Tests that all three pick endpoints return required fields:
- ai_confidence_rating (0-100)
- h5_rate, h5_over, h5_games (L5 hit rate data)
- h10_rate, h10_over, h10_games (L10 hit rate data)
- season_avg (season average)

These fields are used by the PickCard component on the frontend.
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestWarZoneEndpoint:
    """Tests for /api/v3/war-zone endpoint - Demon picks section"""
    
    def test_war_zone_returns_success(self):
        """Verify war-zone endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') is True
        print(f"War Zone - SUCCESS: {data.get('picks_count')} picks returned")
    
    def test_war_zone_has_ai_confidence_rating(self):
        """Verify all war-zone picks have ai_confidence_rating field"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            assert 'ai_confidence_rating' in pick, f"Pick {pick.get('player_name')} missing ai_confidence_rating"
            rating = pick.get('ai_confidence_rating')
            assert isinstance(rating, (int, float)), f"ai_confidence_rating should be numeric, got {type(rating)}"
            assert 0 <= rating <= 100, f"ai_confidence_rating should be 0-100, got {rating}"
            print(f"  {pick.get('player_name')}: ai_confidence_rating={rating}")
        
        print(f"War Zone - PASSED: All {len(picks)} picks have valid ai_confidence_rating")
    
    def test_war_zone_has_l5_data(self):
        """Verify all war-zone picks have L5 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h5_rate' in pick, f"{player} missing h5_rate"
            assert 'h5_over' in pick, f"{player} missing h5_over"
            assert 'h5_games' in pick, f"{player} missing h5_games"
        
        print(f"War Zone - PASSED: All {len(picks)} picks have L5 data (h5_rate, h5_over, h5_games)")
    
    def test_war_zone_has_l10_data(self):
        """Verify all war-zone picks have L10 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h10_rate' in pick, f"{player} missing h10_rate"
            assert 'h10_over' in pick, f"{player} missing h10_over"
            assert 'h10_games' in pick, f"{player} missing h10_games"
        
        print(f"War Zone - PASSED: All {len(picks)} picks have L10 data (h10_rate, h10_over, h10_games)")
    
    def test_war_zone_has_season_avg(self):
        """Verify all war-zone picks have season_avg"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'season_avg' in pick, f"{player} missing season_avg"
        
        print(f"War Zone - PASSED: All {len(picks)} picks have season_avg")


class TestGoblinVaultEndpoint:
    """Tests for /api/v3/goblin-vault endpoint - Safe Haven picks section"""
    
    def test_goblin_vault_returns_success(self):
        """Verify goblin-vault endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') is True
        print(f"Goblin Vault - SUCCESS: {data.get('picks_count')} picks returned")
    
    def test_goblin_vault_has_ai_confidence_rating(self):
        """Verify all goblin-vault picks have ai_confidence_rating field"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            assert 'ai_confidence_rating' in pick, f"Pick {pick.get('player_name')} missing ai_confidence_rating"
            rating = pick.get('ai_confidence_rating')
            assert isinstance(rating, (int, float)), f"ai_confidence_rating should be numeric, got {type(rating)}"
            assert 0 <= rating <= 100, f"ai_confidence_rating should be 0-100, got {rating}"
            print(f"  {pick.get('player_name')}: ai_confidence_rating={rating}")
        
        print(f"Goblin Vault - PASSED: All {len(picks)} picks have valid ai_confidence_rating")
    
    def test_goblin_vault_has_l5_data(self):
        """Verify all goblin-vault picks have L5 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h5_rate' in pick, f"{player} missing h5_rate"
            assert 'h5_over' in pick, f"{player} missing h5_over"
            assert 'h5_games' in pick, f"{player} missing h5_games"
        
        print(f"Goblin Vault - PASSED: All {len(picks)} picks have L5 data")
    
    def test_goblin_vault_has_l10_data(self):
        """Verify all goblin-vault picks have L10 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h10_rate' in pick, f"{player} missing h10_rate"
            assert 'h10_over' in pick, f"{player} missing h10_over"
            assert 'h10_games' in pick, f"{player} missing h10_games"
        
        print(f"Goblin Vault - PASSED: All {len(picks)} picks have L10 data")
    
    def test_goblin_vault_has_season_avg(self):
        """Verify all goblin-vault picks have season_avg"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'season_avg' in pick, f"{player} missing season_avg"
        
        print(f"Goblin Vault - PASSED: All {len(picks)} picks have season_avg")


class TestFrontLinesEndpoint:
    """Tests for /api/v3/front-lines endpoint - Mixed picks section"""
    
    def test_front_lines_returns_success(self):
        """Verify front-lines endpoint returns success"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        data = response.json()
        assert data.get('success') is True
        print(f"Front Lines - SUCCESS: {data.get('picks_count')} picks returned")
    
    def test_front_lines_has_ai_confidence_rating(self):
        """Verify all front-lines picks have ai_confidence_rating field"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            assert 'ai_confidence_rating' in pick, f"Pick {pick.get('player_name')} missing ai_confidence_rating"
            rating = pick.get('ai_confidence_rating')
            assert isinstance(rating, (int, float)), f"ai_confidence_rating should be numeric, got {type(rating)}"
            assert 0 <= rating <= 100, f"ai_confidence_rating should be 0-100, got {rating}"
            print(f"  {pick.get('player_name')}: ai_confidence_rating={rating}")
        
        print(f"Front Lines - PASSED: All {len(picks)} picks have valid ai_confidence_rating")
    
    def test_front_lines_has_l5_data(self):
        """Verify all front-lines picks have L5 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h5_rate' in pick, f"{player} missing h5_rate"
            assert 'h5_over' in pick, f"{player} missing h5_over"
            assert 'h5_games' in pick, f"{player} missing h5_games"
        
        print(f"Front Lines - PASSED: All {len(picks)} picks have L5 data")
    
    def test_front_lines_has_l10_data(self):
        """Verify all front-lines picks have L10 hit rate data"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'h10_rate' in pick, f"{player} missing h10_rate"
            assert 'h10_over' in pick, f"{player} missing h10_over"
            assert 'h10_games' in pick, f"{player} missing h10_games"
        
        print(f"Front Lines - PASSED: All {len(picks)} picks have L10 data")
    
    def test_front_lines_has_season_avg(self):
        """Verify all front-lines picks have season_avg"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        data = response.json()
        picks = data.get('picks', [])
        
        for pick in picks:
            player = pick.get('player_name')
            assert 'season_avg' in pick, f"{player} missing season_avg"
        
        print(f"Front Lines - PASSED: All {len(picks)} picks have season_avg")


class TestAIConfidenceRatingValues:
    """Test AI Confidence Rating value ranges and distribution"""
    
    def test_ai_confidence_distribution(self):
        """Check AI confidence rating distribution across all picks"""
        all_ratings = []
        
        for endpoint in ['/api/v3/war-zone', '/api/v3/goblin-vault', '/api/v3/front-lines']:
            response = requests.get(f"{BASE_URL}{endpoint}")
            assert response.status_code == 200
            data = response.json()
            picks = data.get('picks', [])
            for pick in picks:
                all_ratings.append(pick.get('ai_confidence_rating', 0))
        
        # Distribution analysis
        high_confidence = len([r for r in all_ratings if r >= 80])  # Green
        medium_confidence = len([r for r in all_ratings if 60 <= r < 80])  # Purple
        low_medium_confidence = len([r for r in all_ratings if 40 <= r < 60])  # Yellow
        low_confidence = len([r for r in all_ratings if r < 40])  # Red
        
        print(f"\nAI Confidence Distribution (Total: {len(all_ratings)} picks):")
        print(f"  High (>=80%, green): {high_confidence} picks")
        print(f"  Medium (60-79%, purple): {medium_confidence} picks")
        print(f"  Low-Medium (40-59%, yellow): {low_medium_confidence} picks")
        print(f"  Low (<40%, red): {low_confidence} picks")
        
        # Verify we have some distribution (not all same value)
        unique_values = set(all_ratings)
        assert len(unique_values) >= 2, "AI confidence ratings should have some variance"
        print(f"\nUnique confidence values: {sorted(unique_values)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
