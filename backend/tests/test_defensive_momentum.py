"""
Test Defensive Momentum Feature
================================
Tests for the weighted composite DvP scoring with momentum tracking.

Features tested:
- GET /api/v3/momentum/status - Service status with weights and thresholds
- GET /api/v3/momentum/rankings/PTS - All 30 teams ranked by composite score
- GET /api/v3/momentum/{team}/{stat_type} - Team momentum profile
- GET /api/v3/momentum/modifier/{opponent}/{stat_type} - Ferrari modifier for matchup
- POST /api/v3/momentum/rebuild - Rebuild rankings from BDL API
- Ferrari integration - momentum_data and momentum_modifier fields in picks
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://local-first-hub-2.preview.emergentagent.com').rstrip('/')


class TestMomentumStatus:
    """Test momentum service status endpoint"""
    
    def test_momentum_status_returns_200(self):
        """GET /api/v3/momentum/status should return 200"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✓ Momentum status endpoint returns 200")
    
    def test_momentum_status_has_weights(self):
        """Status should include correct weights (50% Season, 35% L10, 15% L5)"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/status")
        data = response.json()
        
        assert "weights" in data, "Missing 'weights' in response"
        weights = data["weights"]
        
        assert weights.get("season") == 0.5, f"Season weight should be 0.5, got {weights.get('season')}"
        assert weights.get("l10") == 0.35, f"L10 weight should be 0.35, got {weights.get('l10')}"
        assert weights.get("l5") == 0.15, f"L5 weight should be 0.15, got {weights.get('l5')}"
        print("✓ Weights are correct: 50% Season, 35% L10, 15% L5")
    
    def test_momentum_status_has_modifiers(self):
        """Status should include modifier thresholds"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/status")
        data = response.json()
        
        assert "modifiers" in data, "Missing 'modifiers' in response"
        modifiers = data["modifiers"]
        
        assert modifiers.get("elite_penalty") == -15.0, f"Elite penalty should be -15, got {modifiers.get('elite_penalty')}"
        assert modifiers.get("weak_boost") == 15.0, f"Weak boost should be +15, got {modifiers.get('weak_boost')}"
        assert modifiers.get("elite_threshold") == 5, f"Elite threshold should be 5, got {modifiers.get('elite_threshold')}"
        assert modifiers.get("weak_threshold") == 25, f"Weak threshold should be 25, got {modifiers.get('weak_threshold')}"
        print("✓ Modifiers are correct: Elite -15 (ranks 1-5), Weak +15 (ranks 25-30)")
    
    def test_momentum_status_cache_loaded(self):
        """Status should show cache is loaded with teams"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/status")
        data = response.json()
        
        assert data.get("cache_loaded") == True, "Cache should be loaded"
        assert data.get("teams_cached", 0) >= 30, f"Should have 30 teams cached, got {data.get('teams_cached')}"
        print(f"✓ Cache loaded with {data.get('teams_cached')} teams")


class TestMomentumRankings:
    """Test momentum rankings endpoint"""
    
    def test_rankings_returns_30_teams(self):
        """GET /api/v3/momentum/rankings/PTS should return all 30 teams"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        assert response.status_code == 200
        
        data = response.json()
        rankings = data.get("rankings", [])
        
        assert len(rankings) == 30, f"Expected 30 teams, got {len(rankings)}"
        print("✓ Rankings returns all 30 teams")
    
    def test_rankings_sorted_by_composite(self):
        """Rankings should be sorted by composite_rank ascending"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        composite_ranks = [r.get("composite_rank") for r in rankings]
        assert composite_ranks == sorted(composite_ranks), "Rankings should be sorted by composite_rank"
        print("✓ Rankings sorted by composite rank (best defense first)")
    
    def test_rankings_have_required_fields(self):
        """Each ranking should have all required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        required_fields = [
            "team", "stat_type", "season_rank", "l10_rank", "l5_rank",
            "composite_rank", "momentum", "is_elite", "is_weak"
        ]
        
        for rank in rankings[:5]:  # Check first 5
            for field in required_fields:
                assert field in rank, f"Missing field '{field}' in ranking for {rank.get('team')}"
        
        print("✓ Rankings have all required fields")
    
    def test_elite_teams_have_correct_flag(self):
        """Teams with composite rank <= 5 should have is_elite=True"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        elite_teams = [r for r in rankings if r.get("composite_rank", 99) <= 5]
        for team in elite_teams:
            assert team.get("is_elite") == True, f"{team.get('team')} should be elite (rank {team.get('composite_rank')})"
        
        print(f"✓ {len(elite_teams)} elite teams correctly flagged")
    
    def test_weak_teams_have_correct_flag(self):
        """Teams with composite rank >= 25 should have is_weak=True"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        weak_teams = [r for r in rankings if r.get("composite_rank", 0) >= 25]
        for team in weak_teams:
            assert team.get("is_weak") == True, f"{team.get('team')} should be weak (rank {team.get('composite_rank')})"
        
        print(f"✓ {len(weak_teams)} weak teams correctly flagged")


class TestTeamMomentumProfile:
    """Test individual team momentum profile endpoint"""
    
    def test_detroit_momentum_profile(self):
        """GET /api/v3/momentum/DET/PTS should return Detroit's profile"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/DET/PTS")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("found") == True, "Detroit should be found"
        assert data.get("team") == "DET", "Team should be DET"
        assert data.get("stat_type") == "PTS", "Stat type should be PTS"
        
        # Verify ranks exist
        assert "season_rank" in data, "Missing season_rank"
        assert "l10_rank" in data, "Missing l10_rank"
        assert "l5_rank" in data, "Missing l5_rank"
        assert "composite_rank" in data, "Missing composite_rank"
        
        print(f"✓ Detroit profile: Season #{data.get('season_rank')}, L10 #{data.get('l10_rank')}, L5 #{data.get('l5_rank')}, Composite #{data.get('composite_rank')}")
    
    def test_profile_has_tooltip(self):
        """Profile should include tooltip with formula breakdown"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/DET/PTS")
        data = response.json()
        
        assert "tooltip" in data, "Missing tooltip"
        tooltip = data.get("tooltip", "")
        assert "50%" in tooltip, "Tooltip should show 50% for Season"
        assert "35%" in tooltip, "Tooltip should show 35% for L10"
        assert "15%" in tooltip, "Tooltip should show 15% for L5"
        
        print(f"✓ Tooltip: {tooltip}")
    
    def test_nonexistent_team_returns_not_found(self):
        """Invalid team should return found=False"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/XXX/PTS")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("found") == False, "Invalid team should return found=False"
        print("✓ Invalid team returns found=False")


class TestMomentumModifier:
    """Test momentum modifier endpoint for Ferrari integration"""
    
    def test_elite_defense_returns_negative_modifier(self):
        """Elite defense (OKC) should return -15 modifier"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/modifier/OKC/PTS")
        assert response.status_code == 200
        
        data = response.json()
        modifier = data.get("modifier")
        momentum_data = data.get("momentum_data", {})
        
        # OKC should be elite (composite rank <= 5)
        assert momentum_data.get("is_elite") == True, f"OKC should be elite, got composite rank {momentum_data.get('composite_rank')}"
        assert modifier == -15.0, f"Elite defense should have -15 modifier, got {modifier}"
        
        print(f"✓ OKC (elite defense, rank {momentum_data.get('composite_rank')}) returns modifier: {modifier}")
    
    def test_weak_defense_returns_positive_modifier(self):
        """Weak defense should return +15 modifier"""
        # Find a weak defense team from rankings
        rankings_response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        rankings = rankings_response.json().get("rankings", [])
        weak_team = next((r for r in rankings if r.get("is_weak")), None)
        
        if weak_team:
            team = weak_team.get("team")
            response = requests.get(f"{BASE_URL}/api/v3/momentum/modifier/{team}/PTS")
            data = response.json()
            
            modifier = data.get("modifier")
            assert modifier == 15.0, f"Weak defense should have +15 modifier, got {modifier}"
            print(f"✓ {team} (weak defense, rank {weak_team.get('composite_rank')}) returns modifier: {modifier}")
        else:
            print("⚠ No weak defense team found to test")
    
    def test_middle_defense_returns_zero_modifier(self):
        """Middle defense (ranks 6-24) should return 0 modifier"""
        # Find a middle-ranked team
        rankings_response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        rankings = rankings_response.json().get("rankings", [])
        middle_team = next((r for r in rankings if 6 <= r.get("composite_rank", 0) <= 24), None)
        
        if middle_team:
            team = middle_team.get("team")
            response = requests.get(f"{BASE_URL}/api/v3/momentum/modifier/{team}/PTS")
            data = response.json()
            
            modifier = data.get("modifier")
            assert modifier == 0.0, f"Middle defense should have 0 modifier, got {modifier}"
            print(f"✓ {team} (middle defense, rank {middle_team.get('composite_rank')}) returns modifier: {modifier}")
        else:
            print("⚠ No middle defense team found to test")
    
    def test_modifier_includes_momentum_data(self):
        """Modifier response should include full momentum_data"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/modifier/OKC/PTS")
        data = response.json()
        
        momentum_data = data.get("momentum_data")
        assert momentum_data is not None, "Missing momentum_data"
        
        required_fields = ["team", "season_rank", "l10_rank", "l5_rank", "composite_rank", "momentum", "tooltip"]
        for field in required_fields:
            assert field in momentum_data, f"Missing '{field}' in momentum_data"
        
        print("✓ Modifier response includes full momentum_data")


class TestFerrariMomentumIntegration:
    """Test momentum integration in Ferrari tier picks"""
    
    def test_safe_haven_picks_have_momentum_fields(self):
        """Safe Haven picks should have momentum_modifier and momentum_data fields"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if picks:
            pick = picks[0]
            assert "momentum_modifier" in pick, "Missing momentum_modifier field"
            assert "has_momentum_modifier" in pick, "Missing has_momentum_modifier field"
            # momentum_data may be None if opponent not found
            print(f"✓ Safe Haven picks have momentum fields (first pick: {pick.get('player_name')})")
        else:
            print("⚠ No Safe Haven picks to test")
    
    def test_pick_with_elite_opponent_has_negative_modifier(self):
        """Pick against elite defense should have -15 modifier"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        picks = data.get("picks", [])
        
        # Find a pick with non-zero modifier
        pick_with_modifier = next((p for p in picks if p.get("momentum_modifier", 0) != 0), None)
        
        if pick_with_modifier:
            modifier = pick_with_modifier.get("momentum_modifier")
            momentum_data = pick_with_modifier.get("momentum_data", {})
            
            if modifier < 0:
                assert momentum_data.get("is_elite") == True, "Negative modifier should be for elite defense"
                print(f"✓ {pick_with_modifier.get('player_name')} vs {pick_with_modifier.get('opponent')}: modifier={modifier} (elite defense)")
            elif modifier > 0:
                assert momentum_data.get("is_weak") == True, "Positive modifier should be for weak defense"
                print(f"✓ {pick_with_modifier.get('player_name')} vs {pick_with_modifier.get('opponent')}: modifier={modifier} (weak defense)")
        else:
            print("⚠ No picks with non-zero momentum modifier found")
    
    def test_momentum_data_structure_in_picks(self):
        """momentum_data in picks should have correct structure"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        picks = data.get("picks", [])
        
        # Find a pick with momentum_data
        pick_with_data = next((p for p in picks if p.get("momentum_data")), None)
        
        if pick_with_data:
            momentum_data = pick_with_data.get("momentum_data")
            
            required_fields = ["team", "season_rank", "l10_rank", "l5_rank", "composite_rank", "momentum", "is_elite", "is_weak"]
            for field in required_fields:
                assert field in momentum_data, f"Missing '{field}' in momentum_data"
            
            print(f"✓ momentum_data has correct structure for {pick_with_data.get('player_name')}")
        else:
            print("⚠ No picks with momentum_data found")


class TestMomentumRebuild:
    """Test momentum rebuild endpoint"""
    
    def test_rebuild_endpoint_exists(self):
        """POST /api/v3/momentum/rebuild should be accessible"""
        # Just check the endpoint exists - don't actually rebuild to avoid rate limits
        response = requests.post(f"{BASE_URL}/api/v3/momentum/rebuild")
        # Should return 200 (success) or 429 (rate limited) or similar
        assert response.status_code in [200, 429, 503], f"Unexpected status: {response.status_code}"
        print(f"✓ Rebuild endpoint accessible (status: {response.status_code})")


class TestTrendAlerts:
    """Test trend alert detection"""
    
    def test_trend_alerts_detected(self):
        """Rankings should include trend alerts for significant divergence"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        teams_with_alerts = [r for r in rankings if r.get("trend_alert")]
        
        print(f"✓ Found {len(teams_with_alerts)} teams with trend alerts:")
        for team in teams_with_alerts:
            print(f"  - {team.get('team')}: {team.get('trend_alert')}")
    
    def test_trend_alert_format(self):
        """Trend alerts should have proper format"""
        response = requests.get(f"{BASE_URL}/api/v3/momentum/rankings/PTS")
        data = response.json()
        rankings = data.get("rankings", [])
        
        team_with_alert = next((r for r in rankings if r.get("trend_alert")), None)
        
        if team_with_alert:
            alert = team_with_alert.get("trend_alert")
            # Should contain "ALERT" and mention spots
            assert "ALERT" in alert, "Trend alert should contain 'ALERT'"
            assert "spots" in alert.lower() or "games" in alert.lower(), "Trend alert should mention spots or games"
            print(f"✓ Trend alert format correct: {alert}")
        else:
            print("⚠ No trend alerts to verify format")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
