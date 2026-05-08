/**
 * Universal stat-label adapter — direct unit tests.
 *
 * Run with `yarn test src/utils/__tests__/statLabel.test.js`.
 */
import { getStatLabel, getStatLongLabel } from '../statLabel';

describe('getStatLabel — short form', () => {
  test('NBA core markets', () => {
    expect(getStatLabel('player_points')).toBe('PTS');
    expect(getStatLabel('player_rebounds')).toBe('REB');
    expect(getStatLabel('player_assists')).toBe('AST');
    expect(getStatLabel('player_threes')).toBe('3PM');
    expect(getStatLabel('player_steals')).toBe('STL');
    expect(getStatLabel('player_blocks')).toBe('BLK');
  });

  test('NBA combo markets', () => {
    expect(getStatLabel('player_points_rebounds_assists')).toBe('PRA');
    expect(getStatLabel('player_points_rebounds')).toBe('P+R');
    expect(getStatLabel('player_points_assists')).toBe('P+A');
    expect(getStatLabel('player_rebounds_assists')).toBe('R+A');
    expect(getStatLabel('player_steals_blocks')).toBe('BLST');
  });

  test('NBA alternate suffix collapses to base label', () => {
    expect(getStatLabel('player_points_alternate')).toBe('PTS');
    expect(getStatLabel('player_points_rebounds_alternate')).toBe('P+R');
    expect(getStatLabel('player_points_rebounds_assists_alternate')).toBe('PRA');
    expect(getStatLabel('player_points_alternate_q1')).toBe('PTS');
    expect(getStatLabel('player_points_alternate_h2')).toBe('PTS');
  });

  test('MLB batter markets', () => {
    expect(getStatLabel('batter_hits')).toBe('Hits');
    expect(getStatLabel('batter_total_bases')).toBe('Total Bases');
    expect(getStatLabel('batter_hits_runs_rbis')).toBe('H+R+RBI');
    expect(getStatLabel('batter_strikeouts')).toBe('Ks');
    expect(getStatLabel('batter_home_runs')).toBe('HR');
  });

  test('MLB pitcher markets', () => {
    expect(getStatLabel('pitcher_strikeouts')).toBe('Ks');
    expect(getStatLabel('pitcher_outs')).toBe('Outs');
    expect(getStatLabel('pitcher_walks_allowed')).toBe('BB Allowed');
    expect(getStatLabel('pitcher_hits_allowed')).toBe('Hits Allowed');
  });

  test('already-collapsed short codes pass through', () => {
    expect(getStatLabel('PTS')).toBe('PTS');
    expect(getStatLabel('PRA')).toBe('PRA');
    expect(getStatLabel('P+R')).toBe('P+R');
    expect(getStatLabel('Hits')).toBe('Hits');
    expect(getStatLabel('Ks')).toBe('Ks');
  });

  test('humanizes unknown markets safely', () => {
    expect(getStatLabel('player_blocks_steals')).toBe('BLST');  // mapped
    expect(getStatLabel('player_some_new_market')).toBe('Some New Market');
    expect(getStatLabel('batter_some_new_thing')).toBe('Some New Thing');
    expect(getStatLabel('pitcher_unusual_metric')).toBe('Unusual Metric');
  });

  test('null / empty inputs are safe', () => {
    expect(getStatLabel(null)).toBe('');
    expect(getStatLabel(undefined)).toBe('');
    expect(getStatLabel('')).toBe('');
  });
});

describe('getStatLongLabel — long form (Player Detail headings)', () => {
  test('NBA long forms', () => {
    expect(getStatLongLabel('player_points')).toBe('Points');
    expect(getStatLongLabel('player_rebounds')).toBe('Rebounds');
    expect(getStatLongLabel('player_points_rebounds_assists')).toBe('Pts+Reb+Ast');
    expect(getStatLongLabel('player_points_rebounds')).toBe('Pts+Reb');
    expect(getStatLongLabel('PRA')).toBe('Pts+Reb+Ast');
  });

  test('MLB long forms', () => {
    expect(getStatLongLabel('batter_strikeouts')).toBe('Strikeouts');
    expect(getStatLongLabel('pitcher_strikeouts')).toBe('Strikeouts');
    expect(getStatLongLabel('batter_home_runs')).toBe('Home Runs');
    expect(getStatLongLabel('batter_stolen_bases')).toBe('Stolen Bases');
    expect(getStatLongLabel('Total Bases')).toBe('Total Bases');
  });

  test('falls back to short label when no long form registered', () => {
    expect(getStatLongLabel('player_first_basket')).toBe('1st Basket');
    expect(getStatLongLabel('FGM')).toBe('Field Goals');
  });

  test('humanizes unknown markets', () => {
    expect(getStatLongLabel('player_some_new_market')).toBe('Some New Market');
  });
});
