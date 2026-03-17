// Dashboard Components Index
export { DemonIcon, GoblinIcon, VisionBadge } from './Icons';
export { CacheService } from './CacheService';
export * from './constants';

/**
 * UNIVERSAL PLAYER CARD - THE SINGLE CARD FOR THE ENTIRE APP
 * ===========================================================
 * This is the ONLY card component that should be used.
 * All other card components are DEPRECATED.
 * 
 * Architecture:
 * - VAULT FUNNEL: Stats from nba_master_hub_2026 (FG%, 3P%, STL, BLK)
 * - ODDS FUNNEL: Props from dg_cached_board (DEMON, GOBLIN, STANDARD)
 */
export { 
  default as UniversalPlayerCard, 
  PlayerHeadshot, 
  VaultStatsRow, 
  PropRow,
  TIER_THEMES,
  getHighestTier 
} from './UniversalPlayerCard';

// Legacy aliases (point to UniversalPlayerCard)
export { default as UniversalPickCard } from './UniversalPlayerCard';

// ParlayTicket - still needed for parlay display
export { ParlayTicket } from './ParlayTicket';

// Section containers - can be deprecated once Dashboard uses UniversalPlayerCard directly
export { 
  WarZoneSection, 
  SafeHavenSection, 
  FrontLinesSection,
  GauntletSection, 
  ShieldSection, 
  StrikeSection 
} from './SectionContainer';
