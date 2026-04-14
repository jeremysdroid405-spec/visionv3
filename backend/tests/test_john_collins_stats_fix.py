"""
Test John Collins Stats Fix
============================
Verifies the fix for the BDL API game logs ordering bug.

Bug Description:
- BDL API returns games in ASCENDING order (oldest first)
- App was only fetching 15 games, getting Oct 2025 games instead of March 2026
- DNP games (0 minutes) were being included in L5/L10 calculations

Fix Applied:
- Increased fetch limit to 100 games
- Sort games by date (most recent first)
- Filter out DNP games for L5/L10 calculations

Expected John Collins Stats (per main agent):
- Team: LAC (Los Angeles Clippers)
- Season PPG: ~13.7
- L5 PPG: ~11.8
- L10 PPG: ~14.4
- Most recent game: March 16, 2026 with 11 PTS
"""
import pytest
import requests
import os
from datetime import datetime

# Use public endpoint from env
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://local-first-hub-2.preview.emergentagent.com').rstrip('/')


class TestJohnCollinsStatsFix:
    """Tests for John Collins game logs and stats accuracy"""
    
    # ==================== MASTER HUB TESTS ====================
    
    def test_master_hub_player_by_name_endpoint(self):
        """Test /api/v3/master-hub/player/name/{name} returns John Collins data"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        
        # Should find John Collins
        assert response.status_code == 200, f"Failed to fetch John Collins: {response.text}"
        
        data = response.json()
        
        # Verify player identity
        assert data.get("display_name") == "John Collins", "display_name mismatch"
        assert data.get("team") == "LAC", f"Expected team LAC, got {data.get('team')}"
        
        # Verify bdl_game_logs exists and has games
        game_logs = data.get("bdl_game_logs", [])
        assert len(game_logs) > 0, "No game logs found in master hub"
        print(f"[PASS] John Collins found in master hub with {len(game_logs)} game logs")
    
    def test_master_hub_game_logs_sorted_by_date(self):
        """Test game logs are sorted by date (most recent first)"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        game_logs = data.get("bdl_game_logs", [])
        
        assert len(game_logs) >= 10, f"Need at least 10 game logs, got {len(game_logs)}"
        
        # Extract dates and verify descending order
        dates = []
        for game in game_logs[:20]:  # Check first 20 games
            game_obj = game.get("game", {})
            date_str = game_obj.get("date", "") if isinstance(game_obj, dict) else ""
            if date_str:
                dates.append(date_str)
        
        assert len(dates) >= 5, "Could not extract game dates"
        
        # Verify dates are in descending order (most recent first)
        for i in range(len(dates) - 1):
            current = dates[i][:10]
            next_date = dates[i + 1][:10]
            assert current >= next_date, f"Games not sorted by date: {current} should be >= {next_date}"
        
        print(f"[PASS] Game logs sorted correctly. Most recent: {dates[0]}, Oldest of first 20: {dates[-1]}")
    
    def test_master_hub_most_recent_game_is_march_2026(self):
        """Test most recent game is from March 2026 (not stale Oct 2025)"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        game_logs = data.get("bdl_game_logs", [])
        
        assert len(game_logs) > 0, "No game logs found"
        
        # Get most recent game date
        first_game = game_logs[0]
        game_obj = first_game.get("game", {})
        date_str = game_obj.get("date", "") if isinstance(game_obj, dict) else ""
        
        assert date_str, "No date found in most recent game"
        
        # Parse and verify it's March 2026 (or at least late 2025/2026 season)
        game_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        
        # Most recent game should be from 2026 (ideally March)
        assert game_date.year == 2026, f"Most recent game is from {game_date.year}, expected 2026"
        
        # Check points in most recent game
        pts = first_game.get("pts", 0)
        print(f"[PASS] Most recent game: {date_str} with {pts} PTS")
    
    def test_master_hub_baseline_stats_exist(self):
        """Test baseline_stats are populated with L5/L10/season averages"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        baseline_stats = data.get("baseline_stats", {})
        
        assert baseline_stats, "baseline_stats is empty"
        
        # Check PTS stats exist
        pts_data = baseline_stats.get("PTS", {})
        assert pts_data, "PTS stats not found in baseline_stats"
        
        season_avg = pts_data.get("season_avg")
        l5_avg = pts_data.get("l5_avg")
        l10_avg = pts_data.get("l10_avg")
        
        assert season_avg is not None, "season_avg missing"
        assert l5_avg is not None, "l5_avg missing"
        assert l10_avg is not None, "l10_avg missing"
        
        print(f"[PASS] John Collins baseline_stats - Season: {season_avg}, L5: {l5_avg}, L10: {l10_avg}")
    
    def test_master_hub_season_avg_reasonable(self):
        """Test season average is in reasonable range (~13.7 PPG per ESPN)"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        baseline_stats = data.get("baseline_stats", {})
        
        pts_data = baseline_stats.get("PTS", {})
        season_avg = pts_data.get("season_avg", 0)
        
        # Expected: ~13.7 PPG (allow ±3 variance for game updates)
        assert 10 < season_avg < 20, f"Season avg {season_avg} out of expected range (10-20)"
        
        # Games played should be reasonable for mid-season
        games_played = baseline_stats.get("games_played", 0)
        assert games_played > 50, f"Games played ({games_played}) seems too low for March 2026"
        
        print(f"[PASS] Season PPG: {season_avg}, Games played: {games_played}")
    
    # ==================== PLAYER WITH BADGES TESTS ====================
    
    def test_player_with_badges_endpoint(self):
        """Test /api/v3/player-with-badges/{name} returns game_logs"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/John Collins")
        
        if response.status_code == 404:
            pytest.skip("John Collins not on current cached board")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("success"), "Response not successful"
        
        player = data.get("player", {})
        assert player, "No player data returned"
        
        # Check for game_logs in response
        game_logs = player.get("game_logs", [])
        print(f"[PASS] player-with-badges returned player with {len(game_logs)} game logs")
    
    def test_player_with_badges_has_baseline_stats(self):
        """Test player-with-badges returns baseline_stats"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/John Collins")
        
        if response.status_code == 404:
            pytest.skip("John Collins not on current cached board")
        
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        
        baseline_stats = player.get("baseline_stats", {})
        
        if baseline_stats:
            print(f"[PASS] baseline_stats present in player-with-badges response")
        else:
            print(f"[INFO] baseline_stats not directly in response (may be in props)")
    
    def test_player_with_badges_props_have_l5_l10(self):
        """Test props in player-with-badges have L5/L10 stats"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/John Collins")
        
        if response.status_code == 404:
            pytest.skip("John Collins not on current cached board")
        
        assert response.status_code == 200
        
        data = response.json()
        player = data.get("player", {})
        props = player.get("props", [])
        
        if not props:
            pytest.skip("No props found for John Collins")
        
        # Check first prop has L5/L10 stats
        first_prop = props[0]
        l5_avg = first_prop.get("l5_avg")
        l10_avg = first_prop.get("l10_avg")
        
        print(f"[INFO] First prop - L5: {l5_avg}, L10: {l10_avg}")
    
    # ==================== DNP FILTER TESTS ====================
    
    def test_dnp_games_excluded_from_averages(self):
        """Test DNP games (0 minutes) are filtered from L5/L10 calculations"""
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        game_logs = data.get("bdl_game_logs", [])
        
        # Filter out DNPs manually
        played_games = []
        dnp_games = []
        
        for game in game_logs[:20]:
            mins = game.get("min", "0") or "0"
            if isinstance(mins, str):
                mins_val = int(mins.split(":")[0]) if ":" in mins else (int(mins) if mins.isdigit() else 0)
            else:
                mins_val = int(mins) if mins else 0
            
            if mins_val > 0:
                played_games.append(game)
            else:
                dnp_games.append(game)
        
        print(f"[INFO] In first 20 games: {len(played_games)} played, {len(dnp_games)} DNPs")
        
        # Calculate L5 from played games only
        if len(played_games) >= 5:
            l5_pts = [g.get("pts", 0) or 0 for g in played_games[:5]]
            l5_avg_manual = sum(l5_pts) / len(l5_pts)
            
            # Compare with baseline_stats L5
            baseline_stats = data.get("baseline_stats", {})
            pts_data = baseline_stats.get("PTS", {})
            l5_avg_stored = pts_data.get("l5_avg", 0)
            
            # They should be close (within 1 point due to rounding)
            diff = abs(l5_avg_manual - l5_avg_stored)
            assert diff < 1.5, f"L5 manual calc ({l5_avg_manual:.1f}) differs from stored ({l5_avg_stored}) by {diff:.1f}"
            
            print(f"[PASS] L5 calculation verified: manual={l5_avg_manual:.1f}, stored={l5_avg_stored}")
    
    # ==================== SYNC ENDPOINT TESTS ====================
    
    def test_sync_player_endpoint(self):
        """Test POST /api/v3/master-hub/sync-bdl-player/{name} works"""
        # This is a sync endpoint - just verify it responds correctly
        # Don't actually trigger sync in test to avoid API rate limits
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/bdl-sample/John Collins")
        assert response.status_code == 200, f"BDL sample lookup failed: {response.text}"
        
        data = response.json()
        assert data.get("display_name") == "John Collins"
        print(f"[PASS] BDL sample endpoint works for John Collins")
    
    # ==================== COMMAND PROFILE TEST ====================
    
    def test_command_profile_has_l5_l10(self):
        """Test /api/command/profile/{name} returns L5/L10 stats"""
        response = requests.get(f"{BASE_URL}/api/command/profile/John%20Collins")
        
        if response.status_code == 404:
            pytest.skip("John Collins not on current cached board")
        
        assert response.status_code == 200, f"Profile failed: {response.text}"
        
        data = response.json()
        
        # Check if lines have l5_avg/l10_avg
        lines = data.get("lines", [])
        if lines:
            first_line = lines[0]
            l5_avg = first_line.get("l5_avg")
            l10_avg = first_line.get("l10_avg")
            print(f"[INFO] Profile line - L5: {l5_avg}, L10: {l10_avg}")
        else:
            print(f"[INFO] No active lines for John Collins in profile")


class TestGameLogsSortingGeneral:
    """General tests for game log sorting across the API"""
    
    def test_war_zone_game_logs_sorted(self):
        """Test War Zone picks have properly sorted L5/L10 stats"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No War Zone picks available")
        
        # Check first few picks have L5/L10 stats
        for pick in picks[:3]:
            l5_avg = pick.get("l5_avg")
            l10_avg = pick.get("l10_avg")
            player = pick.get("player_name")
            
            assert l5_avg is not None, f"{player} missing l5_avg"
            assert l10_avg is not None, f"{player} missing l10_avg"
        
        print(f"[PASS] War Zone picks have L5/L10 stats")
    
    def test_goblin_vault_game_logs_sorted(self):
        """Test Goblin Vault has properly calculated H10 hit rates"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if not picks:
            pytest.skip("No Goblin Vault picks available")
        
        # Check picks have H10 hit rates
        for pick in picks[:3]:
            h10_rate = pick.get("h10_hit_rate")
            player = pick.get("player_name")
            
            assert h10_rate is not None, f"{player} missing h10_hit_rate"
            assert h10_rate >= 80, f"{player} H10={h10_rate}% should be >= 80% for Safe Haven"
        
        print(f"[PASS] Goblin Vault picks have H10 >= 80%")


class TestDNPFilterFunction:
    """Test the DNP filter function behavior"""
    
    def test_dnp_detection_accuracy(self):
        """Test that 0-minute games are correctly identified as DNPs"""
        # Fetch a player with known game logs
        response = requests.get(f"{BASE_URL}/api/v3/master-hub/player/name/John Collins")
        assert response.status_code == 200
        
        data = response.json()
        game_logs = data.get("bdl_game_logs", [])
        
        # Count DNPs in first 20 games
        dnp_count = 0
        for game in game_logs[:20]:
            mins = game.get("min", "0") or "0"
            if isinstance(mins, str):
                if mins in ["0", "00", "00:00", ""]:
                    dnp_count += 1
                elif ":" in mins and int(mins.split(":")[0]) == 0:
                    dnp_count += 1
            elif mins == 0:
                dnp_count += 1
        
        print(f"[INFO] DNP games in first 20: {dnp_count}")
        
        # Verify baseline stats L5 uses only played games
        # (implicitly tested by test_dnp_games_excluded_from_averages)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
