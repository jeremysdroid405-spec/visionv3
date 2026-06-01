/**
 * TeamPropRow — renders the team-prop card row inside a tier section.
 *
 * Clones the visual language of the player tier row (same
 * UniversalPlayerCard, same tier color, same compact mode), with the
 * identity binding switched from player → team:
 *   - player_name      → team_name
 *   - photo_url        → team_logo_url (currently null; falls back to abbr badge)
 *
 * Every team card carries `prop_type="team"`, `odds_routed=true`,
 * `team_model_pending=true`, so model-dependent fields render the
 * "model pending" affordance instead of zeros.
 *
 * Data source: useTeamSafeHaven / useTeamFrontLines / useTeamWarZone
 * (see hooks/useLiveOdds.js). Phase 1: odds-routed only.
 */
import React, { memo } from 'react';
import UniversalPlayerCard from './dashboard/UniversalPlayerCard';
import {
  useTeamSafeHaven,
  useTeamFrontLines,
  useTeamWarZone,
} from '../hooks/useLiveOdds';

const HOOK_BY_TIER = {
  safe_haven:  useTeamSafeHaven,
  front_lines: useTeamFrontLines,
  war_zone:    useTeamWarZone,
};

const SECTION_COLOR_BY_TIER = {
  safe_haven:  'green',
  front_lines: 'amber',
  war_zone:    'red',
};

const THEME_BY_TIER = {
  safe_haven:  'GOBLIN',
  front_lines: 'BALANCED',
  war_zone:    'DEMON',
};

const TITLE_BY_TIER = {
  safe_haven:  'TEAM PROPS — SAFE HAVEN',
  front_lines: 'TEAM PROPS — FRONT LINES',
  war_zone:    'TEAM PROPS — WAR ZONE',
};

/**
 * Adapt a team-prop card to the field shape UniversalPlayerCard expects.
 * The backend already emits team_name / team_abbr / team_logo_url, but
 * the card primarily keys off `player_name` for the identity slot. So
 * we map team_name → player_name and team_logo_url → photo_url ONLY
 * for the card's identity slot — `prop_type` stays `"team"` so the
 * card can branch where needed.
 */
const adaptTeamPickForCard = (pick) => {
  if (!pick) return pick;
  return {
    ...pick,
    // Identity-slot rebinding (display layer only)
    player_name: pick.team_name || pick.team_abbr || '—',
    photo_url:   pick.team_logo_url || null,
    headshot_url: pick.team_logo_url || null,
    // `team` is already populated; keep `opponent` from backend.
    // Pass-through flags so the card knows this is a team row.
    is_team_prop: true,
  };
};

const TeamPropRow = memo(({ tier, onPickClick, onQuickAdd }) => {
  const hook = HOOK_BY_TIER[tier];
  if (!hook) return null;
  const { data, isLoading } = hook();
  const picks = Array.isArray(data?.picks) ? data.picks : [];
  const color = SECTION_COLOR_BY_TIER[tier];
  const theme = THEME_BY_TIER[tier];
  const title = TITLE_BY_TIER[tier];

  // Hide the row entirely while loading (the player row above shows
  // the section skeleton, so we don't double-render skeletons).
  if (isLoading) return null;
  // Hide when empty — same "no team props yet" message would be
  // visual noise during Phase 1.
  if (picks.length === 0) return null;

  // 2026-06-01 — caption now reflects model state. Once the trained
  // XGB scorer has populated model_probability on the row, drop the
  // "model pending" caption and surface model_version instead.
  const scored = picks.some(
    (p) => p && p.model_version && p.model_probability != null);
  const captionText = scored
    ? `xgb · ${picks[0].model_version}`
    : 'model pending — odds-routed';

  return (
    <div
      className="team-prop-row mt-3 mb-2"
      data-testid={`team-prop-row-${tier}`}
    >
      <div className="flex items-center justify-between mb-2 px-2">
        <h4
          className="text-xs uppercase tracking-wider font-semibold text-zinc-400"
          data-testid={`team-prop-row-title-${tier}`}
        >
          {title}
        </h4>
        <span
          className="text-[10px] uppercase tracking-wider text-zinc-500 italic"
          data-testid={`team-prop-row-pending-${tier}`}
        >
          {captionText}
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
        {picks.slice(0, 10).map((p, idx) => (
          <div
            key={`team-${tier}-${p.event_id}-${p.team_id}-${p.market_key}-${p.side}-${p.line ?? 'ml'}-${idx}`}
            className="swipe-card"
            data-testid={`team-prop-card-${tier}-${idx}`}
          >
            <UniversalPlayerCard
              player={adaptTeamPickForCard(p)}
              onClick={() => onPickClick && onPickClick(p)}
              onQuickAdd={onQuickAdd}
              showStats={false}
              showProps={false}
              mode="compact"
              sectionColor={color}
              forceTheme={theme}
              isBoardPick={true}
            />
          </div>
        ))}
      </div>
    </div>
  );
});

TeamPropRow.displayName = 'TeamPropRow';

export default TeamPropRow;
