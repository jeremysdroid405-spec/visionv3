/**
 * COMMAND POST COMPONENT
 * ======================
 * Risk Assessment Hub - Parlay Simulator
 * 
 * Terminology (No "Certainty"):
 * - Convergence Rate: Combined tactical probability
 * - Infiltration Grade: Overall risk assessment (S/A/B/C/D)
 * - Volatility Index: Outcome variance measure
 * - Defensive Friction: DvP-based resistance
 */

import React, { useState, useCallback, memo } from 'react';
import { 
  Shield, AlertTriangle, TrendingUp, X, Plus, 
  Target, ChevronDown, ChevronUp, Trash2, RefreshCw 
} from 'lucide-react';
import { Button } from '../ui/button';
import CommandSearch from './CommandSearch';
import TacticalProfile from './TacticalProfile';

const API_URL = process.env.REACT_APP_BACKEND_URL;

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
const LegCard = memo(({ leg, index, onRemove }) => {
  const dvpColor = {
    green: 'text-emerald-400',
    yellow: 'text-amber-400',
    red: 'text-red-400'
  }[leg.dvp_rank_color] || 'text-zinc-400';

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
          <div className={`flex items-center gap-0.5 ${dvpColor}`}>
            <Shield className="w-2.5 h-2.5" />
            <span className="text-[10px]">#{leg.dvp_rank}</span>
          </div>
        </div>
      </div>
      
      <div className="text-right">
        <div className="text-xs text-zinc-400">
          <span className={leg.volatility_label === 'High Volatility' ? 'text-red-400' : ''}>
            {leg.tactical_probability?.toFixed(1)}%
          </span>
        </div>
        <div className="text-[10px] text-zinc-500">
          {leg.friction_label?.split(' ')[0]}
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
const CommandPost = memo(({ isOpen, onClose }) => {
  const [legs, setLegs] = useState([]);
  const [simulation, setSimulation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [expanded, setExpanded] = useState(true);

  // Fetch player profile
  const fetchProfile = useCallback(async (player) => {
    setProfileLoading(true);
    try {
      const response = await fetch(
        `${API_URL}/api/command/profile/${encodeURIComponent(player.player_name)}`
      );
      const data = await response.json();
      
      if (data.success) {
        setSelectedProfile(data);
      } else {
        console.error('Profile fetch failed:', data.error);
      }
    } catch (error) {
      console.error('Profile error:', error);
    } finally {
      setProfileLoading(false);
    }
  }, []);

  // Add leg from profile line selection
  const addLegFromLine = useCallback((line) => {
    if (!selectedProfile || !line) return;
    
    const newLeg = {
      player_name: selectedProfile.player_name,
      player_id: selectedProfile.player_id,
      stat_type: line.stat_type,
      line: line.line,
      direction: line.direction || 'over',
      team: selectedProfile.team,
      opponent: selectedProfile.opponent,
      is_home: true,
      h10_rate: line.hit_rates?.h10 || 50,
      h5_rate: line.hit_rates?.h5 || 50,
      usage_bump_percent: selectedProfile.usage_ripple?.bump_percent || 0
    };
    
    setLegs(prev => [...prev, newLeg]);
    setSelectedProfile(null);
  }, [selectedProfile]);

  // Remove leg
  const removeLeg = useCallback((index) => {
    setLegs(prev => prev.filter((_, i) => i !== index));
  }, []);

  // Clear all legs
  const clearAll = useCallback(() => {
    setLegs([]);
    setSimulation(null);
  }, []);

  // Run simulation
  const runSimulation = useCallback(async () => {
    if (legs.length === 0) return;
    
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/command/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ legs })
      });
      const data = await response.json();
      
      if (data.success) {
        setSimulation(data.simulation);
        // Update legs with simulation results
        if (data.simulation.legs) {
          setLegs(data.simulation.legs);
        }
      }
    } catch (error) {
      console.error('Simulation error:', error);
    } finally {
      setLoading(false);
    }
  }, [legs]);

  if (!isOpen) return null;

  return (
    <div 
      className="fixed right-0 top-0 h-full w-96 bg-zinc-950 border-l border-zinc-800 shadow-2xl z-50 flex flex-col"
      data-testid="command-post"
    >
      {/* Header */}
      <div className="p-4 border-b border-zinc-800 bg-gradient-to-r from-zinc-900 to-zinc-950">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Target className="w-5 h-5 text-cyan-400" />
              Command Post
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

      {/* Search */}
      <div className="p-4 border-b border-zinc-800">
        <CommandSearch 
          onPlayerSelect={fetchProfile}
          placeholder="Search player to add leg..."
        />
        
        {/* Selected Profile */}
        {profileLoading && (
          <div className="mt-3 text-center text-sm text-zinc-400">
            Loading profile...
          </div>
        )}
        
        {selectedProfile && !profileLoading && (
          <div className="mt-3">
            <TacticalProfile 
              profile={selectedProfile}
              onSelectLine={addLegFromLine}
              onClose={() => setSelectedProfile(null)}
            />
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
      <div className="flex-1 overflow-y-auto p-4">
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
            {legs.length > 0 ? (
              legs.map((leg, idx) => (
                <LegCard 
                  key={`${leg.player_name}-${leg.stat_type}-${idx}`}
                  leg={leg}
                  index={idx}
                  onRemove={removeLeg}
                />
              ))
            ) : (
              <div className="text-center py-8 text-zinc-500">
                <Target className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="text-sm">No legs configured</p>
                <p className="text-xs mt-1">Search and select players above</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="p-4 border-t border-zinc-800 bg-zinc-900/50">
        <Button
          onClick={runSimulation}
          disabled={legs.length === 0 || loading}
          className="w-full bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
          data-testid="run-simulation-btn"
        >
          {loading ? (
            <>
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
              Simulating...
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
