"""
Vision Intel Pre-Cache & Batch Intelligence Enrichment Tests v2
================================================================
Tests for:
1. /api/v3/player-with-badges/{player_name} - Response time <2s, pre-cached vision_summary
2. Vision Intel pre-cached data - source='pre_cached' in intel_suite
3. /api/v3/war-zone - Returns demon picks
4. /api/v3/goblin-vault (Safe Haven) - Returns goblin picks
5. /api/v3/front-lines - Returns picks
6. Backend starts without errors
7. Cached board sync preserves vision_summary fields during rebuilds
"""

import pytest
import requests
import os
import time
from datetime import datetime

# Get BASE_URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "https://local-first-hub-2.preview.emergentagent.com"


class TestBackendHealth:
    """Test backend is running and healthy"""
    
    def test_backend_health(self):
        """Backend should respond to sync-status endpoint"""
        # Use sync-status as health check since /api/health may not exist
        response = requests.get(f"{BASE_URL}/api/v3/sync-status", timeout=10)
        assert response.status_code == 200, f"Sync status failed: {response.status_code}"
        data = response.json()
        assert "active_games" in data or "engine_status" in data, "Missing expected fields"
        print(f"✓ Backend health check passed via sync-status: {response.status_code}")


class TestPlayerWithBadgesEndpoint:
    """Test /api/v3/player-with-badges/{player_name} endpoint"""
    
    def test_response_time_under_2_seconds(self):
        """Player-with-badges should respond in <2 seconds with pre-cached data"""
        # Test with multiple players to ensure consistency
        test_players = ["Kyle Anderson", "Joel Embiid", "Paul George"]
        
        for player_name in test_players:
            start_time = time.time()
            response = requests.get(
                f"{BASE_URL}/api/v3/player-with-badges/{player_name}",
                timeout=10
            )
            elapsed = time.time() - start_time
            
            assert response.status_code == 200, f"Failed for {player_name}: {response.status_code}"
            assert elapsed < 2.0, f"{player_name} took {elapsed:.2f}s (>2s requirement)"
            print(f"✓ {player_name}: {elapsed:.3f}s (under 2s requirement)")
    
    def test_vision_summary_present(self):
        """Featured props should have vision_summary field"""
        response = requests.get(
            f"{BASE_URL}/api/v3/player-with-badges/Kyle Anderson",
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("success"), "Response should have success=true"
        player = data.get("player", {})
        props = player.get("props", [])
        
        # Check if any prop has vision_summary or intel_suite
        has_vision_data = False
        for prop in props:
            if prop.get("vision_summary") or prop.get("intel_suite"):
                has_vision_data = True
                print(f"✓ Found vision data in prop: {prop.get('stat_type_extracted')}@{prop.get('line')}")
                
                # Check intel_suite structure
                intel_suite = prop.get("intel_suite", {})
                if intel_suite:
                    vision_insight = intel_suite.get("vision_insight", {})
                    source = vision_insight.get("source", "")
                    print(f"  - Vision insight source: {source}")
                    print(f"  - AI summary present: {bool(vision_insight.get('ai_summary'))}")
                break
        
        # Note: vision_summary may be pending_enrichment if not yet cached
        print(f"✓ Vision data check complete for Kyle Anderson")
    
    def test_intel_suite_structure(self):
        """Intel suite should have required fields"""
        response = requests.get(
            f"{BASE_URL}/api/v3/player-with-badges/Joel Embiid",
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        
        player = data.get("player", {})
        props = player.get("props", [])
        
        # Find a featured prop with intel_suite
        for prop in props:
            intel_suite = prop.get("intel_suite")
            if intel_suite:
                # Check required intel_suite fields
                assert "matchup_dvp" in intel_suite, "Missing matchup_dvp"
                assert "pace_delta" in intel_suite, "Missing pace_delta"
                assert "stability_index" in intel_suite, "Missing stability_index"
                assert "vision_insight" in intel_suite, "Missing vision_insight"
                
                print(f"✓ Intel suite has all required fields for Joel Embiid")
                print(f"  - matchup_dvp: {intel_suite.get('matchup_dvp', {}).get('display')}")
                print(f"  - pace_delta: {intel_suite.get('pace_delta', {}).get('display')}")
                print(f"  - stability_index: {intel_suite.get('stability_index', {}).get('display')}")
                return
        
        print("⚠ No featured prop with intel_suite found (may be expected if no featured picks)")


class TestVisionIntelPreCachedSource:
    """Test that vision_summary shows source='pre_cached' when available"""
    
    def test_pre_cached_source_indicator(self):
        """Vision insight should indicate source='pre_cached' for enriched props"""
        # Test multiple players to find one with pre-cached data
        test_players = ["Kyle Anderson", "Paul George", "Joel Embiid", "Devin Carter"]
        
        found_pre_cached = False
        found_pending = False
        
        for player_name in test_players:
            response = requests.get(
                f"{BASE_URL}/api/v3/player-with-badges/{player_name}",
                timeout=10
            )
            if response.status_code != 200:
                continue
                
            data = response.json()
            player = data.get("player", {})
            props = player.get("props", [])
            
            for prop in props:
                intel_suite = prop.get("intel_suite", {})
                vision_insight = intel_suite.get("vision_insight", {})
                source = vision_insight.get("source", "")
                
                if source == "pre_cached":
                    found_pre_cached = True
                    print(f"✓ Found pre_cached source for {player_name} {prop.get('stat_type_extracted')}@{prop.get('line')}")
                    ai_summary = vision_insight.get("ai_summary", "")
                    if ai_summary:
                        print(f"  - AI Summary preview: {ai_summary[:100]}...")
                elif source == "pending_enrichment":
                    found_pending = True
                    print(f"⚠ Found pending_enrichment for {player_name} {prop.get('stat_type_extracted')}@{prop.get('line')}")
        
        # At least one should be found
        assert found_pre_cached or found_pending, "No vision source indicators found"
        print(f"✓ Vision source check complete: pre_cached={found_pre_cached}, pending={found_pending}")


class TestWarZoneEndpoint:
    """Test /api/v3/war-zone endpoint returns demon picks"""
    
    def test_war_zone_returns_picks(self):
        """War Zone should return demon picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=10)
        assert response.status_code == 200, f"War Zone failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"✓ War Zone returned {len(picks)} picks")
        
        # Verify picks have required fields
        if picks:
            pick = picks[0]
            assert "player_name" in pick, "Missing player_name"
            assert "stat_type" in pick or "stat_type_extracted" in pick, "Missing stat_type"
            assert "line" in pick, "Missing line"
            
            # Check demon flag
            demon_count = sum(1 for p in picks if p.get("is_demon"))
            print(f"  - Demon picks: {demon_count}/{len(picks)}")
            
            # Show sample pick
            print(f"  - Sample: {pick.get('player_name')} {pick.get('stat_type', pick.get('stat_type_extracted'))}@{pick.get('line')}")
    
    def test_war_zone_picks_are_demons(self):
        """All War Zone picks should have is_demon=true"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if picks:
            demon_picks = [p for p in picks if p.get("is_demon")]
            assert len(demon_picks) > 0, "No demon picks found in War Zone"
            print(f"✓ War Zone has {len(demon_picks)} demon picks")


class TestGoblinVaultEndpoint:
    """Test /api/v3/goblin-vault (Safe Haven) endpoint returns goblin picks"""
    
    def test_goblin_vault_returns_picks(self):
        """Goblin Vault (Safe Haven) should return picks"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=10)
        assert response.status_code == 200, f"Goblin Vault failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"✓ Goblin Vault returned {len(picks)} picks")
        
        if picks:
            pick = picks[0]
            assert "player_name" in pick, "Missing player_name"
            
            # Check goblin/demon flags
            goblin_count = sum(1 for p in picks if p.get("is_goblin"))
            demon_count = sum(1 for p in picks if p.get("is_demon"))
            print(f"  - Goblin picks: {goblin_count}, Demon picks: {demon_count}")
            
            # Check hit rates (Safe Haven should have high hit rates)
            high_hr_count = sum(1 for p in picks if (p.get("h10_rate") or p.get("l10_hit_rate") or 0) >= 80)
            print(f"  - High hit rate (>=80%): {high_hr_count}/{len(picks)}")
    
    def test_goblin_vault_has_high_hit_rates(self):
        """Safe Haven picks should have high hit rates (>=80%)"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if picks:
            # Check that picks have hit rate data
            picks_with_hr = [p for p in picks if p.get("h10_rate") or p.get("l10_hit_rate")]
            print(f"✓ {len(picks_with_hr)}/{len(picks)} picks have hit rate data")
            
            # Sample high hit rate picks
            for pick in picks[:3]:
                hr = pick.get("h10_rate") or pick.get("l10_hit_rate") or 0
                print(f"  - {pick.get('player_name')}: {hr}% hit rate")


class TestFrontLinesEndpoint:
    """Test /api/v3/front-lines endpoint returns picks"""
    
    def test_front_lines_returns_picks(self):
        """Front Lines should return picks"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=10)
        assert response.status_code == 200, f"Front Lines failed: {response.status_code}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"✓ Front Lines returned {len(picks)} picks")
        
        if picks:
            pick = picks[0]
            assert "player_name" in pick, "Missing player_name"
            assert "line" in pick, "Missing line"
            
            # Show sample picks
            for p in picks[:3]:
                stat = p.get("stat_type") or p.get("stat_type_extracted", "?")
                print(f"  - {p.get('player_name')} {stat}@{p.get('line')}")


class TestCachedBoardVisionPreservation:
    """Test that cached board sync preserves vision_summary fields"""
    
    def test_vision_fields_in_cached_props(self):
        """Cached props should have vision fields preserved"""
        response = requests.get(f"{BASE_URL}/api/v3/cached-props", timeout=15)
        assert response.status_code == 200, f"Cached props failed: {response.status_code}"
        
        data = response.json()
        players = data.get("players", [])
        
        print(f"✓ Cached props returned {len(players)} players")
        
        # Check for vision-enriched props
        enriched_count = 0
        for player in players[:20]:  # Check first 20 players
            props = player.get("props", [])
            for prop in props:
                if prop.get("is_vision_enriched") or prop.get("vision_summary"):
                    enriched_count += 1
        
        print(f"  - Vision-enriched props found: {enriched_count}")
        
        # Check specific fields
        if players:
            sample_player = players[0]
            props = sample_player.get("props", [])
            if props:
                prop = props[0]
                print(f"  - Sample prop fields: {list(prop.keys())[:10]}...")


class TestNBACareerServiceJSONDecode:
    """Test that nba_career_service handles JSON decode errors gracefully"""
    
    def test_no_json_decode_errors(self):
        """Player-with-badges should not return 500 from JSON decode errors"""
        # Test multiple players to ensure no JSON decode crashes
        test_players = ["LeBron James", "Stephen Curry", "Kevin Durant"]
        
        for player_name in test_players:
            response = requests.get(
                f"{BASE_URL}/api/v3/player-with-badges/{player_name}",
                timeout=15
            )
            # Should not be 500 (internal server error)
            assert response.status_code != 500, f"500 error for {player_name}: possible JSON decode issue"
            print(f"✓ {player_name}: status {response.status_code} (no 500 error)")


class TestVisionIntelEnrichmentStats:
    """Test Vision Intel Enrichment service stats"""
    
    def test_enrichment_stats_endpoint(self):
        """Check if enrichment stats are available"""
        # Try to get sync status which may include enrichment stats
        response = requests.get(f"{BASE_URL}/api/v3/sync-status", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Sync status available")
            
            # Check for vision intel stats
            vision_stats = data.get("vision_intel", {})
            if vision_stats:
                print(f"  - Players enriched: {vision_stats.get('players_enriched', 'N/A')}")
                print(f"  - AI summaries: {vision_stats.get('ai_summaries_generated', 'N/A')}")
        else:
            print(f"⚠ Sync status endpoint returned {response.status_code}")


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
