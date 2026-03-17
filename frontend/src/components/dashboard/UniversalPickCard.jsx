/**
 * UNIVERSAL PICK CARD
 * ===================
 * Single source of truth for ALL player pick displays across the app.
 * 
 * DATA SOURCES (ONLY):
 * - nba_master_hub_2026 (Master Vault): Player info, photos, BDL stats
 * - dg_cached_board (Odds API): Lines, odds, tier classification
 * 
 * DISPLAY MODES:
 * - "full": Complete card for dashboard sections (War Zone, Safe Haven, Front Lines)
 * - "compact": Condensed view for search results & parlay lists
 * - "mini": Minimal view for inline displays
 * - "tactical": Command Post view with multiple props
 * 
 * USED IN:
 * - Dashboard.jsx (War Zone, Safe Haven, Front Lines sections)
 * - CommandPost.jsx (Player search & slate)
 * - PlayerDetailPage.jsx (Props list)
 * - Search results
 */

import React, { memo, useCallback } from 'react';
import { 
  Target, Shield, Zap, ChevronRight, Plus,
  Crosshair, TrendingUp
} from 'lucide-react';
import { Badge } from '../ui/badge';

// ==================== CONSTANTS ====================

const TEAM_LOGOS = {
  ATL: 'https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg',
  BOS: 'https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg',
  BKN: 'https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg',
  CHA: 'https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg',
  CHI: 'https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg',
  CLE: 'https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg',
  DAL: 'https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg',
  DEN: 'https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg',
  DET: 'https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg',
  GSW: 'https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg',
  HOU: 'https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg',
  IND: 'https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg',
  LAC: 'https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg',
  LAL: 'https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg',
  MEM: 'https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg',
  MIA: 'https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg',
  MIL: 'https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg',
  MIN: 'https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg',
  NOP: 'https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg',
  NYK: 'https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg',
  OKC: 'https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg',
  ORL: 'https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg',
  PHI: 'https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg',
  PHX: 'https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg',
  POR: 'https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg',
  SAC: 'https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg',
  SAS: 'https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg',
  TOR: 'https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg',
  UTA: 'https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg',
  WAS: 'https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg',
};

const STAT_LABELS = {
  'PTS': 'Points', 'REB': 'Rebounds', 'AST': 'Assists',
  '3PM': '3-Pointers', 'STL': 'Steals', 'BLK': 'Blocks',
  'TO': 'Turnovers', 'PRA': 'Pts+Reb+Ast', 'PR': 'Pts+Reb',
  'P+R': 'Pts+Reb', 'PA': 'Pts+Ast', 'P+A': 'Pts+Ast',
  'RA': 'Reb+Ast', 'R+A': 'Reb+Ast', 'MIN': 'Minutes',
};

// ==================== THEME CONFIG ====================

const THEMES = {
  red: {
    border: 'border-red-500/40',
    bg: 'from-red-950/50 to-zinc-900',
    text: 'text-red-400',
    accent: 'bg-red-600',
    glow: 'rgba(239, 68, 68, 0.3)',
    ring: 'ring-red-800/50'
  },
  green: {
    border: 'border-green-500/40',
    bg: 'from-green-950/50 to-zinc-900',
    text: 'text-green-400',
    accent: 'bg-green-600',
    glow: 'rgba(34, 197, 94, 0.3)',
    ring: 'ring-green-800/50'
  },
  amber: {
    border: 'border-amber-500/40',
    bg: 'from-amber-950/50 to-zinc-900',
    text: 'text-amber-400',
    accent: 'bg-amber-600',
    glow: 'rgba(245, 158, 11, 0.3)',
    ring: 'ring-amber-800/50'
  },
  cyan: {
    border: 'border-cyan-500/40',
    bg: 'from-cyan-950/50 to-zinc-900',
    text: 'text-cyan-400',
    accent: 'bg-cyan-600',
    glow: 'rgba(6, 182, 212, 0.3)',
    ring: 'ring-cyan-800/50'
  },
  neutral: {
    border: 'border-zinc-700/40',
    bg: 'from-zinc-900 to-zinc-900',
    text: 'text-zinc-400',
    accent: 'bg-zinc-700',
    glow: 'rgba(63, 63, 70, 0.3)',
    ring: 'ring-zinc-700/50'
  }
};

// ==================== HELPER FUNCTIONS ====================

const getHitRateColor = (rate) => {
  if (rate >= 80) return 'text-green-400';
  if (rate >= 60) return 'text-yellow-400';
  if (rate >= 40) return 'text-orange-400';
  return 'text-red-400';
};

const getInitials = (name) => {
  if (!name) return '??';
  return name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
};

const getThemeFromPick = (pick) => {
  if (pick.is_demon || pick.tier_label === 'DEMON') return 'red';
  if (pick.is_goblin || pick.tier_label === 'GOBLIN') return 'green';
  if (pick.colorTheme) return pick.colorTheme;
  return 'amber';
};

// ==================== SUB-COMPONENTS ====================

// Player Headshot with fallback
const PlayerHeadshot = memo(({ photoUrl, playerName, team, size = 'md' }) => {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-10 h-10',
    lg: 'w-12 h-12',
    xl: 'w-16 h-16'
  };
  
  const [imgError, setImgError] = React.useState(false);
  
  // Fallback to team logo or initials
  if (!photoUrl || imgError) {
    const teamLogo = TEAM_LOGOS[team];
    if (teamLogo) {
      return (
        <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800 flex items-center justify-center p-1`}>
          <img src={teamLogo} alt={team} className="w-full h-full object-contain" />
        </div>
      );
    }
    return (
      <div className={`${sizeClasses[size]} rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 flex items-center justify-center`}>
        <span className="text-zinc-400 font-bold text-xs">{getInitials(playerName)}</span>
      </div>
    );
  }
  
  return (
    <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800`}>
      <img 
        src={photoUrl} 
        alt={playerName}
        className="w-full h-full object-cover"
        style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }}
        onError={() => setImgError(true)}
      />
    </div>
  );
});
PlayerHeadshot.displayName = 'PlayerHeadshot';

// DvP Badge
const DvPBadge = memo(({ rank }) => {
  if (!rank) return null;
  
  const color = rank >= 25 ? 'emerald' : rank <= 9 ? 'red' : 'amber';
  const colorClasses = {
    emerald: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
    amber: 'bg-amber-500/20 text-amber-400 border-amber-500/40',
    red: 'bg-red-500/20 text-red-400 border-red-500/40'
  };
  
  return (
    <div 
      className={`flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] font-medium border ${colorClasses[color]}`}
      title={`DvP Rank #${rank}`}
      data-testid="dvp-badge"
    >
      <Shield className="w-2.5 h-2.5" />
      <span>#{rank}</span>
    </div>
  );
});
DvPBadge.displayName = 'DvPBadge';

// BDL Vault Stats Row (FG%, 3P%, STL, BLK)
const VaultStatsRow = memo(({ pick }) => {
  const hasStats = pick.fg_pct != null || pick.fg3_pct != null || pick.stl != null || pick.blk != null;
  if (!hasStats) return null;
  
  return (
    <div className="bg-zinc-800/30 rounded-lg p-1.5 mt-1.5 border border-zinc-700/30" data-testid="vault-stats">
      <div className="flex items-center justify-between text-center">
        {pick.fg_pct != null && (
          <div className="flex-1">
            <div className="text-[8px] text-zinc-500 uppercase">FG%</div>
            <div className="text-[11px] font-bold text-cyan-400">{pick.fg_pct}%</div>
          </div>
        )}
        {pick.fg3_pct != null && (
          <div className="flex-1">
            <div className="text-[8px] text-zinc-500 uppercase">3P%</div>
            <div className="text-[11px] font-bold text-purple-400">{pick.fg3_pct}%</div>
          </div>
        )}
        {pick.stl != null && (
          <div className="flex-1">
            <div className="text-[8px] text-zinc-500 uppercase">STL</div>
            <div className="text-[11px] font-bold text-green-400">{pick.stl}</div>
          </div>
        )}
        {pick.blk != null && (
          <div className="flex-1">
            <div className="text-[8px] text-zinc-500 uppercase">BLK</div>
            <div className="text-[11px] font-bold text-amber-400">{pick.blk}</div>
          </div>
        )}
      </div>
    </div>
  );
});
VaultStatsRow.displayName = 'VaultStatsRow';

// Hit Rate Display
const HitRateRow = memo(({ h5_rate, h10_rate, season_avg }) => {
  return (
    <div className="bg-zinc-800/50 rounded-lg p-2">
      <div className="flex items-center justify-between">
        <div className="text-center flex-1">
          <div className="text-[9px] text-zinc-500 uppercase">L5</div>
          <div className={`text-sm font-bold ${getHitRateColor(h5_rate || 0)}`}>
            {h5_rate || 0}%
          </div>
        </div>
        <div className="h-8 w-px bg-zinc-700" />
        <div className="text-center flex-1">
          <div className="text-[9px] text-zinc-500 uppercase">L10</div>
          <div className={`text-sm font-bold ${getHitRateColor(h10_rate || 0)}`}>
            {h10_rate || 0}%
          </div>
        </div>
        <div className="h-8 w-px bg-zinc-700" />
        <div className="text-center flex-1">
          <div className="text-[9px] text-zinc-500 uppercase">Avg</div>
          <div className="text-sm font-bold text-white">
            {season_avg ? season_avg.toFixed(1) : '—'}
          </div>
        </div>
      </div>
    </div>
  );
});
HitRateRow.displayName = 'HitRateRow';

// Locked Badge Overlay
const LockedOverlay = memo(({ isLocked }) => {
  if (!isLocked) return null;
  return (
    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm z-10 flex flex-col items-center justify-center rounded-lg">
      <div className="flex items-center gap-2 px-4 py-2 bg-red-500/30 rounded-full border border-red-500/50">
        <span className="text-red-400 font-bold text-sm">LOCKED</span>
      </div>
      <span className="text-zinc-400 text-xs mt-2">Game In Progress</span>
    </div>
  );
});
LockedOverlay.displayName = 'LockedOverlay';

// ==================== MAIN COMPONENT ====================

/**
 * UniversalPickCard - Single card component for all displays
 * 
 * @param {Object} pick - Pick data from API (merged from Master Vault + Odds API)
 * @param {string} mode - Display mode: 'full' | 'compact' | 'mini' | 'tactical'
 * @param {string} colorTheme - Override theme: 'red' | 'green' | 'amber' | 'cyan' | 'neutral'
 * @param {number} rank - Ranking number to display
 * @param {Function} onClick - Click handler
 * @param {Function} onQuickAdd - Quick add to Command Post handler
 * @param {boolean} showVaultStats - Show BDL stats (FG%, 3P%, STL, BLK)
 * @param {boolean} showVision - Show AI Vision text
 */
const UniversalPickCard = memo(({
  pick,
  mode = 'full',
  colorTheme,
  rank,
  onClick,
  onQuickAdd,
  showVaultStats = true,
  showVision = true
}) => {
  // Handle click - MUST be at top before any conditional returns
  const handleClick = useCallback(() => {
    if (pick) onClick?.(pick);
  }, [onClick, pick]);
  
  // Handle quick add - MUST be at top before any conditional returns
  const handleQuickAdd = useCallback((e) => {
    e.stopPropagation();
    if (pick) onQuickAdd?.(pick);
  }, [onQuickAdd, pick]);
  
  if (!pick) return null;
  
  // Determine theme
  const theme = THEMES[colorTheme || getThemeFromPick(pick)];
  
  // Extract data from pick (from Master Vault + Odds API)
  const {
    player_name,
    team,
    photo_url,
    stat_type,
    line,
    odds,
    h5_rate,
    h10_rate,
    season_avg,
    diff_from_avg,
    is_demon,
    is_goblin,
    tier_label,
    opponent,
    locked,
    vision_text,
    // BDL Vault Stats
    fg_pct,
    fg3_pct,
    stl,
    blk,
    // DvP
    dvp_rank
  } = pick;
  
  const playerSlug = player_name?.replace(/\s+/g, '-').toLowerCase();
  
  // ==================== MINI MODE ====================
  if (mode === 'mini') {
    return (
      <div 
        className={`flex items-center gap-2 p-2 rounded-lg border ${theme.border} bg-zinc-900/50 cursor-pointer hover:bg-zinc-800/50 transition-all`}
        onClick={handleClick}
        data-testid={`mini-card-${playerSlug}`}
      >
        <PlayerHeadshot photoUrl={photo_url} playerName={player_name} team={team} size="sm" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-white truncate">{player_name}</div>
          <div className={`text-[10px] ${theme.text}`}>{stat_type} O{line}</div>
        </div>
        <ChevronRight className="w-4 h-4 text-zinc-600" />
      </div>
    );
  }
  
  // ==================== COMPACT MODE ====================
  if (mode === 'compact') {
    return (
      <div 
        className={`relative flex items-center gap-3 p-3 rounded-lg border ${theme.border} bg-gradient-to-br ${theme.bg} cursor-pointer hover:scale-[1.01] transition-all`}
        onClick={handleClick}
        data-testid={`compact-card-${playerSlug}`}
      >
        <LockedOverlay isLocked={locked} />
        
        <PlayerHeadshot photoUrl={photo_url} playerName={player_name} team={team} size="md" />
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-bold text-white text-sm truncate">{player_name}</span>
            <span className="text-[10px] text-zinc-500">{team}</span>
            {dvp_rank && <DvPBadge rank={dvp_rank} />}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className={`text-xs ${theme.text}`}>{stat_type} O{line}</span>
            {tier_label && tier_label !== 'STANDARD' && (
              <Badge variant="outline" className={`text-[9px] ${theme.text} border-current`}>
                {tier_label}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-3 mt-1 text-[10px]">
            <span className="text-zinc-500">L5: <span className={getHitRateColor(h5_rate || 0)}>{h5_rate || 0}%</span></span>
            <span className="text-zinc-500">L10: <span className={getHitRateColor(h10_rate || 0)}>{h10_rate || 0}%</span></span>
            <span className="text-zinc-500">Avg: <span className="text-white">{season_avg?.toFixed(1) || '—'}</span></span>
          </div>
        </div>
        
        {onQuickAdd && (
          <button
            onClick={handleQuickAdd}
            className="flex-shrink-0 w-7 h-7 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
            data-testid={`quick-add-${playerSlug}`}
          >
            <Plus className="w-4 h-4" />
          </button>
        )}
      </div>
    );
  }
  
  // ==================== TACTICAL MODE (Command Post) ====================
  if (mode === 'tactical') {
    const props = pick.props || [pick];
    
    return (
      <div 
        className={`rounded-lg border ${theme.border} bg-gradient-to-br ${theme.bg} overflow-hidden`}
        data-testid={`tactical-card-${playerSlug}`}
      >
        {/* Header */}
        <div className="p-3 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <PlayerHeadshot photoUrl={photo_url} playerName={player_name} team={team} size="lg" />
            <div className="flex-1 min-w-0">
              <div className="font-bold text-white">{player_name}</div>
              <div className="text-xs text-zinc-500">{team} {opponent ? `vs ${opponent}` : ''}</div>
            </div>
            {onQuickAdd && (
              <button
                onClick={handleQuickAdd}
                className="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-all"
              >
                <Plus className="w-4 h-4" />
              </button>
            )}
          </div>
          
          {/* Vault Stats */}
          {showVaultStats && <VaultStatsRow pick={pick} />}
        </div>
        
        {/* Props List */}
        <div className="p-2 space-y-1 max-h-60 overflow-y-auto">
          {props.map((prop, idx) => (
            <div 
              key={`${prop.stat_type}-${prop.line}-${idx}`}
              className={`flex items-center justify-between p-2 rounded-lg cursor-pointer transition-all ${
                prop.is_demon ? 'bg-red-950/40 border border-red-500/30' :
                prop.is_goblin ? 'bg-green-950/40 border border-green-500/30' :
                'bg-zinc-800/30 hover:bg-zinc-700/30'
              }`}
              onClick={() => onClick?.(prop)}
            >
              <div className="flex items-center gap-2">
                {prop.is_demon && <Target className="w-3.5 h-3.5 text-red-400" />}
                {prop.is_goblin && <Crosshair className="w-3.5 h-3.5 text-green-400" />}
                <span className="text-sm text-white">
                  {prop.stat_type} <span className={prop.is_demon ? 'text-red-400' : prop.is_goblin ? 'text-green-400' : 'text-zinc-300'}>O{prop.line}</span>
                </span>
                {prop.tier_label && prop.tier_label !== 'STANDARD' && (
                  <Badge variant="outline" className="text-[8px] px-1 py-0">
                    {prop.tier_label}
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className={getHitRateColor(prop.h10_rate || 0)}>{prop.h10_rate || 0}%</span>
                <span className="text-zinc-500">{prop.season_avg?.toFixed(1) || '—'}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }
  
  // ==================== FULL MODE (Default) ====================
  return (
    <div 
      className={`bg-gradient-to-br ${theme.bg} border ${theme.border} hover:scale-[1.02] transition-all duration-300 cursor-pointer active:scale-[0.98] relative overflow-visible min-h-[280px] rounded-lg`}
      style={{ boxShadow: `0 0 20px ${theme.glow}` }}
      onClick={handleClick}
      data-testid={`pick-card-${playerSlug}`}
    >
      <LockedOverlay isLocked={locked} />
      
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center gap-2 mb-2">
          {/* Tier Icon */}
          <div className="flex-shrink-0">
            {is_demon ? (
              <Target className={`w-5 h-5 ${theme.text}`} />
            ) : is_goblin ? (
              <Crosshair className={`w-5 h-5 ${theme.text}`} />
            ) : (
              <TrendingUp className={`w-5 h-5 ${theme.text}`} />
            )}
          </div>
          
          {/* Headshot with rank */}
          <div className="relative">
            <div className={`rounded-full ring-2 ${theme.ring}`}>
              <PlayerHeadshot photoUrl={photo_url} playerName={player_name} team={team} size="md" />
            </div>
            {rank && (
              <div className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center font-bold text-[10px] border-2 border-zinc-900 ${theme.accent} text-white`}>
                {rank}
              </div>
            )}
          </div>
          
          {/* Name & Team */}
          <div className="min-w-0 flex-1">
            <span className="font-bold text-white text-sm truncate block">{player_name}</span>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{team}</span>
              <span>· {stat_type}</span>
              {dvp_rank && <DvPBadge rank={dvp_rank} />}
            </div>
          </div>
          
          {/* Quick Add Button */}
          {onQuickAdd && (
            <button
              onClick={handleQuickAdd}
              className="flex-shrink-0 w-7 h-7 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 hover:scale-110 transition-all duration-200"
              title="Quick-Add to Command Post"
              data-testid={`quick-add-${playerSlug}`}
            >
              <Plus className="w-4 h-4" />
            </button>
          )}
        </div>
        
        {/* Stats Section */}
        <div className="space-y-1.5">
          {/* Line & Tier */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Line:</span>
            <span className="text-white font-bold">{line}</span>
          </div>
          
          {/* Season Average & Diff */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Avg:</span>
            <div className="flex items-center gap-1">
              <span className="text-white font-mono">{season_avg?.toFixed(1) || '—'}</span>
              {diff_from_avg != null && (
                <span className={`text-[10px] px-1 py-0.5 rounded ${diff_from_avg >= 0 ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'}`}>
                  {diff_from_avg >= 0 ? '+' : ''}{diff_from_avg}%
                </span>
              )}
            </div>
          </div>
          
          {/* Hit Rate Breakdown */}
          <HitRateRow h5_rate={h5_rate} h10_rate={h10_rate} season_avg={season_avg} />
          
          {/* BDL Vault Stats */}
          {showVaultStats && <VaultStatsRow pick={pick} />}
          
          {/* Vision Text */}
          {showVision && vision_text && (
            <div className={`mt-2 pt-2 border-t ${theme.border.replace('/40', '/30')}`}>
              <div className="flex items-center gap-1 mb-1">
                <Zap className="w-3 h-3 text-purple-400" />
                <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
              </div>
              <p className="text-[10px] text-purple-300/80 leading-relaxed italic">
                "{vision_text}"
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

UniversalPickCard.displayName = 'UniversalPickCard';

export { UniversalPickCard, PlayerHeadshot, DvPBadge, VaultStatsRow, HitRateRow };
export default UniversalPickCard;
