/**
 * Vegas Killer Service
 * ====================
 * Fetches Vegas Killer ML predictions from the backend.
 * Integrates with the board to provide forward-looking edge analysis.
 */

const API_URL = process.env.REACT_APP_BACKEND_URL || '';

/**
 * Get VK prediction for a single player prop
 */
export async function getVKPrediction(playerName, statType, line, opponent = null) {
  try {
    const response = await fetch(`${API_URL}/api/v3/vegas-killer/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player_name: playerName,
        stat_type: statType,
        line: line,
        opponent_team: opponent,
      }),
    });
    
    if (!response.ok) return null;
    
    const data = await response.json();
    return data.success !== false ? data : null;
  } catch (error) {
    console.error('VK prediction error:', error);
    return null;
  }
}

/**
 * Get VK predictions for entire board (enriched)
 */
export async function getEnrichedBoard() {
  try {
    const response = await fetch(`${API_URL}/api/v3/vegas-killer/enrich-board`);
    if (!response.ok) return null;
    
    const data = await response.json();
    return data.success ? data : null;
  } catch (error) {
    console.error('VK enrich board error:', error);
    return null;
  }
}

/**
 * Predict full slate of props
 */
export async function predictSlate(playerLines) {
  try {
    const response = await fetch(`${API_URL}/api/v3/vegas-killer/predict-slate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(playerLines),
    });
    
    if (!response.ok) return null;
    
    const data = await response.json();
    return data.success ? data : null;
  } catch (error) {
    console.error('VK slate prediction error:', error);
    return null;
  }
}

/**
 * Get backtest results
 */
export async function getBacktestResults() {
  try {
    const response = await fetch(`${API_URL}/api/v3/vegas-killer/backtest/results`);
    if (!response.ok) return null;
    
    const data = await response.json();
    return data.success ? data : null;
  } catch (error) {
    console.error('VK backtest error:', error);
    return null;
  }
}

/**
 * Format VK recommendation as badge
 */
export function getVKBadgeStyle(recommendation) {
  const styles = {
    'STRONG_OVER': { bg: 'bg-green-500/30', border: 'border-green-500/50', text: 'text-green-400', label: '↑ STRONG' },
    'LEAN_OVER': { bg: 'bg-green-500/20', border: 'border-green-500/30', text: 'text-green-300', label: '↑ LEAN' },
    'STRONG_UNDER': { bg: 'bg-red-500/30', border: 'border-red-500/50', text: 'text-red-400', label: '↓ STRONG' },
    'LEAN_UNDER': { bg: 'bg-red-500/20', border: 'border-red-500/30', text: 'text-red-300', label: '↓ LEAN' },
    'NEUTRAL': { bg: 'bg-zinc-500/20', border: 'border-zinc-500/30', text: 'text-zinc-400', label: '― HOLD' },
  };
  
  return styles[recommendation] || styles['NEUTRAL'];
}

/**
 * Calculate edge color based on percentage
 */
export function getEdgeColor(edgePct) {
  if (edgePct >= 20) return 'text-green-400';
  if (edgePct >= 10) return 'text-green-300';
  if (edgePct >= 5) return 'text-lime-400';
  if (edgePct <= -20) return 'text-red-400';
  if (edgePct <= -10) return 'text-red-300';
  if (edgePct <= -5) return 'text-orange-400';
  return 'text-zinc-400';
}

export default {
  getVKPrediction,
  getEnrichedBoard,
  predictSlate,
  getBacktestResults,
  getVKBadgeStyle,
  getEdgeColor,
};
