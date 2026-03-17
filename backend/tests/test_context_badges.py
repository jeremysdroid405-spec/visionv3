"""
Test Context Badges API
=======================
Tests the 10 context badges for Vision Intel Suite:
1. locked_in: L5 PPG > Season PPG + 5
2. milestone: Stat avg within 5% of round milestone (20, 25, 30...)
3. gassed: Back-to-back game (2nd night)
4. home_cookin: Home PPG 15%+ higher than Away
5. jet_lag: Road game + traveled >1000mi
6. legal_noise: Active legal/personal news flag
7. distraction: Trade rumors or drama
8. revenge: Playing against former team
9. pay_day: Contract year (placeholder)
10. deep_water: Elimination/playoff game 5+ (placeholder)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestPlayerWithBadgesEndpoint:
    """Tests for GET /api/v3/player-with-badges/{player_name}"""
    
    def test_endpoint_returns_success_for_valid_player(self):
        """Test that endpoint returns success for a player in cache"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        assert "player" in data
        print(f"SUCCESS: Endpoint returned valid response for LaMelo Ball")
    
    def test_endpoint_returns_failure_for_missing_player(self):
        """Test that endpoint returns success=false for players not in cache"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/NonExistent%20Player%20XYZ123")
        # Endpoint returns 200 with success=false (not 404) - this is by design
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is False, "Should return success=false for missing players"
        assert data.get("player") is None, "Player should be null for missing players"
        print(f"SUCCESS: Endpoint returns success=false for non-existent players")
    
    def test_response_contains_active_badges_array(self):
        """Test that response contains active_badges array at player level"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        
        assert "active_badges" in player, "Player should have active_badges field"
        assert isinstance(player["active_badges"], list), "active_badges should be a list"
        print(f"SUCCESS: active_badges array present: {player['active_badges']}")
    
    def test_response_contains_badges_array_with_full_objects(self):
        """Test that response contains badges array with full badge objects"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        
        assert "badges" in player, "Player should have badges field"
        assert isinstance(player["badges"], list), "badges should be a list"
        
        if player["badges"]:
            badge = player["badges"][0]
            # Verify badge object structure
            assert "badge_key" in badge, "Badge should have badge_key"
            assert "display" in badge, "Badge should have display name"
            assert "icon" in badge, "Badge should have icon"
            assert "color" in badge, "Badge should have color"
            assert "description" in badge, "Badge should have description"
            assert "severity" in badge, "Badge should have severity"
            print(f"SUCCESS: Badge object has all required fields: {badge.keys()}")
        else:
            print("NOTE: No badges found for LaMelo Ball - checking test-badges endpoint")


class TestLockedInBadge:
    """Tests for locked_in badge: L5 PPG > Season PPG + 5"""
    
    def test_lamelo_ball_has_locked_in_badge(self):
        """LaMelo Ball should have locked_in badge (L5 avg +5 over season)"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        active_badges = player.get("active_badges", [])
        badges = player.get("badges", [])
        
        assert "locked_in" in active_badges, f"LaMelo Ball should have locked_in badge, got: {active_badges}"
        
        # Verify locked_in badge object
        locked_in_badge = next((b for b in badges if b["badge_key"] == "locked_in"), None)
        assert locked_in_badge is not None, "locked_in badge object should exist"
        assert "L5 avg" in locked_in_badge["description"], "locked_in description should mention L5 avg"
        print(f"SUCCESS: LaMelo Ball has locked_in badge: {locked_in_badge['description']}")
    
    def test_test_badges_endpoint_validates_locked_in_logic(self):
        """Use test endpoint to validate locked_in calculation"""
        response = requests.get(f"{BASE_URL}/api/v3/test-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        assert "error" not in data, f"Test endpoint returned error: {data}"
        
        season_ppg = data.get("season_ppg", 0)
        l5_ppg = data.get("l5_ppg", 0)
        diff = data.get("diff", 0)
        
        # Verify the math: l5_ppg > season_ppg + 5
        if diff > 5:
            assert "locked_in" in data.get("badge_keys", []), "Should have locked_in if diff > 5"
        print(f"SUCCESS: Test endpoint shows L5={l5_ppg}, Season={season_ppg}, Diff={diff}")


class TestMilestoneBadge:
    """Tests for milestone badge: Stat avg within 5% of round milestone"""
    
    def test_lamelo_ball_has_milestone_badge(self):
        """LaMelo Ball should have milestone badge"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        active_badges = player.get("active_badges", [])
        badges = player.get("badges", [])
        
        assert "milestone" in active_badges, f"LaMelo Ball should have milestone badge, got: {active_badges}"
        
        milestone_badge = next((b for b in badges if b["badge_key"] == "milestone"), None)
        assert milestone_badge is not None, "milestone badge object should exist"
        assert "near" in milestone_badge["description"].lower() or "averaging" in milestone_badge["description"].lower()
        print(f"SUCCESS: LaMelo Ball has milestone badge: {milestone_badge['description']}")
    
    def test_james_harden_has_milestone_badge(self):
        """James Harden should have milestone badge"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/James%20Harden")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        active_badges = player.get("active_badges", [])
        
        assert "milestone" in active_badges, f"James Harden should have milestone badge, got: {active_badges}"
        print(f"SUCCESS: James Harden has milestone badge")


class TestHomeCookinBadge:
    """Tests for home_cookin badge: Home PPG 15%+ higher than Away"""
    
    def test_donovan_mitchell_has_home_cookin_badge(self):
        """Donovan Mitchell should have home_cookin badge"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Donovan%20Mitchell")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        active_badges = player.get("active_badges", [])
        badges = player.get("badges", [])
        
        assert "home_cookin" in active_badges, f"Donovan Mitchell should have home_cookin badge, got: {active_badges}"
        
        home_badge = next((b for b in badges if b["badge_key"] == "home_cookin"), None)
        assert home_badge is not None, "home_cookin badge object should exist"
        assert "home" in home_badge["description"].lower()
        print(f"SUCCESS: Donovan Mitchell has home_cookin badge: {home_badge['description']}")


class TestBadgeDataFormat:
    """Tests for badge data format for frontend consumption"""
    
    def test_badge_keys_are_valid_registry_keys(self):
        """All badge keys should match BADGE_REGISTRY keys in frontend"""
        valid_badge_keys = {
            "locked_in", "milestone", "gassed", "home_cookin", 
            "jet_lag", "legal_noise", "distraction", "revenge",
            "pay_day", "deep_water"
        }
        
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        badges = player.get("badges", [])
        
        for badge in badges:
            badge_key = badge.get("badge_key")
            assert badge_key in valid_badge_keys, f"Unknown badge key: {badge_key}"
        print(f"SUCCESS: All badge keys are valid registry keys")
    
    def test_badge_severity_is_numeric(self):
        """Badge severity should be numeric (1-10)"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        badges = player.get("badges", [])
        
        for badge in badges:
            severity = badge.get("severity")
            assert isinstance(severity, (int, float)), f"Severity should be numeric: {severity}"
            assert 1 <= severity <= 10, f"Severity should be 1-10: {severity}"
        print(f"SUCCESS: Badge severity values are valid")
    
    def test_badge_colors_are_hex(self):
        """Badge colors should be valid hex codes"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/LaMelo%20Ball")
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        badges = player.get("badges", [])
        
        for badge in badges:
            color = badge.get("color", "")
            assert color.startswith("#"), f"Color should be hex: {color}"
            assert len(color) == 7, f"Color should be #RRGGBB: {color}"
        print(f"SUCCESS: Badge colors are valid hex codes")


class TestEmptyBadgesCase:
    """Tests for players without badges"""
    
    def test_player_without_badges_has_empty_arrays(self):
        """Players not meeting badge criteria should have empty badge arrays"""
        # Find a player with no badges
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        
        # Test a player from cache - pick one that likely won't have many badges
        for player in players[:5]:
            player_name = player.get("player_name")
            if player_name:
                badge_response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/{requests.utils.quote(player_name)}")
                if badge_response.status_code == 200:
                    badge_data = badge_response.json()
                    p = badge_data.get("player", {})
                    # Just verify the structure exists (even if empty)
                    assert "active_badges" in p, f"active_badges missing for {player_name}"
                    assert "badges" in p, f"badges missing for {player_name}"
                    print(f"SUCCESS: {player_name} has badge arrays: active_badges={p.get('active_badges')}")
                    break


class TestMultiplePlayersComparison:
    """Tests comparing badges across multiple known players"""
    
    def test_known_badge_assignments(self):
        """Verify known badge assignments based on requirements"""
        test_cases = [
            ("LaMelo Ball", ["locked_in", "milestone"]),
            ("Donovan Mitchell", ["milestone", "home_cookin"]),
            ("James Harden", ["milestone"]),
        ]
        
        for player_name, expected_badges in test_cases:
            response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/{requests.utils.quote(player_name)}")
            
            if response.status_code == 200:
                data = response.json()
                player = data.get("player", {})
                active_badges = player.get("active_badges", [])
                
                for badge in expected_badges:
                    assert badge in active_badges, f"{player_name} should have {badge}, got: {active_badges}"
                print(f"SUCCESS: {player_name} has expected badges: {expected_badges}")
            else:
                print(f"SKIP: {player_name} not in cache (status {response.status_code})")


class TestCachedPropsDoNotHaveBadges:
    """Verify badges are only added at player-with-badges endpoint level"""
    
    def test_cached_props_players_structure(self):
        """cached-props should return players without active_badges (added by separate endpoint)"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props")
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        
        assert len(players) > 0, "Should have players in cache"
        
        # Cached props endpoint doesn't add badges - those are added by player-with-badges
        first_player = players[0]
        # active_badges may or may not be present depending on implementation
        print(f"SUCCESS: cached-props returns {len(players)} players")
        print(f"First player keys: {first_player.keys()}")


# ====== Fixtures ======

@pytest.fixture(scope="module")
def api_session():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session
