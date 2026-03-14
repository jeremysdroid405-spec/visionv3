/**
 * PLAYER CARD COMPONENT
 * =====================
 * Unified player card for Demon Radar and Goblin Recon picks.
 * 
 * DATA PIPES (v4.0):
 * - Photos & Stats: From nba_master_hub_2026 via player_id
 * - Odds/Lines: From daily_slate_master (via cached_board)
 */

import React, { memo } from 'react';
import { Flame, ChevronRight } from 'lucide-react';
import { 
  PlayerPhoto, HeatIndicator, StatBadge, HitRateDisplay, 
  LockedBadge, VisionText, formatStatType, getTeamColor 
} from '../../lib/GlobalUtilities';

const PlayerCard = memo(({ 
  pick, 
  type = 'demon', // 'demon' | 'goblin'
  variant = 'full', // 'full' | 'compact' | 'mini'
  onClick,
  showVision = true 
}) => {
  if (!pick) return null;
  
  // DATA PIPES: Extract data with player_id as primary key
  const {
    // Primary identifier from nba_master_hub_2026
    player_id,
    player_name,
    
    // Team/Photo from nba_master_hub_2026 via player_id
    team,
    photo_url,        // From hub: headshot_url
    headshot_url,     // Direct hub field
    
    // Odds/Lines from daily_slate_master
    stat_type,
    demon_line,
    line,
    direction = 'Over',
    
    // Stats from nba_master_hub_2026 via player_id
    h10_rate,
    h5_rate,
    season_avg,       // From hub: stats.season_avg
    l10_stats,        // From cached_board (derived from hub)
    
    // Analytics
    heat_level = 0,
    hit_probability,
    price,
    vision_text,
    locked
  } = pick;
  
  // Use headshot_url first (from hub), then photo_url
  const displayPhotoUrl = headshot_url || photo_url;
  const displayLine = demon_line || line;
  const isDemon = type === 'demon';
  const borderColor = isDemon ? 'border-amber-500/30 hover:border-amber-400/50' : 'border-emerald-500/30 hover:border-emerald-400/50';
  const accentColor = isDemon ? 'text-amber-400' : 'text-emerald-400';
  const bgGradient = isDemon 
    ? 'bg-gradient-to-br from-zinc-900 via-amber-950/10 to-zinc-900' 
    : 'bg-gradient-to-br from-zinc-900 via-emerald-950/10 to-zinc-900';

  // Mini variant - just name and stat
  if (variant === 'mini') {
    return (
      <div 
        className={`flex items-center gap-2 px-2 py-1.5 rounded border ${borderColor} bg-zinc-900/50 cursor-pointer`}
        onClick={() => onClick?.(pick)}
        data-testid={`mini-card-${player_id || player_name?.replace(/\s/g, '-')}`}
      >
        <PlayerPhoto photoUrl={displayPhotoUrl} playerName={player_name} size="sm" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-white truncate">{player_name}</p>
          <p className={`text-[10px] ${accentColor}`}>{stat_type} {direction} {displayLine}</p>
        </div>
        {heat_level >= 4 && <Flame className="w-3 h-3 text-orange-400" />}
      </div>
    );
  }

  // Compact variant - for parlay lists
  if (variant === 'compact') {
    return (
      <div 
        className={`relative flex items-center gap-3 p-2 rounded-lg border ${borderColor} ${bgGradient} cursor-pointer transition-all`}
        onClick={() => onClick?.(pick)}
        data-testid={`compact-card-${player_id || player_name?.replace(/\s/g, '-')}`}
      >
        <LockedBadge isLocked={locked} />
        <PlayerPhoto photoUrl={displayPhotoUrl} playerName={player_name} size="md" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-bold text-white truncate">{player_name}</p>
            <span className="text-[10px] text-zinc-500">{team}</span>
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <StatBadge stat={stat_type} line={displayLine} direction={direction} isDemon={isDemon} isGoblin={!isDemon} />
            <HitRateDisplay l10={h10_rate} l5={h5_rate} size="sm" />
          </div>
        </div>
        <HeatIndicator level={heat_level} />
      </div>
    );
  }

  // Full variant - detailed card
  return (
    <div 
      className={`relative rounded-lg border ${borderColor} ${bgGradient} p-3 cursor-pointer transition-all`}
      onClick={() => onClick?.(pick)}
      data-testid={`player-card-${player_id || player_name?.replace(/\s/g, '-')}`}
    >
      <LockedBadge isLocked={locked} />
      
      {/* Header */}
      <div className="flex items-start gap-3 mb-2">
        <PlayerPhoto photoUrl={displayPhotoUrl} playerName={player_name} size="lg" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white truncate">{player_name}</h3>
            <HeatIndicator level={heat_level} showLabel />
          </div>
          <p className="text-xs text-zinc-500">{team}</p>
          <div className="mt-1">
            <StatBadge stat={stat_type} line={displayLine} direction={direction} isDemon={isDemon} isGoblin={!isDemon} />
          </div>
        </div>
      </div>
      
      {/* Stats Row - from nba_master_hub_2026 */}
      <div className="flex items-center justify-between py-2 border-t border-zinc-800">
        <HitRateDisplay l10={h10_rate} l5={h5_rate} />
        <div className="flex items-center gap-2">
          {hit_probability && (
            <span className="text-xs text-zinc-400">
              P: <span className={accentColor}>{Math.round(hit_probability)}%</span>
            </span>
          )}
          {price && (
            <span className="text-xs text-zinc-500">
              {price > 0 ? `+${price}` : price}
            </span>
          )}
        </div>
      </div>
      
      {/* Vision */}
      {showVision && vision_text && (
        <div className="mt-2 vision-glow">
          <VisionText text={vision_text} />
        </div>
      )}
      
      {/* Action hint */}
      <div className="flex items-center justify-end mt-2">
        <ChevronRight className="w-4 h-4 text-zinc-600" />
      </div>
    </div>
  );
});

PlayerCard.displayName = 'PlayerCard';

export default PlayerCard;
