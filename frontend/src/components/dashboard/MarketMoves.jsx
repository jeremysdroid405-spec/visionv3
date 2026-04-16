/**
 * MarketMoves — Board-diff activity feed
 * Shows picks that were recently on a visible tier and then left.
 * NOT a recommendation tier — purely a trust/visibility layer.
 */
import React, { memo } from 'react';
import { Activity, ArrowRight, Clock, Lock, TrendingDown, XCircle } from 'lucide-react';

const STATUS_CONFIG = {
  'Line moved': {
    icon: TrendingDown,
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/20',
  },
  'Moved off board': {
    icon: XCircle,
    color: 'text-zinc-400',
    bg: 'bg-zinc-500/10',
    border: 'border-zinc-500/20',
  },
  'Locked': {
    icon: Lock,
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
  },
  'No longer qualified': {
    icon: XCircle,
    color: 'text-orange-400',
    bg: 'bg-orange-500/10',
    border: 'border-orange-500/20',
  },
};

const TIER_COLORS = {
  'Safe Haven': 'text-emerald-400',
  'Front Lines': 'text-amber-400',
  'War Zone': 'text-red-400',
};

function timeAgo(isoString) {
  if (!isoString) return '';
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

const MarketMoveItem = memo(({ event }) => {
  const cfg = STATUS_CONFIG[event.status] || STATUS_CONFIG['Moved off board'];
  const Icon = cfg.icon;
  const tierColor = TIER_COLORS[event.previous_tier] || 'text-zinc-400';

  return (
    <div
      className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${cfg.bg} ${cfg.border} transition-all`}
      data-testid={`market-move-${event.pick_id}`}
    >
      <div className={`w-7 h-7 rounded-full flex items-center justify-center ${cfg.bg}`}>
        <Icon size={14} className={cfg.color} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-white font-semibold truncate">{event.player_name}</span>
          <span className="text-zinc-500">·</span>
          <span className="text-zinc-400 font-mono">
            {event.stat_type}
            {event.old_line != null && ` ${event.old_line}`}
          </span>
          {event.status === 'Line moved' && event.new_line != null && (
            <>
              <ArrowRight size={10} className="text-zinc-600" />
              <span className="text-amber-400 font-mono">{event.new_line}</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className={`text-[10px] font-medium ${cfg.color}`}>{event.status}</span>
          <span className="text-zinc-600 text-[10px]">·</span>
          <span className={`text-[10px] ${tierColor}`}>{event.previous_tier}</span>
          <span className="text-zinc-600 text-[10px]">·</span>
          <span className="text-[10px] uppercase text-zinc-500 font-medium">{event.sport}</span>
        </div>
      </div>

      <div className="flex items-center gap-1 text-[10px] text-zinc-500 whitespace-nowrap">
        <Clock size={10} />
        {timeAgo(event.changed_at)}
      </div>
    </div>
  );
});

MarketMoveItem.displayName = 'MarketMoveItem';

const MarketMoves = memo(({ events = [], isLoading = false }) => {
  if (isLoading) {
    return (
      <div className="mt-6 opacity-60" data-testid="market-moves-section">
        <div className="flex items-center gap-2 mb-3">
          <Activity size={14} className="text-zinc-500" />
          <span className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Market Moves</span>
        </div>
        <div className="space-y-2">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-12 bg-zinc-800/40 rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!events || events.length === 0) return null;

  return (
    <div className="mt-6" data-testid="market-moves-section">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Activity size={14} className="text-zinc-500" />
          <span className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Market Moves</span>
          <span className="text-[10px] text-zinc-600 font-mono">Board changes · last 20 min</span>
        </div>
        <span className="text-[10px] text-zinc-600 font-mono">{events.length} event{events.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="space-y-1.5">
        {events.map((event, idx) => (
          <MarketMoveItem key={event.pick_id + '-' + idx} event={event} />
        ))}
      </div>
    </div>
  );
});

MarketMoves.displayName = 'MarketMoves';

export default MarketMoves;
