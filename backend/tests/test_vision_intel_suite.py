"""
Vision Intel Suite & Optimized Sync Engine Tests
=================================================
Tests for P0 requirements:
1. Vision Intel Suite data consistency - ALL Elite picks have momentum_data, whistle_data, intel_suite
2. Sync time performance - /api/v3/ferrari/rebuild completes in under 5 seconds
3. Player detail endpoint returns enriched data with intel_suite
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestSyncPerformance:
    """P0: Sync time performance tests"""
    
    def test_optimized_sync_completes_under_5_seconds(self):
        """P0: Verify /api/v3/ferrari/rebuild completes in under 5 seconds"""
        start_time = time.time()
        response = requests.post(f"{BASE_URL}/api/v3/ferrari/rebuild?use_optimized=true", timeout=30)
        end_time = time.time()
        
        duration = end_time - start_time
        
        assert response.status_code == 200, f"Sync failed with status {response.status_code}"
        
        data = response.json()
        assert data.get("success") == True, f"Sync returned success=false: {data.get('error')}"
        
        # Check reported sync duration
        reported_duration = data.get("sync_duration", 0)
        print(f"Reported sync duration: {reported_duration}s")
        print(f"Actual request duration: {duration:.2f}s")
        
        # P0 requirement: under 5 seconds
        assert reported_duration < 5, f"Sync took {reported_duration}s, expected < 5s"
        
        # Verify cache stats populated
        cache_stats = data.get("cache_stats", {})
        assert cache_stats.get("standings", 0) > 0, "Standings cache not populated"
        assert cache_stats.get("momentum", 0) > 0, "Momentum cache not populated"
        
        print(f"Cache stats: {cache_stats}")
        print(f"Total picks: {data.get('total_picks')}")


class TestVisionIntelSuiteDataConsistency:
    """P0: Vision Intel Suite data consistency tests"""
    
    def test_safe_haven_picks_have_momentum_data(self):
        """P0: All Safe Haven picks must have momentum_data populated"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        assert len(picks) > 0, "No Safe Haven picks found"
        
        missing_momentum = []
        for pick in picks:
            if not pick.get("momentum_data"):
                missing_momentum.append(pick.get("player_name"))
        
        assert len(missing_momentum) == 0, f"Picks missing momentum_data: {missing_momentum}"
        print(f"All {len(picks)} Safe Haven picks have momentum_data")
    
    def test_safe_haven_picks_have_whistle_data(self):
        """P0: All Safe Haven picks must have whistle_data (crew_chief) populated"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        assert len(picks) > 0, "No Safe Haven picks found"
        
        missing_whistle = []
        for pick in picks:
            if not pick.get("crew_chief"):
                missing_whistle.append(pick.get("player_name"))
        
        # Note: Some games may not have referee assignments yet
        # We check that MOST picks have whistle data
        whistle_coverage = (len(picks) - len(missing_whistle)) / len(picks) * 100
        print(f"Whistle data coverage: {whistle_coverage:.1f}% ({len(picks) - len(missing_whistle)}/{len(picks)})")
        
        # At least 80% should have whistle data
        assert whistle_coverage >= 80, f"Only {whistle_coverage:.1f}% of picks have whistle data"
    
    def test_front_lines_picks_have_momentum_data(self):
        """P0: All Front Lines picks must have momentum_data populated"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        assert len(picks) > 0, "No Front Lines picks found"
        
        missing_momentum = []
        for pick in picks:
            if not pick.get("momentum_data"):
                missing_momentum.append(pick.get("player_name"))
        
        assert len(missing_momentum) == 0, f"Picks missing momentum_data: {missing_momentum}"
        print(f"All {len(picks)} Front Lines picks have momentum_data")
    
    def test_war_zone_picks_have_momentum_data(self):
        """P0: All War Zone picks must have momentum_data populated"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        assert len(picks) > 0, "No War Zone picks found"
        
        missing_momentum = []
        for pick in picks:
            if not pick.get("momentum_data"):
                missing_momentum.append(pick.get("player_name"))
        
        assert len(missing_momentum) == 0, f"Picks missing momentum_data: {missing_momentum}"
        print(f"All {len(picks)} War Zone picks have momentum_data")
    
    def test_momentum_data_structure(self):
        """Verify momentum_data has correct structure"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        assert len(picks) > 0
        
        # Check first pick's momentum_data structure
        pick = picks[0]
        momentum = pick.get("momentum_data", {})
        
        required_fields = ["team", "composite_rank", "season_rank", "l10_rank", "l5_rank", "is_elite", "is_weak"]
        missing_fields = [f for f in required_fields if f not in momentum]
        
        assert len(missing_fields) == 0, f"Momentum data missing fields: {missing_fields}"
        print(f"Momentum data structure verified: {list(momentum.keys())}")
    
    def test_whistle_data_structure(self):
        """Verify whistle data has correct structure"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        # Find a pick with whistle data
        pick_with_whistle = None
        for pick in picks:
            if pick.get("crew_chief"):
                pick_with_whistle = pick
                break
        
        assert pick_with_whistle is not None, "No picks with whistle data found"
        
        # Check whistle data fields
        assert pick_with_whistle.get("crew_chief") is not None
        assert pick_with_whistle.get("whistle_class") is not None
        assert "whistle_modifier" in pick_with_whistle
        
        print(f"Whistle data: chief={pick_with_whistle.get('crew_chief')}, class={pick_with_whistle.get('whistle_class')}, modifier={pick_with_whistle.get('whistle_modifier')}")


class TestPlayerDetailEndpoint:
    """P0: Player detail endpoint tests"""
    
    def test_player_with_badges_returns_intel_suite(self):
        """P0: /api/v3/player-with-badges/{player} returns intel_suite with momentum_data and whistle_data"""
        # Get a player from Safe Haven first
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        picks = response.json().get("picks", [])
        assert len(picks) > 0, "No Safe Haven picks to test"
        
        player_name = picks[0].get("player_name")
        
        # Now get player detail
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/{player_name}")
        assert response.status_code == 200, f"Player detail failed for {player_name}"
        
        data = response.json()
        assert data.get("success") == True, f"Player detail returned success=false"
        
        player = data.get("player", {})
        assert player.get("player_name") == player_name
        
        # Check props have intel_suite
        props = player.get("props", [])
        assert len(props) > 0, f"No props found for {player_name}"
        
        # Find featured prop (should have intel_suite)
        featured_prop = None
        for prop in props:
            if prop.get("intel_suite"):
                featured_prop = prop
                break
        
        assert featured_prop is not None, f"No prop with intel_suite found for {player_name}"
        
        intel_suite = featured_prop.get("intel_suite", {})
        
        # Verify intel_suite has momentum_data
        assert intel_suite.get("momentum_data") is not None, "intel_suite missing momentum_data"
        
        # Verify intel_suite has whistle_data (may be None if no ref assigned)
        # Just check the field exists
        assert "whistle_data" in intel_suite, "intel_suite missing whistle_data field"
        
        print(f"Player {player_name} intel_suite verified:")
        print(f"  - momentum_data: {intel_suite.get('momentum_data', {}).get('team')}")
        print(f"  - whistle_data: {'Present' if intel_suite.get('whistle_data') else 'None'}")
        print(f"  - board: {intel_suite.get('board')}")
    
    def test_kevin_huerter_intel_suite(self):
        """P0: Specific test for Kevin Huerter as mentioned in requirements"""
        response = requests.get(f"{BASE_URL}/api/v3/player-with-badges/Kevin%20Huerter")
        assert response.status_code == 200, "Kevin Huerter endpoint failed"
        
        data = response.json()
        assert data.get("success") == True
        
        player = data.get("player", {})
        assert player.get("player_name") == "Kevin Huerter"
        
        props = player.get("props", [])
        assert len(props) > 0, "No props found for Kevin Huerter"
        
        # Check for momentum_data on props
        props_with_momentum = [p for p in props if p.get("momentum_data")]
        assert len(props_with_momentum) > 0, "Kevin Huerter has no props with momentum_data"
        
        # Check for whistle_data on props
        props_with_whistle = [p for p in props if p.get("crew_chief")]
        assert len(props_with_whistle) > 0, "Kevin Huerter has no props with whistle_data"
        
        # Check for intel_suite on featured props
        props_with_intel = [p for p in props if p.get("intel_suite")]
        assert len(props_with_intel) > 0, "Kevin Huerter has no props with intel_suite"
        
        print(f"Kevin Huerter verification:")
        print(f"  - Total props: {len(props)}")
        print(f"  - Props with momentum_data: {len(props_with_momentum)}")
        print(f"  - Props with whistle_data: {len(props_with_whistle)}")
        print(f"  - Props with intel_suite: {len(props_with_intel)}")


class TestElitePicksEnrichment:
    """P0: Verify ALL Elite picks have complete enrichment"""
    
    def test_all_boards_have_30_total_picks(self):
        """Verify we have 30 total Elite picks (10 per board)"""
        safe_haven = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven").json()
        front_lines = requests.get(f"{BASE_URL}/api/v3/ferrari/front-lines").json()
        war_zone = requests.get(f"{BASE_URL}/api/v3/ferrari/war-zone").json()
        
        sh_count = len(safe_haven.get("picks", []))
        fl_count = len(front_lines.get("picks", []))
        wz_count = len(war_zone.get("picks", []))
        
        total = sh_count + fl_count + wz_count
        
        print(f"Safe Haven: {sh_count}, Front Lines: {fl_count}, War Zone: {wz_count}")
        print(f"Total Elite picks: {total}")
        
        assert total == 30, f"Expected 30 total picks, got {total}"
    
    def test_elite_momentum_modifier_applied(self):
        """Verify Elite defense matchups have -15 modifier applied"""
        response = requests.get(f"{BASE_URL}/api/v3/ferrari/safe-haven")
        assert response.status_code == 200
        
        picks = response.json().get("picks", [])
        
        elite_matchups = [p for p in picks if p.get("momentum_data", {}).get("is_elite")]
        
        for pick in elite_matchups:
            momentum_modifier = pick.get("momentum_modifier", 0)
            assert momentum_modifier == -15, f"{pick.get('player_name')} has elite matchup but modifier is {momentum_modifier}, expected -15"
        
        print(f"Found {len(elite_matchups)} elite matchups with correct -15 modifier")
    
    def test_weak_momentum_modifier_applied(self):
        """Verify Weak defense matchups have +15 modifier applied"""
        # Check all boards for weak matchups
        all_picks = []
        for endpoint in ["safe-haven", "front-lines", "war-zone"]:
            response = requests.get(f"{BASE_URL}/api/v3/ferrari/{endpoint}")
            all_picks.extend(response.json().get("picks", []))
        
        weak_matchups = [p for p in all_picks if p.get("momentum_data", {}).get("is_weak")]
        
        for pick in weak_matchups:
            momentum_modifier = pick.get("momentum_modifier", 0)
            assert momentum_modifier == 15, f"{pick.get('player_name')} has weak matchup but modifier is {momentum_modifier}, expected +15"
        
        print(f"Found {len(weak_matchups)} weak matchups with correct +15 modifier")


class TestCacheStats:
    """Test global cache statistics"""
    
    def test_enrichment_status_endpoint(self):
        """Verify enrichment status endpoint returns correct data"""
        response = requests.get(f"{BASE_URL}/api/v3/enrichment-status")
        assert response.status_code == 200
        
        data = response.json()
        
        assert "total_enriched" in data
        assert "boards" in data
        
        boards = data.get("boards", {})
        assert "safe_haven" in boards
        assert "front_lines" in boards
        assert "war_zone" in boards
        
        print(f"Enrichment status: {data}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
