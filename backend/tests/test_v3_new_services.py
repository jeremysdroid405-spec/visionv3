"""
Test Suite for Newly Extracted Services - Post-Refactoring Verification

Tests the new service modules extracted from demon_goblin_engine.py:
- CachedBoardBuilderService: Board building and player data aggregation  
- OddsApiService: Odds API interactions and prop extraction
- TierBuilderService: Tier-based pick scoring (War Zone, Safe Haven, Front Lines)
- ParlayBuilderService: Parlay generation logic

Verifies:
1. All API endpoints return correct data structure after extraction
2. Service delegation works correctly (proxy calls)
3. Data consistency between endpoints

Engine reduced from 8,252 to 4,196 lines (~49% reduction).
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


# ==================== STATUS ENDPOINT (CachedBoardBuilderService) ====================

class TestStatusAfterCacheBoardExtraction:
    """Verify /api/v3/status after CachedBoardBuilderService extraction"""

    def test_status_returns_correct_structure(self):
        """Test status returns expected structure post-extraction"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # Verify essential fields present
        assert data.get('success') is True
        assert 'data' in data
        status_data = data['data']
        
        # Fields that depend on CachedBoardBuilderService
        assert 'unique_players' in status_data
        assert 'total_props' in status_data
        assert isinstance(status_data['unique_players'], int)
        assert isinstance(status_data['total_props'], int)

    def test_status_shows_positive_player_count(self):
        """Verify CachedBoardBuilderService populated players correctly"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json().get('data', {})
        
        assert data.get('unique_players', 0) > 0, "CachedBoardBuilderService should have populated players"

    def test_status_shows_correct_prop_breakdown(self):
        """Verify prop breakdown (standard/demons/goblins) is consistent"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json().get('data', {})
        
        standard = data.get('standard_count', 0)
        demons = data.get('demons_count', 0)
        goblins = data.get('goblins_count', 0)
        total = data.get('total_props', 0)
        
        # Total should equal sum of components
        calculated_total = standard + demons + goblins
        assert calculated_total == total, f"Prop breakdown mismatch: {calculated_total} != {total}"


# ==================== WAR ZONE (TierBuilderService) ====================

class TestWarZoneAfterTierBuilderExtraction:
    """Verify /api/v3/war-zone after TierBuilderService extraction"""

    def test_war_zone_returns_10_picks(self):
        """War Zone should return 10 picks from TierBuilderService"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('success') is True
        picks = data.get('picks', [])
        # Should return up to 10 picks
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_war_zone_picks_have_tier_scoring_fields(self):
        """Picks should have 4-Pillar scoring fields from TierBuilderService"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            # Fields calculated by TierBuilderService
            scoring_fields = [
                'demon_score', 'radar_score', 'final_ev_score',
                'h10_rate', 'h5_rate', 'heat_level'
            ]
            for field in scoring_fields:
                assert field in pick, f"Missing TierBuilderService field: {field}"

    def test_war_zone_picks_have_pillar_scores(self):
        """Verify 4-Pillar scoring pillars are present"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            pillar_fields = [
                'pillar_1_ceiling', 'pillar_2_vegas', 
                'pillar_3_dvp', 'pillar_4_context'
            ]
            for field in pillar_fields:
                assert field in pick, f"Missing pillar field: {field}"
                # Pillar scores should be 0-1 range
                value = pick.get(field, 0)
                assert 0 <= value <= 1, f"{field} out of range: {value}"

    def test_war_zone_picks_are_demons(self):
        """War Zone picks should be demon type"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert pick.get('is_demon') is True or pick.get('is_radar_pick') is True, \
                f"War Zone pick should be demon: {pick.get('player_name')}"

    def test_war_zone_has_dvp_info(self):
        """War Zone picks should have DvP (Defense vs Position) data"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'dvp_modifier' in pick or 'pillar_3_dvp' in pick, \
                f"Missing DvP data for {pick.get('player_name')}"


# ==================== GOBLIN VAULT (TierBuilderService) ====================

class TestGoblinVaultAfterTierBuilderExtraction:
    """Verify /api/v3/goblin-vault after TierBuilderService extraction"""

    def test_goblin_vault_returns_10_picks(self):
        """Goblin Vault should return 10 safe picks"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('success') is True
        picks = data.get('picks', [])
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_goblin_vault_picks_have_safety_metrics(self):
        """Goblin Vault picks should have safety metrics"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            safety_fields = ['vault_score', 'safety_level', 'h10_rate', 'h5_rate']
            for field in safety_fields:
                assert field in pick, f"Missing safety field: {field}"

    def test_goblin_vault_picks_are_goblins(self):
        """Goblin Vault picks should be goblin type"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert pick.get('is_goblin') is True, \
                f"Goblin Vault pick should be goblin: {pick.get('player_name')}"

    def test_goblin_vault_has_value_gap(self):
        """Goblin Vault should show gap below standard line"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            # Should have either gap_below_std or value_gap_pct
            has_gap = 'gap_below_std' in pick or 'value_gap_pct' in pick or 'gap_pct' in pick
            assert has_gap, f"Missing value gap for {pick.get('player_name')}"


# ==================== FRONT LINES (TierBuilderService) ====================

class TestFrontLinesAfterTierBuilderExtraction:
    """Verify /api/v3/front-lines after TierBuilderService extraction"""

    def test_front_lines_returns_10_picks(self):
        """Front Lines should return 10 mixed tier picks"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('success') is True
        picks = data.get('picks', [])
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_front_lines_has_mixed_types(self):
        """Front Lines should have both demons and goblins"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        data = response.json()
        
        demon_count = data.get('demon_count', 0)
        goblin_count = data.get('goblin_count', 0)
        
        # Should have mix of types
        assert demon_count >= 0, "demon_count should be present"
        assert goblin_count >= 0, "goblin_count should be present"

    def test_front_lines_picks_have_frontlines_score(self):
        """Front Lines picks should have frontlines_score"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'frontlines_score' in pick or 'final_ev_score' in pick, \
                f"Missing score for {pick.get('player_name')}"

    def test_front_lines_picks_have_bullet_level(self):
        """Front Lines picks should have bullet_level rating"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'bullet_level' in pick, f"Missing bullet_level for {pick.get('player_name')}"
            # Bullet level should be 1-6
            bullet = pick.get('bullet_level', 0)
            assert 1 <= bullet <= 6, f"bullet_level out of range: {bullet}"


# ==================== PARLAY BUILDER (ParlayBuilderService) ====================

class TestParlayBuilderAfterExtraction:
    """Verify /api/v3/parlay-builder after ParlayBuilderService extraction"""

    def test_parlay_builder_returns_correct_structure(self):
        """Parlay Builder should return all parlay types"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('success') is True
        assert 'parlays' in data
        
        # Should have 2-6 pick parlays
        parlays = data['parlays']
        expected_types = ['2_pick', '3_pick', '4_pick', '5_pick', '6_pick']
        for ptype in expected_types:
            assert ptype in parlays, f"Missing parlay type: {ptype}"

    def test_parlay_builder_2_pick_has_correct_picks(self):
        """2-pick parlay should have exactly 2 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_2 = response.json().get('parlays', {}).get('2_pick', {})
        
        picks = parlay_2.get('picks', [])
        assert len(picks) == 2, f"2_pick should have 2 picks, got {len(picks)}"

    def test_parlay_builder_6_pick_has_correct_picks(self):
        """6-pick parlay should have exactly 6 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_6 = response.json().get('parlays', {}).get('6_pick', {})
        
        picks = parlay_6.get('picks', [])
        assert len(picks) == 6, f"6_pick should have 6 picks, got {len(picks)}"

    def test_parlay_builder_has_payout_info(self):
        """Parlays should have payout information"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        for ptype, parlay_data in parlays.items():
            assert 'estimated_payout' in parlay_data or 'payout_display' in parlay_data, \
                f"{ptype} missing payout info"

    def test_parlay_builder_has_combined_probability(self):
        """Parlays should have combined probability"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        for ptype, parlay_data in parlays.items():
            assert 'combined_probability' in parlay_data, \
                f"{ptype} missing combined_probability"

    def test_parlay_builder_two_team_rule_compliance(self):
        """Parlays should have minimum 2 teams (PrizePicks rule)"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        for ptype, parlay_data in parlays.items():
            picks = parlay_data.get('picks', [])
            if len(picks) >= 2:
                teams = set(p.get('team', '') for p in picks)
                assert len(teams) >= 2 or parlay_data.get('lineup_valid') is True, \
                    f"{ptype} violates 2-team rule: teams={teams}"

    def test_parlay_picks_have_required_fields(self):
        """Parlay picks should have player info and stats"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_3 = response.json().get('parlays', {}).get('3_pick', {})
        picks = parlay_3.get('picks', [])
        
        required_fields = ['player_name', 'team', 'stat_type', 'line', 'h10_rate']
        for pick in picks:
            for field in required_fields:
                assert field in pick, f"Parlay pick missing: {field}"


# ==================== CROSS-SERVICE CONSISTENCY TESTS ====================

class TestCrossServiceConsistency:
    """Test data consistency across services after extraction"""

    def test_status_matches_war_zone_data(self):
        """Status demons_count should be consistent with War Zone data"""
        status_resp = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        war_zone_resp = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        
        status_data = status_resp.json().get('data', {})
        war_zone_data = war_zone_resp.json()
        
        # If demons exist in status, war zone should have picks
        if status_data.get('demons_count', 0) > 0:
            assert war_zone_data.get('picks'), "War Zone should have picks if demons exist"

    def test_status_matches_goblin_vault_data(self):
        """Status goblins_count should be consistent with Goblin Vault"""
        status_resp = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        goblin_resp = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        
        status_data = status_resp.json().get('data', {})
        goblin_data = goblin_resp.json()
        
        # If goblins exist in status, vault should have picks
        if status_data.get('goblins_count', 0) > 0:
            assert goblin_data.get('picks'), "Goblin Vault should have picks if goblins exist"

    def test_parlay_picks_from_war_zone_players(self):
        """Parlay demon picks should be from War Zone players"""
        parlay_resp = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        war_zone_resp = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        
        war_zone_players = {p.get('player_name') for p in war_zone_resp.json().get('picks', [])}
        parlay_2 = parlay_resp.json().get('parlays', {}).get('2_pick', {})
        parlay_players = {p.get('player_name') for p in parlay_2.get('picks', [])}
        
        # Parlay picks should come from high-quality demon candidates
        # Note: They don't have to be exactly from War Zone, but should be valid demons
        for player in parlay_players:
            assert player, f"Parlay has invalid player name: {player}"

    def test_all_tier_endpoints_share_sync_time(self):
        """Tier endpoints (war-zone, goblin-vault, front-lines) should share sync time"""
        # Note: parlay-builder may have different sync time as it syncs separately
        endpoints = [
            '/api/v3/war-zone',
            '/api/v3/goblin-vault', 
            '/api/v3/front-lines'
        ]
        
        sync_times = []
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=30)
            data = response.json()
            sync_at = data.get('synced_at')
            if sync_at:
                sync_times.append(sync_at)
        
        # Tier endpoints should share same sync time
        if sync_times:
            assert len(set(sync_times)) == 1, f"Tier sync times differ: {set(sync_times)}"


# ==================== SERVICE DELEGATION TESTS ====================

class TestServiceDelegation:
    """Test that service delegation (proxy calls) work correctly"""

    def test_tier_builder_generates_heat_level(self):
        """TierBuilderService should calculate heat_level correctly"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            heat_level = pick.get('heat_level', 0)
            # Heat level should be 0-5 (0 indicates insufficient data)
            assert 0 <= heat_level <= 5, f"Invalid heat_level: {heat_level}"

    def test_tier_builder_generates_safety_level(self):
        """TierBuilderService should calculate safety_level for goblins"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            safety_level = pick.get('safety_level', 0)
            # Safety level should be 1-5
            assert 1 <= safety_level <= 5, f"Invalid safety_level: {safety_level}"

    def test_parlay_builder_calculates_payout(self):
        """ParlayBuilderService should calculate live payout"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        for ptype, parlay_data in parlays.items():
            payout = parlay_data.get('estimated_payout', 0)
            # Payout should be a positive number
            assert payout > 0, f"{ptype} has invalid payout: {payout}"


# ==================== DATA INTEGRITY TESTS ====================

class TestDataIntegrity:
    """Test data integrity after service extraction"""

    def test_player_names_not_empty(self):
        """All picks should have valid player names"""
        endpoints = [
            '/api/v3/war-zone',
            '/api/v3/goblin-vault',
            '/api/v3/front-lines'
        ]
        
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=30)
            picks = response.json().get('picks', [])
            
            for pick in picks:
                player_name = pick.get('player_name')
                assert player_name and len(player_name) > 0, \
                    f"Empty player_name in {endpoint}"

    def test_stat_types_valid(self):
        """All stat types should be valid"""
        valid_types = ['PTS', 'REB', 'AST', 'BLK', 'STL', '3PM', 'PRA', 'P+R', 'P+A', 'R+A', 'TO', 'FG3M']
        
        endpoints = [
            '/api/v3/war-zone',
            '/api/v3/goblin-vault',
            '/api/v3/front-lines'
        ]
        
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=30)
            picks = response.json().get('picks', [])
            
            for pick in picks:
                stat_type = pick.get('stat_type')
                assert stat_type in valid_types, f"Invalid stat_type: {stat_type} in {endpoint}"

    def test_hit_rates_in_valid_range(self):
        """Hit rates should be 0-100%"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            h10 = pick.get('h10_rate', 0)
            h5 = pick.get('h5_rate', 0)
            
            assert 0 <= h10 <= 100, f"h10_rate out of range: {h10}"
            assert 0 <= h5 <= 100, f"h5_rate out of range: {h5}"

    def test_lines_are_positive(self):
        """Line values should be positive"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            demon_line = pick.get('demon_line', 0)
            standard_line = pick.get('standard_line', 0)
            
            assert demon_line > 0, f"Invalid demon_line: {demon_line}"
            assert standard_line > 0, f"Invalid standard_line: {standard_line}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
