/**
 * MarketMoves — Board-diff activity feed
 * Shows picks that were recently on a visible tier and then left.
 * NOT a recommendation tier — purely a trust/visibility layer.
 */
import React, { memo } from 'react';
import { Activity, ArrowRight, Clock, Lock, TrendingDown, XCircle, AlertTriangle, ShieldOff, ArrowDownRight, Ban } from 'lucide-react';

// Maps exit_reason from backend to visual config
const EXIT_REASON_CONFIG = {
  line_changed: {
    icon: TrendingDown,
    label: 'Line Changed',
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/20',
  },
  locked: {
    icon: Lock,
    label: 'Game Locked',
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
  },
  injury_repriced: {
    icon: AlertTriangle,
    label: 'Injury',
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
  },
  prop_removed: {
    icon: Ban,
    label: 'Prop Removed',
    color: 'text-zinc-400',
    bg: 'bg-zinc-500/10',
    border: 'border-zinc-500/20',
  },
  displaced_by_higher: {
    icon: ArrowDownRight,
    label: 'Displaced',
    color: 'text-sky-400',
    bg: 'bg-sky-500/10',
    border: 'border-sky-500/20',
  },
  no_longer_qualified: {
    icon: ShieldOff,
    label: 'Disqualified',
    color: 'text-orange-400',
    bg: 'bg-orange-500/10',
    border: 'border-orange-500/20',
  },
  validation_failed: {
    icon: XCircle,
    label: 'Validation Failed',
    color: 'text-orange-400',
    bg: 'bg-orange-500/10',
    border: 'border-orange-500/20',
  },
  odds_changed: {
    icon: TrendingDown,
    label: 'Odds Moved',
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/20',
  },
  unknown: {
    icon: XCircle,
    label: 'Removed',
    color: 'text-zinc-500',
    bg: 'bg-zinc-500/10',
    border: 'border-zinc-500/20',
  },
};

// Fallback for legacy events that only have status, no exit_reason
const STATUS_FALLBACK = {
  'Line moved': EXIT_REASON_CONFIG.line_changed,
  'line_moved': EXIT_REASON_CONFIG.line_changed,
  'Moved off board': EXIT_REASON_CONFIG.unknown,
  'moved_off_board': EXIT_REASON_CONFIG.unknown,
  'Locked': EXIT_REASON_CONFIG.locked,
  'locked': EXIT_REASON_CONFIG.locked,
  'No longer qualified': EXIT_REASON_CONFIG.no_longer_qualified,
  'no_longer_qualified': EXIT_REASON_CONFIG.no_longer_qualified,
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
  const reason = event.exit_reason || '';
  const cfg = EXIT_REASON_CONFIG[reason] || STATUS_FALLBACK[event.status] || EXIT_REASON_CONFIG.unknown;
  const Icon = cfg.icon;
  const tierColor = TIER_COLORS[event.previous_tier] || 'text-zinc-400';
  const showLineShift = (reason === 'line_changed' || event.status === 'line_moved') && event.new_line != null;

  // Humanize exit_detail: upstream emits raw key=value debug strings like
  //   "extreme_delta=12.0 market=player_points_rebounds_assists_alternate"
  // Surface only the most signal-y fragment and drop verbose market ids.
  const humanizeDetail = (raw) => {
    if (!raw || typeof raw !== 'string') return null;
    const parts = raw.split(/\s+/).filter(Boolean);
    const kv = {};
    for (const p of parts) {
      const idx = p.indexOf('=');
      if (idx > 0) kv[p.slice(0, idx)] = p.slice(idx + 1);
    }
    if (kv.extreme_delta) {
      const n = Number(kv.extreme_delta);
      return `Line shifted ${isNaN(n) ? kv.extreme_delta : n.toFixed(1)} pts`;
    }
    if (kv.reason) return kv.reason.replace(/_/g, ' ');
    // No key=value pairs — assume already human copy; trim long tokens.
    if (raw.includes('=')) return null;
    return raw.length > 60 ? raw.slice(0, 57) + '…' : raw;
  };
  const detail = humanizeDetail(event.exit_detail);

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
          {showLineShift && (
            <>
              <ArrowRight size={10} className="text-zinc-600" />
              <span className="text-amber-400 font-mono">{event.new_line}</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className={`text-[10px] font-medium ${cfg.color}`}>{cfg.label}</span>
          {detail && (
            <>
              <span className="text-zinc-600 text-[10px]">·</span>
              <span className="text-[10px] text-zinc-400">{detail}</span>
            </>
          )}
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
