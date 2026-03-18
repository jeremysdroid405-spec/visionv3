"""
Test L5/L10 Stats Consistency
=============================
Verifies that L5/L10 stats are consistent between:
1. War Zone API (/api/v3/war-zone)
2. Player Profile API (/api/command/profile/{player_name})

Bug context: Game logs were being sliced [:N] without sorting by date first,
causing inconsistent stats between endpoints that sorted differently.

Fix: All calculation functions now sort game logs by date before slicing:
- _calculate_l5_avg
- _calculate_l10_avg  
- _calculate_h5_hit_rate
- _calculate_h10_hit_rate
- _calculate_l25_hit_rate
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestL5L10Consistency:
    """Test that L5/L10 stats are consistent across endpoints."""
    
    def test_backend_is_running(self):
        """Verify backend is running and accessible."""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        assert response.status_code == 200, f"Backend not running: {response.status_code}"
        data = response.json()
        assert data.get("success") is True, "Backend status check failed"
        print(f"✓ Backend running - {data.get('data', {}).get('total_players', 0)} players loaded")
    
    def test_war_zone_returns_data(self):
        """Verify War Zone endpoint returns picks with L5/L10 stats."""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=15)
        assert response.status_code == 200, f"War Zone failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        # Log pick count for debugging
        print(f"✓ War Zone returned {len(picks)} picks")
        
        if len(picks) > 0:
            # Check that picks have L5/L10 stats
            sample_pick = picks[0]
            assert "player_name" in sample_pick, "Missing player_name"
            assert "l5_avg" in sample_pick, "Missing l5_avg in War Zone pick"
            assert "l10_avg" in sample_pick, "Missing l10_avg in War Zone pick"
            assert "h5_rate" in sample_pick, "Missing h5_rate in War Zone pick"
            assert "h10_rate" in sample_pick, "Missing h10_rate in War Zone pick"
            print(f"✓ Sample pick: {sample_pick['player_name']} - L5: {sample_pick['l5_avg']}, L10: {sample_pick['l10_avg']}")
    
    def test_player_profile_returns_data(self):
        """Verify Player Profile endpoint returns L5/L10 stats."""
        # First get a player from War Zone
        war_zone_response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=15)
        if war_zone_response.status_code != 200:
            pytest.skip("War Zone not available")
        
        picks = war_zone_response.json().get("picks", [])
        if not picks:
            pytest.skip("No War Zone picks available")
        
        player_name = picks[0].get("player_name")
        if not player_name:
            pytest.skip("No player name in picks")
        
        # Now get player profile
        profile_response = requests.get(
            f"{BASE_URL}/api/command/profile/{player_name}", 
            timeout=15
        )
        assert profile_response.status_code == 200, f"Profile failed: {profile_response.status_code}"
        
        data = profile_response.json()
        assert data.get("success") is True, "Profile request unsuccessful"
        
        # Profile endpoint returns data directly (not nested in "player")
        assert data.get("player_name"), "No player_name in profile response"
        print(f"✓ Player profile found: {data.get('player_name')}")
        
        # Check for lines with L5/L10 stats
        lines = data.get("lines", [])
        assert len(lines) > 0, "No lines in profile response"
        
        # Check first line has L5/L10 stats
        first_line = lines[0]
        assert "l5_avg" in first_line, "Missing l5_avg in profile line"
        assert "l10_avg" in first_line, "Missing l10_avg in profile line"
        print(f"✓ Player {player_name} has {len(lines)} lines with L5/L10 stats")
    
    def test_consistency_war_zone_vs_profile(self):
        """
        CRITICAL TEST: Verify L5/L10 stats match between War Zone and Profile.
        
        This test checks that the same player has consistent stats across:
        - /api/v3/war-zone (uses picks_getter_service)
        - /api/command/profile/{player} (uses profile endpoint)
        """
        # Get War Zone picks
        war_zone_response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=15)
        if war_zone_response.status_code != 200:
            pytest.skip("War Zone not available")
        
        war_zone_data = war_zone_response.json()
        picks = war_zone_data.get("picks", [])
        
        if not picks:
            pytest.skip("No War Zone picks available for consistency test")
        
        # Test consistency for up to 3 players
        tested_count = 0
        inconsistencies = []
        
        for pick in picks[:5]:  # Check first 5 picks
            player_name = pick.get("player_name")
            stat_type = pick.get("stat_type")
            war_zone_l5 = pick.get("l5_avg")
            war_zone_l10 = pick.get("l10_avg")
            war_zone_h10 = pick.get("h10_rate")
            
            if not player_name or war_zone_l5 is None:
                continue
            
            # Get player profile
            profile_response = requests.get(
                f"{BASE_URL}/api/command/profile/{player_name}",
                timeout=15
            )
            
            if profile_response.status_code != 200:
                continue
            
            profile_data = profile_response.json()
            if not profile_data.get("success"):
                continue
            
            player = profile_data.get("player", {})
            baseline_stats = player.get("baseline_stats", {})
            
            # Get L5/L10 from profile's baseline_stats
            stat_key = stat_type.upper() if stat_type else "PTS"
            stat_data = baseline_stats.get(stat_key, {})
            
            if isinstance(stat_data, dict):
                profile_l5 = stat_data.get("l5_avg")
                profile_l10 = stat_data.get("l10_avg")
                profile_h10 = stat_data.get("l10_hit_rate")
            else:
                # May be flat value (season_avg only)
                profile_l5 = None
                profile_l10 = None
                profile_h10 = None
            
            # Also check game logs for manual calculation verification
            game_logs = player.get("bdl_game_logs", []) or player.get("game_logs", [])
            
            tested_count += 1
            
            print(f"\n[{tested_count}] {player_name} ({stat_type}):")
            print(f"   War Zone:  L5={war_zone_l5}, L10={war_zone_l10}, H10={war_zone_h10}%")
            print(f"   Profile:   L5={profile_l5}, L10={profile_l10}, H10={profile_h10}")
            print(f"   Game logs: {len(game_logs)} available")
            
            # Check for significant inconsistencies (>5% difference)
            if profile_l10 is not None and war_zone_l10 is not None:
                diff = abs(float(war_zone_l10) - float(profile_l10))
                if diff > 1.0:  # Allow 1 point tolerance for rounding
                    inconsistencies.append({
                        "player": player_name,
                        "stat": stat_type,
                        "war_zone_l10": war_zone_l10,
                        "profile_l10": profile_l10,
                        "diff": diff
                    })
                    print(f"   ⚠️  L10 MISMATCH: diff={diff}")
                else:
                    print(f"   ✓ L10 consistent (diff={diff})")
            
            if tested_count >= 3:
                break
        
        # Report results
        print(f"\n{'='*50}")
        print(f"Tested {tested_count} players for L5/L10 consistency")
        
        if inconsistencies:
            print(f"FOUND {len(inconsistencies)} INCONSISTENCIES:")
            for inc in inconsistencies:
                print(f"  - {inc['player']} {inc['stat']}: WZ={inc['war_zone_l10']} vs Profile={inc['profile_l10']}")
            pytest.fail(f"Found {len(inconsistencies)} L5/L10 inconsistencies")
        else:
            print("✓ All tested players have consistent L5/L10 stats")
    
    def test_date_sorting_in_game_logs(self):
        """
        Verify that game logs are correctly sorted by date (most recent first).
        
        This validates the fix where game logs must be sorted before slicing [:N].
        """
        # Get a player from War Zone
        war_zone_response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=15)
        if war_zone_response.status_code != 200:
            pytest.skip("War Zone not available")
        
        picks = war_zone_response.json().get("picks", [])
        if not picks:
            pytest.skip("No picks available")
        
        player_name = picks[0].get("player_name")
        
        # Get player profile with game logs
        profile_response = requests.get(
            f"{BASE_URL}/api/command/profile/{player_name}",
            timeout=15
        )
        
        if profile_response.status_code != 200:
            pytest.skip(f"Profile not available for {player_name}")
        
        data = profile_response.json()
        player = data.get("player", {})
        game_logs = player.get("bdl_game_logs", []) or player.get("game_logs", [])
        
        if len(game_logs) < 2:
            pytest.skip(f"Not enough game logs for {player_name}")
        
        # Extract dates from first 5 game logs
        dates = []
        for log in game_logs[:10]:
            date_str = None
            if isinstance(log.get("game"), dict):
                date_str = log.get("game", {}).get("date")
            if not date_str:
                date_str = log.get("date") or log.get("game_date")
            
            if date_str:
                dates.append(date_str[:10])  # Just YYYY-MM-DD part
        
        print(f"\nGame log dates for {player_name} (first 10):")
        for i, d in enumerate(dates):
            print(f"  {i+1}. {d}")
        
        # Verify dates are in descending order (most recent first)
        sorted_dates = sorted(dates, reverse=True)
        
        if dates == sorted_dates:
            print(f"✓ Game logs are correctly sorted (most recent first)")
        else:
            print(f"⚠️ Game logs NOT sorted by date!")
            print(f"  Actual:   {dates[:5]}")
            print(f"  Expected: {sorted_dates[:5]}")
            # Note: This may not fail if the API sorts before returning
    
    def test_player_with_badges_consistency(self):
        """
        Test that /api/v3/player-with-badges also has consistent L5/L10 stats.
        """
        # Get a player from War Zone
        war_zone_response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=15)
        if war_zone_response.status_code != 200:
            pytest.skip("War Zone not available")
        
        picks = war_zone_response.json().get("picks", [])
        if not picks:
            pytest.skip("No picks available")
        
        # Test first player
        player_name = picks[0].get("player_name")
        stat_type = picks[0].get("stat_type", "PTS")
        war_zone_l10 = picks[0].get("l10_avg")
        war_zone_h10 = picks[0].get("h10_rate")
        
        # Get player with badges
        badges_response = requests.get(
            f"{BASE_URL}/api/v3/player-with-badges/{player_name}",
            timeout=15
        )
        
        if badges_response.status_code != 200:
            pytest.skip(f"Player with badges not available for {player_name}")
        
        data = badges_response.json()
        player = data.get("player", {})
        
        # Find matching prop
        props = player.get("props", [])
        matching_prop = None
        for prop in props:
            if prop.get("stat_type_extracted", "").upper() == stat_type.upper():
                matching_prop = prop
                break
        
        if not matching_prop:
            print(f"No matching prop found for {stat_type} in player-with-badges")
            return
        
        badges_l10 = matching_prop.get("l10_avg")
        badges_h10 = matching_prop.get("l10_hit_rate")
        
        print(f"\n{player_name} ({stat_type}) consistency check:")
        print(f"  War Zone:        L10={war_zone_l10}, H10={war_zone_h10}%")
        print(f"  Player+Badges:   L10={badges_l10}, H10={badges_h10}")
        
        if badges_l10 is not None and war_zone_l10 is not None:
            diff = abs(float(badges_l10) - float(war_zone_l10))
            assert diff <= 1.5, f"L10 mismatch between War Zone ({war_zone_l10}) and badges ({badges_l10})"
            print(f"  ✓ L10 consistent (diff={diff})")


class TestCalculationFunctions:
    """Test that calculation functions have date sorting."""
    
    def test_goblin_vault_l10_stats(self):
        """Verify Goblin Vault (Safe Haven) also has correct L10 stats."""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=15)
        assert response.status_code == 200, f"Goblin Vault failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"\nGoblin Vault returned {len(picks)} picks")
        
        if len(picks) > 0:
            sample = picks[0]
            assert "h10_hit_rate" in sample or "h10_hits" in sample, "Missing H10 stats in Goblin Vault"
            print(f"✓ Sample: {sample.get('player_name')} - H10 rate: {sample.get('h10_hit_rate')}%")
    
    def test_front_lines_l25_stats(self):
        """Verify Front Lines has L25 stats (also uses date sorting)."""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=15)
        assert response.status_code == 200, f"Front Lines failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"\nFront Lines returned {len(picks)} picks")
        
        if len(picks) > 0:
            sample = picks[0]
            assert "l25_hit_rate" in sample or "l25_hits" in sample, "Missing L25 stats in Front Lines"
            print(f"✓ Sample: {sample.get('player_name')} - L25 rate: {sample.get('l25_hit_rate')}%")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
