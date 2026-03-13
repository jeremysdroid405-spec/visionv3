// API Configuration
export const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

// NBA CDN headshot URL
export const NBA_HEADSHOT_URL = (nbaId) => `https://cdn.nba.com/headshots/nba/latest/1040x760/${nbaId}.png`;

// Team Logo URLs (fallback for missing headshots)
export const TEAM_LOGOS = {
  "ATL": "https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg",
  "BOS": "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
  "BKN": "https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg",
  "CHA": "https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg",
  "CHI": "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
  "CLE": "https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg",
  "DAL": "https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg",
  "DEN": "https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg",
  "DET": "https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg",
  "GSW": "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
  "HOU": "https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg",
  "IND": "https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg",
  "LAC": "https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg",
  "LAL": "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
  "MEM": "https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg",
  "MIA": "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
  "MIL": "https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg",
  "MIN": "https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg",
  "NOP": "https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg",
  "NYK": "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
  "OKC": "https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg",
  "ORL": "https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg",
  "PHI": "https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg",
  "PHX": "https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg",
  "POR": "https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg",
  "SAC": "https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg",
  "SAS": "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
  "TOR": "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
  "UTA": "https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg",
  "WAS": "https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg",
};

// Cache Keys
export const CACHE_KEYS = {
  PLAYERS: 'dg_players',
  LINES: 'dg_lines',
  RADAR: 'dg_radar',
  VAULT: 'dg_vault',
  PARLAYS: 'dg_parlays',
};

// Market Short Names
export const MARKET_SHORT = {
  'player_points': 'Points',
  'player_rebounds': 'Rebounds',
  'player_assists': 'Assists',
  'player_threes': '3PM',
  'player_steals': 'Steals',
  'player_blocks': 'Blocks',
  'player_turnovers': 'Turnovers',
  'player_points_rebounds_assists': 'Pts+Reb+Ast',
  'player_points_rebounds': 'Pts+Reb',
  'player_points_assists': 'Pts+Ast',
  'player_rebounds_assists': 'Reb+Ast',
  'player_steals_blocks': 'Stl+Blk',
  'player_double_double': 'Double-Double',
  'player_triple_double': 'Triple-Double',
};

export const getMarketName = (market) => {
  return MARKET_SHORT[market] || market.replace('player_', '').replace(/_/g, ' ');
};

// Stat Categories for Player Detail Page
export const STAT_CATEGORIES = {
  'combo': { name: 'Combo Stats', color: 'text-purple-400', markets: ['player_points_rebounds_assists', 'player_points_rebounds', 'player_points_assists', 'player_rebounds_assists', 'player_steals_blocks'] },
  'points': { name: 'Points', color: 'text-yellow-400', markets: ['player_points'] },
  'rebounds': { name: 'Rebounds', color: 'text-blue-400', markets: ['player_rebounds'] },
  'assists': { name: 'Assists', color: 'text-green-400', markets: ['player_assists'] },
  'threes': { name: '3-Pointers', color: 'text-orange-400', markets: ['player_threes'] },
  'defense': { name: 'Defense', color: 'text-red-400', markets: ['player_steals', 'player_blocks'] },
  'other': { name: 'Other', color: 'text-zinc-400', markets: ['player_turnovers', 'player_double_double', 'player_triple_double'] },
};

export const getCategoryKey = (market) => {
  for (const [key, cat] of Object.entries(STAT_CATEGORIES)) {
    if (cat.markets.includes(market)) return key;
  }
  return 'other';
};

export const getCategoryColor = (key) => {
  const colors = {
    'combo': 'border-purple-500 bg-purple-950/30',
    'points': 'border-yellow-500 bg-yellow-950/30',
    'rebounds': 'border-blue-500 bg-blue-950/30',
    'assists': 'border-green-500 bg-green-950/30',
    'threes': 'border-orange-500 bg-orange-950/30',
    'defense': 'border-red-500 bg-red-950/30',
    'other': 'border-zinc-500 bg-zinc-900/30',
  };
  return colors[key] || colors.other;
};
