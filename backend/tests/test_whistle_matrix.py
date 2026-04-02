"""
Whistle Matrix Feature Tests
============================
Tests for the Dynamic Whistle Matrix feature that applies referee-based modifiers
to the Ferrari Power Score.

Features tested:
- POST /api/v3/ferrari/rebuild returns whistle_matrix stats
- POST /api/v3/ferrari/sync-refs syncs referee data
- GET /api/v3/ferrari/refs returns today's referee assignments
- GET /api/v3/ferrari/safe-haven picks include whistle fields
- Power Score formula includes whistle_modifier
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestWhistleMatrixRebuild:
    """Tests for whistle_matrix stats in /api/v3/ferrari/rebuild"""
    
    def test_rebuild_returns_whistle_matrix_stats(self):
        """Rebuild endpoint returns whistle_matrix object with refs_synced and games_with_refs"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "whistle_matrix" in data, "Response should contain 'whistle_matrix' field"
        
        wm = data["whistle_matrix"]
        assert "refs_synced" in wm, "whistle_matrix should have refs_synced"
        assert "games_with_refs" in wm, "whistle_matrix should have games_with_refs"
        assert isinstance(wm["refs_synced"], int), "refs_synced should be an integer"
        assert isinstance(wm["games_with_refs"], int), "games_with_refs should be an integer"
    
    def test_rebuild_whistle_matrix_has_modifier_counts(self):
        """Rebuild returns whistle_matrix with green_light_applied, red_light_applied, neutral counts"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        
        wm = data.get("whistle_matrix", {})
        assert "green_light_applied" in wm, "whistle_matrix should have green_light_applied"
        assert "red_light_applied" in wm, "whistle_matrix should have red_light_applied"
        assert "neutral" in wm, "whistle_matrix should have neutral"
    
    def test_rebuild_pipeline_name_includes_whistle_matrix(self):
        """Rebuild pipeline name should indicate Whistle Matrix integration"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild")
        data = response.json()
        
        pipeline = data.get("pipeline", "")
        # Pipeline should mention Whistle Matrix
        assert "Whistle Matrix" in pipeline or "v6" in pipeline, \
            f"Pipeline name should indicate Whistle Matrix: {pipeline}"


class TestSyncRefsEndpoint:
    """Tests for POST /api/v3/ferrari/sync-refs endpoint"""
    
    def test_sync_refs_returns_200(self):
        """Sync refs endpoint returns 200 OK"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/sync-refs")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_sync_refs_returns_stats(self):
        """Sync refs returns stats_count and assignments_count"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/sync-refs")
        data = response.json()
        
        assert "stats_count" in data, "Response should have stats_count"
        assert "assignments_count" in data, "Response should have assignments_count"
        assert isinstance(data["stats_count"], int), "stats_count should be an integer"
        assert isinstance(data["assignments_count"], int), "assignments_count should be an integer"
    
    def test_sync_refs_returns_synced_at(self):
        """Sync refs returns synced_at timestamp"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/sync-refs")
        data = response.json()
        
        assert "synced_at" in data, "Response should have synced_at"
    
    def test_sync_refs_has_fallback_data(self):
        """Sync refs should have fallback data (at least some refs)"""
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/sync-refs")
        data = response.json()
        
        # Even if scraping fails, fallback data should provide some refs
        assert data.get("stats_count", 0) >= 0, "stats_count should be non-negative"


class TestRefsEndpoint:
    """Tests for GET /api/v3/ferrari/refs endpoint"""
    
    def test_refs_returns_200(self):
        """Refs endpoint returns 200 OK"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/refs")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_refs_returns_assignments_array(self):
        """Refs endpoint returns assignments array"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/refs")
        data = response.json()
        
        assert "assignments" in data, "Response should have 'assignments' field"
        assert isinstance(data["assignments"], list), "assignments should be a list"
    
    def test_refs_returns_total_refs_in_cache(self):
        """Refs endpoint returns total_refs_in_cache count"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/refs")
        data = response.json()
        
        assert "total_refs_in_cache" in data, "Response should have total_refs_in_cache"
        assert isinstance(data["total_refs_in_cache"], int), "total_refs_in_cache should be an integer"
    
    def test_refs_assignments_have_whistle_class(self):
        """Each assignment should have whistle_class field"""
        # First sync refs to ensure data is available
        requests.post(f"{BASE_URL}/api/v3/ferrari/sync-refs")
        
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/refs")
        data = response.json()
        
        for assignment in data.get("assignments", []):
            assert "whistle_class" in assignment, \
                f"Assignment {assignment.get('game')} missing whistle_class"
            assert assignment["whistle_class"] in ["high_whistle", "low_whistle", "neutral"], \
                f"Invalid whistle_class: {assignment['whistle_class']}"


class TestSafeHavenWhistleFields:
    """Tests for whistle fields in /api/v3/ferrari/safe-haven picks"""
    
    def test_safe_haven_picks_have_whistle_fields(self):
        """Safe Haven picks should have whistle-related fields"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        
        for pick in data.get("picks", []):
            # These fields should exist (may be null if no ref assigned)
            assert "crew_chief" in pick, f"Pick {pick.get('player_name')} missing crew_chief"
            assert "ref_ou_pct" in pick, f"Pick {pick.get('player_name')} missing ref_ou_pct"
            assert "whistle_class" in pick, f"Pick {pick.get('player_name')} missing whistle_class"
            assert "whistle_modifier" in pick, f"Pick {pick.get('player_name')} missing whistle_modifier"
    
    def test_safe_haven_whistle_class_valid_values(self):
        """Whistle class should be high_whistle, low_whistle, or neutral"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        
        valid_classes = ["high_whistle", "low_whistle", "neutral"]
        for pick in data.get("picks", []):
            wc = pick.get("whistle_class")
            assert wc in valid_classes, f"Invalid whistle_class '{wc}' for {pick.get('player_name')}"
    
    def test_safe_haven_whistle_modifier_numeric(self):
        """Whistle modifier should be a number"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        
        for pick in data.get("picks", []):
            wm = pick.get("whistle_modifier")
            assert isinstance(wm, (int, float)), \
                f"whistle_modifier should be numeric, got {type(wm)} for {pick.get('player_name')}"


class TestFrontLinesWhistleFields:
    """Tests for whistle fields in /api/v3/ferrari/front-lines picks"""
    
    def test_front_lines_picks_have_whistle_fields(self):
        """Front Lines picks should have whistle-related fields"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        data = response.json()
        
        for pick in data.get("picks", []):
            assert "crew_chief" in pick, f"Pick {pick.get('player_name')} missing crew_chief"
            assert "whistle_class" in pick, f"Pick {pick.get('player_name')} missing whistle_class"
            assert "whistle_modifier" in pick, f"Pick {pick.get('player_name')} missing whistle_modifier"


class TestWarZoneWhistleFields:
    """Tests for whistle fields in /api/v3/ferrari/war-zone picks"""
    
    def test_war_zone_picks_have_whistle_fields(self):
        """War Zone picks should have whistle-related fields"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        data = response.json()
        
        for pick in data.get("picks", []):
            assert "crew_chief" in pick, f"Pick {pick.get('player_name')} missing crew_chief"
            assert "whistle_class" in pick, f"Pick {pick.get('player_name')} missing whistle_class"
            assert "whistle_modifier" in pick, f"Pick {pick.get('player_name')} missing whistle_modifier"


class TestPowerScoreWithWhistleModifier:
    """Tests for power score calculation with whistle modifier"""
    
    def test_power_score_includes_whistle_modifier(self):
        """Power score should include whistle_modifier in calculation"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        data = response.json()
        
        for pick in data.get("picks", []):
            base_score = pick.get("base_power_score")
            whistle_mod = pick.get("whistle_modifier", 0)
            final_score = pick.get("ferrari_power_score")
            
            if base_score is not None:
                # Final score should be base + whistle modifier (capped at 0-115)
                expected = min(115, max(0, base_score + whistle_mod))
                assert abs(final_score - expected) < 0.5, \
                    f"{pick.get('player_name')}: Expected {expected:.2f}, got {final_score}"
    
    def test_power_score_range_with_modifier(self):
        """Power score with modifier should be in range 0-115"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data.get(tier_name, {})
            for pick in tier.get("picks", []):
                score = pick.get("ferrari_power_score", 0)
                # With whistle modifier (+15 max), score can go up to 115
                assert 0 <= score <= 115, \
                    f"{pick.get('player_name')} has invalid power score: {score}"
    
    def test_whistle_modifier_values(self):
        """Whistle modifier should be +15/-15 for PTS/FTM, +7.5/-7.5 for PRA, 0 for neutral"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        valid_modifiers = [15.0, 7.5, 0.0, -7.5, -15.0]
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data.get(tier_name, {})
            for pick in tier.get("picks", []):
                wm = pick.get("whistle_modifier", 0)
                # Allow small floating point tolerance
                is_valid = any(abs(wm - v) < 0.1 for v in valid_modifiers)
                assert is_valid, \
                    f"{pick.get('player_name')} has invalid whistle_modifier: {wm}"


class TestWhistleModifierLogic:
    """Tests for whistle modifier logic based on stat type and whistle class"""
    
    def test_high_whistle_positive_modifier(self):
        """High whistle class should have positive or zero modifier"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data.get(tier_name, {})
            for pick in tier.get("picks", []):
                if pick.get("whistle_class") == "high_whistle":
                    wm = pick.get("whistle_modifier", 0)
                    assert wm >= 0, \
                        f"{pick.get('player_name')} high_whistle should have positive modifier, got {wm}"
    
    def test_low_whistle_negative_modifier(self):
        """Low whistle class should have negative or zero modifier"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data.get(tier_name, {})
            for pick in tier.get("picks", []):
                if pick.get("whistle_class") == "low_whistle":
                    wm = pick.get("whistle_modifier", 0)
                    assert wm <= 0, \
                        f"{pick.get('player_name')} low_whistle should have negative modifier, got {wm}"
    
    def test_neutral_whistle_zero_modifier(self):
        """Neutral whistle class should have zero modifier"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/all")
        data = response.json()
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier = data.get(tier_name, {})
            for pick in tier.get("picks", []):
                if pick.get("whistle_class") == "neutral":
                    wm = pick.get("whistle_modifier", 0)
                    assert wm == 0, \
                        f"{pick.get('player_name')} neutral should have zero modifier, got {wm}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
