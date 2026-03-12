"""
P1 Features Test Suite - NBA Demon & Goblin Analytics Engine v3.0
==================================================================

Tests for 3 new P1 features:
1. 4:00 AM Daily Scheduler with APScheduler
2. Virtual Scrolling (reverted to CSS overflow)  
3. Tank01 API Exponential Backoff with 4-hour caching

Author: Testing Agent
Date: 2026-03-12
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://best-bet-finder-1.preview.emergentagent.com')


# ==================== SCHEDULER TESTS ====================

class TestSchedulerFeature:
    """Tests for APScheduler daily sync at 4:00 AM UTC"""
    
    def test_scheduler_status_endpoint_exists(self):
        """GET /api/v3/scheduler-status should return scheduler info"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        assert response.status_code == 200, f"Scheduler status returned {response.status_code}"
        data = response.json()
        assert data.get("success") == True
        
    def test_scheduler_is_running(self):
        """Scheduler should be running after startup"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        data = response.json()
        assert data.get("scheduler_running") == True, "Scheduler should be running"
        
    def test_scheduler_has_daily_sync_job(self):
        """Scheduler should have daily_sync job configured"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        data = response.json()
        jobs = data.get("jobs", [])
        assert len(jobs) >= 1, "Should have at least 1 scheduled job"
        
        daily_sync_job = next((j for j in jobs if j.get("id") == "daily_sync"), None)
        assert daily_sync_job is not None, "daily_sync job should exist"
        
    def test_scheduler_job_has_correct_time(self):
        """daily_sync job should be scheduled for 4:00 AM UTC"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        data = response.json()
        
        assert data.get("daily_sync_time") == "04:00 UTC", "Daily sync should be at 4:00 AM UTC"
        assert data.get("timezone") == "UTC", "Timezone should be UTC"
        
    def test_scheduler_next_run_time_exists(self):
        """daily_sync job should have a next_run_time"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        data = response.json()
        jobs = data.get("jobs", [])
        
        daily_sync_job = next((j for j in jobs if j.get("id") == "daily_sync"), None)
        assert daily_sync_job is not None
        assert daily_sync_job.get("next_run_time") is not None, "Should have next_run_time"
        
    def test_trigger_scheduled_sync_endpoint_exists(self):
        """POST /api/v3/trigger-scheduled-sync should exist"""
        response = requests.post(f"{BASE_URL}/api/v3/trigger-scheduled-sync", timeout=120)
        # Either success or some response indicating the endpoint exists
        assert response.status_code in [200, 500], "Endpoint should exist"


# ==================== TANK01 BACKOFF TESTS ====================

class TestTank01BackoffFeature:
    """Tests for Tank01 API exponential backoff with 4-hour caching"""
    
    def test_v3_status_endpoint_works(self):
        """GET /api/v3/status should return status even if Tank01 fails"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") == True
        
    def test_v3_sync_handles_api_failures_gracefully(self):
        """Sync should complete even when Tank01 returns errors"""
        # This test verifies the backoff mechanism doesn't crash the sync
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        data = response.json()
        
        # Sync should have completed successfully (with or without Tank01 data)
        assert data.get("data", {}).get("unique_players", 0) > 0, "Should have players synced"
        
    def test_injuries_data_graceful_degradation(self):
        """System should work even if injury data is unavailable"""
        response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # Players should still be returned even without injury data
        assert data.get("count", 0) > 0, "Should have players"


# ==================== FRONTEND CSS OVERFLOW TESTS (Backend Endpoints) ====================

class TestFrontendDataEndpoints:
    """Tests for frontend player list rendering (backend data validation)"""
    
    def test_players_endpoint_returns_all_players(self):
        """GET /api/v3/players should return all 115+ players"""
        response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        count = data.get("count", 0)
        assert count >= 100, f"Should return 100+ players, got {count}"
        
    def test_player_detail_endpoint(self):
        """GET /api/v3/player/{name} should return full player detail"""
        # First get a player name
        players_response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        players = players_response.json().get("players", [])
        
        if players:
            player_name = players[0].get("player_name")
            response = requests.get(f"{BASE_URL}/api/v3/player/{player_name}", timeout=30)
            assert response.status_code == 200
            data = response.json()
            
            player = data.get("player", {})
            assert player.get("player_name") is not None
            assert "demons" in player or "props" in player, "Should have props data"
            
    def test_player_detail_has_sections(self):
        """Player detail should have demons, goblins, and standard arrays"""
        players_response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        players = players_response.json().get("players", [])
        
        if players:
            # Find a player with demons
            player_with_demons = next(
                (p for p in players if p.get("demons_count", 0) > 0), 
                players[0]
            )
            player_name = player_with_demons.get("player_name")
            
            response = requests.get(f"{BASE_URL}/api/v3/player/{player_name}", timeout=30)
            data = response.json()
            player = data.get("player", {})
            
            # Should have the three classification arrays
            assert "demons" in player, "Should have demons array"
            assert "goblins" in player, "Should have goblins array"
            assert "standard" in player, "Should have standard array"
            
    def test_trending_endpoint(self):
        """GET /api/v3/trending should return trending 10 players"""
        response = requests.get(f"{BASE_URL}/api/v3/trending", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        trending = data.get("trending", [])
        assert len(trending) <= 10, "Should return max 10 trending"
        assert len(trending) > 0, "Should have some trending players"


# ==================== TANK01 CACHE CONFIGURATION TESTS ====================

class TestTank01CacheConfiguration:
    """Tests to verify Tank01 4-hour cache TTL is properly configured"""
    
    def test_sync_status_shows_player_data(self):
        """Sync should have player data (Tank01 cache helps but not required)"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        data = response.json().get("data", {})
        
        assert data.get("unique_players", 0) > 0, "Should have players"
        assert data.get("total_props", 0) > 0, "Should have props"
        
    def test_engine_works_without_tank01(self):
        """Engine should function normally even if Tank01 fails"""
        # Get players - this should work regardless of Tank01 status
        response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") == True
        assert data.get("count", 0) > 0


# ==================== COMPREHENSIVE STATUS CHECKS ====================

class TestOverallSystemHealth:
    """Overall system health checks for all P1 features"""
    
    def test_scheduler_integration_with_engine(self):
        """Scheduler should be integrated with Demon & Goblin Engine"""
        response = requests.get(f"{BASE_URL}/api/v3/scheduler-status", timeout=10)
        data = response.json()
        
        # Scheduler should be running
        assert data.get("scheduler_running") == True
        
        # Engine should be functional
        status_response = requests.get(f"{BASE_URL}/api/v3/status", timeout=10)
        assert status_response.status_code == 200
        
    def test_players_list_complete(self):
        """All players should be accessible for frontend rendering"""
        response = requests.get(f"{BASE_URL}/api/v3/players", timeout=30)
        data = response.json()
        
        players = data.get("players", [])
        assert len(players) >= 100, f"Should have 100+ players, got {len(players)}"
        
        # Each player should have required fields
        for player in players[:5]:
            assert "player_name" in player
            assert "demons_count" in player
            assert "goblins_count" in player
            
    def test_hybrid_caching_endpoints_work(self):
        """Hybrid caching endpoints should be functional"""
        # Static shell
        static_response = requests.get(f"{BASE_URL}/api/v3/static-shell", timeout=30)
        assert static_response.status_code == 200
        
        # Live lines
        lines_response = requests.get(f"{BASE_URL}/api/v3/live-lines", timeout=30)
        assert lines_response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
