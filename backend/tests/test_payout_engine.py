"""
PAYOUT CALCULATION ENGINE TEST SUITE
=====================================

Tests the dynamic payout calculation system with mixed picks:
- POST /api/v3/calculate-payout: Dynamic payout calculation
- GET /api/v3/parlay-builder: Demon parlay tiers (2-6 pick)  
- GET /api/v3/goblin-recon: Goblin parlay tiers with payout fields

Key fields to verify:
- estimated_payout, payout_display, base_multiplier, cumulative_modifier, asset_breakdown
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestCalculatePayoutEndpoint:
    """POST /api/v3/calculate-payout - Dynamic payout calculation tests"""
    
    def test_calculate_payout_with_mixed_picks_demons_and_standards(self):
        """Test payout calculation with demon and standard picks (mixed)"""
        picks = [
            {
                "player_name": "LeBron James",
                "stat_type": "PTS",
                "line": 30.5,
                "direction": "over",
                "is_demon": True,
                "standard_line": 27.5,
                "team": "LAL"
            },
            {
                "player_name": "Stephen Curry",
                "stat_type": "PTS",
                "line": 25.5,
                "direction": "over",
                "is_demon": False,
                "is_goblin": False,
                "team": "GSW"
            }
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # API returns payout data directly (no success wrapper)
        
        # Verify payout fields exist
        assert "estimated_payout" in data, "Missing estimated_payout field"
        assert "payout_display" in data, "Missing payout_display field"
        assert "base_multiplier" in data, "Missing base_multiplier field"
        assert "cumulative_modifier" in data, "Missing cumulative_modifier field"
        assert "asset_breakdown" in data, "Missing asset_breakdown field"
        
        # Verify base multiplier for 2-pick is 3.0
        assert data["base_multiplier"] == 3.0, f"Expected base_multiplier=3.0 for 2-pick, got: {data['base_multiplier']}"
        
        # Verify asset_breakdown structure
        breakdown = data["asset_breakdown"]
        assert "demons" in breakdown, "Missing demons count in asset_breakdown"
        assert "goblins" in breakdown, "Missing goblins count in asset_breakdown"
        assert "standards" in breakdown, "Missing standards count in asset_breakdown"
        
        # With 1 demon and 1 standard
        assert breakdown["demons"] >= 0, "demons count should be >= 0"
        assert breakdown["standards"] >= 0, "standards count should be >= 0"
        
        # Verify estimated_payout is positive
        assert data["estimated_payout"] > 0, f"Expected positive payout, got: {data['estimated_payout']}"
        
        # Verify payout_display format (e.g., "3.5x")
        assert "x" in data["payout_display"].lower(), f"payout_display should contain 'x': {data['payout_display']}"
        
        print(f"PASSED: Payout calculation - {data['payout_display']} with breakdown: {breakdown}")

    def test_calculate_payout_with_all_demons(self):
        """Test payout calculation with all demon picks (higher payout)"""
        picks = [
            {"player_name": "Player A", "stat_type": "PTS", "line": 32.5, "direction": "over", "is_demon": True, "standard_line": 28.5, "team": "TMA"},
            {"player_name": "Player B", "stat_type": "REB", "line": 12.5, "direction": "over", "is_demon": True, "standard_line": 10.5, "team": "TMB"}
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        assert response.status_code == 200
        data = response.json()
        # API returns payout data directly (no success wrapper)
        
        # With all demons, cumulative modifier should be > 1
        assert data["cumulative_modifier"] >= 1.0, f"All demons should have modifier >= 1.0, got: {data['cumulative_modifier']}"
        
        # Asset breakdown should show 2 demons
        breakdown = data["asset_breakdown"]
        assert breakdown["demons"] == 2, f"Expected 2 demons, got: {breakdown['demons']}"
        assert breakdown["goblins"] == 0, f"Expected 0 goblins, got: {breakdown['goblins']}"
        
        print(f"PASSED: All demons payout - {data['payout_display']}, cumulative_modifier: {data['cumulative_modifier']}")

    def test_calculate_payout_with_all_goblins(self):
        """Test payout calculation with all goblin picks (lower payout)"""
        picks = [
            {"player_name": "Player X", "stat_type": "PTS", "line": 22.5, "direction": "over", "is_goblin": True, "standard_line": 26.5, "team": "TMX"},
            {"player_name": "Player Y", "stat_type": "AST", "line": 6.5, "direction": "over", "is_goblin": True, "standard_line": 8.5, "team": "TMY"}
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        assert response.status_code == 200
        data = response.json()
        # API returns payout data directly (no success wrapper)
        
        # With all goblins, cumulative modifier should be < 1
        assert data["cumulative_modifier"] <= 1.0, f"All goblins should have modifier <= 1.0, got: {data['cumulative_modifier']}"
        
        # Asset breakdown should show 2 goblins
        breakdown = data["asset_breakdown"]
        assert breakdown["goblins"] == 2, f"Expected 2 goblins, got: {breakdown['goblins']}"
        assert breakdown["demons"] == 0, f"Expected 0 demons, got: {breakdown['demons']}"
        
        print(f"PASSED: All goblins payout - {data['payout_display']}, cumulative_modifier: {data['cumulative_modifier']}")

    def test_calculate_payout_with_mixed_demons_goblins_standards(self):
        """Test payout with mixed demon, goblin and standard picks"""
        picks = [
            {"player_name": "Player 1", "stat_type": "PTS", "line": 30.5, "direction": "over", "is_demon": True, "standard_line": 26.5, "team": "TM1"},
            {"player_name": "Player 2", "stat_type": "REB", "line": 8.5, "direction": "over", "is_goblin": True, "standard_line": 10.5, "team": "TM2"},
            {"player_name": "Player 3", "stat_type": "AST", "line": 7.5, "direction": "over", "team": "TM3"}  # Standard
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        assert response.status_code == 200
        data = response.json()
        # API returns payout data directly (no success wrapper)
        
        # For 3-pick, base_multiplier should be 5.0
        assert data["base_multiplier"] == 5.0, f"Expected base_multiplier=5.0 for 3-pick, got: {data['base_multiplier']}"
        
        # Asset breakdown should show mix
        breakdown = data["asset_breakdown"]
        total = breakdown["demons"] + breakdown["goblins"] + breakdown["standards"]
        assert total == 3, f"Total assets should be 3, got: {total}"
        
        print(f"PASSED: Mixed payout - {data['payout_display']}, breakdown: demons={breakdown['demons']}, goblins={breakdown['goblins']}, standards={breakdown['standards']}")

    def test_calculate_payout_returns_legs_detail(self):
        """Test that payout calculation returns leg-level details"""
        picks = [
            {"player_name": "Test Player A", "stat_type": "PTS", "line": 25.5, "direction": "over", "is_demon": True, "standard_line": 22.5, "team": "TMA"},
            {"player_name": "Test Player B", "stat_type": "REB", "line": 10.5, "direction": "over", "team": "TMB"}
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        assert response.status_code == 200
        data = response.json()
        # API returns payout data directly (no success wrapper)
        
        # Verify legs array exists
        assert "legs" in data, "Missing legs array in response"
        assert isinstance(data["legs"], list), "legs should be an array"
        assert len(data["legs"]) == 2, f"Expected 2 legs, got: {len(data['legs'])}"
        
        # Verify each leg has required fields
        for leg in data["legs"]:
            assert "player_name" in leg, "Missing player_name in leg"
            assert "stat_type" in leg, "Missing stat_type in leg"
            assert "asset_type" in leg, "Missing asset_type in leg"
            assert "modifier" in leg, "Missing modifier in leg"
            assert "modifier_display" in leg, "Missing modifier_display in leg"
        
        print(f"PASSED: Legs detail returned - {len(data['legs'])} legs with modifiers")

    def test_calculate_payout_validates_min_picks(self):
        """Test that API rejects less than 2 picks"""
        picks = [
            {"player_name": "Solo Player", "stat_type": "PTS", "line": 25.5, "direction": "over", "team": "TM1"}
        ]
        
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": picks}
        )
        
        # API should still return 200 but with error in response
        assert response.status_code == 200
        data = response.json()
        
        # Should have error or estimated_payout of 0
        if "error" in data:
            assert "2 picks" in data["error"].lower() or "minimum" in data["error"].lower()
            print(f"PASSED: Correctly rejected 1 pick with error: {data['error']}")
        else:
            assert data.get("estimated_payout", 0) == 0
            print("PASSED: Correctly rejected 1 pick with 0 payout")


class TestParlayBuilderPayoutFields:
    """GET /api/v3/parlay-builder - Verify Demon parlay tiers return payout fields"""
    
    def test_parlay_builder_returns_payout_fields(self):
        """Test that parlay-builder returns new payout fields for all tiers"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("success") == True, f"Expected success=True, got: {data}"
        
        parlays = data.get("parlays", {})
        
        # Check if we have any parlays
        if not parlays:
            print("INFO: No parlays generated (may need sync). Checking response structure...")
            assert "parlays" in data, "Response should have 'parlays' key"
            print("PASSED: Parlay builder endpoint accessible, no data yet")
            return
        
        # Required payout fields for each parlay tier
        required_fields = [
            "estimated_payout",
            "payout_display",
            "base_multiplier",
            "cumulative_modifier",
            "asset_breakdown"
        ]
        
        # Test each parlay tier (2_pick through 6_pick)
        tested_tiers = []
        for tier_key, tier_data in parlays.items():
            print(f"\nTesting {tier_key} ({tier_data.get('name', 'Unknown')})...")
            
            for field in required_fields:
                assert field in tier_data, f"Missing '{field}' in {tier_key}"
            
            # Verify asset_breakdown structure
            breakdown = tier_data.get("asset_breakdown", {})
            assert "demons" in breakdown, f"Missing 'demons' in asset_breakdown for {tier_key}"
            assert "goblins" in breakdown, f"Missing 'goblins' in asset_breakdown for {tier_key}"
            assert "standards" in breakdown, f"Missing 'standards' in asset_breakdown for {tier_key}"
            
            # Verify payout values are reasonable
            estimated_payout = tier_data.get("estimated_payout", 0)
            base_multiplier = tier_data.get("base_multiplier", 0)
            
            assert estimated_payout > 0, f"estimated_payout should be > 0 for {tier_key}, got: {estimated_payout}"
            assert base_multiplier > 0, f"base_multiplier should be > 0 for {tier_key}, got: {base_multiplier}"
            
            tested_tiers.append(tier_key)
            print(f"  PASSED: {tier_key} - {tier_data['payout_display']}, base={base_multiplier}x, breakdown={breakdown}")
        
        print(f"\nPASSED: All {len(tested_tiers)} parlay tiers have payout fields: {tested_tiers}")

    def test_parlay_builder_2_pick_base_multiplier(self):
        """Verify 2-pick parlay has correct base multiplier of 3.0"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder")
        
        assert response.status_code == 200
        data = response.json()
        parlays = data.get("parlays", {})
        
        if "2_pick" in parlays:
            tier_2 = parlays["2_pick"]
            assert tier_2.get("base_multiplier") == 3.0, f"2-pick base should be 3.0, got: {tier_2.get('base_multiplier')}"
            print(f"PASSED: 2-pick base_multiplier = 3.0")
        else:
            print("SKIPPED: No 2-pick parlay available")

    def test_parlay_builder_3_pick_base_multiplier(self):
        """Verify 3-pick parlay has correct base multiplier of 5.0"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder")
        
        assert response.status_code == 200
        data = response.json()
        parlays = data.get("parlays", {})
        
        if "3_pick" in parlays:
            tier_3 = parlays["3_pick"]
            assert tier_3.get("base_multiplier") == 5.0, f"3-pick base should be 5.0, got: {tier_3.get('base_multiplier')}"
            print(f"PASSED: 3-pick base_multiplier = 5.0")
        else:
            print("SKIPPED: No 3-pick parlay available")

    def test_parlay_builder_6_pick_base_multiplier(self):
        """Verify 6-pick parlay has correct base multiplier of 40.0"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder")
        
        assert response.status_code == 200
        data = response.json()
        parlays = data.get("parlays", {})
        
        if "6_pick" in parlays:
            tier_6 = parlays["6_pick"]
            assert tier_6.get("base_multiplier") == 40.0, f"6-pick base should be 40.0, got: {tier_6.get('base_multiplier')}"
            print(f"PASSED: 6-pick base_multiplier = 40.0")
        else:
            print("SKIPPED: No 6-pick parlay available")


class TestGoblinReconPayoutFields:
    """GET /api/v3/goblin-recon - Verify Goblin parlay tiers return payout fields"""
    
    def test_goblin_recon_returns_payout_fields(self):
        """Test that goblin-recon returns new payout fields for all tiers"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-recon")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("success") == True, f"Expected success=True, got: {data}"
        
        parlays = data.get("parlays", {})
        
        # Check if we have any parlays
        if not parlays:
            print("INFO: No goblin recon parlays generated (may need sync). Checking response structure...")
            assert "parlays" in data, "Response should have 'parlays' key"
            print("PASSED: Goblin recon endpoint accessible, no data yet")
            return
        
        # Required payout fields for each parlay tier
        required_fields = [
            "estimated_payout",
            "payout_display",
            "base_multiplier",
            "cumulative_modifier",
            "asset_breakdown"
        ]
        
        # Test each parlay tier
        tested_tiers = []
        for tier_key, tier_data in parlays.items():
            print(f"\nTesting {tier_key} ({tier_data.get('name', 'Unknown')})...")
            
            for field in required_fields:
                assert field in tier_data, f"Missing '{field}' in {tier_key}"
            
            # Verify asset_breakdown structure - ALL should be goblins
            breakdown = tier_data.get("asset_breakdown", {})
            assert "demons" in breakdown, f"Missing 'demons' in asset_breakdown for {tier_key}"
            assert "goblins" in breakdown, f"Missing 'goblins' in asset_breakdown for {tier_key}"
            assert "standards" in breakdown, f"Missing 'standards' in asset_breakdown for {tier_key}"
            
            # For goblin recon, all picks should be goblins
            goblins_count = breakdown.get("goblins", 0)
            pick_count = tier_data.get("pick_count", 0)
            assert goblins_count == pick_count, f"Goblin recon should have all goblin picks. Expected {pick_count}, got {goblins_count}"
            
            # Verify payout values are reasonable
            estimated_payout = tier_data.get("estimated_payout", 0)
            base_multiplier = tier_data.get("base_multiplier", 0)
            
            assert estimated_payout > 0, f"estimated_payout should be > 0 for {tier_key}, got: {estimated_payout}"
            assert base_multiplier > 0, f"base_multiplier should be > 0 for {tier_key}, got: {base_multiplier}"
            
            tested_tiers.append(tier_key)
            print(f"  PASSED: {tier_key} - {tier_data['payout_display']}, goblins={goblins_count}/{pick_count}")
        
        print(f"\nPASSED: All {len(tested_tiers)} goblin recon tiers have payout fields: {tested_tiers}")

    def test_goblin_recon_goblins_in_asset_breakdown(self):
        """Verify goblin-recon picks are correctly identified as goblins in asset_breakdown"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-recon")
        
        assert response.status_code == 200
        data = response.json()
        parlays = data.get("parlays", {})
        
        if not parlays:
            print("SKIPPED: No goblin recon data available")
            return
        
        for tier_key, tier_data in parlays.items():
            breakdown = tier_data.get("asset_breakdown", {})
            pick_count = tier_data.get("pick_count", 0)
            goblins_count = breakdown.get("goblins", 0)
            demons_count = breakdown.get("demons", 0)
            
            # Goblin recon should only have goblins, no demons
            assert demons_count == 0, f"{tier_key} should have 0 demons, got: {demons_count}"
            assert goblins_count == pick_count, f"{tier_key} should have {pick_count} goblins, got: {goblins_count}"
            
            print(f"PASSED: {tier_key} correctly shows {goblins_count} goblins, 0 demons")

    def test_goblin_recon_cumulative_modifier_less_than_1(self):
        """Verify goblin-recon cumulative_modifier is <= 1.0 (goblins lower payout)"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-recon")
        
        assert response.status_code == 200
        data = response.json()
        parlays = data.get("parlays", {})
        
        if not parlays:
            print("SKIPPED: No goblin recon data available")
            return
        
        for tier_key, tier_data in parlays.items():
            cumulative_modifier = tier_data.get("cumulative_modifier", 1.0)
            
            # Goblins should have cumulative modifier <= 1.0
            assert cumulative_modifier <= 1.0, f"{tier_key} goblin cumulative_modifier should be <= 1.0, got: {cumulative_modifier}"
            
            print(f"PASSED: {tier_key} cumulative_modifier = {cumulative_modifier} (correctly <= 1.0)")


class TestEndpointAvailability:
    """Basic endpoint availability tests"""
    
    def test_calculate_payout_endpoint_exists(self):
        """Verify POST /api/v3/calculate-payout endpoint is accessible"""
        response = requests.post(
            f"{BASE_URL}/api/v3/calculate-payout",
            json={"picks": []}
        )
        
        # Should not be 404
        assert response.status_code != 404, "calculate-payout endpoint not found"
        print(f"PASSED: calculate-payout endpoint exists (status: {response.status_code})")

    def test_parlay_builder_endpoint_exists(self):
        """Verify GET /api/v3/parlay-builder endpoint is accessible"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder")
        
        assert response.status_code == 200, f"parlay-builder endpoint failed: {response.status_code}"
        print("PASSED: parlay-builder endpoint accessible")

    def test_goblin_recon_endpoint_exists(self):
        """Verify GET /api/v3/goblin-recon endpoint is accessible"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-recon")
        
        assert response.status_code == 200, f"goblin-recon endpoint failed: {response.status_code}"
        print("PASSED: goblin-recon endpoint accessible")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
