import React, { memo } from 'react';
import { Card } from '../ui/card';
import { Zap } from 'lucide-react';
import { DemonIcon, GoblinIcon, VisionBadge } from './Icons';
import { NBA_HEADSHOT_URL } from './constants';

// Theme color configurations
const THEME_COLORS = {
  red: { 
    border: 'border-red-500/40', 
    glow: 'rgba(239, 68, 68, 0.3)', 
    text: 'text-red-400', 
    bg: 'from-red-950/50',
    ring: 'ring-red-800/50',
    rankBg: 'bg-red-600',
    priceColor: 'text-red-400',
    borderLine: 'border-red-900/30',
    scoreBarHigh: 'from-red-500 to-red-400'
  },
  amber: { 
    border: 'border-amber-500/40', 
    glow: 'rgba(245, 158, 11, 0.3)', 
    text: 'text-amber-400', 
    bg: 'from-amber-950/50',
    ring: 'ring-amber-800/50',
    rankBg: 'bg-amber-600',
    priceColor: 'text-amber-400',
    borderLine: 'border-amber-900/30',
    scoreBarHigh: 'from-amber-500 to-amber-400'
  },
  green: { 
    border: 'border-green-500/40', 
    glow: 'rgba(34, 197, 94, 0.3)', 
    text: 'text-green-400', 
    bg: 'from-green-950/50',
    ring: 'ring-green-800/50',
    rankBg: 'bg-green-600',
    priceColor: 'text-green-400',
    borderLine: 'border-green-900/30',
    scoreBarHigh: 'from-green-500 to-green-400'
  }
};

// Emblem components
const BulletEmblem = memo(({ size = 20 }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="bulletGradient" x1="50%" y1="0%" x2="50%" y2="100%">
        <stop offset="0%" stopColor="#FFD700" />
        <stop offset="50%" stopColor="#B8860B" />
        <stop offset="100%" stopColor="#8B6914" />
      </linearGradient>
    </defs>
    <ellipse cx="12" cy="6" rx="4" ry="2.5" fill="#B8860B" />
    <path d="M8 6 L8 18 Q8 22 12 22 Q16 22 16 18 L16 6" fill="url(#bulletGradient)" />
    <ellipse cx="12" cy="18" rx="4" ry="2" fill="#8B6914" />
    <path d="M9 8 L9 16" stroke="#FFD700" strokeWidth="0.5" opacity="0.6" />
    <path d="M10 6.5 L10 7.5" stroke="#FFF" strokeWidth="1" opacity="0.4" strokeLinecap="round" />
  </svg>
));

const FireEmblem = memo(({ size = 20 }) => (
  <span style={{ fontSize: size, filter: 'drop-shadow(0 0 4px #ff6b35)' }}>🔥</span>
));

const GemEmblem = memo(({ size = 20 }) => (
  <span style={{ fontSize: size, filter: 'drop-shadow(0 0 4px #00BFFF)' }}>💎</span>
));

// Player headshot component
const PlayerHeadshot = memo(({ nbaId, playerName, team, photoUrl, size = 'md', className = '' }) => {
  const sizeClasses = { sm: 'w-8 h-8', md: 'w-10 h-10', lg: 'w-12 h-12' };
  
  const imgSrc = photoUrl || (nbaId ? NBA_HEADSHOT_URL(nbaId) : null);
  
  return (
    <div className={`${sizeClasses[size]} rounded-full overflow-hidden bg-zinc-800 flex-shrink-0 ${className}`}>
      {imgSrc ? (
        <img src={imgSrc} alt={playerName} className="w-full h-full object-cover" 
          onError={(e) => { e.target.style.display = 'none'; }} />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-zinc-500 text-xs font-bold">
          {playerName?.charAt(0) || '?'}
        </div>
      )}
    </div>
  );
});

// Locked badge for games in progress
const LockedBadge = memo(({ isLocked }) => {
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

// Hit rate color helper
const getHitRateColor = (rate) => {
  if (rate >= 80) return 'text-green-400';
  if (rate >= 60) return 'text-yellow-400';
  if (rate >= 40) return 'text-orange-400';
  return 'text-red-400';
};

// AI Confidence color helper
const getConfidenceColor = (confidence) => {
  if (confidence >= 80) return 'text-green-400';
  if (confidence >= 60) return 'text-purple-400';
  if (confidence >= 40) return 'text-yellow-400';
  return 'text-red-400';
};

// AI Confidence gradient helper
const getConfidenceGradient = (confidence) => {
  if (confidence >= 80) return 'bg-gradient-to-r from-green-500 to-emerald-400';
  if (confidence >= 60) return 'bg-gradient-to-r from-purple-500 to-purple-400';
  if (confidence >= 40) return 'bg-gradient-to-r from-yellow-500 to-amber-400';
  return 'bg-gradient-to-r from-red-500 to-red-400';
};

// Heat level labels
const getLevelLabel = (level, emblem) => {
  if (emblem === 'gem') {
    const labels = { 5: 'FORTRESS', 4: 'DIAMOND', 3: 'VAULT', 2: 'SAFE', 1: 'BASE' };
    return labels[level] || '';
  }
  if (emblem === 'bullet') {
    const labels = { 5: 'ELITE', 4: 'STRONG', 3: 'SOLID', 2: 'FAIR', 1: 'BASE' };
    return labels[level] || '';
  }
  const labels = { 5: 'ON FIRE', 4: 'HOT', 3: 'WARM', 2: 'MILD', 1: 'COOL' };
  return labels[level] || '';
};

/**
 * UniversalPickCard - Single card template for all tiers
 * @param {Object} pick - Pick data object
 * @param {number} rank - Display rank (1-10)
 * @param {Function} onClick - Click handler
 * @param {string} colorTheme - 'red' | 'amber' | 'green'
 * @param {string} emblem - 'fire' | 'bullet' | 'gem'
 */
export const PickCard = memo(({ 
  pick, 
  rank, 
  onClick, 
  colorTheme = 'red',
  emblem = 'fire'
}) => {
  const theme = THEME_COLORS[colorTheme] || THEME_COLORS.red;
  const h10Rate = pick.h10_rate || 0;
  
  // Calculate display level
  const getDisplayLevel = () => {
    if (emblem === 'fire') return pick.heat_level || 0;
    if (h10Rate >= 100) return 5;
    if (h10Rate >= 90) return 4;
    if (h10Rate >= 80) return 3;
    if (h10Rate >= 70) return 2;
    if (h10Rate >= 60) return 1;
    return 0;
  };
  
  const displayLevel = getDisplayLevel();
  const lineValue = pick.demon_line || pick.goblin_line || pick.line || 0;
  const scoreValue = pick.radar_score || pick.vault_score || (h10Rate / 100);
  
  const hasVisionGlow = pick.has_high_conflict || 
    ((pick.intel_briefing || pick.insight_summary) && 
     !(pick.intel_briefing || pick.insight_summary).toLowerCase().includes('standard'));
  
  const renderEmblem = () => {
    if (emblem === 'fire') return <FireEmblem size={20} />;
    if (emblem === 'bullet') return <BulletEmblem size={22} />;
    return <GemEmblem size={20} />;
  };
  
  const renderIndicators = () => {
    if (displayLevel === 0) return null;
    if (emblem === 'bullet') {
      return (
        <div className="flex items-center gap-0.5">
          {[...Array(Math.min(5, displayLevel))].map((_, i) => (
            <BulletEmblem key={i} size={12} />
          ))}
        </div>
      );
    }
    return (
      <div className="flex items-center gap-0.5">
        {[...Array(displayLevel)].map((_, i) => (
          <span key={i} className="text-[12px]" style={{ 
            filter: emblem === 'fire' ? 'drop-shadow(0 0 2px #ff6b35)' : 'drop-shadow(0 0 2px #00BFFF)'
          }}>
            {emblem === 'fire' ? '🔥' : '💎'}
          </span>
        ))}
      </div>
    );
  };
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${theme.bg} to-zinc-900 border ${theme.border}
        hover:scale-[1.02] transition-all duration-300
        cursor-pointer active:scale-[0.98] relative overflow-visible
        min-h-[280px]
        ${pick.locked ? 'pointer-events-none' : ''}
      `}
      style={{ boxShadow: `0 0 20px ${theme.glow}` }}
      onClick={pick.locked ? undefined : onClick}
      data-testid={`pick-card-${colorTheme}-${rank}`}
    >
      <LockedBadge isLocked={pick.locked} />
      
      {hasVisionGlow && <VisionBadge type={colorTheme === 'green' ? 'goblin' : 'demon'} hasVision={true} />}
      
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center gap-2 mb-2">
          <div className="flex-shrink-0">
            {pick.is_demon ? <DemonIcon size={20} hasVision={hasVisionGlow} /> :
             pick.is_goblin ? <GoblinIcon size={20} hasVision={hasVisionGlow} /> :
             colorTheme === 'red' ? <DemonIcon size={20} hasVision={hasVisionGlow} /> :
             colorTheme === 'green' ? <GoblinIcon size={20} hasVision={hasVisionGlow} /> : null}
          </div>
          
          <div className="relative">
            <PlayerHeadshot 
              nbaId={pick.nba_id} 
              playerName={pick.player_name}
              photoUrl={pick.photo_url}
              size="md"
              className={`ring-2 ${theme.ring}`}
            />
            <div className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center 
                          font-bold text-[10px] border-2 border-zinc-900 ${theme.rankBg} text-white`}>
              {rank}
            </div>
          </div>
          
          <div className="min-w-0 flex-1">
            <span className="font-bold text-white text-sm truncate block">{pick.player_name}</span>
            <div className="flex items-center gap-1 text-[10px] text-zinc-500">
              <span className="font-mono">{pick.team || '---'}</span>
              <span>· {pick.stat_type}</span>
            </div>
          </div>
        </div>
        
        {/* Tier Indicators */}
        {displayLevel > 0 && (
          <div className="flex items-center justify-between mb-2 px-1">
            {renderIndicators()}
            <span className={`text-[10px] font-medium ${theme.text}`}>
              {getLevelLabel(displayLevel, emblem)}
            </span>
          </div>
        )}
        
        {/* Stats */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Line:</span>
            <span className="text-white font-bold">{lineValue}</span>
          </div>
          
          {/* Season Average Comparison */}
          {pick.season_avg > 0 && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-zinc-400">Avg:</span>
              <div className="flex items-center gap-1">
                <span className="text-white font-mono">{pick.season_avg.toFixed(1)}</span>
                {lineValue > 0 && (
                  <span className={`text-[10px] px-1 py-0.5 rounded ${
                    pick.season_avg > lineValue 
                      ? 'bg-green-500/20 text-green-400' 
                      : 'bg-red-500/20 text-red-400'
                  }`}>
                    {pick.season_avg > lineValue ? '+' : ''}{((pick.season_avg - lineValue) / lineValue * 100).toFixed(0)}%
                  </span>
                )}
              </div>
            </div>
          )}
          
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">Gap:</span>
            <span className="text-yellow-400 font-medium">
              {pick.gap_pct > 0 ? '+' : ''}{pick.gap_pct || 0}% above std
            </span>
          </div>
          
          {/* Hit Rate Breakdown */}
          <div className="bg-zinc-800/50 rounded-lg p-2 mt-2">
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-center flex-1">
                <div className="text-[9px] text-zinc-500 uppercase">L5</div>
                <div className={`text-sm font-bold ${getHitRateColor(pick.h5_rate || 0)}`}>
                  {pick.h5_rate || 0}%
                </div>
                <div className="text-[9px] text-zinc-500">
                  {pick.h5_over || 0}/{pick.h5_games || 0}
                </div>
              </div>
              <div className="h-8 w-px bg-zinc-700" />
              <div className="text-center flex-1">
                <div className="text-[9px] text-zinc-500 uppercase">L10</div>
                <div className={`text-sm font-bold ${getHitRateColor(h10Rate)}`}>
                  {h10Rate}%
                </div>
                <div className="text-[9px] text-zinc-500">
                  {pick.h10_over || 0}/{pick.h10_games || 0}
                </div>
              </div>
              <div className="h-8 w-px bg-zinc-700" />
              <div className="text-center flex-1">
                <div className="text-[9px] text-zinc-500 uppercase">Season</div>
                <div className="text-sm font-bold text-white">
                  {pick.season_avg ? pick.season_avg.toFixed(1) : '---'}
                </div>
                <div className="text-[9px] text-zinc-500">avg</div>
              </div>
            </div>
          </div>
          
          {/* AI Confidence Meter */}
          {(pick.ai_confidence_rating !== undefined && pick.ai_confidence_rating !== null) ? (
            <div className="mt-2">
              <div className="flex items-center justify-between text-[10px] mb-1">
                <span className="text-purple-400 flex items-center gap-1">
                  <Zap className="w-2.5 h-2.5" /> AI Confidence
                </span>
                <span className={`font-bold ${getConfidenceColor(pick.ai_confidence_rating)}`}>
                  {pick.ai_confidence_rating}%
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className={`h-full rounded-full transition-all ${getConfidenceGradient(pick.ai_confidence_rating)}`}
                  style={{ width: `${pick.ai_confidence_rating}%` }}
                />
              </div>
            </div>
          ) : (
            <div className="mt-2">
              <div className="flex items-center justify-between text-[10px] mb-1">
                <span className="text-zinc-500">Value Score</span>
                <span className={`font-bold ${theme.text}`}>{(scoreValue * 100).toFixed(1)}%</span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className={`h-full rounded-full bg-gradient-to-r ${theme.scoreBarHigh}`}
                  style={{ width: `${Math.min(100, scoreValue * 100)}%` }}
                />
              </div>
            </div>
          )}
          
          {/* AI Vision */}
          {(pick.intel_briefing || pick.insight_summary) && (
            <div className={`mt-2 pt-2 border-t ${theme.borderLine}`}>
              <div className="flex items-center gap-1 mb-1">
                <Zap className="w-2.5 h-2.5 text-purple-400" />
                <span className="text-[9px] text-purple-400 uppercase tracking-wider font-semibold">The Vision</span>
              </div>
              <p className="text-[10px] text-purple-300/80 leading-relaxed italic">
                "{pick.intel_briefing || pick.insight_summary}"
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
});

PickCard.displayName = 'PickCard';
export default PickCard;
