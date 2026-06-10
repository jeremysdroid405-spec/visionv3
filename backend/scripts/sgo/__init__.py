"""SportsGameOdds (SGO) historical replay ingester."""

# BallDontLie numeric team_id → canonical mlb_xxx team identifier used
# throughout the SGO pipeline.  Edge cases resolved from BDL's own /teams
# endpoint: KC→kcr, SD→sdp, SF→sfg, TB→tbr, WSH→wsn.
BDL_TEAM_ID_TO_MLG_TEAM_ID: dict[int, str] = {
    1:  "mlb_ari",
    2:  "mlb_atl",
    3:  "mlb_bal",
    4:  "mlb_bos",
    5:  "mlb_chc",
    6:  "mlb_chw",
    7:  "mlb_cin",
    8:  "mlb_cle",
    9:  "mlb_col",
    10: "mlb_det",
    11: "mlb_hou",
    12: "mlb_kcr",
    13: "mlb_laa",
    14: "mlb_lad",
    15: "mlb_mia",
    16: "mlb_mil",
    17: "mlb_min",
    18: "mlb_nym",
    19: "mlb_nyy",
    20: "mlb_oak",
    21: "mlb_phi",
    22: "mlb_pit",
    23: "mlb_sdp",
    24: "mlb_sfg",
    25: "mlb_sea",
    26: "mlb_stl",
    27: "mlb_tbr",
    28: "mlb_tex",
    29: "mlb_tor",
    30: "mlb_wsn",
}

# BallDontLie numeric team_id → canonical nba_xxx team identifier.
# Active 30 franchises only; historical/relocated teams omitted.
BDL_NBA_TEAM_ID_TO_MLG_TEAM_ID: dict[int, str] = {
    1:  'nba_atl',
    2:  'nba_bos',
    3:  'nba_bkn',
    4:  'nba_cha',
    5:  'nba_chi',
    6:  'nba_cle',
    7:  'nba_dal',
    8:  'nba_den',
    9:  'nba_det',
    10: 'nba_gsw',
    11: 'nba_hou',
    12: 'nba_ind',
    13: 'nba_lac',
    14: 'nba_lal',
    15: 'nba_mem',
    16: 'nba_mia',
    17: 'nba_mil',
    18: 'nba_min',
    19: 'nba_nop',
    20: 'nba_nyk',
    21: 'nba_okc',
    22: 'nba_orl',
    23: 'nba_phi',
    24: 'nba_phx',
    25: 'nba_por',
    26: 'nba_sac',
    27: 'nba_sas',
    28: 'nba_tor',
    29: 'nba_uta',
    30: 'nba_was',
}


def get_bdl_team_mapping(sport: str) -> dict:
    if sport == 'mlb':
        return BDL_TEAM_ID_TO_MLG_TEAM_ID
    elif sport == 'nba':
        return BDL_NBA_TEAM_ID_TO_MLG_TEAM_ID
    return {}
