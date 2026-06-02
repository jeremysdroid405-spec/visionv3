/**
 * TeamDetailPage — EXACT clone of PlayerDetailPage, powered by team data.
 *
 * Per user directive (2026-06-02):
 *   "all team related cards should be exact clones of player cards.
 *    the pick card, the player card, ect. they should be exactly
 *    alike, contain all of the same info. ... no pick glowing yellow
 *    like on players."
 *
 * Architecture:
 *   1. Fetch `/api/v3/team-with-badges/{team_id}?sport=...` once on
 *      mount. Backend returns a player-shaped payload with ALL of
 *      the team's available props (every market × line × side),
 *      real historical `hit_rate_l5/l10/l20`, `l*_avg`, projection
 *      (`vk_predicted`), deterministic `vision_intel`, and
 *      `scout_badges`.
 *   2. Forward the payload to `PlayerDetailPage` as `playerData`
 *      with `props.length > 1` so PlayerDetailPage's internal
 *      `useEffect` short-circuits (no second fetch). The shell,
 *      header, prop rows, GameLogBarChart, and Vision Intel Suite
 *      all render unchanged.
 *   3. Pass `highlightProp={null}` so no row receives the yellow
 *      Vision-pick glow. `props.is_vision_enriched` is never stamped
 *      for teams, so the dynamic highlight path (line 845 of
 *      PlayerDetailPage) is also a no-op.
 *
 * Identity resolution: `props[0]` carries the original team pick
 * with `team_id` (e.g. `nba_bos`). Falls back to deriving the
 * team_id from `team` / `team_abbr` + `sport` when an older click
 * path doesn't supply it.
 */
import React, { useEffect, useMemo, useState } from 'react';
import PlayerDetailPage from './PlayerDetailPage';
import { BACKEND_URL } from './constants';

const API = BACKEND_URL || process.env.REACT_APP_BACKEND_URL || '';

const _resolveTeamId = (pick, sportFallback) => {
  if (!pick) return { teamId: null, sport: sportFallback || null };
  const sport = (pick.sport || sportFallback || '').toLowerCase();
  const explicit = pick.team_id;
  if (explicit) {
    return { teamId: String(explicit).toLowerCase(), sport };
  }
  const abbr = (pick.team_abbr || pick.team || '').toLowerCase();
  if (sport && abbr) return { teamId: `${sport}_${abbr}`, sport };
  return { teamId: null, sport };
};

const TeamDetailPage = (props) => {
  const {
    playerData,    // wrapper built by Dashboard.handleRadarClick /
                   // handleVaultClick — `props[0]` is the clicked team pick
    onBack,
    onQuickAdd,
  } = props;

  // 1) Resolve the team_id + sport from the wrapper / clicked pick.
  const sourcePick = useMemo(() => {
    if (playerData?.props && playerData.props[0]) return playerData.props[0];
    return playerData || null;
  }, [playerData]);

  const { teamId, sport } = useMemo(
    () => _resolveTeamId(sourcePick, playerData?.sport),
    [sourcePick, playerData],
  );

  // 2) Fetch the team-with-badges payload.
  const [teamPayload, setTeamPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!teamId || !sport) {
      setLoading(false);
      setError('Missing team_id / sport');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const url = `${API}/api/v3/team-with-badges/${encodeURIComponent(teamId)}?sport=${encodeURIComponent(sport)}`;
    fetch(url, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => {
        if (cancelled) return;
        if (data && data.success && data.player) {
          setTeamPayload(data.player);
        } else {
          setError('No team data available');
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e?.message || 'Failed to load team data');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, sport]);

  // 3) Build the `playerData` PlayerDetailPage consumes. We keep the
  //    backend's returned shape verbatim — every field the player
  //    page reads (`player_name`, `team`, `photo_url`, `props`,
  //    `baseline_stats`, `game_logs`, `is_team_prop`) is already in
  //    `teamPayload`. PlayerDetailPage's fetch short-circuits when
  //    `playerData?.props?.length !== 1`, so passing the full payload
  //    here means NO second fetch (and no /api/v3/player-with-badges
  //    404 for the team's display name).
  const adaptedPlayerData = useMemo(() => {
    if (!teamPayload) return null;
    return {
      ...teamPayload,
      // PlayerDetailPage reads `name`/`player_name` interchangeably.
      name: teamPayload.player_name,
      // Ensure props.length !== 1 so the fetch effect skips. When the
      // team has 0 props (NBA pre-Finals-ingest), we still want the
      // header + game history + baseline_stats to render — so we pad
      // an empty array. PlayerDetailPage handles props.length === 0
      // cleanly (renders "No available Bets today").
      props: Array.isArray(teamPayload.props) ? teamPayload.props : [],
    };
  }, [teamPayload]);

  // 4) Render states.
  if (loading) {
    return (
      <div
        className="min-h-screen bg-zinc-950 flex items-center justify-center"
        data-testid="team-detail-loading"
      >
        <div className="text-zinc-400 text-sm">Loading team props…</div>
      </div>
    );
  }
  if (error || !adaptedPlayerData) {
    return (
      <div
        className="min-h-screen bg-zinc-950 flex flex-col items-center justify-center gap-3 p-8"
        data-testid="team-detail-error"
      >
        <div className="text-zinc-400 text-sm">
          {error || 'No team data available'}
        </div>
        <button
          onClick={onBack}
          className="px-3 py-1.5 text-xs rounded-md bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
          data-testid="team-detail-error-back"
        >
          Go Back
        </button>
      </div>
    );
  }

  // 5) Forward to PlayerDetailPage. Pass `highlightProp={null}` so
  //    NO yellow Vision-pick glow appears on the team detail view —
  //    explicit user directive.
  return (
    <div data-testid="team-detail-page" data-prop-type="team">
      <PlayerDetailPage
        playerName={adaptedPlayerData.player_name}
        playerData={adaptedPlayerData}
        onBack={onBack}
        highlightProp={null}
        highlightType={null}
        onQuickAdd={onQuickAdd}
      />
    </div>
  );
};

TeamDetailPage.displayName = 'TeamDetailPage';

export default TeamDetailPage;
