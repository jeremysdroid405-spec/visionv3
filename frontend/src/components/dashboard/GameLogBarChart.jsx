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

// Map stat types to game log fields.
// SSOT (2026-05-13): the backend canonicalizes every NBA stat to ONE
// short-code token at the ingest boundary (`universal_odds_sync`).
// Score docs in `nba_prop_scores` carry stat_type ∈ {PTS, REB, AST,
// 3PM, STL, BLK, TO, PRA, PR, PA, RA, BLST, FTM, MIN, FGM}.
// This map only needs short codes plus the MLB display labels.
// The `getStatValue` fallback still strips `_alternate` defensively in
// case any future external feed bypasses the canonical ingest path.
const STAT_FIELD_MAP = {
  // NBA short-code SSOT tokens (universal_odds_sync.stat_type_map)
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
  // MLB Batter Stats
  'Hits': 'hits',
  'HITS': 'hits',
  'Total Bases': 'total_bases',
  'TOTAL BASES': 'total_bases',
  'TB': 'total_bases',
  'RBIs': 'rbis',
  'RBIS': 'rbis',
  'Runs': 'runs',
  'RUNS': 'runs',
  'Stolen Bases': 'stolen_bases',
  'STOLEN BASES': 'stolen_bases',
  'SB': 'stolen_bases',
  'Home Runs': 'home_runs',
  'HOME RUNS': 'home_runs',
  'HR': 'home_runs',
  // Singles = hits - doubles - triples - home_runs (calculated field)
  'Singles': 'singles',
  'SINGLES': 'singles',
  '1B': 'singles',
  // Doubles
  'Doubles': 'doubles',
  'DOUBLES': 'doubles',
  '2B': 'doubles',
  // Triples
  'Triples': 'triples',
  'TRIPLES': 'triples',
  '3B': 'triples',
  // Walks
  'Walks': 'walks',
  'WALKS': 'walks',
  'Batter Walks': 'walks',
  'BATTER WALKS': 'walks',
  'BB': 'walks',
  // Strikeouts
  'Strikeouts': 'strikeouts',
  'STRIKEOUTS': 'strikeouts',
  'Batter Strikeouts': 'strikeouts',
  'BATTER STRIKEOUTS': 'strikeouts',
  'K': 'strikeouts',
  // MLB Pitcher Stats
  'Hits Allowed': 'hits_allowed',
  'HITS ALLOWED': 'hits_allowed',
  'Earned Runs': 'earned_runs',
  'EARNED RUNS': 'earned_runs',
  'ER': 'earned_runs',
  'Pitcher Strikeouts': 'pitcher_strikeouts',
  'PITCHER STRIKEOUTS': 'pitcher_strikeouts',
  'SO': 'pitcher_strikeouts',
  'Walks Allowed': 'pitcher_walks',
  'WALKS ALLOWED': 'pitcher_walks',
  'Pitcher Outs': 'pitcher_outs',
  'PITCHER OUTS': 'pitcher_outs',
  // MLB Combo Stats
  'Hits+Runs+RBIs': ['hits', 'runs', 'rbis'],
  'HITS+RUNS+RBIS': ['hits', 'runs', 'rbis'],
  'HRR': ['hits', 'runs', 'rbis'],
  'batter_hits_runs_rbis': ['hits', 'runs', 'rbis'],
  // ---- Team Props (TeamDetailPage clone of PlayerDetailPage) -----
  // SSOT tokens emitted by GET /api/v3/team-with-badges/{team_id}.
  // Each token resolves to a field already attached to the team's
  // game-log row (team_score / opp_score / total_score / margin),
  // so GameLogBarChart renders unchanged.
  'TEAM_TOTAL': 'team_score',
  'team_total': 'team_score',
  'OPP_TOTAL':  'opp_score',
  'opp_total':  'opp_score',
  'GAME_TOTAL': 'total_score',
  'game_total': 'total_score',
  'SPREAD':     'margin',
  'spread':     'margin',
  // MONEYLINE has no continuous projection; route the bar chart to
  // the team's own score so the visual still has meaningful bars.
  'MONEYLINE':  'team_score',
  'h2h':        'team_score',
};

const getStatValue = (game, statType) => {
  // Try exact match first, then uppercase, then lowercase-stripped `_alternate`
  // suffix (2026-05-13 — defensive fallback so any future Odds-API
  // alt-market key resolves to its base stat without a code change).
  let field =
    STAT_FIELD_MAP[statType] ||
    STAT_FIELD_MAP[statType?.toUpperCase()] ||
    STAT_FIELD_MAP[String(statType || '').replace(/_alternate$/i, '')];
  if (!field) return null;
  
  // Handle combo stats (arrays)
  if (Array.isArray(field)) {
    return field.reduce((sum, f) => sum + (parseFloat(game[f]) || 0), 0);
  }
  
  // Handle calculated fields
  if (field === 'singles') {
    // Singles = hits - doubles - triples - home_runs
    const hits = parseFloat(game.hits) || 0;
    const doubles = parseFloat(game.doubles) || 0;
    const triples = parseFloat(game.triples) || 0;
    const home_runs = parseFloat(game.home_runs) || 0;
    return hits - doubles - triples - home_runs;
  }
  
  // Handle minutes (time format)
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
  seasonAvg = null,
  // 2026-06-03 — Side-aware hit logic. For UNDER picks, "hit" means
  // the game finished BELOW the line, so the green/red bar colors
  // and the hit-rate count must invert. Default is "OVER" to
  // preserve legacy player-prop behaviour.
  direction = 'OVER',
  // 2026-04-29 — Hit-Profile parity contract.
  // Backend stamps `hit_profile` (`l10_hit_count` / `l10_total` /
  // `l10_values`) on every dashboard pick. When available we use it
  // directly so the green-bar count and the displayed Hit Rate share
  // the SAME single source of truth.
  hitProfile = null
}) => {
  // SSOT predicate: "did this game value HIT the prop?"
  //   OVER side  → game.value >  line
  //   UNDER side → game.value <  line
  //   equal (push) is neither hit nor miss — treated as a miss here
  //   so the bar stays red (matches the standard sportsbook grading
  //   convention where a push doesn't pay out).
  const dirUp = (typeof direction === 'string' ? direction : 'OVER')
    .toUpperCase();
  const isUnder = dirUp === 'UNDER' || dirUp === 'AWAY';
  const isHitFn = (value) =>
    isUnder ? value < line : value > line;
  const chartData = useMemo(() => {
    if (!gameLogs || !Array.isArray(gameLogs) || !statType || line === undefined) {
      return null;
    }
    
    const recentGames = gameLogs.slice(0, showGames);
    
    // Parse opponent abbreviation from `matchup` (format: "DET vs. NYK" or
    // "DET @ NYK"). Tank01/BDL game logs do not always include an
    // opponent_team_id, but `matchup` is consistently populated.
    const parseOpponentFromMatchup = (matchup) => {
      if (!matchup || typeof matchup !== 'string') return null;
      const m = matchup.match(/[A-Z]{2,4}\s*(?:vs\.?|@)\s*([A-Z]{2,4})/i);
      return m ? m[1].toUpperCase() : null;
    };
    const parseIsHomeFromMatchup = (matchup) => {
      if (!matchup || typeof matchup !== 'string') return undefined;
      if (/\bvs\.?\b/i.test(matchup)) return true;
      if (/@/.test(matchup)) return false;
      return undefined;
    };
    
    const values = recentGames.map(game => {
      const oppId = game.opponent_team_id;
      const oppAbbr =
        TEAM_ID_TO_ABBR[oppId] ||
        game.opponent ||
        parseOpponentFromMatchup(game.matchup) ||
        '???';
      const isHome =
        game.home_game !== undefined && game.home_game !== null
          ? game.home_game
          : parseIsHomeFromMatchup(game.matchup);
      
      return {
        value: getStatValue(game, statType),
        opponent: oppAbbr,
        isHome
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
    const chartMax = maxValue * 1.25; // 25% padding above highest bar for value labels
    
    // ── Hit count: prefer backend hit_profile when present.
    //    Otherwise fall back to local computation using the
    //    direction-aware `isHitFn` (OVER vs UNDER).
    const localHits = values.filter(v => isHitFn(v.value)).length;
    let hits = localHits;
    let total = values.length;
    if (hitProfile && typeof hitProfile.l10_hit_count === 'number'
        && !isUnder) {
      // Backend hit_profile is computed for the OVER side only.
      // When the pick is UNDER, IGNORE the backend hit_profile and
      // use the locally inverted count — otherwise the green-bar
      // count and the displayed Hit Rate diverge for every UNDER.
      hits = hitProfile.l10_hit_count;
      total = hitProfile.l10_total || values.length;
      // Hard parity assertion: dev mode throws, prod logs.
      if (localHits !== hits) {
        const msg = (
          `[HIT_PROFILE PARITY] graph_local=${localHits} ` +
          `backend_l10_hit_count=${hits} ` +
          `(stat=${statType}, line=${line}, dir=${dirUp}). ` +
          `Graph and Hit Rate would diverge.`
        );
        if (typeof process !== 'undefined' && process.env && process.env.NODE_ENV !== 'production') {
          throw new Error(msg);
        } else {
          // Surface in production logs without breaking the page.
          // eslint-disable-next-line no-console
          console.error(msg);
        }
      }
    }
    const hitRate = total > 0 ? Math.round((hits / total) * 100) : 0;
    
    return {
      values: values.reverse(), // Oldest first for left-to-right
      chartMax,
      line,
      hits,
      total,
      hitRate
    };
  }, [gameLogs, statType, line, showGames, l5Avg, l10Avg, seasonAvg, hitProfile]);
  
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
    <div className={`relative flex flex-col h-full w-full ${className}`}>
      {/* Chart container - fills available height and width, isolated stacking context */}
      <div 
        className="relative bg-zinc-900/50 rounded border border-zinc-800 overflow-visible isolate flex-1 w-full"
        style={{ minHeight: '140px' }}
      >
        {/* Top padding zone for value labels - ensures numbers aren't clipped */}
        <div className="absolute inset-x-0 top-0 h-5 pointer-events-none" style={{ zIndex: 25 }} />
        
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
            const isHit = isHitFn(item.value);
            // Calculate bar height as percentage - ensure visual accuracy.
            // Visual contract:
            //   • OVER  — green bars must visibly CROSS ABOVE the line,
            //             red bars stay below.
            //   • UNDER — green bars stay BELOW the line, red bars
            //             visibly cross above.
            let barHeightPercent = (item.value / chartMax) * 100;

            if (isUnder) {
              // UNDER: a "hit" (game went under line) must visually
              // stay below the line. If it's too close to the line,
              // push it down slightly so the line is clearly above.
              if (isHit && barHeightPercent > linePosition - 3) {
                barHeightPercent = Math.min(
                  barHeightPercent, linePosition - 2);
              }
            } else {
              // OVER: a miss (under line) must stay visibly below.
              if (!isHit && barHeightPercent > linePosition - 3) {
                barHeightPercent = Math.min(
                  barHeightPercent, linePosition - 2);
              }
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
                  } ring-1 ring-white/80`}
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
            const isHit = isHitFn(item.value);
            if (isUnder) {
              if (isHit && barHeightPercent > linePosition - 3) {
                barHeightPercent = Math.min(
                  barHeightPercent, linePosition - 2);
              }
            } else {
              if (!isHit && barHeightPercent > linePosition - 3) {
                barHeightPercent = Math.min(
                  barHeightPercent, linePosition - 2);
              }
            }
            
            return (
              <div 
                key={idx}
                className="relative flex flex-col items-center h-full justify-end"
                style={{ width: `${85 / values.length}%` }}
              >
                <div 
                  className="absolute text-[13px] font-bold text-white"
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
            className="text-[10px] text-white font-medium"
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
