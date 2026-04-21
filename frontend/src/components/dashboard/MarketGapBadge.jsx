/**
 * MarketGapBadge – sport-agnostic "Book Spread" / "Market Gap" signal.
 *
 * Renders a subtle, uncluttered pill when a pick's sportsbook disagreement
 * exceeds the medium threshold. Stays silent otherwise.
 *
 * Works identically for NBA, MLB, and any future sport because it consumes
 * only the shared backend contract:
 *   pick.market_gap_level       ("none" | "medium" | "high")
 *   pick.market_gap_points      (int)
 *   pick.market_best_book       (short label, e.g. "DK")
 *   pick.market_best_price      (American odds int)
 *   pick.market_price_map       ({ DK: -234, FD: -350, ... })
 *   pick.market_books_compared  (int)
 *
 * Design rules enforced:
 *   - No glow, no animation beyond existing hover conventions
 *   - Muted zinc palette, same typography as the card
 *   - Compact label: "Book Spread 116" (uses neutral product copy)
 */

import React, { memo } from 'react';

const LEVEL_CLASSES = {
  medium:
    'border-zinc-600/60 bg-zinc-800/50 text-zinc-300',
  high:
    'border-zinc-400/60 bg-zinc-800/70 text-zinc-100',
};

const formatPrice = (p) => {
  if (p == null || Number.isNaN(Number(p))) return '—';
  const n = Number(p);
  return n > 0 ? `+${n}` : `${n}`;
};

/**
 * Compact badge variant — intended for the main card surface.
 * Silent when level === 'none' or undefined.
 */
export const MarketGapBadge = memo(({ pick, label = 'Book Spread', className = '' }) => {
  if (!pick) return null;
  const level = pick.market_gap_level;
  if (level !== 'medium' && level !== 'high') return null;

  const gap = pick.market_gap_points;
  if (gap == null || gap <= 0) return null;

  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm border text-[10px] font-mono uppercase tracking-[0.12em] ${LEVEL_CLASSES[level]} ${className}`}
      data-testid={`market-gap-badge-${level}`}
      title={`${pick.market_books_compared ?? 0} books compared`}
    >
      <span className="opacity-70">{label}</span>
      <span className="tabular-nums font-semibold">{gap}</span>
    </span>
  );
});
MarketGapBadge.displayName = 'MarketGapBadge';

/**
 * Expanded detail row — intended for PlayerDetailPage / drawer views.
 * Shows a concise book comparison. Silent when fewer than 2 books or
 * no gap worth displaying.
 */
export const MarketGapDetail = memo(({ pick, label = 'Book Spread' }) => {
  if (!pick) return null;
  const books = pick.market_price_map;
  const level = pick.market_gap_level;
  if (!books || (pick.market_books_compared ?? 0) < 2) return null;
  if (level === 'none' || level == null) return null;

  const entries = Object.entries(books);

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-400 font-mono"
      data-testid="market-gap-detail"
    >
      <span className="uppercase tracking-[0.14em] text-zinc-500">
        {label} {pick.market_gap_points}
      </span>
      <span className="tabular-nums">
        {entries
          .map(([book, price], i) => (
            <React.Fragment key={book}>
              {i > 0 && <span className="text-zinc-600 px-1">·</span>}
              <span className={pick.market_best_book === book ? 'text-zinc-200' : ''}>
                {book} {formatPrice(price)}
              </span>
            </React.Fragment>
          ))}
      </span>
      {pick.market_best_book && (
        <span className="uppercase tracking-[0.14em] text-zinc-500">
          Best <span className="text-zinc-300">{pick.market_best_book}</span>
        </span>
      )}
    </div>
  );
});
MarketGapDetail.displayName = 'MarketGapDetail';

export default MarketGapBadge;
