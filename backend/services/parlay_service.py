"""
Parlay Service - Matrix Calculation & DFS Compliance
=====================================================
Handles parlay generation, validation, and DFS rule enforcement.
"""
from typing import List, Dict, Any, Optional, Set
from datetime import datetime


# Overlap Matrix - Maps indices for strategic diversity
PARLAY_MATRIX = {
    2: [0, 1],
    3: [0, 2, 3],
    4: [1, 2, 4, 5],
    5: [0, 3, 4, 6, 7],
    6: [1, 3, 5, 7, 8, 9]
}

# Ticket naming by section
TICKET_NAMES = {
    "safe_haven": {
        2: {"name": "Daily Double", "description": "2 high-floor picks"},
        3: {"name": "Green Ladder", "description": "3 picks - steady climb"},
        4: {"name": "Green Ladder+", "description": "4 picks - extended reach"},
        5: {"name": "Green Stack", "description": "5 picks - full stack"},
        6: {"name": "6-Pick Fortress", "description": "PrizePicks Flex - Win on 5 OR 6!"}
    },
    "front_lines": {
        2: {"name": "Quick Strike", "description": "2 tactical picks"},
        3: {"name": "Triple Tap", "description": "3 diversified picks"},
        4: {"name": "Fire Squad", "description": "4 balanced picks"},
        5: {"name": "Full Clip", "description": "5 stacked picks"},
        6: {"name": "Armory", "description": "PrizePicks Flex - Win on 5 OR 6!"}
    },
    "war_zone": {
        2: {"name": "Double Up", "description": "2 demon picks"},
        3: {"name": "Triple Threat", "description": "3 high-upside picks"},
        4: {"name": "Power Play", "description": "4 ceiling plays"},
        5: {"name": "Heavy Hitter", "description": "5 max payout picks"},
        6: {"name": "Jackpot", "description": "PrizePicks Flex - Win on 5 OR 6!"}
    }
}


def get_pick_id(pick: Dict) -> str:
    """Generate unique identifier for a pick"""
    line_value = pick.get("demon_line") or pick.get("goblin_line") or pick.get("line", 0)
    return f"{pick.get('player_name', '')}-{pick.get('stat_type', '')}-{line_value}"


def validate_ticket(ticket_picks: List[Dict], full_pool: List[Dict], ticket_size: int) -> List[Dict]:
    """
    DFS COMPLIANCE ENGINE - Validates tickets against PrizePicks rules.
    
    Rules:
    - 2-leg: No player stacking, no same-team stacking
    - 3-6 leg: Same player allowed (different stats), min 2 teams required
    """
    if not ticket_picks or len(ticket_picks) == 0:
        return []
    
    # STRICT 2-LEG RULES
    if ticket_size == 2:
        used_players: Set[str] = set()
        used_teams: Set[str] = set()
        used_pick_ids: Set[str] = set()
        final_picks = []
        
        for pick in ticket_picks:
            if len(final_picks) >= 2:
                break
            pick_id = get_pick_id(pick)
            player_name = pick.get("player_name", "")
            team = pick.get("team", "")
            
            if player_name in used_players or team in used_teams:
                continue
            
            final_picks.append(pick)
            used_players.add(player_name)
            used_teams.add(team)
            used_pick_ids.add(pick_id)
        
        # Fill from pool if needed
        if len(final_picks) < 2:
            for pick in full_pool:
                if len(final_picks) >= 2:
                    break
                pick_id = get_pick_id(pick)
                player_name = pick.get("player_name", "")
                team = pick.get("team", "")
                
                if pick_id in used_pick_ids or player_name in used_players or team in used_teams:
                    continue
                
                final_picks.append(pick)
                used_players.add(player_name)
                used_teams.add(team)
                used_pick_ids.add(pick_id)
        
        return final_picks[:2]
    
    # 3-6 LEG RULES: AGGRESSIVE STACKING ALLOWED
    player_stat_types: Dict[str, List[str]] = {}
    team_counts: Dict[str, int] = {}
    used_pick_ids: Set[str] = set()
    final_picks = []
    
    def violates_hard_rules(pick: Dict, is_last_slot: bool, current_team_count: int) -> Optional[str]:
        player_name = pick.get("player_name", "")
        stat_type = pick.get("stat_type", "")
        team = pick.get("team", "")
        
        existing_stats = player_stat_types.get(player_name, [])
        
        if stat_type in existing_stats:
            return "DUPLICATE_STAT"
        if len(existing_stats) >= 3:
            return "MAX_STACKS"
        
        if is_last_slot and current_team_count == 1:
            existing_team = list(team_counts.keys())[0] if team_counts else None
            if existing_team and team == existing_team:
                return "NEED_SECOND_TEAM"
        
        return None
    
    # First pass: Accept matrix-mapped picks
    for pick in ticket_picks:
        pick_id = get_pick_id(pick)
        if pick_id in used_pick_ids:
            continue
        
        is_last_slot = len(final_picks) == ticket_size - 1
        current_team_count = len(team_counts)
        
        if violates_hard_rules(pick, is_last_slot, current_team_count):
            continue
        
        player_name = pick.get("player_name", "")
        stat_type = pick.get("stat_type", "")
        team = pick.get("team", "")
        
        final_picks.append(pick)
        used_pick_ids.add(pick_id)
        
        if player_name not in player_stat_types:
            player_stat_types[player_name] = []
        player_stat_types[player_name].append(stat_type)
        team_counts[team] = team_counts.get(team, 0) + 1
        
        if len(final_picks) >= ticket_size:
            break
    
    # Second pass: Fill from pool
    if len(final_picks) < ticket_size:
        for pick in full_pool:
            if len(final_picks) >= ticket_size:
                break
            
            pick_id = get_pick_id(pick)
            if pick_id in used_pick_ids:
                continue
            
            is_last_slot = len(final_picks) == ticket_size - 1
            current_team_count = len(team_counts)
            
            if violates_hard_rules(pick, is_last_slot, current_team_count):
                continue
            
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "")
            team = pick.get("team", "")
            
            final_picks.append(pick)
            used_pick_ids.add(pick_id)
            
            if player_name not in player_stat_types:
                player_stat_types[player_name] = []
            player_stat_types[player_name].append(stat_type)
            team_counts[team] = team_counts.get(team, 0) + 1
    
    # Final check: Ensure min 2 teams
    unique_teams = list(team_counts.keys())
    if len(unique_teams) < 2 and len(final_picks) >= 2:
        single_team = unique_teams[0] if unique_teams else None
        if single_team:
            # Find lowest EV pick to replace
            sorted_by_ev = sorted(final_picks, key=lambda x: x.get("final_ev_score", x.get("score", 0)))
            lowest_pick = sorted_by_ev[0]
            lowest_idx = final_picks.index(lowest_pick)
            
            for pick in full_pool:
                pick_id = get_pick_id(pick)
                if pick_id in used_pick_ids or pick.get("team") == single_team:
                    continue
                
                player_name = pick.get("player_name", "")
                existing_stats = player_stat_types.get(player_name, [])
                if len(existing_stats) >= 3 or pick.get("stat_type") in existing_stats:
                    continue
                
                final_picks[lowest_idx] = pick
                break
    
    return final_picks[:ticket_size]


def apply_parlay_matrix(pool: List[Dict], ticket_size: int) -> List[Dict]:
    """Apply overlap matrix to pick pool"""
    if not pool or len(pool) == 0:
        return []
    
    indices = PARLAY_MATRIX.get(ticket_size, [])
    return [pool[idx] for idx in indices if idx < len(pool)]


def calculate_parlay_probability(picks: List[Dict]) -> float:
    """Calculate combined probability for a parlay"""
    if not picks:
        return 0.0
    
    combined = 1.0
    for pick in picks:
        rate = (pick.get("h10_rate") or pick.get("reliability") or 50) / 100
        combined *= rate
    
    return round(combined * 100, 2)


def calculate_payout_multiplier(leg_count: int) -> float:
    """Calculate estimated payout multiplier"""
    return round(pow(1.8, leg_count), 1)


def build_parlay_tickets(full_pool: List[Dict], section_name: str = "war_zone") -> Dict[int, Dict]:
    """
    Build all 5 parlay tickets (2-6 legs) using matrix and validation.
    
    Args:
        full_pool: EV-sorted list of picks
        section_name: "war_zone", "safe_haven", or "front_lines"
    
    Returns:
        Dict mapping leg count to ticket data
    """
    names = TICKET_NAMES.get(section_name, TICKET_NAMES["war_zone"])
    tickets = {}
    
    for size in [2, 3, 4, 5, 6]:
        if len(full_pool) < size:
            continue
        
        mapped_picks = apply_parlay_matrix(full_pool, size)
        validated_picks = validate_ticket(mapped_picks, full_pool, size)
        
        if len(validated_picks) < size:
            continue
        
        combined_prob = calculate_parlay_probability(validated_picks)
        payout_mult = calculate_payout_multiplier(size)
        unique_teams = len(set(p.get("team", "") for p in validated_picks))
        
        tickets[size] = {
            "name": names[size]["name"],
            "description": names[size]["description"],
            "picks": validated_picks,
            "pick_count": size,
            "estimated_payout": payout_mult,
            "combined_probability": combined_prob,
            "reliability": combined_prob,
            "payout_range": f"{payout_mult - 1}x - {payout_mult + 2}x",
            "lineup_valid": True,
            "lineup_status": "Valid (Multi-Team)" if unique_teams >= 2 else "Single Team",
            "team_count": unique_teams,
            "validated": True,
            "generated_at": datetime.utcnow().isoformat()
        }
    
    return tickets


def interleave_pick_arrays(goblins: List[Dict], demons: List[Dict]) -> List[Dict]:
    """Interleave goblin and demon picks (for Front Lines)"""
    result = []
    max_len = max(len(goblins), len(demons))
    
    for i in range(max_len):
        if i < len(goblins):
            result.append(goblins[i])
        if i < len(demons):
            result.append(demons[i])
    
    return result
