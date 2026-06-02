/**
 * TeamHistoricalSurfaces — three team-specific visualizations that
 * the player detail page doesn't have.
 *
 * Per user directive 2026-06-02: live in `TeamDetailPage.jsx` only,
 * never touch `PlayerDetailPage.jsx`. Reusable for any sport that
 * has graded rows in `team_historical_outcomes` (MLB / NBA / NFL
 * today, NCAAF / NHL / WNBA when launched).
 *
 * Components:
 *   1. <TeamHitRateBar>      — last-N hit/miss per (market, side, line)
 *   2. <TeamScoringSplit>    — rolling team_score vs opp_score
 *   3. <TeamH2HHistory>      — head-to-head row vs current opponent
 *
 * Data source: `useTeamMasterStats` hook → `/api/v3/team/historical`.
 * Consumes the same `team_historical_outcomes` collection the
 * optimizer pool uses, so visuals stay aligned with replay/backtest
 * inputs.
 */
import React, { useMemo } from 'react';
import { useTeamMasterStats } from '../../hooks/useTeamMasterStats';


/**
 * Formats a Mongo `commence_time` ISO string or a `game_date` (YYYY-MM-DD)
 * into a compact MMM-DD label.
 */
function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return iso.slice(0, 10);
  }
}


/**
 * Last-N hit/miss bar — green = hit, red = miss, neutral = push/no
 * outcome resolved. Visual analog of `GameLogBarChart` for teams.
 */
export const TeamHitRateBar = ({ outcomes = [], title = 'Last 10 Outcomes' }) => {
  const rows = useMemo(() => {
    return outcomes
      .filter((r) => r && r.outcome_resolved !== false)
      .slice(0, 10)
      .reverse(); // oldest → newest, left to right
  }, [outcomes]);

  if (!rows.length) {
    return (
      <div
        data-testid="team-hit-rate-empty"
        className="rounded-lg border border-white/10 bg-white/5 px-4 py-6 text-center text-sm text-white/50">
        No graded outcomes yet for this team / market.
      </div>
    );
  }

  const hits = rows.filter((r) => r.hit === true).length;
  const total = rows.filter((r) => r.hit !== null && r.hit !== undefined).length;
  const pct = total > 0 ? Math.round((hits / total) * 100) : 0;

  return (
    <div
      data-testid="team-hit-rate-bar"
      className="rounded-lg border border-white/10 bg-white/5 px-4 py-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-sm font-semibold text-white/80">{title}</div>
        <div className="text-xs text-white/60">
          <span className="text-white font-mono">{hits}/{total}</span>
          <span className="ml-2 text-emerald-400 font-semibold">{pct}%</span>
        </div>
      </div>
      <div className="flex items-end gap-1.5 h-16">
        {rows.map((r, i) => {
          const isHit = r.hit === true;
          const isPush = r.hit === null || r.hit === undefined;
          const color = isPush
            ? 'bg-white/20'
            : isHit
              ? 'bg-emerald-500'
              : 'bg-red-500';
          const label = `${fmtDate(r.commence_time || r.game_date)} • ${r.market_name || r.market_category} ${r.side || ''} ${r.line ?? ''}`.trim();
          return (
            <div
              key={`${r.event_id}-${r.market_key}-${i}`}
              className="flex-1 flex flex-col items-center"
              title={label}
              data-testid={`team-hit-rate-cell-${i}`}>
              <div className={`w-full ${color} rounded-sm`} style={{ height: '60%' }} />
              <div className="mt-1 text-[10px] text-white/40">
                {fmtDate(r.commence_time || r.game_date)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};


/**
 * Rolling scoring/conceding split. Two thin bars per game — team
 * score (blue) above opponent score (orange). Diff colour-coded.
 */
export const TeamScoringSplit = ({ scoring = [], title = 'Scoring vs Conceding (Last 10)' }) => {
  const games = useMemo(() => scoring.slice(0, 10).reverse(), [scoring]);
  if (!games.length) {
    return (
      <div
        data-testid="team-scoring-split-empty"
        className="rounded-lg border border-white/10 bg-white/5 px-4 py-6 text-center text-sm text-white/50">
        No final scores yet for this team window.
      </div>
    );
  }
  const maxScore = Math.max(
    1,
    ...games.flatMap((g) => [g.team_score || 0, g.opp_score || 0]),
  );
  const teamAvg = games.reduce((s, g) => s + (g.team_score || 0), 0) / games.length;
  const oppAvg = games.reduce((s, g) => s + (g.opp_score || 0), 0) / games.length;
  const margin = teamAvg - oppAvg;
  return (
    <div
      data-testid="team-scoring-split"
      className="rounded-lg border border-white/10 bg-white/5 px-4 py-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-sm font-semibold text-white/80">{title}</div>
        <div className="text-xs text-white/60 flex items-center gap-3">
          <span><span className="inline-block w-2 h-2 bg-blue-400 rounded-sm mr-1" />Team {teamAvg.toFixed(1)}</span>
          <span><span className="inline-block w-2 h-2 bg-orange-400 rounded-sm mr-1" />Opp {oppAvg.toFixed(1)}</span>
          <span className={margin >= 0 ? 'text-emerald-400 font-semibold' : 'text-red-400 font-semibold'}>
            {margin >= 0 ? '+' : ''}{margin.toFixed(1)}
          </span>
        </div>
      </div>
      <div className="flex items-end gap-2 h-20">
        {games.map((g, i) => {
          const teamH = ((g.team_score || 0) / maxScore) * 100;
          const oppH = ((g.opp_score || 0) / maxScore) * 100;
          const label = `${fmtDate(g.commence_time || g.game_date)} ${g.home_away || ''} — Team ${g.team_score} vs Opp ${g.opp_score} (Δ${g.diff > 0 ? '+' : ''}${g.diff})`;
          return (
            <div
              key={`${g.event_id}-${i}`}
              className="flex-1 flex flex-col items-center gap-0.5"
              title={label}
              data-testid={`team-scoring-game-${i}`}>
              <div className="w-full flex items-end gap-px h-16">
                <div className="flex-1 bg-blue-400/80 rounded-sm" style={{ height: `${teamH}%` }} />
                <div className="flex-1 bg-orange-400/80 rounded-sm" style={{ height: `${oppH}%` }} />
              </div>
              <div className="text-[10px] text-white/40 truncate">
                {fmtDate(g.commence_time || g.game_date)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};


/**
 * Head-to-head history — last N games vs the SPECIFIC opponent.
 * Renders a compact row list, not a chart (sparse data; tooltip
 * not enough). Shows date, home/away, market, side+line, hit.
 */
export const TeamH2HHistory = ({ h2hRows = [], opponentTeamId, title }) => {
  if (!opponentTeamId) {
    return null; // no opponent context yet — nothing to render
  }
  if (!h2hRows.length) {
    return (
      <div
        data-testid="team-h2h-empty"
        className="rounded-lg border border-white/10 bg-white/5 px-4 py-6 text-center text-sm text-white/50">
        No graded head-to-head outcomes for this matchup yet.
      </div>
    );
  }
  return (
    <div
      data-testid="team-h2h-history"
      className="rounded-lg border border-white/10 bg-white/5 px-4 py-4">
      <div className="text-sm font-semibold text-white/80 mb-3">
        {title || `Head-to-Head History (vs ${opponentTeamId})`}
      </div>
      <div className="divide-y divide-white/5">
        {h2hRows.slice(0, 10).map((r, i) => {
          const isHit = r.hit === true;
          const isPush = r.hit === null || r.hit === undefined;
          const dot = isPush ? 'bg-white/30' : isHit ? 'bg-emerald-500' : 'bg-red-500';
          return (
            <div
              key={`${r.event_id}-${r.market_key}-${i}`}
              data-testid={`team-h2h-row-${i}`}
              className="flex items-center justify-between text-xs py-1.5">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`inline-block w-2 h-2 rounded-full ${dot} shrink-0`} />
                <span className="text-white/60 font-mono">{fmtDate(r.commence_time || r.game_date)}</span>
                <span className="text-white/40">{r.home_away?.toUpperCase()}</span>
                <span className="text-white/80 truncate">{r.market_name || r.market_category}</span>
              </div>
              <div className="text-white/70 font-mono shrink-0 ml-2">
                {r.side || ''} {r.line != null ? r.line : ''}
                {r.actual_value != null && (
                  <span className="text-white/40 ml-2">→ {r.actual_value}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};


/**
 * Composite shell — drop into `TeamDetailPage` once and it renders
 * all three surfaces wired to one `useTeamMasterStats` query.
 *
 * Props:
 *   teamId           — string (e.g. `nba_bos`)
 *   sport            — `mlb` | `nba` | `nfl`
 *   opponentTeamId   — optional; activates the H2H surface
 *   marketCategory   — optional filter for the hit-rate bar
 *   limit            — default 20 (renders top 10 visually)
 */
const TeamHistoricalSurfaces = ({
  teamId, sport, opponentTeamId, marketCategory, limit = 20,
}) => {
  const { data, isLoading, error } = useTeamMasterStats({
    teamId, sport, opponentTeamId, marketCategory, limit,
  });

  if (isLoading) {
    return (
      <div
        data-testid="team-historical-loading"
        className="rounded-lg border border-white/10 bg-white/5 px-4 py-6 text-center text-sm text-white/50">
        Loading team historical data…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div
        data-testid="team-historical-error"
        className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-xs text-red-300">
        Unable to load team historical data{error ? `: ${error.message}` : ''}.
      </div>
    );
  }
  return (
    <div data-testid="team-historical-surfaces" className="space-y-3">
      <TeamHitRateBar
        outcomes={data.recent_outcomes || []}
        title={`Last 10 ${marketCategory ? marketCategory.toUpperCase() + ' ' : ''}Outcomes`}
      />
      <TeamScoringSplit scoring={data.scoring_split || []} />
      <TeamH2HHistory
        h2hRows={data.h2h_outcomes || []}
        opponentTeamId={opponentTeamId}
      />
    </div>
  );
};

TeamHistoricalSurfaces.displayName = 'TeamHistoricalSurfaces';
export default TeamHistoricalSurfaces;
