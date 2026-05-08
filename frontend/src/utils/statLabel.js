/**
 * Universal Stat Label Adapter
 * ============================
 *
 * One canonical helper for transforming any backend `stat_type` /
 * `market` string into a human-readable label. Used by every UI
 * surface that ever shows a prop label — Command Center, Player
 * Detail, simulation cards, board cards, Vision Intel, MarketMoves,
 * toasts, etc.
 *
 * Sport-agnostic. No frontend branching by sport. The same call
 * resolves NBA, MLB, and any future sport that follows the
 * canonical naming convention.
 *
 * Contract:
 *   - INPUT:  the canonical backend `stat_type` value.
 *   - OUTPUT: a stable display label (short or long form).
 *   - The backend canonical string is NEVER mutated; this is a
 *     read-side display transform only.
 *
 * Two helpers:
 *   - getStatLabel(stat)      → short form ("PRA", "P+R", "PTS",
 *                                "Hits", "Ks", "3PM").
 *   - getStatLongLabel(stat)  → long form ("Pts+Reb+Ast", "Points",
 *                                "Strikeouts"). Used for section
 *                                headings on the detail page.
 *
 * Fallback chain (both helpers):
 *   1. Direct hit in the canonical map.
 *   2. Strip alternate suffixes (`_alternate`, `_alternate_q1`,
 *      `_alternate_q2`, `_alternate_h1`, `_alternate_h2`, `_alts`)
 *      and re-lookup.
 *   3. Already-collapsed short code (`PTS`, `PRA`, `Hits`) — return
 *      verbatim.
 *   4. Humanize (`player_blocks_steals` → "Blocks Steals",
 *      `batter_singles` → "Singles", `pitcher_walks_allowed` →
 *      "Walks Allowed").
 *
 * Adding a new market = one entry in `_SHORT` (and optionally
 * `_LONG`). No component edits required.
 */

// ---------------------------------------------------------------------------
// Short-form display labels (the ones that appear on board / player cards).
// Keys are the canonical backend `stat_type` strings emitted by the scoring
// engine. Values are the universally-displayed short label.
// ---------------------------------------------------------------------------
const _SHORT = {
  // ---- NBA ----------------------------------------------------------------
  player_points: 'PTS',
  player_rebounds: 'REB',
  player_assists: 'AST',
  player_threes: '3PM',
  player_steals: 'STL',
  player_blocks: 'BLK',
  player_turnovers: 'TO',
  player_minutes: 'MIN',
  player_field_goals: 'FGM',
  player_free_throws_made: 'FTM',
  player_double_double: 'DD',
  player_triple_double: 'TD',
  player_points_rebounds_assists: 'PRA',
  player_points_rebounds: 'P+R',
  player_points_assists: 'P+A',
  player_rebounds_assists: 'R+A',
  player_steals_blocks: 'BLST',
  player_blocks_steals: 'BLST',
  player_twos: '2PM',
  player_first_basket: '1st Basket',

  // ---- MLB Batter ---------------------------------------------------------
  batter_hits: 'Hits',
  batter_total_bases: 'Total Bases',
  batter_runs: 'Runs',
  batter_rbis: 'RBIs',
  batter_singles: 'Singles',
  batter_doubles: 'Doubles',
  batter_triples: 'Triples',
  batter_home_runs: 'HR',
  batter_walks: 'Walks',
  batter_strikeouts: 'Ks',
  batter_stolen_bases: 'SB',
  batter_hits_runs_rbis: 'H+R+RBI',

  // ---- MLB Pitcher --------------------------------------------------------
  pitcher_strikeouts: 'Ks',
  pitcher_outs: 'Outs',
  pitcher_walks: 'Walks',
  pitcher_walks_allowed: 'BB Allowed',
  pitcher_hits_allowed: 'Hits Allowed',
  pitcher_earned_runs: 'ER',
  pitcher_innings_pitched: 'IP',
  pitcher_strikeouts_allowed: 'Ks',

  // ---- Already-collapsed short codes (passthrough) -----------------------
  PTS: 'PTS', REB: 'REB', AST: 'AST', '3PM': '3PM',
  STL: 'STL', BLK: 'BLK', TO: 'TO', MIN: 'MIN',
  FGM: 'FGM', FTM: 'FTM', DD: 'DD', TD: 'TD',
  PRA: 'PRA', PR: 'P+R', PA: 'P+A', RA: 'R+A',
  'P+R': 'P+R', 'P+A': 'P+A', 'R+A': 'R+A',
  BLST: 'BLST', '2PM': '2PM',
  Hits: 'Hits', Runs: 'Runs', RBIs: 'RBIs',
  'Total Bases': 'Total Bases', Singles: 'Singles',
  Doubles: 'Doubles', Triples: 'Triples',
  'Home Runs': 'HR', Walks: 'Walks', Strikeouts: 'Ks',
  Ks: 'Ks', SB: 'SB', HR: 'HR', BB: 'Walks',
  'Stolen Bases': 'SB',
  'Hits Allowed': 'Hits Allowed', 'Earned Runs': 'ER',
  'Pitcher Strikeouts': 'Ks', 'Pitcher Walks': 'Walks',
  'Pitcher Outs': 'Outs', Outs: 'Outs',
  'Walks Allowed': 'BB Allowed',
  'Hits+Runs+RBIs': 'H+R+RBI',
  'H+R+RBI': 'H+R+RBI',
};

// ---------------------------------------------------------------------------
// Long-form labels (section headings on Player Detail). Falls back to the
// short label when not present.
// ---------------------------------------------------------------------------
const _LONG = {
  PTS: 'Points', REB: 'Rebounds', AST: 'Assists', '3PM': '3-Pointers',
  STL: 'Steals', BLK: 'Blocks', TO: 'Turnovers', MIN: 'Minutes',
  FGM: 'Field Goals', FTM: 'Free Throws',
  DD: 'Double-Double', TD: 'Triple-Double',
  PRA: 'Pts+Reb+Ast', 'P+R': 'Pts+Reb', 'P+A': 'Pts+Ast',
  'R+A': 'Reb+Ast', BLST: 'Blk+Stl', '2PM': '2-Pointers',
  Hits: 'Hits', Runs: 'Runs', RBIs: 'RBIs',
  'Total Bases': 'Total Bases', Singles: 'Singles',
  Doubles: 'Doubles', Triples: 'Triples',
  HR: 'Home Runs', Walks: 'Walks', Ks: 'Strikeouts',
  SB: 'Stolen Bases', 'BB Allowed': 'Walks Allowed',
  'Hits Allowed': 'Hits Allowed', ER: 'Earned Runs',
  Outs: 'Outs', 'H+R+RBI': 'Hits+Runs+RBIs',
};

// Suffix patterns that should be stripped before re-lookup. Order matters —
// longer patterns first.
const _ALT_SUFFIX_RE = /(_alternate_q[1-4]|_alternate_h[12]|_alternate|_alts?)$/i;

// Sport-prefix collapse for unknown markets (used by the humanizer fallback).
const _PREFIX_STRIP_RE = /^(player_|batter_|pitcher_)/i;

const _humanize = (s) => {
  if (typeof s !== 'string' || !s) return s ?? '';
  const stripped = s.replace(_PREFIX_STRIP_RE, '').replace(_ALT_SUFFIX_RE, '');
  return stripped
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
};

const _resolve = (statType, table) => {
  if (statType == null || statType === '') return '';
  const raw = String(statType);
  if (table[raw]) return table[raw];
  // Try lower-cased canonical form.
  const lower = raw.toLowerCase();
  if (table[lower]) return table[lower];
  // Strip the alternate / quarter / half suffix and re-lookup.
  const stripped = lower.replace(_ALT_SUFFIX_RE, '');
  if (stripped !== lower && table[stripped]) return table[stripped];
  // Try the upper-cased short-code form (PTS, REB, ...).
  const upper = raw.toUpperCase();
  if (table[upper]) return table[upper];
  return null;
};

/**
 * Universal short-form label.
 * @param {string} statType — canonical backend stat_type / market.
 * @returns {string} — display label (e.g. "PRA", "P+R", "Ks", "Hits").
 */
export const getStatLabel = (statType) => {
  const hit = _resolve(statType, _SHORT);
  return hit != null ? hit : _humanize(statType);
};

/**
 * Universal long-form label. Used for section headings on Player Detail.
 * Falls back to the short label when no long form is registered.
 */
export const getStatLongLabel = (statType) => {
  // First normalize through the short table so PTS / player_points / pts
  // all converge to the same long lookup key.
  const short = _resolve(statType, _SHORT);
  if (short && _LONG[short]) return _LONG[short];
  if (short) return short;
  return _humanize(statType);
};

// Back-compat aliases for components that imported the old names.
export const formatStatType = getStatLabel;
export default getStatLabel;
