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
import { usePlayerProfile, useSimulation, useCommandCenterProps } from '../../hooks/useLiveOdds';
import { useSport } from '../../context/SportContext';
import { getStatLabel } from '../../utils/statLabel';

// Grade colors and styles
const GRADE_STYLES = {
  A: { bg: 'bg-emerald-500/20', border: 'border-emerald-500', text: 'text-emerald-400', glow: 'shadow-emerald-500/20' },
  B: { bg: 'bg-lime-500/20', border: 'border-lime-500', text: 'text-lime-400', glow: 'shadow-lime-500/20' },
  C: { bg: 'bg-yellow-500/20', border: 'border-yellow-500', text: 'text-yellow-400', glow: 'shadow-yellow-500/20' },
  D: { bg: 'bg-orange-500/20', border: 'border-orange-500', text: 'text-orange-400', glow: 'shadow-orange-500/20' },
  F: { bg: 'bg-red-500/20', border: 'border-red-500', text: 'text-red-400', glow: 'shadow-red-500/20' },
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
              {getStatLabel(leg.stat_type)} {leg.direction?.toUpperCase()} {leg.line}
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
            {getStatLabel(leg.stat_type)} {leg.direction?.toUpperCase()} {leg.line}
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
            {/* 2026-05-07 P0 Phase 4B: canonical `hit_rate_l10`. */}
            {leg.tactical_probability?.toFixed(1) || leg.hit_rate_l10?.toFixed(1) || '--'}%
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
  // 2026-05-08 — multi-sport awareness: source sport from SportContext.
  const { currentSport } = useSport();
  const [legs, setLegs] = useState([]);
  const [simulation, setSimulation] = useState(null);
  // 2026-05-08 — `loading` is derived from the mutation's own pending
  // state below (`simulationMutation.isPending`). Local setLoading was
  // removed because if the local onSuccess/onError closure ever failed
  // to fire (closure capture / unmount race / mobile bundle stale),
  // the button stayed disabled forever. The mutation cache is the
  // single source of truth.
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [expanded, setExpanded] = useState(true);

  // ==================== STRICT CONFLICT ENGINE ====================
  // Check if a player already exists in legs (BLOCK duplicate players entirely)
  const isPlayerInLegs = useCallback((playerId, playerName) => {
    // Must have either a valid playerId OR playerName to check
    if (!playerId && !playerName) return false;
    
    return legs.some(leg => {
      // Check by player_id (only if both are truthy)
      if (playerId && leg.player_id && leg.player_id === playerId) {
        return true;
      }
      // Check by player_name (case-insensitive)
      if (playerName && leg.player_name && 
          leg.player_name.toLowerCase() === playerName.toLowerCase()) {
        return true;
      }
      return false;
    });
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

      // 2026-05-08 — Universal canonical leg from Quick-Add.
      // Pick-card / PlayerDetail pendingLeg payloads already carry
      // canonical fields (`/api/v3/ferrari/all` and friends ship the
      // full prop_scores row). We forward canonical fields ONLY —
      // never legacy aliases (`h5_rate / h10_rate / hit_rate /
      // hit_rates`).
      const recommendation = (pendingLeg.recommendation || pendingLeg.direction || 'OVER')
        .toString()
        .toUpperCase();
      const newLeg = {
        canonical_key: pendingLeg.canonical_key || null,
        sport: pendingLeg.sport || currentSport,
        player_name: pendingLeg.player_name,
        player_id: pendingLeg.player_id,
        stat_type: pendingLeg.stat_type,
        line: pendingLeg.demon_line || pendingLeg.goblin_line || pendingLeg.line || 0,
        recommendation,
        direction: recommendation.toLowerCase(),
        // Hit rates (canonical only)
        hit_rate_l5: pendingLeg.hit_rate_l5,
        hit_rate_l10: pendingLeg.hit_rate_l10,
        hit_rate_l20: pendingLeg.hit_rate_l20,
        hit_rate_over: pendingLeg.hit_rate_over,
        hit_rate_under: pendingLeg.hit_rate_under,
        // Probability + edge
        p_true_active: pendingLeg.p_true_active,
        edge_vs_fair: pendingLeg.edge_vs_fair,
        vision_score: pendingLeg.vision_score,
        cv: pendingLeg.cv,
        // Tier context
        tier: pendingLeg.tier,
        tier_reason: pendingLeg.tier_reason,
        tier_reference_book: pendingLeg.tier_reference_book,
        tier_reference_odds: pendingLeg.tier_reference_odds,
        // Odds
        pp_odds: pendingLeg.pp_odds,
        dk_odds: pendingLeg.dk_odds,
        fd_odds: pendingLeg.fd_odds,
        bol_odds: pendingLeg.bol_odds,
        mgm_odds: pendingLeg.mgm_odds,
        // Game context
        team: pendingLeg.team || '',
        opponent: pendingLeg.opponent || pendingLeg.opponent_abbr || '',
        is_home: pendingLeg.is_home ?? true,
        event_id: pendingLeg.event_id || null,
        game_start_utc: pendingLeg.game_start_utc || null,
      };

      setLegs(prev => [...prev, newLeg]);
      toast.success(`Added: ${newLeg.player_name} ${getStatLabel(newLeg.stat_type)} ${newLeg.direction} ${newLeg.line}`, {
        duration: 2000,
      });

      onPendingLegProcessed();
    }
  }, [pendingLeg, onPendingLegProcessed, isPlayerInLegs, currentSport]);

  // State for profile fetching via the universal Command Center hook.
  // 2026-05-08 — Command Center is system-level. The universal route
  // (`/api/command/props`) reads canonical rows from
  // `{sport}_prop_scores[final-{sport}-rt]` only. No cached_board, no
  // sport-specific player-detail builder, no `.map()` reshape, no
  // legacy aliases (`h5_rate / h10_rate / hit_rate / hit_rates`).
  const [profilePlayerName, setProfilePlayerName] = useState(null);
  const {
    data: profileData,
    isLoading: profileQueryLoading,
    error: profileError,
  } = useCommandCenterProps(profilePlayerName, currentSport);

  // Sync profile data from TanStack Query
  useEffect(() => {
    if (!profileData) return;
    if (profileData.success === false) {
      setSelectedProfile(null);
      toast.error(`Player not found: ${profilePlayerName}`, {
        description: profileData.message || 'No active props for this player',
        duration: 3000,
      });
    } else if (Array.isArray(profileData.props) && profileData.props.length > 0) {
      const meta = profileData.meta || {};
      const first = profileData.props[0] || {};
      setSelectedProfile({
        sport: profileData.sport,
        player_name: profileData.player_name,
        player_id: meta.bdl_player_id || first.bdl_player_id || null,
        team: meta.team || first.team || '',
        opponent: first.opponent || '',
        position: meta.position || null,
        photo_url: meta.photo_url || null,
        // Canonical rows pass through verbatim — no reshape.
        props: profileData.props,
      });
    } else {
      setSelectedProfile(null);
    }
    setProfileLoading(false);
  }, [profileData, profilePlayerName]);
  
  // Handle profile fetch errors
  useEffect(() => {
    if (profileError) {
      setProfileLoading(false);
      setSelectedProfile(null);
      toast.error(`Error loading player profile`, {
        description: profileError.message || 'Please try again',
        duration: 3000,
      });
    }
  }, [profileError]);

  // PIPE 2: Fetch player profile via universal Command Center hook.
  const fetchProfile = useCallback((player) => {
    // Clear previous profile to avoid stale data conflicts
    setSelectedProfile(null);
    setProfileLoading(true);
    setProfilePlayerName(player.player_name);
  }, []);

  // Universal canonical leg builder — Command Center is sport-agnostic.
  // Forwards ONLY canonical fields from the score doc; never emits
  // legacy aliases.
  const buildCanonicalLeg = useCallback((profile, prop) => {
    if (!profile || !prop) return null;
    const recommendation = (prop.recommendation || prop.direction || 'OVER')
      .toString()
      .toUpperCase();
    return {
      // Identity
      canonical_key: prop.canonical_key || null,
      sport: prop.sport || profile.sport || currentSport,
      player_name: profile.player_name,
      player_id: profile.player_id,
      // Prop
      stat_type: prop.stat_type,
      line: prop.line,
      recommendation,
      direction: recommendation.toLowerCase(),
      // Hit rates (canonical trio + side-aware)
      hit_rate_l5: prop.hit_rate_l5,
      hit_rate_l10: prop.hit_rate_l10,
      hit_rate_l20: prop.hit_rate_l20,
      hit_rate_over: prop.hit_rate_over,
      hit_rate_under: prop.hit_rate_under,
      // Probability + edge
      p_true_active: prop.p_true_active,
      edge_vs_fair: prop.edge_vs_fair,
      vision_score: prop.vision_score,
      cv: prop.cv,
      // Tier context
      tier: prop.tier,
      tier_reason: prop.tier_reason,
      tier_reference_book: prop.tier_reference_book,
      tier_reference_odds: prop.tier_reference_odds,
      // Odds
      pp_odds: prop.pp_odds,
      dk_odds: prop.dk_odds,
      fd_odds: prop.fd_odds,
      bol_odds: prop.bol_odds,
      mgm_odds: prop.mgm_odds,
      // Game context
      team: profile.team || prop.team || '',
      opponent: profile.opponent || prop.opponent || '',
      is_home: prop.is_home ?? true,
      event_id: prop.event_id || null,
      game_start_utc: prop.game_start_utc || null,
    };
  }, [currentSport]);

  // Add leg from profile line selection (with STRICT conflict check)
  const addLegFromLine = useCallback((line) => {
    if (!selectedProfile || !line) return;

    const playerName = selectedProfile.player_name;
    const playerId = selectedProfile.player_id;

    // STRICT CONFLICT CHECK: Block if player already exists
    if (isPlayerInLegs(playerId, playerName)) {
      toast.error(`Conflict: ${playerName} is already in your Command Hub`, {
        description: 'Remove the existing prop first to add a different one.',
        icon: <Ban className="w-4 h-4" />,
        duration: 4000,
      });
      return;
    }

    const newLeg = buildCanonicalLeg(selectedProfile, line);
    if (!newLeg) return;
    setLegs(prev => [...prev, newLeg]);
    toast.success(`Added: ${newLeg.player_name} ${getStatLabel(newLeg.stat_type)} ${newLeg.direction} ${newLeg.line}`, {
      duration: 2000,
    });
    setSelectedProfile(null);
    setProfilePlayerName(null);  // Clear to allow re-fetching
  }, [selectedProfile, isPlayerInLegs, buildCanonicalLeg]);

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

    // 2026-05-08 — diagnostic + defensive: rely on React Query's
    // mutation cache instead of local loading state so the button
    // can never get stuck disabled.
    console.log('[CC] mutate → POST /api/command/simulate  legs=', legs);
    simulationMutation.mutate(legs, {
      onSuccess: (data) => {
        console.log('[CC] onSuccess data=', data);
        if (data && data.success) {
          console.log('[CC] setSimulation(', data.simulation, ')');
          setSimulation(data.simulation);
          if (data.simulation && data.simulation.legs) {
            setLegs(data.simulation.legs);
          }
        } else {
          console.warn('[CC] response.success falsy — panel will fall back to mutation cache');
        }
      },
      onError: (error) => {
        console.error('[CC] simulation error:', error);
      }
    });
  }, [legs, simulationMutation]);

  // Mutation pending state drives the button + spinner directly.
  const loading = simulationMutation.isPending;

  // 2026-05-08 — defensive panel source: prefer local state, but fall
  // back to the mutation's own data cache so the result panel always
  // renders the most-recent successful simulation even if the local
  // setState path is interrupted (closure / unmount race / stale
  // bundle). The mutation cache is owned by React Query and survives
  // re-renders.
  const effectiveSimulation = simulation || simulationMutation.data?.simulation || null;

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
            message: `Tactical Conflict: Cannot have OVER and UNDER on ${leg.player_name} ${getStatLabel(leg.stat_type)}`
          };
        }
        
        // Check if same direction but different lines (also a conflict)
        if (existingLeg.line !== leg.line) {
          return {
            indices: [existingIdx, idx],
            message: `Tactical Conflict: Duplicate objective on ${leg.player_name} ${getStatLabel(leg.stat_type)}`
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
          result[idx] = `Tactical Conflict: Duplicate ${getStatLabel(leg.stat_type)} objective at different lines`;
        }
      }
    });
    
    return result;
  }, [legs]);

  const hasAnyConflicts = Object.keys(conflicts).length > 0;

  if (!isOpen) return null;

  return (
    <div 
      className="fixed right-0 top-0 h-full w-full sm:w-96 bg-zinc-950 border-l border-zinc-800 shadow-2xl z-50 flex flex-col"
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
                player={{
                  player_name: selectedProfile.player_name,
                  player_id: selectedProfile.player_id,
                  team: selectedProfile.team,
                  position: selectedProfile.position,
                  photo_url: selectedProfile.photo_url,
                  opponent: selectedProfile.opponent,
                  sport: selectedProfile.sport,
                }}
                // 2026-05-08 — Universal Command Center: canonical rows
                // pass through verbatim from `/api/command/props`.
                // No `.map(...)` reshape, no legacy aliases.
                props={selectedProfile.props}
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
                  // PropRow click → playerOrProp is a canonical row.
                  if (playerOrProp.stat_type) {
                    const newLeg = buildCanonicalLeg(selectedProfile, playerOrProp);
                    if (!newLeg) return;
                    setLegs(prev => [...prev, newLeg]);
                    toast.success(`Added: ${newLeg.player_name} ${getStatLabel(newLeg.stat_type)} ${newLeg.direction} ${newLeg.line}`, {
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
          {/* 2026-05-08 — visible mobile-friendly status row so we can
              diagnose without DevTools. Renders the mutation state
              and HTTP-level signal. Will be removed once Command
              Center is verified stable. */}
          <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider"
               data-testid="cc-status-row">
            <span className="text-zinc-500">Status</span>
            <span className={
              simulationMutation.isError
                ? 'text-red-400'
                : simulationMutation.isPending
                  ? 'text-amber-400'
                  : effectiveSimulation
                    ? 'text-emerald-400'
                    : 'text-zinc-500'
            }>
              {simulationMutation.isError
                ? `error: ${(simulationMutation.error && simulationMutation.error.message) || 'unknown'}`.slice(0, 80)
                : simulationMutation.isPending
                  ? 'simulating...'
                  : effectiveSimulation
                    ? `ready • grade ${effectiveSimulation.infiltration_grade || '-'} • ${(effectiveSimulation.convergence_rate || 0).toFixed?.(1) ?? 0}%`
                    : 'idle'}
            </span>
          </div>

          <InfiltrationGrade 
            grade={effectiveSimulation?.infiltration_grade || '-'}
            label={effectiveSimulation?.grade_label}
            convergenceRate={effectiveSimulation?.convergence_rate}
          />
          
          <div className="grid grid-cols-2 gap-2 mt-3">
            <VolatilityDisplay 
              index={effectiveSimulation?.volatility_index}
              label={effectiveSimulation?.volatility_label}
            />
            {/* 2026-05-08 — universal correlation card. Backend ships
                `correlation_kind` ("none" | "same_player" | "same_game"
                | "same_team") so we can render an explicit, never-blank
                summary. The percent number remains `correlation_penalty`
                (back-compat). */}
            {(() => {
              const kind = effectiveSimulation?.correlation_kind || 'none';
              const pct = Math.max(0, Math.round(effectiveSimulation?.correlation_penalty || 0));
              const KIND_LABEL = {
                same_player: 'Same Player',
                same_game: 'Same Game',
                same_team: 'Same Team',
                none: 'Independent',
              };
              const label = KIND_LABEL[kind] || 'Independent';
              const pctText = kind === 'none' ? '0%' : `-${pct}%`;
              return (
                <div
                  className="flex items-center gap-2 p-2 rounded bg-zinc-800/50 border border-zinc-700"
                  data-testid="correlation-card"
                >
                  <TrendingUp className="w-4 h-4 text-cyan-400" />
                  <div>
                    <span className="text-[10px] uppercase text-zinc-500">Correlation</span>
                    <div
                      className="text-sm font-medium text-cyan-400"
                      data-testid="correlation-value"
                    >
                      {label} · {pctText}
                    </div>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Risk Flags */}
          {effectiveSimulation?.risk_flags?.length > 0 && (
            <div className="mt-3">
              <RiskFlags flags={effectiveSimulation.risk_flags} />
            </div>
          )}

          {/* Environmental Summary */}
          {effectiveSimulation?.environmental_summary && (
            <p className="text-[11px] text-zinc-500 mt-2 text-center">
              {effectiveSimulation.environmental_summary}
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
