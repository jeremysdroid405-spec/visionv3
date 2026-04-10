/**
 * Mock Data Configuration for Sport-Exclusive Testing
 * ====================================================
 * 
 * Set USE_MOCK_DATA = true to test sport switching without backend data.
 * 
 * NBA: Points, Rebounds props
 * MLB: Strikeouts, Total Bases props
 */

// ⚠️ SET TO TRUE TO ENABLE MOCK DATA MODE
export const USE_MOCK_DATA = true;

// NBA Mock Props (Points, Rebounds)
export const NBA_MOCK_DATA = {
  safe_haven: {
    tier: "safe_haven",
    tier_label: "Safe Haven",
    description: "High-floor plays with best consistency",
    count: 2,
    picks: [
      {
        player_name: "LeBron James",
        team: "LAL",
        opponent: "GSW",
        stat_type: "PTS",
        line: 27.5,
        recommendation: "OVER",
        hit_rate_l10: 80,
        hit_rate_l5: 100,
        l10_rate: 80,
        l5_rate: 100,
        edge: 12.5,
        prob_over: 72,
        dk_odds: -180,
        tier: "safe_haven",
        sport: "nba",
        photo_url: null,
        intel_verdict: "CHALK",
        intel_score: 8,
        composite_score: 85
      },
      {
        player_name: "Nikola Jokic",
        team: "DEN",
        opponent: "PHX",
        stat_type: "REB",
        line: 12.5,
        recommendation: "OVER",
        hit_rate_l10: 90,
        hit_rate_l5: 100,
        l10_rate: 90,
        l5_rate: 100,
        edge: 15.3,
        prob_over: 78,
        dk_odds: -220,
        tier: "safe_haven",
        sport: "nba",
        photo_url: null,
        intel_verdict: "CHALK",
        intel_score: 9,
        composite_score: 92
      }
    ]
  },
  front_lines: {
    tier: "front_lines",
    tier_label: "Front Lines",
    description: "Balanced risk-reward opportunities",
    count: 2,
    picks: [
      {
        player_name: "Stephen Curry",
        team: "GSW",
        opponent: "LAL",
        stat_type: "PTS",
        line: 29.5,
        recommendation: "OVER",
        hit_rate_l10: 70,
        hit_rate_l5: 80,
        l10_rate: 70,
        l5_rate: 80,
        edge: 8.2,
        prob_over: 65,
        dk_odds: -145,
        tier: "front_lines",
        sport: "nba",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 7,
        composite_score: 72
      },
      {
        player_name: "Jayson Tatum",
        team: "BOS",
        opponent: "MIA",
        stat_type: "REB",
        line: 8.5,
        recommendation: "OVER",
        hit_rate_l10: 75,
        hit_rate_l5: 80,
        l10_rate: 75,
        l5_rate: 80,
        edge: 10.1,
        prob_over: 68,
        dk_odds: -160,
        tier: "front_lines",
        sport: "nba",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 7,
        composite_score: 74
      }
    ]
  },
  war_zone: {
    tier: "war_zone",
    tier_label: "War Zone",
    description: "High-upside demon plays",
    count: 1,
    picks: [
      {
        player_name: "Anthony Edwards",
        team: "MIN",
        opponent: "DAL",
        stat_type: "PTS",
        line: 25.5,
        recommendation: "OVER",
        hit_rate_l10: 60,
        hit_rate_l5: 60,
        l10_rate: 60,
        l5_rate: 60,
        edge: 5.5,
        prob_over: 55,
        dk_odds: 120,
        tier: "war_zone",
        sport: "nba",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 6,
        composite_score: 58
      }
    ]
  }
};

// MLB Mock Props (Strikeouts, Total Bases)
export const MLB_MOCK_DATA = {
  safe_haven: {
    tier: "safe_haven",
    tier_label: "Safe Haven",
    description: "High-floor plays with best consistency",
    count: 2,
    picks: [
      {
        player_name: "Shohei Ohtani",
        team: "LAD",
        opponent: "SF",
        stat_type: "Strikeouts",
        line: 8.5,
        recommendation: "OVER",
        hit_rate_l10: 85,
        hit_rate_l5: 100,
        l10_rate: 85,
        l5_rate: 100,
        edge: 14.2,
        prob_over: 75,
        dk_odds: -200,
        tier: "safe_haven",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "CHALK",
        intel_score: 9,
        composite_score: 88
      },
      {
        player_name: "Mookie Betts",
        team: "LAD",
        opponent: "SF",
        stat_type: "Total Bases",
        line: 2.5,
        recommendation: "OVER",
        hit_rate_l10: 75,
        hit_rate_l5: 80,
        l10_rate: 75,
        l5_rate: 80,
        edge: 11.5,
        prob_over: 70,
        dk_odds: -170,
        tier: "safe_haven",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "CHALK",
        intel_score: 8,
        composite_score: 82
      }
    ]
  },
  front_lines: {
    tier: "front_lines",
    tier_label: "Front Lines",
    description: "Balanced risk-reward opportunities",
    count: 2,
    picks: [
      {
        player_name: "Gerrit Cole",
        team: "NYY",
        opponent: "BOS",
        stat_type: "Strikeouts",
        line: 7.5,
        recommendation: "OVER",
        hit_rate_l10: 70,
        hit_rate_l5: 80,
        l10_rate: 70,
        l5_rate: 80,
        edge: 9.8,
        prob_over: 68,
        dk_odds: -155,
        tier: "front_lines",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 7,
        composite_score: 73
      },
      {
        player_name: "Aaron Judge",
        team: "NYY",
        opponent: "BOS",
        stat_type: "Total Bases",
        line: 2.5,
        recommendation: "OVER",
        hit_rate_l10: 80,
        hit_rate_l5: 80,
        l10_rate: 80,
        l5_rate: 80,
        edge: 12.0,
        prob_over: 72,
        dk_odds: -180,
        tier: "front_lines",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 8,
        composite_score: 78
      }
    ]
  },
  war_zone: {
    tier: "war_zone",
    tier_label: "War Zone",
    description: "High-upside demon plays",
    count: 2,
    picks: [
      {
        player_name: "Corbin Burnes",
        team: "BAL",
        opponent: "TOR",
        stat_type: "Strikeouts",
        line: 6.5,
        recommendation: "OVER",
        hit_rate_l10: 65,
        hit_rate_l5: 60,
        l10_rate: 65,
        l5_rate: 60,
        edge: 6.2,
        prob_over: 58,
        dk_odds: 110,
        tier: "war_zone",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 6,
        composite_score: 60
      },
      {
        player_name: "Ronald Acuna Jr.",
        team: "ATL",
        opponent: "NYM",
        stat_type: "Total Bases",
        line: 1.5,
        recommendation: "OVER",
        hit_rate_l10: 60,
        hit_rate_l5: 60,
        l10_rate: 60,
        l5_rate: 60,
        edge: 4.5,
        prob_over: 54,
        dk_odds: 130,
        tier: "war_zone",
        sport: "mlb",
        photo_url: null,
        intel_verdict: "VALUE",
        intel_score: 5,
        composite_score: 55
      }
    ]
  }
};

/**
 * Get mock data for a specific sport and tier
 */
export const getMockTierData = (sport, tier) => {
  const data = sport === 'mlb' ? MLB_MOCK_DATA : NBA_MOCK_DATA;
  return data[tier] || { picks: [], count: 0 };
};

/**
 * Get all mock props for a sport
 */
export const getMockAllProps = (sport) => {
  const data = sport === 'mlb' ? MLB_MOCK_DATA : NBA_MOCK_DATA;
  const allPicks = [
    ...data.safe_haven.picks,
    ...data.front_lines.picks,
    ...data.war_zone.picks
  ];
  return {
    players: allPicks.map(p => ({
      player_name: p.player_name,
      team: p.team,
      props: [p]
    })),
    total_players: allPicks.length,
    total_props: allPicks.length
  };
};

export default { USE_MOCK_DATA, NBA_MOCK_DATA, MLB_MOCK_DATA, getMockTierData, getMockAllProps };
