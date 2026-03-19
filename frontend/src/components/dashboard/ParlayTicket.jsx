import React, { memo } from 'react';
import { Card } from '../ui/card';
import { ChevronRight } from 'lucide-react';

// Get tier label based on size and section type
const getTierLabel = (size, sectionType) => {
  const labels = {
    war_zone: { 2: 'DOUBLE UP', 3: 'TRIPLE THREAT', 4: 'POWER PLAY', 5: 'HEAVY HITTER', 6: 'JACKPOT' },
    front_lines: { 2: 'QUICK STRIKE', 3: 'TRIPLE TAP', 4: 'FIRE SQUAD', 5: 'FULL CLIP', 6: 'ARMORY' },
    safe_haven: { 2: 'DAILY DOUBLE', 3: 'GREEN LADDER', 4: 'GREEN LADDER+', 5: 'GREEN STACK', 6: '6-PICK FORTRESS' }
  };
  return labels[sectionType]?.[size] || `${size}-LEG`;
};

// Theme color configs
const TICKET_THEMES = {
  war_zone: {
    border: 'border-red-500/30',
    bg: 'from-red-950/40',
    text: 'text-red-400',
    glow: 'rgba(239, 68, 68, 0.15)',
    badge: 'bg-red-500/20 text-red-400'
  },
  front_lines: {
    border: 'border-amber-500/30',
    bg: 'from-amber-950/40',
    text: 'text-amber-400',
    glow: 'rgba(245, 158, 11, 0.15)',
    badge: 'bg-amber-500/20 text-amber-400'
  },
  safe_haven: {
    border: 'border-green-500/30',
    bg: 'from-green-950/40',
    text: 'text-green-400',
    glow: 'rgba(34, 197, 94, 0.15)',
    badge: 'bg-green-500/20 text-green-400'
  }
};

// Mini player photo
const MiniPhoto = memo(({ photoUrl, name }) => (
  <div className="w-6 h-6 rounded-full overflow-hidden bg-zinc-800 flex-shrink-0 border border-zinc-700">
    {photoUrl ? (
      <img src={photoUrl} alt={name} className="w-full h-full object-cover" 
        onError={(e) => { e.target.style.display = 'none'; }} />
    ) : (
      <div className="w-full h-full flex items-center justify-center text-zinc-500 text-[8px]">
        {name?.charAt(0) || '?'}
      </div>
    )}
  </div>
));

/**
 * ParlayTicket - Unified ticket card for all parlay sections
 * @param {Object} ticket - Ticket data with picks array
 * @param {Function} onClick - Click handler to expand
 * @param {string} sectionType - 'war_zone' | 'front_lines' | 'safe_haven'
 */
export const ParlayTicket = memo(({ ticket, onClick, sectionType = 'war_zone' }) => {
  const theme = TICKET_THEMES[sectionType] || TICKET_THEMES.war_zone;
  const picks = ticket.picks || [];
  const size = picks.length;
  
  if (size < 2) return null;
  
  const combinedProb = ticket.combined_probability || 
    (picks.reduce((acc, p) => acc * ((p.h10_rate || 50) / 100), 1) * 100);
  
  return (
    <Card 
      className={`
        bg-gradient-to-br ${theme.bg} to-zinc-900 border ${theme.border}
        hover:scale-[1.02] transition-all duration-300 cursor-pointer
        min-w-[260px] max-w-[280px] flex-shrink-0
      `}
      style={{ boxShadow: `0 0 15px ${theme.glow}` }}
      onClick={onClick}
      data-testid={`parlay-ticket-${sectionType}-${size}`}
    >
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className={`text-sm font-bold ${theme.text}`}>
              {getTierLabel(size, sectionType)}
            </div>
            <div className="text-[10px] text-zinc-500">{ticket.description || `${size} picks`}</div>
          </div>
          <div className={`px-2 py-0.5 rounded text-[10px] font-bold ${theme.badge}`}>
            {size}-LEG
          </div>
        </div>
        
        {/* Player Photos Stack */}
        <div className="flex items-center gap-1 mb-3">
          <div className="flex -space-x-2">
            {picks.slice(0, 4).map((pick, idx) => (
              <MiniPhoto key={idx} photoUrl={pick.photo_url} name={pick.player_name} />
            ))}
            {picks.length > 4 && (
              <div className="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-[9px] text-zinc-300 border border-zinc-600">
                +{picks.length - 4}
              </div>
            )}
          </div>
          <div className="ml-2 text-[10px] text-zinc-400 truncate flex-1">
            {picks.map(p => p.player_name?.split(' ').pop()).join(', ')}
          </div>
        </div>
        
        {/* Stats Row */}
        <div className="flex items-center justify-between bg-zinc-800/50 rounded-lg px-3 py-2">
          <div className="text-center">
            <div className="text-[9px] text-zinc-500 uppercase">Prob</div>
            <div className="text-sm font-bold text-white">{combinedProb.toFixed(1)}%</div>
          </div>
          <div className="h-8 w-px bg-zinc-700" />
          <div className="text-center">
            <div className="text-[9px] text-zinc-500 uppercase">Picks</div>
            <div className={`text-sm font-bold ${theme.text}`}>{size}</div>
          </div>
          <div className="h-8 w-px bg-zinc-700" />
          <div className="text-center">
            <div className="text-[9px] text-zinc-500 uppercase">Teams</div>
            <div className="text-sm font-bold text-white">
              {new Set(picks.map(p => p.team)).size}
            </div>
          </div>
        </div>
        
        {/* Expand Button */}
        <button 
          className="w-full mt-2 py-1.5 flex items-center justify-center gap-1 
                     text-[10px] text-zinc-400 hover:text-white bg-zinc-800/30 
                     hover:bg-zinc-800/50 rounded transition-colors"
        >
          <span>View Picks</span>
          <ChevronRight className="w-3 h-3" />
        </button>
      </div>
    </Card>
  );
});

ParlayTicket.displayName = 'ParlayTicket';
export default ParlayTicket;
