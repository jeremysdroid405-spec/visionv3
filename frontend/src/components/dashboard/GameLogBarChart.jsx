/**
 * GameLogBarChart - PrizePicks-style bar graph showing game outcomes vs line
 * 
 * All bars grow upward from bottom:
 * - GREEN bars bust THROUGH the line (value >= line)
 * - RED bars stop SHORT of the line with a gap (value < line)
 * 
 * Layout:
 * - Values on TOP of bars
 * - Opponent abbreviations at BOTTOM
 * - Constant horizontal line
 */
import React, { memo, useMemo } from 'react';

// BDL Team ID to Abbreviation mapping
const TEAM_ID_TO_ABBR = {
  1: 'ATL', 2: 'BOS', 3: 'BKN', 4: 'CHA', 5: 'CHI',
  6: 'CLE', 7: 'DAL', 8: 'DEN', 9: 'DET', 10: 'GSW',
  11: 'HOU', 12: 'IND', 13: 'LAC', 14: 'LAL', 15: 'MEM',
  16: 'MIA', 17: 'MIL', 18: 'MIN', 19: 'NOP', 20: 'NYK',
  21: 'OKC', 22: 'ORL', 23: 'PHI', 24: 'PHX', 25: 'POR',
  26: 'SAC', 27: 'SAS', 28: 'TOR', 29: 'UTA', 30: 'WAS'
};

// Map stat types to game log fields
const STAT_FIELD_MAP = {
  'PTS': 'pts',
  'REB': 'reb',
  'AST': 'ast',
  'STL': 'stl',
  'BLK': 'blk',
  'FG3M': 'fg3m',
  '3PM': 'fg3m',
  'TO': 'turnover',
  'PRA': ['pts', 'reb', 'ast'],
  'PR': ['pts', 'reb'],
  'PA': ['pts', 'ast'],
  'RA': ['reb', 'ast'],
  'BLST': ['blk', 'stl'],
  'FTM': 'ftm',
  'MIN': 'min',
};

const getStatValue = (game, statType) => {
  const field = STAT_FIELD_MAP[statType?.toUpperCase()];
  if (!field) return null;
  
  if (Array.isArray(field)) {
    return field.reduce((sum, f) => sum + (parseFloat(game[f]) || 0), 0);
  }
  
  if (field === 'min') {
    const min = game[field];
    if (typeof min === 'string' && min.includes(':')) {
      const [mins, secs] = min.split(':').map(Number);
      return mins + (secs / 60);
    }
    return parseFloat(min) || 0;
  }
  
  return parseFloat(game[field]) || 0;
};

const GameLogBarChart = memo(({ 
  gameLogs = [], 
  statType, 
  line, 
  showGames = 10,
  height = 80,
  className = '',
  l5Avg = null,
  l10Avg = null,
  seasonAvg = null
}) => {
  const chartData = useMemo(() => {
    if (!gameLogs || !Array.isArray(gameLogs) || !statType || line === undefined) {
      return null;
    }
    
    const recentGames = gameLogs.slice(0, showGames);
    
    const values = recentGames.map(game => {
      const oppId = game.opponent_team_id;
      const oppAbbr = TEAM_ID_TO_ABBR[oppId] || game.opponent || '???';
      
      return {
        value: getStatValue(game, statType),
        opponent: oppAbbr,
        isHome: game.home_game
      };
    }).filter(v => v.value !== null);
    
    if (values.length === 0) return null;
    
    // Find max value for scaling (include line, averages and add padding)
    const allRelevantValues = [
      ...values.map(v => v.value), 
      line,
      l5Avg, 
      l10Avg, 
      seasonAvg
    ].filter(v => v != null && v > 0);
    const maxValue = Math.max(...allRelevantValues);
    const chartMax = maxValue * 1.15; // 15% padding above highest bar
    
    const hits = values.filter(v => v.value >= line).length;
    const hitRate = Math.round((hits / values.length) * 100);
    
    return {
      values: values.reverse(), // Oldest first for left-to-right
      chartMax,
      line,
      hits,
      total: values.length,
      hitRate
    };
  }, [gameLogs, statType, line, showGames, l5Avg, l10Avg, seasonAvg]);
  
  if (!chartData) {
    return (
      <div className={`text-zinc-500 text-xs text-center py-2 ${className}`}>
        No game data
      </div>
    );
  }
  
  const { values, chartMax, hits, total, hitRate } = chartData;
  // Line position as percentage from bottom
  const linePosition = (line / chartMax) * 100;
  
  // Averages are now displayed as text above the chart (no reference lines)
  
  return (
    <div className={`relative flex flex-col h-full ${className}`}>
      {/* Chart container - fills available height, isolated stacking context */}
      <div 
        className="relative bg-zinc-900/50 rounded border border-zinc-800 overflow-hidden isolate flex-1"
        style={{ minHeight: '120px' }}
      >
        {/* Main target line (amber, solid) - positioned within the bar area */}
        <div 
          className="absolute left-0 right-0 border-t-2 border-amber-500 pointer-events-none"
          style={{ bottom: `${linePosition}%`, zIndex: 2 }}
        >
          <span className="absolute -right-1 -top-2.5 text-[9px] text-amber-400 font-bold bg-zinc-900/90 px-1 rounded">
            {line}
          </span>
        </div>
        
        {/* Bars - all grow upward from bottom, fill entire container */}
        <div className="absolute inset-0 flex items-end justify-around px-1">
          {values.map((item, idx) => {
            const isHit = item.value >= line;
            // Calculate bar height as percentage - ensure visual accuracy
            // For misses, cap the bar slightly below the line for visual clarity
            let barHeightPercent = (item.value / chartMax) * 100;
            
            // Ensure hits visually cross the line, misses stay visibly below
            if (!isHit && barHeightPercent > linePosition - 3) {
              // If miss is too close to line, reduce slightly for visual gap
              barHeightPercent = Math.min(barHeightPercent, linePosition - 2);
            }
            
            return (
              <div 
                key={idx}
                className="relative flex flex-col items-center h-full justify-end"
                style={{ width: `${85 / values.length}%` }}
              >
                {/* The bar - grows from bottom */}
                <div 
                  className={`w-full rounded-t ${
                    isHit 
                      ? 'bg-emerald-500' 
                      : 'bg-red-500'
                  }`}
                  style={{ 
                    height: `${barHeightPercent}%`,
                    minHeight: '4px'
                  }}
                />
              </div>
            );
          })}
        </div>
        
        {/* Value labels - rendered separately on top layer */}
        <div className="absolute inset-0 flex items-end justify-around px-1 pointer-events-none" style={{ zIndex: 20 }}>
          {values.map((item, idx) => {
            let barHeightPercent = (item.value / chartMax) * 100;
            const isHit = item.value >= line;
            if (!isHit && barHeightPercent > linePosition - 3) {
              barHeightPercent = Math.min(barHeightPercent, linePosition - 2);
            }
            
            return (
              <div 
                key={idx}
                className="relative flex flex-col items-center h-full justify-end"
                style={{ width: `${85 / values.length}%` }}
              >
                <div 
                  className="absolute text-[10px] font-bold text-white"
                  style={{ 
                    bottom: `${barHeightPercent + 3}%`,
                    textShadow: '0 1px 3px rgba(0,0,0,0.9), 0 0 5px rgba(0,0,0,0.5)'
                  }}
                >
                  {Math.round(item.value)}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      
      {/* Opponent abbreviations at bottom */}
      <div className="flex justify-around px-1 mt-1">
        {values.map((item, idx) => (
          <span 
            key={idx} 
            className="text-[8px] text-white font-medium"
            style={{ width: `${100 / values.length}%`, textAlign: 'center' }}
          >
            {item.opponent}
          </span>
        ))}
      </div>
    </div>
  );
});

GameLogBarChart.displayName = 'GameLogBarChart';

export default GameLogBarChart;
