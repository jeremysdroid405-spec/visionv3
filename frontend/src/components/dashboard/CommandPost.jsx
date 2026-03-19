/**
 * COMMAND POST COMPONENT
 * ======================
 * Risk Assessment Hub - Parlay Simulator
 * 
 * SSOT Two-Pipe Architecture:
 * - PIPE 1: useMasterStats for player stats (24hr cache)
 * - PIPE 2: usePlayerProfile for profiles, useSimulation for parlay sim
 * 
 * HIGHLANDER PROTOCOL: All data via TanStack Query - no localized fetches
 * 
 * Terminology (No "Certainty"):
 * - Convergence Rate: Combined tactical probability
 * - Infiltration Grade: Overall risk assessment (S/A/B/C/D)
 * - Volatility Index: Outcome variance measure
 * - Defensive Friction: DvP-based resistance
 */

import React, { useState, useCallback, memo, useMemo, useEffect } from 'react';
import { 
  Shield, AlertTriangle, TrendingUp, X, Plus, 
  Target, ChevronDown, ChevronUp, Trash2, RefreshCw, Lock, Ban
} from 'lucide-react';
import { Button } from '../ui/button';
import { toast } from 'sonner';
import CommandSearch from './CommandSearch';
import UniversalPlayerCard from './UniversalPlayerCard';

// SSOT Global State Hooks - TanStack Query
import { usePlayerProfile, useSimulation } from '../../hooks/useLiveOdds';

// Grade colors and styles
const GRADE_STYLES = {
  S: { bg: 'bg-emerald-500/20', border: 'border-emerald-500', text: 'text-emerald-400', glow: 'shadow-emerald-500/20' },
  A: { bg: 'bg-blue-500/20', border: 'border-blue-500', text: 'text-blue-400', glow: 'shadow-blue-500/20' },
  B: { bg: 'bg-amber-500/20', border: 'border-amber-500', text: 'text-amber-400', glow: 'shadow-amber-500/20' },
  C: { bg: 'bg-orange-500/20', border: 'border-orange-500', text: 'text-orange-400', glow: 'shadow-orange-500/20' },
  D: { bg: 'bg-red-500/20', border: 'border-red-500', text: 'text-red-400', glow: 'shadow-red-500/20' },
  '-': { bg: 'bg-zinc-700/50', border: 'border-zinc-600', text: 'text-zinc-400', glow: '' }
};

// Infiltration Grade Display
const InfiltrationGrade = memo(({ grade, label, convergenceRate }) => {
  const style = GRADE_STYLES[grade] || GRADE_STYLES['-'];
  
  return (
    <div 
      className={`p-4 rounded-lg border ${style.border} ${style.bg} ${style.glow} shadow-lg`}
      data-testid="infiltration-grade"
    >
      <div className="flex items-center justify-between">
        <div>
          <span className="text-[10px] uppercase tracking-wider text-zinc-400">
            Infiltration Grade
          </span>
          <div className={`text-4xl font-black ${style.text} mt-1`}>
            {grade}
          </div>
        </div>
        <div className="text-right">
          <span className="text-[10px] uppercase tracking-wider text-zinc-400">
            Convergence Rate
          </span>
          <div className={`text-2xl font-bold ${style.text} mt-1`}>
            {convergenceRate?.toFixed(1) || 0}%
          </div>
        </div>
      </div>
      <p className="text-xs text-zinc-400 mt-2 border-t border-zinc-700 pt-2">
        {label || 'Add legs to begin simulation'}
      </p>
    </div>
  );
});

// Volatility Index Display
const VolatilityDisplay = memo(({ index, label }) => {
  const isHigh = label === 'High Volatility';
  const isMedium = label === 'Medium Volatility';
  
  return (
    <div className="flex items-center gap-2 p-2 rounded bg-zinc-800/50 border border-zinc-700">
      <AlertTriangle className={`w-4 h-4 ${isHigh ? 'text-red-400' : isMedium ? 'text-amber-400' : 'text-emerald-400'}`} />
      <div>
        <span className="text-[10px] uppercase text-zinc-500">Volatility Index</span>
        <div className={`text-sm font-medium ${isHigh ? 'text-red-400' : isMedium ? 'text-amber-400' : 'text-emerald-400'}`}>
          {index?.toFixed(2) || '0.00'} - {label || 'N/A'}
        </div>
      </div>
    </div>
  );
});

// Single Leg Card in Configuration
const LegCard = memo(({ leg, index, onRemove, hasConflict, conflictMessage }) => {
  const dvpColor = {
    green: 'text-emerald-400',
    yellow: 'text-amber-400',
    red: 'text-red-400'
  }[leg.dvp_rank_color] || 'text-zinc-400';

  // If this leg has a conflict, show redacted state
  if (hasConflict) {
    return (
      <div 
        className="flex items-center gap-2 p-2 rounded bg-red-950/30 border-2 border-red-500/50 group relative"
        data-testid={`leg-card-${index}-conflict`}
      >
        <div className="absolute inset-0 bg-red-500/5 pointer-events-none" 
             style={{ backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 5px, rgba(239,68,68,0.1) 5px, rgba(239,68,68,0.1) 10px)' }} />
        
        <Lock className="w-4 h-4 text-red-400 flex-shrink-0" />
        
        <div className="flex-1 min-w-0 relative z-10">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-red-300 truncate line-through opacity-60">
              {leg.player_name}
            </span>
            <span className="px-1.5 py-0.5 text-[9px] font-bold bg-red-500 text-white rounded">
              REDACTED
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-red-400 font-medium line-through opacity-60">
              {leg.stat_type} {leg.direction?.toUpperCase()} {leg.line}
            </span>
          </div>
          <div className="mt-1 text-[10px] text-red-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            {conflictMessage || 'Tactical Conflict: Mutually Exclusive Parameters'}
          </div>
        </div>
        
        <button
          onClick={() => onRemove(index)}
          className="p-1 text-red-400 hover:text-white transition-opacity"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div 
      className="flex items-center gap-2 p-2 rounded bg-zinc-800/50 border border-zinc-700 group"
      data-testid={`leg-card-${index}`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-white truncate">
            {leg.player_name}
          </span>
          <span className="text-[10px] text-zinc-500 font-mono">
            {leg.team}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[11px] text-cyan-400 font-medium">
            {leg.stat_type} {leg.direction?.toUpperCase()} {leg.line}
          </span>
          {leg.dvp_rank && (
            <div className={`flex items-center gap-0.5 ${dvpColor}`}>
              <Shield className="w-2.5 h-2.5" />
              <span className="text-[10px]">#{leg.dvp_rank}</span>
            </div>
          )}
        </div>
      </div>
      
      <div className="text-right">
        <div className="text-xs text-zinc-400">
          <span className={leg.volatility_label === 'High Volatility' ? 'text-red-400' : ''}>
            {leg.tactical_probability?.toFixed(1) || leg.h10_rate?.toFixed(1) || '--'}%
          </span>
        </div>
        <div className="text-[10px] text-zinc-500">
          {leg.friction_label?.split(' ')[0] || 'Standard'}
        </div>
      </div>
      
      <button
        onClick={() => onRemove(index)}
        className="p-1 opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 transition-opacity"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
});

// Risk Flags Display
const RiskFlags = memo(({ flags }) => {
  if (!flags || flags.length === 0) return null;
  
  return (
    <div className="space-y-1">
      {flags.map((flag, idx) => (
        <div 
          key={idx}
          className={`flex items-start gap-2 p-2 rounded text-xs ${
            flag.startsWith('CRITICAL') 
              ? 'bg-red-500/10 border border-red-500/30 text-red-400'
              : flag.startsWith('HIGH') 
                ? 'bg-orange-500/10 border border-orange-500/30 text-orange-400'
                : 'bg-amber-500/10 border border-amber-500/30 text-amber-400'
          }`}
        >
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <span>{flag}</span>
        </div>
      ))}
    </div>
  );
});

// Main Command Post Component
const CommandPost = memo(({ isOpen, onClose, pendingLeg, onPendingLegProcessed }) => {
  const [legs, setLegs] = useState([]);
  const [simulation, setSimulation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [expanded, setExpanded] = useState(true);

  // ==================== STRICT CONFLICT ENGINE ====================
  // Check if a player already exists in legs (BLOCK duplicate players entirely)
  const isPlayerInLegs = useCallback((playerId, playerName) => {
    return legs.some(leg => 
      leg.player_id === playerId || 
      leg.player_name?.toLowerCase() === playerName?.toLowerCase()
    );
  }, [legs]);

  // Process incoming pendingLeg from Quick-Add
  React.useEffect(() => {
    if (pendingLeg && onPendingLegProcessed) {
      // STRICT CONFLICT CHECK: Block if player already exists
      if (isPlayerInLegs(pendingLeg.player_id, pendingLeg.player_name)) {
        toast.error(`Conflict: ${pendingLeg.player_name} is already in your Command Hub`, {
          description: 'Remove the existing prop first to add a different one.',
          icon: <Ban className="w-4 h-4" />,
          duration: 4000,
        });
        onPendingLegProcessed();
        return;
      }
      
      const newLeg = {
        player_name: pendingLeg.player_name,
        player_id: pendingLeg.player_id,
        stat_type: pendingLeg.stat_type,
        line: pendingLeg.demon_line || pendingLeg.goblin_line || pendingLeg.line || 0,
        direction: pendingLeg.direction || 'over',
        team: pendingLeg.team || '',
        opponent: pendingLeg.opponent || pendingLeg.opponent_abbr || '',
        is_home: pendingLeg.is_home ?? true,
        h10_rate: pendingLeg.h10_rate || 50,
        h5_rate: pendingLeg.h5_rate || 50,
        season_avg: pendingLeg.season_avg,
        l5_avg: pendingLeg.l5_avg,
        l10_avg: pendingLeg.l10_avg,
        usage_bump_percent: pendingLeg.usage_bump_percent || 0,
        dvp_rank: pendingLeg.dvp_rank,
        dvp_rank_color: pendingLeg.dvp_rank_color
      };
      
      setLegs(prev => [...prev, newLeg]);
      toast.success(`Added: ${newLeg.player_name} ${newLeg.stat_type} ${newLeg.direction} ${newLeg.line}`, {
        duration: 2000,
      });
      
      onPendingLegProcessed();
    }
  }, [pendingLeg, onPendingLegProcessed, isPlayerInLegs]);

  // State for profile fetching via hook
  const [profilePlayerName, setProfilePlayerName] = useState(null);
  const { data: profileData, isLoading: profileQueryLoading } = usePlayerProfile(profilePlayerName);
  
  // Sync profile data from TanStack Query
  useEffect(() => {
    if (profileData?.success) {
      setSelectedProfile(profileData);
      setProfileLoading(false);
    }
  }, [profileData]);

  // PIPE 2: Fetch player profile via usePlayerProfile hook
  const fetchProfile = useCallback((player) => {
    setProfileLoading(true);
    setProfilePlayerName(player.player_name);
  }, []);

  // Add leg from profile line selection (with STRICT conflict check)
  const addLegFromLine = useCallback((line) => {
    if (!selectedProfile || !line) return;
    
    // STRICT CONFLICT CHECK: Block if player already exists
    if (isPlayerInLegs(selectedProfile.player_id, selectedProfile.player_name)) {
      toast.error(`Conflict: ${selectedProfile.player_name} is already in your Command Hub`, {
        description: 'Remove the existing prop first to add a different one.',
        icon: <Ban className="w-4 h-4" />,
        duration: 4000,
      });
      return;
    }
    
    const newLeg = {
      player_name: selectedProfile.player_name,
      player_id: selectedProfile.player_id,
      stat_type: line.stat_type,
      line: line.line,
      direction: line.direction || 'over',
      team: selectedProfile.team,
      opponent: selectedProfile.opponent,
      is_home: true,
      h10_rate: line.h10_rate || line.hit_rates?.h10 || 50,
      h5_rate: line.h5_rate || line.hit_rates?.h5 || 50,
      season_avg: line.season_avg,
      l5_avg: line.l5_avg,
      l10_avg: line.l10_avg,
      usage_bump_percent: selectedProfile.usage_ripple?.bump_percent || 0,
      dvp_rank: line.dvp_rank,
      dvp_rank_color: line.dvp_rank_color
    };
    
    setLegs(prev => [...prev, newLeg]);
    toast.success(`Added: ${newLeg.player_name} ${newLeg.stat_type} ${newLeg.direction} ${newLeg.line}`, {
      duration: 2000,
    });
    setSelectedProfile(null);
    setProfilePlayerName(null);  // Clear to allow re-fetching
  }, [selectedProfile, isPlayerInLegs]);

  // Remove leg
  const removeLeg = useCallback((index) => {
    setLegs(prev => prev.filter((_, i) => i !== index));
  }, []);

  // Clear all legs
  const clearAll = useCallback(() => {
    setLegs([]);
    setSimulation(null);
  }, []);

  // PIPE 2: Run simulation via TanStack Query useMutation (HIGHLANDER PROTOCOL)
  const simulationMutation = useSimulation();
  
  const runSimulationHandler = useCallback(() => {
    if (legs.length === 0) return;
    
    setLoading(true);
    simulationMutation.mutate(legs, {
      onSuccess: (data) => {
        if (data.success) {
          setSimulation(data.simulation);
          if (data.simulation.legs) {
            setLegs(data.simulation.legs);
          }
        }
        setLoading(false);
      },
      onError: (error) => {
        console.error('Simulation error:', error);
        setLoading(false);
      }
    });
  }, [legs, simulationMutation]);

  // ==================== CONFLICT DETECTION ENGINE ====================
  // Detect mutually exclusive parameters (Over/Under on same player+stat)
  const conflicts = useMemo(() => {
    const conflictMap = new Map();
    
    legs.forEach((leg, idx) => {
      // Create a unique key for player + stat type
      const key = `${leg.player_name}-${leg.stat_type}`;
      
      if (conflictMap.has(key)) {
        const existingIdx = conflictMap.get(key);
        const existingLeg = legs[existingIdx];
        
        // Check if directions conflict (over vs under on same line)
        if (existingLeg.direction?.toLowerCase() !== leg.direction?.toLowerCase()) {
          return { 
            indices: [existingIdx, idx], 
            message: `Tactical Conflict: Cannot have OVER and UNDER on ${leg.player_name} ${leg.stat_type}`
          };
        }
        
        // Check if same direction but different lines (also a conflict)
        if (existingLeg.line !== leg.line) {
          return {
            indices: [existingIdx, idx],
            message: `Tactical Conflict: Duplicate objective on ${leg.player_name} ${leg.stat_type}`
          };
        }
      }
      
      conflictMap.set(key, idx);
    });
    
    // Build a map of index -> conflict info
    const result = {};
    legs.forEach((leg, idx) => {
      const key = `${leg.player_name}-${leg.stat_type}`;
      const otherLegs = legs.filter((l, i) => i !== idx && `${l.player_name}-${l.stat_type}` === key);
      
      if (otherLegs.length > 0) {
        const otherLeg = otherLegs[0];
        if (otherLeg.direction?.toLowerCase() !== leg.direction?.toLowerCase()) {
          result[idx] = `Tactical Conflict: ${leg.direction?.toUpperCase()} conflicts with ${otherLeg.direction?.toUpperCase()}`;
        } else if (otherLeg.line !== leg.line) {
          result[idx] = `Tactical Conflict: Duplicate ${leg.stat_type} objective at different lines`;
        }
      }
    });
    
    return result;
  }, [legs]);

  const hasAnyConflicts = Object.keys(conflicts).length > 0;

  if (!isOpen) return null;

  return (
    <div 
      className="fixed right-0 top-0 h-full w-96 bg-zinc-950 border-l border-zinc-800 shadow-2xl z-50 flex flex-col"
      data-testid="command-post"
    >
      {/* Header - Fixed */}
      <div className="p-4 border-b border-zinc-800 bg-gradient-to-r from-zinc-900 to-zinc-950 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Target className="w-5 h-5 text-emerald-400" />
              Command Hub
            </h2>
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider">
              Risk Assessment Hub
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-zinc-800 rounded text-zinc-400 hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Scrollable Content Area with Custom Scrollbar */}
      <div 
        className="flex-1 overflow-y-auto scrollbar-thin scrollbar-track-zinc-900 scrollbar-thumb-zinc-700 hover:scrollbar-thumb-zinc-600"
        style={{
          scrollbarWidth: 'thin',
          scrollbarColor: '#52525b #18181b'
        }}
      >
        {/* Search Section */}
        <div className="p-4 border-b border-zinc-800">
          <CommandSearch 
            onPlayerSelect={fetchProfile}
            placeholder="Search player to add leg..."
          />
          
          {/* Selected Profile - Using Tactical Player Card */}
          {profileLoading && (
            <div className="mt-3 text-center text-sm text-zinc-400">
              Loading profile...
            </div>
          )}
          
          {selectedProfile && !profileLoading && (
            <div className="mt-3">
              <UniversalPlayerCard 
                player={selectedProfile.playerData || {
                  player_name: selectedProfile.player_name,
                  player_id: selectedProfile.player_id,
                  team: selectedProfile.team,
                  position: selectedProfile.position,
                  photo_url: selectedProfile.photo_url,
                  opponent: selectedProfile.opponent,
                }}
                props={selectedProfile.lines?.map(line => ({
                  stat_type: line.stat_type,
                  line: line.line,
                  direction: line.direction || 'over',
                  odds: line.odds,
                  l5_avg: line.l5_avg || line.hit_rates?.l5?.avg,
                  l10_avg: line.l10_avg || line.hit_rates?.l10?.avg,
                  season_avg: line.season_avg || line.hit_rates?.season?.avg,
                  h5_rate: line.h5_rate || (line.hit_rates?.l5?.hit_rate ? line.hit_rates.l5.hit_rate * 100 : null),
                  h10_rate: line.h10_rate || (line.hit_rates?.l10?.hit_rate ? line.hit_rates.l10.hit_rate * 100 : null),
                  is_demon: line.is_demon,
                  is_goblin: line.is_goblin,
                  tier_label: line.tier_label
                })) || []}
                onClick={(playerOrProp) => {
                  // When clicking a prop in the player card
                  if (isPlayerInLegs(selectedProfile.player_id, selectedProfile.player_name)) {
                    toast.error(`Conflict: ${selectedProfile.player_name} is already in your Command Hub`, {
                      description: 'Remove the existing prop first to add a different one.',
                      icon: <Ban className="w-4 h-4" />,
                      duration: 4000,
                    });
                    return;
                  }
                  
                  // Check if it's a prop click (has stat_type) or player click
                  if (playerOrProp.stat_type) {
                    const newLeg = {
                      player_name: selectedProfile.player_name,
                      player_id: selectedProfile.player_id,
                      stat_type: playerOrProp.stat_type,
                      line: playerOrProp.line,
                      direction: playerOrProp.direction || 'over',
                      team: selectedProfile.team,
                      opponent: selectedProfile.opponent,
                      is_home: true,
                      h10_rate: playerOrProp.h10_rate || 50,
                      h5_rate: playerOrProp.h5_rate || 50,
                      season_avg: playerOrProp.season_avg,
                      l5_avg: playerOrProp.l5_avg,
                      l10_avg: playerOrProp.l10_avg
                    };
                    
                    setLegs(prev => [...prev, newLeg]);
                    toast.success(`Added: ${newLeg.player_name} ${newLeg.stat_type} ${newLeg.direction} ${newLeg.line}`, {
                      duration: 2000,
                    });
                  }
                }}
              />
              <button
                onClick={() => setSelectedProfile(null)}
                className="w-full mt-2 py-2 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Close Profile
              </button>
            </div>
          )}
        </div>

        {/* Simulation Results */}
        <div className="p-4 border-b border-zinc-800">
          <InfiltrationGrade 
            grade={simulation?.infiltration_grade || '-'}
            label={simulation?.grade_label}
            convergenceRate={simulation?.convergence_rate}
          />
          
          <div className="grid grid-cols-2 gap-2 mt-3">
            <VolatilityDisplay 
              index={simulation?.volatility_index}
              label={simulation?.volatility_label}
            />
            <div className="flex items-center gap-2 p-2 rounded bg-zinc-800/50 border border-zinc-700">
              <TrendingUp className="w-4 h-4 text-cyan-400" />
              <div>
                <span className="text-[10px] uppercase text-zinc-500">Correlation</span>
                <div className="text-sm font-medium text-cyan-400">
                  -{simulation?.correlation_penalty || 0}%
                </div>
              </div>
            </div>
          </div>

          {/* Risk Flags */}
          {simulation?.risk_flags?.length > 0 && (
            <div className="mt-3">
              <RiskFlags flags={simulation.risk_flags} />
            </div>
          )}

          {/* Environmental Summary */}
          {simulation?.environmental_summary && (
            <p className="text-[11px] text-zinc-500 mt-2 text-center">
              {simulation.environmental_summary}
            </p>
          )}
        </div>

        {/* Active Configuration */}
        <div className="p-4">
          <div className="flex items-center justify-between mb-3">
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-2 text-xs font-medium text-zinc-400 hover:text-white"
            >
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              Active Configuration ({legs.length})
            </button>
            {legs.length > 0 && (
              <button
                onClick={clearAll}
                className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-red-400"
              >
                <Trash2 className="w-3 h-3" />
                Clear
              </button>
            )}
          </div>

          {expanded && (
            <div className="space-y-2">
              {hasAnyConflicts && (
                <div className="flex items-center gap-2 p-2 rounded bg-red-950/30 border border-red-500/30 mb-3">
                  <AlertTriangle className="w-4 h-4 text-red-400" />
                  <span className="text-xs text-red-400">
                    Tactical conflicts detected - resolve before simulation
                  </span>
                </div>
              )}
              {legs.length > 0 ? (
                legs.map((leg, idx) => (
                  <LegCard 
                    key={`${leg.player_name}-${leg.stat_type}-${idx}`}
                    leg={leg}
                    index={idx}
                    onRemove={removeLeg}
                    hasConflict={!!conflicts[idx]}
                    conflictMessage={conflicts[idx]}
                  />
                ))
              ) : (
                <div className="text-center py-8 text-zinc-500">
                  <Target className="w-8 h-8 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No objectives configured</p>
                  <p className="text-xs mt-1">Search and select players above</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Actions - Fixed at Bottom */}
      <div className="p-4 border-t border-zinc-800 bg-zinc-900/50 flex-shrink-0">
        <Button
          onClick={runSimulationHandler}
          disabled={legs.length === 0 || loading || hasAnyConflicts}
          className={`w-full font-medium ${
            hasAnyConflicts 
              ? 'bg-red-900/50 text-red-400 cursor-not-allowed' 
              : 'bg-cyan-600 hover:bg-cyan-500 text-white'
          }`}
          data-testid="run-simulation-btn"
        >
          {loading ? (
            <>
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
              Simulating...
            </>
          ) : hasAnyConflicts ? (
            <>
              <Lock className="w-4 h-4 mr-2" />
              Resolve Conflicts
            </>
          ) : (
            <>
              <Target className="w-4 h-4 mr-2" />
              Run Simulation
            </>
          )}
        </Button>
      </div>
    </div>
  );
});

CommandPost.displayName = 'CommandPost';
InfiltrationGrade.displayName = 'InfiltrationGrade';
VolatilityDisplay.displayName = 'VolatilityDisplay';
LegCard.displayName = 'LegCard';
RiskFlags.displayName = 'RiskFlags';

export default CommandPost;
