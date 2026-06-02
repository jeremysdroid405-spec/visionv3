/**
 * useTeamMasterStats — team analog of `useMasterStats`.
 *
 * Fetches team historical surfaces consumed by `TeamDetailPage`:
 *   • recent_outcomes  → last-N over/under hit-rate bar chart
 *   • scoring_split    → rolling scoring vs. conceding average
 *   • h2h_outcomes     → head-to-head history (when opponent provided)
 *   • summary          → headline `last_10_hit_rate`
 *
 * Source: `GET /api/v3/team/historical/{team_id}?sport=...
 *           &opponent_team_id=...&market_category=...&limit=...`
 * Backend: `routes/team_historical.py` over `team_historical_outcomes`
 * (graded by `workers/team/team_outcomes_grader.py`).
 *
 * Same cache contract as `useMasterStats` — 24 h stale window because
 * `team_historical_outcomes` only changes when nightly grading runs.
 *
 * Wrapper-clone pattern (per user directive 2026-06-02): change the
 * data source, keep the React Query shape. New surfaces (NHL,
 * NCAAF, WNBA when launched) reuse this hook with their sport key.
 */
import { useQuery } from '@tanstack/react-query';

const API = process.env.REACT_APP_BACKEND_URL;
const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

const fetchTeamMasterStats = async ({
  teamId, sport, opponentTeamId, marketCategory, limit = 20,
}) => {
  if (!teamId || !sport) return null;
  const params = new URLSearchParams({ sport, limit: String(limit) });
  if (opponentTeamId) params.set('opponent_team_id', opponentTeamId);
  if (marketCategory) params.set('market_category', marketCategory);
  const url = `${API}/api/v3/team/historical/${encodeURIComponent(teamId)}?${params.toString()}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`useTeamMasterStats: HTTP ${res.status} for ${url}`);
  }
  return res.json();
};

/**
 * @param {Object}   opts
 * @param {string}   opts.teamId            — e.g. `nba_bos`
 * @param {string}   opts.sport             — `mlb` | `nba` | `nfl`
 * @param {string=}  opts.opponentTeamId    — narrows head-to-head
 * @param {string=}  opts.marketCategory    — team_total | spread | …
 * @param {number=}  opts.limit             — default 20, max 50
 * @param {boolean=} opts.enabled           — default `Boolean(teamId && sport)`
 */
export const useTeamMasterStats = ({
  teamId,
  sport,
  opponentTeamId,
  marketCategory,
  limit = 20,
  enabled,
} = {}) => {
  const enabledResolved = enabled ?? Boolean(teamId && sport);
  return useQuery({
    queryKey: [
      'teamMasterStats',
      teamId, sport, opponentTeamId, marketCategory, limit,
    ],
    queryFn: () => fetchTeamMasterStats({
      teamId, sport, opponentTeamId, marketCategory, limit,
    }),
    enabled: enabledResolved,
    staleTime: TWENTY_FOUR_HOURS,
    gcTime: TWENTY_FOUR_HOURS,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
};

export default useTeamMasterStats;
