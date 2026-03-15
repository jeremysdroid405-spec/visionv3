import React, { memo, useMemo } from 'react';
import { PickCard } from './PickCard';
import { ParlayTicket } from './ParlayTicket';
import { buildMasterParlayTickets } from '../../logic/matrixEngine';

// Section header component
const SectionHeader = memo(({ icon, title, subtitle, badgeText, badgeColor = 'red' }) => {
  const badgeColors = {
    red: 'bg-red-500/20 text-red-400 border-red-500/30',
    amber: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    green: 'bg-green-500/20 text-green-400 border-green-500/30'
  };
  
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-zinc-800/50 border border-zinc-700">
          {icon}
        </div>
        <div>
          <span className="text-sm font-bold text-white">{title}</span>
          {subtitle && <p className="text-[10px] text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {badgeText && (
        <div className={`px-2 py-1 rounded text-[10px] font-bold border ${badgeColors[badgeColor]}`}>
          {badgeText}
        </div>
      )}
    </div>
  );
});

// Horizontal scrollable container
const SwipeContainer = memo(({ children }) => (
  <div className="overflow-x-auto pb-2 -mx-3 px-3">
    <div className="flex gap-3" style={{ minWidth: 'max-content' }}>
      {children}
    </div>
  </div>
));

/**
 * WarZoneSection - Top 10 Demon picks (highest risk/reward)
 */
export const WarZoneSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  if (!picks?.length) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">🔥</span>}
        title="WAR ZONE"
        subtitle="High-risk, high-reward demon plays"
        badgeText={`${picks.length} DEMONS`}
        badgeColor="red"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`warzone-${pick.player_name}-${idx}`} className="w-[280px] flex-shrink-0">
            <PickCard 
              pick={pick}
              rank={idx + 1}
              onClick={() => onPickClick(pick)}
              colorTheme="red"
              emblem="fire"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

/**
 * SafeHavenSection - Top 10 Goblin picks (safest plays)
 */
export const SafeHavenSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  if (!picks?.length) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">💎</span>}
        title="SAFE HAVEN"
        subtitle="High-floor goblin plays with best consistency"
        badgeText={`${picks.length} GOBLINS`}
        badgeColor="green"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`safehaven-${pick.player_name}-${idx}`} className="w-[280px] flex-shrink-0">
            <PickCard 
              pick={pick}
              rank={idx + 1}
              onClick={() => onPickClick(pick)}
              colorTheme="green"
              emblem="gem"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

/**
 * FrontLinesSection - Middle tier (alternating demons/goblins)
 */
export const FrontLinesSection = memo(({ picks, onPickClick, tMinusGames = [] }) => {
  if (!picks?.length) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">🎯</span>}
        title="FRONT LINES"
        subtitle="Balanced demon/goblin mix for tactical plays"
        badgeText={`${picks.length} PICKS`}
        badgeColor="amber"
      />
      <SwipeContainer>
        {picks.slice(0, 10).map((pick, idx) => (
          <div key={`frontlines-${pick.player_name}-${idx}`} className="w-[280px] flex-shrink-0">
            <PickCard 
              pick={pick}
              rank={idx + 1}
              onClick={() => onPickClick(pick)}
              colorTheme="amber"
              emblem="bullet"
            />
          </div>
        ))}
      </SwipeContainer>
    </div>
  );
});

/**
 * GauntletSection - War Zone parlay tickets
 */
export const GauntletSection = memo(({ picks, onParlayClick }) => {
  const tickets = useMemo(() => {
    if (!picks?.length) return {};
    return buildMasterParlayTickets(picks, { sectionName: 'war_zone' });
  }, [picks]);
  
  if (!picks?.length || Object.keys(tickets).length === 0) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">⚔️</span>}
        title="GAUNTLET"
        subtitle="War Zone parlay combinations"
        badgeText="PARLAYS"
        badgeColor="red"
      />
      <SwipeContainer>
        {Object.entries(tickets).map(([size, ticket]) => (
          <ParlayTicket 
            key={`gauntlet-${size}`}
            ticket={ticket}
            onClick={() => onParlayClick(ticket)}
            sectionType="war_zone"
          />
        ))}
      </SwipeContainer>
    </div>
  );
});

/**
 * ShieldSection - Safe Haven parlay tickets
 */
export const ShieldSection = memo(({ picks, onParlayClick }) => {
  const tickets = useMemo(() => {
    if (!picks?.length) return {};
    return buildMasterParlayTickets(picks, { sectionName: 'safe_haven' });
  }, [picks]);
  
  if (!picks?.length || Object.keys(tickets).length === 0) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">🛡️</span>}
        title="SHIELD"
        subtitle="Safe Haven parlay combinations"
        badgeText="PARLAYS"
        badgeColor="green"
      />
      <SwipeContainer>
        {Object.entries(tickets).map(([size, ticket]) => (
          <ParlayTicket 
            key={`shield-${size}`}
            ticket={ticket}
            onClick={() => onParlayClick(ticket)}
            sectionType="safe_haven"
          />
        ))}
      </SwipeContainer>
    </div>
  );
});

/**
 * StrikeSection - Front Lines parlay tickets (interleaved)
 */
export const StrikeSection = memo(({ picks, onParlayClick }) => {
  const tickets = useMemo(() => {
    if (!picks?.length) return {};
    return buildMasterParlayTickets(picks, { sectionName: 'front_lines' });
  }, [picks]);
  
  if (!picks?.length || Object.keys(tickets).length === 0) return null;
  
  return (
    <div className="mb-4">
      <SectionHeader 
        icon={<span className="text-lg">🎯</span>}
        title="STRIKE"
        subtitle="Front Lines parlay combinations"
        badgeText="PARLAYS"
        badgeColor="amber"
      />
      <SwipeContainer>
        {Object.entries(tickets).map(([size, ticket]) => (
          <ParlayTicket 
            key={`strike-${size}`}
            ticket={ticket}
            onClick={() => onParlayClick(ticket)}
            sectionType="front_lines"
          />
        ))}
      </SwipeContainer>
    </div>
  );
});

export default {
  WarZoneSection,
  SafeHavenSection,
  FrontLinesSection,
  GauntletSection,
  ShieldSection,
  StrikeSection
};
