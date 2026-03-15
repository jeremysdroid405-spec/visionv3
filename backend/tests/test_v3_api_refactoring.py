"""
Test Suite for v3 API Endpoints - Post-Refactoring Verification

Tests all v3 endpoints to verify no regressions occurred after extracting
services from demon_goblin_engine.py:
- RosterService, PhotoService, PropsService, SyncService

Endpoints tested:
- GET /api/v3/status - Sync status
- GET /api/v3/war-zone - Top demon picks
- GET /api/v3/goblin-vault - Safe picks
- GET /api/v3/front-lines - Mixed tier picks
- GET /api/v3/parlay-builder - Parlay tickets
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


# ==================== STATUS ENDPOINT ====================

class TestStatusEndpoint:
    """Test /api/v3/status endpoint - verify sync status"""

    def test_status_returns_200(self):
        """Test that status endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_status_returns_success_true(self):
        """Test that status returns success: true"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json()
        assert data.get('success') is True, "Expected success to be true"

    def test_status_has_data_object(self):
        """Test that status has a data object"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json()
        assert 'data' in data, "Expected 'data' key in response"

    def test_status_data_has_required_fields(self):
        """Test status data has required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json().get('data', {})
        
        required_fields = ['last_sync', 'sync_date', 'unique_players', 'total_props',
                          'standard_count', 'demons_count', 'goblins_count', 'season']
        
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_status_counts_are_positive(self):
        """Test that data counts are positive (data is synced)"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json().get('data', {})
        
        assert data.get('unique_players', 0) > 0, "No players synced"
        assert data.get('total_props', 0) > 0, "No props synced"

    def test_status_counts_sum_correctly(self):
        """Test standard + demons + goblins = total_props"""
        response = requests.get(f"{BASE_URL}/api/v3/status", timeout=30)
        data = response.json().get('data', {})
        
        calculated_total = (
            data.get('standard_count', 0) +
            data.get('demons_count', 0) +
            data.get('goblins_count', 0)
        )
        assert calculated_total == data.get('total_props'), \
            f"Counts don't sum correctly: {calculated_total} != {data.get('total_props')}"


# ==================== WAR ZONE ENDPOINT ====================

class TestWarZoneEndpoint:
    """Test /api/v3/war-zone endpoint - top demon picks"""

    def test_war_zone_returns_200(self):
        """Test war-zone returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_war_zone_returns_success_true(self):
        """Test war-zone returns success: true"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        data = response.json()
        assert data.get('success') is True, "Expected success to be true"

    def test_war_zone_has_picks(self):
        """Test war-zone has picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        data = response.json()
        assert 'picks' in data, "Expected 'picks' key in response"
        assert isinstance(data['picks'], list), "picks should be a list"

    def test_war_zone_picks_have_required_fields(self):
        """Test war-zone picks have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            required_fields = ['player_name', 'stat_type', 'demon_line', 'h10_rate', 'h5_rate']
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"

    def test_war_zone_returns_up_to_10_picks(self):
        """Test war-zone returns max 10 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_war_zone_picks_have_hit_rates(self):
        """Test war-zone picks have h10_rate and h5_rate"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'h10_rate' in pick, "Pick missing h10_rate"
            assert 'h5_rate' in pick, "Pick missing h5_rate"
            # Hit rates should be percentages (0-100)
            if pick.get('h10_rate') is not None:
                assert 0 <= pick['h10_rate'] <= 100, "h10_rate out of range"


# ==================== GOBLIN VAULT ENDPOINT ====================

class TestGoblinVaultEndpoint:
    """Test /api/v3/goblin-vault endpoint - safe picks"""

    def test_goblin_vault_returns_200(self):
        """Test goblin-vault returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_goblin_vault_returns_success_true(self):
        """Test goblin-vault returns success: true"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        data = response.json()
        assert data.get('success') is True, "Expected success to be true"

    def test_goblin_vault_has_picks(self):
        """Test goblin-vault has picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        data = response.json()
        assert 'picks' in data, "Expected 'picks' key in response"
        assert isinstance(data['picks'], list), "picks should be a list"

    def test_goblin_vault_picks_have_required_fields(self):
        """Test goblin-vault picks have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            required_fields = ['player_name', 'stat_type', 'goblin_line', 'h10_rate', 'h5_rate']
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"

    def test_goblin_vault_returns_up_to_10_picks(self):
        """Test goblin-vault returns max 10 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_goblin_vault_picks_are_goblins(self):
        """Test goblin-vault picks are marked as goblins"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert pick.get('is_goblin') is True, f"Pick {pick.get('player_name')} should be a goblin"


# ==================== FRONT LINES ENDPOINT ====================

class TestFrontLinesEndpoint:
    """Test /api/v3/front-lines endpoint - mixed tier picks"""

    def test_front_lines_returns_200(self):
        """Test front-lines returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_front_lines_returns_success_true(self):
        """Test front-lines returns success: true"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        data = response.json()
        assert data.get('success') is True, "Expected success to be true"

    def test_front_lines_has_picks(self):
        """Test front-lines has picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        data = response.json()
        assert 'picks' in data, "Expected 'picks' key in response"
        assert isinstance(data['picks'], list), "picks should be a list"

    def test_front_lines_picks_have_required_fields(self):
        """Test front-lines picks have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        picks = response.json().get('picks', [])
        
        if picks:
            pick = picks[0]
            required_fields = ['player_name', 'stat_type', 'line', 'h10_rate', 'h5_rate']
            for field in required_fields:
                assert field in pick, f"Pick missing required field: {field}"

    def test_front_lines_returns_up_to_10_picks(self):
        """Test front-lines returns max 10 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        picks = response.json().get('picks', [])
        assert len(picks) <= 10, f"Expected max 10 picks, got {len(picks)}"

    def test_front_lines_has_demon_and_goblin_counts(self):
        """Test front-lines has demon_count and goblin_count"""
        response = requests.get(f"{BASE_URL}/api/v3/front-lines", timeout=30)
        data = response.json()
        assert 'demon_count' in data, "Expected demon_count in response"
        assert 'goblin_count' in data, "Expected goblin_count in response"


# ==================== PARLAY BUILDER ENDPOINT ====================

class TestParlayBuilderEndpoint:
    """Test /api/v3/parlay-builder endpoint - parlay tickets"""

    def test_parlay_builder_returns_200(self):
        """Test parlay-builder returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_parlay_builder_returns_success_true(self):
        """Test parlay-builder returns success: true"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        data = response.json()
        assert data.get('success') is True, "Expected success to be true"

    def test_parlay_builder_has_parlays(self):
        """Test parlay-builder has parlays object"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        data = response.json()
        assert 'parlays' in data, "Expected 'parlays' key in response"
        assert isinstance(data['parlays'], dict), "parlays should be a dictionary"

    def test_parlay_builder_has_required_parlay_types(self):
        """Test parlay-builder has all parlay types (2-6 picks)"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        expected_types = ['2_pick', '3_pick', '4_pick', '5_pick', '6_pick']
        for ptype in expected_types:
            assert ptype in parlays, f"Missing parlay type: {ptype}"

    def test_parlay_builder_parlays_have_picks(self):
        """Test each parlay type has picks array"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlays = response.json().get('parlays', {})
        
        for ptype, parlay_data in parlays.items():
            assert 'picks' in parlay_data, f"{ptype} missing picks array"
            assert isinstance(parlay_data['picks'], list), f"{ptype} picks should be a list"

    def test_parlay_builder_2_pick_has_2_picks(self):
        """Test 2_pick parlay has exactly 2 picks"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_2 = response.json().get('parlays', {}).get('2_pick', {})
        picks = parlay_2.get('picks', [])
        assert len(picks) == 2, f"2_pick should have 2 picks, got {len(picks)}"

    def test_parlay_builder_picks_have_required_fields(self):
        """Test parlay picks have required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_2 = response.json().get('parlays', {}).get('2_pick', {})
        picks = parlay_2.get('picks', [])
        
        if picks:
            pick = picks[0]
            required_fields = ['player_name', 'stat_type', 'line', 'h10_rate', 'h5_rate']
            for field in required_fields:
                assert field in pick, f"Parlay pick missing required field: {field}"


# ==================== DATA VALIDATION TESTS ====================

class TestDataValidation:
    """Test data validation across all endpoints"""

    def test_all_endpoints_return_valid_json(self):
        """Test all endpoints return valid JSON"""
        endpoints = [
            '/api/v3/status',
            '/api/v3/war-zone',
            '/api/v3/goblin-vault',
            '/api/v3/front-lines',
            '/api/v3/parlay-builder'
        ]
        
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=30)
            try:
                data = response.json()
                assert 'success' in data, f"{endpoint} missing 'success' field"
            except Exception as e:
                pytest.fail(f"{endpoint} returned invalid JSON: {e}")

    def test_all_endpoints_have_synced_at_timestamp(self):
        """Test endpoints with picks have synced_at timestamp"""
        endpoints = [
            '/api/v3/war-zone',
            '/api/v3/goblin-vault',
            '/api/v3/front-lines',
            '/api/v3/parlay-builder'
        ]
        
        for endpoint in endpoints:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=30)
            data = response.json()
            assert 'synced_at' in data, f"{endpoint} missing 'synced_at' timestamp"

    def test_player_names_are_not_empty(self):
        """Test player names are not empty in war-zone picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert pick.get('player_name'), f"Pick has empty player_name: {pick}"

    def test_stat_types_are_valid(self):
        """Test stat types are valid in war-zone picks"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        valid_stat_types = ['PTS', 'REB', 'AST', 'BLK', 'STL', 'FG3M', 'PRA', 'PR', 'PA', 'RA']
        
        for pick in picks:
            stat_type = pick.get('stat_type')
            assert stat_type in valid_stat_types, f"Invalid stat_type: {stat_type}"


# ==================== INTEGRATION TESTS ====================

class TestIntegration:
    """Test integration between endpoints"""

    def test_war_zone_uses_demon_data(self):
        """Test war-zone picks are demons with demon_line"""
        response = requests.get(f"{BASE_URL}/api/v3/war-zone", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'demon_line' in pick, "War zone pick should have demon_line"
            assert pick.get('demon_line') is not None, "demon_line should not be None"

    def test_goblin_vault_uses_goblin_data(self):
        """Test goblin-vault picks have goblin_line"""
        response = requests.get(f"{BASE_URL}/api/v3/goblin-vault", timeout=30)
        picks = response.json().get('picks', [])
        
        for pick in picks:
            assert 'goblin_line' in pick, "Goblin vault pick should have goblin_line"

    def test_parlay_builder_picks_unique(self):
        """Test parlay picks are from different players"""
        response = requests.get(f"{BASE_URL}/api/v3/parlay-builder", timeout=30)
        parlay_6 = response.json().get('parlays', {}).get('6_pick', {})
        picks = parlay_6.get('picks', [])
        
        player_names = [p.get('player_name') for p in picks]
        assert len(player_names) == len(set(player_names)), "Parlay should have unique players"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
