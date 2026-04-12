/**
 * MLB Matchup Analysis Component
 * ==============================
 * Displays split matchup analysis for MLB props in the Vision Intel Suite.
 * 
 * FOR HITTER PROPS (The Pitching Gauntlet):
 * - vs. Starting Pitcher: xFIP-based rank
 * - vs. Bullpen: ERA-based rank
 * - Overall Edge calculation
 * 
 * FOR PITCHER PROPS (The Discipline Check):
 * - Lineup K-Rate: How easy to strike out
 * - Lineup wRC+: Offensive threat level
 * - Overall Edge calculation
 */

import React from 'react';
import { Target, Flame, Shield, Zap } from 'lucide-react';

// Determine if stat is a pitcher prop
const isPitcherStat = (statType) => {
  const normalizedStat = (statType || '').toLowerCase().replace(/\s+/g, '_').replace(/-/g, '_');
  const pitcherStats = [
    'k', 'outs', 'er', 'pitcher_strikeouts', 'earned_runs', 'pitching_outs',
    'strikeouts', 'walks_allowed', 'hits_allowed'
  ];
  return pitcherStats.includes(normalizedStat);
};

// Get color classes based on rank
const getRankColorClasses = (rank, isForPitcherKProp = false) => {
  // For pitcher K props, flip the interpretation (high rank = good = green)
  if (isForPitcherKProp) {
    if (rank >= 21) return { bg: 'bg-green-500', text: 'text-green-400', border: 'border-green-500/30' };
    if (rank >= 11) return { bg: 'bg-amber-500', text: 'text-amber-400', border: 'border-amber-500/30' };
    return { bg: 'bg-red-500', text: 'text-red-400', border: 'border-red-500/30' };
  }
  
  // Standard: low rank = tough = red, high rank = easy = green
  if (rank <= 10) return { bg: 'bg-red-500', text: 'text-red-400', border: 'border-red-500/30' };
  if (rank <= 20) return { bg: 'bg-amber-500', text: 'text-amber-400', border: 'border-amber-500/30' };
  return { bg: 'bg-green-500', text: 'text-green-400', border: 'border-green-500/30' };
};

// Get edge color
const getEdgeColor = (edge) => {
  if (edge >= 10) return 'text-green-400';
  if (edge > 0) return 'text-green-300';
  if (edge === 0) return 'text-zinc-300';
  if (edge >= -10) return 'text-red-300';
  return 'text-red-400';
};

/**
 * Hitter Matchup Display
 * Shows: SP matchup + Bullpen matchup + Overall Edge
 */
const HitterMatchupDisplay = ({ matchupData, opponent }) => {
  const { sp_matchup, bullpen_matchup, overall_edge, edge_label } = matchupData;
  
  const spColors = getRankColorClasses(sp_matchup?.rank || 15);
  const bpColors = getRankColorClasses(bullpen_matchup?.rank || 15);
  
  return (
    <div className="bg-gradient-to-r from-cyan-950/40 to-zinc-900 border border-cyan-500/30 rounded-lg p-4">
      <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
        <Target className="w-4 h-4 text-cyan-400" />
        MATCHUP ANALYSIS
      </h3>
      
      {/* Split Matchup Rows */}
      <div className="space-y-2">
        {/* vs Starting Pitcher */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded px-3 py-2">
          <div className="flex items-center gap-2">
            <Flame className="w-4 h-4 text-orange-400" />
            <span className="text-sm text-zinc-300">
              vs. SP {sp_matchup?.pitcher_name && sp_matchup.pitcher_name !== 'Unknown' 
                ? `(${sp_matchup.pitcher_name})` 
                : ''}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${spColors.bg} text-white`}>
              {sp_matchup?.label || 'Medium'}
            </span>
            <span className={`text-xs font-mono ${spColors.text}`}>
              Rank #{sp_matchup?.rank || 15}
            </span>
          </div>
        </div>
        
        {/* vs Bullpen */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded px-3 py-2">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-blue-400" />
            <span className="text-sm text-zinc-300">
              vs. Bullpen ({bullpen_matchup?.team || opponent || 'OPP'})
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${bpColors.bg} text-white`}>
              {bullpen_matchup?.label || 'Medium'}
            </span>
            <span className={`text-xs font-mono ${bpColors.text}`}>
              Rank #{bullpen_matchup?.rank || 15}
            </span>
          </div>
        </div>
      </div>
      
      {/* Overall Edge */}
      <div className="mt-3 pt-3 border-t border-zinc-700/50">
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-400">Overall Edge</span>
          <div className="flex items-center gap-2">
            <span className={`text-lg font-bold ${getEdgeColor(overall_edge)}`}>
              {overall_edge > 0 ? '+' : ''}{overall_edge?.toFixed?.(1) || '0'}%
            </span>
            <span className={`text-xs px-2 py-0.5 rounded ${
              overall_edge > 0 ? 'bg-green-500/20 text-green-300' : 
              overall_edge < 0 ? 'bg-red-500/20 text-red-300' : 
              'bg-zinc-500/20 text-zinc-300'
            }`}>
              {edge_label || 'Neutral'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * Pitcher Matchup Display
 * Shows: Lineup K-Rate + Lineup wRC+ + Overall Edge
 */
const PitcherMatchupDisplay = ({ matchupData, opponent }) => {
  const { k_rate_matchup, wrc_matchup, overall_edge, edge_label } = matchupData;
  
  // For K-rate, high rank = easy to K = good for pitcher
  const kColors = getRankColorClasses(k_rate_matchup?.rank || 15, true);
  // For wRC+, low rank = weak offense = good for pitcher (flip display)
  const wrcColors = getRankColorClasses(31 - (wrc_matchup?.rank || 15));
  
  return (
    <div className="bg-gradient-to-r from-purple-950/40 to-zinc-900 border border-purple-500/30 rounded-lg p-4">
      <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
        <Zap className="w-4 h-4 text-purple-400" />
        MATCHUP ANALYSIS
      </h3>
      
      {/* Split Matchup Rows */}
      <div className="space-y-2">
        {/* Lineup K-Rate */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded px-3 py-2">
          <div className="flex items-center gap-2">
            <Target className="w-4 h-4 text-purple-400" />
            <span className="text-sm text-zinc-300">Lineup K-Rate</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${kColors.bg} text-white`}>
              {k_rate_matchup?.label || 'Medium'}
            </span>
            <span className={`text-xs font-mono ${kColors.text}`}>
              Rank #{k_rate_matchup?.rank || 15}
            </span>
            {k_rate_matchup?.note && (
              <span className="text-[10px] text-zinc-500">
                ({k_rate_matchup.note})
              </span>
            )}
          </div>
        </div>
        
        {/* Lineup wRC+ */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded px-3 py-2">
          <div className="flex items-center gap-2">
            <Flame className="w-4 h-4 text-orange-400" />
            <span className="text-sm text-zinc-300">Lineup wRC+</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${wrcColors.bg} text-white`}>
              {wrc_matchup?.label || 'Medium'}
            </span>
            <span className={`text-xs font-mono ${wrcColors.text}`}>
              Rank #{wrc_matchup?.rank || 15}
            </span>
          </div>
        </div>
      </div>
      
      {/* Overall Edge */}
      <div className="mt-3 pt-3 border-t border-zinc-700/50">
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-400">Overall Edge</span>
          <div className="flex items-center gap-2">
            <span className={`text-lg font-bold ${getEdgeColor(overall_edge)}`}>
              {overall_edge > 0 ? '+' : ''}{overall_edge?.toFixed?.(1) || '0'}%
            </span>
            <span className={`text-xs px-2 py-0.5 rounded ${
              overall_edge > 0 ? 'bg-green-500/20 text-green-300' : 
              overall_edge < 0 ? 'bg-red-500/20 text-red-300' : 
              'bg-zinc-500/20 text-zinc-300'
            }`}>
              {edge_label || 'Neutral'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * Main MLB Matchup Analysis Component
 * Auto-detects hitter vs pitcher props and renders appropriate view
 */
export const MLBMatchupAnalysis = ({ 
  matchupData, 
  statType, 
  opponent,
  className = '' 
}) => {
  // If no matchup data, don't render
  if (!matchupData) return null;
  
  const isPitcher = matchupData.prop_type === 'pitcher' || isPitcherStat(statType);
  
  return (
    <div className={className}>
      {isPitcher ? (
        <PitcherMatchupDisplay matchupData={matchupData} opponent={opponent} />
      ) : (
        <HitterMatchupDisplay matchupData={matchupData} opponent={opponent} />
      )}
    </div>
  );
};

export default MLBMatchupAnalysis;
