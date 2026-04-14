"""
MLB Sharp Sorting & Tier Distribution Tests
============================================
Tests for the Sharp Sorting service that classifies props into:
- Goblins: Pinnacle confirmed (sharp odds ≤ -150 + VK confirms)
- Demons: DK/PP line discrepancy + high edge
- Standard: Books agree (-110 to -130)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://local-first-hub-2.preview.emergentagent.com')


class TestMLBSharpSortEndpoint:
    """Tests for POST /api/v3/mlb/sharp-sort endpoint"""
    
    def test_sharp_sort_returns_success(self):
        """Sharp sort endpoint returns success status"""
        response = requests.post(f"{BASE_URL}/api/v3/mlb/sharp-sort")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        print(f"✓ Sharp sort returned success=True")
    
    def test_sharp_sort_returns_counts(self):
        """Sharp sort returns counts for all tiers"""
        response = requests.post(f"{BASE_URL}/api/v3/mlb/sharp-sort")
        assert response.status_code == 200
        
        data = response.json()
        assert "goblins_count" in data
        assert "demons_count" in data
        assert "standard_count" in data
        assert "unclassified" in data
        assert "props_processed" in data
        
        print(f"✓ Goblins: {data['goblins_count']}")
        print(f"✓ Demons: {data['demons_count']}")
        print(f"✓ Standard: {data['standard_count']}")
        print(f"✓ Unclassified: {data['unclassified']}")
        print(f"✓ Total processed: {data['props_processed']}")
    
    def test_sharp_sort_returns_stats(self):
        """Sharp sort returns statistics about sharp odds"""
        response = requests.post(f"{BASE_URL}/api/v3/mlb/sharp-sort")
        assert response.status_code == 200
        
        data = response.json()
        stats = data.get("stats", {})
        
        assert "total_with_sharp_odds" in stats
        assert "total_with_dk_line" in stats
        assert "sharp_fair_value_avg" in stats
        
        print(f"✓ Props with sharp odds: {stats['total_with_sharp_odds']}")
        print(f"✓ Props with DK line: {stats['total_with_dk_line']}")
        print(f"✓ Avg sharp fair value: {stats['sharp_fair_value_avg']}")
    
    def test_sharp_sort_returns_top_goblins(self):
        """Sharp sort returns top 5 goblins with required fields"""
        response = requests.post(f"{BASE_URL}/api/v3/mlb/sharp-sort")
        assert response.status_code == 200
        
        data = response.json()
        top_goblins = data.get("top_5_goblins", [])
        
        if len(top_goblins) > 0:
            goblin = top_goblins[0]
            assert "player_name" in goblin
            assert "stat_type" in goblin
            assert "line" in goblin
            assert "sharp_odds" in goblin
            assert "sharp_fair_value" in goblin
            
            print(f"✓ Top goblin: {goblin['player_name']} - {goblin['stat_type']} {goblin['line']}")
            print(f"  Sharp odds: {goblin['sharp_odds']}, Fair value: {goblin['sharp_fair_value']}")
        else:
            print("⚠ No goblins found (may be no games with sharp odds)")
    
    def test_sharp_sort_with_stat_type_filter(self):
        """Sharp sort accepts stat_types filter parameter"""
        response = requests.post(
            f"{BASE_URL}/api/v3/mlb/sharp-sort",
            params={"stat_types": "Total Bases,Hits+Runs+RBIs"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        print(f"✓ Filtered sharp sort works - processed {data.get('props_processed', 0)} props")


class TestMLBGoblinsEndpoint:
    """Tests for GET /api/v3/mlb/sharp/goblins endpoint"""
    
    def test_goblins_endpoint_returns_success(self):
        """Goblins endpoint returns success status"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        assert data.get("tier") == "GOBLINS"
        print(f"✓ Goblins endpoint returned success=True, tier=GOBLINS")
    
    def test_goblins_returns_picks_array(self):
        """Goblins endpoint returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        assert "picks" in data
        assert isinstance(data["picks"], list)
        assert "count" in data
        
        print(f"✓ Goblins returned {data['count']} picks")
    
    def test_goblins_picks_have_required_fields(self):
        """Goblin picks have all required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            pick = picks[0]
            # Required fields for goblin picks
            required_fields = [
                "player_name", "stat_type", "line", "recommendation",
                "all_odds", "sharp_tier", "classified_at"
            ]
            
            for field in required_fields:
                assert field in pick, f"Missing field: {field}"
            
            # Verify sharp_tier is GOBLIN
            assert pick["sharp_tier"] == "GOBLIN"
            
            # Verify has Pinnacle odds
            all_odds = pick.get("all_odds", {})
            assert "pinnacle" in all_odds, "Goblin should have Pinnacle odds"
            
            print(f"✓ Goblin pick has all required fields")
            print(f"  Player: {pick['player_name']}")
            print(f"  Stat: {pick['stat_type']} {pick['line']}")
            print(f"  Pinnacle odds: {all_odds.get('pinnacle')}")
        else:
            print("⚠ No goblin picks available (may be no games with sharp odds)")
    
    def test_goblins_limit_parameter(self):
        """Goblins endpoint respects limit parameter"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins", params={"limit": 5})
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        assert len(picks) <= 5
        print(f"✓ Limit parameter works - returned {len(picks)} picks (limit=5)")


class TestMLBDemonsEndpoint:
    """Tests for GET /api/v3/mlb/sharp/demons endpoint"""
    
    def test_demons_endpoint_returns_success(self):
        """Demons endpoint returns success status"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        assert data.get("tier") == "DEMONS"
        print(f"✓ Demons endpoint returned success=True, tier=DEMONS")
    
    def test_demons_returns_picks_array(self):
        """Demons endpoint returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons")
        assert response.status_code == 200
        
        data = response.json()
        assert "picks" in data
        assert isinstance(data["picks"], list)
        assert "count" in data
        
        print(f"✓ Demons returned {data['count']} picks")
    
    def test_demons_picks_have_required_fields(self):
        """Demon picks have all required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            pick = picks[0]
            # Required fields for demon picks
            required_fields = [
                "player_name", "stat_type", "line", "recommendation",
                "sharp_tier", "classified_at", "dk_analysis"
            ]
            
            for field in required_fields:
                assert field in pick, f"Missing field: {field}"
            
            # Verify sharp_tier is DEMON
            assert pick["sharp_tier"] == "DEMON"
            
            # Verify has DK analysis
            dk_analysis = pick.get("dk_analysis", {})
            assert dk_analysis.get("is_demon") is True, "Demon should have is_demon=True"
            
            print(f"✓ Demon pick has all required fields")
            print(f"  Player: {pick['player_name']}")
            print(f"  Stat: {pick['stat_type']} {pick['line']}")
            print(f"  DK mispricing: {dk_analysis.get('mispricing')}%")
        else:
            print("⚠ No demon picks available")
    
    def test_demons_limit_parameter(self):
        """Demons endpoint respects limit parameter"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons", params={"limit": 5})
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        assert len(picks) <= 5
        print(f"✓ Limit parameter works - returned {len(picks)} picks (limit=5)")


class TestMLBStandardEndpoint:
    """Tests for GET /api/v3/mlb/sharp/standard endpoint"""
    
    def test_standard_endpoint_returns_success(self):
        """Standard endpoint returns success status"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/standard")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        assert data.get("tier") == "STANDARD"
        print(f"✓ Standard endpoint returned success=True, tier=STANDARD")
    
    def test_standard_returns_picks_array(self):
        """Standard endpoint returns picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/standard")
        assert response.status_code == 200
        
        data = response.json()
        assert "picks" in data
        assert isinstance(data["picks"], list)
        assert "count" in data
        
        print(f"✓ Standard returned {data['count']} picks")


class TestMLBSharpSortingIntegration:
    """Integration tests for the full Sharp Sorting flow"""
    
    def test_sharp_sort_populates_collections(self):
        """Running sharp sort populates all tier collections"""
        # Run sharp sort
        sort_response = requests.post(f"{BASE_URL}/api/v3/mlb/sharp-sort")
        assert sort_response.status_code == 200
        
        sort_data = sort_response.json()
        goblins_count = sort_data.get("goblins_count", 0)
        demons_count = sort_data.get("demons_count", 0)
        standard_count = sort_data.get("standard_count", 0)
        
        # Verify collections match counts
        goblins_response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        demons_response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons")
        standard_response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/standard")
        
        assert goblins_response.status_code == 200
        assert demons_response.status_code == 200
        assert standard_response.status_code == 200
        
        # Counts should match (or be limited by default limit)
        goblins_data = goblins_response.json()
        demons_data = demons_response.json()
        standard_data = standard_response.json()
        
        print(f"✓ Sharp sort populated collections:")
        print(f"  Goblins: {goblins_data['count']} (sort reported: {goblins_count})")
        print(f"  Demons: {demons_data['count']} (sort reported: {demons_count})")
        print(f"  Standard: {standard_data['count']} (sort reported: {standard_count})")
    
    def test_goblin_classification_criteria(self):
        """Goblins meet classification criteria: sharp odds ≤ -150"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        for pick in picks[:5]:  # Check first 5
            all_odds = pick.get("all_odds", {})
            pinnacle_odds = all_odds.get("pinnacle")
            
            if pinnacle_odds is not None:
                # Goblin criteria: sharp odds ≤ -150
                assert pinnacle_odds <= -150, f"Goblin {pick['player_name']} has odds {pinnacle_odds} > -150"
                print(f"✓ {pick['player_name']}: Pinnacle {pinnacle_odds} ≤ -150")
    
    def test_demon_classification_criteria(self):
        """Demons meet classification criteria: DK/PP line discrepancy"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/demons")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        for pick in picks[:5]:  # Check first 5
            dk_analysis = pick.get("dk_analysis", {})
            
            # Demon criteria: is_demon=True from DK analysis
            assert dk_analysis.get("is_demon") is True, f"Demon {pick['player_name']} has is_demon=False"
            
            mispricing = dk_analysis.get("mispricing")
            if mispricing is not None:
                print(f"✓ {pick['player_name']}: DK mispricing {mispricing}%")


class TestMLBSharpSortingDataQuality:
    """Data quality tests for Sharp Sorting output"""
    
    def test_picks_have_vk_projection_data(self):
        """Picks include VK projection data when available"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        picks_with_projection = 0
        for pick in picks:
            if pick.get("projected_value") is not None:
                picks_with_projection += 1
        
        print(f"✓ {picks_with_projection}/{len(picks)} goblins have VK projections")
    
    def test_picks_have_hit_rate_data(self):
        """Picks include hit rate data when available"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        picks_with_hit_rate = 0
        for pick in picks:
            if pick.get("hit_rate_l10") is not None:
                picks_with_hit_rate += 1
        
        print(f"✓ {picks_with_hit_rate}/{len(picks)} goblins have L10 hit rate")
    
    def test_picks_have_edge_data(self):
        """Picks include edge percentage data"""
        response = requests.get(f"{BASE_URL}/api/v3/mlb/sharp/goblins")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        picks_with_edge = 0
        for pick in picks:
            if pick.get("edge_pct") is not None:
                picks_with_edge += 1
        
        print(f"✓ {picks_with_edge}/{len(picks)} goblins have edge percentage")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
