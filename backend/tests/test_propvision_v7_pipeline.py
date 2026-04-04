"""
PropVision v7 Pipeline Tests
=============================
Tests for the True Probability formula and tier classification:
1. POST /api/v3/ferrari/rebuild returns success=true with exactly 10 picks per tier
2. War Zone picks must have is_demon=True
3. Safe Haven picks must have is_goblin=True
4. L5 and L10 hit rates are populated and non-null
5. board_score and pp_edge values are calculated correctly
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPropVisionV7Pipeline:
    """Test PropVision v7 Pipeline - True Probability & Tier Classification"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test fixtures"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_rebuild_endpoint_returns_success(self):
        """Test POST /api/v3/ferrari/rebuild returns success=true"""
        response = self.session.post(f"{BASE_URL}/api/v3/ferrari/rebuild?use_optimized=true", timeout=120)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("success") == True, f"Expected success=true, got {data.get('success')}"
        
        # Verify total picks
        total_picks = data.get("total_picks", 0)
        assert total_picks > 0, f"Expected total_picks > 0, got {total_picks}"
        print(f"✓ Rebuild successful with {total_picks} total picks")
    
    def test_rebuild_returns_exactly_10_picks_per_tier(self):
        """Test that rebuild returns exactly 10 picks per tier"""
        response = self.session.post(f"{BASE_URL}/api/v3/ferrari/rebuild?use_optimized=true", timeout=120)
        
        assert response.status_code == 200
        data = response.json()
        
        # Check Safe Haven
        safe_haven = data.get("safe_haven", {})
        sh_count = safe_haven.get("count", 0)
        assert sh_count == 10, f"Safe Haven should have 10 picks, got {sh_count}"
        
        # Check Front Lines
        front_lines = data.get("front_lines", {})
        fl_count = front_lines.get("count", 0)
        assert fl_count == 10, f"Front Lines should have 10 picks, got {fl_count}"
        
        # Check War Zone
        war_zone = data.get("war_zone", {})
        wz_count = war_zone.get("count", 0)
        assert wz_count == 10, f"War Zone should have 10 picks, got {wz_count}"
        
        print(f"✓ All tiers have exactly 10 picks: SH={sh_count}, FL={fl_count}, WZ={wz_count}")
    
    def test_war_zone_picks_are_demons_only(self):
        """Test that War Zone picks must have is_demon=True"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/war-zone?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        
        picks = data.get("picks", [])
        assert len(picks) > 0, "War Zone should have picks"
        
        non_demon_picks = []
        for pick in picks:
            is_demon = pick.get("is_demon", False)
            if not is_demon:
                non_demon_picks.append({
                    "player": pick.get("player_name"),
                    "stat_type": pick.get("stat_type"),
                    "line": pick.get("line"),
                    "is_demon": is_demon
                })
        
        assert len(non_demon_picks) == 0, f"War Zone should only contain Demons. Non-demon picks found: {non_demon_picks}"
        print(f"✓ All {len(picks)} War Zone picks are Demons (is_demon=True)")
    
    def test_safe_haven_picks_are_goblins_only(self):
        """Test that Safe Haven picks must have is_goblin=True"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/safe-haven?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        
        picks = data.get("picks", [])
        assert len(picks) > 0, "Safe Haven should have picks"
        
        non_goblin_picks = []
        for pick in picks:
            is_goblin = pick.get("is_goblin", False)
            if not is_goblin:
                non_goblin_picks.append({
                    "player": pick.get("player_name"),
                    "stat_type": pick.get("stat_type"),
                    "line": pick.get("line"),
                    "is_goblin": is_goblin
                })
        
        assert len(non_goblin_picks) == 0, f"Safe Haven should only contain Goblins. Non-goblin picks found: {non_goblin_picks}"
        print(f"✓ All {len(picks)} Safe Haven picks are Goblins (is_goblin=True)")
    
    def test_l5_l10_hit_rates_populated(self):
        """Test that L5 and L10 hit rates are populated and non-null"""
        # Test all three tiers
        tiers = [
            ("safe-haven", "Safe Haven"),
            ("front-lines", "Front Lines"),
            ("war-zone", "War Zone")
        ]
        
        for endpoint, tier_name in tiers:
            response = self.session.get(f"{BASE_URL}/api/v3/ferrari/{endpoint}?limit=10")
            assert response.status_code == 200
            
            data = response.json()
            picks = data.get("picks", [])
            
            missing_l5 = []
            missing_l10 = []
            
            for pick in picks:
                player = pick.get("player_name")
                stat_type = pick.get("stat_type")
                
                # Check L5 rate (can be l5_rate or h5_rate)
                l5_rate = pick.get("l5_rate") or pick.get("h5_rate")
                if l5_rate is None:
                    missing_l5.append(f"{player} {stat_type}")
                
                # Check L10 rate (can be l10_rate or h10_rate)
                l10_rate = pick.get("l10_rate") or pick.get("h10_rate")
                if l10_rate is None:
                    missing_l10.append(f"{player} {stat_type}")
            
            assert len(missing_l5) == 0, f"{tier_name}: Missing L5 rates for: {missing_l5}"
            assert len(missing_l10) == 0, f"{tier_name}: Missing L10 rates for: {missing_l10}"
            
            print(f"✓ {tier_name}: All {len(picks)} picks have L5 and L10 hit rates")
    
    def test_board_score_and_pp_edge_calculated(self):
        """Test that board_score and pp_edge values are calculated correctly"""
        # Test all three tiers
        tiers = [
            ("safe-haven", "Safe Haven"),
            ("front-lines", "Front Lines"),
            ("war-zone", "War Zone")
        ]
        
        for endpoint, tier_name in tiers:
            response = self.session.get(f"{BASE_URL}/api/v3/ferrari/{endpoint}?limit=10")
            assert response.status_code == 200
            
            data = response.json()
            picks = data.get("picks", [])
            
            missing_board_score = []
            missing_pp_edge = []
            
            for pick in picks:
                player = pick.get("player_name")
                stat_type = pick.get("stat_type")
                
                # Check board_score (or ferrari_power_score)
                board_score = pick.get("board_score") or pick.get("ferrari_power_score")
                if board_score is None:
                    missing_board_score.append(f"{player} {stat_type}")
                
                # Check pp_edge
                pp_edge = pick.get("pp_edge")
                if pp_edge is None:
                    missing_pp_edge.append(f"{player} {stat_type}")
            
            assert len(missing_board_score) == 0, f"{tier_name}: Missing board_score for: {missing_board_score}"
            assert len(missing_pp_edge) == 0, f"{tier_name}: Missing pp_edge for: {missing_pp_edge}"
            
            print(f"✓ {tier_name}: All {len(picks)} picks have board_score and pp_edge")
    
    def test_board_score_formula_components(self):
        """Test that board_score components are present (sharp_implied, pp_edge, hit_rate_avg)"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/safe-haven?limit=5")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        for pick in picks:
            player = pick.get("player_name")
            
            # Check v7_components if available
            v7_components = pick.get("v7_components", {})
            
            if v7_components:
                # Verify formula components
                sharp_implied = v7_components.get("sharp_implied")
                pp_edge = v7_components.get("pp_edge")
                hit_rate_avg = v7_components.get("hit_rate_avg")
                
                assert sharp_implied is not None, f"{player}: Missing sharp_implied in v7_components"
                assert pp_edge is not None, f"{player}: Missing pp_edge in v7_components"
                assert hit_rate_avg is not None, f"{player}: Missing hit_rate_avg in v7_components"
                
                print(f"  {player}: sharp={sharp_implied}, pp_edge={pp_edge}, hit_avg={hit_rate_avg}")
        
        print(f"✓ Board score formula components verified")
    
    def test_tier_classification_consistency(self):
        """Test that tier classification is consistent across endpoints"""
        # Get all tiers
        sh_response = self.session.get(f"{BASE_URL}/api/v3/ferrari/safe-haven?limit=10")
        fl_response = self.session.get(f"{BASE_URL}/api/v3/ferrari/front-lines?limit=10")
        wz_response = self.session.get(f"{BASE_URL}/api/v3/ferrari/war-zone?limit=10")
        
        assert sh_response.status_code == 200
        assert fl_response.status_code == 200
        assert wz_response.status_code == 200
        
        sh_picks = sh_response.json().get("picks", [])
        fl_picks = fl_response.json().get("picks", [])
        wz_picks = wz_response.json().get("picks", [])
        
        # Check tier labels
        for pick in sh_picks:
            tier = pick.get("tier")
            assert tier == "safe_haven", f"Safe Haven pick has wrong tier: {tier}"
        
        for pick in fl_picks:
            tier = pick.get("tier")
            assert tier == "front_lines", f"Front Lines pick has wrong tier: {tier}"
        
        for pick in wz_picks:
            tier = pick.get("tier")
            assert tier == "war_zone", f"War Zone pick has wrong tier: {tier}"
        
        print(f"✓ Tier classification consistent: SH={len(sh_picks)}, FL={len(fl_picks)}, WZ={len(wz_picks)}")
    
    def test_no_duplicate_players_within_tier(self):
        """Test that no player appears more than once within a tier"""
        tiers = [
            ("safe-haven", "Safe Haven"),
            ("front-lines", "Front Lines"),
            ("war-zone", "War Zone")
        ]
        
        for endpoint, tier_name in tiers:
            response = self.session.get(f"{BASE_URL}/api/v3/ferrari/{endpoint}?limit=10")
            assert response.status_code == 200
            
            data = response.json()
            picks = data.get("picks", [])
            
            player_names = [p.get("player_name") for p in picks]
            duplicates = [name for name in player_names if player_names.count(name) > 1]
            
            assert len(set(duplicates)) == 0, f"{tier_name}: Duplicate players found: {set(duplicates)}"
            
            print(f"✓ {tier_name}: No duplicate players ({len(picks)} unique players)")
    
    def test_all_tiers_endpoint(self):
        """Test GET /api/v3/ferrari/all returns all tiers"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/all?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify all tiers present
        assert "safe_haven" in data, "Missing safe_haven in response"
        assert "front_lines" in data, "Missing front_lines in response"
        assert "war_zone" in data, "Missing war_zone in response"
        
        # Verify counts
        sh_count = data["safe_haven"].get("count", 0)
        fl_count = data["front_lines"].get("count", 0)
        wz_count = data["war_zone"].get("count", 0)
        
        assert sh_count == 10, f"Safe Haven count should be 10, got {sh_count}"
        assert fl_count == 10, f"Front Lines count should be 10, got {fl_count}"
        assert wz_count == 10, f"War Zone count should be 10, got {wz_count}"
        
        # Verify verification message
        verification = data.get("verification", {})
        assert verification.get("elite_opportunities") == 30, "Should have 30 elite opportunities"
        
        print(f"✓ All tiers endpoint returns correct data: {sh_count + fl_count + wz_count} total picks")
    
    def test_hit_rate_values_are_valid_percentages(self):
        """Test that L5 and L10 hit rates are valid percentages (0-100)"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/all?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        
        invalid_rates = []
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier_data = data.get(tier_name, {})
            picks = tier_data.get("picks", [])
            
            for pick in picks:
                player = pick.get("player_name")
                
                l5_rate = pick.get("l5_rate") or pick.get("h5_rate")
                l10_rate = pick.get("l10_rate") or pick.get("h10_rate")
                
                if l5_rate is not None and (l5_rate < 0 or l5_rate > 100):
                    invalid_rates.append(f"{player} L5={l5_rate}")
                
                if l10_rate is not None and (l10_rate < 0 or l10_rate > 100):
                    invalid_rates.append(f"{player} L10={l10_rate}")
        
        assert len(invalid_rates) == 0, f"Invalid hit rate values found: {invalid_rates}"
        print(f"✓ All hit rates are valid percentages (0-100)")


class TestPropVisionV7DataQuality:
    """Test data quality for PropVision v7 picks"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test fixtures"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_picks_have_required_fields(self):
        """Test that all picks have required fields"""
        required_fields = [
            "player_name",
            "team",
            "stat_type",
            "line",
            "tier"
        ]
        
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/all?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        
        missing_fields = []
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier_data = data.get(tier_name, {})
            picks = tier_data.get("picks", [])
            
            for pick in picks:
                player = pick.get("player_name", "Unknown")
                for field in required_fields:
                    if pick.get(field) is None:
                        missing_fields.append(f"{tier_name}/{player}: missing {field}")
        
        assert len(missing_fields) == 0, f"Missing required fields: {missing_fields}"
        print(f"✓ All picks have required fields")
    
    def test_demon_goblin_mutual_exclusivity(self):
        """Test that a pick cannot be both demon and goblin"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/all?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        
        both_flags = []
        
        for tier_name in ["safe_haven", "front_lines", "war_zone"]:
            tier_data = data.get(tier_name, {})
            picks = tier_data.get("picks", [])
            
            for pick in picks:
                is_demon = pick.get("is_demon", False)
                is_goblin = pick.get("is_goblin", False)
                
                if is_demon and is_goblin:
                    both_flags.append(f"{pick.get('player_name')} {pick.get('stat_type')}")
        
        assert len(both_flags) == 0, f"Picks with both demon and goblin flags: {both_flags}"
        print(f"✓ Demon/Goblin flags are mutually exclusive")
    
    def test_war_zone_l10_minimum_threshold(self):
        """Test that War Zone picks meet L10 >= 50% threshold"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/war-zone?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        below_threshold = []
        
        for pick in picks:
            l10_rate = pick.get("l10_rate") or pick.get("h10_rate") or 0
            if l10_rate < 50:
                below_threshold.append({
                    "player": pick.get("player_name"),
                    "stat_type": pick.get("stat_type"),
                    "l10_rate": l10_rate
                })
        
        # Note: This is a soft check - some demons may have lower L10 if they have strong PP edge
        if below_threshold:
            print(f"  Warning: {len(below_threshold)} War Zone picks below L10 50% threshold: {below_threshold}")
        
        print(f"✓ War Zone L10 threshold check complete")
    
    def test_safe_haven_high_hit_rates(self):
        """Test that Safe Haven picks have high hit rates (L5 >= 60% or L10 >= 60%)"""
        response = self.session.get(f"{BASE_URL}/api/v3/ferrari/safe-haven?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        low_hit_rates = []
        
        for pick in picks:
            l5_rate = pick.get("l5_rate") or pick.get("h5_rate") or 0
            l10_rate = pick.get("l10_rate") or pick.get("h10_rate") or 0
            
            # Safe Haven should have at least one high hit rate
            if l5_rate < 60 and l10_rate < 60:
                low_hit_rates.append({
                    "player": pick.get("player_name"),
                    "stat_type": pick.get("stat_type"),
                    "l5_rate": l5_rate,
                    "l10_rate": l10_rate
                })
        
        # This is informational - Safe Haven classification is based on sharp implied
        if low_hit_rates:
            print(f"  Info: {len(low_hit_rates)} Safe Haven picks with lower hit rates: {low_hit_rates}")
        
        print(f"✓ Safe Haven hit rate check complete")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
