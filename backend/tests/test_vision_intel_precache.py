"""
Vision Intel Pre-Cache & Batch Intelligence Enrichment Tests
=============================================================
Tests for the Vision Intel Suite pre-caching feature that eliminates
1+ minute JIT Gemini AI calls by pre-computing AI summaries.

Test Coverage:
1. /api/v3/player-with-badges/{player_name} responds in <2 seconds
2. /api/v3/war-zone returns picks with is_demon=true
3. /api/v3/goblin-vault (Safe Haven) returns picks with is_goblin=true
4. /api/v3/front-lines returns picks
5. Vision Intel endpoint serves from MongoDB (no JIT Gemini calls)
6. Backend starts without errors
"""

import pytest
import requests
import os
import time
from typing import Dict, Any

# Get BASE_URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://best-bet-finder-1.preview.emergentagent.com').rstrip('/')


class TestBackendHealth:
    """Test that backend is running and healthy"""
    
    def test_backend_status(self):
        """Backend should respond to status endpoint"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        assert response.status_code == 200, f"Status endpoint failed: {response.status_code}"
        data = response.json()
        assert "status" in data or "success" in data, "Status response missing expected fields"
        print(f"Backend status: {data}")


class TestPlayerWithBadgesPerformance:
    """Test /api/v3/player-with-badges/{player_name} performance - should be <2 seconds"""
    
    def test_player_with_badges_response_time_joel_embiid(self):
        """Joel Embiid player-with-badges should respond in <2 seconds"""
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Joel%20Embiid", timeout=10)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Request failed: {response.status_code}"
        assert elapsed < 2.0, f"Response time {elapsed:.2f}s exceeds 2 second limit"
        
        data = response.json()
        assert data.get("success") == True, "Response should indicate success"
        assert "player" in data, "Response should contain player data"
        
        print(f"Joel Embiid response time: {elapsed:.3f}s (PASS: <2s)")
    
    def test_player_with_badges_response_time_lamelo_ball(self):
        """LaMelo Ball player-with-badges should respond in <2 seconds"""
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball", timeout=10)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Request failed: {response.status_code}"
        assert elapsed < 2.0, f"Response time {elapsed:.2f}s exceeds 2 second limit"
        
        print(f"LaMelo Ball response time: {elapsed:.3f}s (PASS: <2s)")
    
    def test_player_with_badges_response_time_kyle_filipowski(self):
        """Kyle Filipowski player-with-badges should respond in <2 seconds"""
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Kyle%20Filipowski", timeout=10)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Request failed: {response.status_code}"
        assert elapsed < 2.0, f"Response time {elapsed:.2f}s exceeds 2 second limit"
        
        print(f"Kyle Filipowski response time: {elapsed:.3f}s (PASS: <2s)")
    
    def test_player_with_badges_has_intel_suite(self):
        """Player with badges should include intel_suite for featured props"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Joel%20Embiid", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        props = player.get("props", [])
        
        # Check if any prop has intel_suite (featured props should have it)
        props_with_intel = [p for p in props if p.get("intel_suite")]
        
        # At least one prop should have intel_suite if player is on a tier board
        print(f"Props with intel_suite: {len(props_with_intel)}/{len(props)}")
        
        if props_with_intel:
            intel = props_with_intel[0]["intel_suite"]
            assert "matchup_dvp" in intel, "intel_suite should have matchup_dvp"
            assert "pace_delta" in intel, "intel_suite should have pace_delta"
            assert "stability_index" in intel, "intel_suite should have stability_index"
            assert "vision_insight" in intel, "intel_suite should have vision_insight"
            print(f"Intel suite keys: {list(intel.keys())}")


class TestWarZoneEndpoint:
    """Test /api/v3/war-zone returns demon picks"""
    
    def test_war_zone_returns_picks(self):
        """War Zone should return picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=10)
        assert response.status_code == 200, f"War Zone failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        assert len(picks) > 0, "War Zone should have picks"
        print(f"War Zone picks count: {len(picks)}")
    
    def test_war_zone_picks_are_demons(self):
        """War Zone picks should have is_demon=true"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        demons = [p for p in picks if p.get("is_demon") == True]
        assert len(demons) > 0, "War Zone should have demon picks"
        
        # All picks in War Zone should be demons
        non_demons = [p for p in picks if p.get("is_demon") != True]
        assert len(non_demons) == 0, f"War Zone has {len(non_demons)} non-demon picks"
        
        print(f"War Zone: {len(demons)} demon picks (100% demons)")
    
    def test_war_zone_picks_have_required_fields(self):
        """War Zone picks should have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        required_fields = ["player_name", "stat_type", "line", "is_demon", "h10_rate", "l5_avg", "l10_avg"]
        
        for pick in picks[:5]:  # Check first 5 picks
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"
        
        print(f"War Zone picks have all required fields")


class TestGoblinVaultEndpoint:
    """Test /api/v3/goblin-vault (Safe Haven) returns goblin picks"""
    
    def test_goblin_vault_returns_picks(self):
        """Goblin Vault should return picks"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=10)
        assert response.status_code == 200, f"Goblin Vault failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        assert len(picks) > 0, "Goblin Vault should have picks"
        print(f"Goblin Vault picks count: {len(picks)}")
    
    def test_goblin_vault_picks_are_goblins(self):
        """Goblin Vault picks should have is_goblin=true"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        goblins = [p for p in picks if p.get("is_goblin") == True]
        assert len(goblins) > 0, "Goblin Vault should have goblin picks"
        
        # All picks in Goblin Vault should be goblins
        non_goblins = [p for p in picks if p.get("is_goblin") != True]
        assert len(non_goblins) == 0, f"Goblin Vault has {len(non_goblins)} non-goblin picks"
        
        print(f"Goblin Vault: {len(goblins)} goblin picks (100% goblins)")
    
    def test_goblin_vault_picks_have_high_hit_rates(self):
        """Goblin Vault picks should have high hit rates (>=80%)"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        high_hit_rate_picks = [p for p in picks if p.get("h10_rate", 0) >= 80]
        
        # Most goblin picks should have high hit rates
        hit_rate_pct = len(high_hit_rate_picks) / len(picks) * 100 if picks else 0
        print(f"Goblin Vault: {len(high_hit_rate_picks)}/{len(picks)} picks have h10_rate >= 80% ({hit_rate_pct:.0f}%)")
        
        # At least 50% should have high hit rates
        assert hit_rate_pct >= 50, f"Only {hit_rate_pct:.0f}% of goblin picks have h10_rate >= 80%"


class TestFrontLinesEndpoint:
    """Test /api/v3/front-lines returns picks"""
    
    def test_front_lines_returns_picks(self):
        """Front Lines should return picks"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=10)
        assert response.status_code == 200, f"Front Lines failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        assert len(picks) > 0, "Front Lines should have picks"
        print(f"Front Lines picks count: {len(picks)}")
    
    def test_front_lines_picks_have_required_fields(self):
        """Front Lines picks should have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        required_fields = ["player_name", "stat_type", "line", "h10_rate", "l5_avg", "l10_avg"]
        
        for pick in picks[:5]:  # Check first 5 picks
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"
        
        print(f"Front Lines picks have all required fields")


class TestVisionIntelNoJITCalls:
    """Test that Vision Intel serves from MongoDB without JIT Gemini calls"""
    
    def test_vision_insight_source_is_not_jit(self):
        """Vision insight should indicate pre-cached or pending_enrichment, not JIT"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Joel%20Embiid", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        props = player.get("props", [])
        
        # Find props with intel_suite
        for prop in props:
            intel = prop.get("intel_suite", {})
            vision_insight = intel.get("vision_insight", {})
            source = vision_insight.get("source")
            
            if source:
                # Source should be "pre_cached" or "pending_enrichment", NOT "jit" or "live"
                assert source in ["pre_cached", "pending_enrichment"], \
                    f"Vision insight source should be pre_cached or pending_enrichment, got: {source}"
                print(f"Vision insight source: {source} (PASS: no JIT)")
                return
        
        print("No props with vision_insight.source found (acceptable if no featured props)")
    
    def test_player_with_badges_response_time_multiple_players(self):
        """Multiple player requests should all be fast (<2s) - proves no JIT blocking"""
        players = ["Joel Embiid", "LaMelo Ball", "Tyrese Maxey", "Josh Hart", "Jalen Brunson"]
        
        for player in players:
            start_time = time.time()
            response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/{player.replace(' ', '%20')}", timeout=10)
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                assert elapsed < 2.0, f"{player} response time {elapsed:.2f}s exceeds 2 second limit"
                print(f"{player}: {elapsed:.3f}s (PASS)")
            else:
                print(f"{player}: Not found (status {response.status_code})")


class TestNBACareerServiceJSONDecode:
    """Test that nba_career_service handles JSON decode errors gracefully"""
    
    def test_player_with_badges_no_500_errors(self):
        """Player with badges should not return 500 errors from JSON decode issues"""
        # Test multiple players to ensure no JSON decode errors
        players = ["Joel Embiid", "LeBron James", "Stephen Curry", "Kevin Durant"]
        
        for player in players:
            response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/{player.replace(' ', '%20')}", timeout=15)
            
            # Should not get 500 error from JSON decode issues
            assert response.status_code != 500, f"{player} returned 500 error"
            
            if response.status_code == 200:
                data = response.json()
                assert "player" in data or "error" not in data, f"{player} has unexpected error"
                print(f"{player}: OK (status {response.status_code})")
            else:
                print(f"{player}: status {response.status_code} (not 500)")


class TestCachedPropsEndpoint:
    """Test /api/v3/cached-props serves from MongoDB"""
    
    def test_cached_props_returns_data(self):
        """Cached props should return player data"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props", timeout=10)
        assert response.status_code == 200, f"Cached props failed: {response.status_code}"
        
        data = response.json()
        assert "players" in data or "success" in data, "Cached props should return data"
        print(f"Cached props response keys: {list(data.keys())}")
    
    def test_cached_props_response_time(self):
        """Cached props should respond quickly (from MongoDB)"""
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/cached-props", timeout=10)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200
        assert elapsed < 3.0, f"Cached props took {elapsed:.2f}s (should be <3s for MongoDB read)"
        
        print(f"Cached props response time: {elapsed:.3f}s")


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
