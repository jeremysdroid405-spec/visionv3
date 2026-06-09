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
