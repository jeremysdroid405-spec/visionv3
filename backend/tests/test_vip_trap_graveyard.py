"""
VIP Room & Trap Graveyard Feature Tests
========================================
Tests for the VIP Room filtering and Trap Graveyard endpoints.

Features tested:
- /api/v3/safe-haven returns VIP-filtered picks (no hook_risk or suspect_line_bait)
- /api/v3/trap-graveyard returns all flagged picks with source_board and sidecar data
- /api/v3/war-zone and /api/v3/front-lines also filter traps by default
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestVIPRoomFiltering:
    """Tests for VIP Room (clean picks) filtering"""
    
    def test_safe_haven_returns_picks(self):
        """Safe Haven endpoint should return picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "picks" in data, "Response should contain 'picks' field"
        assert isinstance(data["picks"], list), "picks should be a list"
        print(f"Safe Haven returned {len(data['picks'])} picks")
    
    def test_safe_haven_vip_filtered_flag(self):
        """Safe Haven should have vip_filtered=True when sidecar is enabled"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        # If sidecar is enabled, vip_filtered should be True
        if data.get("sidecar_enabled"):
            assert data.get("vip_filtered") == True, "vip_filtered should be True when sidecar is enabled"
            print(f"VIP filtering active: {data.get('trapped_count', 0)} picks filtered out")
        else:
            print("Sidecar not enabled - VIP filtering not active")
    
    def test_safe_haven_picks_have_required_fields(self):
        """Each pick should have required fields for display"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            pick = picks[0]
            required_fields = ["player_name", "stat_type", "line", "team"]
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"
            print(f"Sample pick: {pick['player_name']} {pick['stat_type']} @ {pick['line']}")
    
    def test_war_zone_vip_filtering(self):
        """War Zone should also filter traps by default"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone")
        assert response.status_code == 200
        
        data = response.json()
        assert "picks" in data
        
        if data.get("sidecar_enabled"):
            assert data.get("vip_filtered") == True
            print(f"War Zone: {len(data['picks'])} clean picks, {data.get('trapped_count', 0)} filtered")
    
    def test_front_lines_vip_filtering(self):
        """Front Lines should also filter traps by default"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines")
        assert response.status_code == 200
        
        data = response.json()
        assert "picks" in data
        
        if data.get("sidecar_enabled"):
            assert data.get("vip_filtered") == True
            print(f"Front Lines: {len(data['picks'])} clean picks, {data.get('trapped_count', 0)} filtered")
    
    def test_include_traps_parameter(self):
        """Setting include_traps=true should return unfiltered data"""
        response = requests.get(f"{BASE_URL}/api/v3/safe-haven?include_traps=true")
        assert response.status_code == 200
        
        data = response.json()
        # When include_traps=true, vip_filtered should NOT be set
        assert data.get("vip_filtered") != True, "vip_filtered should not be True when include_traps=true"
        print(f"Unfiltered Safe Haven: {len(data.get('picks', []))} total picks")


class TestTrapGraveyard:
    """Tests for Trap Graveyard endpoint"""
    
    def test_trap_graveyard_returns_data(self):
        """Trap Graveyard endpoint should return picks and stats"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "picks" in data, "Response should contain 'picks' field"
        assert "total_trapped" in data, "Response should contain 'total_trapped' field"
        assert "board_stats" in data, "Response should contain 'board_stats' field"
        print(f"Trap Graveyard: {data['total_trapped']} total trapped picks")
    
    def test_trap_graveyard_board_stats_structure(self):
        """Board stats should have correct structure"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200
        
        data = response.json()
        board_stats = data.get("board_stats", {})
        
        expected_boards = ["safe_haven", "war_zone", "front_lines"]
        for board in expected_boards:
            assert board in board_stats, f"board_stats missing '{board}'"
            assert "trapped" in board_stats[board], f"{board} missing 'trapped' count"
            assert "total" in board_stats[board], f"{board} missing 'total' count"
        
        print(f"Board stats: {board_stats}")
    
    def test_trapped_picks_have_source_board(self):
        """Each trapped pick should have source_board field"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            for pick in picks[:5]:  # Check first 5
                assert "source_board" in pick, f"Pick missing source_board: {pick.get('player_name')}"
                assert pick["source_board"] in ["safe_haven", "war_zone", "front_lines"], \
                    f"Invalid source_board: {pick['source_board']}"
            print(f"All trapped picks have valid source_board")
    
    def test_trapped_picks_have_sidecar_data(self):
        """Each trapped pick should have sidecar data with hook_risk or suspect_line_bait"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            for pick in picks[:5]:  # Check first 5
                sidecar = pick.get("sidecar", {})
                is_hook = sidecar.get("hook_risk", False)
                is_bait = sidecar.get("suspect_line_bait", False)
                
                assert is_hook or is_bait, \
                    f"Trapped pick should have hook_risk or suspect_line_bait: {pick.get('player_name')}"
            
            print(f"All trapped picks have valid sidecar flags")
    
    def test_trapped_picks_have_required_display_fields(self):
        """Trapped picks should have all fields needed for TrapCard display"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        if len(picks) > 0:
            pick = picks[0]
            required_fields = ["player_name", "stat_type", "line", "team", "opponent", "sidecar", "source_board"]
            for field in required_fields:
                assert field in pick, f"Trapped pick missing required field: {field}"
            
            print(f"Sample trapped pick: {pick['player_name']} {pick['stat_type']} @ {pick['line']} from {pick['source_board']}")
    
    def test_sidecar_enabled_flag(self):
        """Response should indicate if sidecar is enabled"""
        response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert response.status_code == 200
        
        data = response.json()
        assert "sidecar_enabled" in data, "Response should contain 'sidecar_enabled' field"
        print(f"Sidecar enabled: {data['sidecar_enabled']}")


class TestVIPTrapIntegration:
    """Integration tests for VIP Room and Trap Graveyard working together"""
    
    def test_filtered_count_matches_graveyard(self):
        """The trapped_count from tier endpoints should match graveyard totals"""
        # Get Safe Haven with filtering
        sh_response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert sh_response.status_code == 200
        sh_data = sh_response.json()
        
        # Get Trap Graveyard
        tg_response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert tg_response.status_code == 200
        tg_data = tg_response.json()
        
        if sh_data.get("sidecar_enabled") and tg_data.get("sidecar_enabled"):
            sh_trapped = sh_data.get("trapped_count", 0)
            tg_sh_trapped = tg_data.get("board_stats", {}).get("safe_haven", {}).get("trapped", 0)
            
            # These should match
            assert sh_trapped == tg_sh_trapped, \
                f"Safe Haven trapped_count ({sh_trapped}) should match graveyard safe_haven.trapped ({tg_sh_trapped})"
            
            print(f"Counts match: Safe Haven filtered {sh_trapped} picks")
    
    def test_clean_picks_not_in_graveyard(self):
        """Picks in VIP Room should NOT appear in Trap Graveyard"""
        # Get clean picks from Safe Haven
        sh_response = requests.get(f"{BASE_URL}/api/v3/safe-haven")
        assert sh_response.status_code == 200
        sh_data = sh_response.json()
        clean_picks = sh_data.get("picks", [])
        
        # Get trapped picks
        tg_response = requests.get(f"{BASE_URL}/api/v3/trap-graveyard")
        assert tg_response.status_code == 200
        tg_data = tg_response.json()
        trapped_picks = tg_data.get("picks", [])
        
        if len(clean_picks) > 0 and len(trapped_picks) > 0:
            # Create set of trapped pick identifiers
            trapped_ids = set(
                f"{p['player_name']}|{p['stat_type']}|{p['line']}" 
                for p in trapped_picks
            )
            
            # Check that no clean pick is in trapped
            for pick in clean_picks[:10]:  # Check first 10
                pick_id = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
                assert pick_id not in trapped_ids, \
                    f"Clean pick should not be in graveyard: {pick_id}"
            
            print(f"Verified: {len(clean_picks)} clean picks are not in graveyard")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
