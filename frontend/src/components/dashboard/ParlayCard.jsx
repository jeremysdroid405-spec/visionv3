/**
 * PARLAY CARD COMPONENT
 * =====================
 * Displays parlay tier with picks and payout info.
 */

import React, { memo } from 'react';
import { ChevronRight, CheckCircle, XCircle } from 'lucide-react';
import { PlayerPhoto, PayoutDisplay, formatStatType } from '../../lib/GlobalUtilities';

const ParlayCard = memo(({ 
  parlay, 
  type = 'demon', // 'demon' | 'goblin'
  onClick 
}) => {
  if (!parlay) return null;
  
  const {
    name,
    tier,
    picks = [],
    pick_count,
    estimated_payout,
    payout_display,
    base_multiplier,
    reliability,
    lineup_valid,
    team_count,
    description
  } = parlay;
  
  const isDemon = type === 'demon';
  const borderColor = isDemon ? 'border-amber-500/30' : 'border-emerald-500/30';
  const accentColor = isDemon ? 'text-amber-400' : 'text-emerald-400';
  const bgColor = isDemon ? 'bg-amber-500/10' : 'bg-emerald-500/10';
  
  // Parse payout
  const payoutValue = typeof estimated_payout === 'number' 
    ? estimated_payout 
    : parseFloat(payout_display?.replace('x', '')) || base_multiplier;

  return (
    <div 
      className={`rounded-lg border ${borderColor} bg-zinc-900/50 p-3 cursor-pointer hover:bg-zinc-900/80 transition-all`}
      onClick={() => onClick?.(parlay)}
      data-testid={`parlay-card-${tier}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div>
          <h4 className={`text-sm font-bold ${accentColor}`}>{name}</h4>
          {description && <p className="text-[10px] text-zinc-500">{description}</p>}
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${bgColor} ${accentColor}`}>
            {pick_count || picks.length}-PICK
          </span>
          <PayoutDisplay multiplier={payoutValue} />
        </div>
      </div>
      
      {/* Picks Preview */}
      <div className="space-y-1.5 mb-2">
        {picks.slice(0, 3).map((pick, idx) => (
          <div key={idx} className="flex items-center gap-2 py-1 border-b border-zinc-800/50 last:border-0">
            <PlayerPhoto 
              photoUrl={pick.photo_url} 
              playerName={pick.player_name} 
              size="sm" 
            />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-white truncate">{pick.player_name}</p>
              <p className="text-[10px] text-zinc-500">
                {formatStatType(pick.stat_type)} {pick.direction || 'Over'} {pick.line || pick.demon_line}
              </p>
            </div>
            <span className="text-[10px] text-zinc-500">{pick.team}</span>
          </div>
        ))}
        {picks.length > 3 && (
          <p className="text-[10px] text-zinc-500 text-center">
            +{picks.length - 3} more picks
          </p>
        )}
      </div>
      
      {/* Footer */}
      <div className="flex items-center justify-between pt-2 border-t border-zinc-800">
        <div className="flex items-center gap-2">
          {lineup_valid ? (
            <div className="flex items-center gap-1 text-emerald-400">
              <CheckCircle className="w-3 h-3" />
              <span className="text-[10px]">Valid ({team_count} teams)</span>
            </div>
          ) : (
            <div className="flex items-center gap-1 text-red-400">
              <XCircle className="w-3 h-3" />
              <span className="text-[10px]">Invalid</span>
            </div>
          )}
          {reliability && (
            <span className="text-[10px] text-zinc-500">
              {Math.round(reliability)}% reliable
            </span>
          )}
        </div>
        <ChevronRight className="w-4 h-4 text-zinc-600" />
      </div>
    </div>
  );
});

ParlayCard.displayName = 'ParlayCard';

export default ParlayCard;
