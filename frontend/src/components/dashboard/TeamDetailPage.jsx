/**
 * TeamDetailPage — clone of PlayerDetailPage for team props.
 *
 * Per user directive 2026-06-02:
 *   "team detail card should be a clone of player detail card with
 *    different inputs, teams badgesets, etc. Moving forward
 *    everything we build should just be replicas of already working
 *    features with minor changes."
 *
 * Architecture:
 *   • Renders the SAME `PlayerDetailPage` shell (no visual divergence
 *     for now — same header / odds rows / books panel / matchup
 *     summary / quick-add tray).
 *   • Adapts a team pick into the `player`-shaped object
 *     `PlayerDetailPage` already consumes:
 *       team_name      → player_name
 *       team_logo_url  → photo_url / headshot_url
 *       team_abbr      → team (badge slot)
 *       opponent       → opposing team identity
 *       prop_type='team' is preserved so PlayerDetailPage's
 *       `is_team_prop` branches can skip player-only sections
 *       (game-log bar chart, NBA per-player badges) when added.
 *   • Lives in a separate file so any future TEAM-specific surface
 *     (team historical hit-rate chart, team scoring-conceding
 *     splits, team injury report) lands here without polluting
 *     `PlayerDetailPage.jsx`.
 *
 * Identity-slot mapping mirrors `TeamPropRow.adaptTeamPickForCard`.
 * Same pattern used MLB / NBA / NFL / future sports.
 */
import React, { useMemo } from 'react';
import PlayerDetailPage from './PlayerDetailPage';
import TeamHistoricalSurfaces from './TeamHistoricalSurfaces';
import { getTeamLogo } from './constants';


/**
 * Convert a team pick (or `playerData`-shaped team payload) into the
 * shape `PlayerDetailPage` expects.
 *
 * Accepts the SAME `playerData` payload Dashboard.jsx already
 * synthesises for player picks (single-prop wrapper with
 * `name` / `team` / `photo_url` / `props=[pick]`), but with the
 * pick's identity slot mapped from team_name / team_abbr /
 * team_logo_url.
 */
function adaptTeamPickToPlayerShape(pick, sport) {
  if (!pick) return null;

  // Resolve a team logo. Prefer explicit team_logo_url; fall back to
  // the shared `getTeamLogo(sport, team_abbr)` helper used by every
  // other team-aware surface.
  const teamAbbr = pick.team_abbr || pick.team || null;
  const photoUrl =
    pick.team_logo_url ||
    pick.photo_url ||
    (teamAbbr && sport ? getTeamLogo(sport, teamAbbr) : null);

  const identityName =
    pick.team_name ||
    pick.team_abbr ||
    pick.team ||
    'Unknown Team';

  const opponent =
    pick.opponent ||
    pick.opponent_team ||
    pick.opp_team ||
    null;

  return {
    // Identity slot
    name: identityName,
    player_name: identityName,
    photo_url: photoUrl,
    headshot_url: photoUrl,
    team: teamAbbr,
    opponent,
    // Carry sport, event_id, commence_time straight through so
    // PlayerDetailPage's matchup row / commence-time stamp render.
    sport: pick.sport || sport,
    event_id: pick.event_id,
    commence_time: pick.commence_time,
    home_team: pick.home_team,
    away_team: pick.away_team,
    // Wrap the original pick as a single-prop list (same shape player
    // picks use in Dashboard.handleVaultClick / handleRadarClick).
    props: [{
      ...pick,
      // SSOT identity-slot rebind (so any field-level renderer that
      // reads `player_name` directly gets the team identity).
      player_name: identityName,
      photo_url: photoUrl,
      headshot_url: photoUrl,
      // Stat-type / market normalisation for the row label.
      stat_type_extracted: pick.market_key || pick.stat_type || pick.market,
      market: pick.market_key || pick.stat_type || pick.market,
      direction: pick.direction || pick.recommendation || pick.side || 'OVER',
      // Carry the prop-type flag so PlayerDetailPage can branch
      // (skip game-log bar chart etc.) once the team-aware
      // conditionals are wired in.
      is_team_prop: true,
      prop_type: 'team',
    }],
    // Top-level is_team_prop flag for easy detection in
    // PlayerDetailPage if it adds team-specific branching later.
    is_team_prop: true,
    prop_type: 'team',
  };
}


/**
 * TeamDetailPage — same signature as PlayerDetailPage so Dashboard.jsx
 * can swap the component in place (Dashboard already passes
 * `playerData` as the 4th arg of `handlePlayerClick`).
 *
 * Props mirror PlayerDetailPage. The only adaptation happens to
 * `player`/`playerData`: we route the team pick through
 * `adaptTeamPickToPlayerShape` before forwarding.
 */
const TeamDetailPage = (props) => {
  const { player, playerData, sport } = props;
  // playerData is the canonical payload (built in Dashboard.jsx); fall
  // back to `player` if a caller passes the pick directly.
  const sourcePick = useMemo(() => {
    if (playerData && playerData.props && playerData.props[0]) {
      // playerData carries the team pick at props[0].
      return playerData.props[0];
    }
    return player;
  }, [player, playerData]);

  const adaptedPlayer = useMemo(
    () => adaptTeamPickToPlayerShape(sourcePick, sport),
    [sourcePick, sport],
  );

  // Resolve the team_id / opponent_team_id for the team-historical
  // surfaces. The pick payload uses sport-prefixed slugs (e.g.
  // `nba_bos`) — same shape the backend `team_historical_outcomes`
  // collection is keyed by. Fall back to lowercased abbr when no
  // explicit id is supplied (legacy ingest paths).
  const teamId = useMemo(() => {
    if (!sourcePick) return null;
    const explicit = sourcePick.team_id;
    if (explicit) return String(explicit).toLowerCase();
    const sportLow = (sourcePick.sport || sport || '').toLowerCase();
    const abbr = (sourcePick.team_abbr || sourcePick.team || '').toLowerCase();
    if (sportLow && abbr) return `${sportLow}_${abbr}`;
    return null;
  }, [sourcePick, sport]);

  const opponentTeamId = useMemo(() => {
    if (!sourcePick) return null;
    const explicit = sourcePick.opponent_team_id;
    if (explicit) return String(explicit).toLowerCase();
    const sportLow = (sourcePick.sport || sport || '').toLowerCase();
    const opp = (
      sourcePick.opponent ||
      sourcePick.opponent_team ||
      sourcePick.opp_team ||
      ''
    ).toLowerCase();
    if (sportLow && opp) return `${sportLow}_${opp}`;
    return null;
  }, [sourcePick, sport]);

  // Market category routes the hit-rate chart to the right slice of
  // outcomes. h2h | spread | totals | team_total — backend slugs
  // match the pick's `market_key`/`market_category` field.
  const marketCategory = useMemo(() => {
    if (!sourcePick) return null;
    return (
      sourcePick.market_category ||
      sourcePick.market_key ||
      sourcePick.market ||
      null
    );
  }, [sourcePick]);

  if (!adaptedPlayer) return null;

  const teamSport = (sourcePick?.sport || sport || '').toLowerCase();

  return (
    <div data-testid="team-detail-page" data-prop-type="team">
      <PlayerDetailPage
        {...props}
        player={adaptedPlayer}
        playerData={adaptedPlayer}
      />
      {/* Team-specific surfaces — live in TeamDetailPage only,
          never touch PlayerDetailPage. Renders the last-N hit-rate
          bar, scoring/conceding split, and head-to-head history
          when an opponent_team_id resolves. Backed by
          `useTeamMasterStats` → `/api/v3/team/historical`. */}
      {teamId && teamSport && (
        <div
          data-testid="team-detail-historical-block"
          className="mt-4 px-4 pb-6 space-y-3">
          <div className="text-xs uppercase tracking-wider text-white/60 font-semibold mb-1">
            Team Historical Edge
          </div>
          <TeamHistoricalSurfaces
            teamId={teamId}
            sport={teamSport}
            opponentTeamId={opponentTeamId}
            marketCategory={marketCategory}
          />
        </div>
      )}
    </div>
  );
};

TeamDetailPage.displayName = 'TeamDetailPage';

export default TeamDetailPage;
export { adaptTeamPickToPlayerShape };
