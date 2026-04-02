"""
Ferrari v6 Pipeline Tests
=========================
Tests for the Ferrari Tiered Dashboard with Global Power Ranking.

Features tested:
- GET /api/v3/ferrari/safe-haven - 10 picks with ferrari_power_score
- GET /api/v3/ferrari/front-lines - 10 picks with ferrari_power_score
- GET /api/v3/ferrari/war-zone - 10 picks with ferrari_power_score
- GET /api/v3/ferrari/all - All tiers plus verification object
- POST /api/v3/ferrari/rebuild - Full pipeline execution
- Power score components: edge_component, cushion_component, consistency_component
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestFerrariSafeHaven:
    """Tests for /api/v3/ferrari/safe-haven endpoint"""
    
    def test_safe_haven_returns_200(self):
        """Safe Haven endpoint returns 200 OK"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_safe_haven_returns_10_picks(self):
        """Safe Haven returns exactly 10 picks (or less if not enough data)"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        assert "picks" in data, "Response should contain 'picks' field"
        assert len(data["picks"]) <= 10, f"Should return max 10 picks, got {len(data['picks'])}"
    
    def test_safe_haven_has_verification_stats(self):
        """Safe Haven returns verification stats for Market Intel footer"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        assert "verification" in data, "Response should contain 'verification' field"
        verification = data["verification"]
        assert "active_props_verified" in verification, "Verification should have active_props_verified"
        assert "safe_haven_pool" in verification, "Verification should have safe_haven_pool"
    
    def test_safe_haven_picks_have_power_score(self):
        """Each Safe Haven pick has ferrari_power_score"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        for pick in data["picks"]:
            assert "ferrari_power_score" in pick, f"Pick {pick.get('player_name')} missing ferrari_power_score"
            assert isinstance(pick["ferrari_power_score"], (int, float)), "ferrari_power_score should be numeric"
            assert 0 <= pick["ferrari_power_score"] <= 100, f"Power score {pick['ferrari_power_score']} out of range 0-100"
    
    def test_safe_haven_picks_have_components(self):
        """Each Safe Haven pick has edge, cushion, consistency components"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        for pick in data["picks"]:
            assert "edge_component" in pick, f"Pick {pick.get('player_name')} missing edge_component"
            assert "cushion_component" in pick, f"Pick {pick.get('player_name')} missing cushion_component"
            assert "consistency_component" in pick, f"Pick {pick.get('player_name')} missing consistency_component"


class TestFerrariFrontLines:
    """Tests for /api/v3/ferrari/front-lines endpoint"""
    
    def test_front_lines_returns_200(self):
        """Front Lines endpoint returns 200 OK"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_front_lines_returns_10_picks(self):
        """Front Lines returns exactly 10 picks (or less if not enough data)"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        data = response.json()
        assert "picks" in data, "Response should contain 'picks' field"
        assert len(data["picks"]) <= 10, f"Should return max 10 picks, got {len(data['picks'])}"
    
    def test_front_lines_has_verification_stats(self):
        """Front Lines returns verification stats"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        data = response.json()
        assert "verification" in data, "Response should contain 'verification' field"
        verification = data["verification"]
        assert "active_props_verified" in verification, "Verification should have active_props_verified"
        assert "front_lines_pool" in verification, "Verification should have front_lines_pool"
    
    def test_front_lines_picks_have_power_score(self):
        """Each Front Lines pick has ferrari_power_score"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        data = response.json()
        for pick in data["picks"]:
            assert "ferrari_power_score" in pick, f"Pick {pick.get('player_name')} missing ferrari_power_score"
            assert isinstance(pick["ferrari_power_score"], (int, float)), "ferrari_power_score should be numeric"


class TestFerrariWarZone:
    """Tests for /api/v3/ferrari/war-zone endpoint"""
    
    def test_war_zone_returns_200(self):
        """War Zone endpoint returns 200 OK"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_war_zone_returns_10_picks(self):
        """War Zone returns exactly 10 picks (or less if not enough data)"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        data = response.json()
        assert "picks" in data, "Response should contain 'picks' field"
        assert len(data["picks"]) <= 10, f"Should return max 10 picks, got {len(data['picks'])}"
    
    def test_war_zone_has_verification_stats(self):
        """War Zone returns verification stats"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        data = response.json()
        assert "verification" in data, "Response should contain 'verification' field"
        verification = data["verification"]
        assert "active_props_verified" in verification, "Verification should have active_props_verified"
        assert "war_zone_pool" in verification, "Verification should have war_zone_pool"
    
    def test_war_zone_picks_have_power_score(self):
        """Each War Zone pick has ferrari_power_score"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        data = response.json()
        for pick in data["picks"]:
            assert "ferrari_power_score" in pick, f"Pick {pick.get('player_name')} missing ferrari_power_score"
            assert isinstance(pick["ferrari_power_score"], (int, float)), "ferrari_power_score should be numeric"


class TestFerrariAll:
    """Tests for /api/v3/ferrari/all endpoint"""
    
    def test_all_returns_200(self):
        """All tiers endpoint returns 200 OK"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_all_contains_three_tiers(self):
        """All endpoint returns safe_haven, front_lines, war_zone"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        assert "safe_haven" in data, "Response should contain 'safe_haven'"
        assert "front_lines" in data, "Response should contain 'front_lines'"
        assert "war_zone" in data, "Response should contain 'war_zone'"
    
    def test_all_has_verification_object(self):
        """All endpoint returns verification object with active_props_verified and elite_opportunities"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        assert "verification" in data, "Response should contain 'verification'"
        verification = data["verification"]
        assert "active_props_verified" in verification, "Verification should have active_props_verified"
        assert "elite_opportunities" in verification, "Verification should have elite_opportunities"
        assert "message" in verification, "Verification should have message"
    
    def test_all_elite_opportunities_equals_30(self):
        """Elite opportunities should equal total picks across all tiers (max 30)"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        total_picks = (
            data["safe_haven"]["count"] + 
            data["front_lines"]["count"] + 
            data["war_zone"]["count"]
        )
        assert data["verification"]["elite_opportunities"] == total_picks, \
            f"elite_opportunities ({data['verification']['elite_opportunities']}) should equal total picks ({total_picks})"
    
    def test_all_verification_message_format(self):
        """Verification message follows expected format"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        message = data["verification"]["message"]
        assert "Verified" in message, "Message should contain 'Verified'"
        assert "active props" in message, "Message should contain 'active props'"
        assert "Elite opportunities" in message, "Message should contain 'Elite opportunities'"


class TestFerrariRebuild:
    """Tests for POST /api/v3/ferrari/rebuild endpoint"""
    
    def test_rebuild_returns_200(self):
        """Rebuild endpoint returns 200 OK"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_rebuild_returns_success(self):
        """Rebuild returns success=True"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        assert data.get("success") == True, f"Expected success=True, got {data.get('success')}"
    
    def test_rebuild_has_verification_message(self):
        """Rebuild returns verification message"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        assert "verification" in data, "Response should contain 'verification'"
        verification = data["verification"]
        assert "message" in verification, "Verification should have message"
        assert "active_props_verified" in verification, "Verification should have active_props_verified"
        assert "elite_opportunities" in verification, "Verification should have elite_opportunities"
    
    def test_rebuild_has_pipeline_info(self):
        """Rebuild returns pipeline info"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        assert data.get("pipeline") == "Ferrari v6", f"Expected pipeline='Ferrari v6', got {data.get('pipeline')}"
    
    def test_rebuild_has_universal_scan_stats(self):
        """Rebuild returns universal scan statistics"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        assert "universal_scan" in data, "Response should contain 'universal_scan'"
        us = data["universal_scan"]
        assert "total_props_scanned" in us, "universal_scan should have total_props_scanned"
        assert "players_processed" in us, "universal_scan should have players_processed"
    
    def test_rebuild_has_output_counts(self):
        """Rebuild returns output counts for each tier"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        assert "output" in data, "Response should contain 'output'"
        output = data["output"]
        assert "safe_haven" in output, "output should have safe_haven count"
        assert "front_lines" in output, "output should have front_lines count"
        assert "war_zone" in output, "output should have war_zone count"
        assert "total" in output, "output should have total count"
        # Verify total equals sum of tiers
        expected_total = output["safe_haven"] + output["front_lines"] + output["war_zone"]
        assert output["total"] == expected_total, f"Total {output['total']} should equal sum {expected_total}"


class TestPowerScoreCalculation:
    """Tests for power score calculation correctness"""
    
    def test_power_score_within_range(self):
        """All power scores should be between 0 and 100"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data[tier_name]
            for pick in tier["picks"]:
                score = pick.get("ferrari_power_score", 0)
                assert 0 <= score <= 100, f"{pick['player_name']} has invalid power score: {score}"
    
    def test_components_within_range(self):
        """All components should be between 0 and 100"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data[tier_name]
            for pick in tier["picks"]:
                edge = pick.get("edge_component", 0)
                cushion = pick.get("cushion_component", 0)
                consistency = pick.get("consistency_component", 0)
                
                assert 0 <= edge <= 100, f"{pick['player_name']} has invalid edge_component: {edge}"
                assert 0 <= cushion <= 100, f"{pick['player_name']} has invalid cushion_component: {cushion}"
                assert 0 <= consistency <= 100, f"{pick['player_name']} has invalid consistency_component: {consistency}"
    
    def test_power_score_formula(self):
        """Power score should follow formula: (Edge*0.4)+(Cushion*0.3)+(Consistency*0.3)"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        
        for pick in data["picks"][:3]:  # Test first 3 picks
            edge = pick.get("edge_component", 0)
            cushion = pick.get("cushion_component", 0)
            consistency = pick.get("consistency_component", 0)
            actual_score = pick.get("ferrari_power_score", 0)
            
            expected_score = (edge * 0.4) + (cushion * 0.3) + (consistency * 0.3)
            # Allow small floating point tolerance
            assert abs(actual_score - expected_score) < 0.1, \
                f"{pick['player_name']}: Expected {expected_score:.2f}, got {actual_score}"


class TestTierSorting:
    """Tests for tier sorting by power score"""
    
    def test_safe_haven_sorted_by_power_score(self):
        """Safe Haven picks should be sorted by ferrari_power_score descending"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        picks = data["picks"]
        
        if len(picks) > 1:
            scores = [p["ferrari_power_score"] for p in picks]
            assert scores == sorted(scores, reverse=True), "Safe Haven picks not sorted by power score"
    
    def test_front_lines_sorted_by_power_score(self):
        """Front Lines picks should be sorted by ferrari_power_score descending"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        data = response.json()
        picks = data["picks"]
        
        if len(picks) > 1:
            scores = [p["ferrari_power_score"] for p in picks]
            assert scores == sorted(scores, reverse=True), "Front Lines picks not sorted by power score"
    
    def test_war_zone_sorted_by_power_score(self):
        """War Zone picks should be sorted by ferrari_power_score descending"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        data = response.json()
        picks = data["picks"]
        
        if len(picks) > 1:
            scores = [p["ferrari_power_score"] for p in picks]
            assert scores == sorted(scores, reverse=True), "War Zone picks not sorted by power score"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
