/**
 * MASTER PARLAY MATRIX & DFS COMPLIANCE ENGINE
 * 
 * CRITICAL: This engine must ONLY be fed with EV-SORTED data arrays!
 * - DO NOT pass popularity/volume-sorted data (e.g., Most Popular bets)
 * - Parlays must be built from Expected Value rankings to preserve math integrity
 *
 * APPROVED DATA SOURCES:
 *   - vaultPicks (Safe Haven / THE SHIELD) - sorted by final_ev_score
 *   - frontLinesPicks (Front Lines / THE STRIKE) - sorted by final_ev_score
 *   - radarPicks (War Zone / THE GAUNTLET) - sorted by final_ev_score
 */

// Overlap Matrix - Maps indices from a 10-player pool for strategic diversity
export const PARLAY_MATRIX = {
  2: [0, 1],
  3: [0, 2, 3],
  4: [1, 2, 4, 5],
  5: [0, 3, 4, 6, 7],
  6: [1, 3, 5, 7, 8, 9]
};

// Ticket naming per section
export const TICKET_NAMES = {
  safe_haven: {
    2: { name: 'Daily Double', description: '2 high-floor picks' },
    3: { name: 'Green Ladder', description: '3 picks - steady climb' },
    4: { name: 'Green Ladder+', description: '4 picks - extended reach' },
    5: { name: 'Green Stack', description: '5 picks - full stack' },
    6: { name: '6-Pick Fortress', description: 'PrizePicks Flex - Win on 5 OR 6!' }
  },
  front_lines: {
    2: { name: 'Quick Strike', description: '2 tactical picks' },
    3: { name: 'Triple Tap', description: '3 diversified picks' },
    4: { name: 'Fire Squad', description: '4 balanced picks' },
    5: { name: 'Full Clip', description: '5 stacked picks' },
    6: { name: 'Armory', description: 'PrizePicks Flex - Win on 5 OR 6!' }
  },
  war_zone: {
    2: { name: 'Double Up', description: '2 demon picks' },
    3: { name: 'Triple Threat', description: '3 high-upside picks' },
    4: { name: 'Power Play', description: '4 ceiling plays' },
    5: { name: 'Heavy Hitter', description: '5 max payout picks' },
    6: { name: 'Jackpot', description: 'PrizePicks Flex - Win on 5 OR 6!' }
  }
};

/**
 * Helper: Get unique identifier for a pick
 */
const getPickId = (pick) => {
  const lineValue = pick.demon_line || pick.goblin_line || pick.line || 0;
  return `${pick.player_name}-${pick.stat_type}-${lineValue}`;
};

/**
 * DFS COMPLIANCE ENGINE - Validates tickets against PrizePicks rules
 * 
 * CORE MANDATE: TRUST THE EV RANKINGS!
 * - Aggressively STACK same-player props when legally possible
 * - Only reject picks for HARD DFS violations, not "visual variety"
 */
export const validateTicket = (ticketPicks, fullPool, ticketSize) => {
  if (!ticketPicks || ticketPicks.length === 0) return [];
  
  // STRICT 2-LEG RULES: No Stacks Allowed
  if (ticketSize === 2) {
    const usedPlayers = new Set();
    const usedTeams = new Set();
    const usedPickIds = new Set();
    const finalPicks = [];
    
    for (const pick of ticketPicks) {
      if (finalPicks.length >= 2) break;
      const pickId = getPickId(pick);
      if (usedPlayers.has(pick.player_name) || usedTeams.has(pick.team)) continue;
      
      finalPicks.push(pick);
      usedPlayers.add(pick.player_name);
      usedTeams.add(pick.team);
      usedPickIds.add(pickId);
    }
    
    // Fill from pool if needed
    if (finalPicks.length < 2) {
      for (const pick of fullPool) {
        if (finalPicks.length >= 2) break;
        const pickId = getPickId(pick);
        if (usedPickIds.has(pickId) || usedPlayers.has(pick.player_name) || usedTeams.has(pick.team)) continue;
        
        finalPicks.push(pick);
        usedPlayers.add(pick.player_name);
        usedTeams.add(pick.team);
        usedPickIds.add(pickId);
      }
    }
    
    return finalPicks.slice(0, 2);
  }
  
  // 3-6 LEG RULES: AGGRESSIVE STACKING ALLOWED
  const playerStatTypes = {};
  const teamCounts = {};
  const usedPickIds = new Set();
  const finalPicks = [];
  
  const violatesHardRules = (pick, isLastSlot, currentTeamCount) => {
    const existingStats = playerStatTypes[pick.player_name] || [];
    
    if (existingStats.includes(pick.stat_type)) return 'DUPLICATE_STAT';
    if (existingStats.length >= 3) return 'MAX_STACKS';
    
    if (isLastSlot && currentTeamCount === 1) {
      const existingTeam = Object.keys(teamCounts)[0];
      if (pick.team === existingTeam) return 'NEED_SECOND_TEAM';
    }
    
    return null;
  };
  
  // First pass: Accept matrix-mapped picks
  for (const pick of ticketPicks) {
    const pickId = getPickId(pick);
    if (usedPickIds.has(pickId)) continue;
    
    const isLastSlot = (finalPicks.length === ticketSize - 1);
    const currentTeamCount = Object.keys(teamCounts).length;
    
    if (violatesHardRules(pick, isLastSlot, currentTeamCount)) continue;
    
    finalPicks.push(pick);
    usedPickIds.add(pickId);
    
    if (!playerStatTypes[pick.player_name]) playerStatTypes[pick.player_name] = [];
    playerStatTypes[pick.player_name].push(pick.stat_type);
    teamCounts[pick.team] = (teamCounts[pick.team] || 0) + 1;
    
    if (finalPicks.length >= ticketSize) break;
  }
  
  // Second pass: Fill from pool
  if (finalPicks.length < ticketSize) {
    for (const pick of fullPool) {
      if (finalPicks.length >= ticketSize) break;
      
      const pickId = getPickId(pick);
      if (usedPickIds.has(pickId)) continue;
      
      const isLastSlot = (finalPicks.length === ticketSize - 1);
      const currentTeamCount = Object.keys(teamCounts).length;
      
      if (violatesHardRules(pick, isLastSlot, currentTeamCount)) continue;
      
      finalPicks.push(pick);
      usedPickIds.add(pickId);
      
      if (!playerStatTypes[pick.player_name]) playerStatTypes[pick.player_name] = [];
      playerStatTypes[pick.player_name].push(pick.stat_type);
      teamCounts[pick.team] = (teamCounts[pick.team] || 0) + 1;
    }
  }
  
  // Final check: Ensure min 2 teams
  const uniqueTeams = Object.keys(teamCounts);
  if (uniqueTeams.length < 2 && finalPicks.length >= 2) {
    const singleTeam = uniqueTeams[0];
    const sortedByEV = [...finalPicks].sort((a, b) => 
      (a.final_ev_score || a.score || 0) - (b.final_ev_score || b.score || 0)
    );
    
    const lowestPick = sortedByEV[0];
    const lowestIdx = finalPicks.indexOf(lowestPick);
    
    for (const pick of fullPool) {
      const pickId = getPickId(pick);
      if (usedPickIds.has(pickId) || pick.team === singleTeam) continue;
      
      const existingStats = playerStatTypes[pick.player_name] || [];
      if (existingStats.length >= 3 || existingStats.includes(pick.stat_type)) continue;
      
      finalPicks[lowestIdx] = pick;
      break;
    }
  }
  
  return finalPicks.slice(0, ticketSize);
};

/**
 * MATRIX MAPPER - Applies overlap matrix to pick pool
 */
export const applyParlayMatrix = (pool, ticketSize) => {
  if (!pool || pool.length === 0) return [];
  
  const indices = PARLAY_MATRIX[ticketSize] || [];
  return indices.filter(idx => idx < pool.length).map(idx => pool[idx]);
};

/**
 * INTERLEAVE HELPER - For Front Lines (Goblin, Demon, Goblin, Demon...)
 */
export const interleavePickArrays = (goblins, demons) => {
  const result = [];
  const maxLen = Math.max(goblins.length, demons.length);
  
  for (let i = 0; i < maxLen; i++) {
    if (i < goblins.length) result.push(goblins[i]);
    if (i < demons.length) result.push(demons[i]);
  }
  
  return result;
};

/**
 * MASTER PARLAY BUILDER - Builds all 5 tickets with matrix + validation
 */
export const buildMasterParlayTickets = (fullPool, options = {}) => {
  const { sectionName = 'default' } = options;
  const names = TICKET_NAMES[sectionName] || TICKET_NAMES.war_zone;
  const tickets = {};
  
  for (const size of [2, 3, 4, 5, 6]) {
    const mappedPicks = applyParlayMatrix(fullPool, size);
    const validatedPicks = validateTicket(mappedPicks, fullPool, size);
    
    if (validatedPicks.length < size) continue;
    
    const combinedProb = validatedPicks.reduce((acc, pick) => {
      const rate = (pick.h10_rate || pick.reliability || 50) / 100;
      return acc * rate;
    }, 1) * 100;
    
    const payoutMultiplier = Math.round(Math.pow(1.8, size) * 10) / 10;
    
    tickets[size] = {
      name: names[size].name,
      description: names[size].description,
      picks: validatedPicks,
      pick_count: size,
      estimated_payout: payoutMultiplier,
      combined_probability: Math.round(combinedProb * 10) / 10,
      reliability: Math.round(combinedProb * 10) / 10,
      payout_range: `${payoutMultiplier - 1}x - ${payoutMultiplier + 2}x`,
      lineup_valid: true,
      lineup_status: 'Valid (Multi-Team)',
      team_count: new Set(validatedPicks.map(p => p.team)).size,
      validated: true
    };
  }
  
  return tickets;
};
